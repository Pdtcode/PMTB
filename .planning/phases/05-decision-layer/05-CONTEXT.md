# Phase 5: Decision Layer - Context

**Gathered:** 2026-03-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Every trade candidate passes through three sequential gates — edge detection rejects sub-threshold opportunities, Kelly sizing produces a survivable position size, and the risk manager enforces hard portfolio limits with an independent watchdog that cannot be bypassed. No order placement, no fill tracking — just the decision of whether and how much to trade.

</domain>

<decisions>
## Implementation Decisions

### Gate Ordering
- Sequential pipeline: Edge → Size → Risk
- Edge detector filters first (cheapest check — pure math on p_model vs p_market)
- Kelly sizer computes position size for survivors
- Risk manager enforces portfolio limits on the sized order
- Each gate can reject — no trade reaches executor without passing all three
- Shadow predictions (is_shadow=True) are excluded before the pipeline

### Watchdog Architecture
- Separate OS process — truly independent, survives main process crashes
- Polls portfolio state from PostgreSQL every 30 seconds
- Communicates halt signal via a database flag (trading_halted row/column)
- Main bot checks halt flag before every order placement
- On halt trigger: watchdog sets DB flag AND calls Kalshi API to cancel all pending orders
- Watchdog needs its own Kalshi API credentials (same keys, separate client instance)

### Auto-Hedge Behavior
- Claude's discretion on hedge trigger (edge reversal, configurable shift threshold, or hybrid approach)
- Claude's discretion on hedge action (sell/close position vs opposing bet)
- Claude's discretion on hedge timing (scan cycle vs continuous monitoring)

### Duplicate Bet Detection
- Block any second bet on same market — if open position exists for the ticker, reject at risk gate
- Use existing Position table unique market_id constraint as the source of truth

### VaR Computation
- Portfolio-level VaR only (not per-trade)
- 95% VaR computed across all open positions
- Block new trades if adding the position would push portfolio VaR beyond configurable limit

### Position Tracker
- In-memory state synced from database
- Load positions on startup, keep in sync as orders fill
- DB remains source of truth, memory is cache for fast checks
- Resolves the STATE.md research flag: "Redis vs in-process async dict for hot portfolio state" — use in-process async dict, defer Redis

### Claude's Discretion
- Auto-hedge trigger strategy (what constitutes "significant shift")
- Auto-hedge action (sell vs opposing bet)
- Auto-hedge timing (scan cycle vs continuous)
- VaR configurable limit default value
- Max exposure limit and max single-bet limit default values
- Position tracker sync mechanism details (event-driven vs periodic refresh)

</decisions>

<specifics>
## Specific Ideas

- Config already has edge_threshold=0.04, kelly_alpha=0.25, max_drawdown=0.08 — use these directly
- Kelly formula specified in requirements: f* = (p*b - q) / b, then f = alpha * f*
- Edge formula: edge = p_model - p_market, EV = p_model * b - (1 - p_model)
- The watchdog is a safety-critical component — it must be simple and reliable, not clever
- MarketCandidate already has implied_probability — this is p_market for edge computation

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `PredictionResult` (src/pmtb/prediction/models.py): Input to decision layer — has p_model, confidence_low/high, ticker, is_shadow, cycle_id
- `MarketCandidate` (src/pmtb/scanner/models.py): Has implied_probability (p_market), yes_bid, yes_ask, spread — needed for edge computation
- `Settings` (src/pmtb/config.py): Already has edge_threshold, kelly_alpha, max_drawdown fields
- `OrderExecutorProtocol` (src/pmtb/executor.py): Paper/live executor pattern — decision layer outputs feed into this
- `Position` DB model (src/pmtb/db/models.py): Has market_id (unique), side, quantity, avg_price, status — position tracking source of truth
- `Order` DB model: Has market_id, status, kalshi_order_id — for cancellation by watchdog

### Established Patterns
- Pydantic models as pipeline contracts between phases
- Settings class for all configurable thresholds
- Prometheus metrics for monitoring (counters, histograms)
- Loguru structured logging with .bind() for contextual fields
- cycle_id correlation for end-to-end tracing
- Lazy imports for optional dependencies

### Integration Points
- Decision layer receives `list[PredictionResult]` + `list[MarketCandidate]` from prediction pipeline
- Decision layer outputs sized trade decisions consumed by Phase 6 executor
- Position tracker queries/updates Position table
- Watchdog queries Position table and Order table independently
- Risk manager writes to a halt-status mechanism in PostgreSQL

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-decision-layer*
*Context gathered: 2026-03-10*
