"""Stopwords for token-set / bigram work (plan section 4.3 STOPWORDS + a much
wider domain stoplist so generic academic-writing filler and ambient ML
vocabulary don't drown out real candidate terms — this matters most for
worker.pipeline.discovery, which scans the WHOLE corpus each week rather than
a human-curated set of liked papers, so the noise floor is much higher).
"""
from __future__ import annotations

# from PAPER_RADAR_SPEC 4.3, plus enough additional function words that
# corpus-wide bigram scanning (discovery.py) doesn't surface filler like
# "rather than" / "does not" / "not only" as if they were candidate terms.
STOPWORDS: frozenset[str] = frozenset({
    # spec 4.3 baseline
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "are",
    "was", "were", "with", "without", "using", "use", "based", "new", "novel",
    "we", "propose", "present", "this", "paper", "show", "that", "by", "as",
    "at", "from", "into", "via", "can", "be", "it", "its", "their", "our",
    "these", "those", "which",
    # additional function words / connectors that flood corpus-wide scans
    "not", "only", "than", "rather", "does", "do", "did", "have", "has",
    "had", "will", "would", "could", "should", "may", "might", "must",
    "also", "both", "each", "such", "some", "many", "most", "more", "less",
    "very", "just", "even", "still", "yet", "so", "then", "thus", "hence",
    "however", "while", "when", "where", "how", "why", "what", "who",
    "across", "between", "among", "within", "over", "under", "about",
    "other", "another", "same", "different", "various", "several", "multiple",
    "existing", "prior", "current", "given", "here", "there", "them", "they",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "all", "any", "no", "first", "second", "third",
})

# extra noise that must never be promoted to a learned vocab term / flagged as
# an "unprompted" corpus-wide burst — generic academic-writing scaffolding and
# ambient ML vocabulary so common it carries no discovery signal on its own.
DOMAIN_STOP_BIGRAMS: frozenset[str] = frozenset({
    "we propose", "we present", "we show", "we introduce", "we study",
    "we demonstrate", "we find", "we observe", "in this", "this paper",
    "this work", "our work", "experimental results", "results show",
    "results demonstrate", "experiments demonstrate", "experiments show",
    "state of", "of the", "art performance", "extensive experiments",
    "our method", "our approach", "our model", "our framework", "our results",
    "recent work", "prior work", "future work", "related work",
    "large language", "language model", "language models", "deep learning",
    "neural network", "neural networks", "machine learning",
    "foundation models", "foundation model", "training data", "test set",
    "test time", "real world", "wide range", "significant improvement",
    "significantly outperforms", "outperforms existing", "existing methods",
    "existing approaches", "compared to", "compared with", "in terms",
    "terms of", "large scale", "small scale", "case study", "case studies",
    "ablation study", "ablation studies", "human evaluation",
    "empirical study", "empirical results", "open source", "code available",
    "publicly available", "github com", "artificial intelligence",
    "llm agents", "llm agent", "reinforcement learning",
    "method achieves", "further introduce", "strong performance",
    "results highlight", "methods typically", "benchmarks demonstrate",
    "increasingly used", "widely used", "commonly used", "further improves",
    "benchmark dataset", "benchmark datasets", "downstream tasks",
    "downstream task",
})


def content_tokens(text: str) -> set[str]:
    """lowercase words len>=4, minus STOPWORDS (spec 4.3 token_set)."""
    import re
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-]{3,}", (text or "").lower())
            if w not in STOPWORDS}
