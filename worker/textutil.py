"""Text matching / normalization primitives.

Term matching is WORD-BOUNDARY, not substring. Substring matching makes a bare
`ground` keyword fire inside "foreground segmentation" and "background
subtraction"; `\\bground\\b` matches neither, but still matches "grounded in the
input signal". (Plan step 4 + its verification item.)
"""
from __future__ import annotations

import re
import unicodedata

_SURVEY_RE = re.compile(r"\b(survey|review|overview|comprehensive)\b", re.IGNORECASE)

# regex metacharacters that must be neutralised before splicing a term into a
# Python OR a POSIX (Postgres) pattern.
_META = r".\\+*?()[]{}|^$"


def strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def strip_tags(s: str | None) -> str | None:
    """Drop JATS / HTML tags (Crossref & Europe PMC abstracts carry them)."""
    if not s:
        return None
    out = re.sub(r"<[^>]+>", " ", s)
    out = re.sub(r"\s+", " ", out).strip()
    return out or None


def abstract_from_inverted_index(inv: dict | None) -> str | None:
    """Reconstruct an OpenAlex abstract_inverted_index into plain text."""
    if not inv:
        return None
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos)) or None


def normalize_ws(s: str) -> str:
    return " ".join((s or "").split())


def looks_like_survey(title: str, abstract: str | None) -> bool:
    if _SURVEY_RE.search(title or ""):
        return True
    abs = abstract or ""
    if _SURVEY_RE.search(abs[:200]):
        return True
    return False


def normalize_surname(author: str) -> str:
    """'Jane Q. Doe' -> 'doe'; 'Doe, Jane' -> 'doe'."""
    a = strip_accents(author).strip()
    if not a:
        return ""
    surname = a.split(",")[0] if "," in a else a.split()[-1]
    return re.sub(r"[^a-z]", "", surname.lower())


def escape_term_python(term: str) -> str:
    return "".join("\\" + c if c in _META else c for c in term.lower())


def escape_term_posix(term: str) -> str:
    """Escape for a Postgres POSIX regex (used with ~* and \\y boundaries)."""
    return "".join("\\" + c if c in _META else c for c in term.lower())


def word_boundary_match(term: str, text: str) -> bool:
    pat = r"\b" + escape_term_python(term) + r"\b"
    return re.search(pat, (text or "").lower()) is not None


def matched_terms(terms: list[str], text: str) -> list[str]:
    low = (text or "").lower()
    return [t for t in terms if re.search(r"\b" + escape_term_python(t) + r"\b", low)]
