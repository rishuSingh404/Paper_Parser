# Paper Radar — revised build plan (v1, "full send", no LLM)

## Context

**Why this exists.** Rishu (PhD, medical vision-language model safety; also
JEPA/world-model medical imaging) currently learns "what's trending" from his
advisor and ad-hoc social feeds. That doesn't scale and structurally can't
surface cross-domain transfers. Paper Radar is a scheduled system that casts a
wide net across open scholarly APIs, dedupes, enriches with citation/reference
data, computes **deterministic** trend signals, and presents everything on a live
dashboard + Telegram digest so Rishu can do his own analysis efficiently.

**What changed from `PAPER_RADAR_SPEC (1).md`.** The spec is sound; this plan
keeps its architecture, decay/prune ordering, two-distinct-signals rule, editable
config with history, and git-identity section. Decisions from Rishu that reshape
it:

1. **Full send** — all deterministic robustness folded into v1 (backfill + shadow
   mode, dedup + mute state, multi-source ingest, co-citation velocity, multi-lab
   burst, clustering, measurement/audit, run log + alerts).
2. **No LLM anywhere.** No per-paper relevance pass, no LLM cluster labels
   (use c-TF-IDF top terms + representative titles), no LLM query proposals
   (deterministic bigram-frequency suggestion list). The system fetches and
   organizes; Rishu judges.
