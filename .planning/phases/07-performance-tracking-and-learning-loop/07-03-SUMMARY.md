---
phase: 07-performance-tracking-and-learning-loop
plan: 03
subsystem: ml-training
tags: [xgboost, apscheduler, learning-loop, retraining, settlement-polling, kalshi, sklearn]

# Dependency graph
requires:
  - phase: 07-01
    provides: MetricsService.compute_all, recompute_all_windows, MetricsSnapshot, PerformanceMetric table
  - phase: 04-probability-model
    provides: XGBoostPredictor with train/predict/save/load
  - phase: 06-execution-integration-and-deployment
    provides: PipelineOrchestrator asyncio.gather pattern, stop_event interruptible sleep

provides:
  - LearningLoop with settlement polling (GET /portfolio/settlements), cursor pagination
  - resolve_trades: updates Trade.resolved_outcome/resolved_at/pnl, skips void markets
  - compute_recency_weights: exponential decay with configurable half_life_days
  - temporal_train_test_split: chronological split (no lookahead bias)
  - maybe_retrain: hold-out Brier gate — new model replaces live only if improved
  - APScheduler with periodic retraining + daily recompute_all_windows jobs
  - XGBoostPredictor.train() updated to accept optional sample_weight parameter
  - PipelineOrchestrator updated with optional learning_loop parameter
  - Prometheus counters: RETRAINING_EVENTS (trigger/result), SETTLEMENTS_PROCESSED

affects:
  - main.py (wire LearningLoop into PipelineOrchestrator at startup)
  - any future retraining or drift detection work

# Tech tracking
tech-stack:
  added:
    - apscheduler==3.11.2 (AsyncIOScheduler, IntervalTrigger)
    - tzlocal==5.3.1 (apscheduler dependency)
  patterns:
    - TDD red-green: tests committed before implementation, then implementation
    - asyncio.wait_for(stop_event.wait(), timeout=N) for interruptible polling loops
    - Temporal train/test split (never random) to prevent lookahead bias in retraining
    - Hold-out Brier gate: new model only deployed when it beats the current model
    - Recency-weighted samples via exponential decay (lambda = ln(2)/half_life_days)

key-files:
  created:
    - src/pmtb/performance/learning_loop.py
    - tests/performance/test_learning_loop.py
  modified:
    - src/pmtb/prediction/xgboost_model.py (sample_weight parameter)
    - src/pmtb/orchestrator.py (learning_loop optional parameter)

key-decisions:
  - "APScheduler AsyncIOScheduler requires a running event loop — scheduler tests must be async (pytest.mark.asyncio)"
  - "AsyncIOScheduler.shutdown(wait=False) is async — brief await asyncio.sleep(0.05) needed in tests before asserting running=False"
  - "temporal_train_test_split sorts by resolved_at ascending, last 20% is hold-out — never random"
  - "Retraining candidate trained on a temp-path XGBoostPredictor — live predictor state updated only after gate passes"
  - "sample_weight propagated to brier_score_loss as well as fit() for consistent weighted evaluation"
  - "brier_degradation trigger calls _get_baseline_brier() from PerformanceMetric table (most recent 30d row)"

requirements-completed: [PERF-05, PERF-06]

# Metrics
duration: 6min
completed: 2026-03-11
---

# Phase 7 Plan 03: LearningLoop Summary

**XGBoost feedback loop with Kalshi settlement polling, recency-weighted retraining gated by hold-out Brier improvement, APScheduler for weekly retraining and daily metric recomputation, and PipelineOrchestrator wiring**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-11T21:45:00Z
- **Completed:** 2026-03-11T21:51:00Z
- **Tasks:** 2 (each with TDD RED + GREEN commits)
- **Files modified:** 4

## Accomplishments
- Updated `XGBoostPredictor.train()` to accept optional `sample_weight` parameter — backward compatible, passes weights to both raw_clf and CalibratedClassifierCV
- Implemented `LearningLoop` with full settlement polling (cursor pagination), trade resolution, recency-weighted exponential decay, temporal split, and hold-out Brier gate
- APScheduler with two jobs: periodic retraining (configurable interval, default weekly) and daily `recompute_all_windows`
- Wired `LearningLoop` into `PipelineOrchestrator` as optional 4th concurrent task in asyncio.gather

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: sample_weight failing tests** - `1e717f0` (test)
2. **Task 1 GREEN: XGBoostPredictor.train() sample_weight** - `40bde3b` (feat)
3. **Task 2 RED: LearningLoop failing tests** - `bccff7a` (test)
4. **Task 2 GREEN: LearningLoop + orchestrator wiring** - `9b02f4e` (feat)

