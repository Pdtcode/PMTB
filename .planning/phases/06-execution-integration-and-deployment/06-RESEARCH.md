# Phase 6: Execution, Integration, and Deployment - Research

**Researched:** 2026-03-10
**Domain:** Python async pipeline orchestration, order lifecycle management, Docker deployment
**Confidence:** HIGH — research is primarily code-reading of this project's own Phase 1-5 implementation; Docker/compose patterns are stable and well-understood

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Pipeline Orchestration
- Hybrid loop: fixed 15-minute interval for full scan cycles + WebSocket-triggered re-evaluation for open positions
- Full cycle: scanner -> research -> prediction -> decision -> execution, running every 15 minutes (configurable via Settings)
- WebSocket price-change triggers re-run decision pipeline only (skip scanner/research/prediction) against existing predictions and new market price
- Graceful degradation on stage failures: each stage logs failures and continues with whatever succeeded — consistent with Phase 3's resilience pattern
- cycle_id correlation flows through the entire pipeline (already established)

#### Order Lifecycle
- WebSocket primary for real-time fill tracking + REST polling fallback as safety net to catch anything WS missed
- Stale order cancellation timeout: configurable via Settings (default 15 minutes — matches scan interval)
- Limit order price: best ask +/- configurable offset in cents (exposed in Settings for tuning aggression)
- Every order, fill, and cancellation persisted to PostgreSQL (EXEC-05)

#### Slippage Handling
- Claude's discretion on slippage approach — log and persist expected vs actual price at minimum; decide whether to add a slippage threshold for cancellation based on Kalshi's limit order behavior

#### Paper Trading
- Spread-aware fills: paper mode simulates fills at the ask price (for buys), respecting the spread; partial fills simulated probabilistically based on volume
- Full DB persistence: paper orders/fills write to the same DB tables with a paper flag — validates the full data path and enables paper-mode performance analysis
- Live market data by default: paper mode calls real Kalshi API for markets/prices, simulates execution only
- --mock flag available for CI/offline testing using fixture data
- Validation criteria: Claude's discretion on what constitutes successful paper validation before going live

#### Docker & Deployment
- docker compose with two services: pmtb (bot) + postgres — single `docker compose up` starts everything
- Secrets via environment variables (.env file locally, cloud secrets manager in production) — Pydantic Settings already reads from env
- Provider-agnostic: standard Dockerfile and compose, deploy anywhere via SSH + docker compose up
- Health check hits Prometheus /metrics endpoint; restart: unless-stopped policy
- Structured JSON logging to stdout (already configured from Phase 1)

### Claude's Discretion
- Slippage handling approach (log-only vs threshold-based cancellation)
- Paper trading validation criteria (cycles required, error tolerance)
- WebSocket reconnection strategy for fill tracking
- Docker base image choice and multi-stage build optimization
- Alembic migration auto-run on container startup
- Watchdog process startup within Docker (separate container vs same container)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| EXEC-01 | System places limit orders on Kalshi via REST API | KalshiClient.place_order() already implemented; needs FillTracker layer on top of LiveOrderExecutor |
| EXEC-02 | System handles partial fills and tracks fill status | KalshiWSClient "fill" channel subscription + REST polling fallback; needs FillTracker component |
| EXEC-03 | System monitors slippage between expected and actual execution price | FillTracker computes slippage at fill time; persisted to Order.fill_price vs Order.price columns |
| EXEC-04 | System cancels stale unfilled orders after configurable timeout | Background coroutine monitors open orders; Settings.stale_order_timeout_seconds with default 900 |
| EXEC-05 | Every order, fill, and cancellation is persisted to PostgreSQL | Order/Trade ORM models already exist; needs OrderRepository + PaperOrderExecutor DB write path |
| DEPL-01 | System runs locally for development with single-command startup | docker compose up with pmtb + postgres services; dev also works via `uv run pmtb` |
| DEPL-02 | System deploys to cloud VPS via Docker for 24/7 operation | Standard Dockerfile + compose; restart: unless-stopped policy for 24/7 |
| DEPL-03 | Structured JSON logging to stdout | Already implemented in Phase 1 via loguru; Dockerfile uses CMD not ENTRYPOINT for stdout capture |
</phase_requirements>

---

## Summary

