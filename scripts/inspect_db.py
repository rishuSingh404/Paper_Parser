#!/usr/bin/env python3
"""Human-readable snapshot of what Paper Radar has pulled and computed.

    DATABASE_URL=... python scripts/inspect_db.py
    DATABASE_URL=... python scripts/inspect_db.py --papers 40

Answers "what is it pulling / is it working" without opening psql.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker import db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--papers", type=int, default=20)
    args = ap.parse_args()

    with db.connect() as conn:
        tot = db.q1(conn, "SELECT count(*) n FROM papers")["n"]
        print(f"\n=== papers: {tot} total ===")
        for r in db.q(conn, "SELECT source, count(*) n FROM paper_sources GROUP BY source ORDER BY n DESC"):
            print(f"  {r['source']:<12} {r['n']}")

        print(f"\n=== {args.papers} most recently seen ===")
        for r in db.q(conn,
                      "SELECT p.title, p.announce_date, array_agg(ps.source) srcs, p.abstract_missing "
                      "FROM papers p JOIN paper_sources ps USING (paper_id) "
                      "GROUP BY p.paper_id, p.title, p.announce_date, p.abstract_missing, p.first_seen_at "
                      "ORDER BY p.first_seen_at DESC LIMIT %s", (args.papers,)):
            miss = " [no-abstract]" if r["abstract_missing"] else ""
            print(f"  {str(r['announce_date'] or '?'):<12} {','.join(sorted(r['srcs'])):<22} {r['title'][:90]}{miss}")

        print("\n=== term_counts: top current-week ===")
        rows = db.q(conn,
                    "SELECT term, iso_week, count, distinct_authors FROM term_counts "
                    "WHERE iso_week = (SELECT max(iso_week) FROM term_counts) "
                    "ORDER BY count DESC LIMIT 20")
        for r in rows:
            print(f"  {r['iso_week']}  {r['term']:<34} papers={r['count']:<4} groups={r['distinct_authors']}")

        d = db.q1(conn, "SELECT * FROM digests ORDER BY run_date DESC LIMIT 1")
        if d:
            print(f"\n=== latest digest {d['run_date']} ({d['mode']}), scanned {d['papers_scanned']} ===")
            print("  rising terms:")
            for t in (d["rising_terms"] or [])[:8]:
                print(f"    {t['term']:<32} Δ{t['momentum']:+g}  now={t['current']}")
            print(f"  broad cards: {len(d['broad_ranked'] or [])}")
            for c in (d["broad_ranked"] or [])[:8]:
                print(f"    {c['rank']}. [{c['citation_tag']['badge']}] {c['title'][:80]}")
                print(f"       why: {c['why']}")
            print(f"  niche cards: {len(d['niche_papers'] or [])} "
                  f"({sum(1 for n in (d['niche_papers'] or []) if n['new_since_last_digest'])} new)")
            for n in (d["niche_papers"] or [])[:8]:
                print(f"    - {n['title'][:88]}{' *new*' if n['new_since_last_digest'] else ''}")
        else:
            print("\n=== no digest yet — run `python -m worker.run` ===")

        r = db.q1(conn, "SELECT id, run_date, kind, status, started_at, finished_at, stats "
                        "FROM run_log ORDER BY id DESC LIMIT 1")
        if r:
            print(f"\n=== last run_log #{r['id']} {r['kind']} {r['status']} "
                  f"({r['started_at']} -> {r['finished_at']}) ===")
            for k, v in (r["stats"] or {}).items():
                print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
