"""DBLP publication search.

** DBLP RETURNS NO ABSTRACTS. ** Every row here is created with abstract=None and
must be resolved by worker/enrich/abstracts.py (Crossref -> OpenAlex); if nothing
resolves, worker/pipeline/score.py scores it title-only with an `abstract_missing`
flag — it is never silently dropped.

Value: catch keyword-matching papers in real CS venues (title + venue + year +
DOI) that the arXiv sweep misses.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http
from .base import RawPaper

name = "dblp"
_URL = "https://dblp.org/search/publ/api"


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    min_year = (since.year if since else dt.date.today().year - 1)
    page_size = int(params.get("max_per_query", 30))
    for term in params.get("queries", []):
        data = http.get_json(
            _URL,
            params={"q": term, "format": "json", "h": min(page_size, 100)},
            source="dblp", endpoint="search/publ", on_call=on_call, pace=1.0,
        )
        hits = (((data or {}).get("result") or {}).get("hits") or {}).get("hit", []) or []
        if isinstance(hits, dict):  # DBLP returns a bare object for a single hit
            hits = [hits]
        for h in hits:
            info = h.get("info") or {}
            title = (info.get("title") or "").rstrip(".")
            if not title:
                continue
            try:
                year = int(info.get("year") or 0)
            except ValueError:
                year = 0
            if year and year < min_year:
                continue
            au = (info.get("authors") or {}).get("author") or []
            if isinstance(au, dict):
                au = [au]
            authors = [a.get("text") for a in au if isinstance(a, dict) and a.get("text")]
            yield RawPaper(
                source="dblp",
                source_id=info.get("key") or info.get("doi") or title[:80],
                doi=info.get("doi"),
                title=title,
                abstract=None,  # DBLP has none — see module docstring
                authors=authors,
                published_date=(dt.date(year, 1, 1) if year else None),
                announce_date=(dt.date(year, 1, 1) if year else None),
                url=info.get("ee") or info.get("url"),
                extra={"venue": info.get("venue"), "dblp_type": info.get("type")},
            )
