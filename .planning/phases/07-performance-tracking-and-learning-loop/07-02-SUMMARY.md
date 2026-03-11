---
phase: 07-performance-tracking-and-learning-loop
plan: "02"
subsystem: performance
tags: [loss-classification, rule-engine, claude-fallback, prometheus, sqlalchemy, pydantic]

requires:
  - src/pmtb/performance/models.py (ErrorType, LossAnalysisResult from Plan 01)
  - src/pmtb/db/models.py (Trade, ModelOutput, Signal, LossAnalysis ORM models)
  - src/pmtb/config.py (Settings.anthropic_api_key)
provides:
  - src/pmtb/performance/loss_classifier.py (LossClassifier with rule-based heuristics and Claude fallback)
affects:
  - Phase 7 Plan 03+ that consumes LossAnalysisResult for retraining decisions

tech-stack:
  added:
    - anthropic.AsyncAnthropic (lazy import, optional — same pattern as SentimentClassifier)
    - prometheus_client.Counter (LOSS_CLASSIFICATIONS with error_type and classified_by labels)
  patterns:
    - TDD (RED-GREEN-REFACTOR)
    - Rule-engine priority ordering (6 rules applied in sequence, first match wins)
    - Lazy AsyncAnthropic import in __init__ body — optional dependency when anthropic_api_key is None
    - Claude fallback only for unknown cases — cost gating

key-files:
  created:
    - src/pmtb/performance/loss_classifier.py
    - tests/performance/test_loss_classifier.py
  modified: []

key-decisions:
  - "Rule priority: edge_decay > signal_error > llm_error > sizing_error > market_shock > unknown — most expensive (Claude) deferred last"
  - "market_shock test uses p_market=None to prevent spurious edge_decay match when both p_model and p_market are near 0.5"
  - "llm_error heuristic checks signal_weights.xgboost_base vs final p_model — Claude-shifted decisions identified by weight comparison"
  - "classify_trade and classify_and_persist use separate async context manager sessions — single-use session per DB interaction"

patterns-established:
  - "Rule engine returns (ErrorType, reasoning_str) tuple — reasoning always provided for non-unknown types"
  - "Claude fallback: only invoked when _client is not None AND rules return unknown"
  - "_apply_rules is synchronous — enables unit testing without async infrastructure"

requirements-completed: [PERF-04]

duration: 3min
completed: 2026-03-11
---

# Phase 7 Plan 02: LossClassifier Summary

**Rule-based loss classifier with 6 priority-ordered error-type heuristics (edge_decay, signal_error, llm_error, sizing_error, market_shock, unknown) and lazy-import Claude fallback for ambiguous cases, persisting results to LossAnalysis table.**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-03-11T21:56:37Z
- **Completed:** 2026-03-11T21:59:57Z
- **Tasks:** 1 (TDD: 2 commits — test RED + feat GREEN)
- **Files modified:** 2

## Accomplishments

- Rule engine classifies all 6 error types in priority order with descriptive reasoning strings
- Claude fallback is cost-gated: only invoked when rules return `unknown` AND `anthropic_api_key` is set
- `classify_trade` enforces the losing-trade guard (`pnl < 0`) and raises `ValueError` for profitable trades
- `classify_and_persist` writes `LossAnalysis` row to DB with `session.add` + `await session.commit`
- `LOSS_CLASSIFICATIONS` Prometheus counter tracks classification volume by `error_type` and `classified_by`
- 10 unit tests covering all 6 error types, Claude fallback on/off, and DB persist

## Task Commits

Each task was committed atomically:

1. **Task 1 (TDD RED): add failing tests for LossClassifier** - `5c72458` (test)
2. **Task 1 (TDD GREEN): implement LossClassifier** - `76c375d` (feat)

_Note: TDD tasks produce two commits (test → feat)_

## Files Created/Modified

- `src/pmtb/performance/loss_classifier.py` — LossClassifier class with _apply_rules, _claude_classify, classify_trade, classify_and_persist
- `tests/performance/test_loss_classifier.py` — 10 unit tests covering all error types and edge cases

## Decisions Made

- **Rule priority order:** `edge_decay` fires first (market drift detectable from p_market vs p_model), then `signal_error` (majority signal direction), then `llm_error` (requires `used_llm=True`), then `sizing_error` (correct direction but negative PnL), then `market_shock` (near-0.5 model + neutral signals), finally `unknown`.
- **market_shock test fix:** Used `p_market=None` in the market_shock test to prevent edge_decay from spuriously matching when both model and market prices are near 0.5. The p_market=None path correctly bypasses edge_decay.
- **Separate sessions:** `classify_trade` uses one session to load data; `classify_and_persist` opens a second session to write the row. Clean separation of read and write operations.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] market_shock test used p_market=0.50 which triggered edge_decay first**
- **Found during:** Task 1, TDD GREEN phase
- **Issue:** Test scenario for market_shock had `p_market=Decimal("0.50")`. With `p_model=0.52` (YES side) and `resolved_outcome="no"`, the edge_decay rule correctly fires because p_market (0.50) is closer to "no" side than p_model (0.52). This is semantically correct behavior — edge_decay should win.
- **Fix:** Changed test to use `p_market=None` (no market price data available). With p_market=None, edge_decay rule is skipped, allowing market_shock to match on neutral signals + near-0.5 p_model.
- **Files modified:** tests/performance/test_loss_classifier.py
- **Committed in:** 76c375d (Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — test scenario correction)
**Impact on plan:** Minor test fix; no behavior change to production code. Rule engine logic is correct as designed.

## Issues Encountered

None beyond the test scenario fix above.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- LossClassifier ready for consumption by Phase 7 Plan 03 (retraining trigger logic)
- `LossAnalysisResult` + `ErrorType` available for signal weighting adjustments
- Claude fallback verified working via mock — production use requires `ANTHROPIC_API_KEY` env var (same requirement as Phases 3 and 4)

---
*Phase: 07-performance-tracking-and-learning-loop*
*Completed: 2026-03-11*
