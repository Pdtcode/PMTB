---
phase: 05-decision-layer
plan: 03
subsystem: decision
tags: [watchdog, pipeline, multiprocessing, drawdown, prometheus, asyncio, sqlalchemy, tdd]

# Dependency graph
requires:
  - phase: 05-01
    provides: TradeDecision, RejectionReason, EdgeDetector, KellySizer, Settings
  - phase: 05-02
    provides: PositionTracker, RiskManager

provides:
  - watchdog: Independent OS process polling DB for drawdown breaches (RISK-05)
  - DecisionPipeline: Single entry point wiring Edge -> Size -> Risk for Phase 6

affects:
  - 06-execution (calls DecisionPipeline.evaluate to get approved TradeDecisions)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - multiprocessing.Process with daemon=False — survives main process crash
    - Settings.model_dump() serialization across fork boundary — no Pydantic objects passed
    - asyncio.run() inside forked process — clean event loop per process
    - Never-crash watchdog loop — all exceptions caught, logged, and loop continues
    - Shadow filter before any gate — is_shadow=True rejects immediately
    - Sequential short-circuit pipeline — edge -> size -> risk, first rejection wins
    - Hedge decisions appended independently of main pipeline flow
    - DECISION_LATENCY Histogram wraps full batch evaluation

key-files:
  created:
    - src/pmtb/decision/watchdog.py
    - src/pmtb/decision/pipeline.py
    - tests/decision/test_watchdog.py
    - tests/decision/test_pipeline.py
  modified: []

key-decisions:
  - "daemon=False on multiprocessing.Process — watchdog must survive main process crash (RISK-05 requirement)"
  - "Settings.model_dump() to serialize across fork — Pydantic objects cannot cross fork boundary"
  - "Watchdog creates own engine and async_sessionmaker inside run_watchdog — no shared DB connections"
  - "Hedge decisions appended before edge gate runs — hedge check is independent of edge direction"

patterns-established:
  - "Watchdog as independent circuit breaker: create engine inside forked process, poll TradingState, set halt flag"
  - "DecisionPipeline as single entry point: shadow -> hedge -> edge -> size -> risk, all gates injectable"
  - "TDD: RED commit then GREEN commit per task, both atomic"

requirements-completed: [RISK-05]

# Metrics
duration: 4min
completed: 2026-03-10
---

# Phase 5 Plan 03: Watchdog and DecisionPipeline Summary

**Independent OS process circuit breaker (daemon=False, DB polling, fork-safe DB pool) plus DecisionPipeline orchestrator wiring shadow filter, hedge check, Edge -> Size -> Risk in sequence with Prometheus observability**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-03-10T23:47:49Z
- **Completed:** 2026-03-10T23:51:19Z
- **Tasks:** 2
- **Files created:** 4

## Accomplishments

- Watchdog runs as independent OS process (`daemon=False`) — survives main process crash. Polls PostgreSQL every 30 seconds via its own engine and session factory created inside the forked process (never shared across fork boundary). Sets `TradingState(key='trading_halted', value='true')` on drawdown breach. Updates `peak_portfolio_value` when portfolio reaches new highs.
- DecisionPipeline provides a single async `evaluate(predictions, candidates)` entry point for Phase 6. Filters `is_shadow=True` predictions immediately, runs `check_hedge` for open positions, then passes each candidate through EdgeDetector → KellySizer → RiskManager in sequence. Short-circuits on first rejection, preserving the rejection reason. Returns all decisions (approved + rejected) for full observability.
- Full Prometheus coverage: `WATCHDOG_HALT_TRIGGERS`, `WATCHDOG_POLLS`, `DECISION_APPROVALS`, `DECISION_REJECTIONS` (with reason label), `DECISION_LATENCY` histogram.
- 64 tests passing across the entire decision layer (edge, sizer, tracker, risk, watchdog, pipeline).

## Task Commits

Each TDD phase committed atomically:

1. **Task 1 RED: Watchdog tests** — `3f9248c` (test)
2. **Task 1 GREEN: Watchdog implementation** — `33f7517` (feat)
3. **Task 2 RED: Pipeline tests** — `f5aeda8` (test)
4. **Task 2 GREEN: Pipeline implementation** — `23d16b7` (feat)

## Files Created

- `src/pmtb/decision/watchdog.py` — `_check_and_act`, `_watchdog_loop`, `run_watchdog`, `launch_watchdog`
- `src/pmtb/decision/pipeline.py` — `DecisionPipeline` with `evaluate()` and `from_settings()` factory
- `tests/decision/test_watchdog.py` — 5 watchdog tests (breach, no-breach, peak-update, halt-flag, process daemon)
- `tests/decision/test_pipeline.py` — 9 pipeline tests (shadow, approved, edge-reject, kelly-reject, risk-reject, hedge, batch, metrics)

## Decisions Made

- `daemon=False` on `multiprocessing.Process` — watchdog must survive main process crash (RISK-05); `daemon=True` would kill it when the parent exits
- `Settings.model_dump()` serialization before `Process(target=run_watchdog, args=(settings_dict,))` — Pydantic v2 BaseSettings objects cannot be pickled across fork boundary; plain dicts can
- Engine and `async_sessionmaker` created inside `run_watchdog` — avoids sharing SQLAlchemy connection pools across fork boundary (undefined behavior with asyncpg)
- Hedge decisions appended before the edge gate runs — hedge check is about reversing an existing position; it is independent of whether we'd enter a new position

## Deviations from Plan

None — plan executed exactly as written. All test behaviors matched implementation on first attempt.

## Issues Encountered

None — mock pattern for `session.merge()` verification worked cleanly for all watchdog TradingState tests.

## User Setup Required

None — all new code is unit-tested with mocks. Watchdog process integration requires a live PostgreSQL and running `launch_watchdog(settings)` from the main process.

## Phase 5 Completion

All three Phase 5 plans are complete:
- Plan 01: EdgeDetector, KellySizer, models, config (EDGE-01..04, SIZE-01..03, CONFIG-01..03)
- Plan 02: PositionTracker, RiskManager (RISK-01..04, RISK-06..08)
- Plan 03: Watchdog, DecisionPipeline (RISK-05)

Phase 6 (Execution Layer) can now call `DecisionPipeline.evaluate()` to get approved `TradeDecision` objects and call `launch_watchdog(settings)` for the independent circuit breaker.

---
*Phase: 05-decision-layer*
*Completed: 2026-03-10*

## Self-Check: PASSED

- watchdog.py: FOUND
- pipeline.py: FOUND
- test_watchdog.py: FOUND
- test_pipeline.py: FOUND
- Commits 3f9248c, 33f7517, f5aeda8, 23d16b7: FOUND
- 64 decision tests passing: CONFIRMED
