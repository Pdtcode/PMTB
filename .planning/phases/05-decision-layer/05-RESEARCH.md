# Phase 5: Decision Layer - Research

**Researched:** 2026-03-10
**Domain:** Trading decision pipeline — edge detection, Kelly sizing, risk management, watchdog process
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Gate Ordering**
- Sequential pipeline: Edge -> Size -> Risk
- Edge detector filters first (cheapest check — pure math on p_model vs p_market)
- Kelly sizer computes position size for survivors
- Risk manager enforces portfolio limits on the sized order
- Each gate can reject — no trade reaches executor without passing all three
- Shadow predictions (is_shadow=True) are excluded before the pipeline

**Watchdog Architecture**
- Separate OS process — truly independent, survives main process crashes
- Polls portfolio state from PostgreSQL every 30 seconds
- Communicates halt signal via a database flag (trading_halted row/column)
- Main bot checks halt flag before every order placement
- On halt trigger: watchdog sets DB flag AND calls Kalshi API to cancel all pending orders
- Watchdog needs its own Kalshi API credentials (same keys, separate client instance)

**Auto-Hedge Behavior**
- Claude's discretion on hedge trigger (edge reversal, configurable shift threshold, or hybrid approach)
- Claude's discretion on hedge action (sell/close position vs opposing bet)
- Claude's discretion on hedge timing (scan cycle vs continuous monitoring)

**Duplicate Bet Detection**
- Block any second bet on same market — if open position exists for the ticker, reject at risk gate
- Use existing Position table unique market_id constraint as the source of truth

**VaR Computation**
- Portfolio-level VaR only (not per-trade)
- 95% VaR computed across all open positions
- Block new trades if adding the position would push portfolio VaR beyond configurable limit

**Position Tracker**
- In-memory state synced from database
- Load positions on startup, keep in sync as orders fill
- DB remains source of truth, memory is cache for fast checks
- Resolves the STATE.md research flag: use in-process async dict, defer Redis

### Claude's Discretion
- Auto-hedge trigger strategy (what constitutes "significant shift")
- Auto-hedge action (sell vs opposing bet)
- Auto-hedge timing (scan cycle vs continuous)
- VaR configurable limit default value
- Max exposure limit and max single-bet limit default values
- Position tracker sync mechanism details (event-driven vs periodic refresh)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| EDGE-01 | Compute p_market from current Kalshi bid/ask prices | MarketCandidate.implied_probability is already parsed; formula is mid = (yes_bid + yes_ask) / 2 |
| EDGE-02 | Compute EV = p_model * b - (1 - p_model) | Pure arithmetic; b = 1/p_market - 1 (binary market payout) |
| EDGE-03 | Compute edge = p_model - p_market | Trivial subtraction; gating on Settings.edge_threshold |
| EDGE-04 | Only pass trades when edge > 0.04 (configurable) | Settings.edge_threshold already exists with default 0.04 |
| SIZE-01 | Compute Kelly optimal fraction: f* = (p*b - q) / b | Formula specified; b = net decimal odds; q = 1 - p_model |
| SIZE-02 | Apply fractional Kelly: f = alpha * f* with configurable alpha | Settings.kelly_alpha = 0.25 already exists |
| SIZE-03 | Cap position size by risk management limits before order placement | Requires max_position_size limit; cap applied after fractional Kelly |
| RISK-01 | Enforce maximum total portfolio exposure limit | Requires new config field; check against PositionTracker total exposure |
| RISK-02 | Enforce maximum single-bet size limit | Requires new config field; applied as hard cap in sizer |
| RISK-03 | Compute 95% VaR: VaR = mu - 1.645 * sigma across open positions | Portfolio-level; needs current_value and historical price data per position |
| RISK-04 | Halt all trading when portfolio drawdown exceeds 8% | Settings.max_drawdown = 0.08 exists; needs peak equity tracking |
| RISK-05 | Independent watchdog process that can halt trading even if main loop is hung | Separate OS process via multiprocessing or subprocess; polls DB every 30s |
| RISK-06 | Position tracker maintains real-time view of all open positions | In-process async dict; load from DB on startup, update on fills |
| RISK-07 | Auto-hedge when odds shift significantly against an open position | Hedge trigger is Claude's discretion; implemented in risk gate scan |
| RISK-08 | Detect and prevent duplicate bets on the same market | Check PositionTracker by ticker before proceeding through gates |
</phase_requirements>

---

## Summary

Phase 5 implements the decision layer that sits between the prediction pipeline (Phase 4) and the executor (Phase 6). Every trade candidate passes through three sequential gates: edge detection, Kelly sizing, and risk management. The math is straightforward; the architecture complexity lives in the watchdog process and the position tracker.

