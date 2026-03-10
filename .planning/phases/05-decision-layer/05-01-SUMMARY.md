---
phase: 05-decision-layer
plan: 01
subsystem: decision
tags: [pydantic, kelly-criterion, edge-detection, position-sizing, sqlalchemy, alembic]

# Dependency graph
requires:
  - phase: 04-probability-model
    provides: PredictionResult with p_model probability output
  - phase: 02-market-scanner
    provides: MarketCandidate with implied_probability from orderbook

provides:
  - TradeDecision and RejectionReason Pydantic type contracts (decision/models.py)
  - EdgeDetector: pure-math p_market/edge/EV computation with threshold gating (EDGE-01..04)
  - KellySizer: fractional Kelly f* with alpha scaling and position cap (SIZE-01..03)
  - Settings risk fields: max_exposure, max_single_bet, var_limit, hedge_shift_threshold
  - TradingState SQLAlchemy model and Alembic migration for halt signaling

affects:
  - 05-02 (risk manager will consume TradeDecision from EdgeDetector + KellySizer)
  - 05-03 (executor consumes approved, sized TradeDecision)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Stateless synchronous computation in decision layer — no DB, no async, fully testable
    - TradeDecision as central pipeline data object flowing through edge -> size -> risk
    - RejectionReason enum documents every disqualification reason for post-hoc analysis
    - TDD red-green cycle per task, each stage committed separately

key-files:
  created:
    - src/pmtb/decision/__init__.py
    - src/pmtb/decision/models.py
    - src/pmtb/decision/edge.py
    - src/pmtb/decision/sizer.py
    - migrations/versions/003_add_trading_state.py
    - tests/decision/__init__.py
    - tests/decision/test_edge.py
    - tests/decision/test_sizer.py
  modified:
    - src/pmtb/config.py
    - src/pmtb/db/models.py

key-decisions:
  - "v1 EdgeDetector only supports YES-side bets — NO-side edge detection deferred to future version"
  - "TradingState uses string primary key (not UUID) — singleton rows like 'halted' and 'peak_value' need O(1) lookup by name"
  - "Migration placed in migrations/versions/ (not alembic/versions/) — follows existing project convention"
  - "MarketCandidate has more required fields than plan spec showed — test helpers updated with title/category/event_context/close_time/volume_24h"
  - "KellySizer rejects f*<=0 rather than f*<0 — zero Kelly means breakeven, no positive EV edge"

patterns-established:
  - "Stateless decision classes: __init__ stores config, single public method returns updated TradeDecision copy"
  - "model_copy(update={...}) for immutable decision mutation through pipeline"
  - "pytest.approx(value, rel=1e-3) for all float comparisons in decision tests"

requirements-completed: [EDGE-01, EDGE-02, EDGE-03, EDGE-04, SIZE-01, SIZE-02, SIZE-03]

# Metrics
duration: 20min
completed: 2026-03-10
---

# Phase 5 Plan 01: Decision Layer — EdgeDetector and KellySizer Summary

**Pure-math edge/EV detection (EDGE-01..04) and fractional Kelly sizing (SIZE-01..03) with Pydantic contracts, Settings risk fields, and TradingState Alembic migration**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-03-10T23:18:17Z
- **Completed:** 2026-03-10T23:38:48Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments

- TradeDecision/RejectionReason Pydantic models establish typed pipeline contracts flowing from edge detection through sizing to risk management
- EdgeDetector implements EDGE-01..04: p_market from implied_probability, b=(1-p_mkt)/p_mkt, EV=p*b-(1-p), edge=p-p_mkt, threshold gate (11 tests green)
- KellySizer implements SIZE-01..03: full Kelly f*=(p*b-q)/b, fractional alpha scaling, hard position cap, minimum 1 contract floor (12 tests green)
- Settings gains 4 risk management fields: max_exposure (0.80), max_single_bet (0.05), var_limit (0.20), hedge_shift_threshold (0.03)
- TradingState DB model (key-value halt signaling) with Alembic migration 003

## Task Commits

Each task was committed atomically:

