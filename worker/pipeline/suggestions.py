"""Deterministic config suggestions (plan step 15). NO auto-apply.

Bigrams frequent across liked papers that are absent from BOTH `vocab` and the
`niche_queries` phrases -> written to `config_suggestions` (status='pending') for
the dashboard to accept/reject individually.
"""
from __future__ import annotations

import psycopg
from psycopg.types.json import Json

from .. import db
from ..textutil import extract_bigrams as _bigrams
from .niche import niche_phrases


def refresh(conn: psycopg.Connection, cfg: dict, *, min_docs: int = 2) -> dict:
    likes = db.q(
        conn,
        "SELECT p.title, p.abstract FROM feedback f JOIN papers p ON p.paper_id = f.paper_id "
        "WHERE f.verdict = 'liked'",
    )
    if len(likes) < min_docs:
        return {"suggested": 0}

    have = {r["term"] for r in db.q(conn, "SELECT term FROM vocab")}
    have.update(p.lower() for p in niche_phrases(cfg))
    pending = {
        r["payload"].get("term")
        for r in db.q(conn, "SELECT payload FROM config_suggestions "
                            "WHERE kind = 'vocab_term' AND status = 'pending'")
    }

    freq: dict[str, int] = {}
    for lk in likes:
        for g in set(_bigrams(f"{lk['title']} {lk['abstract'] or ''}")):
            if g not in have and g not in pending:
                freq[g] = freq.get(g, 0) + 1

    picks = sorted((kv for kv in freq.items() if kv[1] >= min_docs),
                   key=lambda kv: (-kv[1], kv[0]))[:10]
    for term, n in picks:
        db.execute(
            conn,
            "INSERT INTO config_suggestions (kind, payload, rationale) VALUES ('vocab_term', %s, %s)",
            (Json({"term": term, "weight": 1.0}),
             f"appears in {n} liked papers; not in vocab or niche_queries"),
        )
    return {"suggested": len(picks)}