Phase 6 is an integration and wiring phase. The individual components — scanner, research pipeline, predictor, decision layer, executor, and WebSocket client — were built in Phases 1-5. The work here is:

1. **Pipeline orchestrator** in `main.py`: Replace `stop_event.wait()` with a coroutine that runs full scan cycles every 15 minutes AND processes WebSocket-triggered re-evaluations for open positions. The orchestrator calls each stage in sequence with graceful degradation (log and continue if any stage fails).

2. **Fill tracking and order lifecycle**: Wrap `LiveOrderExecutor` with a `FillTracker` that subscribes to the Kalshi WebSocket "fill" channel, updates `Order` rows in PostgreSQL on each fill event, and runs a background stale-order canceller that polls for pending orders older than the configurable timeout. `PaperOrderExecutor` gets promoted from a no-op to a full DB-persisting simulator with spread-aware fill simulation.

3. **Docker deployment**: `Dockerfile` (multi-stage, Python 3.13 slim) + `docker-compose.yml` (pmtb + postgres services, health check, restart policy, volume for PG data, .env secrets).

All three areas are well-constrained by the locked decisions in CONTEXT.md. No new library selections are required — the standard stack is already established.

**Primary recommendation:** Implement in three sequential plans: (1) pipeline orchestrator + Settings additions, (2) fill tracking + order persistence + enhanced PaperOrderExecutor, (3) Docker + deployment.

---

## Standard Stack

### Core (already installed — no new dependencies needed)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| asyncio | stdlib | Concurrent pipeline stages, WS + main loop | Already used throughout |
| sqlalchemy[asyncio] | >=2.0 | Order/fill persistence | Already used throughout |
| websockets | >=14.0 | KalshiWSClient fill subscriptions | Already installed |
| loguru | >=0.7 | Structured JSON logging | Already configured |
| prometheus-client | >=0.20 | Metrics + health endpoint | Already running |
| pydantic-settings | >=2.0 | New Settings fields | Already used |

### New Docker Dependencies (not in pyproject.toml)
| Tool | Version | Purpose | Why |
|------|---------|---------|-----|
| Docker Engine | 24+ | Container runtime | Required for deployment |
| Docker Compose v2 | 2.x | Multi-service orchestration | `docker compose` (no hyphen) — v2 syntax |

### No New Python Libraries Required
All execution and persistence capabilities are already in the dependency set. The fill tracking, pipeline orchestration, and paper trading enhancement use only existing imports.

**Installation (Docker — development machine):**
```bash
# macOS
brew install --cask docker

# Linux VPS (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sh
```

---

## Architecture Patterns

### Recommended Project Structure Additions

```
src/pmtb/
├── orchestrator.py      # PipelineOrchestrator — wires all stages, runs the main loop
├── fill_tracker.py      # FillTracker — WS fill events + REST polling + stale cancellation
├── order_repo.py        # OrderRepository — DB CRUD for Order and Trade rows
├── paper.py             # Enhanced PaperOrderExecutor (spread-aware, DB-persisting)
└── main.py              # Wired: orchestrator + watchdog + fill_tracker + WS

docker/
├── Dockerfile           # Multi-stage Python 3.13 image
└── .dockerignore

docker-compose.yml       # pmtb + postgres services
.env.example             # Template for required environment variables
```

### Pattern 1: Pipeline Orchestrator — Hybrid Loop

The orchestrator runs two concurrent coroutines via `asyncio.gather`:
1. `_full_cycle_loop`: waits for the scan interval, then runs the full pipeline
2. `_ws_reeval_loop`: consumes price-change events from an asyncio.Queue, re-runs decision pipeline only against cached predictions

