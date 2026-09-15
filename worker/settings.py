"""Environment-derived settings. No secrets live in this file."""
from __future__ import annotations

import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Shared secret between the Vercel dashboard and this worker's web service.
INTERNAL_SHARED_SECRET = os.environ.get("INTERNAL_SHARED_SECRET", "")

# Telegram digest delivery
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Public dashboard URL (used only for the "full digest" link in the Telegram message)
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")

# This worker's OWN public URL — used only for the self-heartbeat (see
# run.py's _heartbeat()). Render's free web services spin down after ~15 min
# with no incoming HTTP traffic; /internal/run ACKs instantly and does the
# real work in a background thread, so once that response goes out, nothing
# else reaches Render's edge for the rest of a run — the idle clock has
# nothing to reset it. Observed live (2026-09-13/14): three separate runs
# died silently 10-35 minutes in, right in that window, only since switching
# to the instant-ACK pattern. A self-ping to this URL every few minutes
# during a run is free traffic that keeps the idle clock reset.
WORKER_PUBLIC_URL = os.environ.get("WORKER_PUBLIC_URL", "https://paper-radar-worker-api.onrender.com")

# Enrichment (all optional — the system degrades to arXiv-only without them)
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "")
CORE_API_KEY = os.environ.get("CORE_API_KEY", "")
CROSSREF_MAILTO = os.environ.get("CROSSREF_MAILTO", "")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

# Hosted embeddings (preferred — works on the free Render tier, no local RAM
# needed). Voyage AI has a 200M-token one-time free grant, which covers this
# system's volume for years. Self-hosted sentence-transformers is the fallback
# when this is unset and the ML deps happen to be installed (worker/requirements.txt).
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL = os.environ.get("VOYAGE_MODEL", "voyage-4-lite")
# Per-run cap on how many papers embed_new_papers() will attempt. Once a
# Voyage payment method is CONFIRMED active (2000 RPM / 16M TPM — still $0
# real cost, a card only raises the rate ceiling, not what's billed), this can
# go up to a few thousand and clear the backlog in well under a minute.
#
# Was stuck at 96 (one batch, the safe no-card value) for a bit: raising it
# on "I added a card" alone turned out to be premature TWICE — first because a
# 3-request test can't distinguish the tiers (fits inside the free 3RPM
# ceiling either way), second because the card was on file but not set as
# Voyage's DEFAULT payment method, and Voyage's rate-limit upgrade apparently
# keys off the default, not just "a card exists on the org". Confirmed fixed
# 2026-09-12 with the test that actually matters: a real 96-paper / ~32K-token
# batch request (the size that exercises the limit) returned a clean 200
# after Rishu set the card as default. If this ever regresses, re-run that
# exact check before touching this value — a small request proves nothing.
VOYAGE_MAX_PER_RUN = int(os.environ.get("VOYAGE_MAX_PER_RUN", "5000"))

# Separate, much smaller cap for the DAILY pipeline specifically (run.py
# passes this explicitly; worker.backfill's one-off bootstrap keeps using
# embed_new_papers's own 5000 default, unchanged — a controlled one-time
# catch-up is a different risk profile than every ordinary day). Added
# 2026-09-15 after run 44: with no explicit limit, a daily run defaulted to
# min(5000, VOYAGE_MAX_PER_RUN) = 5000 — comfortably more than the 1,040-paper
# backlog that had built up after ~30h of failed runs, so it tried to embed
# the WHOLE backlog in one run, layered on top of everything else already
# running in the same process (ingestion from 7 sources, scoring, clustering)
# on Render's memory-constrained worker tier. The run died silently mid-batch
# with no error logged — the signature of a hard kill (OOM), not a graceful
# failure; see score.py's _CANDIDATE_UNIVERSE_CAP comment for the same prior
# pattern. A bounded daily slice means a large backlog drains gradually
# across several days instead of risking the whole run in one memory spike.
DAILY_EMBED_LIMIT = int(os.environ.get("DAILY_EMBED_LIMIT", "150"))

# arXiv politeness (do NOT lower the interval — a shared IP gets throttled fast)
ARXIV_MIN_INTERVAL_SECONDS = float(os.environ.get("ARXIV_MIN_INTERVAL_SECONDS", "3.0"))
# Lowered from 5 (2026-09-15): during a sustained 429 block a single query
# exhausting 5 retries with a 60s timeout and growing backoff was measured
# taking 5+ minutes in production (run 42/43's api_call_log — individual
# attempts logged at 15.5s/25.9s/30.7s/45.6s/60.6s). The run's own arXiv
# circuit breaker (run.py) needs only 1-2 queries to prove a sustained block,
# so a smaller per-query budget here means it actually trips fast instead of
# still burning most of the run's time budget before it gets the chance to.
ARXIV_MAX_RETRIES = int(os.environ.get("ARXIV_MAX_RETRIES", "2"))
ARXIV_BACKOFF_START_SECONDS = float(os.environ.get("ARXIV_BACKOFF_START_SECONDS", "5.0"))
# Was a 60s hardcoded httpx timeout in ingest/arxiv.py — same measured cause
# as above: a hung/slow connection could eat a full 60s per attempt on its
# own, independent of the backoff between attempts. 429 responses are near-
# instant when arXiv actually answers (~200-400ms observed); anything not
# back by 15s is already a hang, not a real response worth waiting out.
ARXIV_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("ARXIV_REQUEST_TIMEOUT_SECONDS", "15.0"))

# Bound on the dashboard-triggered inline backfill so it stays inside the
# serverless timeout. Leftover is finished by the next daily run (pipeline step 0).
TERM_BACKFILL_MAX_REQUESTS = int(os.environ.get("TERM_BACKFILL_MAX_REQUESTS", "8"))
TERM_BACKFILL_PAGE_SIZE = int(os.environ.get("TERM_BACKFILL_PAGE_SIZE", "100"))

# Daily fetch sizing
DAILY_MAX_RESULTS_PER_QUERY = int(os.environ.get("DAILY_MAX_RESULTS_PER_QUERY", "100"))
DAILY_BROAD_MAX_RESULTS = int(os.environ.get("DAILY_BROAD_MAX_RESULTS", "300"))

_repo = "https://github.com/rishuSingh404/Paper_Parser"
USER_AGENT = os.environ.get(
    "PAPER_RADAR_USER_AGENT",
    f"PaperRadar/{'0.1.0'} (+{_repo}; mailto:{CROSSREF_MAILTO or 'unknown@example.com'})",
)


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return DATABASE_URL