The key architectural challenge is the watchdog: it must be a truly independent OS process that can halt trading even when the main process is unresponsive. The design uses a PostgreSQL flag as the inter-process communication channel, which is already supported by the existing DB infrastructure. No new dependencies are needed for this — Python's `multiprocessing` module or a standalone script invoked via `subprocess` are sufficient and reliable.

The position tracker is an in-process async dict (dict[str, Position]) keyed by ticker, loaded at startup from the DB and kept in sync. This resolves the STATE.md Redis research flag in favor of simplicity: no new infrastructure, no additional dependency, and it fits cleanly into the existing async architecture.

**Primary recommendation:** Implement the three gates as a single `DecisionPipeline` class with injected sub-components (EdgeDetector, KellySizer, RiskManager), the PositionTracker as a separate stateful component, and the watchdog as a standalone Python script invoked as a subprocess. Each component gets its own module, its own Pydantic output model, and its own pytest file.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | (via pydantic-settings already installed) | Pipeline contract models between gates | Already used throughout; all existing phase outputs are Pydantic models |
| sqlalchemy[asyncio] | >=2.0 (already installed) | Position tracker DB queries, watchdog DB polling, halt flag writes | Already in use; async session pattern established |
| asyncpg | >=0.29 (already installed) | PostgreSQL async driver | Already in use |
| loguru | >=0.7 (already installed) | Structured logging with .bind() | Established project pattern |
| prometheus-client | >=0.20 (already installed) | Metrics counters/histograms | Established project pattern |
| multiprocessing (stdlib) | stdlib | Watchdog OS process | No dependency needed; stdlib Process class fits the use case |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| statistics (stdlib) | stdlib | stdev computation for VaR | Portfolio-level VaR calculation — mean and stdev of position values |
| asyncio (stdlib) | stdlib | Async PositionTracker, pipeline orchestration | Already in use everywhere |
| math (stdlib) | stdlib | Kelly formula, log operations | Pure math in sizer |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| multiprocessing.Process for watchdog | subprocess + standalone script | Both work; multiprocessing keeps everything in one codebase; subprocess isolates the watchdog more completely — either is fine for 30s polling workload |
| In-process async dict for position tracker | Redis | Redis adds infra overhead with no concrete benefit at current scale; dict is faster and simpler |
| stdlib statistics for VaR | numpy/scipy | numpy not yet in dependencies; stdlib sufficient for portfolio-level VaR with tens of positions |

**Installation:** No new packages required. All dependencies already installed.

---

## Architecture Patterns

### Recommended Project Structure

```
src/pmtb/decision/
├── __init__.py
├── models.py          # TradeDecision, RejectionReason, PortfolioState Pydantic models
├── edge.py            # EdgeDetector — pure math, no I/O
├── sizer.py           # KellySizer — pure math, no I/O
├── risk.py            # RiskManager — stateful, reads PositionTracker
├── tracker.py         # PositionTracker — async dict, DB sync
├── pipeline.py        # DecisionPipeline orchestrator — composes all three gates
└── watchdog.py        # Standalone watchdog process (can be run as __main__)

tests/decision/
├── __init__.py
├── test_edge.py
├── test_sizer.py
├── test_risk.py
├── test_tracker.py
└── test_pipeline.py
```

### Pattern 1: Sequential Gate with Pydantic Contracts

**What:** Each gate receives a typed input, either returns a `TradeDecision` (approved with sizing) or raises/returns a rejection. Rejections are modeled as a result type, not exceptions, so the pipeline can log them and continue.

**When to use:** Every trade candidate through the decision pipeline.

**Example:**
```python
# src/pmtb/decision/models.py
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field


class RejectionReason(str, Enum):
    SHADOW = "shadow"
    INSUFFICIENT_EDGE = "insufficient_edge"
    KELLY_NEGATIVE = "kelly_negative"
    MAX_EXPOSURE = "max_exposure"
    MAX_SINGLE_BET = "max_single_bet"
    DRAWDOWN_HALTED = "drawdown_halted"
    DUPLICATE_POSITION = "duplicate_position"
    VAR_EXCEEDED = "var_exceeded"


class TradeDecision(BaseModel):
    ticker: str
    cycle_id: str
    approved: bool
    rejection_reason: RejectionReason | None = None
    # Populated only when approved=True
    side: str | None = None           # "yes" or "no"
    quantity: int | None = None       # integer contracts
    edge: float | None = None         # p_model - p_market
    ev: float | None = None           # expected value
    kelly_f: float | None = None      # fractional Kelly fraction (after alpha)
    p_model: float | None = None
    p_market: float | None = None
```

### Pattern 2: EdgeDetector — Pure Math, No State

