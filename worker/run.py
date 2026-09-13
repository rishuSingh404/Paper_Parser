"""Daily pipeline entry point:  python -m worker.run

Implements plan steps 0-18. Idempotent: safe to re-run the same day. `run:<date>`
and `decay:<iso_week>` markers guard once-per-period steps; term_counts is
recomputed (not incremented) so nothing double-counts. Heavy enrichment
(embeddings, clustering) degrades to a logged skip when the ML deps are absent.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import sys

from . import config_store, db, digest, settings, telegram
from .arxiv_lock import arxiv_lock, release_pipeline_lock, try_pipeline_lock
from .enrich import abstracts, citations, embeddings, tags
from .ingest import arxiv, term_backfill
from .ingest.base import upsert_papers_batch
from .ingest.registry import enabled_generic
from .isoweek import current_iso_week
from .observability import RunLog, check_and_alert_staleness
from .pipeline import cluster, discovery, momentum, niche, score, suggestions, vocab
from .pipeline import terms as term_counts

BROAD_MAX = settings.DAILY_BROAD_MAX_RESULTS
TOP_SEED_TERMS_FOR_QUERIES = 8
GENERIC_MAX_PER_QUERY = 20
RUN_DAILY_TIMEOUT_SECONDS = 1200  # 20 min hard wall-clock ceiling — see run_daily()


def _generic_queries(conn, cfg: dict) -> list[str]:
    """Keep the per-source fan-out bounded — niche phrases + the highest-weight
    seed terms. Everything else still gets swept via the broad arXiv categories."""
    phrases = niche.niche_phrases(cfg)
    rows = db.q(conn, "SELECT term FROM vocab WHERE is_seed ORDER BY weight DESC LIMIT %s",
                (TOP_SEED_TERMS_FOR_QUERIES,))
    return sorted(set(phrases + [r["term"] for r in rows]))


def _tracked_terms(conn, cfg: dict) -> list[str]:
    live = {r["term"] for r in db.q(conn, "SELECT term FROM vocab")}
    live.update((cfg.get("seed_vocab") or {}).keys())
    return sorted(live)


def run_daily(kind: str = "daily") -> dict:
    """Entry point. Thin wrapper: acquires the pipeline lock, then runs the
    real pipeline (_run_daily_body) on a worker thread with a hard wall-clock
    ceiling.

    Why a thread, not a plain call: observed live, repeatedly (2026-09-12/13,
    run_log ids 27/28/29 — the last one the actual scheduled run, so the
    Telegram digest that morning just never arrived) — a single outbound HTTP
    call (arXiv or Voyage) hung indefinitely, no exception, no timeout,
    despite an explicit httpx `timeout` on every call. api_call_log went
    completely silent for 2+ hours while the container stayed healthy
    (/healthz kept answering). Because the hang was inside `with
    db.connect()`, the pipeline lock (held on a SEPARATE connection,
    `lock_conn`) stayed acquired the whole time — every later trigger,
    including the next scheduled one, got an instant "skipped" and nothing
    ever ran. The only thing that ever cleared it was an unrelated git push
    forcing a redeploy, which killed the container as a side effect.

    A plain function call can't be interrupted from outside in Python; a
    thread can be waited on with a timeout via `future.result(timeout=...)`.
    This does NOT kill the underlying hang — Python cannot forcibly stop a
    thread — the abandoned thread keeps running until it resolves on its own
    or the container restarts, same as before. What it DOES fix: the run
    itself gives up cleanly at the ceiling, releases the pipeline lock (this
    function's `finally`, below — a different connection than whatever the
    hung thread is still holding), logs a real `error` row instead of a
    zombie `running` one, and sends the Telegram failure alert — so a single
    hung call can no longer silently block every future run or eat a whole
    day with nothing delivered. Small residual risk accepted knowingly: if
    the abandoned thread ever *does* resolve late, it writes its own
    `rlog.finish("ok")` to the same run_log row after the fact, and if a new
    run started in the meantime, both bodies could briefly overlap. Observed
    behavior across three real hangs today: none resolved on their own even
    after 2+ hours, so this is a much smaller risk than the status quo.
    """
    today = dt.date.today()
    week = current_iso_week(today)

    # One full pipeline at a time (run_bootstrap shares this key too) — a
    # duplicate/overlapping trigger (Render retry, a manual re-fire while the
    # scheduled one is still going) must bail out cheaply, not pile up a
    # second heavy run alongside the first and OOM the instance. Held on a
    # connection kept open for this whole function; see arxiv_lock.py.
    lock_conn = db.raw_connect()
    if not try_pipeline_lock(lock_conn):
        lock_conn.close()
        return {"status": "skipped", "reason": "another pipeline run is already in progress"}

    rlog = RunLog(kind=kind, run_date=today).start()

    def on_call(**kw):
        rlog.api_call(source=kw.pop("source", "?"), endpoint=kw.pop("endpoint", "?"), **kw)

    try:
        # NOT `with ThreadPoolExecutor() as pool:` — ThreadPoolExecutor's own
        # __exit__ calls shutdown(wait=True), which blocks until the
        # submitted task actually finishes regardless of future.result()'s
        # own timeout already having fired. Verified directly: a `with`-block
        # wrapping a 10s hang with result(timeout=1) still takes the full 10s
        # to exit the `with` — silently defeating the entire point of this
        # fix. shutdown(wait=False) on the timeout path returns immediately;
        # the pool object (and its one abandoned thread) is simply dropped.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_run_daily_body, kind, today, week, rlog, on_call)
        try:
            result = future.result(timeout=RUN_DAILY_TIMEOUT_SECONDS)
            pool.shutdown(wait=False)
            return result
        except concurrent.futures.TimeoutError:
            pool.shutdown(wait=False)
            raise TimeoutError(
                f"run_daily exceeded its {RUN_DAILY_TIMEOUT_SECONDS}s wall-clock budget — "
                "likely a hung network call; check api_call_log for the last entry before "
                "the gap"
            ) from None
    except Exception as exc:  # noqa: BLE001
        rlog.error("run_daily", exc)
        rlog.finish("error")
        telegram.send(f"[Paper Radar] run {today.isoformat()} FAILED: {exc!r}")
        raise
    finally:
        release_pipeline_lock(lock_conn)
        lock_conn.close()


def _run_daily_body(kind: str, today: dt.date, week: str, rlog: RunLog, on_call) -> dict:
    """The actual pipeline, steps 0-18 — unchanged logic, just split out of
    run_daily() so it can be run under a thread + hard timeout. See
    run_daily()'s docstring for why."""
    seen: set[str] = set()
    cfg: dict = {}
    try:
        with db.connect() as conn:
            cfg = config_store.load(conn)
            config_store.sync_seed_vocab(conn, cfg)
            rlog.config_version = cfg["version"]
            db.claim_marker(conn, f"run:{today.isoformat()}")

            # ---- steps 0-1a: arXiv (under the shared lock) --------------------
            # Every sub-step below rolls back on its own exception (not just
            # logs it) — a DB-level failure (e.g. a constraint violation from a
            # bad upsert) otherwise leaves the whole transaction "aborted" in
            # Postgres, and every later statement on this same connection fails
            # with InFailedSqlTransaction — including the generic-sources loop,
            # enrichment, and the run's own cleanup. Hit live in production.
            def _rb() -> None:
                try:
                    conn.rollback()
                except Exception:
                    pass

            with arxiv_lock(conn, wait=True):
                try:
                    rlog.stat("term_backfills", term_backfill.drain_pending(conn, on_call=on_call))
                except Exception as exc:
                    rlog.error("term_backfill.drain", exc)
                    _rb()
                for query in cfg["niche_queries"]:
                    try:
                        _, batch = arxiv.search(query, max_results=settings.DAILY_MAX_RESULTS_PER_QUERY,
                                                on_call=on_call)
                        seen.update(upsert_papers_batch(conn, batch))
                        rlog.incr("arxiv_niche", len(batch))
                    except Exception as exc:
                        rlog.error(f"arxiv_niche:{query[:40]}", exc)
                        _rb()
                        continue
                broad_q = " OR ".join(f"cat:{c}" for c in cfg["broad_categories"])
                try:
                    _, bbatch = arxiv.search(broad_q, max_results=BROAD_MAX, on_call=on_call)
                    seen.update(upsert_papers_batch(conn, bbatch))
                    rlog.incr("arxiv_broad", len(bbatch))
                except Exception as exc:
                    rlog.error("arxiv_broad", exc)
                    _rb()

            # ---- step 1b: generic sources (no arXiv lock) --------------------
            gq = _generic_queries(conn, cfg)
            params = {"queries": gq, "keywords": gq, "categories": cfg["broad_categories"],
                      "max_per_query": GENERIC_MAX_PER_QUERY,
                      "openreview_venues": (cfg.get("sources", {})
                      .get("openreview", {}) or {}).get("venues", [])}
            for mod in enabled_generic(cfg):
                n = 0
                try:
                    rps = list(mod.fetch(None, params, on_call=on_call))
                    ids = upsert_papers_batch(conn, rps)
                    seen.update(ids)
                    n = len(rps)
                    hf_rows = [
                        (pid, rp.extra.get("hf_date") or today.isoformat(),
                         rp.extra.get("hf_upvotes", 0), rp.extra.get("hf_comments", 0))
                        for pid, rp in zip(ids, rps) if rp.source == "hf_daily"
                    ]
                    if hf_rows:
                        with conn.cursor() as cur:
                            placeholders = ", ".join(["(%s,%s,%s,%s)"] * len(hf_rows))
                            cur.execute(
                                f"INSERT INTO hf_signals (paper_id, snapshot_date, upvotes, comments) "
                                f"VALUES {placeholders} ON CONFLICT (paper_id, snapshot_date) DO UPDATE "
                                f"SET upvotes = EXCLUDED.upvotes, comments = EXCLUDED.comments",
                                [v for row in hf_rows for v in row],
                            )
                    conn.commit()  # checkpoint per source — matches backfill.py:
                                   # one source's later failure must not roll back
                                   # sources that already succeeded in this run
                except Exception as exc:  # one source must not kill the run
                    rlog.error(f"ingest:{mod.name}", exc)
                    try:
                        conn.rollback()  # clear the aborted transaction so the
                                         # NEXT source (and everything after this
                                         # loop) can still use this connection —
                                         # hit live in production: a failed hf_daily
                                         # upsert left every later statement this
                                         # run (crossref, europepmc, the run's own
                                         # cleanup) failing with
                                         # InFailedSqlTransaction until the process died
                    except Exception:
                        pass  # connection itself is dead — the outer try/except
                              # will end the run; whatever committed above stands
                rlog.incr(f"src_{mod.name}", n)
            rlog.stat("papers_upserted", len(seen))

            # ---- step 3: vocab decay/prune BEFORE counts (once per iso_week) --
            if db.claim_marker(conn, f"decay:{week}"):
                vocab.decay(conn, cfg["vocab_decay_rate"])
                pruned = vocab.prune(conn, cfg, week)
                rlog.stat("vocab_pruned", pruned)

            tracked = _tracked_terms(conn, cfg)
            for t in tracked:
                term_counts.recompute_term(conn, t, only_weeks=[week])
            term_counts.recompute_category_volume(conn, only_weeks=[week])
            term_counts.refresh_last_seen(conn)
            rlog.stat("terms_tracked", len(tracked))

            # make the fetch + counts durable before the slower/riskier
            # enrichment + scoring — a later failure shouldn't lose ingestion
            conn.commit()

            # ---- enrichment (all best-effort / nullable) --------------------
            try:
                rlog.stat("abstracts", abstracts.resolve_missing(conn, limit=50, on_call=on_call))
            except Exception as exc:
                rlog.error("enrich:abstracts", exc)
            try:
                rlog.stat("tags", tags.apply(conn))
            except Exception as exc:
                rlog.error("enrich:tags", exc)
            try:
                rlog.stat("cite_snapshots", citations.refresh_snapshots(conn, on_call=on_call))
                rlog.stat("cite_edges", citations.build_reference_edges(conn, on_call=on_call))
            except Exception as exc:
                rlog.error("enrich:citations", exc)
            try:
                rlog.stat("embed_papers", embeddings.embed_new_papers(
                    conn, cfg["embedding_model_version"], on_call=on_call))
                rlog.stat("embed_anchors", embeddings.embed_anchors(conn, cfg, on_call=on_call))
            except Exception as exc:
                rlog.error("enrich:embeddings", exc)

            # ---- steps 5-12: signals + scoring ------------------------------
            rising = momentum.rising_terms(conn, tracked, week)
            bursts = momentum.bursts(conn, tracked, week, cfg["burst_min_groups"])
            try:
                unprompted = discovery.scan_corpus_bursts(conn, week)
            except Exception as exc:
                rlog.error("discovery", exc)
                unprompted = []
            rlog.stat("discovery_bursts", len(unprompted))
            broad = score.score_broad(conn, cfg, week, tracked)
            niche_feed = niche.niche_feed(conn, cfg)
            try:
                cl = cluster.cluster_recent(conn, cfg)
            except Exception as exc:
                rlog.error("cluster", exc)
                cl = {"skipped": repr(exc)}
            rlog.stat("cluster", cl)
            rlog.stat("broad", {k: broad[k] for k in ("surfaced", "universe", "truncated_at")})
            rlog.stat("cross_domain", len(broad["cross_domain"]))

            clusters_summary = db.q(
                conn,
                "SELECT cluster_key, label_terms, representative_paper_ids, size, prev_size "
                "FROM clusters WHERE period = %s ORDER BY size DESC LIMIT 12",
                (dt.date.today().strftime("%Y-%m"),),
            )
            clusters_summary = [
                {**c, "is_new": c["prev_size"] is None} for c in clusters_summary
            ]

            # ---- step 13: store digest ------------------------------------
            digest.store(conn, today, cfg["digest_mode"], broad=broad["cards"],
                         cross_domain=broad["cross_domain"],
                         niche=niche_feed, rising=rising, bursts=bursts,
                         clusters=clusters_summary, papers_scanned=len(seen))

            # ---- steps 14-15: feedback growth + suggestions --------------
            rlog.stat("vocab_growth", vocab.grow_from_feedback(conn))
            rlog.stat("suggestions", suggestions.refresh(conn, cfg))

        # ---- step 16: deliver -----------------------------------------------
        text = digest.telegram_text(
            today, cfg["digest_mode"], broad=broad["cards"], niche=niche_feed,
            rising=rising, bursts=bursts, unprompted=unprompted, papers_scanned=len(seen),
            dashboard_url=settings.DASHBOARD_URL or None,
            cross_domain=broad["cross_domain"],
        )
        rlog.stat("telegram", telegram.send(text))

        summary = rlog.finish("ok")
        check_and_alert_staleness()
        return summary
    except Exception:
        # No error reporting / lock handling here on purpose — this function
        # doesn't hold the pipeline lock and would otherwise duplicate the
        # rlog.error/finish/telegram-alert that run_daily()'s except block
        # already does exactly once, whether this raised directly or the
        # wrapper's own future.result() timed out on us. connection cleanup
        # for `conn` is already handled by db.connect()'s own context
        # manager (rollback + close on exception) regardless.
        raise


if __name__ == "__main__":
    result = run_daily()
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") == "ok" else 1)
