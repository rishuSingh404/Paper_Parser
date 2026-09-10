"""On-demand 6-month arXiv backfill for a keyword/query added from the dashboard.

Flow (plan: Dashboard "On-demand historical backfill"):
  1. dashboard POST /api/config enqueues a `term_backfills` row (status=pending)
  2. it calls this module's `run_one` via the worker web service; we take the
     arXiv advisory lock NON-WAITING — if the daily run holds it, we leave the
     row pending and return {"status": "queued"}
  3. otherwise we page the last `keyword_backfill_months`, upsert papers, and
     recompute term_counts BUCKETED BY REAL ANNOUNCE WEEK (never the add date)
  4. leftover (hit the request cap) -> status=partial + cursor; the next daily
     run's `drain_pending` finishes it with no double-counting
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import psycopg
from psycopg.types.json import Json

from .. import config_store, db, settings
from ..arxiv_lock import LockUnavailable, arxiv_lock
from ..pipeline import terms
from . import arxiv
from .base import upsert_paper


def _since(months: int) -> dt.date:
    return dt.date.today() - dt.timedelta(days=int(months) * 31)


def _query_for(term: str, kind: str) -> str:
    if kind == "query":
        return term
    return f'abs:"{term}" OR ti:"{term}"'


def _execute_backfill(conn: psycopg.Connection, bf: dict, months: int,
                      on_call=None) -> dict[str, Any]:
    """Run one backfill row to completion or to the request cap. Caller holds the
    arXiv lock."""
    term, kind = bf["term"], bf["kind"]
    start = int(bf["cursor"]) if bf.get("cursor") else 0
    since = _since(months)

    gen = arxiv.paginate(
        _query_for(term, kind),
        since=since,
        page_size=settings.TERM_BACKFILL_PAGE_SIZE,
        max_requests=settings.TERM_BACKFILL_MAX_REQUESTS,
        start=start,
        on_call=on_call,
    )
    seen: list[dict] = []
    cursor: int | None = None
    try:
        while True:
            rp = next(gen)
            pid = upsert_paper(conn, rp)
            seen.append({
                "paper_id": pid,
                "title": rp.title,
                "url": rp.url,
                "announce_date": rp.announce_date.isoformat() if rp.announce_date else None,
            })
    except StopIteration as stop:
        cursor = stop.value  # int -> partial; None -> complete

    if kind == "keyword":
        conn.execute(
            "INSERT INTO vocab (term, weight, is_seed) VALUES (%s, 1.0, TRUE) "
            "ON CONFLICT (term) DO NOTHING",
            (term,),
        )
        terms.recompute_term(conn, term)          # all weeks — idempotent
        terms.recompute_category_volume(conn)
        terms.refresh_last_seen(conn)

    status = "partial" if isinstance(cursor, int) else "done"
    stats = {
        "found": len(seen),
        "since": since.isoformat(),
        "months": months,
        "hit_request_cap": status == "partial",
    }
    conn.execute(
        "UPDATE term_backfills SET status = %s, cursor = %s, stats = %s WHERE id = %s",
        (status, str(cursor) if cursor is not None else None, Json(stats), bf["id"]),
    )
    return {"status": status, "term": term, "kind": kind, "found": len(seen),
            "papers": seen, "backfill_id": bf["id"]}


def run_one(term: str, kind: str = "keyword") -> dict[str, Any]:
    """Entry point for the web service. Non-blocking on the arXiv lock."""
    kind = "query" if kind == "query" else "keyword"
    with db.connect() as conn:
        cfg = config_store.load(conn)
        months = int(cfg["keyword_backfill_months"])
        row = db.q1(
            conn,
            "INSERT INTO term_backfills (term, kind, status) VALUES (%s, %s, 'pending') RETURNING *",
            (term, kind),
        )
        try:
            with arxiv_lock(conn, wait=False):
                return _execute_backfill(conn, dict(row), months)
        except LockUnavailable:
            return {"status": "queued", "term": term, "kind": kind,
                    "backfill_id": row["id"],
                    "note": "arXiv busy with the daily run; the next run will finish this"}


def drain_pending(conn: psycopg.Connection, on_call=None) -> dict[str, Any]:
    """Pipeline step 0. Caller already holds the arXiv lock on `conn`."""
    cfg = config_store.load(conn)
    months = int(cfg["keyword_backfill_months"])
    rows = db.q(
        conn,
        "SELECT * FROM term_backfills WHERE status IN ('pending','partial') "
        "ORDER BY requested_at ASC",
    )
    done = 0
    for r in rows:
        try:
            _execute_backfill(conn, dict(r), months, on_call=on_call)
            done += 1
        except Exception as exc:  # one bad term must not kill the run
            conn.execute(
                "UPDATE term_backfills SET status = 'error', stats = stats || %s WHERE id = %s",
                (Json({"error": repr(exc)}), r["id"]),
            )
    return {"drained": done, "open_before": len(rows)}
