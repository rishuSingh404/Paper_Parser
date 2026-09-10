"""Stopwords for token-set / bigram work (plan section 4.3 STOPWORDS + a
domain stoplist so "we propose" / "experimental results" don't become vocab)."""
from __future__ import annotations

# from PAPER_RADAR_SPEC 4.3
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "are",
    "was", "were", "with", "without", "using", "use", "based", "new", "novel",
    "we", "propose", "present", "this", "paper", "show", "that", "by", "as",
    "at", "from", "into", "via", "can", "be", "it", "its", "their", "our",
    "these", "those", "which",
})

# extra noise that must never be promoted to a learned vocab term
DOMAIN_STOP_BIGRAMS: frozenset[str] = frozenset({
    "we propose", "we present", "we show", "we introduce", "we study",
    "in this", "this paper", "experimental results", "results show",
    "results demonstrate", "state of", "of the", "art performance",
    "extensive experiments", "our method", "our approach", "our model",
    "recent work", "prior work", "future work", "large language",
    "language model", "language models", "deep learning", "neural network",
    "neural networks", "machine learning",
})


def content_tokens(text: str) -> set[str]:
    """lowercase words len>=4, minus STOPWORDS (spec 4.3 token_set)."""
    import re
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-]{3,}", (text or "").lower())
            if w not in STOPWORDS}
