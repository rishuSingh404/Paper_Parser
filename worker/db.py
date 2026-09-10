"""Thin Postgres helpers built on psycopg 3.

Every call site uses `with connect() as conn:` — the context manager commits on
clean exit and rolls back on exception. Rows come back as dicts.
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from . import settings


@contextlib.contextmanager
def connect() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(settings.require_database_url(), row_factory=dict_row, autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def q(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def q1(conn: psycopg.Connection, sql: str, params: Any = None) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


def execute(conn: psycopg.Connection, sql: str, params: Any = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.rowcount


def claim_marker(conn: psycopg.Connection, marker: str) -> bool:
    """Return True exactly once per marker string (idempotency guard)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO run_markers (marker) VALUES (%s) ON CONFLICT DO NOTHING",
            (marker,),
        )
        return cur.rowcount == 1
