"""OpenAlex — breadth + enrichment payload in one call.

Stashes `cited_by_count`, `concepts`, `referenced_works`, `openalex_id` in
RawPaper.extra so worker/enrich can use them without a second request. Sends the
free API key if configured; degrades quietly on 402/quota (returns nothing).
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http, settings
from ..textutil import abstract_from_inverted_index
from .base import RawPaper

name = "openalex"
_URL = "https://api.openalex.org/works"

_SELECT = ",".join([
    "id", "doi", "title", "abstract_inverted_index", "authorships",
    "publication_date", "primary_location", "concepts", "referenced_works",
    "cited_by_count", "ids",
])


def _params_base() -> dict:
    p = {"select": _SELECT, "per-page": 50}
    if settings.OPENALEX_API_KEY:
        p["api_key"] = settings.OPENALEX_API_KEY
    if settings.CROSSREF_MAILTO:
        p["mailto"] = settings.CROSSREF_MAILTO
    return p


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    frm = (since.date() if since else dt.date.today() - dt.timedelta(days=30)).isoformat()
    per_q = int(params.get("max_per_query", 40))
    for term in params.get("queries", []):
        p = _params_base()
        p.update({
            "search": term,
            "filter": f"from_created_date:{frm}",
            "sort": "publication_date:desc",
            "per-page": min(per_q, 200),
        })
        data = http.get_json(_URL, params=p, source="openalex", endpoint="works",
                             on_call=on_call, pace=0.5)
        for w in (data or {}).get("results", []) or []:
            title = w.get("title") or ""
            if not title:
                continue
            authors = [
                (a.get("author") or {}).get("display_name")
                for a in (w.get("authorships") or [])
            ]
            d = None
            if w.get("publication_date"):
                try:
                    d = dt.date.fromisoformat(w["publication_date"])
                except ValueError:
                    d = None
            doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
            loc = w.get("primary_location") or {}
            concepts = [c.get("display_name") for c in (w.get("concepts") or []) if c.get("score", 0) >= 0.3]
            arxiv_id = ((w.get("ids") or {}).get("arxiv") or "").rsplit("/", 1)[-1] or None
            yield RawPaper(
                source="openalex",
                source_id=(w.get("id") or "").rsplit("/", 1)[-1] or title[:80],
                doi=doi,
                arxiv_id=arxiv_id,
                title=title,
                abstract=abstract_from_inverted_index(w.get("abstract_inverted_index")),
                authors=[a for a in authors if a],
                published_date=d,
                announce_date=d,
                url=loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None),
                extra={
                    "openalex_id": w.get("id"),
                    "cited_by_count": w.get("cited_by_count"),
                    "concepts": concepts,
                    "referenced_works": w.get("referenced_works") or [],
                },
            )
