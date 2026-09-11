"""Embeddings for broad-track structural similarity.

Two backends, tried in this order:
  1. Hosted (Voyage AI) — preferred. One HTTP call, no local RAM, works on
     Render's free tier. Voyage's 200M-token free grant covers this system's
     volume for years at ~500 papers/day (https://voyageai.com pricing).
  2. Self-hosted (sentence-transformers, default `all-MiniLM-L6-v2`) — used
     only when VOYAGE_API_KEY is unset. Needs worker/requirements.txt (not
     -core.txt) and enough RAM (~400 MB+) — see the Cost note in render.yaml.

Vectors are stored as REAL[]; cosine is computed in Python at score time.
DEGRADES: if neither backend is configured, every function here is a logged
no-op and the pipeline branches to lexical-only scoring.
"""
from __future__ import annotations

import psycopg

from .. import db, http, settings, vectors

try:  # only needed for the self-hosted fallback
    from sentence_transformers import SentenceTransformer  # type: ignore

    _ST_AVAILABLE = True
except Exception:  # noqa: BLE001
    SentenceTransformer = None  # type: ignore
    _ST_AVAILABLE = False

HOSTED = bool(settings.VOYAGE_API_KEY)
AVAILABLE = HOSTED or _ST_AVAILABLE

_MODELS: dict[str, object] = {}
_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_BATCH = 96


def active_model_version(cfg: dict) -> str:
    """The model actually doing the encoding right now — may differ from
    config.embedding_model_version if Voyage is configured (it takes
    priority)."""
    return settings.VOYAGE_MODEL if HOSTED else cfg["embedding_model_version"]


def _model(name: str):
    if not _ST_AVAILABLE:
        raise RuntimeError("sentence-transformers not installed")
    if name not in _MODELS:
        _MODELS[name] = SentenceTransformer(name)
    return _MODELS[name]


def _encode_hosted(texts: list[str], on_call=None) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), _VOYAGE_BATCH):
        chunk = texts[i:i + _VOYAGE_BATCH]
        data = http.post_json(
            _VOYAGE_URL,
            json_body={"input": chunk, "model": settings.VOYAGE_MODEL, "input_type": "document"},
            headers={"Authorization": f"Bearer {settings.VOYAGE_API_KEY}"},
            source="voyage", endpoint="embeddings", on_call=on_call,
        )
        if not data or "data" not in data:
            raise RuntimeError(f"Voyage embeddings call failed: {data}")
        by_index = {d["index"]: d["embedding"] for d in data["data"]}
        out.extend(by_index[j] for j in range(len(chunk)))
    return out


def _encode(model_or_cfg_name: str, texts: list[str], on_call=None) -> list[list[float]]:
    if HOSTED:
        return _encode_hosted(texts, on_call=on_call)
    vecs = _model(model_or_cfg_name).encode(texts, normalize_embeddings=True, show_progress_bar=False)
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


def embed_new_papers(conn: psycopg.Connection, model_version: str, *, limit: int = 400,
                     on_call=None) -> dict:
    if not AVAILABLE:
        return {"skipped": "no embedding backend configured (set VOYAGE_API_KEY, or install worker/requirements.txt)", "embedded": 0}
    mv = settings.VOYAGE_MODEL if HOSTED else model_version
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
        (mv, limit),
    )
    if not rows:
        return {"embedded": 0, "backend": "voyage" if HOSTED else "local", "model": mv}
    texts = [f"{r['title']}\n{r['abstract'] or ''}".strip() for r in rows]
    batch = _VOYAGE_BATCH if HOSTED else 64
    for i in range(0, len(rows), batch):
        for r, v in zip(rows[i:i + batch], _encode(mv, texts[i:i + batch], on_call=on_call)):
            db.execute(
                conn,
                "INSERT INTO embeddings (paper_id, model_version, embedding) VALUES (%s, %s, %s) "
                "ON CONFLICT (paper_id, model_version) DO UPDATE SET embedding = EXCLUDED.embedding",
                (r["paper_id"], mv, v),
            )
    return {"embedded": len(rows), "backend": "voyage" if HOSTED else "local", "model": mv}


def embed_anchors(conn: psycopg.Connection, cfg: dict, *, on_call=None) -> dict:
    if not AVAILABLE:
        return {"skipped": "no embedding backend configured"}
    mv = active_model_version(cfg)
    n = 0

    for i, text in enumerate(cfg.get("open_problems") or []):
        (v,) = _encode(mv, [text], on_call=on_call)
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
        (v,) = _encode(mv, [f"{row['title']}\n{row['abstract'] or ''}"], on_call=on_call)
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
    return {"anchors": n, "backend": "voyage" if HOSTED else "local", "model": mv}