**What:** Takes `PredictionResult` + `MarketCandidate`, computes edge and EV, returns approved/rejected `TradeDecision`. No DB access, no async.

**Example:**
```python
# src/pmtb/decision/edge.py
from pmtb.decision.models import TradeDecision, RejectionReason
from pmtb.prediction.models import PredictionResult
from pmtb.scanner.models import MarketCandidate


class EdgeDetector:
    def __init__(self, edge_threshold: float) -> None:
        self._threshold = edge_threshold

    def evaluate(
        self,
        prediction: PredictionResult,
        candidate: MarketCandidate,
    ) -> TradeDecision:
        p_model = prediction.p_model
        p_market = candidate.implied_probability

        edge = p_model - p_market
        # b = net decimal odds (win $b per $1 risked if YES resolves)
        # On a binary prediction market: payout = $1 per contract
        # b = (1 - p_market) / p_market
        b = (1.0 - p_market) / p_market if p_market > 0 else 0.0
        ev = p_model * b - (1.0 - p_model)

        if edge <= self._threshold:
            return TradeDecision(
                ticker=prediction.ticker,
                cycle_id=prediction.cycle_id,
                approved=False,
                rejection_reason=RejectionReason.INSUFFICIENT_EDGE,
                edge=edge,
                ev=ev,
                p_model=p_model,
                p_market=p_market,
            )

        return TradeDecision(
            ticker=prediction.ticker,
            cycle_id=prediction.cycle_id,
            approved=True,
            side="yes",  # positive edge -> bet YES
            edge=edge,
            ev=ev,
            p_model=p_model,
            p_market=p_market,
        )
```

**Note on p_market computation (EDGE-01):** `MarketCandidate.implied_probability` is already computed and available. The formula used in the scanner is the mid-market price. For edge computation, use `implied_probability` directly — it is already a parsed float in [0, 1].

**Note on b (payout odds):** In Kalshi binary markets, each contract pays $1 on resolution. If you pay p_market per contract, the net win is (1 - p_market) per contract and the net loss is p_market. So b = (1 - p_market) / p_market. This gives the standard Kelly denominator.

### Pattern 3: KellySizer — Pure Math, No State

**What:** Takes an approved `TradeDecision`, computes fractional Kelly fraction, converts to integer contracts, caps at max_single_bet.

**Example:**
```python
# src/pmtb/decision/sizer.py
import math
from pmtb.decision.models import TradeDecision, RejectionReason


class KellySizer:
    def __init__(
        self,
        kelly_alpha: float,        # 0.25 = quarter Kelly
        max_single_bet: float,     # fraction of portfolio, e.g. 0.05
        portfolio_value: float,    # current total portfolio value in dollars
    ) -> None:
        self._alpha = kelly_alpha
        self._max_single_bet = max_single_bet
        self._portfolio_value = portfolio_value

    def size(self, decision: TradeDecision) -> TradeDecision:
        assert decision.approved
        p = decision.p_model
        b = (1.0 - decision.p_market) / decision.p_market
        q = 1.0 - p

        # Full Kelly: f* = (p*b - q) / b
        f_star = (p * b - q) / b

        if f_star <= 0:
            return decision.model_copy(update={
                "approved": False,
                "rejection_reason": RejectionReason.KELLY_NEGATIVE,
            })

        # Fractional Kelly
        f = self._alpha * f_star

        # Cap at max_single_bet (fraction of portfolio)
        f = min(f, self._max_single_bet)

        # Convert to integer contracts (floor — never over-size)
        dollar_amount = f * self._portfolio_value
        quantity = max(1, int(dollar_amount))  # minimum 1 contract

        return decision.model_copy(update={
            "quantity": quantity,
            "kelly_f": f,
        })
```

### Pattern 4: PositionTracker — Async Dict with DB Sync

**What:** Holds open positions in an async-safe dict (keyed by ticker). Loads on startup, updated when fills arrive. DB is source of truth; the dict is a cache.

**Example:**
```python
# src/pmtb/decision/tracker.py
from __future__ import annotations
import asyncio
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from pmtb.db.models import Position


class PositionTracker:
    """In-process cache of open positions. Thread-safe via asyncio Lock."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        self._positions: dict[str, Position] = {}  # ticker -> Position
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Load all open positions from DB at startup."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Position).where(Position.status == "open")
            )
            positions = result.scalars().all()
        async with self._lock:
            self._positions = {p.market.ticker: p for p in positions}
            # Note: requires eager loading or separate ticker lookup

    async def has_position(self, ticker: str) -> bool:
        async with self._lock:
            return ticker in self._positions

    async def get_all(self) -> list[Position]:
        async with self._lock:
            return list(self._positions.values())

    async def total_exposure(self) -> Decimal:
        async with self._lock:
            return sum(
                (p.avg_price * p.quantity for p in self._positions.values()),
                Decimal("0"),
            )

    async def add_position(self, ticker: str, position: Position) -> None:
        async with self._lock:
            self._positions[ticker] = position

    async def remove_position(self, ticker: str) -> None:
        async with self._lock:
            self._positions.pop(ticker, None)
```

