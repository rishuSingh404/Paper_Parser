r"""term_counts / category_volume maintenance.

Counts are recomputed from `papers` in SQL rather than incremented, so a re-run
or an out-of-order backfill can never double-count (plan: "all steps idempotent",
and the term-backfill "no double-counting" verification item).

Bucketing is by each paper's real announce/publication ISO week
(to_char(..., 'IYYY"-W"IW')), never the processing date. Surveys are excluded.
Matching is word-boundary via the POSIX \y operator — mirrors
textutil.word_boundary_match so Python-side and SQL-side agree.
"""
from __future__ import annotations

import psycopg

from ..textutil import escape_term_posix

# papers that count toward momentum
_ELIGIBLE = "NOT muted AND NOT is_survey AND COALESCE(announce_date, published_date) IS NOT NULL"
_WEEK = "to_char(COALESCE(announce_date, published_date), 'IYYY\"-W\"IW')"


def recompute_term(conn: psycopg.Connection, term: str, *, only_weeks: list[str] | None = None) -> int:
    """Recompute term_counts rows for one term. If `only_weeks` is given, limit
    to those ISO weeks (cheaper for the daily run); otherwise all weeks."""
    pattern = r"\y" + escape_term_posix(term) + r"\y"
    week_filter = ""
    params: list = [term, pattern]
    if only_weeks:
        week_filter = f"AND {_WEEK} = ANY(%s)"
        params.append(only_weeks)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO term_counts (term, iso_week, count, distinct_authors)
            SELECT %s AS term,
                   {_WEEK} AS iso_week,
                   count(*) AS count,
                   count(DISTINCT first_author_group) AS distinct_authors
            FROM papers
            WHERE {_ELIGIBLE}
              AND (title || ' ' || COALESCE(abstract, '')) ~* %s
              {week_filter}
            GROUP BY {_WEEK}
            ON CONFLICT (term, iso_week) DO UPDATE SET
                count = EXCLUDED.count,
                distinct_authors = EXCLUDED.distinct_authors
            """,
            params,
        )
        return cur.rowcount


def recompute_category_volume(conn: psycopg.Connection, only_weeks: list[str] | None = None) -> int:
    week_filter = ""
    params: list = []
    if only_weeks:
        week_filter = f"AND {_WEEK} = ANY(%s)"
        params.append(only_weeks)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO category_volume (iso_week, category, count)
            SELECT {_WEEK} AS iso_week, cat AS category, count(*) AS count
            FROM papers, unnest(categories) AS cat
            WHERE {_ELIGIBLE}
              {week_filter}
            GROUP BY {_WEEK}, cat
            ON CONFLICT (iso_week, category) DO UPDATE SET count = EXCLUDED.count
            """,
            params,
        )
        return cur.rowcount


def refresh_last_seen(conn: psycopg.Connection) -> None:
    """Sync vocab.last_seen_iso_week to the most recent week each term has a
    non-zero count (drives calendar-correct pruning in Phase 4)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE vocab v SET last_seen_iso_week = sub.wk
            FROM (
                SELECT term, max(iso_week) AS wk
                FROM term_counts WHERE count > 0 GROUP BY term
            ) sub
            WHERE sub.term = v.term
              AND (v.last_seen_iso_week IS NULL OR sub.wk > v.last_seen_iso_week)
            """
        )
