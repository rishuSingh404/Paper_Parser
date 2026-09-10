"""Copy OpenAlex concept tags and linked-repo URLs from `paper_sources.raw`
onto `papers` so the scorer can read them cheaply. Also scrapes github.com
links out of arXiv abstracts.
"""
from __future__ import annotations

import re

import psycopg

from .. import db

_GITHUB = re.compile(r"https?://github\.com/[\w.\-]+/[\w.\-]+", re.I)


def apply(conn: psycopg.Connection, *, limit: int = 600) -> dict:
    # concepts from openalex ingest raw
    oa = db.q(
        conn,
        "SELECT paper_id, raw->'concepts' AS concepts, raw->>'repo' AS repo "
        "FROM paper_sources WHERE source IN ('openalex','hf_daily') "
        "AND first_seen_at > now() - interval '45 days' LIMIT %s",
        (limit,),
    )
    tagged = 0
    for r in oa:
        concepts = r["concepts"] or []
        repo = r["repo"]
        if concepts:
            db.execute(
                conn,
                "UPDATE papers SET concepts = %s WHERE paper_id = %s AND cardinality(concepts) = 0",
                (list(concepts), r["paper_id"]),
            )
            tagged += 1
        if repo:
            db.execute(conn, "UPDATE papers SET repo_url = COALESCE(repo_url, %s) WHERE paper_id = %s",
                       (repo, r["paper_id"]))

    # github links from arXiv abstracts
    ax = db.q(
        conn,
        "SELECT paper_id, abstract FROM papers "
        "WHERE repo_url IS NULL AND abstract ILIKE '%%github.com%%' "
        "AND first_seen_at > now() - interval '45 days' LIMIT %s",
        (limit,),
    )
    for r in ax:
        m = _GITHUB.search(r["abstract"] or "")
        if m:
            db.execute(conn, "UPDATE papers SET repo_url = %s WHERE paper_id = %s",
                       (m.group(0), r["paper_id"]))
    return {"concept_tagged": tagged, "repo_scanned": len(ax)}
