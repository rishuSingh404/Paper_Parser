"""Assemble + store the daily digest, write digest_cards, build the Telegram text
(plan steps 13 + 16).
"""
from __future__ import annotations

import datetime as dt

import psycopg
from psycopg.types.json import Json

from . import db


def store(conn: psycopg.Connection, run_date: dt.date, mode: str, *, broad: list[dict],
          niche: list[dict], rising: list[dict], bursts: list[dict],
          clusters: list[dict], papers_scanned: int) -> None:
    db.execute(
        conn,
        """
        INSERT INTO digests (run_date, mode, broad_ranked, niche_papers, rising_terms,
                             bursts, clusters_summary, papers_scanned)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_date) DO UPDATE SET
            mode = EXCLUDED.mode,
            broad_ranked = EXCLUDED.broad_ranked,
            niche_papers = EXCLUDED.niche_papers,
            rising_terms = EXCLUDED.rising_terms,
            bursts = EXCLUDED.bursts,
            clusters_summary = EXCLUDED.clusters_summary,
            papers_scanned = EXCLUDED.papers_scanned,
            created_at = now()
        """,
        (run_date, mode, Json(broad), Json(niche), Json(rising),
         Json(bursts), Json(clusters), papers_scanned),
    )

    # digest_cards — preserve any outcome already recorded for (run_date, paper_id)
    for section, items in (("broad", broad), ("niche", niche)):
        for i, it in enumerate(items, 1):
            db.execute(
                conn,
                "INSERT INTO digest_cards (run_date, paper_id, section, rank) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (run_date, paper_id, section) DO UPDATE SET rank = EXCLUDED.rank",
                (run_date, it["paper_id"], section, it.get("rank", i)),
            )


def telegram_text(run_date: dt.date, mode: str, *, broad: list[dict], niche: list[dict],
                  rising: list[dict], bursts: list[dict], papers_scanned: int,
                  dashboard_url: str | None, unprompted: list[dict] | None = None) -> str:
    lines = [
        f"Paper Radar — {run_date.isoformat()} ({mode})",
        f"scanned {papers_scanned} papers | broad {len(broad)} | niche {len(niche)}",
        "",
    ]
    if rising:
        lines.append("Rising terms:")
        for r in rising[:8]:
            lines.append(f"  {r['term']}  Δ{r['momentum']:+g}  (now {r['current']})")
        lines.append("")
    if bursts:
        lines.append("Multi-lab bursts (tracked terms): " + ", ".join(
            f"{b['term']} ({b['distinct_groups']} groups)" for b in bursts[:6]))
        lines.append("")
    if unprompted:
        fa = [u for u in unprompted if u.get("tier") == "first_appearance"]
        bu = [u for u in unprompted if u.get("tier") == "bursting"]
        if fa:
            lines.append("🆕 First appearance (genuinely new this week, read even though it's mostly noise):")
            for u in fa[:8]:
                lines.append(f"  {u['term']}  {u['distinct_groups']} groups")
            lines.append("")
        if bu:
            lines.append("📈 Still bursting (not brand new, but rising outside your vocab):")
            for u in bu[:6]:
                lines.append(f"  {u['term']}  {u['distinct_groups']} groups  Δ{u['delta']:+g}")
            lines.append("")
    if broad:
        lines.append("Broad sweep (top 5):")
        for c in broad[:5]:
            tag = c["citation_tag"]["badge"]
            lines.append(f"  {c['rank']}. {c['title']}  {tag}")
            lines.append(f"     {c['why']}")
            lines.append(f"     {c['link'] or ''}")
    else:
        lines.append("Broad sweep: quiet week — nothing cleared the gate.")
    lines.append("")
    if niche:
        lines.append(f"Niche ({sum(1 for n in niche if n['new_since_last_digest'])} new):")
        for n in niche[:6]:
            flag = " *new*" if n["new_since_last_digest"] else ""
            lines.append(f"  - {n['title']}{flag}")
            lines.append(f"    {n['link'] or ''}")
    if dashboard_url:
        lines += ["", f"Full digest: {dashboard_url}"]
    return "\n".join(lines)