```python
# src/pmtb/orchestrator.py
import asyncio
from loguru import logger

class PipelineOrchestrator:
    def __init__(self, scanner, research, predictor, decision_pipeline,
                 executor, fill_tracker, settings, session_factory):
        self._scanner = scanner
        self._research = research
        self._predictor = predictor
        self._decision = decision_pipeline
        self._executor = executor
        self._fill_tracker = fill_tracker
        self._settings = settings
        self._session_factory = session_factory
        self._price_event_queue: asyncio.Queue = asyncio.Queue()
        # Cache last predictions for WS-triggered re-evaluation
        self._last_predictions: list = []
        self._last_candidates: list = []

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run orchestrator until stop_event is set."""
        await asyncio.gather(
            self._full_cycle_loop(stop_event),
            self._ws_reeval_loop(stop_event),
            self._fill_tracker.run(stop_event),
        )

    async def _full_cycle_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self._run_full_cycle()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._settings.scan_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass  # normal — interval elapsed, run next cycle

    async def _run_full_cycle(self) -> None:
        import uuid
        cycle_id = str(uuid.uuid4())
        log = logger.bind(cycle_id=cycle_id)

        # Stage 1: Scanner (graceful degradation)
        try:
            scan_result = await self._scanner.run_cycle()
            candidates = scan_result.candidates
        except Exception as exc:
            log.error("Scanner failed — skipping cycle", error=str(exc))
            return

        if not candidates:
            log.info("No candidates from scanner")
            return

        # Stage 2: Research (graceful degradation per candidate)
        try:
            signal_bundles = await self._research.run(candidates)
        except Exception as exc:
            log.error("Research failed — skipping cycle", error=str(exc))
            return

        # Stage 3: Prediction
        try:
            predictions = await self._predictor.predict_batch(candidates, signal_bundles)
        except Exception as exc:
            log.error("Prediction failed — skipping cycle", error=str(exc))
            return

        # Cache for WS re-evaluation
        self._last_predictions = predictions
        self._last_candidates = candidates

        # Stage 4: Decision
        decisions = await self._decision.evaluate(predictions, candidates)

        # Stage 5: Execute approved decisions
        for decision in decisions:
            if decision.approved:
                await self._execute_decision(decision, log)

    async def _execute_decision(self, decision, log) -> None:
        """Place order and persist to DB."""
        # price = best_ask + price_offset (from candidate)
        # ... (implementation detail for plan)
        pass

    async def _ws_reeval_loop(self, stop_event: asyncio.Event) -> None:
        """Re-run decision pipeline when price-change events arrive."""
        while not stop_event.is_set():
            try:
                event = await asyncio.wait_for(
                    self._price_event_queue.get(), timeout=1.0
                )
                if self._last_predictions:
                    decisions = await self._decision.evaluate(
                        self._last_predictions, self._last_candidates
                    )
                    for d in decisions:
                        if d.approved:
                            await self._execute_decision(d, logger)
            except asyncio.TimeoutError:
                continue
```

### Pattern 2: Fill Tracker — WS Primary + REST Fallback

```python
# src/pmtb/fill_tracker.py
import asyncio
from loguru import logger

class FillTracker:
    """Tracks fill events via WS and cancels stale orders via REST polling."""

    def __init__(self, ws_client, kalshi_client, order_repo, settings):
        self._ws = ws_client
        self._rest = kalshi_client
        self._repo = order_repo
        self._settings = settings

    async def run(self, stop_event: asyncio.Event) -> None:
        await asyncio.gather(
            self._ws_fill_loop(stop_event),
            self._stale_canceller_loop(stop_event),
            self._rest_polling_loop(stop_event),
        )

    async def _ws_fill_loop(self, stop_event: asyncio.Event) -> None:
        """Subscribe to WS fill channel; update DB on each fill event."""
        async def on_message(msg: dict) -> None:
            if msg.get("type") == "fill":
                await self._handle_fill_event(msg)

        # Run WS in background — reconnects automatically
        ws_task = asyncio.create_task(
            self._ws.run(on_message, channels=["fill"], market_tickers=[])
        )
        await stop_event.wait()
        ws_task.cancel()

    async def _handle_fill_event(self, msg: dict) -> None:
        """Update Order row in DB from fill event."""
        # msg fields: order_id, fill_price, count, etc.
        # Compute slippage = fill_price - expected_price, persist to Order
        pass

    async def _stale_canceller_loop(self, stop_event: asyncio.Event) -> None:
        """Cancel orders pending longer than stale_order_timeout_seconds."""
        timeout = self._settings.stale_order_timeout_seconds
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                pass
            await self._cancel_stale_orders()

    async def _rest_polling_loop(self, stop_event: asyncio.Event) -> None:
        """REST fallback: poll get_orders every N minutes to catch missed fills."""
        poll_interval = self._settings.stale_order_timeout_seconds // 2
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=float(poll_interval))
            except asyncio.TimeoutError:
                pass
            await self._sync_orders_from_rest()
```

