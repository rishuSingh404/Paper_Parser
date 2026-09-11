"""Broad-track scoring (plan steps 7-9).

Deterministic. Component signals stay decomposable (each written to `signals`);
the composite rank is ORDERING ONLY, never a gate. Inclusion is threshold-based
(clear ANY recall-gate threshold), not top-N — a quiet week yields a short list.
No token-ratio structural_overlap, no citation bonus: embeddings replace the
former; citation status is a separate lagging tag.

Title-only fallback: a paper with no resolvable abstract is scored on its title
alone with `abstract_missing=True` surfaced on the card — never dropped.
"""
from __future__ import annotations

import datetime as dt

import psycopg
from psycopg.types.json import Json

from .. import db, vectors
from ..textutil import matched_terms, word_boundary_match
from . import citation_tier
from .momentum import momentum_for

_CANDIDATE_UNIVERSE_CAP = 2500
SIGNAL_NAMES = ("embedding_sim", "lexical", "concept_overlap", "cocitation_velocity", "hf_upvotes")


def _minmax(values: list[float]) -> callable:
    lo, hi = (min(values), max(values)) if values else (0.0, 0.0)
    span = hi - lo
    return (lambda x: (x - lo) / span) if span > 1e-9 else (lambda x: 0.0)


def score_broad(conn: psycopg.Connection, cfg: dict, current_week: str,
                tracked_terms: list[str]) -> dict:
    from ..enrich.embeddings import active_model_version
    mv = active_model_version(cfg)
    weights = cfg.get("rank_weights") or {}
    gate = cfg.get("recall_gate_thresholds") or {}
    hard_cap = int(gate.get("hard_cap", 60))

    vocab_w = {str(k).lower(): float(v) for k, v in (cfg.get("seed_vocab") or {}).items()}
    vocab_w.update({r["term"]: r["weight"] for r in db.q(conn, "SELECT term, weight FROM vocab")})
    mom = momentum_for(conn, tracked_terms, current_week)

    cands = db.q(
        conn,
        """
        SELECT paper_id, title, abstract, abstract_missing, authors, announce_date,
               link, concepts
        FROM papers
        WHERE NOT muted
          AND (first_seen_at > now() - interval '10 days'
               OR announce_date > CURRENT_DATE - 21)
        ORDER BY first_seen_at DESC
        LIMIT %s
        """,
        (_CANDIDATE_UNIVERSE_CAP,),
    )
    if not cands:
        return {"cards": [], "surfaced": 0, "universe": 0, "truncated_at": None}

    ids = [c["paper_id"] for c in cands]
    src_map: dict[str, list[str]] = {}
    for r in db.q(conn, "SELECT paper_id, array_agg(source) AS s FROM paper_sources "
                        "WHERE paper_id = ANY(%s) GROUP BY paper_id", (ids,)):
        src_map[r["paper_id"]] = sorted(r["s"])

    emb_map: dict[str, list[float]] = {}
    for r in db.q(conn, "SELECT paper_id, embedding FROM embeddings "
                        "WHERE model_version = %s AND paper_id = ANY(%s)", (mv, ids)):
        v = vectors.parse_pg(r["embedding"])
        if v:
            emb_map[r["paper_id"]] = v
    anchors = []
    for r in db.q(conn, "SELECT anchor_kind, text, embedding FROM anchor_embeddings "
                        "WHERE model_version = %s", (mv,)):
        v = vectors.parse_pg(r["embedding"])
        if v:
            anchors.append((r["anchor_kind"], r["text"], v))

    hf_map: dict[str, int] = {
        r["paper_id"]: int(r["upvotes"])
        for r in db.q(conn, "SELECT DISTINCT ON (paper_id) paper_id, upvotes FROM hf_signals "
                            "WHERE paper_id = ANY(%s) ORDER BY paper_id, snapshot_date DESC", (ids,))
    }
    from ..enrich.citations import cocitation_velocity
    coc_map = cocitation_velocity(conn, cfg["cocitation_lookback_weeks"])
    tag_map = citation_tier.tiers(conn, cfg)

    scored = []
    for c in cands:
        text = f"{c['title']} {c['abstract'] or ''}"
        matched = matched_terms(tracked_terms, text)
        lexical = sum(vocab_w.get(t, 0.0) * max(0.0, mom.get(t, {}).get("momentum", 0.0))
                      for t in matched)

        best_sim, best_anchor = 0.0, None
        pv = emb_map.get(c["paper_id"])
        if pv and anchors:
            for kind, atext, av in anchors:
                s = vectors.cosine(pv, av)
                if s > best_sim:
                    best_sim, best_anchor = s, (kind, atext)

        concepts_txt = " ".join(c["concepts"] or [])
        concept_overlap = sum(1 for t in tracked_terms if word_boundary_match(t, concepts_txt))

        coc = int(coc_map.get(c["paper_id"], 0))
        hf = int(hf_map.get(c["paper_id"], 0))

        sig = {
            "embedding_sim": round(best_sim, 4) if pv and anchors else None,
            "lexical": round(lexical, 4),
            "concept_overlap": concept_overlap,
            "cocitation_velocity": coc,
            "hf_upvotes": hf,
        }
        passes = (
            (sig["embedding_sim"] is not None and sig["embedding_sim"] >= gate.get("embedding_sim", 1e9))
            or lexical >= gate.get("lexical", 1e9)
            or coc >= gate.get("cocitation_velocity", 1e9)
            or hf >= gate.get("hf_upvotes", 1e9)
            or concept_overlap >= gate.get("concept_overlap", 1e9)
        )
        if not passes:
            continue
        scored.append({"row": c, "matched": matched, "sig": sig,
                       "best_anchor": best_anchor, "coc": coc, "hf": hf})

    if not scored:
        return {"cards": [], "surfaced": 0, "universe": len(cands), "truncated_at": None}

    norms = {
        name: _minmax([float(s["sig"][name] or 0.0) for s in scored])
        for name in SIGNAL_NAMES
    }
    for s in scored:
        s["composite"] = round(sum(
            (weights.get(name, 0.0)) * norms[name](float(s["sig"][name] or 0.0))
            for name in SIGNAL_NAMES
        ), 5)
    scored.sort(key=lambda s: -s["composite"])

    truncated_at = None
    if len(scored) > hard_cap:
        truncated_at = hard_cap
        scored = scored[:hard_cap]

    run_date = dt.date.today()
    cards = []
    for rank, s in enumerate(scored, 1):
        c = s["row"]
        tag = tag_map.get(c["paper_id"], {"tier": "ndata", "delta": None})
        why = _why(s, tag)
        for name, val in s["sig"].items():
            if val is None:
                continue
            db.execute(
                conn,
                "INSERT INTO signals (paper_id, run_date, name, value, detail) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (paper_id, run_date, name) DO UPDATE SET value = EXCLUDED.value, detail = EXCLUDED.detail",
                (c["paper_id"], run_date, name, float(val),
                 Json({"matched": s["matched"]} if name == "lexical" else
                      {"anchor": s["best_anchor"]} if name == "embedding_sim" else None)),
            )
        db.execute(
            conn,
            "INSERT INTO signals (paper_id, run_date, name, value, detail) "
            "VALUES (%s, %s, 'composite_rank', %s, %s) "
            "ON CONFLICT (paper_id, run_date, name) DO UPDATE SET value = EXCLUDED.value, detail = EXCLUDED.detail",
            (c["paper_id"], run_date, float(rank), Json({"composite": s["composite"], "why": why})),
        )
        cards.append({
            "paper_id": c["paper_id"],
            "title": c["title"],
            "authors": c["authors"][:8],
            "announce_date": c["announce_date"].isoformat() if c["announce_date"] else None,
            "link": c["link"],
            "sources": src_map.get(c["paper_id"], []),
            "rank": rank,
            "composite": s["composite"],
            "signals": s["sig"],
            "why": why,
            "abstract": c["abstract"],
            "abstract_missing": c["abstract_missing"],
            "citation_tag": {
                "tier": tag["tier"], "delta": tag["delta"],
                "badge": citation_tier.BADGE[tag["tier"]],
            },
        })
    return {"cards": cards, "surfaced": len(cards), "universe": len(cands),
            "truncated_at": truncated_at}


def _why(s: dict, tag: dict) -> str:
    bits = []
    if s["matched"]:
        bits.append("matches " + ", ".join(f"'{t}'" for t in s["matched"][:4]))
    if s["best_anchor"]:
        kind, txt = s["best_anchor"]
        short = (txt[:90] + "…") if txt and len(txt) > 90 else (txt or kind)
        bits.append(f"near {kind}: “{short}” (sim {s['sig']['embedding_sim']})")
    if s["coc"]:
        bits.append(f"cited by {s['coc']} recent paper(s)")
    if s["hf"]:
        bits.append(f"{s['hf']} HF upvotes")
    if tag["tier"] == "hot":
        bits.append(f"🔥 +{tag['delta']} citations / 30d")
    elif tag["tier"] == "warming":
        bits.append(f"📈 +{tag['delta']} citations / 30d")
    return "; ".join(bits) or "passed recall gate"
