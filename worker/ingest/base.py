"""Common ingest types + the `papers`/`paper_sources` upsert.

A source normalizes its rows to `RawPaper`; `upsert_paper`/`upsert_papers_batch`
resolve identity and merge into the shared `papers` row (filling NULLs, unioning
categories, adding a `paper_sources` provenance row). Never creates a duplicate
for a paper another source already brought in.

Use the batch form wherever you have more than a handful of papers at once (any
arXiv page, any generic-source fetch). Measured cost of the row-at-a-time form
against Neon from a cross-region client: ~330ms/round-trip x 2 statements/paper
= ~65s for a single 100-paper page. The batch form does the whole page in 2
round trips total, independent of page size.
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


_PAPERS_UPSERT = """
    INSERT INTO papers (paper_id, canonical_key, arxiv_id, doi, title, abstract,
                        abstract_missing, authors, first_author_group,
                        published_date, announce_date, link, categories, is_survey)
    VALUES {values}
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
"""

_SOURCES_UPSERT = """
    INSERT INTO paper_sources (paper_id, source, source_id, source_url, raw)
    VALUES {values}
    ON CONFLICT (paper_id, source) DO UPDATE SET
        source_id = EXCLUDED.source_id,
        source_url = EXCLUDED.source_url,
        raw = EXCLUDED.raw
"""


def _paper_row(rp: RawPaper) -> tuple:
    rp.clean()
    year = (rp.published_date or rp.announce_date).year if (rp.published_date or rp.announce_date) else None
    key = identity.canonical_key(
        doi=rp.doi, arxiv_id=rp.arxiv_id, title=rp.title, authors=rp.authors, year=year
    )
    return (
        key, key, identity.versionless_arxiv(rp.arxiv_id) if rp.arxiv_id else None,
        rp.doi, rp.title, rp.abstract, rp.abstract is None or rp.abstract == "",
        rp.authors, identity.first_author_group(rp.authors),
        rp.published_date, rp.announce_date, rp.url, rp.categories,
        looks_like_survey(rp.title, rp.abstract),
    ), key


def upsert_paper(conn: psycopg.Connection, rp: RawPaper) -> str:
    """Single-paper upsert — 2 round trips. Fine for one-off callers (e.g. the
    dashboard's on-demand term backfill); use upsert_papers_batch for anything
    fetching more than a few papers at once."""
    return upsert_papers_batch(conn, [rp])[0]


def upsert_papers_batch(conn: psycopg.Connection, raws: list[RawPaper]) -> list[str]:
    """Batch upsert — 2 round trips total, independent of batch size. Same
    identity/merge semantics as upsert_paper. Returns paper_ids in input order
    (deduped: if two RawPapers in the batch resolve to the same canonical key,
    only the last is written, matching the ON CONFLICT DO UPDATE semantics)."""
    if not raws:
        return []

    ordered_ids: list[str] = []
    by_key: dict[str, tuple] = {}
    sources: dict[tuple[str, str], tuple] = {}
    for rp in raws:
        row, key = _paper_row(rp)
        ordered_ids.append(key)
        by_key[key] = row
        sources[(key, rp.source)] = (key, rp.source, rp.source_id, rp.url, Json(rp.extra))

    paper_rows = list(by_key.values())
    source_rows = list(sources.values())

    with conn.cursor() as cur:
        placeholders = ", ".join(["(" + ", ".join(["%s"] * 14) + ")"] * len(paper_rows))
        params = [v for row in paper_rows for v in row]
        cur.execute(_PAPERS_UPSERT.format(values=placeholders), params)

        placeholders = ", ".join(["(" + ", ".join(["%s"] * 5) + ")"] * len(source_rows))
        params = [v for row in source_rows for v in row]
        cur.execute(_SOURCES_UPSERT.format(values=placeholders), params)

    return ordered_ids
