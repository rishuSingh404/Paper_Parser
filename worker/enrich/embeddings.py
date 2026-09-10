"""Self-hosted embeddings for broad-track structural similarity.

Default model `all-MiniLM-L6-v2` (384-d, ~300-400 MB RSS, Render Starter);
switch to `allenai/specter2_base` via config.embedding_model_version + a
Standard-tier Cron Job (Phase 2 must profile the real RSS first — see render.yaml).

Vectors are stored as REAL[]; cosine is computed in Python at score time.

DEGRADES: if sentence-transformers / torch are not installed, every function
here is a logged no-op and the pipeline branches to lexical-only scoring.
"""
from __future__ import annotations

import psycopg

from .. import db, vectors

try:  # Phase 2 ML stack
    from sentence_transformers import SentenceTransformer  # type: ignore

    AVAILABLE = True
except Exception:  # noqa: BLE001
    SentenceTransformer = None  # type: ignore
    AVAILABLE = False

_MODELS: dict[str, object] = {}


def _model(name: str):
    if not AVAILABLE:
        raise RuntimeError("sentence-transformers not installed")
    if name not in _MODELS:
        _MODELS[name] = SentenceTransformer(name)
    return _MODELS[name]


def _encode(name: str, texts: list[str]) -> list[list[float]]:
    vecs = _model(name).encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [[float(x) for x in v] for v in vecs]


def _upsert_anchor(conn, kind: str, anchor_id: str, mv: str, vec: list[float], text: str) -> None:
    db.execute(
        conn,
        "INSERT INTO anchor_embeddings (anchor_kind, anchor_id, model_version, embedding, text) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (anchor_kind, anchor_id, model_version) "
        "DO UPDATE SET embedding = EXCLUDED.embedding, text = EXCLUDED.text, updated_at = now()",
        (kind, anchor_id, mv, vec, text),
    )


def embed_new_papers(conn: psycopg.Connection, model_version: str, *, limit: int = 400) -> dict:
    if not AVAILABLE:
        return {"skipped": "no ML deps", "embedded": 0}
    rows = db.q(
        conn,
        """
        SELECT p.paper_id, p.title, p.abstract
        FROM papers p
        LEFT JOIN embeddings e ON e.paper_id = p.paper_id AND e.model_version = %s
        WHERE e.paper_id IS NULL AND NOT p.muted
        ORDER BY p.first_seen_at DESC
        LIMIT %s
        """,
        (model_version, limit),
    )
    if not rows:
        return {"embedded": 0}
    texts = [f"{r['title']}\n{r['abstract'] or ''}".strip() for r in rows]
    for i in range(0, len(rows), 64):
        for r, v in zip(rows[i:i + 64], _encode(model_version, texts[i:i + 64])):
            db.execute(
                conn,
                "INSERT INTO embeddings (paper_id, model_version, embedding) VALUES (%s, %s, %s) "
                "ON CONFLICT (paper_id, model_version) DO UPDATE SET embedding = EXCLUDED.embedding",
                (r["paper_id"], model_version, v),
            )
    return {"embedded": len(rows)}


def embed_anchors(conn: psycopg.Connection, cfg: dict) -> dict:
    if not AVAILABLE:
        return {"skipped": "no ML deps"}
    mv = cfg["embedding_model_version"]
    n = 0

    for i, text in enumerate(cfg.get("open_problems") or []):
        (v,) = _encode(mv, [text])
        _upsert_anchor(conn, "open_problem", str(i), mv, v, text)
        n += 1

    for sp in cfg.get("seed_papers") or []:
        row = db.q1(
            conn,
            "SELECT paper_id, title, abstract FROM papers WHERE arxiv_id = %s OR paper_id = %s",
            (sp, f"arxiv:{sp}"),
        )
        if not row:
            continue
        (v,) = _encode(mv, [f"{row['title']}\n{row['abstract'] or ''}"])
        _upsert_anchor(conn, "seed_paper", row["paper_id"], mv, v, row["title"])
        n += 1

    liked = db.q(
        conn,
        "SELECT e.embedding AS emb FROM feedback f JOIN embeddings e "
        "ON e.paper_id = f.paper_id AND e.model_version = %s WHERE f.verdict = 'liked'",
        (mv,),
    )
    vs = [vectors.parse_pg(r["emb"]) for r in liked]
    vs = [v for v in vs if v]
    if vs:
        dim = len(vs[0])
        centroid = [sum(v[j] for v in vs) / len(vs) for j in range(dim)]
        _upsert_anchor(conn, "liked_centroid", "centroid", mv, centroid,
                       f"centroid of {len(vs)} liked papers")
        n += 1
    return {"anchors": n}
