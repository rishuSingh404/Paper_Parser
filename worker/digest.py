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
          clusters: list[dict], papers_scanned: int,
          cross_domain: list[dict] | None = None) -> None:
    cross_domain = cross_domain or []
    db.execute(
        conn,
        """
        INSERT INTO digests (run_date, mode, broad_ranked, cross_domain, niche_papers,
                             rising_terms, bursts, clusters_summary, papers_scanned)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_date) DO UPDATE SET
            mode = EXCLUDED.mode,
            broad_ranked = EXCLUDED.broad_ranked,
            cross_domain = EXCLUDED.cross_domain,
            niche_papers = EXCLUDED.niche_papers,
            rising_terms = EXCLUDED.rising_terms,
            bursts = EXCLUDED.bursts,
            clusters_summary = EXCLUDED.clusters_summary,
            papers_scanned = EXCLUDED.papers_scanned,
            created_at = now()
        """,
        (run_date, mode, Json(broad), Json(cross_domain), Json(niche), Json(rising),
         Json(bursts), Json(clusters), papers_scanned),
    )

    # digest_cards — preserve any outcome already recorded for (run_date, paper_id)
    for section, items in (("broad", broad), ("niche", niche), ("cross_domain", cross_domain)):
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
                  dashboard_url: str | None, unprompted: list[dict] | None = None,
                  cross_domain: list[dict] | None = None) -> str:
    cross_domain = cross_domain or []
    lines = [
        f"Paper Radar — {run_date.isoformat()} ({mode})",
        f"scanned {papers_scanned} papers | new-field {len(cross_domain)} | in-field {len(broad)} | niche {len(niche)}",
        "",
    ]
    # "New field popping up" goes first, on purpose — two sub-parts, names then
    # papers, same order as the dashboard. This is the MedJEPA-Critic-shaped
    # catch: jepa popping with 2-3 papers, 5 months before any hallucination
    # paper touched it. "Papers in your field" (below) you'd have found anyway.
    if unprompted or cross_domain:
        lines.append("🌱 NEW FIELD POPPING UP (not your field — read this one):")
        fa = [u for u in (unprompted or []) if u.get("tier") == "first_appearance"]
        bu = [u for u in (unprompted or []) if u.get("tier") == "bursting"]
        if fa:
            lines.append("  🆕 by name, first appearance (mostly noise, that's expected):")
            for u in fa[:8]:
                lines.append(f"     {u['term']}  {u['distinct_groups']} groups")
        if bu:
            lines.append("  📈 by name, still bursting:")
            for u in bu[:6]:
                lines.append(f"     {u['term']}  {u['distinct_groups']} groups  Δ{u['delta']:+g}")
        if cross_domain:
            lines.append("  🏷 by paper, structurally close to an open problem:")
            for c in cross_domain[:6]:
                tag = (c.get("matched") or [None])[0] or c.get("anchor_label") or "?"
                lines.append(f"     {c['rank']}. [{tag}] {c['title']}")
                lines.append(f"        {c['why']}")
                lines.append(f"        {c['link'] or ''}")
        lines.append("")
    if rising:
        lines.append("Rising terms:")
        for r in rising[:8]:
            lines.append(f"  {r['term']}  Δ{r['momentum']:+g}  (now {r['current']})")
        lines.append("")
    if bursts:
        lines.append("Multi-lab bursts (tracked terms): " + ", ".join(
            f"{b['term']} ({b['distinct_groups']} groups)" for b in bursts[:6]))
        lines.append("")
    if broad:
        lines.append("📍 Papers in your field (top 5 — hallucination/safety/security, you'd find these anyway):")
        for c in broad[:5]:
            tag = c["citation_tag"]["badge"]
            lines.append(f"  {c['rank']}. {c['title']}  {tag}")
            lines.append(f"     {c['why']}")
            lines.append(f"     {c['link'] or ''}")
    else:
        lines.append("📍 Papers in your field: quiet week — nothing cleared the gate.")
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
