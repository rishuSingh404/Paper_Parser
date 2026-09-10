"""Crossref — breadth (OA journals / proceedings) + light metadata.

No key; use the polite pool via `mailto`. One query per tracked term/phrase,
filtered to recently created works.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http, settings
from ..textutil import strip_tags
from .base import RawPaper

name = "crossref"
_URL = "https://api.crossref.org/works"


def _date(parts) -> dt.date | None:
    try:
        p = (parts or {}).get("date-parts", [[None]])[0]
        if not p or p[0] is None:
            return None
        return dt.date(p[0], p[1] if len(p) > 1 else 1, p[2] if len(p) > 2 else 1)
    except Exception:
        return None


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    frm = (since.date() if since else dt.date.today() - dt.timedelta(days=30)).isoformat()
    rows = int(params.get("max_per_query", 40))
    for term in params.get("queries", []):
        data = http.get_json(
            _URL,
            params={
                "query.bibliographic": term,
                "filter": f"from-created-date:{frm},type:journal-article",
                "rows": rows,
                "select": "DOI,title,abstract,author,created,published,subject,type,URL",
                "mailto": settings.CROSSREF_MAILTO or "unknown@example.com",
                "sort": "created",
                "order": "desc",
            },
            source="crossref", endpoint="works", on_call=on_call, pace=1.0,
        )
        items = ((data or {}).get("message") or {}).get("items") or []
        for it in items:
            title = " ".join(it.get("title") or []) or ""
            if not title:
                continue
            authors = [
                " ".join(x for x in [a.get("given"), a.get("family")] if x)
                for a in (it.get("author") or [])
            ]
            d = _date(it.get("published")) or _date(it.get("created"))
            yield RawPaper(
                source="crossref",
                source_id=it.get("DOI") or title[:80],
                doi=it.get("DOI"),
                title=title,
                abstract=strip_tags(it.get("abstract")),
                authors=[a for a in authors if a.strip()],
                published_date=d,
                announce_date=d,
                url=it.get("URL"),
                categories=list(it.get("subject") or []),
                extra={"type": it.get("type")},
            )
