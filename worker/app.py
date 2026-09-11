"""Worker web service:  uvicorn worker.app:app --host 0.0.0.0 --port $PORT

Runs on requirements-core.txt (no torch/sentence-transformers — that heavy
local-model path stays in requirements.txt for optional self-hosted mode) so
it fits Render's free tier (512 MB, no card required). numpy/scikit-learn/
hdbscan ARE in requirements-core: clustering itself is cheap, only a local
neural embedding model needed real RAM. Embeddings run via the hosted Voyage
AI API (worker/enrich/embeddings.py, HOSTED = bool(VOYAGE_API_KEY)) instead of
a local model — no extra RAM either way, just an HTTP call. The daily pipeline
and the one-off bootstrap are triggered over HTTP by a scheduled GitHub
Actions workflow rather than a Render Cron Job (Render's cron plan has no free
tier and requires a card on file — this avoids that entirely). All internal
endpoints require the shared secret.

/internal/run and /internal/bootstrap both take a whole-pipeline Postgres
advisory lock (arxiv_lock.PIPELINE_LOCK_KEY) before doing any real work and
return {"status": "skipped"} immediately if another run already holds it —
learned this the hard way: a slow/retried trigger plus a second manual one
piled up 2-3 full pipelines on this 512 MB instance at once and OOM-killed the
container, orphaning every one of them as a permanently "running" row.
"""
from __future__ import annotations

import hmac

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import settings

app = FastAPI(title="Paper Radar worker", version="0.1.0")


def _check_secret(secret: str | None) -> None:
    expected = settings.INTERNAL_SHARED_SECRET
    if not expected or not secret or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=401, detail="bad or missing x-internal-secret")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "paper-radar-worker", "version": app.version}


class BackfillTermIn(BaseModel):
    term: str
    kind: str = "keyword"  # "keyword" | "query"


@app.post("/internal/backfill-term")
def backfill_term(body: BackfillTermIn,
                  x_internal_secret: str | None = Header(default=None)) -> dict:
    _check_secret(x_internal_secret)
    if not body.term.strip():
        raise HTTPException(status_code=400, detail="term is empty")
    from .ingest import term_backfill
    return term_backfill.run_one(body.term.strip(), body.kind)


@app.post("/internal/run")
def internal_run(x_internal_secret: str | None = Header(default=None)) -> dict:
    _check_secret(x_internal_secret)
    from .run import run_daily
    return run_daily(kind="manual")


@app.post("/internal/bootstrap")
def internal_bootstrap(x_internal_secret: str | None = Header(default=None)) -> dict:
    """One-off 12-16 week history backfill. Trigger once after first deploy
    (or via the 'bootstrap' GitHub Actions workflow_dispatch), not on a
    schedule — see worker/backfill.py."""
    _check_secret(x_internal_secret)
    from .backfill import run_bootstrap
    return run_bootstrap()
