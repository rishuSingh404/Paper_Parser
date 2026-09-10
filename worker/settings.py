"""Environment-derived settings. No secrets live in this file."""
from __future__ import annotations

import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Shared secret between the Vercel dashboard and this worker's web service.
INTERNAL_SHARED_SECRET = os.environ.get("INTERNAL_SHARED_SECRET", "")

# Telegram digest delivery
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Enrichment (all optional — the system degrades to arXiv-only without them)
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "")
CORE_API_KEY = os.environ.get("CORE_API_KEY", "")
CROSSREF_MAILTO = os.environ.get("CROSSREF_MAILTO", "")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

# arXiv politeness (do NOT lower the interval — a shared IP gets throttled fast)
ARXIV_MIN_INTERVAL_SECONDS = float(os.environ.get("ARXIV_MIN_INTERVAL_SECONDS", "3.0"))
ARXIV_MAX_RETRIES = int(os.environ.get("ARXIV_MAX_RETRIES", "5"))
ARXIV_BACKOFF_START_SECONDS = float(os.environ.get("ARXIV_BACKOFF_START_SECONDS", "5.0"))

# Bound on the dashboard-triggered inline backfill so it stays inside the
# serverless timeout. Leftover is finished by the next daily run (pipeline step 0).
TERM_BACKFILL_MAX_REQUESTS = int(os.environ.get("TERM_BACKFILL_MAX_REQUESTS", "8"))
TERM_BACKFILL_PAGE_SIZE = int(os.environ.get("TERM_BACKFILL_PAGE_SIZE", "100"))

# Daily fetch sizing (kept small for Phase 0; raised in Phase 1/4)
DAILY_MAX_RESULTS_PER_QUERY = int(os.environ.get("DAILY_MAX_RESULTS_PER_QUERY", "100"))

_repo = "https://github.com/rishuSingh404/Paper_Parser"
USER_AGENT = os.environ.get(
    "PAPER_RADAR_USER_AGENT",
    f"PaperRadar/{'0.1.0'} (+{_repo}; mailto:{CROSSREF_MAILTO or 'unknown@example.com'})",
)


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return DATABASE_URL
