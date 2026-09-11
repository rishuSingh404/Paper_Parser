"""Postgres advisory locks that keep the worker's components from stepping on
each other. Two, for two different problems:

  ARXIV_LOCK_KEY    - the one every component takes before touching arXiv, to
                      honour arXiv's one-connection / >=3s rule across the Web
                      Service and the bootstrap Job at once.
  PIPELINE_LOCK_KEY - the whole pipeline (run_daily / run_bootstrap), to stop
                      TWO FULL RUNS overlapping. Discovered the hard way: a
                      slow/hung request (Render free-tier request or the
                      client retrying) plus a second manual trigger left 2-3
                      full pipelines (each loading data + running the scorer
                      over thousands of candidates) running at once on a
                      512 MB instance -> OOM, container restart, every one of
                      them orphaned as a permanently "running" row with no
                      finished_at. See run_daily()/run_bootstrap(): they take
                      this non-blocking, on a connection held open for the
                      run's full duration, and return {"status": "skipped"}
                      immediately (no run_log row created) rather than queue
                      or block, since a skip is cheap to retry on the next
                      scheduled trigger but a pile-up is not.

  wait=True  -> pg_advisory_lock (blocks). Used by the daily pipeline / bootstrap
                for ARXIV_LOCK_KEY specifically.
  wait=False -> pg_try_advisory_lock. Used by the dashboard-triggered backfill
                (ARXIV_LOCK_KEY) and by every PIPELINE_LOCK_KEY caller.

The lock is session-scoped: the connection MUST stay open for the whole
critical section.
"""
from __future__ import annotations

import contextlib
from typing import Iterator

import psycopg

ARXIV_LOCK_KEY = 0x50524152      # ascii "PRAR"
PIPELINE_LOCK_KEY = 0x50524152 + 1  # distinct key, one whole-pipeline run at a time


class LockUnavailable(RuntimeError):
    pass


@contextlib.contextmanager
def arxiv_lock(conn: psycopg.Connection, *, wait: bool = False) -> Iterator[None]:
    with conn.cursor() as cur:
        if wait:
            cur.execute("SELECT pg_advisory_lock(%s)", (ARXIV_LOCK_KEY,))
            got = True
        else:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (ARXIV_LOCK_KEY,))
            got = bool(cur.fetchone()["ok"])
    if not got:
        raise LockUnavailable("arXiv advisory lock is held by another component")
    try:
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (ARXIV_LOCK_KEY,))


def try_pipeline_lock(conn: psycopg.Connection) -> bool:
    """Non-blocking. `conn` must stay open for the whole run if this returns
    True — call release_pipeline_lock(conn) in a finally, always, even on
    exception. Returns False if another full pipeline run already holds it;
    the caller's job is to bail out immediately (cheap to retry later), not
    to queue or wait — see module docstring."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (PIPELINE_LOCK_KEY,))
        return bool(cur.fetchone()["ok"])


def release_pipeline_lock(conn: psycopg.Connection) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (PIPELINE_LOCK_KEY,))
    except Exception:
        pass  # connection may already be dead; nothing more to do
