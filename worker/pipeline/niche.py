"""Niche track (plan step 11). No trend scoring — this track needs completeness,
not momentum: recency + a soft embedding-sim ordering + dedup + a
`new_since_last_digest` flag.

Niche membership is derived deterministically from the quoted phrases in
`config.niche_queries` (word-boundary match), so it doesn't depend on which
arXiv query happened to surface a paper.
"""
from __future__ import annotations

import datetime as dt
import re

import psycopg

from .. import db, vectors
from ..textutil import matched_terms

_PHRASE = re.compile(r'"([^"]+)"')


def niche_phrases(cfg: dict) -> list[str]:
    out: list[str] = []
    for q in cfg.get("niche_queries") or []:
        out.extend(_PHRASE.findall(q))
    return sorted(set(p.strip() for p in out if len(p.strip()) >= 4))


def niche_feed(conn: psycopg.Connection, cfg: dict, *, days: int = 14, limit: int = 40) -> list[dict]:
    phrases = niche_phrases(cfg)
    if not phrases:
        return []
    mv = cfg["embedding_model_version"]

    rows = db.q(
        conn,
        """
        SELECT paper_id, title, abstract, abstract_missing, authors, announce_date, link
        FROM papers
        WHERE NOT muted
          AND (first_seen_at > now() - make_interval(days => %s)
               OR announce_date > CURRENT_DATE - %s)
        ORDER BY COALESCE(announce_date, first_seen_at::date) DESC
        LIMIT 1500
        """,
        (days, days),
    )
    hits = [r for r in rows if matched_terms(phrases, f"{r['title']} {r['abstract'] or ''}")]
    if not hits:
        return []

    # soft ordering: cosine to open-problem anchors when embeddings exist
    anchors = []
    for a in db.q(conn, "SELECT embedding FROM anchor_embeddings "
                        "WHERE model_version = %s AND anchor_kind = 'open_problem'", (mv,)):
        v = vectors.parse_pg(a["embedding"])
        if v:
            anchors.append(v)
    emb = {}
    if anchors:
        for r in db.q(conn, "SELECT paper_id, embedding FROM embeddings "
                            "WHERE model_version = %s AND paper_id = ANY(%s)",
                      (mv, [h["paper_id"] for h in hits])):
            v = vectors.parse_pg(r["embedding"])
            if v:
                emb[r["paper_id"]] = max(vectors.cosine(v, av) for av in anchors)

    prev = db.q1(conn, "SELECT niche_papers FROM digests ORDER BY run_date DESC LIMIT 1")
    prev_ids = {p.get("paper_id") for p in (prev["niche_papers"] if prev else []) or []}

    def sort_key(r):
        d = r["announce_date"] or dt.date.min
        return (d, emb.get(r["paper_id"], 0.0))

    hits.sort(key=sort_key, reverse=True)
    return [
        {
            "paper_id": r["paper_id"],
            "title": r["title"],
            "authors": r["authors"][:8],
            "announce_date": r["announce_date"].isoformat() if r["announce_date"] else None,
            "link": r["link"],
            "abstract": r["abstract"],
            "abstract_missing": r["abstract_missing"],
            "matched": matched_terms(phrases, f"{r['title']} {r['abstract'] or ''}"),
            "sim": round(emb.get(r["paper_id"], 0.0), 4),
            "new_since_last_digest": r["paper_id"] not in prev_ids,
        }
        for r in hits[:limit]
    ]
