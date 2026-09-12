"""Broad-track scoring (plan steps 7-9) + the cross-domain / new-field view.

Deterministic. Component signals stay decomposable (each written to `signals`);
the composite rank is ORDERING ONLY, never a gate. Inclusion is threshold-based
(clear ANY recall-gate threshold), not top-N — a quiet week yields a short list.
No token-ratio structural_overlap, no citation bonus: embeddings replace the
former; citation status is a separate lagging tag.

**Two sections, by `in_domain` alone.** Rishu's own framing: a paper that's
about hallucination detection / mitigation / safety / security IS his domain —
he'd surface that himself, it's not the point of this system. What's worth
surfacing is a paper that is NOT his domain but is structurally close to one
of his open problems: that's a transfer candidate, possibly from a field he
doesn't read (the MedJEPA-Critic origin story — JEPA popping in unrelated
domains 5 months before it had recognition, then "why not apply it to
hallucination"). `score_broad` returns both `cards` (in_domain=True, ranked by
the composite) and `cross_domain` (in_domain=False, ranked by embedding
similarity), a clean partition of every candidate that cleared the recall
gate — never a second bar to clear on top of the gate. (An earlier version
additionally required embedding_sim above a floor to enter `cross_domain`,
which silently routed a not-in-domain paper that passed the gate on a
*lexical* or *co-citation* signal into the "in your field" list — exactly
backwards; fixed.) `in_domain` = matched a niche.domain_marker_phrases()
phrase — niche_phrases() minus a small, reviewed exclusion list of phrases
too generic to independently mean "this is Rishu's domain" (currently just
"vision-language" — common across all multimodal ML/robotics). Caught live: a
pure robotics VLA world-model paper matched bare "vision-language" (leaked
out of `abs:"hallucination detection" AND abs:"vision-language"` when
flattened) and was misclassified as in_domain. (Tried requiring a query's
phrases jointly as an AND-group instead: broke the opposite way, since a
genuine hallucination-detection paper that doesn't also say "vision-language"
— plenty don't — stopped counting as in_domain at all. See
niche.domain_marker_phrases() docstring.) Matching a seed_vocab MECHANISM
term (`necessity ablation`, `latent steering`, ...) does NOT make a card
in_domain — a mechanism you track turning
up outside your domain is exactly the catch this is for.

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
from .niche import domain_marker_phrases

_CANDIDATE_UNIVERSE_CAP = 1000  # lowered from 2500 (2026-09-11): the worker runs
# on Render's free 512 MB instance; scoring builds an in-memory structure per
# candidate (matched terms, embedding cosine vs every anchor, per-card signal
# writes) and this is the single biggest transient allocation in the pipeline.
# Widening broad_categories earlier this session (cs.RO/SE/HC/SY/NE, for
# cross-domain reach) means the daily universe hits this cap far more often
# than before. Observed live: the container crashed mid-run 3x in one night at
# unpredictable phases (once mid-DBLP, once mid-crossref) after the DBLP and
# categories-NULL bugs were already fixed — the signature of an OOM kill, not
# a deterministic fault. 1000 recent candidates/day is still generous (arXiv
# alone announces roughly that many across these categories per day) and cuts
# this step's peak footprint well below what 2500 was costing.
SIGNAL_NAMES = ("embedding_sim", "lexical", "concept_overlap", "cocitation_velocity", "hf_upvotes")
CROSS_DOMAIN_CAP = 20


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
    domain_phrases = domain_marker_phrases(cfg)

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
        return {"cards": [], "cross_domain": [], "surfaced": 0, "universe": 0, "truncated_at": None}

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
        in_domain = bool(matched_terms(domain_phrases, text))
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
        scored.append({"row": c, "matched": matched, "in_domain": in_domain, "sig": sig,
                       "best_anchor": best_anchor, "coc": coc, "hf": hf})

    if not scored:
        return {"cards": [], "cross_domain": [], "surfaced": 0, "universe": len(cands), "truncated_at": None}

    # ---- the two-section split, by `in_domain` alone -------------------------
    # Every candidate here already cleared the recall gate on SOME signal — the
    # split is only about which of the two sections it belongs in, never a second
    # bar to clear. Earlier this also required embedding_sim >= a floor for
    # "new field", which silently routed a not-in-domain paper that passed the
    # gate on a *lexical* or *co-citation* signal (not embedding) into the
    # "in your field" bucket — exactly backwards. `in_domain` (matched a
    # niche_queries domain PHRASE, e.g. "hallucination detection") is the only
    # test: not matching it is what "not your field" means, full stop.
    cross = sorted((s for s in scored if not s["in_domain"]),
                    key=lambda s: -(s["sig"]["embedding_sim"] or 0.0))[:CROSS_DOMAIN_CAP]

    # ---- main list: in_domain candidates, ranked by the usual composite -----
    main = [s for s in scored if s["in_domain"]]
    norms = {
        name: _minmax([float(s["sig"][name] or 0.0) for s in scored])
        for name in SIGNAL_NAMES
    }
    for s in main:
        s["composite"] = round(sum(
            (weights.get(name, 0.0)) * norms[name](float(s["sig"][name] or 0.0))
            for name in SIGNAL_NAMES
        ), 5)
    main.sort(key=lambda s: -s["composite"])

    truncated_at = None
    if len(main) > hard_cap:
        truncated_at = hard_cap
        main = main[:hard_cap]

    run_date = dt.date.today()

    def build(s: dict, rank: int, rank_kind: str) -> dict:
        c = s["row"]
        tag = tag_map.get(c["paper_id"], {"tier": "ndata", "delta": None})
        why = _why(s, tag)
        anchor_label = None
        if s["best_anchor"]:
            kind, atext = s["best_anchor"]
            anchor_label = (atext[:60] + "…") if atext and len(atext) > 60 else (atext or kind)
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
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (paper_id, run_date, name) DO UPDATE SET value = EXCLUDED.value, detail = EXCLUDED.detail",
            (c["paper_id"], run_date, rank_kind, float(rank),
             Json({"composite": s.get("composite"), "why": why})),
        )
        return {
            "paper_id": c["paper_id"],
            "title": c["title"],
            "authors": c["authors"][:8],
            "announce_date": c["announce_date"].isoformat() if c["announce_date"] else None,
            "link": c["link"],
            "sources": src_map.get(c["paper_id"], []),
            "rank": rank,
            "composite": s.get("composite"),
            "signals": s["sig"],
            "why": why,
            "matched": s["matched"],
            "in_domain": s["in_domain"],
            "anchor_label": anchor_label,
            "abstract": c["abstract"],
            "abstract_missing": c["abstract_missing"],
            "citation_tag": {
                "tier": tag["tier"], "delta": tag["delta"],
                "badge": citation_tier.BADGE[tag["tier"]],
            },
        }

    cross_cards = [build(s, rank, "cross_domain_rank") for rank, s in enumerate(cross, 1)]
    cards = [build(s, rank, "composite_rank") for rank, s in enumerate(main, 1)]

    return {"cards": cards, "cross_domain": cross_cards, "surfaced": len(cards),
            "universe": len(cands), "truncated_at": truncated_at}


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