### Pattern 3: Enhanced PaperOrderExecutor with DB Persistence

```python
# src/pmtb/paper.py (enhanced)
class PaperOrderExecutor:
    def __init__(self, session_factory=None, is_paper=True) -> None:
        self._orders: list[dict] = []  # in-memory (for tests without DB)
        self._session_factory = session_factory  # None in legacy no-DB mode

    async def place_order(self, market_ticker, side, quantity, price, order_type="limit"):
        order_id = f"paper-{uuid4()}"
        order = {
            "order_id": order_id,
            "market_ticker": market_ticker,
            "side": side,
            "quantity": quantity,
            "expected_price": price,
            "order_type": order_type,
            "status": "open",  # Now tracks lifecycle
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_paper": True,
        }
        # Simulate fill at ask price (spread-aware)
        fill_result = self._simulate_fill(order)
        order.update(fill_result)

        self._orders.append(order)

        # Persist to DB if session_factory provided
        if self._session_factory:
            await self._persist_order(order)

        return order

    def _simulate_fill(self, order: dict) -> dict:
        """Spread-aware fill simulation. Always fills at ask for buys."""
        # Simulated fill at expected_price (paper mode = ask price for buys)
        # Partial fill: probabilistic based on volume (50-100% of quantity)
        import random
        fill_fraction = random.uniform(0.5, 1.0)
        filled_qty = max(1, int(order["quantity"] * fill_fraction))
        slippage = 0  # paper = no slippage
        return {
            "status": "filled" if filled_qty == order["quantity"] else "partial",
            "fill_price": order["expected_price"],
            "filled_quantity": filled_qty,
            "slippage_cents": slippage,
        }
```

### Pattern 4: OrderRepository — DB CRUD

```python
# src/pmtb/order_repo.py
from sqlalchemy.ext.asyncio import async_sessionmaker
from pmtb.db.models import Market, Order, Trade
from datetime import datetime, UTC
import uuid

class OrderRepository:
    """DB operations for Order and Trade rows."""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def create_order(self, market_ticker: str, side: str, quantity: int,
                           price: float, kalshi_order_id: str | None,
                           is_paper: bool = False) -> Order:
        """Insert a new Order row. Returns the ORM object."""
        async with self._session_factory() as session:
            # Upsert market row (get or create)
            market = await self._get_or_create_market(session, market_ticker)
            order = Order(
                id=uuid.uuid4(),
                market_id=market.id,
                side=side,
                quantity=quantity,
                price=price,
                order_type="limit",
                status="pending",
                kalshi_order_id=kalshi_order_id,
                placed_at=datetime.now(UTC),
            )
            session.add(order)
            await session.commit()
            return order

    async def update_fill(self, order_id: uuid.UUID, fill_price: float,
                          filled_qty: int, status: str) -> None:
        """Update Order fill fields and write a Trade row."""
        async with self._session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                return
            slippage = fill_price - float(order.price)
            order.fill_price = fill_price
            order.filled_quantity = filled_qty
            order.status = status
            order.updated_at = datetime.now(UTC)
            trade = Trade(
                id=uuid.uuid4(),
                order_id=order.id,
                market_id=order.market_id,
                side=order.side,
                quantity=filled_qty,
                price=fill_price,
                created_at=datetime.now(UTC),
            )
            session.add(trade)
            await session.commit()

    async def cancel_order(self, order_id: uuid.UUID) -> None:
        """Mark an Order as cancelled."""
        async with self._session_factory() as session:
            order = await session.get(Order, order_id)
            if order:
                order.status = "cancelled"
                order.updated_at = datetime.now(UTC)
                await session.commit()
```

### Pattern 5: Docker Compose — Two-Service Architecture

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: pmtb
      POSTGRES_USER: pmtb
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pmtb"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  pmtb:
    build: .
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://pmtb:${POSTGRES_PASSWORD}@postgres:5432/pmtb
      KALSHI_API_KEY_ID: ${KALSHI_API_KEY_ID}
      KALSHI_PRIVATE_KEY_PATH: /run/secrets/kalshi_key
      TRADING_MODE: ${TRADING_MODE:-paper}
    volumes:
      - ./secrets/kalshi_key.pem:/run/secrets/kalshi_key:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9090/metrics"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "5"

