"""Common ingest types + the `papers`/`paper_sources` upsert.

A source normalizes its rows to `RawPaper`; `upsert_paper` resolves identity and
merges into the shared `papers` row (filling NULLs, unioning categories, adding a
`paper_sources` provenance row). Never creates a duplicate for a paper another
source already brought in.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Iterable, Protocol, runtime_checkable

import psycopg
from psycopg.types.json import Json

from .. import identity
from ..textutil import looks_like_survey, normalize_ws


@dataclasses.dataclass(slots=True)
class RawPaper:
    source: str
    source_id: str
    title: str
    abstract: str | None = None
    authors: list[str] = dataclasses.field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None            # versionless
    published_date: dt.date | None = None
    announce_date: dt.date | None = None   # real publication/announce date — week-bucketing key
    url: str | None = None
    categories: list[str] = dataclasses.field(default_factory=list)
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    def clean(self) -> "RawPaper":
        self.title = normalize_ws(self.title)
        self.abstract = normalize_ws(self.abstract) if self.abstract else None
        return self


@runtime_checkable
class Source(Protocol):
    name: str

    def fetch(self, since: dt.datetime | None, params: dict) -> Iterable[RawPaper]: ...


def upsert_paper(conn: psycopg.Connection, rp: RawPaper) -> str:
    """Insert-or-merge a paper; return its `paper_id`. Also records provenance."""
    rp.clean()
    year = (rp.published_date or rp.announce_date).year if (rp.published_date or rp.announce_date) else None
    key = identity.canonical_key(
        doi=rp.doi, arxiv_id=rp.arxiv_id, title=rp.title, authors=rp.authors, year=year
    )
    fag = identity.first_author_group(rp.authors)
    is_survey = looks_like_survey(rp.title, rp.abstract)
    abstract_missing = rp.abstract is None or rp.abstract == ""

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO papers (paper_id, canonical_key, arxiv_id, doi, title, abstract,
                                abstract_missing, authors, first_author_group,
                                published_date, announce_date, link, categories, is_survey)
            VALUES (%(pid)s, %(key)s, %(arxiv)s, %(doi)s, %(title)s, %(abstract)s,
                    %(amiss)s, %(authors)s, %(fag)s, %(pub)s, %(ann)s, %(link)s,
                    %(cats)s, %(survey)s)
            ON CONFLICT (paper_id) DO UPDATE SET
                abstract        = COALESCE(papers.abstract, EXCLUDED.abstract),
                abstract_missing = (COALESCE(papers.abstract, EXCLUDED.abstract) IS NULL),
                doi             = COALESCE(papers.doi, EXCLUDED.doi),
                arxiv_id        = COALESCE(papers.arxiv_id, EXCLUDED.arxiv_id),
                announce_date   = LEAST(papers.announce_date, EXCLUDED.announce_date),
                published_date  = LEAST(papers.published_date, EXCLUDED.published_date),
                first_author_group = COALESCE(papers.first_author_group, EXCLUDED.first_author_group),
                categories      = (
                    SELECT array_agg(DISTINCT c)
                    FROM unnest(papers.categories || EXCLUDED.categories) AS c
                ),
                is_survey       = papers.is_survey OR EXCLUDED.is_survey
            """,
            {
                "pid": key, "key": key, "arxiv": identity.versionless_arxiv(rp.arxiv_id) if rp.arxiv_id else None,
                "doi": rp.doi, "title": rp.title, "abstract": rp.abstract, "amiss": abstract_missing,
                "authors": rp.authors, "fag": fag, "pub": rp.published_date, "ann": rp.announce_date,
                "link": rp.url, "cats": rp.categories, "survey": is_survey,
            },
        )
        cur.execute(
            """
            INSERT INTO paper_sources (paper_id, source, source_id, source_url, raw)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (paper_id, source) DO UPDATE SET
                source_id = EXCLUDED.source_id,
                source_url = EXCLUDED.source_url,
                raw = EXCLUDED.raw
            """,
            (key, rp.source, rp.source_id, rp.url, Json(rp.extra)),
        )
    return key
