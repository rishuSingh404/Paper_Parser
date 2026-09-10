"""OpenReview (api2) — ~3-month lead on conference trends.

Disabled by default (`config.sources.openreview.enabled = false`): seasonal
(Sept-Feb), fuzzy title matching, and access may tighten after the 2026 ICLR
scraping incident. We ingest ONLY public review text + scores + decisions, never
author/reviewer identities.

`params["openreview_venues"]` is a list of venue ids, e.g.
["ICLR.cc/2026/Conference"]. Kept dependency-free (plain httpx via worker.http)
rather than pulling in openreview-py for v1.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http
from .base import RawPaper

name = "openreview"
_NOTES = "https://api2.openreview.net/notes"


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    venues = params.get("openreview_venues") or []
    per_venue = int(params.get("openreview_limit", 200))
    for venue in venues:
        offset = 0
        while offset < per_venue:
            data = http.get_json(
                _NOTES,
                params={"content.venueid": venue, "limit": 100, "offset": offset,
                        "details": "replyCount"},
                source="openreview", endpoint="notes", on_call=on_call, pace=1.0,
            )
            notes = (data or {}).get("notes") or []
            if not notes:
                break
            for n in notes:
                c = n.get("content") or {}

                def _v(field):
                    x = c.get(field)
                    return x.get("value") if isinstance(x, dict) else x

                title = _v("title") or ""
                if not title:
                    continue
                cdate = n.get("cdate")
                d = (dt.datetime.utcfromtimestamp(cdate / 1000).date() if cdate else None)
                yield RawPaper(
                    source="openreview",
                    source_id=n.get("id") or title[:80],
                    title=title,
                    abstract=_v("abstract"),
                    authors=[],  # deliberately not ingested
                    published_date=d,
                    announce_date=d,
                    url=f"https://openreview.net/forum?id={n.get('forum') or n.get('id')}",
                    categories=[venue],
                    extra={
                        "venue": venue,
                        "keywords": _v("keywords"),
                        "reply_count": (n.get("details") or {}).get("replyCount"),
                    },
                )
            offset += 100