**Sync mechanism decision (Claude's discretion):** Use periodic refresh every N seconds (same as scan cycle interval) as a fallback safety net, but also update immediately on fill events from Phase 6. This hybrid approach ensures correctness without tight coupling between the decision layer and the executor.

**Ticker lookup problem:** The Position DB model stores `market_id` (UUID), not ticker. At startup, eager-load the related Market to get the ticker. During runtime, maintain ticker -> Position mapping in the dict. When Phase 6 reports a fill, it must pass the ticker (which it has from the trade decision).

### Pattern 5: RiskManager — Stateful, Multiple Checks

**What:** Checks halt flag, duplicate position, max exposure, max single-bet, VaR, and drawdown. Calls PositionTracker for current state.

**Example (halt flag check):**
```python
# src/pmtb/decision/risk.py
async def _check_halt_flag(self, session) -> bool:
    """Returns True if trading is halted."""
    result = await session.execute(
        select(TradingState).where(TradingState.key == "trading_halted")
    )
    row = result.scalar_one_or_none()
    return row is not None and row.value == "true"
```

**VaR computation (RISK-03):**
```python
import statistics

def compute_portfolio_var(positions: list[Position], confidence: float = 0.95) -> float:
    """
    95% VaR using normal approximation: VaR = mu - z * sigma
    z = 1.645 for 95% one-tailed.
    Values are current_value of each open position.
    Returns VaR as a negative dollar amount (loss).
    """
    values = [float(p.current_value or p.avg_price * p.quantity) for p in positions]
    if len(values) < 2:
        return 0.0
    mu = statistics.mean(values)
    sigma = statistics.stdev(values)
    return mu - 1.645 * sigma
```

### Pattern 6: Watchdog — Standalone Process

**What:** A simple polling loop that reads from PostgreSQL every 30 seconds. Sets `trading_halted` flag and cancels orders when drawdown exceeds threshold. Must be simple and reliable — no clever abstractions.

**Launch from main.py:**
```python
import multiprocessing
import sys

def _launch_watchdog(settings_env: dict) -> multiprocessing.Process:
    """Launch watchdog as independent OS process."""
    from pmtb.decision.watchdog import run_watchdog
    proc = multiprocessing.Process(
        target=run_watchdog,
        args=(settings_env,),
        daemon=False,  # NOT daemon — must survive main process crash
    )
    proc.start()
    return proc
```

**Watchdog core loop:**
```python
# src/pmtb/decision/watchdog.py
import asyncio
import time

POLL_INTERVAL_SECONDS = 30

async def _watchdog_loop(settings) -> None:
    """Poll DB for drawdown breach. Set halt flag and cancel orders if breached."""
    from pmtb.db.session import get_session_factory
    from pmtb.kalshi.client import KalshiClient

    session_factory = get_session_factory(settings)
    kalshi_client = KalshiClient(settings)  # own client instance

    while True:
        try:
            await _check_and_act(session_factory, kalshi_client, settings)
        except Exception as exc:
            # Watchdog must never crash — log and continue
            logger.error(f"Watchdog check failed: {exc}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

async def _check_and_act(session_factory, kalshi_client, settings) -> None:
    """Single poll cycle: check drawdown, set halt if breached."""
    # ... query portfolio value and peak value from DB
    # ... if drawdown > max_drawdown: set halt flag, cancel pending orders
    pass

def run_watchdog(settings_env: dict) -> None:
    """Entry point for the watchdog process."""
    # Re-initialize settings from env dict (can't share objects across process boundary)
    asyncio.run(_watchdog_loop(settings))
```

**Critical watchdog design constraints:**
- `daemon=False` — daemon processes are killed when the parent dies; watchdog must survive
- No shared memory with main process — communicate only through PostgreSQL
- Must handle its own DB connection pool (cannot share with main process)
- On halt: first set DB flag (so main bot stops), then cancel orders (non-blocking)
- Watchdog restart strategy: run under `supervisord` or `systemd` in production; for local dev, re-launch if process dies

### Pattern 7: TradingState DB Table (New Table Required)

**What:** A key/value table for system-wide state flags (e.g., `trading_halted`, `peak_portfolio_value`). Needed by both the watchdog (write) and the risk manager (read).

```python
# Addition to src/pmtb/db/models.py
class TradingState(Base):
    """
    Key/value store for system-wide trading state.
    Used by watchdog and risk manager for halt signaling.
    """
    __tablename__ = "trading_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
```

**Migration:** A new Alembic migration is required for this table and any new Settings fields added to config.

### Pattern 8: Auto-Hedge Implementation (Claude's Discretion)

**Recommended approach:** Trigger hedge on edge reversal during the scan cycle.

**Trigger logic:** After edge detection, if the market is already in open positions AND the new edge is negative (p_model < p_market), treat it as a hedge signal rather than a new trade.

**Hedge action:** Sell the position (close) rather than placing an opposing bet. Selling is cleaner: it exits the position rather than creating a correlated pair that can compound losses.

**Timing:** Scan cycle is sufficient. Continuous monitoring is unnecessary complexity for 5-minute scan intervals and the nature of prediction market liquidity.

**Threshold:** A `hedge_shift_threshold` of 0.03 (3% edge reversal, slightly below the entry threshold) avoids excessive churning on noise while catching genuine reversals.

```python
# In RiskManager or DecisionPipeline
async def _check_hedge(
    self,
    prediction: PredictionResult,
    candidate: MarketCandidate,
    tracker: PositionTracker,
) -> TradeDecision | None:
    """Returns a hedge close decision if edge has reversed on an open position."""
    if not await tracker.has_position(prediction.ticker):
        return None
    edge = prediction.p_model - candidate.implied_probability
    if edge < -self._hedge_shift_threshold:
        return TradeDecision(
            ticker=prediction.ticker,
            cycle_id=prediction.cycle_id,
            approved=True,
            side="sell",  # close signal for executor
            quantity=None,  # executor reads current position quantity
            edge=edge,
            p_model=prediction.p_model,
            p_market=candidate.implied_probability,
        )
    return None
```

### Anti-Patterns to Avoid

- **Exception-based gate rejection:** Using exceptions to reject trades makes pipeline logging and metrics hard. Use a result type (TradeDecision.approved=False with RejectionReason) so every rejection is observable.
- **Watchdog as daemon process:** A daemon process is killed when the parent exits. The watchdog's purpose is to outlive a crashed main process — `daemon=False` is required.
- **Sharing DB connection pools across process boundaries:** asyncpg connections are not fork-safe. Each process must create its own engine and session factory after forking.
- **Mutable default arguments in Position tracker:** Python's dict being mutable means accidental sharing between test instances. Always instantiate fresh tracker in tests.
- **Calling Kalshi API inside the edge gate:** Edge detection is pure math. Never make I/O calls inside the edge or Kelly gates — they are synchronous computation only.
- **Trusting in-memory tracker without DB source of truth:** The tracker dict is a cache, not the truth. After any restart, always load from DB before allowing trades.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Process isolation for watchdog | Custom IPC, shared memory, sockets | `multiprocessing.Process` + PostgreSQL flag | OS process boundary is the only truly independent boundary; DB flag survives both processes restarting |
| VaR computation | Full covariance matrix, scipy stats | stdlib `statistics.mean` + `statistics.stdev` + z=1.645 | Portfolio-level VaR with normal approximation is specified; GARCH/covariance is v2 scope |
| Pydantic model copying with updates | Manual dict reconstruction | `model.model_copy(update={...})` | Pydantic v2 native; preserves validation and type safety |
| Async concurrency control in tracker | Custom mutex | `asyncio.Lock()` | stdlib, zero dependencies, correct for single-process async |
| Settings new fields | Separate config file | Add to existing `Settings` class | Already has `edge_threshold`, `kelly_alpha`, `max_drawdown`; add `max_exposure`, `max_single_bet`, `var_limit`, `hedge_shift_threshold` |

**Key insight:** The entire decision layer is pure Python — no new libraries are needed. The complexity is architectural (process boundaries, DB communication) not computational.

---

## Common Pitfalls

### Pitfall 1: Fork Safety with asyncpg

**What goes wrong:** asyncpg connections created before `multiprocessing.Process.start()` are not fork-safe and will silently corrupt or deadlock in the child process.

**Why it happens:** asyncpg uses OS-level sockets and internal state that cannot be safely shared across `fork()`.

**How to avoid:** Create the DB engine and session factory INSIDE the watchdog's entry function (`run_watchdog`), after the fork. Never pass live DB connections or SQLAlchemy engines to a child process.

**Warning signs:** Database errors only in the watchdog process, intermittent connection timeouts, asyncpg "connection closed" errors.

### Pitfall 2: Daemon Watchdog Dies with Parent

**What goes wrong:** `multiprocessing.Process(daemon=True)` is killed when the main process exits or crashes — defeating the entire purpose of the watchdog.

**Why it happens:** Python's daemon flag causes the process to be terminated automatically when the parent exits.

**How to avoid:** Always set `daemon=False` for the watchdog. The watchdog is meant to outlive the main process. Accept that you must explicitly manage its lifecycle.

**Warning signs:** No `trading_halted` flag set after main process crash, pending orders never cancelled during a crash scenario.

### Pitfall 3: Kelly Formula Edge Cases

**What goes wrong:** Kelly fraction goes negative when there is no edge (EV < 0). If negative f is passed to the sizer, it produces a short bet that may not be valid on Kalshi.

**Why it happens:** f* = (p*b - q) / b can be negative when p*b < q (i.e., when the bet has negative expected value).

**How to avoid:** Gate the sizer on positive f*. If f* <= 0, reject with `RejectionReason.KELLY_NEGATIVE`. The edge detector should catch this before sizing, but the sizer should double-check defensively.

**Warning signs:** negative quantity in TradeDecision, downstream executor receiving negative size.

### Pitfall 4: Peak Portfolio Value Tracking for Drawdown

**What goes wrong:** Drawdown requires a "peak" reference value, which is not naturally stored in the DB.

**Why it happens:** Portfolio value at any point = sum of position current values + cash. Peak must be tracked over time to compute drawdown = (peak - current) / peak.

**How to avoid:** Store peak portfolio value in `TradingState` table (key="peak_portfolio_value"). Update it whenever current value exceeds the stored peak. Both the main process and watchdog must use the same stored peak.

**Warning signs:** Drawdown never triggers because peak is reset incorrectly, or drawdown triggers immediately because peak was never set.

### Pitfall 5: Position Table Ticker Lookup

**What goes wrong:** The Position model stores `market_id` (UUID FK to Markets), not ticker. The PositionTracker needs ticker-keyed lookups.

**Why it happens:** The DB schema uses FKs correctly, but the in-memory cache needs the string ticker.

**How to avoid:** At `tracker.load()`, join Position to Market to get the ticker. Use `select(Position, Market.ticker).join(Market)` or eager-load the relationship. Store the result as `dict[ticker, Position]`.

**Warning signs:** KeyError on ticker lookup, tracker showing empty when positions exist.

### Pitfall 6: VaR with Fewer Than 2 Positions

**What goes wrong:** `statistics.stdev([x])` raises `StatisticsError` for samples of size < 2.

**Why it happens:** Standard deviation is undefined for a single data point.

**How to avoid:** Guard with `if len(values) < 2: return 0.0` (single position has no variance to aggregate). VaR of zero means no VaR-based blocking for a single position — rely on other limits instead.

### Pitfall 7: Watchdog Polling Interval vs Scan Interval

**What goes wrong:** If the scan cycle runs every 5 minutes and the watchdog polls every 30 seconds, there can be up to 30 seconds of unprotected trading after a drawdown breach occurs.

**Why it happens:** Asynchronous polling — the watchdog may check just after an order was placed that pushed the portfolio over the limit.

**How to avoid:** The risk manager in the main loop ALSO checks drawdown before every order, not just the watchdog. The watchdog is a safety net for main process failures, not the primary enforcement mechanism. Double-enforcement: RiskManager (inline) + Watchdog (OOB).

---

## Code Examples

### Edge Formula Reference

```python
# EDGE-01: p_market from bid/ask (already available as MarketCandidate.implied_probability)
# The scanner computes: implied_probability = (yes_bid + yes_ask) / 2

# EDGE-02: Expected Value
# b = net odds: win (1 - p_market) per unit risked p_market
b = (1.0 - p_market) / p_market  # binary market payout ratio
ev = p_model * b - (1.0 - p_model)

# EDGE-03: Edge
edge = p_model - p_market

# EDGE-04: Gate
if edge <= settings.edge_threshold:  # 0.04 default
    reject("insufficient_edge")
```

### Kelly Formula Reference

```python
# SIZE-01: Full Kelly
# f* = (p*b - q) / b  where b = net odds, q = 1 - p_model
p = prediction.p_model
q = 1.0 - p
b = (1.0 - p_market) / p_market
f_star = (p * b - q) / b   # equivalent to: (p - q/b) or (p*b - q) / b

# SIZE-02: Fractional Kelly
f = settings.kelly_alpha * f_star  # 0.25 * f_star = quarter Kelly

# SIZE-03: Cap at max_single_bet (fraction of portfolio)
f = min(f, settings.max_single_bet)  # new config field needed
quantity = max(1, int(f * portfolio_value))
```

### VaR Formula Reference

```python
# RISK-03: 95% VaR = mu - 1.645 * sigma
import statistics

values = [float(p.current_value or p.avg_price * p.quantity) for p in open_positions]
if len(values) >= 2:
    mu = statistics.mean(values)
    sigma = statistics.stdev(values)
    var_95 = mu - 1.645 * sigma  # negative = portfolio-level loss at 95% confidence
```

### Drawdown Check Reference

```python
# RISK-04: Drawdown = (peak - current) / peak
current_portfolio_value = sum(float(p.avg_price * p.quantity) for p in positions)
peak_value = float(trading_state["peak_portfolio_value"])
if peak_value > 0:
    drawdown = (peak_value - current_portfolio_value) / peak_value
    if drawdown >= settings.max_drawdown:  # 0.08 default
        halt_trading()
```

### Prometheus Metrics Pattern (established convention)

```python
# src/pmtb/decision/pipeline.py
from prometheus_client import Counter, Histogram

DECISION_REJECTIONS = Counter(
    "pmtb_decision_rejections_total",
    "Trade candidates rejected by decision layer",
    ["reason"],  # label: insufficient_edge, kelly_negative, max_exposure, etc.
)
DECISION_APPROVALS = Counter(
    "pmtb_decision_approvals_total",
    "Trade candidates approved by decision layer",
)
DECISION_LATENCY = Histogram(
    "pmtb_decision_latency_seconds",
    "End-to-end decision pipeline latency",
)
WATCHDOG_HALT_TRIGGERS = Counter(
    "pmtb_watchdog_halt_triggers_total",
    "Times watchdog triggered a trading halt",
)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Full Kelly sizing | Fractional Kelly (alpha 0.25-0.5) | Standard since 2000s academic work | Full Kelly is ruin-prone with miscalibrated probabilities; fractional Kelly is the universal production standard |
| Shared memory IPC for circuit breakers | DB-mediated halt flags | Common pattern in distributed systems | DB provides durability; no shared memory bugs; watchdog survives process crashes |
| Blocking synchronous risk checks | Async with locks | Python asyncio era | Non-blocking checks integrate with async event loop without threads |

**Deprecated/outdated:**
- Full Kelly: Never used in production trading systems with imperfect probability estimates
- Thread-based watchdog: multiprocessing provides true OS-level isolation; threads share fate with the main process

---

## Open Questions

1. **Portfolio value denominator for Kelly and drawdown**
   - What we know: Kalshi contracts are binary ($0 or $1); position value = quantity * current_market_price (not avg_price)
   - What's unclear: Where does `current_value` in Position come from? It's nullable in the DB schema. Who updates it?
   - Recommendation: For Phase 5, compute portfolio value as sum(quantity * avg_price) for simplicity. Phase 6 (executor) will update current_value as fills arrive. Document that drawdown computation becomes more accurate once Phase 6 provides live fill prices.

2. **Watchdog process management in paper trading mode**
   - What we know: Watchdog cancels real Kalshi orders on halt; paper trading has no real orders
   - What's unclear: Should the watchdog run in paper trading mode? It has no orders to cancel but could still set the halt flag
   - Recommendation: Always launch the watchdog regardless of trading_mode. In paper mode, the order cancellation step is a no-op (PaperOrderExecutor already returns empty results). The halt flag still exercises the full system path.

3. **New Settings fields for max_exposure, max_single_bet, var_limit, hedge_shift_threshold**
   - What we know: Settings class already has edge_threshold, kelly_alpha, max_drawdown
   - What's unclear: What are sensible defaults?
   - Recommendation (Claude's discretion): `max_exposure=0.80` (80% of portfolio max), `max_single_bet=0.05` (5% per trade), `var_limit=0.20` (VaR must stay above -20% of portfolio), `hedge_shift_threshold=0.03`

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio |
| Config file | `pyproject.toml` — `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` |
| Quick run command | `pytest tests/decision/ -x -q` |
| Full suite command | `pytest tests/ -x -q` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EDGE-01 | p_market derived from MarketCandidate.implied_probability | unit | `pytest tests/decision/test_edge.py::test_p_market_from_candidate -x` | Wave 0 |
| EDGE-02 | EV = p_model * b - (1 - p_model) | unit | `pytest tests/decision/test_edge.py::test_ev_computation -x` | Wave 0 |
| EDGE-03 | edge = p_model - p_market | unit | `pytest tests/decision/test_edge.py::test_edge_computation -x` | Wave 0 |
| EDGE-04 | Market with edge <= 0.04 is rejected | unit | `pytest tests/decision/test_edge.py::test_edge_gate_rejects_below_threshold -x` | Wave 0 |
| SIZE-01 | f* = (p*b - q) / b computed correctly | unit | `pytest tests/decision/test_sizer.py::test_kelly_formula -x` | Wave 0 |
| SIZE-02 | f = alpha * f* with configurable alpha | unit | `pytest tests/decision/test_sizer.py::test_fractional_kelly_alpha -x` | Wave 0 |
| SIZE-03 | Large computed f* is capped at max_single_bet | unit | `pytest tests/decision/test_sizer.py::test_position_cap_applies -x` | Wave 0 |
| RISK-01 | New trade rejected when total exposure would exceed limit | unit | `pytest tests/decision/test_risk.py::test_max_exposure_blocks_trade -x` | Wave 0 |
| RISK-02 | Single bet size never exceeds max_single_bet limit | unit | `pytest tests/decision/test_risk.py::test_max_single_bet_limit -x` | Wave 0 |
| RISK-03 | 95% VaR computed correctly: mu - 1.645 * sigma | unit | `pytest tests/decision/test_risk.py::test_var_computation -x` | Wave 0 |
| RISK-04 | All orders blocked when drawdown exceeds 8% | unit | `pytest tests/decision/test_risk.py::test_drawdown_halt_blocks_orders -x` | Wave 0 |
| RISK-05 | Watchdog detects breach and sets halt flag even when main process unresponsive | integration | `pytest tests/decision/test_risk.py::test_watchdog_sets_halt_flag -x` | Wave 0 |
| RISK-06 | PositionTracker reflects all open positions; updates in real time | unit | `pytest tests/decision/test_tracker.py::test_tracker_load_and_update -x` | Wave 0 |
| RISK-07 | Hedge triggered when edge reverses against open position | unit | `pytest tests/decision/test_risk.py::test_auto_hedge_trigger -x` | Wave 0 |
| RISK-08 | Duplicate bet on same market blocked before order placement | unit | `pytest tests/decision/test_risk.py::test_duplicate_position_blocked -x` | Wave 0 |

**Note on RISK-05 (watchdog):** The integration test for the watchdog should launch a real subprocess using `multiprocessing.Process`, inject a forced drawdown into the DB, and verify the `trading_halted` flag is set. This is a slower test but validates the critical independent-process behavior. Mark with `@pytest.mark.integration` if needed to separate from fast unit suite.

### Sampling Rate
- **Per task commit:** `pytest tests/decision/ -x -q`
- **Per wave merge:** `pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/decision/__init__.py` — package init
- [ ] `tests/decision/test_edge.py` — covers EDGE-01 through EDGE-04
- [ ] `tests/decision/test_sizer.py` — covers SIZE-01 through SIZE-03
- [ ] `tests/decision/test_risk.py` — covers RISK-01 through RISK-08
- [ ] `tests/decision/test_tracker.py` — covers RISK-06 (PositionTracker)
- [ ] `tests/decision/test_pipeline.py` — covers end-to-end pipeline integration
- [ ] `src/pmtb/decision/__init__.py` — package init
- [ ] Alembic migration for `TradingState` table
- [ ] New Settings fields: `max_exposure`, `max_single_bet`, `var_limit`, `hedge_shift_threshold`

---

## Sources

### Primary (HIGH confidence)

- Codebase inspection — `src/pmtb/config.py`, `src/pmtb/db/models.py`, `src/pmtb/prediction/models.py`, `src/pmtb/scanner/models.py`, `src/pmtb/executor.py`, `pyproject.toml`
- Phase context — `.planning/phases/05-decision-layer/05-CONTEXT.md` (locked decisions)
- Requirements — `.planning/REQUIREMENTS.md` (EDGE-01..04, SIZE-01..03, RISK-01..08)
- Python stdlib documentation — `multiprocessing`, `asyncio`, `statistics` modules

### Secondary (MEDIUM confidence)

- Kelly criterion — standard financial literature; f* = (p*b - q) / b is the canonical binary-outcome formula, widely referenced
- 95% VaR with z=1.645 — standard one-tailed normal approximation; same formula specified in RISK-03 requirements

### Tertiary (LOW confidence)

- Auto-hedge threshold recommendation (0.03) — heuristic based on being slightly below entry threshold; no empirical basis from this codebase

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; all existing libraries
- Architecture: HIGH — patterns follow established project conventions; Pydantic models, async session, Prometheus metrics all verified from codebase
- Pitfalls: HIGH — fork safety, daemon flag, Kelly edge cases are known Python/asyncpg issues; position table ticker lookup verified from schema
- Auto-hedge specifics: MEDIUM — recommended approach is sound but threshold values are heuristic

**Research date:** 2026-03-10
**Valid until:** 2026-04-10 (stable domain — stdlib + sqlalchemy; no fast-moving dependencies)