1. **Task 1: Decision types, Settings fields, TradingState, EdgeDetector** - `88cb302` (feat)
2. **Task 2: KellySizer with fractional Kelly and position cap** - `18738a6` (feat)

_Note: TDD tasks — tests written before implementation in each task._

## Files Created/Modified

- `src/pmtb/decision/__init__.py` - Package init
- `src/pmtb/decision/models.py` - TradeDecision and RejectionReason (8 rejection types)
- `src/pmtb/decision/edge.py` - EdgeDetector.evaluate() — EDGE-01..04 math and gating
- `src/pmtb/decision/sizer.py` - KellySizer.size() — SIZE-01..03 Kelly sizing
- `migrations/versions/003_add_trading_state.py` - Alembic migration for trading_state table
- `tests/decision/__init__.py` - Test package init
- `tests/decision/test_edge.py` - 11 edge detection tests
- `tests/decision/test_sizer.py` - 12 Kelly sizing tests
- `src/pmtb/config.py` - Added 4 risk management Settings fields
- `src/pmtb/db/models.py` - Added TradingState SQLAlchemy model

## Decisions Made

- v1 EdgeDetector only supports YES-side bets — NO-side edge (betting against a market) deferred; documented as limitation in edge.py
- TradingState uses string primary key instead of UUID — singleton rows (halt flag, peak value) need name-based lookup
- Migration file placed in `migrations/versions/` following existing project convention (not `alembic/versions/` as plan specced)
- KellySizer rejects f*<=0 (not just f*<0) — zero Kelly means breakeven, which is not a positive EV trade

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] MarketCandidate test helpers updated with missing required fields**

- **Found during:** Task 1 (test_edge.py TDD GREEN phase)
- **Issue:** Plan's interface spec for MarketCandidate showed only 5 fields (ticker, yes_bid, yes_ask, implied_probability, spread) but the actual model requires 8 fields (adds title, category, event_context, close_time, volume_24h)
- **Fix:** Updated `_make_candidate()` test helper to include all required fields with sensible defaults; also fixed the zero-p_market test that used MarketCandidate constructor directly
- **Files modified:** tests/decision/test_edge.py
- **Verification:** All 11 edge tests pass
- **Committed in:** `88cb302` (Task 1 commit)

**2. [Rule 1 - Bug] test_kelly_formula test corrected for cap interaction**

- **Found during:** Task 2 (test_sizer.py TDD GREEN phase)
- **Issue:** test_kelly_formula used max_single_bet=0.05 but expected kelly_f=0.0625 — cap would reduce it to 0.05
- **Fix:** Changed max_single_bet=1.0 in that specific test (no cap) so the formula result is tested without cap interference
- **Files modified:** tests/decision/test_sizer.py
- **Verification:** All 12 sizer tests pass
- **Committed in:** `18738a6` (Task 2 commit)

**3. [Rule 1 - Bug] test_edge_gate_rejects_at_threshold made floating-point safe**

- **Found during:** Task 1 (edge boundary test)
- **Issue:** 0.70 - 0.60 is not exactly 0.10 in IEEE 754 — test that set threshold=0.10 and expected rejection failed because computed edge was 0.09999...
- **Fix:** Test now computes the edge dynamically and sets threshold equal to that computed value to guarantee exact boundary behavior
- **Files modified:** tests/decision/test_edge.py
- **Verification:** Boundary test passes deterministically
- **Committed in:** `88cb302` (Task 1 commit)

---

**Total deviations:** 3 auto-fixed (3 Rule 1 bugs — test correctness issues)
**Impact on plan:** All fixes necessary for test correctness. No production logic changed. No scope creep.

## Issues Encountered

None — all issues were floating-point precision in test expectations and MarketCandidate field completeness, handled automatically per deviation rules.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Decision layer types and gates complete — ready for Phase 5 Plan 02 (RiskManager: drawdown halt, exposure limits, VaR gating)
- EdgeDetector and KellySizer are pure functions, easily composable into the risk pipeline
- TradingState table migration ready for deployment (depends on Alembic running against live DB)

---
*Phase: 05-decision-layer*
*Completed: 2026-03-10*
