"""Shared JSON HTTP helper for the non-arXiv sources / enrichment APIs.

Polite by default: identifying User-Agent, timeout, exponential backoff on 429
and 5xx, bounded retries. Every call can report to a `on_call(source, endpoint,
status, latency_ms, quota_note)` sink for api_call_log.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from . import settings

OnCall = Callable[..., None]

_DEFAULT_RETRIES = 4
_BACKOFF_START = 3.0


def get_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    source: str = "http",
    endpoint: str = "",
    on_call: OnCall | None = None,
    retries: int = _DEFAULT_RETRIES,
    timeout: float = 45.0,
    pace: float = 0.0,
) -> Any | None:
    """Return parsed JSON, or None on give-up (never raises for network issues —
    enrichment must degrade, not crash)."""
    hdrs = {"User-Agent": settings.USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    delay = _BACKOFF_START
    for attempt in range(retries + 1):
        if pace and attempt == 0:
            time.sleep(pace)
        t0 = time.monotonic()
        try:
            r = httpx.get(url, params=params, headers=hdrs, timeout=timeout,
                          follow_redirects=True)
            latency = int((time.monotonic() - t0) * 1000)
            if on_call:
                on_call(source=source, endpoint=endpoint or url, status=r.status_code,
                        latency_ms=latency)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code in (400, 401, 403, 404, 422):
                return None  # terminal — often "no more results" or missing key
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            if on_call:
                on_call(source=source, endpoint=endpoint or url, status=None,
                        latency_ms=int((time.monotonic() - t0) * 1000), quota_note=repr(exc))
            time.sleep(delay)
            delay *= 2
    return None


def post_json(
    url: str,
    *,
    json_body: Any,
    headers: dict | None = None,
    source: str = "http",
    endpoint: str = "",
    on_call: OnCall | None = None,
    retries: int = _DEFAULT_RETRIES,
    timeout: float = 45.0,
) -> Any | None:
    hdrs = {"User-Agent": settings.USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    delay = _BACKOFF_START
    for _ in range(retries + 1):
        t0 = time.monotonic()
        try:
            r = httpx.post(url, json=json_body, headers=hdrs, timeout=timeout)
            latency = int((time.monotonic() - t0) * 1000)
            if on_call:
                on_call(source=source, endpoint=endpoint or url, status=r.status_code,
                        latency_ms=latency)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code in (400, 401, 403, 404, 422):
                return None
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            if on_call:
                on_call(source=source, endpoint=endpoint or url, status=None,
                        latency_ms=int((time.monotonic() - t0) * 1000), quota_note=repr(exc))
            time.sleep(delay)
            delay *= 2
    return None
