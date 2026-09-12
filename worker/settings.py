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
# Defaults to 96 (one batch) — the safe no-card-tier value — because this was
# raised prematurely once already (2026-09-12) on Rishu's word that he'd added
# a card, without actually verifying it server-side first, and a production
# run promptly ate 5 real 429s and failed. Voyage's own error body was
# unambiguous: {"detail": "You have not yet added your payment method... you
# should see your rate limits increase after several minutes"} — so either it
# hadn't propagated yet or the billing setup didn't fully complete. Don't
# trust "I added the card" or a small (~3-request) test alone — 3 requests
# fits inside the free tier's own 3RPM ceiling either way, so it can't
# distinguish the two tiers. VERIFY with a real ~96-paper batch (the size that
# actually exercises the limit) before raising this again — see the
# diagnostic script pattern used to catch this, in worker/enrich/embeddings.py's
# embed_new_papers() comment.
VOYAGE_MAX_PER_RUN = int(os.environ.get("VOYAGE_MAX_PER_RUN", "96"))

# arXiv politeness (do NOT lower the interval — a shared IP gets throttled fast)
ARXIV_MIN_INTERVAL_SECONDS = float(os.environ.get("ARXIV_MIN_INTERVAL_SECONDS", "3.0"))
ARXIV_MAX_RETRIES = int(os.environ.get("ARXIV_MAX_RETRIES", "5"))
ARXIV_BACKOFF_START_SECONDS = float(os.environ.get("ARXIV_BACKOFF_START_SECONDS", "5.0"))

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
