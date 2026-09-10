"""Worker web service:  uvicorn worker.app:app --host 0.0.0.0 --port $PORT

Small and model-free. Serves the dashboard's on-demand term backfill and an
out-of-band pipeline trigger. Both internal endpoints require the shared secret.
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
