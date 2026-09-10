"""Hugging Face Daily Papers — "what ML is discussing today" (upvotes).

Unofficial endpoint, no auth. Skewed to LLM/generative hype and big labs, so it
feeds the broad track / serendipity only, never the niche signal. Records a
per-day upvote snapshot via RawPaper.extra so the pipeline can write hf_signals.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http
from .base import RawPaper

name = "hf_daily"
_URL = "https://huggingface.co/api/daily_papers"


def _daterange(since: dt.date, until: dt.date) -> Iterator[dt.date]:
    d = since
    while d <= until:
        yield d
        d += dt.timedelta(days=1)


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    until = dt.date.today()
    start = (since.date() if since else until - dt.timedelta(days=2))
    # cap how far back a single run reaches (bootstrap passes a wider `since`)
    max_days = int(params.get("hf_max_days", 120))
    if (until - start).days > max_days:
        start = until - dt.timedelta(days=max_days)

    for day in _daterange(start, until):
        page = 0
        while True:
            data = http.get_json(
                _URL,
                params={"date": day.isoformat(), "page": page, "limit": 100},
                source="hf_daily", endpoint="daily_papers", on_call=on_call, pace=0.5,
            )
            if not data:
                break
            for item in data:
                paper = item.get("paper") or item
                aid = (paper.get("id") or "").strip()
                if not aid:
                    continue
                authors = [a.get("name") for a in (paper.get("authors") or []) if a.get("name")]
                pub = paper.get("publishedAt") or item.get("publishedAt")
                pubd = None
                if pub:
                    try:
                        pubd = dt.datetime.fromisoformat(pub.replace("Z", "+00:00")).date()
                    except ValueError:
                        pubd = None
                yield RawPaper(
                    source="hf_daily",
                    source_id=aid,
                    arxiv_id=aid,
                    title=paper.get("title") or "",
                    abstract=paper.get("summary") or paper.get("abstract"),
                    authors=authors,
                    published_date=pubd,
                    announce_date=pubd,
                    url=f"https://huggingface.co/papers/{aid}",
                    extra={
                        "hf_upvotes": int(paper.get("upvotes") or 0),
                        "hf_comments": int(paper.get("numComments") or item.get("numComments") or 0),
                        "hf_date": day.isoformat(),
                        "repo": paper.get("githubRepo") or None,
                    },
                )
            if len(data) < 100:
                break
            page += 1
