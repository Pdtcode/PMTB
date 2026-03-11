---
phase: 07-performance-tracking-and-learning-loop
plan: "01"
subsystem: performance
tags: [metrics, pydantic, orm, alembic, sklearn, prometheus]
dependency_graph:
  requires:
    - src/pmtb/db/models.py (Trade, ModelOutput, PerformanceMetric ORM models)
    - src/pmtb/config.py (Settings base)
  provides:
    - src/pmtb/performance/models.py (MetricsSnapshot, ErrorType, LossAnalysisResult, BacktestResult)
    - src/pmtb/performance/metrics.py (MetricsService)
    - src/pmtb/db/models.py (LossAnalysis, BacktestRun ORM additions)
    - migrations/versions/005_add_loss_analysis_backtest_run.py
  affects:
    - All subsequent Phase 7 plans that consume MetricsSnapshot or MetricsService
tech_stack:
  added:
    - sklearn.metrics.brier_score_loss (Brier score computation)
    - prometheus_client.Counter/Histogram (METRICS_COMPUTED, METRICS_COMPUTE_DURATION)
  patterns:
    - TDD (RED-GREEN-REFACTOR) for both tasks
    - asyncio.Lock for concurrent write prevention
    - Minimum sample guard (< 10 -> None) for all metrics
    - Dual-window computation (alltime + 30d rolling)
    - Idempotent recompute via delete-then-insert pattern
key_files:
  created:
    - src/pmtb/performance/__init__.py
    - src/pmtb/performance/models.py
    - src/pmtb/performance/metrics.py
    - migrations/versions/005_add_loss_analysis_backtest_run.py
    - tests/performance/__init__.py
    - tests/performance/test_models.py
    - tests/performance/test_metrics.py
  modified:
    - src/pmtb/db/models.py (added LossAnalysis, BacktestRun, Trade.loss_analyses relationship)
    - src/pmtb/config.py (added 5 Phase 7 Settings fields)
decisions:
  - "asyncio.Lock used to prevent concurrent metric writes (anti-pattern from Research)"
  - "Minimum sample guard of 10 trades prevents misleading metrics from small datasets"
  - "recompute_all_windows uses delete-then-insert (not upsert) for strict idempotency"
  - "NaN Sharpe (zero-std) skipped during persist — not persisted to PerformanceMetric"
  - "Mock async context managers required explicit _make_async_cm_factory helper (AsyncMock protocol mismatch)"
metrics:
  duration: "5 min"
  completed_date: "2026-03-11"
  tasks_completed: 2
  files_created: 7
  files_modified: 2
  tests_added: 35
---

# Phase 7 Plan 01: Performance Module Foundation Summary

**One-liner:** Brier/Sharpe/win-rate/profit-factor MetricsService with sklearn, dual alltime+30d windows, asyncio.Lock writes, and alembic migration for LossAnalysis and BacktestRun tables.

## What Was Built

### Task 1: Type contracts, DB models, migration, and Settings fields

Created the `src/pmtb/performance/` package with all Pydantic type contracts:

- **ErrorType(str, Enum):** 6 values — `edge_decay`, `signal_error`, `llm_error`, `sizing_error`, `market_shock`, `unknown`
- **MetricsSnapshot:** period-tagged snapshot with brier_score, sharpe_ratio, win_rate, profit_factor (all nullable), trade_count, computed_at
- **LossAnalysisResult:** trade_id, error_type, reasoning (nullable), classified_by ("rules"/"claude")
- **BacktestResult:** start/end dates, trade_count, all 4 metrics (nullable), parameters dict

Added to `src/pmtb/db/models.py`:

- **LossAnalysis:** `loss_analyses` table with UUID PK, trade_id FK, error_type, reasoning, classified_by, created_at; index on trade_id
- **BacktestRun:** `backtest_runs` table with UUID PK, run_at, date range, metrics, parameters JSON; index on run_at
- **Trade.loss_analyses:** one-to-many relationship added

Added 5 Settings fields with correct defaults (brier_degradation_threshold=0.05, retraining_schedule_hours=168, rolling_window_days=30, retraining_half_life_days=30.0, settlement_poll_interval_seconds=60).

Created Alembic migration 005 chained from 004 — creates both tables with FK constraints and indexes.

### Task 2: MetricsService with dual-window computation, persistence, and daily recompute

Created `src/pmtb/performance/metrics.py` with MetricsService:

- **compute_brier:** sklearn brier_score_loss, None when < 10 samples
- **compute_sharpe:** annualized mean/std * sqrt(252), NaN guard on zero/constant PnL
- **compute_win_rate:** wins/total, None when < 10 total
- **compute_profit_factor:** gross_profit/gross_loss, inf when no losses, None when < 10
- **compute_all:** async DB query with rolling window filter, returns MetricsSnapshot
- **persist_metrics:** writes non-None, non-NaN metrics to PerformanceMetric table
- **recompute_all_windows:** delete stale rows then recompute alltime and 30d (idempotent daily trigger)
- asyncio.Lock prevents concurrent writes
- Prometheus METRICS_COMPUTED counter and METRICS_COMPUTE_DURATION histogram

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] AsyncMock session_factory protocol mismatch in tests**
- **Found during:** Task 2, TDD GREEN phase
- **Issue:** pytest-asyncio's `AsyncMock()` used as a `session_factory` returns a coroutine, not an async context manager. `async with self._session_factory() as session:` raised `TypeError: 'coroutine' object does not support the asynchronous context manager protocol`.
- **Fix:** Added `_make_async_cm_factory(session_mock)` helper in test file that creates a `MagicMock` returning a proper async context manager. Updated all 3 async test classes to use this pattern.
- **Files modified:** tests/performance/test_metrics.py
- **Commit:** c445a2d

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| src/pmtb/performance/models.py | FOUND |
| src/pmtb/performance/metrics.py | FOUND |
| migrations/versions/005_add_loss_analysis_backtest_run.py | FOUND |
| tests/performance/test_models.py | FOUND |
| tests/performance/test_metrics.py | FOUND |
| Commit 777b5ed (Task 1) | FOUND |
| Commit c445a2d (Task 2) | FOUND |
| All 35 tests pass | PASSED |
