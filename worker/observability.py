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


# A hung network call inside run_daily()'s own thread gets caught by its
# in-process 20-minute timeout (see run.py) — but that only helps if the
# PROCESS stays alive long enough to report it. Observed live (2026-09-13/14):
# a run's container was killed/restarted externally (Render, for whatever
# reason) mid-run, well past the 20-minute ceiling but the row never updated
# — the process was simply gone before it could write its own failure. The
# pipeline advisory lock auto-releases when that happens (session-scoped,
# tied to the dead connection), so a NEW trigger isn't blocked — but the old
# run_log row is left permanently stuck at "running", which both looks like
# an active run (it isn't) and hides the fact that a digest was silently
# missed. No in-process fix can cover "the process itself died"; this has to
# be an external check.
STALE_RUNNING_THRESHOLD_MINUTES = 25  # a bit past run.py's 20-min ceiling


def reap_stale_runs() -> list[int]:
    """Mark any run_log row stuck at 'running' past the threshold as 'error'.
    Safe to call anytime, including with a real run in progress: a genuinely
    active run finishes well under the threshold in the normal case, and
    run.py's own 20-minute ceiling guarantees it never legitimately runs
    past ~20 minutes — so anything still 'running' at 25+ minutes is, by
    construction, orphaned. Returns the ids it reaped."""
    with db.connect() as conn:
        rows = db.q(
            conn,
            "SELECT id, kind, started_at FROM run_log WHERE status = 'running' "
            "AND started_at < now() - make_interval(mins => %s)",
            (STALE_RUNNING_THRESHOLD_MINUTES,),
        )
        for r in rows:
            db.execute(
                conn,
                "UPDATE run_log SET status = 'error', finished_at = now(), "
                "errors = errors || %s::jsonb WHERE id = %s",
                (
                    Json([{
                        "where": "reaper",
                        "error": (
                            "orphaned: stuck at 'running' past the "
                            f"{STALE_RUNNING_THRESHOLD_MINUTES}-minute threshold with no "
                            "resolution — the process almost certainly died externally "
                            "(container restart) before it could report its own outcome"
                        ),
                        "at": dt.datetime.utcnow().isoformat(timespec="seconds"),
                    }]),
                    r["id"],
                ),
            )
    ids = [r["id"] for r in rows]
    if ids:
        telegram.send(
            f"[Paper Radar] Found {len(ids)} stuck run(s) (ids {ids}) that never reported "
            "back — likely a container restart mid-run. Marked as failed so future runs "
            "aren't blocked. If digests keep going missing, this is worth a closer look."
        )
    return ids