_Note: TDD tasks have separate test and feat commits per phase_

## Files Created/Modified
- `/Users/petertrinh/Downloads/Computer-Projects/PMTB/src/pmtb/performance/learning_loop.py` - Full LearningLoop class with settlement polling, resolve_trades, recency weights, temporal split, maybe_retrain, APScheduler lifecycle
- `/Users/petertrinh/Downloads/Computer-Projects/PMTB/tests/performance/test_learning_loop.py` - 18 tests covering all LearningLoop behaviors and orchestrator wiring
- `/Users/petertrinh/Downloads/Computer-Projects/PMTB/src/pmtb/prediction/xgboost_model.py` - Added sample_weight parameter to train()
- `/Users/petertrinh/Downloads/Computer-Projects/PMTB/src/pmtb/orchestrator.py` - Added optional learning_loop parameter, updated run() to include it in asyncio.gather

## Decisions Made
- APScheduler AsyncIOScheduler.start() requires a running event loop, so scheduler lifecycle tests must be async (pytest.mark.asyncio) — not sync tests
- AsyncIOScheduler.shutdown(wait=False) is asynchronous in APScheduler 3.x — tests need `await asyncio.sleep(0.05)` before asserting `running=False`
- Retraining candidate is trained on a temporary XGBoostPredictor at a temp path; live predictor's internal model/version state is only swapped after the hold-out Brier gate passes (no in-place mutation before gating)
- `sample_weight` passed to `brier_score_loss` as well as `.fit()` for consistent weighted evaluation
- `_get_baseline_brier()` reads from PerformanceMetric table (most recent 30d brier_score row) rather than keeping in-memory state
- brier_degradation trigger: if no baseline Brier exists yet, retrain is skipped (not enough history)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added sample_weight to brier_score_loss calls**
- **Found during:** Task 1 (GREEN implementation)
- **Issue:** Plan specified passing sample_weight to .fit() but brier_score_loss() also needed it for weighted evaluation consistency
- **Fix:** Added `sample_weight=sample_weight` to both brier_score_loss calls in train()
- **Files modified:** src/pmtb/prediction/xgboost_model.py
- **Verification:** All prediction tests pass
- **Committed in:** 40bde3b

**2. [Rule 1 - Bug] Fixed scheduler test sync/async issue**
- **Found during:** Task 2 (test execution)
- **Issue:** AsyncIOScheduler.start() requires running event loop — sync test methods failed with RuntimeError
- **Fix:** Changed TestSchedulerIntegration tests to @pytest.mark.asyncio; added await asyncio.sleep(0.05) after stop() for shutdown to propagate
- **Files modified:** tests/performance/test_learning_loop.py
- **Verification:** All 18 tests pass
- **Committed in:** 9b02f4e (part of task commit)

---

**Total deviations:** 2 auto-fixed (1 missing critical, 1 bug fix)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered
- APScheduler 3.x AsyncIOScheduler has asynchronous shutdown behavior — `running` flag does not immediately become False after `shutdown(wait=False)` call, requiring a brief sleep in tests

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- LearningLoop is fully implemented and tested
- Requires wiring into main.py at application startup (pass learning_loop to PipelineOrchestrator)
- APScheduler will auto-start when LearningLoop.run() is called
- Phase 7 Plan 03 completes the feedback loop — model will learn from production trades

## Self-Check: PASSED

All files and commits verified present:
- FOUND: src/pmtb/performance/learning_loop.py
- FOUND: tests/performance/test_learning_loop.py
- FOUND: 1e717f0 (test: sample_weight RED)
- FOUND: 40bde3b (feat: sample_weight GREEN)
- FOUND: bccff7a (test: LearningLoop RED)
- FOUND: 9b02f4e (feat: LearningLoop GREEN)

---
*Phase: 07-performance-tracking-and-learning-loop*
*Completed: 2026-03-11*
