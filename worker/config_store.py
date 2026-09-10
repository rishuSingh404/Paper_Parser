"""Read the single `config` row fresh (the pipeline never caches it)."""
from __future__ import annotations

from typing import Any

import psycopg

from . import db

_COLUMNS = [
    "niche_queries", "broad_categories", "seed_vocab", "open_problems", "seed_papers",
    "sources", "vocab_decay_rate", "vocab_prune_weight_threshold",
    "vocab_prune_min_silent_weeks", "citation_hot_threshold", "citation_warming_threshold",
    "cocitation_lookback_weeks", "burst_min_groups", "recall_gate_thresholds",
    "rank_weights", "embedding_model_version", "keyword_backfill_months",
    "digest_mode", "timezone", "version",
]


def load(conn: psycopg.Connection) -> dict[str, Any]:
    row = db.q1(conn, f"SELECT {', '.join(_COLUMNS)} FROM config WHERE id = 1")
    if row is None:
        raise RuntimeError("config row missing — run db/seed_config.sql")
    return dict(row)


def source_enabled(cfg: dict, name: str) -> bool:
    entry = (cfg.get("sources") or {}).get(name)
    return bool(entry and entry.get("enabled"))


def all_terms(cfg: dict) -> list[str]:
    """seed vocab terms; the live tracked set also includes learned terms in
    the `vocab` table (see pipeline)."""
    return list((cfg.get("seed_vocab") or {}).keys())
