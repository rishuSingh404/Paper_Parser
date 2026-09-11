"""One-off bootstrap backfill:  python -m worker.backfill

Fills enough history that momentum (6-week baseline) works on day 1 instead of
being blind for ~2 months. Resumable: term_counts is recomputed from `papers`,
so re-running only widens coverage.

  BOOTSTRAP_HISTORY_WEEKS   (default 14)
  BOOTSTRAP_MAX_REQUESTS    (default 40)  per arXiv query

After this runs, do the initial like-pass over ~20-30 papers from the dashboard,
then let the daily cron take over. Citation snapshots need a second pass ~2 weeks
later before the lagging tag means anything.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

from . import config_store, db
from .arxiv_lock import arxiv_lock, release_pipeline_lock, try_pipeline_lock
from .enrich import abstracts, citations, embeddings, tags
from .ingest import arxiv
from .ingest.base import upsert_papers_batch
from .ingest.registry import enabled_generic
from .observability import RunLog
from .pipeline import niche
from .pipeline import terms as term_counts

HISTORY_WEEKS = int(os.environ.get("BOOTSTRAP_HISTORY_WEEKS", "14"))
MAX_REQUESTS_PER_QUERY = int(os.environ.get("BOOTSTRAP_MAX_REQUESTS", "40"))
PAGE_SIZE = 100


def run_bootstrap() -> dict:
    today = dt.date.today()
    since = dt.datetime.now() - dt.timedelta(weeks=HISTORY_WEEKS)

    # Shares PIPELINE_LOCK_KEY with run_daily — a bootstrap and a daily run
    # must not overlap either, same OOM/pile-up risk. See arxiv_lock.py.
    lock_conn = db.raw_connect()
    if not try_pipeline_lock(lock_conn):
        lock_conn.close()
        return {"status": "skipped", "reason": "another pipeline run is already in progress"}

    rlog = RunLog(kind="backfill", run_date=today).start()

    def on_call(**kw):
        rlog.api_call(source=kw.pop("source", "?"), endpoint=kw.pop("endpoint", "?"), **kw)

    seen: set[str] = set()
    try:
        with db.connect() as conn:
            cfg = config_store.load(conn)
            config_store.sync_seed_vocab(conn, cfg)
            rlog.config_version = cfg["version"]

            # ---- arXiv history (under the lock) ---------------------------
            queries = list(cfg["niche_queries"])
            queries.append(" OR ".join(f"cat:{c}" for c in cfg["broad_categories"]))
            with arxiv_lock(conn, wait=True):
                for q in queries:
                    batch: list = []
                    for rp in arxiv.paginate(q, since=since.date(), page_size=PAGE_SIZE,
                                             max_requests=MAX_REQUESTS_PER_QUERY, on_call=on_call):
                        batch.append(rp)
                        if len(batch) >= PAGE_SIZE:
                            seen.update(upsert_papers_batch(conn, batch))
                            batch = []
                    if batch:
                        seen.update(upsert_papers_batch(conn, batch))
                    rlog.stat("arxiv_upserted", len(seen))
            conn.commit()  # durable checkpoint — a later failure must not cost this

            # ---- generic sources, wide window --------------------------
            phrases = niche.niche_phrases(cfg)
            top_terms = [r["term"] for r in db.q(conn, "SELECT term FROM vocab ORDER BY weight DESC LIMIT 16")]
            gq = sorted(set(phrases + top_terms))
            params = {"queries": gq, "keywords": gq, "categories": cfg["broad_categories"],
                      "max_per_query": 60, "hf_max_days": HISTORY_WEEKS * 7,
                      "biorxiv_page_cap": 10}
            for mod in enabled_generic(cfg):
                n = 0
                try:
                    rps = list(mod.fetch(since, params, on_call=on_call))
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
                    conn.commit()  # checkpoint per source — one source's later
                                   # failure (or connection loss) must not roll
                                   # back sources that already succeeded
                except Exception as exc:
                    rlog.error(f"ingest:{mod.name}", exc)
                    try:
                        conn.rollback()  # clear the aborted transaction so the
                                         # NEXT source can still use this conn
                    except Exception:
                        pass  # connection itself is dead — the outer try/except
                              # will end the run; whatever committed above stands
                rlog.incr(f"src_{mod.name}", n)

            # ---- baselines across ALL history weeks --------------------
            tracked = sorted({*(cfg.get("seed_vocab") or {}),
                              *(r["term"] for r in db.q(conn, "SELECT term FROM vocab"))})
            for t in tracked:
                term_counts.recompute_term(conn, t)
            term_counts.recompute_category_volume(conn)
            term_counts.refresh_last_seen(conn)
            conn.commit()

            # ---- enrichment (each step its own checkpoint) -----------------
            for fn, label in (
                (lambda: abstracts.resolve_missing(conn, limit=800, on_call=on_call), "abstracts"),
                (lambda: tags.apply(conn, limit=2000), "tags"),
                (lambda: citations.refresh_snapshots(conn, limit=800, on_call=on_call), "cite_snapshots"),
                (lambda: citations.build_reference_edges(conn, limit=1500, on_call=on_call), "cite_edges"),
                (lambda: embeddings.embed_new_papers(conn, cfg["embedding_model_version"], limit=5000, on_call=on_call), "embed"),
                (lambda: embeddings.embed_anchors(conn, cfg, on_call=on_call), "anchors"),
            ):
                try:
                    rlog.stat(label, fn())
                    conn.commit()
                except Exception as exc:
                    rlog.error(f"enrich:{label}", exc)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

            rlog.stat("papers_upserted", len(seen))
            rlog.stat("history_weeks", HISTORY_WEEKS)

        summary = rlog.finish("ok")
        print(json.dumps(summary, indent=2, default=str))
        return summary
    except Exception as exc:  # noqa: BLE001
        rlog.error("run_bootstrap", exc)
        summary = rlog.finish("error")
        print(json.dumps(summary, indent=2, default=str), file=sys.stderr)
        raise
    finally:
        release_pipeline_lock(lock_conn)
        lock_conn.close()


if __name__ == "__main__":
    res = run_bootstrap()
    sys.exit(0 if res.get("status") == "ok" else 1)
