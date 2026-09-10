"""run_log / api_call_log writers + the alert path.

These open their own short-lived connections so a run-log row is durable even if
a later pipeline step raises.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from psycopg.types.json import Json

from . import db, telegram


class RunLog:
    def __init__(self, kind: str = "daily", run_date: dt.date | None = None,
                 config_version: int | None = None):
        self.kind = kind
        self.run_date = run_date or dt.date.today()
        self.config_version = config_version
        self.id: int | None = None
        self.stats: dict[str, Any] = {}
        self.errors: list[dict] = []

    def start(self) -> "RunLog":
        with db.connect() as conn:
            row = db.q1(
                conn,
                "INSERT INTO run_log (run_date, kind, status, config_version) "
                "VALUES (%s, %s, 'running', %s) RETURNING id",
                (self.run_date, self.kind, self.config_version),
            )
            self.id = row["id"]
        return self

    def stat(self, key: str, value: Any) -> None:
        self.stats[key] = value

    def incr(self, key: str, by: int = 1) -> None:
        self.stats[key] = int(self.stats.get(key, 0)) + by

    def error(self, where: str, exc: BaseException | str) -> None:
        self.errors.append({
            "where": where,
            "error": exc if isinstance(exc, str) else repr(exc),
            "at": dt.datetime.utcnow().isoformat(timespec="seconds"),
        })

    def api_call(self, source: str, endpoint: str, *, status: int | None = None,
                 latency_ms: int | None = None, quota_note: str | None = None) -> None:
        with db.connect() as conn:
            db.execute(
                conn,
                "INSERT INTO api_call_log (run_id, source, endpoint, status, latency_ms, quota_note) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (self.id, source, endpoint, status, latency_ms, quota_note),
            )

    def finish(self, status: str = "ok") -> dict:
        with db.connect() as conn:
            db.execute(
                conn,
                "UPDATE run_log SET finished_at = now(), status = %s, stats = %s, errors = %s "
                "WHERE id = %s",
                (status, Json(self.stats), Json(self.errors), self.id),
            )
        return {"run_id": self.id, "status": status, "stats": self.stats,
                "errors": self.errors}


def check_and_alert_staleness() -> None:
    """Alert if the last successful daily run is > 26h old."""
    with db.connect() as conn:
        row = db.q1(
            conn,
            "SELECT max(finished_at) AS last_ok FROM run_log "
            "WHERE kind IN ('daily','manual') AND status = 'ok'",
        )
    last_ok = row["last_ok"] if row else None
    if last_ok is None:
        return
    age = dt.datetime.now(dt.timezone.utc) - last_ok
    if age > dt.timedelta(hours=26):
        telegram.send(
            f"[Paper Radar] No successful run in {age.total_seconds() / 3600:.0f}h. "
            f"Check the Render cron logs."
        )
