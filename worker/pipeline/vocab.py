"""Vocabulary self-evolution: decay -> prune -> (counts added elsewhere) -> grow.

Order matters (spec 4.5): decay + prune run BEFORE this run's term_counts are
written, so today's own matches can't keep an at-risk term alive. run.py guards
the decay step with a `decay:<iso_week>` marker for idempotency.
"""
from __future__ import annotations

import psycopg

from .. import db
from ..isoweek import weeks_between
from ..stopwords import DOMAIN_STOP_BIGRAMS, STOPWORDS
from ..textutil import normalize_ws

MAX_LEARNED_VOCAB = 200
MAX_ADDS_PER_RUN = 6


def decay(conn: psycopg.Connection, decay_rate: float) -> None:
    db.execute(conn, "UPDATE vocab SET weight = weight * %s", (decay_rate,))


def prune(conn: psycopg.Connection, cfg: dict, current_week: str) -> list[str]:
    """Delete learned terms that are low-weight AND silent for >= N calendar
    weeks. Seed terms are exempt permanently (they still decay)."""
    thr = cfg["vocab_prune_weight_threshold"]
    min_silent = cfg["vocab_prune_min_silent_weeks"]
    rows = db.q(
        conn,
        "SELECT term, weight, last_seen_iso_week FROM vocab "
        "WHERE is_seed = FALSE AND weight < %s",
        (thr,),
    )
    doomed = []
    for r in rows:
        lsw = r["last_seen_iso_week"]
        silent = min_silent if lsw is None else weeks_between(lsw, current_week)
        if silent >= min_silent:
            doomed.append(r["term"])
    if doomed:
        db.execute(conn, "DELETE FROM vocab WHERE term = ANY(%s)", (doomed,))
    return doomed


def _bigrams(text: str) -> list[str]:
    words = [w for w in normalize_ws(text).lower().split() if w.isalpha() or "-" in w]
    grams = [f"{a} {b}" for a, b in zip(words, words[1:])]
    return [
        g for g in grams
        if g not in DOMAIN_STOP_BIGRAMS
        and not any(part in STOPWORDS for part in g.split())
        and len(g) >= 8
    ]


def grow_from_feedback(conn: psycopg.Connection) -> dict:
    """For each unprocessed 'liked' paper, mine bigrams; a bigram enters vocab at
    weight 1.0 if it appears in >=2 liked papers this batch, else 0.4. Cap adds."""
    likes = db.q(
        conn,
        "SELECT f.paper_id, p.title, p.abstract FROM feedback f JOIN papers p "
        "ON p.paper_id = f.paper_id "
        "WHERE f.verdict = 'liked' AND f.processed_for_vocab = FALSE",
    )
    if not likes:
        return {"added": 0, "processed": 0}

    existing = {r["term"] for r in db.q(conn, "SELECT term FROM vocab")}
    learned_count = db.q1(conn, "SELECT count(*) AS n FROM vocab WHERE is_seed = FALSE")["n"]

    doc_freq: dict[str, int] = {}
    for lk in likes:
        for g in set(_bigrams(f"{lk['title']} {lk['abstract'] or ''}")):
            doc_freq[g] = doc_freq.get(g, 0) + 1

    candidates = sorted(
        ((g, n) for g, n in doc_freq.items() if g not in existing),
        key=lambda kv: (-kv[1], kv[0]),
    )
    added = 0
    for g, n in candidates:
        if added >= MAX_ADDS_PER_RUN or learned_count + added >= MAX_LEARNED_VOCAB:
            break
        db.execute(
            conn,
            "INSERT INTO vocab (term, weight, is_seed) VALUES (%s, %s, FALSE) "
            "ON CONFLICT (term) DO NOTHING",
            (g, 1.0 if n >= 2 else 0.4),
        )
        added += 1

    db.execute(
        conn,
        "UPDATE feedback SET processed_for_vocab = TRUE, updated_at = now() "
        "WHERE verdict = 'liked' AND processed_for_vocab = FALSE",
    )
    return {"added": added, "processed": len(likes)}
