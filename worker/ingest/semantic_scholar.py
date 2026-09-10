"""Semantic Scholar Graph API — optional supplement.

Unauthenticated shared pool -> heavy 429s (the http helper backs off). Sends
x-api-key if configured. The system must run fully without this source.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http, settings
from .base import RawPaper

name = "s2"
_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "externalIds,title,abstract,authors,publicationDate,year,citationCount,influentialCitationCount"


def _headers() -> dict:
    return {"x-api-key": settings.SEMANTIC_SCHOLAR_API_KEY} if settings.SEMANTIC_SCHOLAR_API_KEY else {}


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    limit = int(params.get("max_per_query", 30))
    for term in params.get("queries", []):
        data = http.get_json(
            _URL,
            params={"query": term, "fields": _FIELDS, "limit": min(limit, 100)},
            headers=_headers(), source="s2", endpoint="paper/search",
            on_call=on_call, pace=2.0,
        )
        for p in (data or {}).get("data", []) or []:
            title = p.get("title") or ""
            if not title:
                continue
            ext = p.get("externalIds") or {}
            d = None
            if p.get("publicationDate"):
                try:
                    d = dt.date.fromisoformat(p["publicationDate"])
                except ValueError:
                    d = None
            elif p.get("year"):
                d = dt.date(int(p["year"]), 1, 1)
            yield RawPaper(
                source="s2",
                source_id=p.get("paperId") or ext.get("DOI") or title[:80],
                doi=ext.get("DOI"),
                arxiv_id=(ext.get("ArXiv") or None),
                title=title,
                abstract=p.get("abstract"),
                authors=[a.get("name") for a in (p.get("authors") or []) if a.get("name")],
                published_date=d,
                announce_date=d,
                url=(f"https://www.semanticscholar.org/paper/{p.get('paperId')}"
                     if p.get("paperId") else None),
                extra={
                    "citation_count": p.get("citationCount"),
                    "influential_citation_count": p.get("influentialCitationCount"),
                },
            )
