"""Mention-frequency momentum + multi-lab burst (spec 4.2; plan steps 5-6).

Self-relative: current-week distinct-paper count minus the mean of the trailing
6 *present* weeks. A term appearing 3x/wk after never appearing beats a term
appearing 3x/wk every week.
"""
from __future__ import annotations

import psycopg

from .. import db
from ..isoweek import recent_weeks
from ..textutil import escape_term_posix

LOOKBACK = 6
BURST_WINDOW_WEEKS = 3


def momentum_for(conn: psycopg.Connection, terms: list[str], current_week: str,
                 lookback: int = LOOKBACK) -> dict[str, dict]:
    weeks = recent_weeks(current_week, lookback)
    rows = db.q(
        conn,
        "SELECT term, iso_week, count FROM term_counts WHERE term = ANY(%s) AND iso_week = ANY(%s)",
        (terms, weeks + [current_week]),
    )
    by_term: dict[str, dict[str, int]] = {}
    for r in rows:
        by_term.setdefault(r["term"], {})[r["iso_week"]] = r["count"]

    out: dict[str, dict] = {}
    for t in terms:
        wk = by_term.get(t, {})
        baseline_vals = [wk.get(w, 0) for w in weeks]
        baseline = sum(baseline_vals) / len(baseline_vals) if baseline_vals else 0.0
        current = wk.get(current_week, 0)
        out[t] = {
            "term": t,
            "current": current,
            "baseline": round(baseline, 3),
            "momentum": round(current - baseline, 3),
        }
    return out


def rising_terms(conn: psycopg.Connection, tracked: list[str], current_week: str,
                 top: int = 8) -> list[dict]:
    m = momentum_for(conn, tracked, current_week)
    ranked = sorted(m.values(), key=lambda d: (-d["momentum"], -d["current"], d["term"]))
    return [d for d in ranked if d["momentum"] > 0][:top] or ranked[:top]


def bursts(conn: psycopg.Connection, tracked: list[str], current_week: str,
           min_groups: int) -> list[dict]:
    """Distinct first-author groups matching a term in the last 3 ISO weeks vs
    the prior 3. Flags when >= min_groups and up from the prior window."""
    cur3 = [current_week] + recent_weeks(current_week, BURST_WINDOW_WEEKS - 1)
    prev_anchor = recent_weeks(current_week, BURST_WINDOW_WEEKS)[0]
    prev3 = [prev_anchor] + recent_weeks(prev_anchor, BURST_WINDOW_WEEKS - 1)

    def _groups(term: str, weeks: list[str]) -> int:
        pat = r"\y" + escape_term_posix(term) + r"\y"
        row = db.q1(
            conn,
            """
            SELECT count(DISTINCT first_author_group) AS g
            FROM papers
            WHERE NOT muted AND NOT is_survey AND first_author_group IS NOT NULL
              AND to_char(COALESCE(announce_date, published_date), 'IYYY"-W"IW') = ANY(%s)
              AND (title || ' ' || COALESCE(abstract, '')) ~* %s
            """,
            (weeks, pat),
        )
        return int(row["g"]) if row else 0

    flagged = []
    for t in tracked:
        k_now = _groups(t, cur3)
        k_prev = _groups(t, prev3)
        db.execute(
            conn,
            "INSERT INTO term_bursts (term, iso_week, distinct_groups, prev_distinct_groups) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (term, iso_week) DO UPDATE SET "
            "distinct_groups = EXCLUDED.distinct_groups, prev_distinct_groups = EXCLUDED.prev_distinct_groups",
            (t, current_week, k_now, k_prev),
        )
        if k_now >= min_groups and k_now > k_prev:
            flagged.append({"term": t, "distinct_groups": k_now, "prev_distinct_groups": k_prev})
    flagged.sort(key=lambda d: -d["distinct_groups"])
    return flagged