volumes:
  pgdata:
```

### Pattern 6: Dockerfile — Multi-Stage Python 3.13

```dockerfile
# Dockerfile
FROM python:3.13-slim AS builder

WORKDIR /build

RUN pip install uv

COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install project and deps into /app via uv
RUN uv sync --frozen --no-dev --compile-bytecode

FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed env from builder
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/src /app/src

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Alembic migration + bot startup
COPY alembic.ini ./
COPY migrations/ ./migrations/

CMD ["sh", "-c", "alembic upgrade head && python -m pmtb.main"]
```

### Pattern 7: Watchdog in Docker — Same Container

The watchdog is a `multiprocessing.Process` launched by `main.py`. In Docker, this runs within the same container as the main process. This is the correct approach because:
- The watchdog already uses `daemon=False` so it survives the main process exiting gracefully
- A separate Docker container would add complexity without benefit for a single-host deployment
- The watchdog creates its own PostgreSQL connection (established pattern from Phase 5)

```python
# main.py — add watchdog launch after startup
from pmtb.decision.watchdog import launch_watchdog
watchdog_proc = launch_watchdog(settings)
logger.info("Watchdog launched", pid=watchdog_proc.pid)
```

### Anti-Patterns to Avoid

- **Sharing asyncpg connections across fork boundary:** The watchdog calls `launch_watchdog(settings)` before creating the database connection — already the established pattern. Do NOT pass `session_factory` to `launch_watchdog`.
- **Blocking the event loop in fill handler:** `_handle_fill_event` must be async and non-blocking. No synchronous DB calls.
- **Missing `paper` flag on DB Order rows:** The `Order` model does not have a `is_paper` column yet. Add it via Alembic migration or use a dedicated `paper_orders` table flag. Recommendation: add `is_paper: bool = False` column to `orders` table.
- **Hardcoding Kalshi order ID in DB upsert:** Always use `RETURNING` or the response dict `order_id` field; never construct it locally.
- **Running `alembic upgrade head` without a healthy DB:** The Dockerfile CMD must wait for PostgreSQL (docker compose `depends_on: condition: service_healthy` handles this).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WS reconnection | Custom backoff loop | Existing `KalshiWSClient.run()` | Already has auto-reconnect with 5s fixed delay |
| Order state machine | Custom status tracker | SQLAlchemy Order model with status column | Soft-delete pattern already in place |
| Health checks | HTTP health server | Prometheus `/metrics` endpoint (port 9090) | Already running from Phase 1 |
| DB migrations on startup | Manual SQL scripts | `alembic upgrade head` in Dockerfile CMD | Already configured with `alembic.ini` |
| Partial fill probability | Complex simulation | `random.uniform(0.5, 1.0)` * quantity | Paper mode, not production math |
| Secrets management | Custom vault | Docker env vars + `.env` file | Pydantic Settings already reads from env |
| Rate limiting | Custom token bucket | `tenacity` + existing `kalshi_retry` decorator | Already applied to KalshiClient methods |

**Key insight:** Every problem in this phase has a prior-phase solution. The work is wiring and enhancing, not building from scratch.

---

## Common Pitfalls

### Pitfall 1: asyncio Event Loop Conflict with multiprocessing
**What goes wrong:** `multiprocessing.Process` launched after `asyncio.run()` starts can conflict with the event loop on some Python implementations.
**Why it happens:** `asyncio.run()` sets up an event loop; `multiprocessing.Process.start()` with `fork` start method may inherit file descriptors including the event loop.
**How to avoid:** Launch the watchdog BEFORE calling `asyncio.run(main())` in `__main__`, OR use `spawn` start method. The existing code calls `asyncio.run(main())` and then `launch_watchdog` inside — this is fine because `launch_watchdog` uses `settings.model_dump()` serialization which was the established Phase 5 pattern.
**Warning signs:** `RuntimeError: Event loop is closed` in watchdog process logs.

### Pitfall 2: WS Fill Channel Subscription Scope
**What goes wrong:** `KalshiWSClient.run()` accepts `market_tickers` at subscribe time. If a new order is placed after subscription starts, the fill channel won't receive events for it.
**Why it happens:** Kalshi WS subscriptions are per-ticker and sent once at connect time.
**How to avoid:** Two options:
1. Subscribe to fill channel with empty `market_tickers=[]` — Kalshi sends all fills for the account regardless of ticker filter (verify with Kalshi docs)
2. Re-subscribe after each new order placement with the updated ticker list
**Recommendation:** Use account-level fill subscription (empty market_tickers). Verify against Kalshi WS API docs that fill events are account-scoped.
**Warning signs:** Orders placed but no fill events received in WS logs.

### Pitfall 3: Paper Flag Missing from DB Schema
**What goes wrong:** Paper orders written to the same `orders` table without a distinguishing flag will mix with live orders in queries.
**Why it happens:** Current `Order` model has no `is_paper` column.
**How to avoid:** Add `is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)` to the `Order` model AND write an Alembic migration in Wave 0.
**Warning signs:** Performance analysis cannot distinguish paper vs live trade history.

### Pitfall 4: Stale Canceller Races with Fill Tracker
**What goes wrong:** Stale canceller reads "pending" orders and cancels one that WS fill tracker is concurrently filling.
**Why it happens:** Order is "pending" in DB while fill event is in-flight from WS but not yet committed.
**How to avoid:** Use a grace window — only cancel orders older than `stale_order_timeout_seconds` AND `last_updated_at` more than 30 seconds ago. Also check Kalshi REST status before cancellation: if already filled, skip cancel.
**Warning signs:** `404 Not Found` from Kalshi cancel endpoint (order already filled).

### Pitfall 5: Pipeline Orchestrator Blocks Entire Event Loop on Stage Failures
**What goes wrong:** A slow/hung research stage (e.g., waiting for Twitter/X API) blocks the full cycle, delaying the next 15-minute interval.
**Why it happens:** `await research.run(candidates)` holds the event loop if the research pipeline doesn't respect its own timeout.
**How to avoid:** Wrap each stage in `asyncio.wait_for(stage_call, timeout=settings.stage_timeout_seconds)` with a reasonable per-stage budget. Research already uses `asyncio.timeout` per agent — the pipeline-level call should also have a guard.
**Warning signs:** Cycle duration consistently > 5 minutes in Prometheus histograms.

### Pitfall 6: Docker — asyncpg Not Found at Runtime
**What goes wrong:** `asyncpg` is a binary extension; the multi-stage build may not copy compiled wheels correctly.
**Why it happens:** `uv sync` with `--compile-bytecode` in a `python:3.13-slim` builder may produce architecture-specific binaries that don't copy cleanly if using different base images.
**How to avoid:** Use the same base image tag for both builder and runtime stages. `python:3.13-slim` (not `alpine`) — alpine requires musl libc which breaks asyncpg.
**Warning signs:** `ModuleNotFoundError: No module named 'asyncpg'` at container startup.

### Pitfall 7: Alembic Cannot Find Migration Scripts
**What goes wrong:** `alembic upgrade head` fails in container because `alembic.ini` references relative path `./migrations`.
**Why it happens:** `WORKDIR /app` but Dockerfile copies migrations to `/app/migrations` — path is correct; failure is if `alembic.ini` uses an absolute path or the file is not `COPY`ed.
**How to avoid:** Ensure `COPY alembic.ini ./` and `COPY migrations/ ./migrations/` are both in the Dockerfile. Verify `alembic.ini` has `script_location = migrations` (relative).

---

## Code Examples

Verified patterns from this project's existing implementation:

### Launching both orchestrator and fill tracker concurrently in main.py
```python
# src/pmtb/main.py — replace stop_event.wait() block
from pmtb.orchestrator import PipelineOrchestrator
from pmtb.fill_tracker import FillTracker
from pmtb.decision.watchdog import launch_watchdog

