-- Paper Radar — decided cold-start config (plan section "Cold-start config").
-- Apply AFTER schema.sql:  psql "$DATABASE_URL" -f db/seed_config.sql
-- Idempotent: re-running only fills the row if it is missing.
--
-- seed_vocab is anchored on the recurring structural moves across Rishu's active
-- projects (MedJEPA-Critic, MedFaith, SigPRM/DGT, TRIAGE + related tracked work
-- Latent Critic / LRS / Agent-BRACE), NOT generic hallucination-detection words.
-- The ten weight-1.0-ish terms are the primary anchors; the lower-weight tail
-- keeps the niche track and the "am I still in my field" signal alive.

INSERT INTO config (id, niche_queries, broad_categories, seed_vocab, open_problems,
                    seed_papers, sources, recall_gate_thresholds, rank_weights)
VALUES (
  1,
  -- niche_queries: exact-subfield arXiv queries (edit freely from the dashboard)
  $json$[
    "abs:\"hallucination detection\" AND abs:\"vision-language\"",
    "abs:\"medical vision-language model\"",
    "abs:\"grounding verification\"",
    "abs:\"process reward model\"",
    "abs:\"chain-of-thought faithfulness\"",
    "abs:\"LLM safety\" AND abs:verification"
  ]$json$::jsonb,

  -- broad_categories: cs.* core + eess.SP (DGT signal work) + stat.ML (cross-project causal verification)
  $json$["cs.CL","cs.LG","cs.AI","cs.CV","cs.CR","eess.SP","stat.ML"]$json$::jsonb,

  -- seed_vocab {term: weight}
  $json${
    "decorrelation": 1.0,
    "error correlation": 1.0,
    "latent critic": 1.0,
    "latent steering": 1.0,
    "verbalized uncertainty": 0.9,
    "causal": 0.8,
    "necessity ablation": 1.0,
    "repair policy": 1.0,
    "neurosymbolic": 0.9,
    "signal grounding": 1.0,
    "hallucination detection": 0.7,
    "grounding verification": 0.8,
    "process reward model": 0.9,
    "chain-of-thought faithfulness": 0.7,
    "world model": 0.6,
    "jepa": 0.6
  }$json$::jsonb,

  -- open_problems: embedding anchors, re-derived from the projects
  $json$[
    "A verifier's reliability comes from its errors being decorrelated from the generator's, not from its own accuracy - estimating and enforcing that decorrelation without ground-truth labels on the latent state.",
    "Verifying whether a specific claim is load-bearing by ablating it and checking necessity, rather than by matching it against retrieved text.",
    "Repairing an output when several error sources interact, where fixing one in isolation makes another worse.",
    "Grounding a generated claim directly in a raw signal (waveform, image) instead of in a retrieved-text proxy.",
    "Detecting when a model's verbalized confidence does not track its actual reliability."
  ]$json$::jsonb,

  -- seed_papers: Rishu adds 5-10 arXiv ids (own preprints + Latent Critic / LRS /
  -- Agent-BRACE + canonical necessity-ablation and neurosymbolic-repair papers)
  $json$[]$json$::jsonb,

  -- sources: OpenReview off by default (seasonal, isolated); everything else on
  $json${
    "arxiv":      {"enabled": true},
    "hf_daily":   {"enabled": true},
    "biorxiv":    {"enabled": true},
    "medrxiv":    {"enabled": true},
    "crossref":   {"enabled": true},
    "openalex":   {"enabled": true},
    "s2":         {"enabled": true},
    "core":       {"enabled": true},
    "dblp":       {"enabled": true},
    "europepmc":  {"enabled": true},
    "openreview": {"enabled": false}
  }$json$::jsonb,

  -- recall_gate_thresholds: HEURISTIC starting points, tuned in Phase 5 shadow mode.
  -- A broad-track paper is a candidate if it clears ANY of these.
  $json${
    "embedding_sim": 0.35,
    "lexical": 0.5,
    "cocitation_velocity": 2,
    "hf_upvotes": 20,
    "concept_overlap": 2
  }$json$::jsonb,

  -- rank_weights: HEURISTIC (see plan step 9 - not derived from anything more
  -- principled than "structural overlap matters most"). Ordering only, never a gate.
  $json${
    "embedding_sim": 3.0,
    "lexical": 1.0,
    "concept_overlap": 0.5,
    "cocitation_velocity": 1.0,
    "hf_upvotes": 0.5
  }$json$::jsonb
)
ON CONFLICT (id) DO NOTHING;