3. **Multiple sources for breadth** (the answer to "I want Google Scholar"):
   arXiv + Hugging Face Daily Papers + OpenReview + medRxiv/bioRxiv + Semantic
   Scholar + OpenAlex + Crossref + CORE + DBLP + Europe PMC. **No Google Scholar
   scraping** (no API, bans server IPs in minutes, risks Rishu's Google account).
   Scholar email alerts stay a manual side-channel.
4. **Embeddings self-hosted on the worker** (SPECTER2, MiniLM fallback).
5. **Free API tiers only** — no paid API spend. Graceful degradation when a
   source is rate-limited.
6. **Seed config is decided, not placeholder** (see *Cold-start config* below).
   `seed_vocab`, `broad_categories`, and `open_problems` are derived concretely
   from Rishu's active projects — MedJEPA-Critic, MedFaith, SigPRM/DGT, TRIAGE,
   and related tracked work (Latent Critic, LRS, Agent-BRACE). JEPA/world-models
   is one thing the broad sweep should catch, not a target to hard-tune: the
   *machinery* (category sweep, embedding overlap) stays domain-agnostic, the
   *seeds* are project-specific.
7. **Adding a keyword/query triggers an immediate 6-month arXiv backfill**
   (see *Dashboard*), bucketed into each paper's real publication week so an
   established term doesn't fake a momentum spike.

**Intended outcome.** A system robust enough that a paper blowing up in Rishu's
areas is something Paper Radar surfaced early — measured (lead-time audit), not
assumed — so he stops depending on his advisor for trend awareness.

---

## Architecture

Two logical services (worker, dashboard) + one shared Postgres. The worker is
deployed as **three Render components sharing one codebase** (see *Deployment*).

- **Worker** — Python. Three components:
  - **Cron Job** — runs `worker/run.py` on schedule (the daily pipeline); sized
    for the embedding model.
  - **Web Service** (small) — `POST /internal/backfill-term` (dashboard's
    on-demand 6-month keyword backfill) and `POST /internal/run` (out-of-band
    trigger). Shared-secret auth. No embedding model loaded.
  - **one-off Job** — initial 12–16 week bootstrap backfill.
  **All arXiv access from any component is serialized through one Postgres
  advisory lock** (`pg_try_advisory_lock` on a fixed key) so arXiv's
  one-connection / ≥3 s rule is never violated by two components at once; a
  backfill request that can't take the lock enqueues its `term_backfills` row
  and returns "queued — appears within the hour" instead of blocking.
  Pipeline steps: drain pending `term_backfills` → ingest → identity resolution →
  vocab maintenance → term/trend signals → enrichment → scoring → digest →
  Telegram → run log.
- **Dashboard** — Next.js on Vercel. `/api/state` (live, never static),
  `/api/config` (GET public / POST token-gated), `/api/feedback`, `/api/runs`.
- **Postgres** — Neon free tier (has `pgvector`) or Render Postgres. Reachable
  from all components.

Single-platform fallback (Render Web Service for the dashboard) is acceptable but
two-platform is the default.

**Cost note — embedding model & Render sizing (MEASURE before finalizing
`render.yaml`):** `allenai/specter2_base` is BERT-base (~110 M params, ~440 MB
fp32 on disk); loaded with a CPU `torch` runtime the *resident* footprint is
materially higher than the disk number — a rough estimate is 1.2–1.8 GB RSS, but
that is **not** a profiled figure. **Phase 2 task:** profile the actual peak RSS
of `specter2_base` + CPU `torch` + `adapters` doing a real batch embed on the
target container, and record the measured number as a comment in `render.yaml`.
Pick the Cron Job tier from that number:
- ≤ ~450 MB RSS → Render **Starter** (512 MB, ~$7/mo) — where `all-MiniLM-L6-v2`
  (~120 M params, ~300–400 MB RSS) lands.
- ~1–2 GB RSS → Render **Standard** (2 GB, ~$25/mo) — the likely `specter2_base`
  landing spot.

**Default: `all-MiniLM-L6-v2` on Starter ($7/mo).** `embedding_model_version`
lives in `config`, so switching to `specter2_base` + a Standard-tier Cron Job is
a one-line change if Phase 6 measurement shows broad-track precision needs it.
The Web Service and the Vercel dashboard stay on free/Starter tiers regardless
(no model loaded there).

---

## Ingestion — multi-source, each module isolated and individually disable-able

Common interface: `fetch(since, params) -> list[RawPaper]`, where `RawPaper`
normalizes to `{source, source_id, doi?, arxiv_id?, title, abstract, authors[],
published_date, announce_date?, url, categories[], extra{}}`. Per-source enable
flags live in `config.sources`.

| Source | Endpoint / client | Auth | Role | Notes |
|---|---|---|---|---|
| **arXiv** | `export.arxiv.org/api/query` (Atom) | none | primary discovery (niche queries + broad category sweep) | **1 req / 3 s, single connection**, exp backoff from 5 s on 429, ≤5 retries, identifying `User-Agent`. Bucket weeks by **announce date** (OAI-PMH datestamp), not `published`. Collapse `vN`. Stricter 429s since ~Feb 2026 → real queue + checkpointing. |
| **HF Daily Papers** | `GET huggingface.co/api/daily_papers?date=&page=&limit=100` | none | "what ML is discussing today" signal + serendipity | Unofficial, be polite, may change. Gives `arxiv_id`, `upvotes`, `publishedAt`, repo links, comments. Skewed to LLM/generative hype — broad-track only. |
| **OpenReview** | `openreview-py` v2 (`api2.openreview.net`) | none | ~3-month lead on conference trends | **Ingest only public review text + scores + decisions, never identities** (2026 ICLR scraping incident → possible access tightening). Seasonal (Sept–Feb). Isolated, disable-able. Fuzzy title match to arXiv. |
| **medRxiv / bioRxiv** | `api.biorxiv.org/details/[server]/[from/to]/[cursor]/json` | none | medical / bio coverage arXiv misses | 100/page, clean JSON. |
| **Crossref** | `api.crossref.org/works` | none (`mailto` polite pool) | breadth (OA journals/proceedings) + enrichment | `is-referenced-by-count`, `reference` lists, subject/type/date filters. |
| **OpenAlex** | `api.openalex.org` | **free key (now required)** | enrichment-primary + breadth | Stay within free daily usage (~$1/day worth); cache hard; handle 402/quota. `cited_by_count`, `referenced_works` (co-citation graph), `topics`/`concepts`, author IDs. |
| **Semantic Scholar** | Graph API `/paper/batch`, `/paper/{id}/references`, `/recommendations` | unauth pool | supplement | Heavy 429s, exp backoff mandatory. `citationCount`, `influentialCitationCount`, `externalIds`. Optional — system must run without it. |
| **CORE** | `api.core.ac.uk/v3` | free key | main breadth lever for non-arXiv preprints + institutional-repo OA | 300M+ works aggregated. |
| **DBLP** | `dblp.org/search/publ/api?format=json` | none | CS venue discovery by keyword | **Bibliographic only — no abstracts.** Use to discover title+venue+DOI, then resolve abstract via Crossref/OpenAlex; title-only scoring (or hold back) if none resolves. |
| **Europe PMC** | `ebi.ac.uk/europepmc/webservices/rest/search` | none | biomedical incl. preprints | Covers the medical side broadly. |

**Identity resolution / dedup across sources:** canonical key priority = DOI →
versionless arXiv id → fuzzy(normalized title + first-author surname + year).
One `papers` row, many `paper_sources` rows. Collapse arXiv `v1/v2/v3`.

---

## Enrichment

- **Citation counts** — OpenAlex `cited_by_count` → Crossref
  `is-referenced-by-count` → S2 `citationCount`. Snapshot into
  `citation_snapshots`. All nullable.
- **Reference graph** — OpenAlex `referenced_works` → S2 references → Crossref
  `reference`. Populate `citation_edges(citing_paper_id, cited_paper_id,
  discovered_at)`. Powers **local co-citation velocity**: for a tracked paper,
  count citing papers whose `first_seen_at` is within `cocitation_lookback_weeks`.
- **Concept/topic tags** — OpenAlex `topics`/`concepts`; overlap with tracked
  vocab is a supplementary match signal.
- **Repo links** — HF repo field + `github.com` regex over arXiv
  `comments`/abstract. Stored; GitHub star velocity is Phase 7 (needs daily
  snapshots).
- **Embeddings** — self-hosted `allenai/specter2_base` + `allenai/specter2`
  adapter (`transformers` + `adapters`), MiniLM fallback. Embed `title + "\n" +
  abstract` for every broad-track paper. Store in `embeddings(paper_id,
  model_version, vector)`. Cosine via `pgvector`; numpy in-process fallback
  (volume is thousands, not millions).

A usable digest must be producible from **arXiv alone** — every enrichment field
is nullable and the pipeline branches on presence.

---

## Deterministic pipeline (order per run — all steps idempotent)

Guard the whole run and the decay step with a per-`run_date` / per-`iso_week`
marker so a same-day re-run or a half-completed retry does not double-count.

0. **Drain `term_backfills`** — before the normal fetch, complete any
   `pending`/`partial` rows (dashboard-added terms), fully rate-limited, so their
   history is populated before this run scores anything.
1. **Fetch** all enabled sources since their `watermarks` (broad + niche).
2. **Identity resolution + dedup** → upsert `papers` + `paper_sources`. Drop
   `muted` papers from everything downstream.
3. **Vocab maintenance** (spec 4.5 ordering, corrected): `weight *= decay_rate`
   → prune where `is_seed = false AND weight < prune_weight_threshold AND
   (current_iso_week − last_seen_iso_week) >= prune_min_silent_weeks` (real
   calendar-week arithmetic via an explicit `vocab.last_seen_iso_week`, **not**
   "N most recent rows present") → **then** add this run's counts to
   `term_counts` and update `last_seen_iso_week`.
4. **Term matching** — **word-boundary** regex (`\bterm\b`, case-insensitive)
   over `title + " " + abstract`, not substring. Substring matching makes a bare
   `ground` keyword fire inside "foreground segmentation" and "background
   subtraction"; `\bground\b` matches neither, but still matches the standalone
   word in "evaluated against ground truth". (Strict both-boundary: `\bground\b`
   does **not** match "grounded"/"grounding" — stem matching is a deliberate
   non-goal for v1.) Exclude survey/review papers (title regex
   `survey|review|overview|comprehensive` + abstract-length heuristic) from all
   momentum counts.
5. **Momentum** per term = current-week **distinct-paper count** − mean of the
   trailing 6 *present* weeks (missing week = 0). Also store **distinct
   first-author-group count** (normalized surname) and **share of that week's
   category volume** (normalize out arXiv's weekly seasonality + secular growth).
   Self-relative, as spec 4.2.
6. **Multi-lab burst** — for each term, `K` = distinct first-author groups in the
   last 3 weeks; flag if `K >= burst_min_groups` (config, default 3) and up from
   the prior 3-week window. Write `term_bursts`.
7. **Broad-track component signals** (each stored in `signals` for the card
   breakdown — never merged into one opaque number):
   - **Embedding similarity** — max cosine of the paper vector vs
     `{open-problem statement vectors} ∪ {seed-paper vectors} ∪ {liked-paper
     centroid}`. Keep the best-matching anchor for the "why" text.
   - **Lexical** — Σ over matched terms of `vocab.weight × momentum(term)`.
   - **Concept-tag overlap** — `|OpenAlex concepts ∩ tracked vocab|`.
   - **Co-citation velocity** — count from `citation_edges` (step in Enrichment).
   - **HF upvotes + upvote velocity** — if the paper is in `hf_signals`.
8. **Recall gate → candidate set** — include a broad-track paper if it clears
   **any** threshold in `recall_gate_thresholds` (embedding sim / lexical /
   co-citation / HF upvotes / concept overlap). **Threshold-based, not top-N.**
   Hard cap (config, e.g. 60); overflow dropped by composite rank and logged
   `truncated at N`.
9. **Composite display rank** — ordering only, never a gate. Weighted sum of the
   normalized component signals; weights in `config.rank_weights` with a code
   comment stating they are heuristic, not derived. **No token-ratio
   `structural_overlap` term** (embeddings replace it) and **no
   `citation_velocity_bonus`** (citation status is a lagging tag, shown
   separately — it must not bias an "emerging" score toward established papers).
10. **Citation tiering** (spec 4.6, as a **tag**, not a score input) — Hot ≥5 /
    Warming ≥1 / Flat 0 / Not-enough-data (null) from the 30-day
    `citation_snapshots` delta. Show the actual number ("🔥 +7 / 30d").
11. **Niche track** — no trend scoring. Recency sort + soft ordering by embedding
    sim to open problems + dedup + `new_since_last_digest` flag. This track needs
    completeness, not momentum.
12. **Clustering** (weekly + monthly job) — embed last ~30 days of broad-track
    abstracts → HDBSCAN → per-cluster label = top **c-TF-IDF** terms + 3
    representative titles (nearest centroid) → **match each cluster to the
    previous period by ≥0.5 Jaccard on member paper ids** and carry a stable
    `cluster_key` (HDBSCAN's per-run labels are not stable across runs; unmatched
    clusters get a fresh key). Store `clusters`; `prev_size` and "new this
    period" are keyed off `cluster_key`, never the raw run label.
13. **Write `digests`** (upsert on `run_date`): `broad_ranked` (per-paper signal
    breakdown + why text), `niche_papers`, `rising_terms`, `bursts`,
    `clusters_summary`.
14. **Feedback → vocab growth** — for each `liked` paper not yet processed,
    lowercase bigrams of `title + abstract`, drop stopword-containing, keep the
    3 most frequent **not already in vocab**; insert at **weight 0.4** (not 1.0)
    `is_seed = false`, **or** weight 1.0 only if the bigram appears in ≥2 liked
    papers. Cap per-run additions and total learned vocab (~200).
15. **Config suggestions** (deterministic, no auto-apply) — bigrams/terms
    frequent in liked papers but absent from `vocab` *and* from `niche_queries`
    → write `config_suggestions`; dashboard shows them for manual accept.
16. **Telegram digest** — short summary + top items + dashboard link. **Chunk to
    the 4096-char limit**; use plain text or correctly-escaped MarkdownV2.
    Include run timestamp + papers-scanned count.
17. **`run_log`** — per-source fetch counts, API errors/latency (`api_call_log`),
    stage in/out counts, per-paper drop reasons, per-paper signal breakdown,
    vocab actions, truncation events, `config` version used.
18. **Alerting** — run failed / zero papers surfaced / API error rate over
    threshold / no successful run in 26 h → Telegram alert.

---

## Cold-start config (decided — replaces spec section 3's suggested JSON)

**`seed_vocab`** — anchored on the recurring structural moves across Rishu's
active projects, not generic hallucination-detection keywords:

| Term | weight | Source / rationale |
|---|---|---|
| `decorrelation` | 1.0 | MedJEPA-Critic core thesis: a verifier's reliability comes from being decorrelated from the generator's errors, not from raw accuracy. |
| `error correlation` | 1.0 | Same thesis stated the other way — track both surface forms. |
| `latent critic` | 1.0 | MedJEPA-Critic related work already tracked (Latent Critic). |
| `latent steering` | 1.0 | Related tracked work (LRS). |
| `verbalized uncertainty` | 0.9 | Related tracked work (Agent-BRACE); also the "stated confidence ≠ actual reliability" problem. |
| `causal` | 0.8 | MedFaith (ECUT) + SigPRM/DGT necessity-ablation — verification by targeted ablation. |
| `necessity ablation` | 1.0 | The same move in two separate projects (MedFaith ECUT, SigPRM/DGT) — one recurring pattern, tracked as one term. |
| `repair policy` | 1.0 | TRIAGE interaction-aware correction. |
| `neurosymbolic` | 0.9 | TRIAGE entailment-check branch. |
| `signal grounding` | 1.0 | SigPRM/DGT — verifying a claim against a raw signal instead of retrieved text. |

Keep a low-weight tail of subfield terms as seeds so the niche track and the "am I
still in my field" signal don't go blind: `hallucination detection` 0.7,
`grounding verification` 0.8, `process reward model` 0.9, `chain-of-thought
faithfulness` 0.7, `world model` 0.6, `jepa` 0.6. The ten above are the primary
anchors.

**`broad_categories`** — `cs.CL, cs.LG, cs.AI, cs.CV, cs.CR` **+ `eess.SP`
(DGT's signal work) + `stat.ML` (the causal-verification angle across projects)**.
Without the last two, signal-grounding and causal-verification papers outside the
`cs.*` tree are never fetched.

**`open_problems`** — re-derived from the projects (embedding anchors; editable):
1. "A verifier's reliability comes from its errors being decorrelated from the
   generator's, not from its own accuracy — estimating and enforcing that
   decorrelation without ground-truth labels on the latent state."
2. "Verifying whether a specific claim is load-bearing by ablating it and
   checking necessity, rather than by matching it against retrieved text."
3. "Repairing an output when several error sources interact, where fixing one in
   isolation makes another worse."
4. "Grounding a generated claim directly in a raw signal (waveform, image)
   instead of in a retrieved-text proxy."
5. "Detecting when a model's verbalized confidence does not track its actual
   reliability."

**`seed_papers`** — Rishu supplies 5–10 arXiv ids at setup (his own preprints +
Latent Critic / LRS / Agent-BRACE + the canonical necessity-ablation and
neurosymbolic-repair papers) as additional embedding anchors.

---

## Backfill / bootstrap (one-off Render Job, checkpointed)

The first ~8 weeks are the trust-forming window and every raw signal is blind
then. This is a required phase, not optional.

- 12–16 weeks arXiv history for all niche queries + broad categories
  (1 req/3 s → ~1–2 h, resumable).
- HF Daily Papers by `date` (~90 days); medRxiv/bioRxiv, CORE, DBLP, Europe PMC,
  Crossref for the same window (keyword-scoped for the non-category sources).
- OpenAlex enrichment for backfilled papers, **spread across several days** to
  stay in free daily usage; build the initial `citation_edges` graph.
- Embed every backfilled abstract.
- Compute 6-week `term_counts` baselines so momentum works on day 1.
- Two `citation_snapshots` ~2 weeks apart (second lands during shadow mode) so
  the lagging tag has a baseline in 2 weeks, not 10.
- Seed vocab from the decided `config.seed_vocab` (Cold-start config) + terms
  from Rishu's own papers + `config.seed_papers`.
- This one-off bootstrap is separate from the per-term on-demand backfill the
  dashboard triggers later; both write `term_counts` bucketed by real week.
- **Rishu's initial like-pass** over ~20–30 backfilled papers → seeds the
  liked-paper centroid + first `config_suggestions`.
- **1–2 weeks shadow mode**: daily digests generated + delivered, marked
  `CALIBRATION`, rated; tune thresholds/weights via the config panel; watch
  precision@k. Then flip `digests` to live. (Satisfies the spec's "no fully
  unattended first deploy" non-goal.)

---

## Database schema (extends spec section 3)

Keep the spec's tables; modify / add:

- **`papers`** — drop the hard `track` CHECK (a paper can be both tracks); add
  `canonical_key`, `doi`, `announce_date`, `is_survey BOOL`, `muted BOOL`;
  `arxiv_id` now nullable.
- **`paper_sources`** `(paper_id, source, source_id, source_url, raw JSONB,
  first_seen_at)` — many per paper.
- **`citation_snapshots`** — as spec, keyed by `paper_id`.
- **`citation_edges`** `(citing_paper_id, cited_paper_id, discovered_at)`.
- **`embeddings`** `(paper_id, model_version, vector, created_at)` — `pgvector`.
- **`hf_signals`** `(paper_id, snapshot_date, upvotes, comments)`.
- **`vocab`** — add `last_seen_iso_week TEXT`.
- **`term_counts`** — add `distinct_authors INT`, `category_share REAL`; index on
  `iso_week`.
- **`term_bursts`** `(term, iso_week, distinct_groups, prev_distinct_groups)`.
- **`signals`** `(paper_id, run_date, name, value)`.
- **`feedback`** — `verdict` enum → `liked | disliked | muted | saved`.
- **`digest_cards`** `(run_date, paper_id, section, rank, outcome, outcome_at)` —
  precision@k source.
- **`clusters`** `(period, run_cluster_id, cluster_key, label_terms TEXT[],
  member_paper_ids TEXT[], representative_paper_ids TEXT[], size, prev_size)` —
  `run_cluster_id` is HDBSCAN's per-run label (unstable); `cluster_key` is the
  stable id carried forward on ≥0.5 Jaccard overlap of `member_paper_ids` with a
  previous-period cluster, else new. Dashboard continuity uses `cluster_key` only.
- **`config`** — seed with the **decided** values from *Cold-start config*
  (project-derived `seed_vocab`; `broad_categories` incl. `eess.SP` + `stat.ML`;
  re-derived `open_problems`); add `seed_papers JSONB`, `sources JSONB`
  (per-source enable + params), `cocitation_lookback_weeks`, `burst_min_groups`,
  `recall_gate_thresholds JSONB`, `rank_weights JSONB`,
  `embedding_model_version`, `keyword_backfill_months` (default 6).
- **`term_backfills`** `(id, term, kind ['keyword'|'query'], requested_at,
  status ['pending'|'partial'|'done'|'error'], cursor TEXT, stats JSONB)` —
  one row per term/query added from the dashboard; the inline handler sets
  `partial` + `cursor` when it hits its cap, and the next worker run drains it.
- **`config_history`** (full JSON snapshots) and **`config_suggestions`**
  `(id, kind, payload JSONB, rationale TEXT, created_at, status)`.
- **`run_log`** `(id, run_date, started_at, finished_at, status, stats JSONB,
  errors JSONB, config_version)` and **`api_call_log`** `(id, run_id, source,
  endpoint, status, latency_ms, quota_note)`.
- **`eval_labels`** `(paper_id, label, labeled_at)` — regression set + audit.
- **`watermarks`** `(source, last_fetched_at, last_cursor)`.
- Enable `pgvector`; if unavailable, store vectors as `REAL[]` and cosine in
  numpy.

---

## Dashboard (Next.js on Vercel)

- **`/api/state`** — server-side only, fetched fresh every load (no static / no
  ISR / cache ≤ a few seconds). One JSON payload: latest digest, rising terms +
  bursts, broad cards with full signal breakdown + why text + citation tag,
  niche cards, vocab (seed vs learned, visually distinct), clusters, "is it
  working" summary (precision@k trend), staleness info.
- **`/api/config`** — `GET` public; `POST` requires `x-edit-token` ==
  `DASHBOARD_EDIT_TOKEN` via `hmac.compare_digest` + basic rate-limit / lockout.
  On valid POST: snapshot current `config` → `config_history` → validate shape
  (string arrays for `niche_queries`/`broad_categories`/`open_problems`; `{term:
  weight}` for `seed_vocab`; reject **400**, no coercion) → update `config` →
  new `seed_vocab` terms inserted into `vocab` (`is_seed = true`); removed terms
  set `is_seed = false` (**not** deleted). Reject **401** on bad/missing token.
- **On-demand historical backfill when a keyword or query is added** — it must
  not wait for the next scheduled run:
  1. `POST /api/config` writes the config change immediately **and** inserts a
     `term_backfills` row (`status = pending`) per new `seed_vocab` keyword and
     new `niche_queries` entry.
  2. It then calls the worker's `POST /internal/backfill-term` (shared-secret),
     which takes the `arxiv_lock` and runs a **bounded** arXiv query for that
     term over the last `keyword_backfill_months` (default 6), hard-capped
     (~8 requests / ~800 results). If the lock is held by the daily run it does
     not block — it leaves the row `pending` and returns "queued". On success it
     upserts `papers` + `paper_sources` (**dedup** — a paper already fetched by
     the broad sweep just gains the term match) and writes `term_counts`
     **bucketed into each paper's real announce/publication ISO week**, never the
     week the keyword was added. Marks the row `done`, or `partial` + `cursor`
     if capped.
  3. The response returns the fetched papers; the frontend renders them in a
     "Just added: `<term>` — last 6 months" panel, and the rising-terms view now
     has real historical weekly buckets for that term.
  4. Any `partial` rows are finished by the next worker run (step 0 of the
     pipeline), fully rate-limited, with no double-counting.
  - **Why real-week bucketing is required** (Rishu's explicit point): momentum
    (spec 4.2) is `current_week − mean(trailing 6 present weeks)`. Dumping a
    6-month backfill of an established term into the add-date's week would fake a
    large current-week spike. Bucketing every backfilled paper into the ISO week
    it was actually announced means the current week reflects only papers
    genuinely from this week and the trailing baseline is filled correctly — an
    old term added today reads "steady", a genuinely new term reads "rising".
  - **`open_problems` edits** don't hit arXiv (they're embedding anchors) —
    instead they re-embed the anchor set and re-score the last ~30 days of
    broad-track papers, surfaced on the same panel.
  - Vercel timeout: the synchronous path needs Vercel **Fluid compute / Pro**
    (up to 300 s). On Hobby (10 s), fall back to enqueue-only — the row stays
    `pending`, the worker's cron drains it within the hour, and the panel
    fills on the next `/api/state` poll. Note this as a deploy prerequisite.
- **`/api/feedback`** — `POST` sets `liked | disliked | muted | saved` for a
  paper and records `digest_cards.outcome`.
- **`/api/runs`** — recent `run_log` + a per-card "explain" view (signal
  breakdown, drop reasons).
- **Layout, broad track first** (spec section 6 order, extended): staleness
  banner → rising-terms bar chart (momentum value on each bar) + burst list →
  broad cards (title, authors, date, per-source links, composite rank, why text,
  abstract, citation tag with the actual 30-day number, feedback buttons) →
  niche cards (same, no rank) → cluster rollup (label terms + representative
  titles + size delta) → vocab panel → **"is it working" panel** (precision@k
  over time, lead-time audit notes) → **editable config panel** (tag input for
  seed vocab, textareas for queries/open-problems, multi-select for categories,
  per-source toggles, weight/threshold fields, single Save, version history with
  restore of the last 5) → **"Just added" panel** (6-month history for a
  keyword/query added this session, appears right after Save) → run-log viewer.
- Edit token in `sessionStorage` only.

---

## Deployment & git identity

- **Postgres**: Neon free tier (`pgvector`) or Render Postgres.
- **Worker** — three Render components, one codebase:
  - **Cron Job** (`worker/run.py`, schedule from `config` timezone, e.g.
    `0 3 * * *`) — the daily pipeline. **Instance tier set from the profiled
    embedding RSS** (Cost note): Starter (~$7/mo) for MiniLM — the default —
    or Standard (~$25/mo) for `specter2_base`. Bake the model into the image
    or cache on first run.
  - **Web Service** (FastAPI, Starter tier, no model) — `POST /internal/backfill-term`
    and `POST /internal/run`, both shared-secret.
  - **one-off Job** — initial 12–16 week bootstrap backfill.
  All three take the single `pg_advisory_lock` before any arXiv call. Env (all
  three): `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
  `OPENALEX_API_KEY`, `CORE_API_KEY`, `CROSSREF_MAILTO`, `INTERNAL_SHARED_SECRET`,
  optional `SEMANTIC_SCHOLAR_API_KEY`.
- **Dashboard**: Vercel (**Fluid compute / Pro** for the synchronous term
  backfill; Hobby works in enqueue-only mode). Env: `DATABASE_URL` (read/write),
  `DASHBOARD_EDIT_TOKEN`, `WORKER_INTERNAL_URL`, `INTERNAL_SHARED_SECRET`.
- **Git identity (spec section 7, verbatim)**: on `git init`, set **repo-level**
  (not `--global`) `user.name "Rishu Kumar Singh"` and Rishu's own
  GitHub-registered email. Before any push, run
  `git log --format='%an <%ae>' -n 5` and confirm every line is Rishu — not Akash
  Ghosh or any shared identity. Confirm the remote is Rishu's own GitHub account;
  if it isn't, stop and ask. All commit messages end with
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

---

## Critical files (greenfield — all created under `/home/rishu/Desktop/Paper_Parse`)

- `db/schema.sql` — full schema above + `pgvector`.
- `worker/ingest/base.py` — `RawPaper`, source interface, rate-limit queue,
  checkpointing, backoff.
- `worker/app.py` — FastAPI: `POST /internal/run`, `POST /internal/backfill-term`
  (shared-secret auth).
- `worker/arxiv_lock.py` — the single `pg_advisory_lock` wrapper every component
  acquires before touching arXiv; `try`-lock with a "queued" fallback.
- `worker/ingest/arxiv.py` — 1 req/3 s, announce-date bucketing, version
  collapse; also drives backfill.
- `worker/ingest/term_backfill.py` — bounded N-month single-term arXiv query,
  **real-week `term_counts` bucketing**, dedup upsert, cap → `partial` + cursor;
  called by `/internal/backfill-term` and by pipeline step 0 to drain `partial`
  rows.
- `worker/ingest/{hf_daily,openreview,biorxiv,crossref,openalex,semantic_scholar,core,europepmc}.py`
  — one module each, behind `config.sources`.
- `worker/ingest/dblp.py` — **module docstring must state DBLP returns no
  abstracts.** Every DBLP-sourced row is created with `abstract = NULL` and
  queued for `worker/enrich/abstracts.py`; if resolution fails it takes the
  title-only scoring path (never silent exclusion).
- `worker/identity.py` — canonical-key dedup / cross-source merge.
- `worker/enrich/abstracts.py` — resolve missing abstracts for DBLP/Crossref
  rows: Crossref `abstract` → OpenAlex `abstract_inverted_index` → publisher
  landing-page `<meta>`; leave `NULL` (and flag `abstract_missing`) if all fail.
- `worker/enrich/citations.py` — snapshot + fallback chain +
  `citation_edges` builder + co-citation velocity.
- `worker/enrich/embeddings.py` — self-hosted MiniLM (default) / SPECTER2,
  model-versioned; **Phase 2 must profile and record peak RSS** (Cost note).
- `worker/pipeline/vocab.py` — idempotent decay → prune
  (`last_seen_iso_week`) → add counts; distinct-paper / distinct-group momentum;
  survey exclusion; volume normalization; conservative bigram growth.
- `worker/pipeline/score.py` — component signals → threshold recall gate (not
  top-N) → composite display rank; **no structural-overlap ratio, no citation
  bonus**. **Title-only fallback**: a paper with no resolvable abstract is scored
  on title alone with an `abstract_missing` flag on the card — never dropped
  without a `run_log` trace.
- `worker/pipeline/cluster.py` — HDBSCAN + c-TF-IDF labels + representative
  titles + **previous-period cluster matching** (≥0.5 Jaccard on `member_paper_ids`
  → carry `cluster_key`, else new). Comment must state cluster identity is
  best-effort; the dashboard must not imply continuity beyond `cluster_key`.
- `worker/digest.py` — `digests` upsert + Telegram chunking (4096) + alerts.
- `worker/run.py` — orchestrates pipeline steps 0–18 with the idempotency marker
  (step 0 drains `term_backfills`); `worker/backfill.py` — the one-off bootstrap
  Job.
- `worker/observability.py` — `run_log` / `api_call_log` writers + alert path.
- `dashboard/app/api/state/route.ts`, `dashboard/app/api/config/route.ts`
  (token + rate-limit + validation + version stamping + enqueue `term_backfills`
  + call `/internal/backfill-term`, or enqueue-only on Hobby),
  `dashboard/app/api/feedback/route.ts`, `dashboard/app/api/runs/route.ts`.
- `dashboard/app/page.tsx` + components: `StalenessBanner`, `RisingTerms`,
  `BurstList`, `BroadCard`, `NicheCard`, `ClusterRollup`, `VocabPanel`,
  `WorkingPanel`, `ConfigPanel`, `JustAddedPanel`, `RunLogViewer`.
- `render.yaml` (Cron Job + Web Service + one-off Job; **instance tier as a
  comment citing the profiled embedding RSS**), `dashboard/vercel.json`,
  `worker/requirements.txt` (`fastapi`, `uvicorn`, `httpx`, `feedparser`,
  `psycopg[binary]`, `pgvector`, `transformers`, `adapters`, `torch` CPU,
  `sentence-transformers`, `scikit-learn`, `hdbscan`, `numpy`, `openreview-py`).

Reuse: `openreview-py` for OpenReview (don't hand-roll); `feedparser` for arXiv
Atom; `pgvector` SQL operators for cosine; scikit-learn `TfidfVectorizer` for
c-TF-IDF cluster labels; `hdbscan` for clustering. No suitable prior code exists
in the workspace (directory is empty).

---

## Build phases

- **Phase 0 — skeleton + observability.** Repo + git identity; `db/schema.sql` +
  migrations; `config` seeded with the decided Cold-start values; worker FastAPI
  service (`/internal/run`, `/internal/backfill-term`) + `arxiv_lock` + arXiv
  fetcher with queue + checkpoint; `run_log` / `api_call_log` wired from the
  first commit; `config` table + validated token-gated panel; dashboard shell;
  Telegram "hello" + alert path; Render Cron Job running `worker/run.py` deployed
  and proven to run + log daily.
- **Phase 1 — multi-source ingest + identity.** All source modules behind
  `config.sources`; dedup resolver; `paper_sources`; `watermarks`;
  `worker/enrich/abstracts.py` + the title-only path for DBLP/Crossref rows.
- **Phase 2 — enrichment.** Citation snapshots (fallback chain); reference-graph
  builder + `citation_edges`; self-hosted embeddings + `pgvector`; concept tags;
  repo-link capture. **Profile peak RSS of the chosen embedding model on the
  target container and set the Cron Job tier in `render.yaml` from the measured
  number** (Cost note).
- **Phase 3 — backfill Job.** 12–16 weeks across sources; baselines; initial
  reference graph; embeddings; seed vocab + seed papers; Rishu's like-pass.
- **Phase 4 — deterministic scoring.** Idempotent vocab maintenance;
  word-boundary matching; distinct-paper/group momentum + survey exclusion +
  volume normalization; multi-lab burst; embedding similarity; co-citation
  velocity; HF signal; threshold recall gate + hard cap; composite display rank;
  `signals` breakdown; niche recency feed; citation tiering as a tag.
- **Phase 5 — digest + delivery + dashboard + shadow mode.** `digests` upsert;
  Telegram chunked digest; `/api/state` + all panels; feedback endpoints +
  `digest_cards`; **editable config panel with on-demand 6-month term backfill**
  (`term_backfills`, `/internal/backfill-term`, real-week bucketing, `JustAddedPanel`);
  1–2 weeks `CALIBRATION`; tune thresholds/weights via the config panel.
- **Phase 6 — measurement + clustering + go live.** precision@k rollup;
  retrospective lead-time audit tooling + `eval_labels`; weekly/monthly HDBSCAN +
  c-TF-IDF cluster rollup **with previous-period `cluster_key` matching**;
  deterministic `config_suggestions`; decide MiniLM-vs-SPECTER2 from broad-track
  precision; flip digests to live; tune alert thresholds.
- **Phase 7 — later increments (schema-ready, add on observed weakness).** GitHub
  star velocity (daily snapshots); OpenReview season module hardening; SerpApi
  Scholar bridge only if breadth still feels thin (and Rishu accepts the cost);
  Kleinberg burst detection as an alternative momentum signal; OpenAlex
  author-ID graph de-weighting; monthly LLM query-proposal job **only if** Rishu
  later reverses the no-LLM decision.

---

## Verification (Definition of Done — extends spec section 9)

- [ ] Postgres provisioned, schema applied, `config` seeded with the **decided
      Cold-start config**: the 10 project-derived `seed_vocab` anchors (+ the
      low-weight subfield tail), `broad_categories` including `eess.SP` and
      `stat.ML`, and the 5 re-derived `open_problems`.
- [ ] Backfill Job completes: `papers` holds 12+ weeks across ≥3 sources;
      `term_counts` has 6+ week baselines; `citation_edges` non-empty.
- [ ] Worker deployed on Render (Web Service + Cron hitting `/internal/run` +
      one-off bootstrap Job); one manual run completes and is inspected via
      `run_log`. **Run it twice the same day and diff** — `term_counts`,
      `digests`, decay, and vocab growth are all unchanged the second time.
- [ ] `git log --format='%an <%ae>'` shows Rishu Kumar Singh on every commit;
      remote is Rishu's own GitHub account (verified before first push).
- [ ] Dashboard on Vercel loads `/api/state` live — change a value directly in
      the DB, refresh, see the change with no redeploy.
- [ ] A broad card shows composite rank + why text (matched terms / nearest
      anchor / co-citation count) + citation tag with the **actual 30-day delta
      number** + per-source links + feedback buttons.
- [ ] A paper tracked 14+ days shows a citation tier with a real delta; a paper
      < 30 days old / < 2 snapshots shows "not enough data", not "⚪ 0".
- [ ] Word-boundary matching verified against a real false-positive pair: the
      keyword `ground` does **not** match "foreground segmentation" or
      "background subtraction" (substring false positives), nor "grounded"
      (strict `\bterm\b`, stem matching is a non-goal), but **does** match
      "evaluated against ground truth".
- [ ] Momentum verified: 3 papers from one author group move a term less than 3
      papers from three different groups; a survey paper does not move a term.
- [ ] Multi-lab burst fires only at ≥ `burst_min_groups` distinct first-author
      groups.
- [ ] Dedup verified: the same paper from arXiv + HF + medRxiv → one card;
      arXiv `v1` + `v2` → one card.
- [ ] Mute verified: a `muted` paper never reappears in any later digest.
- [ ] "New since last digest" is the default broad view; persistent items appear
      under "still trending", not re-presented as new.
- [ ] Threshold-based inclusion verified: an artificially quiet week produces a
      short digest, not a padded top-N.
- [ ] Staleness banner verified in both states (fresh: hidden; aged: shown, names
      the gap, points at the Render cron logs).
- [ ] Telegram delivers a > 4096-char digest without error (chunking) on a manual
      trigger; a forced run failure / no-run-in-26h sends an alert.
- [ ] Config edit: a keyword added with the correct token appears in `vocab`
      immediately and is used by the very next run; wrong/missing token → 401;
      malformed payload → 400 with no coercion; a bad edit is one-click
      restorable from `config_history`.
- [ ] Adding a keyword/query via the edit panel → the dashboard shows that term's
      **last 6 months** of arXiv papers within ~1 min (synchronous on Pro/Fluid;
      within the hour on Hobby enqueue-only), and the config now tracks it for
      future daily runs.
- [ ] Backfilled papers land in `term_counts` under their **real announce week**:
      adding an established term (e.g. `neurosymbolic`) shows a flat momentum
      line — **no** current-week spike — while a genuinely new term shows rising.
- [ ] A capped/`partial` `term_backfills` row is finished by the next worker run
      (pipeline step 0) with no double-counting; a backfilled paper already in
      `papers` gains the term match rather than a duplicate row.
- [ ] `config_suggestions` is populated deterministically from liked-paper
      bigrams after a like-pass; nothing is auto-applied.
- [ ] "Is it working" panel renders precision@k from at least the shadow-mode
      data; the retrospective lead-time audit script runs against `eval_labels`.
- [ ] Degraded-mode check: disable OpenAlex + S2 in `config.sources`, run, and a
      usable digest is still produced from arXiv alone (enrichment fields null,
      banner shown).
- [ ] Embedding RSS profiled on the target container; `render.yaml` Cron Job tier
      set from that number with the measurement in a comment (not left as an
      estimate).
- [ ] A DBLP-sourced paper with no abstract is either abstract-resolved via
      Crossref/OpenAlex or scored title-only with an `abstract_missing` flag —
      never dropped without a `run_log` trace.
- [ ] Cluster continuity verified: a cluster whose members are ~unchanged between
      two monthly runs keeps its `cluster_key` and shows a real `prev_size`; a
      genuinely new grouping gets a fresh key and is labelled "new". The
      dashboard shows no continuity that isn't backed by `cluster_key`.
- [ ] arXiv advisory lock verified: a backfill request fired while the daily run
      holds the lock is enqueued and returns "queued", not blocked or a second
      concurrent arXiv connection.
- [ ] Report back: live Vercel URL, Render service links (web service + cron +
      bootstrap job), git-identity confirmation.
