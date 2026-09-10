"""arXiv API client.

Rules (not suggestions): one connection, >= ARXIV_MIN_INTERVAL_SECONDS between
requests, exponential backoff from ARXIV_BACKOFF_START_SECONDS on HTTP 429,
identifying User-Agent. Callers must already hold worker.arxiv_lock.

Week-bucketing note: this API exposes `published` (submission time) and `updated`
(last revision), not the true OAI-PMH announce datestamp. We use `published` as
the announce_date proxy on purpose — using `updated` would push v2 of an old
paper into a recent week and fake a momentum spike. A true OAI datestamp is a
later refinement.
"""
from __future__ import annotations

import datetime as dt
import re
import time
import urllib.parse
from typing import Callable, Iterator

import feedparser
import httpx

from .. import settings
from .base import RawPaper

ENDPOINT = "https://export.arxiv.org/api/query"
_ARXIV_VER = re.compile(r"v\d+$")
_last_request = 0.0

OnCall = Callable[..., None]


def _pace() -> None:
    global _last_request
    wait = settings.ARXIV_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _entry_to_raw(e) -> RawPaper:
    tail = (e.get("id", "") or "").rsplit("/abs/", 1)[-1]
    versionless = _ARXIV_VER.sub("", tail)
    published = _parse_date(e.get("published"))
    cats = [t.get("term") for t in e.get("tags", []) if t.get("term")]
    authors = [a.get("name") for a in e.get("authors", []) if a.get("name")]
    return RawPaper(
        source="arxiv",
        source_id=versionless,
        arxiv_id=versionless,
        title=e.get("title", "") or "",
        abstract=e.get("summary", "") or "",
        authors=authors,
        published_date=published,
        announce_date=published,  # proxy, see module docstring
        url=f"https://arxiv.org/abs/{versionless}",
        categories=cats,
        extra={"primary_category": (e.get("arxiv_primary_category") or {}).get("term")},
    )


def _get(params: dict, on_call: OnCall | None) -> feedparser.FeedParserDict:
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    delay = settings.ARXIV_BACKOFF_START_SECONDS
    last_exc: Exception | None = None
    for _ in range(settings.ARXIV_MAX_RETRIES + 1):
        _pace()
        t0 = time.monotonic()
        try:
            resp = httpx.get(url, headers={"User-Agent": settings.USER_AGENT}, timeout=60.0)
            latency = int((time.monotonic() - t0) * 1000)
            if on_call:
                on_call(source="arxiv", endpoint="query", status=resp.status_code, latency_ms=latency)
            if resp.status_code == 429:
                time.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            return feedparser.parse(resp.content)
        except httpx.HTTPError as exc:
            last_exc = exc
            if on_call:
                on_call(source="arxiv", endpoint="query", status=None,
                        latency_ms=int((time.monotonic() - t0) * 1000), quota_note=repr(exc))
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"arXiv request failed after retries: {last_exc}")


def search(query: str, *, start: int = 0, max_results: int = 100,
           on_call: OnCall | None = None) -> tuple[int, list[RawPaper]]:
    feed = _get(
        {
            "search_query": query,
            "start": start,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        on_call,
    )
    total = int(feed.feed.get("opensearch_totalresults", 0) or 0)
    return total, [_entry_to_raw(e) for e in feed.entries]


def paginate(query: str, *, since: dt.date | None = None, page_size: int = 100,
             max_requests: int = 8, start: int = 0,
             on_call: OnCall | None = None) -> Iterator[RawPaper]:
    """Yield RawPaper newest-first, stopping at `since` (by announce date), at
    `max_requests`, or at the end of results.

    On StopIteration the generator's `.value` is the resume cursor: an int
    `start` if it stopped on the request cap (backfill is PARTIAL), else None
    (COMPLETE).
    """
    made = 0
    while made < max_requests:
        total, batch = search(query, start=start, max_results=page_size, on_call=on_call)
        made += 1
        if not batch:
            return None
        for rp in batch:
            if since and rp.announce_date and rp.announce_date < since:
                return None
            yield rp
        start += page_size
        if start >= total:
            return None
    return start  # hit the cap -> partial, resume here
