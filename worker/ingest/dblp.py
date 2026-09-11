"""DBLP publication search.

** DBLP RETURNS NO ABSTRACTS. ** Every row here is created with abstract=None and
must be resolved by worker/enrich/abstracts.py (Crossref -> OpenAlex); if nothing
resolves, worker/pipeline/score.py scores it title-only with an `abstract_missing`
flag — it is never silently dropped.

Value: catch keyword-matching papers in real CS venues (title + venue + year +
DOI) that the arXiv sweep misses.

**Time budget, not just a retry budget.** Observed live (2026-09-11, production):
dblp.org was unreachable from Render's network — every call a 45s TLS handshake
timeout. http.get_json's default retry policy (4 retries, exponential backoff
from 3s, 45s timeout per attempt) is right for a source that occasionally 429s,
but applied to a source that is *entirely down* it means one query can burn
~4 minutes, and this module runs one query per tracked term (10-15+ per run) —
a single dead source could turn a several-minute daily pipeline into an
hour-long one. Fixed with two changes: a short per-call timeout/retry count
(worst case ~27s/query instead of ~270s), and a circuit breaker that gives up
on the rest of this run's queries after a few consecutive empty results (covers
both "DBLP is down" and "DBLP is up but genuinely has nothing for any of these
terms" — either way, more calls in the same run won't help; try again next run).
Degrades to zero DBLP papers for one run, never to a stalled pipeline.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http
from .base import RawPaper

name = "dblp"
_URL = "https://dblp.org/search/publ/api"
_TIMEOUT = 12.0
_RETRIES = 1
_MAX_CONSECUTIVE_EMPTY = 3


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    min_year = (since.year if since else dt.date.today().year - 1)
    page_size = int(params.get("max_per_query", 30))
    consecutive_empty = 0
    for term in params.get("queries", []):
        data = http.get_json(
            _URL,
            params={"q": term, "format": "json", "h": min(page_size, 100)},
            source="dblp", endpoint="search/publ", on_call=on_call, pace=1.0,
            timeout=_TIMEOUT, retries=_RETRIES,
        )
        hits = (((data or {}).get("result") or {}).get("hits") or {}).get("hit", []) or []
        if isinstance(hits, dict):  # DBLP returns a bare object for a single hit
            hits = [hits]
        if not hits:
            consecutive_empty += 1
            if consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                return  # DBLP down or genuinely empty this run — stop spending budget
            continue
        consecutive_empty = 0
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
