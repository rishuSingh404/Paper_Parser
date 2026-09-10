"""Europe PMC — biomedical literature incl. preprints. No auth."""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from .. import http
from .base import RawPaper

name = "europepmc"
_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def fetch(since: dt.datetime | None, params: dict, on_call=None) -> Iterator[RawPaper]:
    since_year = (since.year if since else dt.date.today().year - 1)
    size = int(params.get("max_per_query", 30))
    for term in params.get("queries", []):
        data = http.get_json(
            _URL,
            params={
                "query": f'({term}) AND (FIRST_PDATE:[{since_year}-01-01 TO 3000-12-31])',
                "format": "json", "pageSize": min(size, 100), "resultType": "core",
                "sort": "P_PDATE_D desc",
            },
            source="europepmc", endpoint="search", on_call=on_call, pace=0.8,
        )
        for r in (((data or {}).get("resultList") or {}).get("result")) or []:
            title = (r.get("title") or "").rstrip(".")
            if not title:
                continue
            d = None
            if r.get("firstPublicationDate"):
                try:
                    d = dt.date.fromisoformat(r["firstPublicationDate"])
                except ValueError:
                    d = None
            au = [a.get("fullName") for a in ((r.get("authorList") or {}).get("author") or [])
                  if a.get("fullName")]
            pmid = r.get("pmid") or r.get("id")
            yield RawPaper(
                source="europepmc",
                source_id=str(pmid or r.get("doi") or title[:80]),
                doi=r.get("doi"),
                title=title,
                abstract=r.get("abstractText"),
                authors=au,
                published_date=d,
                announce_date=d,
                url=(f"https://europepmc.org/article/{r.get('source','MED')}/{pmid}" if pmid else None),
                extra={"pub_type": r.get("pubTypeList")},
            )
