"""Text matching / normalization primitives.

Term matching is WORD-BOUNDARY, not substring. Substring matching makes a bare
`ground` keyword fire inside "foreground segmentation" and "background
subtraction"; `\\bground\\b` matches neither, but still matches "grounded in the
input signal". (Plan step 4 + its verification item.)
"""
from __future__ import annotations

import re
import unicodedata

from .stopwords import ACRONYM_STOPWORDS, DOMAIN_STOP_BIGRAMS, STOPWORDS

_ACRONYM_RE = re.compile(r"\b(?:[A-Z]{2,6}|[A-Z][a-z]+[A-Z][a-zA-Z0-9]*)\b")

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


def extract_bigrams(text: str) -> list[str]:
    """Lowercase adjacent-word bigrams, minus stopwords/domain-stop-bigrams and
    anything too short. Shared by vocab growth (worker.pipeline.vocab),
    config suggestions, and corpus-wide burst discovery."""
    words = [w for w in normalize_ws(text).lower().split() if w.isalpha() or "-" in w]
    grams = [f"{a} {b}" for a, b in zip(words, words[1:])]
    return [
        g for g in grams
        if g not in DOMAIN_STOP_BIGRAMS
        and not any(part in STOPWORDS for part in g.split())
        and len(g) >= 8
    ]


def extract_acronym_candidates(text: str) -> list[str]:
    """Tokens shaped like a just-coined method/technique name — ALL-CAPS
    acronyms ("JEPA", "DPO", "RLHF") or CamelCase coinages ("LoRA",
    "MedJEPA") — pulled from the ORIGINAL-case text, before any lowercasing.

    Why this exists, separate from extract_bigrams(): a single-word acronym
    never forms a stable bigram. "JEPA" fragments across "jepa architecture",
    "novel jepa", "using jepa", "the jepa" — different two-word keys every
    time, none of which individually accumulates enough count or distinct
    groups to cross the discovery thresholds, no matter how many papers
    actually use the term. Corpus-wide discovery (worker.pipeline.discovery)
    was bigram-only and would have missed Rishu's own JEPA example — the
    exact case it was built to catch — because JEPA IS this shape. This
    catches it directly instead of hoping a multi-word phrase forms around it.

    Returns lowercased tokens (for consistent aggregation with bigrams), each
    checked against ACRONYM_STOPWORDS in its ORIGINAL case-insensitive form —
    a small, reviewed, extensible denylist of acronyms too generic/ubiquitous
    across ML and medical-imaging papers to mean anything ("LLM", "MRI", "AI",
    ...), not an attempt at exhaustive precision. Noise here is expected and
    acceptable, same as extract_bigrams() — a human reads this list."""
    out = []
    for m in _ACRONYM_RE.finditer(text or ""):
        low = m.group(0).lower()
        if low in ACRONYM_STOPWORDS or len(low) < 3:
            continue
        out.append(low)
    return out
