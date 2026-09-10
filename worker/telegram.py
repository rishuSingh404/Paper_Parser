"""Telegram delivery. Plain text (no MarkdownV2) to avoid escaping bugs; chunked
to stay under Telegram's 4096-char message limit (plan step 16)."""
from __future__ import annotations

import httpx

from . import settings

_LIMIT = 3900  # headroom under 4096


def _chunks(text: str) -> list[str]:
    out: list[str] = []
    cur = ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > _LIMIT and cur:
            out.append(cur)
            cur = ""
        # a single monster line still has to be split hard
        while len(line) > _LIMIT:
            out.append(line[:_LIMIT])
            line = line[_LIMIT:]
        cur += line
    if cur:
        out.append(cur)
    return out or [""]


def send(text: str) -> dict:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return {"skipped": "telegram not configured"}
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    statuses: list[int] = []
    for part in _chunks(text):
        try:
            r = httpx.post(
                url,
                json={
                    "chat_id": settings.TELEGRAM_CHAT_ID,
                    "text": part,
                    "disable_web_page_preview": True,
                },
                timeout=30.0,
            )
            statuses.append(r.status_code)
        except httpx.HTTPError as exc:  # never let delivery failure crash a run
            statuses.append(-1)
            return {"sent_chunks": len(statuses), "statuses": statuses, "error": repr(exc)}
    return {"sent_chunks": len(statuses), "statuses": statuses}
