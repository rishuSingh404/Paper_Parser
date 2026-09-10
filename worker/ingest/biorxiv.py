"""bioRxiv + medRxiv — medical / bio coverage arXiv misses.

Clean JSON, no auth, cursor-paginated 100/page. Daily volume is large, so a
daily run pulls a short window and keyword-filters against the tracked queries;
the bootstrap widens both.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http
from ..textutil import word_boundary_match
from .base import RawPaper

name = "biorxiv"
_SERVERS = ("biorxiv", "medrxiv")


def _keyword_hit(text: str, terms: list[str]) -> bool:
    if not terms:
        return True
    low = text.lower()
    return any(t.lower() in low for t in terms) or any(word_boundary_match(t, text) for t in terms)


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    until = dt.date.today()
    start = (since.date() if since else until - dt.timedelta(days=3))
    terms: list[str] = params.get("keywords", []) or params.get("queries_flat", [])
    page_cap = int(params.get("biorxiv_page_cap", 6))
    rng = f"{start.isoformat()}/{until.isoformat()}"

    for server in _SERVERS:
        cursor = 0
        for _ in range(page_cap):
            data = http.get_json(
                f"https://api.biorxiv.org/details/{server}/{rng}/{cursor}/json",
                source=f"{server}", endpoint="details", on_call=on_call, pace=0.5,
            )
            coll = (data or {}).get("collection") or []
            if not coll:
                break
            for it in coll:
                title = it.get("title") or ""
                abstract = it.get("abstract") or None
                if not _keyword_hit(f"{title} {abstract or ''}", terms):
                    continue
                authors = [a.strip() for a in (it.get("authors") or "").split(";") if a.strip()]
                d = None
                if it.get("date"):
                    try:
                        d = dt.date.fromisoformat(it["date"])
                    except ValueError:
                        d = None
                yield RawPaper(
                    source=server,
                    source_id=it.get("doi") or f"{server}:{title[:60]}",
                    doi=it.get("doi"),
                    title=title,
                    abstract=abstract,
                    authors=authors,
                    published_date=d,
                    announce_date=d,
                    url=(f"https://doi.org/{it['doi']}" if it.get("doi") else None),
                    categories=[c for c in [it.get("category")] if c],
                    extra={"server": server, "version": it.get("version")},
                )
            got = int((data or {}).get("messages", [{}])[0].get("count", len(coll)))
            if got < 100:
                break
            cursor += 100
