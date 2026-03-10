---
phase: 04-probability-model
plan: "03"
subsystem: prediction
tags: [pipeline, orchestration, cold-start, hybrid, xgboost, claude, prometheus, db-persistence]
dependency_graph:
  requires:
    - "04-01"  # XGBoostPredictor, PredictionResult, FEATURE_NAMES, build_feature_vector
    - "04-02"  # ClaudePredictor, combine_estimates, compute_confidence_interval
  provides:
    - ProbabilityPipeline (predict_one, predict_all, _persist)
  affects:
    - "05-*"  # Phase 5 execution engine calls ProbabilityPipeline.predict_all
tech_stack:
  added: []
  patterns:
    - "LLM gating: Claude only invoked when XGBoost p in [0.4, 0.6] uncertainty band"
    - "Shadow-only p_model=0.5: uninformative prior avoids PredictionResult ge=0.0 constraint violation"
    - "Prometheus PREDICTION_LATENCY histogram + PREDICTION_COUNT counter with mode label"
    - "Async context manager pattern for session_factory via async with session_factory() as session"
    - "text() SQLAlchemy for raw ticker->UUID FK lookup"
key_files:
  created:
    - src/pmtb/prediction/pipeline.py
    - tests/prediction/test_pipeline.py
  modified: []
decisions:
  - "shadow-only p_model=0.5: float('nan') fails PredictionResult ge=0.0 Pydantic constraint; 0.5 (uninformative prior) is semantically correct and is_shadow=True marks it as non-tradeable"
  - "signal_weights NaN filtering: bundle.to_features() returns NaN for missing sources; NaN values stripped before storing in signal_weights JSON field to avoid JSON serialization issues"
  - "DB persistence skipped (not crashed) when market not found: predict_all resilience is a first-class requirement"
metrics:
  duration: "7 min"
  completed: "2026-03-10"
  tasks_completed: 1
  files_created: 2
  files_modified: 0
---

# Phase 04 Plan 03: ProbabilityPipeline Summary

ProbabilityPipeline orchestrating cold-start (Claude-sole estimator), hybrid (XGBoost primary + gated Claude), and shadow-only (uninformative prior) modes with full DB persistence and Prometheus observability.

## What Was Built

`ProbabilityPipeline` wires all Phase 4 prediction components (XGBoostPredictor, ClaudePredictor, combiner, confidence) into a single orchestrator that Phase 5 will call. The pipeline:

1. **Cold start mode** (`xgb.is_ready=False`, `claude.is_available=True`): Claude produces the real estimate. XGBoost `shadow_predict()` runs in the background to accumulate labeled training data. `used_llm=True`, `is_shadow=False`.

2. **Hybrid mode** (`xgb.is_ready=True`): XGBoost provides the base probability. Claude is only invoked when `p_xgb` falls within `[prediction_xgb_confidence_low, prediction_xgb_confidence_high]` (default 0.4–0.6). Outside the band, `combine_estimates` receives `p_claude=None` and passes through `p_xgb` directly. Inside the band with Claude available, both estimates are combined via the configured method (`log_odds` or `weighted_average`).

3. **Shadow-only mode** (`xgb.is_ready=False`, `claude.is_available=False`): `p_model=0.5` (uninformative prior), `is_shadow=True`. Logged but not tradeable. `shadow_predict()` still runs for training data collection.

4. **DB persistence**: Every prediction attempts a `ModelOutput` row write via async SQLAlchemy. The `market_id` FK is resolved by querying `markets WHERE ticker = :ticker`. Missing market logs a warning and skips persistence — the pipeline does not crash.

5. **predict_all resilience**: Matches markets to bundles by ticker. Markets with no bundle are skipped. Individual prediction failures are caught and logged — the batch continues.

6. **Prometheus observability**: `PREDICTION_LATENCY` histogram and `PREDICTION_COUNT` counter with `mode` label (`cold_start`, `hybrid`, `shadow_only`).

## Tests

14 tests in `tests/prediction/test_pipeline.py` covering all modes, LLM gating enforcement, DB persistence field mapping, missing-market-in-DB graceful skip, predict_all failure resilience, ticker-based bundle matching, and Prometheus metric existence.

Full test suite: 275 passed, 3 skipped.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] MarketCandidate fixture missing required `event_context` field**
- **Found during:** RED phase — fixture construction
- **Issue:** `MarketCandidate` requires `event_context: dict` but test fixture omitted it. Also had a non-existent `open_interest` field.
- **Fix:** Added `event_context={}` and removed `open_interest` from `_make_market()` fixture.
- **Files modified:** `tests/prediction/test_pipeline.py`
- **Commit:** d9c042c

**2. [Rule 2 - Missing critical functionality] NaN filtering for signal_weights JSON field**
- **Found during:** Task 1 implementation
- **Issue:** `bundle.to_features()` returns NaN for missing sources. Storing NaN in a PostgreSQL JSON column would cause serialization errors at runtime.
- **Fix:** Filter NaN values from `signal_weights` before storing: `{k: v for k, v in signal_weights.items() if v == v}` (NaN != NaN is standard Python NaN check).
- **Files modified:** `src/pmtb/prediction/pipeline.py`
- **Commit:** d9c042c

## Self-Check: PASSED

- src/pmtb/prediction/pipeline.py: FOUND
- tests/prediction/test_pipeline.py: FOUND
- Commit d9c042c: FOUND
- 275 tests pass, 3 skipped
