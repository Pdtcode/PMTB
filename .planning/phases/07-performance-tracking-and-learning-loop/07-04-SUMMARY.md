---
phase: 07-performance-tracking-and-learning-loop
plan: "04"
subsystem: performance
tags: [backtesting, temporal-integrity, same-code-paths, metrics, prometheus]
dependency_graph:
  requires:
    - "07-01: performance models (BacktestResult, BacktestRun DB table)"
    - "04-01: ProbabilityPipeline.predict_one"
    - "05-01: EdgeDetector.evaluate, KellySizer.size"
  provides:
    - "BacktestDataSource: temporal-filtered signal and market snapshot queries"
    - "BacktestEngine: full backtest loop with PERF-08 same-code-path guarantee"
  affects:
    - "Future strategy evaluation workflows"
tech_stack:
  added: []
  patterns:
    - "TDD RED-GREEN: tests written first, implementation makes them pass"
    - "SimpleNamespace for test ORM object construction (avoids SQLAlchemy instrumentation)"
    - "Temporal integrity enforced at SQL layer (created_at <= :as_of)"
    - "Dependency injection: live predictor/sizer instances injected into BacktestEngine"
key_files:
  created:
    - src/pmtb/performance/backtester.py
    - tests/performance/test_backtester.py
  modified: []
decisions:
  - "SimpleNamespace over SQLAlchemy model __new__ for test helpers — SQLAlchemy ORM
    instrumented attributes fail when set on instances created via __new__ without a
    mapped session; SimpleNamespace is identical for attribute access in tests"
  - "Temporal integrity enforced at SQL layer in BacktestDataSource.get_signals and
    get_market_snapshot — not in Python after the query — ensures the WHERE clause
    cannot be accidentally bypassed"
  - "BacktestEngine injects live ProbabilityPipeline, EdgeDetector, KellySizer instances
    (PERF-08) — same code path guarantee is structural, not a comment"
  - "Minimum 10-trade guard returns None metrics (consistent with Phase 07-01 MetricsSnapshot
    design decision)"
  - "Majority-vote sentiment aggregation in build_signal_bundle — simple, robust, no LLM cost"
metrics:
  duration: "5 min"
  completed_date: "2026-03-11"
  tasks_completed: 1
  files_created: 2
  files_modified: 0
---

# Phase 7 Plan 4: BacktestEngine with Temporal Integrity Summary

**One-liner:** BacktestDataSource + BacktestEngine using SQL `created_at <= as_of` temporal filtering and live ProbabilityPipeline/KellySizer injection for lookahead-free historical replay (PERF-07, PERF-08).

## What Was Built

### `src/pmtb/performance/backtester.py`

**BacktestDataSource** — concrete class enforcing temporal integrity on all historical data access:
- `get_signals(market_id, as_of)` — SELECT with `created_at <= as_of` WHERE clause, most-recent-first
- `get_market_snapshot(ticker, as_of)` — reconstructs MarketCandidate-compatible dict; uses most-recent `ModelOutput.p_market` as-of timestamp as implied_probability
- `build_signal_bundle(ticker, market_id, as_of, cycle_id)` — aggregates temporal-filtered signals into `SignalBundle` with majority-vote sentiment per source

**BacktestEngine** — replays resolved trades through live production code paths:
- `run(start_date, end_date, parameters)` — main backtest loop:
  1. Queries resolved trades in date range via SQL
  2. Guards: < 10 trades returns `BacktestResult` with all `None` metrics
  3. For each trade: reconstructs `MarketCandidate` and `SignalBundle` as-of `trade.created_at`
  4. Calls `predictor.predict_one()` — SAME live code path (PERF-08)
  5. Calls `edge_detector.evaluate()` — SAME live code path
  6. If edge detected: calls `sizer.size()` — SAME live code path (PERF-08)
  7. Records `(p_model, actual_outcome, pnl)` for each simulated trade
  8. Computes Brier score, Sharpe ratio, win rate, profit factor
- `persist_result(result)` — writes `BacktestRun` row with all metrics and parameters
- `run_and_persist(start_date, end_date)` — convenience method

**Metric helpers** (pure functions):
- `_compute_brier_score` — `mean((p_model - outcome)^2)`
- `_compute_sharpe_ratio` — `(mean_pnl - 0) / std_pnl`, returns `None` if std=0
- `_compute_win_rate` — wins / total
- `_compute_profit_factor` — gross_profit / abs(gross_loss), `None` if no losses

**Prometheus metrics:** `BACKTEST_RUNS` counter, `BACKTEST_DURATION` histogram

### `tests/performance/test_backtester.py`

9 tests, all green. Key assertions:
- `test_same_code_paths` — asserts `mock_predictor.predict_one.called` (PERF-08 structural guarantee)
- `test_same_sizer_code_paths` — asserts `mock_sizer.size.called`
- `test_temporal_integrity_no_lookahead` — captures `as_of` arguments to `build_signal_bundle`, asserts all <= future signal timestamp
- `test_insufficient_trades_returns_none_metrics` — 5 trades → all `None` metrics
- `test_backtest_persists_result` — `BacktestRun` object in `session.add()` calls

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] SQLAlchemy ORM instrumentation breaks `__new__` pattern in test helpers**
- **Found during:** Task 1 (RED-GREEN transition)
- **Issue:** `Signal.__new__(Signal)` creates instance without running ORM initialization hooks; setting `s.id = ...` raises `AttributeError: 'NoneType' object has no attribute 'set'` via instrumented attribute setter
- **Fix:** Replaced all test helper functions to return `SimpleNamespace` objects instead of ORM model instances. Tests do not need real ORM objects — they only need attribute-compatible dicts-like objects for mock return values.
- **Files modified:** `tests/performance/test_backtester.py`
- **Commit:** a1ecdd6

## Verification

- `python -c "from pmtb.performance.backtester import BacktestEngine, BacktestDataSource; print('OK')"` → OK
- `pytest tests/performance/test_backtester.py -x -q` → 9 passed
- Full test suite: 451 passed, 3 skipped (no regressions)

## Self-Check: PASSED

- [x] `src/pmtb/performance/backtester.py` exists
- [x] `tests/performance/test_backtester.py` exists
- [x] Commits fb379e8 (RED) and a1ecdd6 (GREEN) present in git log
- [x] All 9 backtest tests pass
- [x] Full suite 451 passed, 3 skipped
