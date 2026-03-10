---
phase: 04-probability-model
plan: "02"
subsystem: prediction
tags: [llm, probability, combiner, confidence-interval, tdd]
dependency_graph:
  requires: [src/pmtb/research/sentiment.py, src/pmtb/scanner/models.py, src/pmtb/research/models.py]
  provides: [ClaudePredictor, combine_estimates, compute_confidence_interval]
  affects: [04-03-PLAN.md]
tech_stack:
  added: []
  patterns: [lazy-anthropic-import, log-odds-combination, ci-clamping]
key_files:
  created:
    - src/pmtb/prediction/llm_predictor.py
    - src/pmtb/prediction/combiner.py
    - src/pmtb/prediction/confidence.py
    - tests/prediction/test_llm_predictor.py
    - tests/prediction/test_combiner.py
    - tests/prediction/test_confidence.py
  modified: []
decisions:
  - id: "Phase 04-02-A"
    summary: "Lazy AsyncAnthropic import in ClaudePredictor __init__ following SentimentClassifier pattern — optional dependency, avoids hard import failure when API key is absent"
  - id: "Phase 04-02-B"
    summary: "PREDICTION_LLM_CALLS Prometheus counter tracks Claude prediction API calls for production cost monitoring"
  - id: "Phase 04-02-C"
    summary: "CI uses simple half-width clamping — more sophisticated methods (bootstrap, beta distribution) deferred to later per plan spec"
metrics:
  duration: "3 min"
  completed_date: "2026-03-10"
  tasks_completed: 2
  files_created: 6
  files_modified: 0
---

# Phase 4 Plan 02: LLM Predictor, Combiner, and Confidence Interval Summary

**One-liner:** ClaudePredictor with anti-anchoring calibration prompt, log-odds Bayesian combiner, and clamped confidence interval computation.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | ClaudePredictor with structured probability estimation | 2199fcb | llm_predictor.py, test_llm_predictor.py |
| 2 | Probability combiner and confidence interval computation | c4f64f2 | combiner.py, confidence.py, test_combiner.py, test_confidence.py |

## What Was Built

### ClaudePredictor (src/pmtb/prediction/llm_predictor.py)

- Follows SentimentClassifier pattern: lazy `AsyncAnthropic` import, `anthropic_api_key=None` disables Claude.
- `is_available` property: True when API key was provided.
- `SYSTEM_PROMPT`: calibrated probabilistic forecaster prompt with explicit anti-anchoring instructions, base rate reminder, and "Do not anchor to the current market price."
- `predict(market, bundle)`: assembles user prompt from market title, close time, implied probability, and research signals; calls Claude; returns `{p_estimate, confidence, reasoning, key_factors}`.
- `p_estimate` clamped to [0, 1] with warning log if out of range.
- Invalid JSON raises `ValueError` with context.
- `PREDICTION_LLM_CALLS` Prometheus counter tracks API calls.

### combine_estimates / Combiner (src/pmtb/prediction/combiner.py)

- `combine_log_odds()`: Bayesian combination in logit space. Inputs clipped to [eps, 1-eps] to avoid log(0). Default weights 0.6/0.4 (XGBoost/Claude).
- `combine_weighted_average()`: linear blend clamped to [0, 1].
- `combine_estimates()`: main entry point — single-estimator pass-through (cold start, Claude skipped), both-None raises `ValueError`, unknown method raises `ValueError`.

### compute_confidence_interval (src/pmtb/prediction/confidence.py)

- Simple `p_model ± half_width` clamped to [0, 1]. Default `half_width=0.1`.
- Key invariant: output always in [0, 1], never negative or > 1.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] MarketCandidate fixture missing required fields**
- **Found during:** Task 1 RED→GREEN transition
- **Issue:** Plan's interface excerpt showed only 5 fields for `MarketCandidate`, but actual model has 9 required fields (`event_context`, `yes_bid`, `yes_ask`, `spread`, `volume_24h` were missing from the plan's interface description).
- **Fix:** Added all required fields to the `make_market()` test fixture.
- **Files modified:** tests/prediction/test_llm_predictor.py
- **Commit:** 2199fcb (included in GREEN commit)

## Test Coverage

| File | Tests | Result |
|------|-------|--------|
| test_llm_predictor.py | 13 | PASS |
| test_combiner.py | 20 | PASS |
| test_confidence.py | 10 | PASS |
| **Total** | **43** | **PASS** |

## Self-Check: PASSED

All created files exist on disk. All task commits verified present:
- 1c96a1c: test(04-02): add failing tests for ClaudePredictor
- 2199fcb: feat(04-02): ClaudePredictor with structured probability estimation
- 093931c: test(04-02): add failing tests for combiner and confidence interval
- c4f64f2: feat(04-02): probability combiner and confidence interval computation
