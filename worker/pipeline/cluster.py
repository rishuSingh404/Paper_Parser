"""Emergent-cluster rollup (plan step 12).

Embed last ~30 days of broad-track abstracts -> HDBSCAN -> label each cluster
with top c-TF-IDF terms + 3 representative titles -> MATCH to the previous
period by >=0.5 Jaccard on member paper ids and carry a stable `cluster_key`
(HDBSCAN's per-run labels are NOT stable across runs). The dashboard shows
continuity (`prev_size`, "new") only via `cluster_key`.

DEGRADES: needs numpy + scikit-learn + hdbscan. Without them this is a logged
no-op and the dashboard simply shows no cluster rollup.
"""
from __future__ import annotations

import datetime as dt
import hashlib

import psycopg

from .. import db, vectors

try:
    import numpy as np  # type: ignore
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    import hdbscan  # type: ignore

    AVAILABLE = True
except Exception:  # noqa: BLE001
    AVAILABLE = False

_JACCARD_MATCH = 0.5


def _key(period: str, members: list[str]) -> str:
    h = hashlib.sha1(("|".join(sorted(members[:5]))).encode()).hexdigest()[:10]
    return f"{period}:{h}"


def cluster_recent(conn: psycopg.Connection, cfg: dict, *, period: str | None = None,
                   days: int = 30, min_cluster_size: int = 4) -> dict:
    if not AVAILABLE:
        return {"skipped": "no ML deps (numpy/scikit-learn/hdbscan)"}
    from ..enrich.embeddings import active_model_version
    mv = active_model_version(cfg)
    period = period or dt.date.today().strftime("%Y-%m")

    rows = db.q(
        conn,
        """
        SELECT p.paper_id, p.title, p.abstract, e.embedding
        FROM papers p JOIN embeddings e
          ON e.paper_id = p.paper_id AND e.model_version = %s
        WHERE NOT p.muted
          AND COALESCE(p.announce_date, p.first_seen_at::date) > CURRENT_DATE - %s
        """,
        (mv, days),
    )
    if len(rows) < min_cluster_size * 2:
        return {"clustered": 0, "note": "not enough embedded papers"}

    X = np.array([vectors.parse_pg(r["embedding"]) for r in rows], dtype="float32")
    labels = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean").fit_predict(X)

    docs = [f"{r['title']} {r['abstract'] or ''}" for r in rows]
    tfidf = TfidfVectorizer(max_features=4000, stop_words="english", ngram_range=(1, 2))
    M = tfidf.fit_transform(docs)
    vocab = np.array(tfidf.get_feature_names_out())

    prev = db.q(conn, "SELECT cluster_key, member_paper_ids FROM clusters "
                      "WHERE period = (SELECT max(period) FROM clusters WHERE period < %s)", (period,))
    prev_sets = {p["cluster_key"]: set(p["member_paper_ids"]) for p in prev}

    written = 0
    for lab in sorted(set(labels)):
        if lab < 0:
            continue
        idx = [i for i, l in enumerate(labels) if l == lab]
        members = [rows[i]["paper_id"] for i in idx]
        centroid = X[idx].mean(axis=0)
        rep_idx = sorted(idx, key=lambda i: float(np.linalg.norm(X[i] - centroid)))[:3]
        rep_titles = [rows[i]["title"] for i in rep_idx]
        weights = np.asarray(M[idx].mean(axis=0)).ravel()
        label_terms = [str(t) for t in vocab[weights.argsort()[::-1][:6]]]

        mset = set(members)
        matched_key, prev_size = None, None
        for k, pset in prev_sets.items():
            j = len(mset & pset) / max(1, len(mset | pset))
            if j >= _JACCARD_MATCH:
                matched_key, prev_size = k, len(pset)
                break
        ckey = matched_key or _key(period, members)

        db.execute(
            conn,
            """
            INSERT INTO clusters (period, run_cluster_id, cluster_key, label_terms,
                                  member_paper_ids, representative_paper_ids, size, prev_size)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (period, cluster_key) DO UPDATE SET
                run_cluster_id = EXCLUDED.run_cluster_id,
                label_terms = EXCLUDED.label_terms,
                member_paper_ids = EXCLUDED.member_paper_ids,
                representative_paper_ids = EXCLUDED.representative_paper_ids,
                size = EXCLUDED.size,
                prev_size = EXCLUDED.prev_size
            """,
            (period, int(lab), ckey, label_terms, members,
             [rows[i]["paper_id"] for i in rep_idx], len(members), prev_size),
        )
        written += 1
    return {"clustered": written, "period": period, "papers": len(rows)}