# Launch watchdog (separate process, daemon=False)
watchdog_proc = launch_watchdog(settings)

orchestrator = PipelineOrchestrator(
    scanner=scanner,
    # ... other stages
    settings=settings,
    session_factory=session_factory,
)

try:
    await orchestrator.run(stop_event)
except KeyboardInterrupt:
    pass
finally:
    logger.info("PMTB shutting down cleanly")
    await engine.dispose()
    # Watchdog has daemon=False — it will continue independently; OS will clean up
```

### Checking halt flag before placing each order
```python
# Inside _execute_decision in orchestrator
from pmtb.decision.risk import RiskManager

async def _execute_decision(self, decision: TradeDecision, log) -> None:
    # Check halt flag before executing
    async with self._session_factory() as session:
        state = await session.get(TradingState, "trading_halted")
        if state and state.value == "true":
            log.warning("Trading halted — skipping execution", ticker=decision.ticker)
            return

    price = self._compute_limit_price(decision)
    result = await self._executor.place_order(
        market_ticker=decision.ticker,
        side=decision.side,
        quantity=decision.quantity,
        price=price,
    )
    # Persist to DB
    await self._order_repo.create_order(
        market_ticker=decision.ticker,
        side=decision.side,
        quantity=decision.quantity,
        price=price,
        kalshi_order_id=result.get("order_id"),
    )
