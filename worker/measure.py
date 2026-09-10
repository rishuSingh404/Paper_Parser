""""Is it working" measurement (plan step / Phase 6).

precision@k over recent broad-track cards, and a lead-time audit helper. Read by
the dashboard's /api/state and runnable standalone:  python -m worker.measure
"""
from __future__ import annotations

import datetime as dt

from . import db

IGNORE_AFTER_DAYS = 5


def precision_at_k(conn, k: int = 10, weeks: int = 8) -> dict:
    rows = db.q(
        conn,
        """
        SELECT run_date, paper_id, rank, outcome, outcome_at
        FROM digest_cards
        WHERE section = 'broad' AND rank <= %s
          AND run_date > CURRENT_DATE - make_interval(weeks => %s)
        ORDER BY run_date
        """,
        (k, weeks),
    )
    by_week: dict[str, dict] = {}
    for r in rows:
        wk = r["run_date"].strftime("%G-W%V")
        b = by_week.setdefault(wk, {"liked": 0, "disliked": 0, "ignored": 0, "pending": 0, "n": 0})
        b["n"] += 1
        o = r["outcome"]
        if o == "liked":
            b["liked"] += 1
        elif o in ("disliked", "muted"):
            b["disliked"] += 1
        elif o == "ignored":
            b["ignored"] += 1
        elif (dt.date.today() - r["run_date"]).days >= IGNORE_AFTER_DAYS:
            b["ignored"] += 1  # shown long enough with no action == ignored
        else:
            b["pending"] += 1

    series = []
    for wk in sorted(by_week):
        b = by_week[wk]
        judged = b["liked"] + b["disliked"] + b["ignored"]
        series.append({
            "week": wk, **b,
            "precision": round(b["liked"] / judged, 3) if judged else None,
        })
    return {"k": k, "weeks": weeks, "series": series}


def lead_time_audit(conn) -> list[dict]:
    """For papers labelled 'blew_up' in eval_labels, when (if ever) did the radar
    first surface them in a digest?"""
    rows = db.q(
        conn,
        """
        SELECT el.paper_id, p.title, p.first_seen_at,
               min(dc.run_date) AS first_carded
        FROM eval_labels el
        JOIN papers p ON p.paper_id = el.paper_id
        LEFT JOIN digest_cards dc ON dc.paper_id = el.paper_id
        WHERE el.label = 'blew_up'
        GROUP BY el.paper_id, p.title, p.first_seen_at
        ORDER BY p.first_seen_at DESC
        """,
    )
    out = []
    for r in rows:
        lead = None
        if r["first_carded"]:
            lead = (r["first_carded"] - r["first_seen_at"].date()).days
        out.append({
            "paper_id": r["paper_id"], "title": r["title"],
            "surfaced": r["first_carded"].isoformat() if r["first_carded"] else None,
            "lead_days_from_first_seen": lead,
            "missed": r["first_carded"] is None,
        })
    return out


if __name__ == "__main__":
    with db.connect() as c:
        print(precision_at_k(c))
        for row in lead_time_audit(c):
            print(row)
