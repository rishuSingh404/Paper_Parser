"""The single Postgres advisory lock that every component takes before touching
arXiv. Honours arXiv's one-connection / >=3s rule across the Cron Job, the Web
Service, and the bootstrap Job at once.

  wait=True  -> pg_advisory_lock (blocks). Used by the daily pipeline / bootstrap.
  wait=False -> pg_try_advisory_lock. Used by the dashboard-triggered backfill;
                on failure the caller enqueues the term_backfills row and
                returns "queued" instead of blocking.

The lock is session-scoped: the connection MUST stay open for the whole
critical section.
"""
from __future__ import annotations

import contextlib
from typing import Iterator

import psycopg

ARXIV_LOCK_KEY = 0x50524152  # ascii "PRAR"


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
