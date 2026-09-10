"""Cross-source identity resolution.

canonical key priority: DOI -> versionless arXiv id -> fuzzy(title + first-author
surname + year) hash. The canonical key doubles as `papers.paper_id`.
"""
from __future__ import annotations

import hashlib
import re

from .textutil import normalize_surname, strip_accents

_ARXIV_VER = re.compile(r"v\d+$")


def versionless_arxiv(arxiv_id: str) -> str:
    return _ARXIV_VER.sub("", (arxiv_id or "").strip().lower())


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(title or "").lower()).strip()


def canonical_key(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    title: str | None = None,
    authors: list[str] | None = None,
    year: int | None = None,
) -> str:
    if doi:
        return "doi:" + doi.strip().lower().removeprefix("https://doi.org/")
    if arxiv_id:
        return "arxiv:" + versionless_arxiv(arxiv_id)
    surname = normalize_surname(authors[0]) if authors else ""
    h = hashlib.sha1(f"{_norm_title(title)}|{surname}|{year or ''}".encode()).hexdigest()[:16]
    return "hash:" + h


def first_author_group(authors: list[str] | None) -> str | None:
    if not authors:
        return None
    return normalize_surname(authors[0]) or None
