"""Citation velocity -> tier. A LAGGING TAG, never fed into the rank (plan step
10 / spec 4.6)."""
from __future__ import annotations

import psycopg

from .. import db


def tiers(conn: psycopg.Connection, cfg: dict) -> dict[str, dict]:
    """paper_id -> {tier, delta} using the 30-day citation_snapshots delta.

    tier: 'hot' (>= hot_threshold) | 'warming' (>= warming, < hot) | 'flat' (0)
          | 'ndata' (null: <2 snapshots or <30d span)
    """
    hot = cfg["citation_hot_threshold"]
    warm = cfg["citation_warming_threshold"]
    rows = db.q(
        conn,
        """
        WITH latest AS (
            SELECT DISTINCT ON (paper_id) paper_id, snapshot_date, citation_count
            FROM citation_snapshots ORDER BY paper_id, snapshot_date DESC
        ),
        prior AS (
            SELECT s.paper_id,
                   (array_agg(s.citation_count ORDER BY abs(s.snapshot_date - (l.snapshot_date - 30))))[1] AS cc,
                   min(abs(s.snapshot_date - (l.snapshot_date - 30))) AS gap
            FROM citation_snapshots s
            JOIN latest l USING (paper_id)
            WHERE s.snapshot_date <= l.snapshot_date - 14
            GROUP BY s.paper_id
        )
        SELECT l.paper_id, l.citation_count AS latest_cc, p.cc AS prior_cc
        FROM latest l LEFT JOIN prior p USING (paper_id)
        """,
    )
    out: dict[str, dict] = {}
    for r in rows:
        if r["prior_cc"] is None:
            out[r["paper_id"]] = {"tier": "ndata", "delta": None}
            continue
        delta = int(r["latest_cc"]) - int(r["prior_cc"])
        if delta >= hot:
            tier = "hot"
        elif delta >= warm:
            tier = "warming"
        elif delta <= 0:
            tier = "flat"
        else:
            tier = "warming"
        out[r["paper_id"]] = {"tier": tier, "delta": delta}
    return out


BADGE = {"hot": "🔥", "warming": "📈", "flat": "⚪", "ndata": "—"}
