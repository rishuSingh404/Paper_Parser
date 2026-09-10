"""Deterministic scoring pipeline (plan: "Deterministic pipeline", steps 0-18).

Phase 0 ships steps 0-3 for real (drain backfills, fetch arXiv, upsert, recompute
term_counts). Steps 4-18 are stubbed with TODO markers in worker/run.py and land
in Phases 4-6.
"""
