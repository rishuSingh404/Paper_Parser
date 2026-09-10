"""Citation snapshots + local co-citation graph.

- Snapshot: OpenAlex `cited_by_count` -> Crossref `is-referenced-by-count`
  -> S2 `citationCount`. One row/paper/day in citation_snapshots (a lagging
  "established" TAG, never a rank input).
- Reference graph: OpenAlex `referenced_works` (already stashed by the openalex
  ingest, else a fresh call). Edges land in citation_edges; co-citation velocity
  = # of citing papers first-seen in the last `cocitation_lookback_weeks`.

All best-effort: a source being down just means fewer snapshots this run.
"""
from __future__ import annotations

import datetime as dt

import psycopg
from psycopg.types.json import Json

from .. import db, http, settings

# a paper needs time to accrue citations before a count is meaningful
MIN_AGE_DAYS = 14


def _openalex_batch(ext_ids: list[str], on_call=None) -> dict[str, int]:
    """ext_ids like 'doi:10.x/y' or 'arxiv:2401.00001'. Returns {ext_id: count}."""
    out: dict[str, int] = {}
    p = {"select": "ids,cited_by_count,referenced_works", "per-page": 50}
    if settings.OPENALEX_API_KEY:
        p["api_key"] = settings.OPENALEX_API_KEY
    for i in range(0, len(ext_ids), 50):
        chunk = ext_ids[i:i + 50]
        oa_filter = "|".join(
            (f"doi:{e.split(':', 1)[1]}" if e.startswith("doi:")
             else f"ids.openalex:{e.split(':', 1)[1]}" if e.startswith("openalex:")
             else f"ids.arxiv:{e.split(':', 1)[1]}")
            for e in chunk
        )
        data = http.get_json(
            "https://api.openalex.org/works",
            params={**p, "filter": oa_filter},
            source="openalex", endpoint="works?filter=ids", on_call=on_call, pace=0.5,
        )
        for w in (data or {}).get("results", []) or []:
            ids = w.get("ids") or {}
            doi = (ids.get("doi") or "").replace("https://doi.org/", "")
            ax = (ids.get("arxiv") or "").rsplit("/", 1)[-1]
            for key in ([f"doi:{doi}"] if doi else []) + ([f"arxiv:{ax}"] if ax else []):
                out[key] = w.get("cited_by_count") or 0
    return out


def refresh_snapshots(conn: psycopg.Connection, *, limit: int = 400, on_call=None) -> dict:
    cutoff = dt.date.today() - dt.timedelta(days=MIN_AGE_DAYS)
    rows = db.q(
        conn,
        """
        SELECT p.paper_id, p.doi, p.arxiv_id
        FROM papers p
        WHERE p.first_seen_at::date <= %s
          AND NOT EXISTS (
            SELECT 1 FROM citation_snapshots s
            WHERE s.paper_id = p.paper_id AND s.snapshot_date = CURRENT_DATE
          )
        ORDER BY p.first_seen_at DESC
        LIMIT %s
        """,
        (cutoff, limit),
    )
    if not rows:
        return {"snapshotted": 0, "eligible": 0}

    ext_of: dict[str, str] = {}
    for r in rows:
        if r["doi"]:
            ext_of[r["paper_id"]] = f"doi:{r['doi']}"
        elif r["arxiv_id"]:
            ext_of[r["paper_id"]] = f"arxiv:{r['arxiv_id']}"
    counts = _openalex_batch(sorted(set(ext_of.values())), on_call=on_call)

    n = 0
    for pid, ext in ext_of.items():
        c = counts.get(ext)
        if c is None:
            continue
        db.execute(
            conn,
            "INSERT INTO citation_snapshots (paper_id, snapshot_date, citation_count, source) "
            "VALUES (%s, CURRENT_DATE, %s, 'openalex') "
            "ON CONFLICT (paper_id, snapshot_date) DO UPDATE SET citation_count = EXCLUDED.citation_count",
            (pid, int(c)),
        )
        n += 1
    return {"snapshotted": n, "eligible": len(rows)}


def build_reference_edges(conn: psycopg.Connection, *, limit: int = 300, on_call=None) -> dict:
    """Use referenced_works stashed by the openalex ingest to link NEW arXiv
    papers to the tracked papers they cite."""
    rows = db.q(
        conn,
        """
        SELECT ps.paper_id, ps.raw
        FROM paper_sources ps
        WHERE ps.source = 'openalex'
          AND ps.raw ? 'referenced_works'
          AND ps.first_seen_at > now() - interval '45 days'
        ORDER BY ps.first_seen_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    # map OpenAlex work id -> our paper_id, for papers we track
    known = db.q(conn, "SELECT paper_id, raw->>'openalex_id' AS oaid FROM paper_sources "
                       "WHERE source = 'openalex' AND raw ? 'openalex_id'")
    oa_to_pid = {k["oaid"]: k["paper_id"] for k in known if k["oaid"]}

    edges = 0
    for r in rows:
        citing = r["paper_id"]
        for ref in (r["raw"] or {}).get("referenced_works", []) or []:
            cited_pid = oa_to_pid.get(ref)
            if not cited_pid or cited_pid == citing:
                continue
            db.execute(
                conn,
                "INSERT INTO citation_edges (citing_paper_id, cited_paper_id) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (citing, cited_pid),
            )
            edges += 1
    return {"edges_added": edges, "scanned": len(rows)}


def cocitation_velocity(conn: psycopg.Connection, lookback_weeks: int) -> dict[str, int]:
    """paper_id -> # of distinct citing papers first seen in the last N weeks."""
    rows = db.q(
        conn,
        """
        SELECT e.cited_paper_id AS pid, count(DISTINCT e.citing_paper_id) AS v
        FROM citation_edges e
        JOIN papers c ON c.paper_id = e.citing_paper_id
        WHERE c.first_seen_at > now() - make_interval(weeks => %s)
        GROUP BY e.cited_paper_id
        """,
        (lookback_weeks,),
    )
    return {r["pid"]: int(r["v"]) for r in rows}
