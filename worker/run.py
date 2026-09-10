"""Daily pipeline entry point:  python -m worker.run

Plan step order (0-18). Phase 0 implements 0-3 + a stub digest + Telegram + run
log; steps 4-18 are marked TODO and land in Phases 4-6.

Idempotent: safe to re-run the same day. `run:<date>` and `decay:<iso_week>`
markers guard the once-per-period steps; term_counts is recomputed (not
incremented) so nothing double-counts.
"""
from __future__ import annotations

import datetime as dt
import sys

from . import config_store, db, settings, telegram
from .arxiv_lock import arxiv_lock
from .ingest import arxiv, term_backfill
from .ingest.base import upsert_paper
from .isoweek import current_iso_week
from .observability import RunLog, check_and_alert_staleness
from .pipeline import terms

BROAD_CATEGORY_MAX = 100  # small in Phase 0; raised in Phase 4


def run_daily(kind: str = "daily") -> dict:
    today = dt.date.today()
    week = current_iso_week(today)
    rlog = RunLog(kind=kind, run_date=today).start()

    def on_call(**kw):
        rlog.api_call(**{"source": kw.pop("source", "arxiv"),
                         "endpoint": kw.pop("endpoint", "query"), **kw})

    try:
        with db.connect() as conn:
            cfg = config_store.load(conn)
            rlog.config_version = cfg["version"]

            first_run_today = db.claim_marker(conn, f"run:{today.isoformat()}")
            rlog.stat("first_run_today", first_run_today)

            with arxiv_lock(conn, wait=True):
                # --- step 0: drain dashboard-added term backfills -------------
                drained = term_backfill.drain_pending(conn, on_call=on_call)
                rlog.stat("term_backfills", drained)

                # --- step 1: fetch (Phase 0: arXiv niche + broad only) -------
                seen: set[str] = set()

                for query in cfg["niche_queries"]:
                    _, batch = arxiv.search(
                        query,
                        max_results=settings.DAILY_MAX_RESULTS_PER_QUERY,
                        on_call=on_call,
                    )
                    rlog.incr("niche_fetched", len(batch))
                    for rp in batch:
                        seen.add(upsert_paper(conn, rp))       # step 2 (identity/upsert)

                broad_query = " OR ".join(f"cat:{c}" for c in cfg["broad_categories"])
                _, broad_batch = arxiv.search(
                    broad_query, max_results=BROAD_CATEGORY_MAX, on_call=on_call
                )
                rlog.incr("broad_fetched", len(broad_batch))
                for rp in broad_batch:
                    seen.add(upsert_paper(conn, rp))

                rlog.stat("papers_upserted", len(seen))

            # --- step 3: vocab decay/prune BEFORE counts (once per iso_week) --
            # TODO(Phase 4): decay + calendar-correct prune. Guard:
            #   if db.claim_marker(conn, f"decay:{week}"): apply_decay_and_prune(conn, cfg)

            # --- step 3b: (re)compute this week's term_counts ---------------
            tracked = _tracked_terms(conn, cfg)
            for term in tracked:
                terms.recompute_term(conn, term, only_weeks=[week])
            terms.recompute_category_volume(conn, only_weeks=[week])
            terms.refresh_last_seen(conn)
            rlog.stat("terms_tracked", len(tracked))

            # --- steps 4-12: momentum, bursts, embeddings, co-citation, -----
            #     recall gate, ranking, citation tiering, niche feed, clusters
            # TODO(Phase 4-6)

            # --- step 13: write the digest row (Phase 0: skeleton) ---------
            db.execute(
                conn,
                """
                INSERT INTO digests (run_date, mode, papers_scanned)
                VALUES (%s, %s, %s)
                ON CONFLICT (run_date) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    papers_scanned = EXCLUDED.papers_scanned,
                    created_at = now()
                """,
                (today, cfg["digest_mode"], len(seen)),
            )

            # --- steps 14-15: feedback -> vocab growth; config suggestions --
            # TODO(Phase 4/6)

        # --- step 16: deliver -------------------------------------------------
        msg = (
            f"[Paper Radar] run {today.isoformat()} ({cfg['digest_mode']})\n"
            f"papers upserted: {len(seen)} | terms tracked: {len(tracked)} | "
            f"backfills drained: {drained['drained']}\n"
            f"(Phase 0 skeleton — scoring lands in Phase 4.)"
        )
        telegram.send(msg)

        summary = rlog.finish("ok")
        check_and_alert_staleness()
        return summary

    except Exception as exc:  # noqa: BLE001 — top-level guard, must record + alert
        rlog.error("run_daily", exc)
        summary = rlog.finish("error")
        telegram.send(f"[Paper Radar] run {today.isoformat()} FAILED: {exc!r}")
        raise


def _tracked_terms(conn, cfg: dict) -> list[str]:
    """Seed vocab (config) unioned with learned terms already in `vocab`."""
    rows = db.q(conn, "SELECT term FROM vocab")
    live = {r["term"] for r in rows}
    live.update((cfg.get("seed_vocab") or {}).keys())
    return sorted(live)


if __name__ == "__main__":
    result = run_daily()
    print(result)
    sys.exit(0 if result.get("status") == "ok" else 1)
