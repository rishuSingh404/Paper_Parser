# Paper Radar

A personal research-paper discovery + momentum-tracking system for one researcher.
It casts a wide net across open scholarly APIs, dedupes across sources, enriches
with citation/reference data, computes **deterministic** trend signals (no LLM in
the loop), and surfaces everything on a live dashboard + Telegram digest so the
researcher does their own analysis efficiently.

Full design: [`docs/PLAN.md`](docs/PLAN.md) · setup & testing: [`docs/SETUP.md`](docs/SETUP.md)

## What it does

- **Ingest** — arXiv (niche queries + broad category sweep) plus Hugging Face
  Daily Papers, OpenAlex, Crossref, Semantic Scholar, CORE, DBLP, Europe PMC,
  bioRxiv/medRxiv, OpenReview. Each source is an isolated module toggled by
  `config.sources`; the system still produces a digest from arXiv alone.
- **Dedupe** — DOI → versionless arXiv id → title/author/year hash; one `papers`
  row, many `paper_sources`.
- **Two distinct "rising" signals** — term **mention momentum** (this ISO week's
  distinct-paper count vs the term's own trailing-6-week mean) and paper
  **citation velocity** (30-day delta, shown as a lagging 🔥/📈/⚪ tag, never fed
  into the rank). Plus **multi-lab burst** (a term from K distinct first-author
  groups in 3 weeks).
- **Broad-track scoring** — self-hosted embedding similarity to open-problem
  statements + seed papers + a liked-paper centroid, word-boundary lexical
  matching (`\bterm\b`, not substring), OpenAlex concept overlap, local
  co-citation velocity, HF upvotes. **Threshold-based recall gate**, not top-N;
  every component is stored separately and shown on the card with a "why" line.
- **Self-evolving vocab** — grows from 👍 feedback (bigrams), decays each run,
  prunes learned terms that go quiet. Seed terms never pruned.
- **Emergent clusters** — monthly HDBSCAN + c-TF-IDF labels, matched to the
  previous month by member-id Jaccard so `cluster_key` continuity is real.
- **On-demand backfill** — adding a keyword/query from the dashboard triggers an
  immediate 6-month arXiv backfill, bucketed by each paper's real publication
  week so an established term doesn't fake a momentum spike.
- **Measurement** — per-card 👍/👎/ignored outcomes, weekly precision@10,
  lead-time audit against `eval_labels`.

## Layout

```
db/schema.sql          full schema
db/seed_config.sql     the decided cold-start config (project-derived seed_vocab)
worker/                Python: ingest, enrich, pipeline, digest, run/backfill, FastAPI app
  ingest/              one module per source + registry + on-demand term_backfill
  enrich/              abstracts, citations + reference graph, embeddings, concept/repo tags
  pipeline/            vocab, momentum + bursts, score, niche, cluster, suggestions, citation tiers
dashboard/             Next.js (App Router): /api/{state,config,feedback,runs} + one page
scripts/               inspect_db.py (what did it pull?), e2e_offline.py (deterministic test)
render.yaml            worker blueprint (cron + web service + one-off job + Postgres)
```

## Quick start (local)

```bash
createdb paper_radar
export DATABASE_URL="postgresql://localhost/paper_radar"
psql "$DATABASE_URL" -f db/schema.sql
psql "$DATABASE_URL" -f db/seed_config.sql

python3 -m venv .venv && . .venv/bin/activate
pip install -r worker/requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

python -m worker.backfill        # 12-16 weeks of history so momentum works day 1
python -m worker.run             # one daily run
python scripts/inspect_db.py     # <- what it pulled + the latest digest

cd dashboard && npm install && npm run build && npm start   # http://localhost:3000
```

Deploy: worker → Render (`render.yaml`), dashboard → Vercel. See
[`docs/SETUP.md`](docs/SETUP.md) for env vars, the embedding-tier decision, and
the verification checklist.

## Attribution

All commits authored by **Rishu Kumar Singh**. Remote:
`github.com/rishuSingh404/Paper_Parser`.
