# Paper Radar — setup & testing

Three pieces: **Postgres** (shared), a **worker** (3 Render components, one
codebase), a **Next.js dashboard** (Vercel). Everything runs on free tiers except
the daily cron, which needs a Starter instance for the embedding model.

---

## 0. Prerequisites / accounts

| Thing | Needed for | Free? |
|---|---|---|
| Postgres 16 (Neon or Render Postgres) | everything | yes |
| Telegram bot token + chat id | digest delivery | yes ([@BotFather](https://t.me/botfather)) |
| Crossref `mailto` (any email) | polite pool | yes |
| OpenAlex API key | citation counts + reference graph | yes ([openalex.org](https://openalex.org/)) |
| CORE API key | the CORE source (optional) | yes ([core.ac.uk](https://core.ac.uk/services/api)) |
| Semantic Scholar key | S2 source (optional, works keyless with 429s) | apply w/ university email |
| Render account | worker | yes |
| Vercel account | dashboard | Pro/Fluid for the synchronous keyword backfill; Hobby works enqueue-only |

The system runs with **only `DATABASE_URL`** — every enrichment source degrades to
a logged skip.

---

## 1. Database

```bash
createdb paper_radar          # or provision Neon / Render Postgres
export DATABASE_URL="postgresql://USER:PASS@HOST:5432/paper_radar"

psql "$DATABASE_URL" -f db/schema.sql
psql "$DATABASE_URL" -f db/seed_config.sql      # the decided cold-start config
```

`db/seed_config.sql` is idempotent (`ON CONFLICT (id) DO NOTHING`). Edit the
`seed_papers` array first if you want Rishu's own arXiv ids as embedding anchors
from day 1 — or add them later from the dashboard.

---

## 2. Worker — local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r worker/requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

cp .env.example .env    # fill DATABASE_URL (+ optional keys)
set -a; source .env; set +a

# (a) bootstrap: 12-16 weeks of history so momentum works on day 1
python -m worker.backfill

# (b) one daily run
python -m worker.run

# (c) see what it pulled / whether it's working
python scripts/inspect_db.py --papers 40
```

`worker.run` and `worker.backfill` are **idempotent** — safe to re-run the same
day. `run_markers` guards decay; `term_counts` is recomputed, not incremented.

Without `numpy` / `scikit-learn` / `hdbscan` / `sentence-transformers` the
embedding + clustering steps log a skip and scoring falls back to lexical +
co-citation + HF signals only. Install `worker/requirements.txt` (not
`-core.txt`) to enable them.

---

## 3. Worker — Render (`render.yaml` blueprint)

`render.yaml` provisions Postgres + three services:

| Service | Command | Notes |
|---|---|---|
| `paper-radar-daily` (cron `0 3 * * *`) | `python -m worker.run` | **Set `plan` from a profiled RSS** — see the box at the top of `render.yaml`. Default `all-MiniLM-L6-v2` fits Starter ($7). |
| `paper-radar-worker-api` (web) | `uvicorn worker.app:app` | model-free, Starter; serves `/internal/backfill-term` + `/internal/run` |
| `paper-radar-bootstrap` (cron, never fires) | `python -m worker.backfill` | trigger manually once from the Render dashboard |

Set the env-var-group secrets in the Render dashboard: `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `OPENALEX_API_KEY`, `CORE_API_KEY`, `CROSSREF_MAILTO`,
`SEMANTIC_SCHOLAR_API_KEY`. `INTERNAL_SHARED_SECRET` is auto-generated and shared.

**First few runs manually** (Render dashboard → "Trigger Run") and read
`scripts/inspect_db.py` / the `run_log` table before trusting the schedule.

---

## 4. Dashboard — Vercel

```bash
cd dashboard
npm install
npm run build && npm start      # local check on :3000
```

Vercel project env:

| Var | Value |
|---|---|
| `DATABASE_URL` | same Postgres, a read/write role |
| `DASHBOARD_EDIT_TOKEN` | a passphrase only you know (gates `POST /api/config`) |
| `WORKER_INTERNAL_URL` | `https://paper-radar-worker-api.onrender.com` |
| `INTERNAL_SHARED_SECRET` | copy from the Render env-var group |

`app/api/config/route.ts` is declared `maxDuration: 300` in `vercel.json` for the
synchronous keyword backfill — needs Vercel **Pro/Fluid**. On Hobby it still
works: the `term_backfills` row stays `pending` and the next daily run drains it
(row → panel fills on the next refresh).

`/api/state` is `force-dynamic` — the page fetches it fresh on every load, no ISR.

---

## 5. Verifying it works

```bash
DATABASE_URL=... python scripts/inspect_db.py
```

shows: papers per source, the 40 most-recently-seen papers with their source
tags, current-week `term_counts`, the latest digest (rising terms, broad cards
with the "why" line, niche cards), and the last `run_log` stats.

Checklist (mirrors `docs/PLAN.md` "Definition of done"):

- [ ] `inspect_db.py` lists papers from ≥3 sources; `term_counts` has rows.
- [ ] After `worker.backfill`, `term_counts` spans ≥6 weeks (`SELECT DISTINCT iso_week FROM term_counts ORDER BY 1`).
- [ ] Run `worker.run` twice the same day → `papers`, `term_counts`, `digests` unchanged the second time.
- [ ] Dashboard loads, `/api/state` returns JSON; change a DB value, refresh, see it (no redeploy).
- [ ] A paper ≥14 days old shows a citation tier + 30-day number; a young one shows "no data", not "⚪ 0".
- [ ] Word-boundary: `SELECT (title||' '||abstract) ~* '\yground\y' FROM papers ...` — no "foreground"/"background" hits.
- [ ] Add a keyword in the config panel → "Just added — last 6 months" panel fills; those papers land in `term_counts` under their real announce week (an established term shows flat momentum, not a spike).
- [ ] Wrong `x-edit-token` → 401; malformed body → 400.
- [ ] Telegram delivers on a manual `worker.run` (or `POST /internal/run`).

---

## 6. Turning sources on/off

`config.sources` is a JSON map. From the dashboard config panel isn't wired for
per-source toggles yet (Phase 5 polish) — do it in SQL for now:

```sql
UPDATE config SET sources = jsonb_set(sources, '{openreview,enabled}', 'true');
UPDATE config SET sources = jsonb_set(sources, '{openreview,venues}', '["ICLR.cc/2026/Conference"]');
```
