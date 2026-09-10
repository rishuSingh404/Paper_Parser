#!/usr/bin/env python3
"""Offline end-to-end test of the DETERMINISTIC pipeline (no network).

Seeds ~30 synthetic papers spanning 8 ISO weeks, then runs term_counts ->
momentum -> bursts -> score -> niche -> digest and prints the result. Proves the
scoring/digest logic independent of the live fetch.

    DATABASE_URL=... python scripts/e2e_offline.py
"""
from __future__ import annotations

import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker import config_store, db, digest  # noqa: E402
from worker.identity import canonical_key  # noqa: E402
from worker.ingest.base import RawPaper, upsert_paper  # noqa: E402
from worker.isoweek import current_iso_week, week_monday, iso_week  # noqa: E402
from worker.pipeline import momentum, niche, score  # noqa: E402
from worker.pipeline import terms as term_counts  # noqa: E402

random.seed(7)

TITLES = [
    ("Decorrelation bounds for latent verifiers", "We show a verifier's reliability follows from decorrelation from the generator's errors, not raw accuracy.", ["Ada Lovelace"]),
    ("A latent critic for medical report grounding", "Our latent critic scores grounding verification against a raw signal grounding target.", ["Bo Zhang"]),
    ("Necessity ablation for claim checking", "We verify each claim by necessity ablation, a causal intervention on the evidence.", ["Cara Diaz"]),
    ("Repair policy under interacting errors", "A repair policy that fixes interacting error sources jointly; a neurosymbolic entailment check guards each edit.", ["Dev Rao"]),
    ("Verbalized uncertainty is miscalibrated in VLMs", "Stated confidence does not track reliability; we measure verbalized uncertainty drift.", ["Eun Kim"]),
    ("World models for radiograph anticipation", "A world model predicts latent state; we add latent steering for control.", ["Fei Long"]),
    ("Process reward models for CoT faithfulness", "A process reward model improves chain-of-thought faithfulness on clinical QA.", ["Gita Bose"]),
    ("Signal grounding without retrieval", "Signal grounding checks a generated claim directly against the waveform.", ["Hal Ives"]),
    ("Contrastive decorrelation objectives", "We combine a contrastive loss with an explicit decorrelation penalty between critic and generator.", ["Ivy Park"]),
    ("Error correlation as a failure predictor", "Error correlation between a model and its verifier predicts silent failures.", ["Jon Ali"]),
    ("Interpretability of latent critics", "We probe what a latent critic attends to during grounding verification.", ["Kim User"]),
    ("Neurosymbolic repair for structured outputs", "A neurosymbolic repair policy corrects tables with an entailment oracle.", ["Lee Wong"]),
    ("Uncertainty quantification for report generation", "Uncertainty quantification and calibration for medical vision-language model outputs.", ["Mira Sen"]),
    ("Causal probes for hallucination detection", "We do hallucination detection with causal probes over the latent state.", ["Nao Ito"]),
    ("Latent steering beats prompt tuning", "Latent steering of a world model outperforms prompt tuning for controllable generation.", ["Ola Ben"]),
]


def _mk(title, abstract, authors, day):
    return RawPaper(
        source="arxiv",
        source_id=f"test.{abs(hash(title)) % 99999:05d}",
        arxiv_id=f"2509.{abs(hash(title)) % 99999:05d}",
        title=title,
        abstract=abstract,
        authors=authors,
        published_date=day,
        announce_date=day,
        url="https://arxiv.org/abs/test",
        categories=["cs.CL", "cs.LG"],
    )


def main() -> int:
    with db.connect() as conn:
        cfg = config_store.load(conn)
        wk = current_iso_week()
        monday = week_monday(wk)

        db.execute(conn, "TRUNCATE papers CASCADE")
        db.execute(conn, "DELETE FROM term_counts")
        db.execute(conn, "DELETE FROM digests")
        db.execute(conn, "DELETE FROM run_markers")

        # spread base papers over the last 8 weeks; pile a few 'decorrelation' /
        # 'latent critic' papers into THIS week from DISTINCT authors -> rising + burst
        n = 0
        for wago in range(8, 0, -1):
            for (t, a, au) in random.sample(TITLES, 3):
                d = monday - dt.timedelta(weeks=wago) + dt.timedelta(days=random.randint(0, 4))
                upsert_paper(conn, _mk(f"[{wago}w] {t}", a, au, d))
                n += 1
        for i, (t, a, au) in enumerate(TITLES):
            d = monday + dt.timedelta(days=random.randint(0, 3))
            upsert_paper(conn, _mk(f"[now] {t}", a, [au[0], f"Coauthor {i}"], d))
            n += 1

        # one older paper with a citation history -> exercises the lagging tag
        old = _mk("Foundational decorrelation critic", "The original decorrelation critic paper.", ["Zero One"],
                  dt.date.today() - dt.timedelta(days=120))
        pid = upsert_paper(conn, old)
        db.execute(conn, "UPDATE papers SET first_seen_at = now() - interval '100 days' WHERE paper_id = %s", (pid,))
        db.execute(conn, "INSERT INTO citation_snapshots (paper_id, snapshot_date, citation_count) VALUES (%s, %s, %s)",
                   (pid, dt.date.today() - dt.timedelta(days=35), 4))
        db.execute(conn, "INSERT INTO citation_snapshots (paper_id, snapshot_date, citation_count) VALUES (%s, %s, %s)",
                   (pid, dt.date.today(), 13))

        tracked = sorted({*(cfg["seed_vocab"].keys()),
                          *(r["term"] for r in db.q(conn, "SELECT term FROM vocab"))})
        for term in tracked:
            term_counts.recompute_term(conn, term)
        term_counts.recompute_category_volume(conn)
        term_counts.refresh_last_seen(conn)

        rising = momentum.rising_terms(conn, tracked, wk)
        bursts = momentum.bursts(conn, tracked, wk, cfg["burst_min_groups"])
        broad = score.score_broad(conn, cfg, wk, tracked)
        nf = niche.niche_feed(conn, cfg)
        digest.store(conn, dt.date.today(), "calibration", broad=broad["cards"], niche=nf,
                     rising=rising, bursts=bursts, clusters=[], papers_scanned=n)

        print(f"\nseeded {n} papers, {len(tracked)} tracked terms, week {wk}\n")
        print("RISING TERMS:")
        for r in rising[:8]:
            print(f"  {r['term']:<30} current={r['current']:<3} baseline={r['baseline']:<6} Δ={r['momentum']:+g}")
        print("\nBURSTS:", [f"{b['term']}({b['distinct_groups']})" for b in bursts] or "none")
        print(f"\nBROAD SCORE: {broad['surfaced']}/{broad['universe']} surfaced, truncated_at={broad['truncated_at']}")
        for c in broad["cards"][:8]:
            print(f"  #{c['rank']} [{c['citation_tag']['badge']} {c['citation_tag']['delta']}] {c['title']}")
            print(f"       why: {c['why']}")
            print(f"       signals: {c['signals']}")
        print(f"\nNICHE: {len(nf)} papers ({sum(1 for x in nf if x['new_since_last_digest'])} new)")
        for x in nf[:6]:
            print(f"  - {x['title']}   matched={x['matched']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
