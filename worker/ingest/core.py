"""CORE v3 — the main breadth lever for non-arXiv preprints + institutional-repo
open access (~300M works). Needs a free API key; skipped entirely without one.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http, settings
from .base import RawPaper

name = "core"
_URL = "https://api.core.ac.uk/v3/search/works"


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    if not settings.CORE_API_KEY:
        return
    frm = (since.date() if since else dt.date.today() - dt.timedelta(days=45))
    limit = int(params.get("max_per_query", 30))
    for term in params.get("queries", []):
        q = f'{term} AND publishedDate>={frm.isoformat()}'
        data = http.post_json(
            _URL,
            json_body={"q": q, "limit": min(limit, 100),
                       "sort": [{"publishedDate": "desc"}]},
            headers={"Authorization": f"Bearer {settings.CORE_API_KEY}"},
            source="core", endpoint="search/works", on_call=on_call,
        )
        for it in (data or {}).get("results", []) or []:
            title = it.get("title") or ""
            if not title:
                continue
            d = None
            for k in ("publishedDate", "createdDate", "depositedDate"):
                if it.get(k):
                    try:
                        d = dt.datetime.fromisoformat(it[k].replace("Z", "+00:00")).date()
                        break
                    except (ValueError, AttributeError):
                        pass
            yield RawPaper(
                source="core",
                source_id=str(it.get("id") or it.get("doi") or title[:80]),
                doi=it.get("doi"),
                arxiv_id=(it.get("arxivId") or None),
                title=title,
                abstract=it.get("abstract"),
                authors=[a.get("name") for a in (it.get("authors") or []) if a.get("name")],
                published_date=d,
                announce_date=d,
                url=(it.get("downloadUrl") or (it.get("links") or [{}])[0].get("url")),
                extra={"publisher": it.get("publisher")},
            )