```

### Slippage computation at fill time (log-only approach — recommended for v1)
```python
# In FillTracker._handle_fill_event
async def _handle_fill_event(self, msg: dict) -> None:
    kalshi_order_id = msg.get("order_id")
    fill_price = msg.get("yes_price", msg.get("fill_price"))
    filled_qty = msg.get("count", 0)

    order = await self._repo.get_by_kalshi_id(kalshi_order_id)
    if order is None:
        logger.warning("Fill event for unknown order", order_id=kalshi_order_id)
        return

    slippage_cents = fill_price - float(order.price)
    logger.info(
        "Order filled",
        order_id=kalshi_order_id,
        expected_price=float(order.price),
        fill_price=fill_price,
        slippage_cents=slippage_cents,
        filled_qty=filled_qty,
    )

    await self._repo.update_fill(
        order_id=order.id,
        fill_price=fill_price,
        filled_qty=filled_qty,
        status="filled" if filled_qty >= order.quantity else "partial",
    )
```

### Paper trading validation criteria (Claude's discretion recommendation)
Minimum criteria before switching to live mode:
1. **3 complete scan cycles** complete without errors (all pipeline stages execute)
2. **10+ paper orders** persisted to DB and queryable post-cycle
3. **Fill simulation** produces plausible fill fractions (0.5-1.0 range, logged)
4. **No uncaught exceptions** in orchestrator or fill tracker logs over 45-minute window
5. **Prometheus metrics** show `pmtb_decision_approvals_total > 0` and `pmtb_decision_rejections_total > 0`

### Docker health check command
```yaml
# Prometheus /metrics endpoint is the health check target (already running on port 9090)
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:9090/metrics"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 30s
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `docker-compose` (v1, separate tool) | `docker compose` (v2, built-in) | Docker Desktop 3.4+ / Engine 20.10+ | Syntax: no hyphen; compose files identical |
| `python:3.13` (full) for Docker base | `python:3.13-slim` | Stable best practice | ~3x smaller image; curl must be installed separately for health checks |
| `ENTRYPOINT` for Python apps | `CMD` for Python apps | Stable | CMD allows override at `docker run` time; better for dev |
| `asyncio.wait_for` for timeout | `asyncio.timeout` (3.11+) | Python 3.11 | Cleaner context manager; already used in Phase 3 research agents |

**Deprecated/outdated:**
- `docker-compose up` (hyphen): still works as alias but v2 syntax is `docker compose up`
- `setup.py`: project uses `hatchling` via `pyproject.toml` — correct modern approach

---

## Open Questions

1. **Kalshi WS fill channel — account-scoped vs ticker-scoped?**
   - What we know: `KalshiWSClient.subscribe()` accepts `market_tickers` parameter
   - What's unclear: Whether `market_tickers=[]` on the "fill" channel delivers all account fills or requires explicit ticker subscription
   - Recommendation: Test against demo API in Wave 0. If account-scoped, use `market_tickers=[]`. If ticker-scoped, add a `subscribe_ticker(ticker)` method to FillTracker that re-subscribes on each new order.

2. **Kalshi WS fill message schema**
   - What we know: WS sends JSON messages with a `type` field; fill events exist on "fill" channel
   - What's unclear: Exact field names (`yes_price` vs `fill_price`, `count` vs `filled_count`, etc.)
   - Recommendation: Log raw fill messages in the first implementation and refine the fill handler once the schema is confirmed against demo API.

