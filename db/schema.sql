-- Paper Radar — database schema
-- Apply with:  psql "$DATABASE_URL" -f db/schema.sql
-- Then seed :  psql "$DATABASE_URL" -f db/seed_config.sql
--
-- Design reference: the approved plan at
--   ~/.claude/plans/read-this-plan-and-soft-finch.md  ("Database schema" section)
-- Deviations from PAPER_RADAR_SPEC section 3 are intentional and noted inline.

-- Embeddings are stored as REAL[] and cosine is computed in numpy/Python
-- (volume is thousands of rows, not millions — a sequential scan is fine, and
-- this drops the pgvector dependency entirely). The approved plan sanctions
-- this fallback explicitly.

-- ---------------------------------------------------------------------------
-- Papers + per-source provenance
-- ---------------------------------------------------------------------------

-- One row per real-world paper. `paper_id` == `canonical_key` (see
-- worker/identity.py): "doi:<lower>", "arxiv:<versionless>", or "hash:<sha1>".
CREATE TABLE papers (
  paper_id            TEXT PRIMARY KEY,
  canonical_key       TEXT NOT NULL,
  arxiv_id            TEXT,                        -- versionless (v1/v2/v3 collapsed)
  doi                 TEXT,
  title               TEXT NOT NULL,
  abstract            TEXT,                        -- NULL allowed: DBLP has none; may be resolved later
  abstract_missing    BOOLEAN NOT NULL DEFAULT FALSE,
  authors             TEXT[] NOT NULL DEFAULT '{}',
  first_author_group  TEXT,                        -- normalized first-author surname, for diffusion counts
  published_date      DATE,
  announce_date       DATE,                        -- week-bucketing key (arXiv: submission date proxy; OAI datestamp later)
  link                TEXT,
  categories          TEXT[] NOT NULL DEFAULT '{}',
  concepts            TEXT[] NOT NULL DEFAULT '{}',   -- OpenAlex topic/concept tags
  repo_url            TEXT,                            -- linked code repo (HF / arXiv comments)
  is_survey           BOOLEAN NOT NULL DEFAULT FALSE,
  muted               BOOLEAN NOT NULL DEFAULT FALSE,
  first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX papers_canonical_key_idx ON papers (canonical_key);
CREATE INDEX papers_arxiv_id_idx      ON papers (arxiv_id);
CREATE INDEX papers_announce_date_idx ON papers (announce_date);
CREATE INDEX papers_first_seen_at_idx ON papers (first_seen_at);

CREATE TABLE paper_sources (
  paper_id      TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  source        TEXT NOT NULL,   -- arxiv|hf_daily|openreview|biorxiv|medrxiv|crossref|openalex|s2|core|dblp|europepmc
  source_id     TEXT NOT NULL,
  source_url    TEXT,
  raw           JSONB,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (paper_id, source)
);
CREATE INDEX paper_sources_source_idx ON paper_sources (source, source_id);

-- ---------------------------------------------------------------------------
-- Citation signals  (lagging "established" tag, NOT a rank input)
-- ---------------------------------------------------------------------------

CREATE TABLE citation_snapshots (
  paper_id       TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  snapshot_date  DATE NOT NULL,
  citation_count INT  NOT NULL,
  source         TEXT NOT NULL DEFAULT 'openalex',
  PRIMARY KEY (paper_id, snapshot_date)
);

-- Local co-citation graph. `citing_paper_id` may be external (no FK).
CREATE TABLE citation_edges (
  citing_paper_id TEXT NOT NULL,
  cited_paper_id  TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (citing_paper_id, cited_paper_id)
);
CREATE INDEX citation_edges_cited_idx ON citation_edges (cited_paper_id);

-- ---------------------------------------------------------------------------
-- Embeddings  (self-hosted; model is switchable via config.embedding_model_version)
-- ---------------------------------------------------------------------------

CREATE TABLE embeddings (
  paper_id      TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  model_version TEXT NOT NULL,
  embedding     REAL[] NOT NULL,      -- dim depends on model (MiniLM 384 / specter2 768)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (paper_id, model_version)
);

-- Similarity anchors: open-problem statements, seed papers, and the running
-- centroid of liked papers. Re-embedded when config.open_problems changes.
CREATE TABLE anchor_embeddings (
  anchor_kind   TEXT NOT NULL,        -- open_problem | seed_paper | liked_centroid
  anchor_id     TEXT NOT NULL,        -- ordinal | paper_id | 'centroid'
  model_version TEXT NOT NULL,
  embedding     REAL[] NOT NULL,
  text          TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (anchor_kind, anchor_id, model_version)
);

-- ---------------------------------------------------------------------------
-- Hugging Face Daily Papers signal
-- ---------------------------------------------------------------------------

CREATE TABLE hf_signals (
  paper_id      TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  snapshot_date DATE NOT NULL,
  upvotes       INT NOT NULL DEFAULT 0,
  comments      INT NOT NULL DEFAULT 0,
  PRIMARY KEY (paper_id, snapshot_date)
);

-- ---------------------------------------------------------------------------
-- Vocabulary + momentum ledger
-- ---------------------------------------------------------------------------

CREATE TABLE vocab (
  term               TEXT PRIMARY KEY,
  weight             REAL NOT NULL,
  is_seed            BOOLEAN NOT NULL DEFAULT FALSE,   -- from config.seed_vocab; never auto-pruned
  last_seen_iso_week TEXT,                             -- explicit, for calendar-correct pruning
  added_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE term_counts (
  term             TEXT NOT NULL,
  iso_week         TEXT NOT NULL,        -- 'IYYY-Www', e.g. '2026-W37'
  count            INT  NOT NULL DEFAULT 0,   -- DISTINCT papers containing the term that week (surveys excluded)
  distinct_authors INT  NOT NULL DEFAULT 0,   -- distinct first-author groups (diffusion)
  category_share   REAL,                      -- count / that week's total category volume
  PRIMARY KEY (term, iso_week)
);
CREATE INDEX term_counts_iso_week_idx ON term_counts (iso_week);

CREATE TABLE term_bursts (
  term                 TEXT NOT NULL,
  iso_week             TEXT NOT NULL,
  distinct_groups      INT NOT NULL,
  prev_distinct_groups INT NOT NULL,
  PRIMARY KEY (term, iso_week)
);

-- Corpus-wide burst detection for terms NOT YET in `vocab` (plan gap: catching
-- "a technique is trending in a domain I don't track yet", e.g. the
-- MedJEPA-Critic origin story — JEPA trending elsewhere, before it was a
-- tracked term). Recomputed fresh each run (worker/pipeline/discovery.py);
-- old rows for run_date are replaced, not accumulated.
CREATE TABLE discovery_bursts (
  run_date        DATE NOT NULL,
  term            TEXT NOT NULL,
  tier            TEXT NOT NULL CHECK (tier IN ('first_appearance','bursting')),
  current_count   INT NOT NULL,
  baseline_count  REAL NOT NULL,
  delta           REAL NOT NULL,
  distinct_groups INT NOT NULL,
  PRIMARY KEY (run_date, term)
);

-- Weekly total papers per category — denominator for term_counts.category_share.
CREATE TABLE category_volume (
  iso_week TEXT NOT NULL,
  category TEXT NOT NULL,
  count    INT NOT NULL DEFAULT 0,
  PRIMARY KEY (iso_week, category)
);

-- ---------------------------------------------------------------------------
-- Per-paper scoring breakdown  (never merged into one opaque number)
-- ---------------------------------------------------------------------------

CREATE TABLE signals (
  paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  run_date DATE NOT NULL,
  name     TEXT NOT NULL,   -- embedding_sim | lexical | concept_overlap | cocitation_velocity | hf_upvotes | composite_rank | ...
  value    DOUBLE PRECISION NOT NULL,
  detail   JSONB,           -- best anchor text, matched terms, drop reason, etc.
  PRIMARY KEY (paper_id, run_date, name)
);
CREATE INDEX signals_run_date_idx ON signals (run_date);

-- ---------------------------------------------------------------------------
-- Feedback + digests + measurement
-- ---------------------------------------------------------------------------

CREATE TABLE feedback (
  paper_id            TEXT PRIMARY KEY REFERENCES papers(paper_id) ON DELETE CASCADE,
  verdict             TEXT NOT NULL CHECK (verdict IN ('liked','disliked','muted','saved')),
  processed_for_vocab BOOLEAN NOT NULL DEFAULT FALSE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE digests (
  run_date         DATE PRIMARY KEY,
  mode             TEXT NOT NULL DEFAULT 'calibration' CHECK (mode IN ('calibration','live')),
  broad_ranked     JSONB NOT NULL DEFAULT '[]',   -- ranked candidates: scores + per-signal breakdown + why-flagged
  cross_domain     JSONB NOT NULL DEFAULT '[]',   -- structurally similar to an open problem, NOT already in Rishu's domain vocab — the transfer-candidate view
  niche_papers     JSONB NOT NULL DEFAULT '[]',
  rising_terms     JSONB NOT NULL DEFAULT '[]',
  bursts           JSONB NOT NULL DEFAULT '[]',
  clusters_summary JSONB NOT NULL DEFAULT '[]',
  papers_scanned   INT  NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE digest_cards (
  run_date   DATE NOT NULL,
  paper_id   TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  section    TEXT NOT NULL CHECK (section IN ('broad','niche','cross_domain')),
  rank       INT,
  outcome    TEXT CHECK (outcome IN ('liked','disliked','muted','saved','ignored')),
  outcome_at TIMESTAMPTZ,
  PRIMARY KEY (run_date, paper_id, section)
);

CREATE TABLE clusters (
  period                  TEXT NOT NULL,       -- 'IYYY-Www' or 'YYYY-MM'
  run_cluster_id          INT  NOT NULL,       -- HDBSCAN label for this run (UNSTABLE across runs)
  cluster_key             TEXT NOT NULL,       -- STABLE id: carried across periods on >=0.5 Jaccard of member ids
  label_terms             TEXT[] NOT NULL DEFAULT '{}',
  member_paper_ids        TEXT[] NOT NULL DEFAULT '{}',
  representative_paper_ids TEXT[] NOT NULL DEFAULT '{}',
  size                    INT  NOT NULL,
  prev_size               INT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (period, cluster_key)
);

CREATE TABLE eval_labels (
  paper_id   TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  label      TEXT NOT NULL,   -- relevant | not_relevant | blew_up
  labeled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (paper_id, label)
);

-- ---------------------------------------------------------------------------
-- Config (single row) + history + suggestions
-- ---------------------------------------------------------------------------

CREATE TABLE config (
  id                           INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  niche_queries                JSONB NOT NULL DEFAULT '[]',
  broad_categories             JSONB NOT NULL DEFAULT '[]',
  seed_vocab                   JSONB NOT NULL DEFAULT '{}',   -- {term: weight}
  open_problems                JSONB NOT NULL DEFAULT '[]',
  seed_papers                  JSONB NOT NULL DEFAULT '[]',   -- arXiv ids, embedding anchors
  sources                      JSONB NOT NULL DEFAULT '{}',   -- {name: {enabled: bool, ...}}
  vocab_decay_rate             REAL NOT NULL DEFAULT 0.97,
  vocab_prune_weight_threshold REAL NOT NULL DEFAULT 0.15,
  vocab_prune_min_silent_weeks INT  NOT NULL DEFAULT 4,
  citation_hot_threshold       INT  NOT NULL DEFAULT 5,
  citation_warming_threshold   INT  NOT NULL DEFAULT 1,
  cocitation_lookback_weeks    INT  NOT NULL DEFAULT 6,
  burst_min_groups             INT  NOT NULL DEFAULT 3,
  recall_gate_thresholds       JSONB NOT NULL DEFAULT '{}',   -- heuristic; tune in Phase 5
  rank_weights                 JSONB NOT NULL DEFAULT '{}',   -- heuristic; tune in Phase 5
  embedding_model_version      TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',
  keyword_backfill_months      INT  NOT NULL DEFAULT 6,
  digest_mode                  TEXT NOT NULL DEFAULT 'calibration' CHECK (digest_mode IN ('calibration','live')),
  timezone                     TEXT NOT NULL DEFAULT 'UTC',
  version                      INT  NOT NULL DEFAULT 1,
  updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per edit, written BEFORE each `config` update (full snapshot).
CREATE TABLE config_history (
  id        BIGSERIAL PRIMARY KEY,
  snapshot  JSONB NOT NULL,
  edited_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE config_suggestions (
  id         BIGSERIAL PRIMARY KEY,
  kind       TEXT NOT NULL,        -- vocab_term | niche_query | open_problem
  payload    JSONB NOT NULL,
  rationale  TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected'))
);

-- ---------------------------------------------------------------------------
-- On-demand 6-month backfill for dashboard-added keywords/queries
-- ---------------------------------------------------------------------------

CREATE TABLE term_backfills (
  id           BIGSERIAL PRIMARY KEY,
  term         TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('keyword','query')),
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','partial','done','error')),
  cursor       TEXT,
  stats        JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX term_backfills_open_idx ON term_backfills (status)
  WHERE status IN ('pending','partial');

-- ---------------------------------------------------------------------------
-- Saved searches — self-service "search my domain" from the dashboard.
--
-- Separate from niche_queries on purpose. niche_queries + niche_phrases()
-- feed the strict `in_domain` classifier (score.py) — deliberately narrow,
-- since being too loose there (a bare "vision-language") wrongly marks
-- cross-domain papers as "already your field". A saved search is the
-- opposite intent: broad recall over a plain-language description ("ECG
-- signal hallucination detection"), re-matched against the full corpus live
-- on every dashboard load (query-time, not a fixed snapshot) — a genuinely
-- new arXiv paper ingested by the regular daily sweep shows up here the next
-- time the dashboard is opened, no separate backend job needed. `words` is
-- the AND-matched significant-word set extracted from `phrase` (mirrors the
-- arXiv query built at creation time); `arxiv_query` is that same query,
-- kept so the one-time historical backfill triggered on creation is
-- reproducible/auditable.
-- ---------------------------------------------------------------------------

CREATE TABLE saved_searches (
  id           BIGSERIAL PRIMARY KEY,
  label        TEXT NOT NULL,
  phrase       TEXT NOT NULL,
  words        TEXT[] NOT NULL DEFAULT '{}',
  arxiv_query  TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Observability
-- ---------------------------------------------------------------------------

CREATE TABLE run_log (
  id             BIGSERIAL PRIMARY KEY,
  run_date       DATE NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'daily',   -- daily | backfill | term_backfill | manual
  started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at    TIMESTAMPTZ,
  status         TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','error')),
  stats          JSONB NOT NULL DEFAULT '{}',
  errors         JSONB NOT NULL DEFAULT '[]',
  config_version INT
);
CREATE INDEX run_log_run_date_idx ON run_log (run_date DESC);

CREATE TABLE api_call_log (
  id         BIGSERIAL PRIMARY KEY,
  run_id     BIGINT REFERENCES run_log(id) ON DELETE SET NULL,
  source     TEXT NOT NULL,
  endpoint   TEXT NOT NULL,
  status     INT,
  latency_ms INT,
  quota_note TEXT,
  called_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX api_call_log_run_id_idx ON api_call_log (run_id);

CREATE TABLE watermarks (
  source          TEXT PRIMARY KEY,
  last_fetched_at TIMESTAMPTZ,
  last_cursor     TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotency guard: pipeline steps that must run at most once per period
-- (decay/prune, the whole daily run) insert a marker here first.
CREATE TABLE run_markers (
  marker     TEXT PRIMARY KEY,   -- e.g. 'decay:2026-W37', 'run:2026-09-11'
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
