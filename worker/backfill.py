"""One-off bootstrap backfill:  python -m worker.backfill

Populates enough history that momentum (6-week baseline) works on day 1 instead
of being blind for ~2 months. Phase 0 covers the arXiv sweep + term_counts
baselines; HF/OpenAlex/bioRxiv/... history, the co-citation graph, embeddings,
seed-paper anchors and Rishu's initial like-pass land in Phases 1-3.

Resumable: term_counts is recomputed from `papers`, so re-running only widens
coverage, never double-counts.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

from . import config_store, db
from .arxiv_lock import arxiv_lock
from .ingest import arxiv
from .ingest.base import upsert_paper
from .observability import RunLog
from .pipeline import terms

HISTORY_WEEKS = int(os.environ.get("BOOTSTRAP_HISTORY_WEEKS", "14"))
PAGE_SIZE = 100
MAX_REQUESTS_PER_QUERY = int(os.environ.get("BOOTSTRAP_MAX_REQUESTS", "40"))


def run_bootstrap() -> dict:
    today = dt.date.today()
    since = today - dt.timedelta(weeks=HISTORY_WEEKS)
    rlog = RunLog(kind="backfill", run_date=today).start()

    def on_call(**kw):
        rlog.api_call(source=kw.pop("source", "arxiv"),
                      endpoint=kw.pop("endpoint", "query"), **kw)

    try:
        with db.connect() as conn:
            cfg = config_store.load(conn)
            rlog.config_version = cfg["version"]
            queries = list(cfg["niche_queries"])
            queries.append(" OR ".join(f"cat:{c}" for c in cfg["broad_categories"]))

            upserted: set[str] = set()
            with arxiv_lock(conn, wait=True):
                for query in queries:
                    for rp in arxiv.paginate(
                        query, since=since, page_size=PAGE_SIZE,
                        max_requests=MAX_REQUESTS_PER_QUERY, on_call=on_call,
                    ):
                        upserted.add(upsert_paper(conn, rp))
                    rlog.stat("upserted_total", len(upserted))

            # term_counts baselines across ALL history weeks
            for term in _tracked_terms(conn, cfg):
                terms.recompute_term(conn, term)
            terms.recompute_category_volume(conn)
            terms.refresh_last_seen(conn)
            rlog.stat("papers_upserted", len(upserted))
            rlog.stat("history_weeks", HISTORY_WEEKS)

        summary = rlog.finish("ok")
        print(summary)
        return summary
    except Exception as exc:  # noqa: BLE001
        rlog.error("run_bootstrap", exc)
        summary = rlog.finish("error")
        print(summary, file=sys.stderr)
        raise


def _tracked_terms(conn, cfg: dict) -> list[str]:
    rows = db.q(conn, "SELECT term FROM vocab")
    live = {r["term"] for r in rows}
    live.update((cfg.get("seed_vocab") or {}).keys())
    return sorted(live)


if __name__ == "__main__":
    res = run_bootstrap()
    sys.exit(0 if res.get("status") == "ok" else 1)
