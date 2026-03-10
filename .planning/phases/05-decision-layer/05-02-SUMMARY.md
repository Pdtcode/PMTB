---
phase: 05-decision-layer
plan: 02
subsystem: decision
tags: [position-tracker, risk-manager, var, drawdown, prometheus, asyncio, sqlalchemy, tdd]

# Dependency graph
requires:
  - phase: 05-01
    provides: TradeDecision, RejectionReason, TradingState, Settings risk fields
  - phase: 04-probability-model
    provides: PredictionResult with p_model and ticker
  - phase: 02-market-scanner
    provides: MarketCandidate with implied_probability

provides:
  - PositionTracker: in-memory async dict synced from DB at startup (tracker.py)
  - RiskManager: sequential 6-gate risk enforcement with auto-hedge (risk.py)
  - RISK_REJECTIONS Prometheus counter with reason label for rejection observability

affects:
  - 05-03 (executor receives approved TradeDecision from full Edge -> Size -> Risk pipeline)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - asyncio.Lock for concurrent dict mutation — all PositionTracker state changes under lock
    - Sequential short-circuit risk gates — cheapest checks first, first rejection wins
    - model_copy(update={...}) for immutable TradeDecision mutation through pipeline
    - 95% parametric VaR (mu - 1.645*sigma) on position dollar values
    - TradingState key-value lookup for halt flag and peak portfolio value
    - Prometheus Counter with reason label for per-rejection-type observability
    - TYPE_CHECKING guard for PredictionResult / MarketCandidate imports in risk.py

key-files:
  created:
    - src/pmtb/decision/tracker.py
    - src/pmtb/decision/risk.py
    - tests/decision/test_tracker.py
    - tests/decision/test_risk.py
  modified: []

key-decisions:
  - "VaR check rejects when mu - 1.645*sigma < -var_limit*portfolio — negative VaR means 95th-percentile tail loss exceeds allowed loss"
  - "Check order: halt_flag -> duplicate -> drawdown -> single_bet -> exposure -> VaR (cheapest to most expensive)"
  - "PositionTracker.total_exposure returns float not Decimal — all risk math uses float, Decimal conversion at boundary only"
  - "selectinload(Position.market) used in load() to avoid lazy-load errors in async SQLAlchemy context"

patterns-established:
  - "PositionTracker as in-memory hot cache: load() at startup, add/remove as executor updates positions"
  - "RiskManager injected with tracker + session_factory — testable with AsyncMock without live DB"
  - "TDD: test file committed as RED, implementation committed as GREEN, both atomic commits"

requirements-completed: [RISK-01, RISK-02, RISK-03, RISK-04, RISK-06, RISK-07, RISK-08]

# Metrics
duration: 4min
completed: 2026-03-10
---

# Phase 5 Plan 02: PositionTracker and RiskManager Summary

**In-memory position dict (asyncio.Lock, DB sync) and 6-gate sequential risk enforcement with 95% parametric VaR, drawdown halt, auto-hedge on edge reversal, and Prometheus rejection counter**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-03-10T23:41:40Z
- **Completed:** 2026-03-10T23:45:03Z
- **Tasks:** 2
- **Files created:** 4

## Accomplishments

- PositionTracker loads all open positions from DB at startup, keyed by market ticker via `selectinload(Position.market)`. Provides `has_position`, `total_exposure`, `add_position`, `remove_position`, `get_all`, `position_count` — all protected by asyncio.Lock.
- RiskManager enforces 6 sequential gates (cheapest first): halt flag, duplicate detection, drawdown computation, max single bet, max exposure, 95% parametric VaR. Short-circuits on first failure.
- `check_hedge` returns a hedge TradeDecision (side='sell') when edge reverses by more than `hedge_shift_threshold` on an open position.
- `RISK_REJECTIONS` Prometheus Counter tracks every rejection with a `reason` label for production cost/risk monitoring.

## Task Commits

Each TDD phase committed atomically:

1. **Task 1 RED: PositionTracker tests** — `8b7c721` (test)
2. **Task 1 GREEN: PositionTracker implementation** — `fd9b19e` (feat)
3. **Task 2 RED: RiskManager tests** — `8e4373a` (test)
4. **Task 2 GREEN: RiskManager implementation** — `dd522b4` (feat)

## Files Created

- `src/pmtb/decision/tracker.py` — PositionTracker with async dict and DB sync
- `src/pmtb/decision/risk.py` — RiskManager with all 6 risk gates and auto-hedge
- `tests/decision/test_tracker.py` — 10 tracker tests (load, has, exposure, add, remove, concurrent)
- `tests/decision/test_risk.py` — 17 risk tests (exposure, single-bet, VaR, drawdown, halt-flag, duplicate, hedge, prometheus)

## Decisions Made

- VaR interpretation: reject when `mu - 1.645*sigma < -var_limit * portfolio_value` — negative VaR represents a 95th-percentile tail loss exceeding the allowed limit
- Check order chosen for cost: halt flag (1 DB get) -> duplicate (dict) -> drawdown (1 DB get) -> single_bet (math) -> exposure (dict sum) -> VaR (list + stdev)
- `PositionTracker.total_exposure()` returns `float` (not `Decimal`) — avoids Decimal/float mixing in all subsequent risk arithmetic
- `selectinload(Position.market)` explicitly used in `load()` — async SQLAlchemy requires eager loading to avoid lazy-load issues in async context

## Deviations from Plan

None — plan executed exactly as written. All test behaviors matched implementation on first attempt. No auto-fixes required.

## Issues Encountered

None — mocking pattern for `async_sessionmaker` (context manager + `session.get`) worked cleanly for all TradingState tests.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Full Edge -> Size -> Risk pipeline is complete and tested
- RiskManager ready for integration into Phase 5 Plan 03 (executor pipeline orchestration)
- PositionTracker.add_position / remove_position ready to be called by executor on fill/close events

---
*Phase: 05-decision-layer*
*Completed: 2026-03-10*
