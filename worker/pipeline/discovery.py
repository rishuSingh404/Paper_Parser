"""Corpus-wide detection of terms NOT in `vocab` yet — two tiers, deliberately
kept separate rather than merged into one ranked list.

The gap this closes: `momentum.bursts()` only watches terms already in vocab.
A technique doesn't get a signal at all under that scheme until *after*
someone has already named it and added it — which is backwards for the thing
this is actually meant to catch. Rishu's own description of the target case:
he noticed JEPA ~5 months before MedJEPA-Critic, when there were only 2-3
papers on it anywhere — not when it was already "trending" by any statistical
measure. No algorithm can tell signal from noise that early better than a
person can (that IS what happened with JEPA — he read it and made the call,
nothing surfaced it for him). What this can do is mechanical: guarantee the
list of "things that didn't exist in the tracked corpus before and just
appeared this week, from more than one group independently" reliably reaches
him every week, short enough to actually read. That's the two tiers below.

  1. FIRST APPEARANCE (surfaced first, this is the actual target signal) —
     a term with ZERO occurrences anywhere in the lookback window before this
     week, appearing now in >= MIN_FIRST_APPEARANCE_GROUPS distinct
     first-author groups. "Genuinely new, and more than one lab is already
     independently using this phrase" — the closest mechanical proxy for
     "this is early enough that 2-3 papers exist."
  2. STILL BURSTING (secondary) — a term with some prior presence whose
     current-week count is rising well above its own recent baseline. Useful,
     but it's a different, later-stage signal than #1 and must not be mixed
     into the same ranked list — that's what buried the original version of
     this feature in noise.

Deterministic, no LLM: bigram extraction + momentum-style deltas, same
building blocks used elsewhere, applied to the whole broad-track corpus
instead of a fixed term list. Computed ad hoc each run (most candidates
are noise and don't deserve a permanent row); only the surfaced top-N per
tier land in `discovery_bursts`.

**Bigrams alone structurally cannot catch JEPA.** A single-word acronym never
forms a stable two-word key — "JEPA" fragments across "jepa architecture",
"novel jepa", "using jepa", each a different bigram, none individually
crossing the count/group thresholds no matter how many papers use the term.
This was bigram-only for a while and would have missed the exact case it was
built to catch. Fixed: also extract acronym/coinage-shaped tokens (ALL-CAPS
or CamelCase — "JEPA", "LoRA", "MedJEPA") from the original-case text via
textutil.extract_acronym_candidates(), merged into the same term set bigrams
feed into, so a single-word coinage gets tracked through the identical
first_appearance/bursting logic as a multi-word phrase.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import psycopg

from .. import db
from ..isoweek import iso_week, recent_weeks
from ..textutil import extract_acronym_candidates, extract_bigrams

DEFAULT_LOOKBACK_WEEKS = 8
MIN_FIRST_APPEARANCE_COUNT = 2   # Rishu's own calibration point: "2-3 papers"
MIN_FIRST_APPEARANCE_GROUPS = 2  # independent groups, not one lab's own series
MIN_BURST_COUNT = 3
MIN_BURST_GROUPS = 2
TOP_N_PER_TIER = 15


def scan_corpus_bursts(
    conn: psycopg.Connection,
    current_week: str | None = None,
    *,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    top_n_per_tier: int = TOP_N_PER_TIER,
) -> list[dict]:
    current_week = current_week or iso_week(dt.date.today())
    weeks = recent_weeks(current_week, lookback_weeks) + [current_week]

    rows = db.q(
        conn,
        """
        SELECT title, abstract, first_author_group,
               to_char(COALESCE(announce_date, first_seen_at::date), 'IYYY"-W"IW') AS wk
        FROM papers
        WHERE NOT muted AND NOT is_survey
          AND to_char(COALESCE(announce_date, first_seen_at::date), 'IYYY"-W"IW') = ANY(%s)
        """,
        (weeks,),
    )
    if not rows:
        return []

    tracked = {r["term"] for r in db.q(conn, "SELECT term FROM vocab")}

    # term -> week -> set(first_author_group)  (a set, so multiple mentions by
    # the same group in one week only count once toward distinct_groups)
    by_term: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in rows:
        if not r["wk"]:
            continue
        raw_text = f"{r['title']} {r['abstract'] or ''}"
        # bigrams catch multi-word phrases ("world model"); acronym candidates
        # catch single-word coinages a bigram scan structurally can't
        # ("JEPA") — see extract_acronym_candidates() docstring.
        grams = set(extract_bigrams(raw_text)) | set(extract_acronym_candidates(raw_text))
        group = r["first_author_group"] or f"anon:{r['title'][:40]}"
        for g in grams:
            if g in tracked:
                continue
            by_term[g][r["wk"]].add(group)

    baseline_weeks = weeks[:-1]
    first_appearance, bursting = [], []
    for term, wk_groups in by_term.items():
        current_groups = wk_groups.get(current_week, set())
        current_count = len(current_groups)
        baseline_counts = [len(wk_groups.get(w, set())) for w in baseline_weeks]
        baseline = sum(baseline_counts) / len(baseline_counts) if baseline_counts else 0.0
        is_first_appearance = all(c == 0 for c in baseline_counts) and bool(baseline_counts)

        if is_first_appearance:
            if current_count < MIN_FIRST_APPEARANCE_COUNT:
                continue
            first_appearance.append({
                "term": term, "tier": "first_appearance", "current": current_count,
                "baseline": 0.0, "delta": float(current_count), "distinct_groups": current_count,
            })
        else:
            if current_count < MIN_BURST_COUNT or current_count < MIN_BURST_GROUPS:
                continue
            delta = current_count - baseline
            if delta <= 0:
                continue  # not rising, just persistently common
            bursting.append({
                "term": term, "tier": "bursting", "current": current_count,
                "baseline": round(baseline, 2), "delta": round(delta, 2),
                "distinct_groups": current_count,
            })

    first_appearance.sort(key=lambda c: -c["current"])
    bursting.sort(key=lambda c: -c["delta"])
    top = first_appearance[:top_n_per_tier] + bursting[:top_n_per_tier]

    run_date = dt.date.today()
    db.execute(conn, "DELETE FROM discovery_bursts WHERE run_date = %s", (run_date,))
    for c in top:
        db.execute(
            conn,
            "INSERT INTO discovery_bursts (run_date, term, tier, current_count, baseline_count, delta, distinct_groups) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (run_date, c["term"], c["tier"], c["current"], c["baseline"], c["delta"], c["distinct_groups"]),
        )
    return top