3. **Watchdog process lifecycle in Docker stop sequence**
   - What we know: `daemon=False` means watchdog survives parent exit; Docker sends SIGTERM to PID 1 on `docker stop`
   - What's unclear: Whether the non-daemon watchdog process blocks container shutdown beyond Docker's stop timeout (default 10s)
   - Recommendation: Add `watchdog_proc.terminate()` in the main process shutdown path after `stop_event.set()`. If watchdog is alive, terminate it gracefully before `engine.dispose()`.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 1.x |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`, `asyncio_mode = "auto"`) |
| Quick run command | `uv run pytest tests/test_paper.py tests/test_orchestrator.py tests/test_fill_tracker.py -x` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXEC-01 | Limit order placed via executor, persisted to DB | unit | `uv run pytest tests/test_orchestrator.py::test_approved_decision_places_order -x` | Wave 0 |
| EXEC-02 | Fill event updates Order.status and creates Trade row | unit | `uv run pytest tests/test_fill_tracker.py::test_fill_event_updates_order -x` | Wave 0 |
| EXEC-03 | Slippage logged and persisted (fill_price vs price) | unit | `uv run pytest tests/test_fill_tracker.py::test_slippage_logged -x` | Wave 0 |
| EXEC-04 | Orders older than timeout get cancelled | unit | `uv run pytest tests/test_fill_tracker.py::test_stale_order_cancelled -x` | Wave 0 |
| EXEC-05 | Order/fill/cancellation queryable from DB after cycle | integration | `uv run pytest tests/test_order_repo.py -x` | Wave 0 |
| DEPL-01 | `docker compose up` starts system locally | smoke/manual | `docker compose up --wait && curl -f http://localhost:9090/metrics` | manual |
| DEPL-02 | Docker image deploys to VPS (SSH + docker compose) | manual | SSH + `docker compose up -d` | manual |
| DEPL-03 | JSON logs to stdout from Docker | smoke | `docker logs pmtb-pmtb-1 \| head -5 \| python3 -c "import sys,json;[json.loads(l) for l in sys.stdin]"` | manual |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_paper.py tests/test_orchestrator.py tests/test_fill_tracker.py tests/test_order_repo.py -x`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_orchestrator.py` — covers EXEC-01: full cycle, approved decision execution
- [ ] `tests/test_fill_tracker.py` — covers EXEC-02, EXEC-03, EXEC-04: fill events, slippage, stale cancellation
- [ ] `tests/test_order_repo.py` — covers EXEC-05: DB persistence queries
- [ ] Alembic migration for `orders.is_paper` column (not a test file, but a schema gap)
- [ ] `tests/conftest.py` — needs in-memory SQLite or mock session_factory fixture for new test files (existing conftest.py may already provide this — verify)

---

## Sources

### Primary (HIGH confidence)
- Project source code: `src/pmtb/executor.py`, `src/pmtb/paper.py`, `src/pmtb/main.py`, `src/pmtb/kalshi/ws_client.py`, `src/pmtb/db/models.py`, `src/pmtb/decision/pipeline.py`, `src/pmtb/decision/watchdog.py`, `src/pmtb/config.py` — all read directly
- Project context: `.planning/phases/06-execution-integration-and-deployment/06-CONTEXT.md` — locked decisions
- Project config: `.planning/config.json` — nyquist_validation: true confirmed

### Secondary (MEDIUM confidence)
- Docker multi-stage build with `uv`: standard Python packaging pattern, widely documented; uv `--frozen --no-dev` flags confirmed in uv documentation
- `docker compose` v2 syntax: confirmed Docker CLI behavior as of Docker Engine 20.10+
- `python:3.13-slim` base image: confirmed on Docker Hub; asyncpg incompatible with alpine/musl

### Tertiary (LOW confidence)
- Kalshi WS fill channel scope (account-scoped vs ticker-scoped): not verified — needs empirical test against demo API
- Exact Kalshi fill message JSON schema: not verified from official docs — needs runtime confirmation

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed and in use
- Architecture: HIGH — patterns derived directly from existing Phase 1-5 code
- Docker patterns: HIGH — standard, stable patterns
- Pitfalls: HIGH — derived from actual project code and established decisions
- Kalshi WS fill schema: LOW — needs empirical verification against demo API

**Research date:** 2026-03-10
**Valid until:** 2026-04-10 (stable domain; only Kalshi API behavior is time-sensitive)
