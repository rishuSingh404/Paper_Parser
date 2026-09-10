# Paper Radar

A personal research-paper discovery + momentum-tracking system for one researcher.
It casts a wide net across open scholarly APIs, dedupes, enriches with
citation/reference data, computes **deterministic** trend signals (no LLM in the
loop), and surfaces everything on a live dashboard + Telegram digest so the
researcher does their own analysis efficiently.

Full design: [`docs/PLAN.md`](docs/PLAN.md) (mirror of the approved plan).

## Status — Phase 0 (skeleton + observability)

| Area | State |
|---|---|
| Postgres schema (`db/schema.sql`) + decided seed config (`db/seed_config.sql`) | ✅ |
| Worker: config load, DB helpers, arXiv advisory lock, run/api logging | ✅ |
| Ingest: `RawPaper`, cross-source `upsert_paper`, arXiv client (rate-limited + backoff) | ✅ |
| On-demand 6-month term backfill (web service `/internal/backfill-term`) | ✅ |
| Daily pipeline steps 0–3 (drain backfills → fetch arXiv → upsert → recompute `term_counts`) | ✅ |
| Bootstrap job (`python -m worker.backfill`) — arXiv history sweep + baselines | ✅ |
| `render.yaml` (cron + web service + one-off job + Postgres) | ✅ |
| Multi-source ingest (HF/OpenReview/bioRxiv/Crossref/OpenAlex/S2/CORE/DBLP/EuropePMC) | ⬜ Phase 1 |
| Enrichment: citations, reference graph, abstract resolution, embeddings | ⬜ Phase 2 |
| Scoring: momentum/bursts/co-citation/recall gate/ranking/clustering | ⬜ Phase 4 |
| Digest UX, feedback, precision@k, config panel + version history | ⬜ Phase 5 |
| Next.js dashboard (Vercel) | ⬜ Phase 5 |

## Architecture

```
Render                                          Vercel
──────────────────────────────────────          ─────────────────────
cron  : python -m worker.run   (daily)          Next.js dashboard
web   : uvicorn worker.app:app (backfill/run)   /api/state  (live)
job   : python -m worker.backfill (once)        /api/config (token-gated)
                     │                                   │
                     └────────── Postgres ───────────────┘
```

All arXiv access from any worker component is serialized through one Postgres
advisory lock (`worker/arxiv_lock.py`).

## Local dev

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r worker/requirements.txt

cp .env.example .env          # fill DATABASE_URL at minimum
psql "$DATABASE_URL" -f db/schema.sql
psql "$DATABASE_URL" -f db/seed_config.sql

python -m worker.run          # one daily run
uvicorn worker.app:app --reload   # the web service
```

## Deploy

- **Worker + DB**: `render.yaml` blueprint. Read the instance-tier box at the top
  of that file — the daily cron's `plan` is a placeholder until Phase 2 profiles
  the embedding model's real RSS.
- **Dashboard**: Vercel (Phase 5).

## Attribution

All commits are authored by **Rishu Kumar Singh**. Remote:
`github.com/rishuSingh404/Paper_Parser`.
