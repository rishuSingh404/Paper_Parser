"""Daily pipeline entry point:  python -m worker.run

Implements plan steps 0-18. Idempotent: safe to re-run the same day. `run:<date>`
and `decay:<iso_week>` markers guard once-per-period steps; term_counts is
recomputed (not incremented) so nothing double-counts. Heavy enrichment
(embeddings, clustering) degrades to a logged skip when the ML deps are absent.
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from . import config_store, db, digest, settings, telegram
from .arxiv_lock import arxiv_lock
from .enrich import abstracts, citations, embeddings, tags
from .ingest import arxiv, term_backfill
from .ingest.base import upsert_paper
from .ingest.registry import enabled_generic
from .isoweek import current_iso_week
from .observability import RunLog, check_and_alert_staleness
from .pipeline import cluster, discovery, momentum, niche, score, suggestions, vocab
from .pipeline import terms as term_counts

BROAD_MAX = settings.DAILY_BROAD_MAX_RESULTS
TOP_SEED_TERMS_FOR_QUERIES = 8
GENERIC_MAX_PER_QUERY = 20


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
    today = dt.date.today()
    week = current_iso_week(today)
    rlog = RunLog(kind=kind, run_date=today).start()

    def on_call(**kw):
        rlog.api_call(source=kw.pop("source", "?"), endpoint=kw.pop("endpoint", "?"), **kw)

    seen: set[str] = set()
    cfg: dict = {}
    try:
        with db.connect() as conn:
            cfg = config_store.load(conn)
            config_store.sync_seed_vocab(conn, cfg)
            rlog.config_version = cfg["version"]
            db.claim_marker(conn, f"run:{today.isoformat()}")

            # ---- steps 0-1a: arXiv (under the shared lock) --------------------
            with arxiv_lock(conn, wait=True):
                try:
                    rlog.stat("term_backfills", term_backfill.drain_pending(conn, on_call=on_call))
                except Exception as exc:
                    rlog.error("term_backfill.drain", exc)
                for query in cfg["niche_queries"]:
                    try:
                        _, batch = arxiv.search(query, max_results=settings.DAILY_MAX_RESULTS_PER_QUERY,
                                                on_call=on_call)
                    except Exception as exc:
                        rlog.error(f"arxiv_niche:{query[:40]}", exc)
                        continue
                    for rp in batch:
                        seen.add(upsert_paper(conn, rp))
                    rlog.incr("arxiv_niche", len(batch))
                broad_q = " OR ".join(f"cat:{c}" for c in cfg["broad_categories"])
                try:
                    _, bbatch = arxiv.search(broad_q, max_results=BROAD_MAX, on_call=on_call)
                    for rp in bbatch:
                        seen.add(upsert_paper(conn, rp))
                    rlog.incr("arxiv_broad", len(bbatch))
                except Exception as exc:
                    rlog.error("arxiv_broad", exc)

            # ---- step 1b: generic sources (no arXiv lock) --------------------
            gq = _generic_queries(conn, cfg)
            params = {"queries": gq, "keywords": gq, "categories": cfg["broad_categories"],
                      "max_per_query": GENERIC_MAX_PER_QUERY,
                      "openreview_venues": (cfg.get("sources", {})
                      .get("openreview", {}) or {}).get("venues", [])}
            for mod in enabled_generic(cfg):
                n = 0
                try:
                    for rp in mod.fetch(None, params, on_call=on_call):
                        pid = upsert_paper(conn, rp)
                        seen.add(pid)
                        n += 1
                        if rp.source == "hf_daily":
                            db.execute(
                                conn,
                                "INSERT INTO hf_signals (paper_id, snapshot_date, upvotes, comments) "
                                "VALUES (%s, %s, %s, %s) ON CONFLICT (paper_id, snapshot_date) "
                                "DO UPDATE SET upvotes = EXCLUDED.upvotes, comments = EXCLUDED.comments",
                                (pid, rp.extra.get("hf_date") or today.isoformat(),
                                 rp.extra.get("hf_upvotes", 0), rp.extra.get("hf_comments", 0)),
                            )
                except Exception as exc:  # one source must not kill the run
                    rlog.error(f"ingest:{mod.name}", exc)
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
        )
        rlog.stat("telegram", telegram.send(text))

        summary = rlog.finish("ok")
        check_and_alert_staleness()
        return summary

    except Exception as exc:  # noqa: BLE001
        rlog.error("run_daily", exc)
        summary = rlog.finish("error")
        telegram.send(f"[Paper Radar] run {today.isoformat()} FAILED: {exc!r}")
        raise


if __name__ == "__main__":
    result = run_daily()
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") == "ok" else 1)
