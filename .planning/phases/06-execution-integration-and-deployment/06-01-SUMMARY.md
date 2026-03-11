---
phase: 06-execution-integration-and-deployment
plan: "01"
subsystem: order-persistence
tags: [order-repo, paper-trading, db-persistence, alembic, tdd]
dependency_graph:
  requires:
    - 05-03  # DecisionPipeline (produces orders)
    - db/models.py (Order, Trade, Market SQLAlchemy models)
  provides:
    - OrderRepository (CRUD for Order/Trade lifecycle)
    - Enhanced PaperOrderExecutor (spread-aware fills, DB persistence)
    - is_paper column on orders table
  affects:
    - 06-02  # FillTracker depends on OrderRepository
    - 06-03  # PipelineOrchestrator depends on OrderRepository + executor
tech_stack:
  added:
    - aiosqlite (test dependency, in-memory SQLite for async tests)
  patterns:
    - async session factory pattern (session_factory injected into repository)
    - get-or-create market by ticker (placeholder row for ordering before scanner)
    - TDD red-green for all new code
key_files:
  created:
    - src/pmtb/order_repo.py
    - migrations/versions/004_add_is_paper_column.py
    - tests/test_order_repo.py
    - tests/test_paper.py (rewritten)
  modified:
    - src/pmtb/db/models.py (added is_paper to Order)
    - src/pmtb/paper.py (full rewrite with fill simulation and DB persistence)
    - src/pmtb/executor.py (session_factory parameter added to create_executor)
    - src/pmtb/config.py (4 new Settings fields)
decisions:
  - "get-or-create market pattern in OrderRepository — orders can be persisted before scanner writes market rows"
  - "PaperOrderExecutor session_factory is optional — None = legacy in-memory mode preserves backward compat"
  - "Paper fill_price = requested price (zero slippage) — spread-aware semantics without complicating simulation"
  - "random.uniform(0.5, 1.0) partial fill model — reflects real partial fills without needing orderbook data"
  - "Alembic server_default='false' on is_paper column — handles existing rows in live DB on upgrade"
metrics:
  duration: "4 min"
  completed_date: "2026-03-10"
  tasks_completed: 2
  files_created: 4
  files_modified: 4
---

# Phase 6 Plan 1: Order Persistence and Enhanced Paper Trading Summary

**One-liner:** OrderRepository with full Order/Trade CRUD and PaperOrderExecutor with spread-aware fill simulation persisting to DB with is_paper=True.

## What Was Built

### OrderRepository (`src/pmtb/order_repo.py`)

Full CRUD layer for the Order/Trade lifecycle, injected with an `async_sessionmaker`:

- `create_order(market_ticker, side, quantity, price, kalshi_order_id, is_paper)` — get-or-create Market by ticker (placeholder with `title=ticker, category="unknown"` if not found), insert Order with `status="pending"`, return detached instance
- `update_fill(order_id, fill_price, filled_qty, status)` — update Order fields + create immutable Trade audit row
- `cancel_order(order_id)` — set `status="cancelled"`, update `updated_at`
- `get_by_kalshi_id(kalshi_order_id)` — lookup by external order ID
- `get_stale_orders(timeout_seconds)` — return pending orders older than timeout (for watchdog/FillTracker)

### Order Model Update (`src/pmtb/db/models.py`)

Added `is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)` to the `Order` model.

### Alembic Migration (`migrations/versions/004_add_is_paper_column.py`)

Adds `is_paper` boolean column (server_default='false', NOT NULL) to `orders` table. Chains from `003_add_trading_state`.

### Settings Additions (`src/pmtb/config.py`)

Four new fields under "Execution settings":
- `stale_order_timeout_seconds: int = 900` — for FillTracker/watchdog order cancellation
- `price_offset_cents: int = 1` — limit order price offset from best ask/bid
- `portfolio_value: float = 10000.0` — initial portfolio value for risk calculations
- `stage_timeout_seconds: float = 120.0` — max seconds per pipeline stage

### Enhanced PaperOrderExecutor (`src/pmtb/paper.py`)

Complete rewrite with:
- `__init__(self, session_factory=None)` — optional DB persistence via injected `OrderRepository`
- `_simulate_fill(quantity, price)` — zero slippage, `filled_qty = max(1, int(qty * random.uniform(0.5, 1.0)))`, `status = "filled"|"partial"`
- `place_order` — simulate fill, store in `_orders`, persist create+fill to DB if repo present
- `cancel_order` — update in-memory + DB (via `get_by_kalshi_id` + `cancel_order`)
- `get_positions`, `get_orders` — unchanged from legacy (backward compatible)

### create_executor Update (`src/pmtb/executor.py`)

Added `session_factory=None` parameter; passed through to `PaperOrderExecutor(session_factory=session_factory)`.

## Tests

| File | Tests | Pattern |
|------|-------|---------|
| `tests/test_order_repo.py` | 10 | in-memory SQLite aiosqlite fixture |
| `tests/test_paper.py` | 19 | in-memory SQLite aiosqlite + legacy mode |

All 29 tests pass.

## Deviations from Plan

None — plan executed exactly as written.

## Decisions Made

1. **get-or-create market pattern** — `create_order` creates a placeholder Market row if the ticker isn't in the DB yet. This allows the execution layer to persist orders independently of the scanner completing market enrichment. The placeholder uses `title=ticker, category="unknown", close_time=2099-12-31`.

2. **Optional session_factory** — `PaperOrderExecutor.__init__` accepts `session_factory=None`. When `None`, the executor operates in legacy in-memory mode exactly as before (backward compatible). When provided, it creates an `OrderRepository` internally and persists all orders.

3. **Zero slippage paper fill model** — `fill_price = requested price`. Paper trading semantics do not model bid/ask spread slippage. Partial fills (50-100% of quantity) model incomplete orderbook depth without requiring actual orderbook data.

4. **Alembic `server_default='false'`** — The migration sets `server_default` in addition to the Python-level `default=False`, ensuring existing rows in a live DB get `False` on `upgrade()` without requiring an explicit `UPDATE`.

## Self-Check: PASSED

All expected files found and all task commits verified:
- FOUND: src/pmtb/order_repo.py
- FOUND: src/pmtb/paper.py
- FOUND: migrations/versions/004_add_is_paper_column.py
- FOUND: tests/test_order_repo.py
- FOUND: tests/test_paper.py
- Commit caa01cf: test(06-01) — RED phase OrderRepository tests
- Commit 53288e0: feat(06-01) — GREEN phase OrderRepository + model + settings + migration
- Commit 1d628da: test(06-01) — RED phase PaperOrderExecutor tests
- Commit 136b4fa: feat(06-01) — GREEN phase enhanced PaperOrderExecutor + executor update
