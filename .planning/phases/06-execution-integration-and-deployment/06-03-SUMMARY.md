---
phase: 06-execution-integration-and-deployment
plan: "03"
subsystem: pipeline-orchestrator
tags: [orchestrator, main, pipeline, execution, watchdog, fill-tracker]
dependency_graph:
  requires:
    - "06-01"   # OrderRepository, create_executor with session_factory
    - "06-02"   # FillTracker
    - "05-03"   # DecisionPipeline, launch_watchdog
    - "04-03"   # ProbabilityPipeline
    - "03-04"   # ResearchPipeline
    - "02-02"   # MarketScanner
  provides:
    - PipelineOrchestrator (src/pmtb/orchestrator.py)
    - Wired main.py with full Phase 1-6 stack
  affects:
    - src/pmtb/main.py
tech_stack:
  added:
    - prometheus_client Counter/Histogram for cycle total and duration
  patterns:
    - asyncio.gather for concurrent loop management
    - asyncio.wait_for for stage timeouts and interruptible sleep
    - asyncio.Queue for WS price event injection
    - TDD (RED -> GREEN) for orchestrator
key_files:
  created:
    - src/pmtb/orchestrator.py
    - tests/test_orchestrator.py
  modified:
    - src/pmtb/main.py
decisions:
  - "asyncio.wait_for on stop_event.wait(timeout=scan_interval) for interruptible cycle sleep — exits cleanly on stop signal without waiting full interval"
  - "asyncio.Queue for WS price event injection — decouples WS handler from decision pipeline, non-blocking put_nowait"
  - "_last_predictions / _last_candidates cache avoids re-running full expensive pipeline on WS events"
  - "Decision stage has no timeout — pure synchronous computation wrapped in async evaluate(), fast"
  - "Limit price clamped to [1, 99] to stay within Kalshi cent range"
  - "ResearchPipeline and ProbabilityPipeline constructed manually in main.py — neither has from_settings factory"
metrics:
  duration: "4 min"
  completed_date: "2026-03-10"
  tasks_completed: 2
  files_changed: 3
---

# Phase 6 Plan 03: Pipeline Orchestrator and main.py Wiring Summary

**One-liner:** PipelineOrchestrator with scanner→research→prediction→decision→execution hybrid loop, WS re-evaluation via asyncio.Queue, and fully wired main.py with watchdog startup and clean shutdown.

## Tasks Completed

### Task 1: PipelineOrchestrator with hybrid loop and execution logic (TDD)
- Created `src/pmtb/orchestrator.py` with `PipelineOrchestrator` class
- `run()` gathers three concurrent coroutines: `_full_cycle_loop`, `_ws_reeval_loop`, `fill_tracker.run`
- `_full_cycle_loop` runs `_run_full_cycle()` then sleeps via `asyncio.wait_for(stop_event.wait(), timeout=scan_interval)` — exits cleanly on stop
- `_run_full_cycle()` chains all 4 pipeline stages with `asyncio.wait_for(stage, timeout=stage_timeout_seconds)` per stage; each stage failure logs and aborts cycle
- `_execute_decision()` checks `TradingState("trading_halted")` before placing orders; computes `price = int(p_market * 100) + price_offset_cents`, clamped to [1, 99]
- `_ws_reeval_loop()` drains price event queue (1-second timeout), re-runs `decision.evaluate()` on cached predictions
- `feed_price_event()` public method for WS handler integration
- Prometheus `CYCLE_TOTAL` counter and `CYCLE_DURATION` histogram
- 9 unit tests: all pass (full cycle happy path, no-candidates skip, scanner/research/prediction failures, halt flag, price computation, WS re-eval)

### Task 2: Wire main.py with orchestrator, fill tracker, and watchdog
- Rewrote `src/pmtb/main.py` to wire all Phase 1-6 components
- Added `KalshiWSClient` for fill tracker WebSocket subscriptions
- Updated `create_executor` call with `session_factory=session_factory`
- Built `ResearchPipeline` from individual agents (Reddit, RSS, Trends, Twitter stub) + `QueryConstructor` + `SentimentClassifier` from settings
- Built `ProbabilityPipeline` from `XGBoostPredictor` + `ClaudePredictor` from settings
- Built `DecisionPipeline.from_settings()`, `OrderRepository`, `FillTracker`
- Launched watchdog (daemon=False) before orchestrator
- Replaced `stop_event.wait()` with `await orchestrator.run(stop_event)`
- `finally` block: terminates watchdog, disposes engine

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

**Note on ResearchPipeline and ProbabilityPipeline construction:**
The plan noted "If they don't have `from_settings`, construct manually." Neither has a `from_settings` factory, so agents and predictors were manually constructed from settings fields. This is consistent with plan guidance and the existing construction patterns in the codebase.

## Verification Results

```
uv run pytest tests/test_orchestrator.py -x -v
9 passed in 1.21s

uv run python -c "from pmtb.main import main; print('OK')"
OK
```

## Self-Check: PASSED

Files created/modified:
- `src/pmtb/orchestrator.py` — FOUND
- `tests/test_orchestrator.py` — FOUND
- `src/pmtb/main.py` — FOUND (modified)

Commits:
- `1685242` — test(06-03): add failing orchestrator tests — RED phase
- `245b24d` — feat(06-03): implement PipelineOrchestrator — GREEN phase
- `c8cb083` — feat(06-03): wire main.py with orchestrator, fill tracker, and watchdog
