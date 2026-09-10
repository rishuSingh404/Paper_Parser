"""Resolve missing abstracts for DBLP / Crossref-sourced rows.

Order: Crossref `abstract` (by DOI) -> OpenAlex `abstract_inverted_index`
(by DOI) -> give up. On give-up the row keeps abstract=NULL / abstract_missing
and score.py takes the title-only path (never a silent drop).
"""
from __future__ import annotations

import psycopg

from .. import db, http, settings
from ..textutil import abstract_from_inverted_index, strip_tags


def _from_crossref(doi: str, on_call=None) -> str | None:
    data = http.get_json(
        f"https://api.crossref.org/works/{doi}",
        params={"mailto": settings.CROSSREF_MAILTO or "unknown@example.com"},
        source="crossref", endpoint="works/{doi}", on_call=on_call, pace=1.0,
    )
    return strip_tags(((data or {}).get("message") or {}).get("abstract"))


def _from_openalex(doi: str, on_call=None) -> str | None:
    p = {}
    if settings.OPENALEX_API_KEY:
        p["api_key"] = settings.OPENALEX_API_KEY
    data = http.get_json(
        f"https://api.openalex.org/works/https://doi.org/{doi}",
        params={**p, "select": "abstract_inverted_index"},
        source="openalex", endpoint="works/{doi}", on_call=on_call, pace=0.5,
    )
    return abstract_from_inverted_index((data or {}).get("abstract_inverted_index"))


def resolve_missing(conn: psycopg.Connection, *, limit: int = 200, on_call=None) -> dict:
    rows = db.q(
        conn,
        "SELECT paper_id, doi FROM papers "
        "WHERE abstract IS NULL AND doi IS NOT NULL "
        "ORDER BY first_seen_at DESC LIMIT %s",
        (limit,),
    )
    resolved = 0
    for r in rows:
        doi = r["doi"]
        text = _from_crossref(doi, on_call) or _from_openalex(doi, on_call)
        if text:
            db.execute(
                conn,
                "UPDATE papers SET abstract = %s, abstract_missing = FALSE WHERE paper_id = %s",
                (text, r["paper_id"]),
            )
            resolved += 1
    return {"checked": len(rows), "resolved": resolved}
