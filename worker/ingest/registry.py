"""Source registry. arXiv is driven directly by run.py / backfill.py (it needs
the advisory lock + its own pagination); every other source goes through the
generic `fetch(since, params, on_call)` loop here.
"""
from __future__ import annotations

from . import biorxiv, core, crossref, dblp, europepmc, hf_daily, openalex, openreview, semantic_scholar

GENERIC_SOURCES = {
    m.name: m
    for m in (hf_daily, biorxiv, crossref, openalex, semantic_scholar, core, dblp, europepmc, openreview)
}


def enabled_generic(cfg: dict) -> list:
    srcs = cfg.get("sources") or {}
    out = []
    for name, mod in GENERIC_SOURCES.items():
        entry = srcs.get(name)
        if entry and entry.get("enabled"):
            out.append(mod)
    return out
