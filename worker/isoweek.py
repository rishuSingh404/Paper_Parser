"""ISO-week helpers. One definition of "the week", used everywhere.

Week label format: 'IYYY-Www', e.g. '2026-W37' (matches Postgres
to_char(d, 'IYYY"-W"IW')). All bucketing is by a paper's real announce /
publication date, never the date a keyword was added — see plan step 0 / the
Dashboard "why real-week bucketing" note.
"""
from __future__ import annotations

import datetime as dt


def iso_week(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def current_iso_week(today: dt.date | None = None) -> str:
    return iso_week(today or dt.date.today())


def week_monday(label: str) -> dt.date:
    """Monday date of an 'IYYY-Www' label."""
    year_s, week_s = label.split("-W")
    return dt.date.fromisocalendar(int(year_s), int(week_s), 1)


def weeks_between(earlier: str, later: str) -> int:
    """Number of ISO weeks from `earlier` to `later` (later - earlier)."""
    return (week_monday(later) - week_monday(earlier)).days // 7


def recent_weeks(current: str | None = None, n: int = 6) -> list[str]:
    """The `n` ISO-week labels strictly before `current`, oldest first."""
    cur = current or current_iso_week()
    monday = week_monday(cur)
    return [iso_week(monday - dt.timedelta(weeks=k)) for k in range(n, 0, -1)]
