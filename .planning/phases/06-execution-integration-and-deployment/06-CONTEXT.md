# Phase 6: Execution, Integration, and Deployment - Context

**Gathered:** 2026-03-10
**Status:** Ready for planning

<domain>
## Phase Boundary

The complete pipeline runs end-to-end on a schedule — scanner feeds research feeds predictor feeds decision layer feeds executor — with paper trading confirming data flow before live capital, and Docker deployment for 24/7 operation. No performance tracking, no learning loop, no model retraining — just wiring, executing, and shipping.

</domain>

<decisions>
## Implementation Decisions

### Pipeline Orchestration
- Hybrid loop: fixed 15-minute interval for full scan cycles + WebSocket-triggered re-evaluation for open positions
- Full cycle: scanner → research → prediction → decision → execution, running every 15 minutes (configurable via Settings)
- WebSocket price-change triggers re-run decision pipeline only (skip scanner/research/prediction) against existing predictions and new market price
- Graceful degradation on stage failures: each stage logs failures and continues with whatever succeeded — consistent with Phase 3's resilience pattern
- cycle_id correlation flows through the entire pipeline (already established)

### Order Lifecycle
- WebSocket primary for real-time fill tracking + REST polling fallback as safety net to catch anything WS missed
- Stale order cancellation timeout: configurable via Settings (default 15 minutes — matches scan interval)
- Limit order price: best ask +/- configurable offset in cents (exposed in Settings for tuning aggression)
- Every order, fill, and cancellation persisted to PostgreSQL (EXEC-05)

### Slippage Handling
- Claude's discretion on slippage approach — log and persist expected vs actual price at minimum; decide whether to add a slippage threshold for cancellation based on Kalshi's limit order behavior

### Paper Trading
- Spread-aware fills: paper mode simulates fills at the ask price (for buys), respecting the spread; partial fills simulated probabilistically based on volume
- Full DB persistence: paper orders/fills write to the same DB tables with a paper flag — validates the full data path and enables paper-mode performance analysis
- Live market data by default: paper mode calls real Kalshi API for markets/prices, simulates execution only
- --mock flag available for CI/offline testing using fixture data
- Validation criteria: Claude's discretion on what constitutes successful paper validation before going live

### Docker & Deployment
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

</decisions>

<specifics>
## Specific Ideas

- main.py currently has a placeholder stop_event.wait() — this is where the pipeline loop gets wired
- MarketScanner.run_forever() already has a scan loop — pipeline orchestrator should integrate with or replace this
- LiveOrderExecutor already delegates to KalshiClient.place_order/cancel_order — needs fill tracking layer on top
- PaperOrderExecutor needs enhancement from simple no-op to spread-aware simulation with DB persistence
- DecisionPipeline.from_settings() factory is ready for integration — outputs list[TradeDecision] with approved/quantity/side/ticker
- Watchdog (Phase 5) is a separate OS process — needs consideration for Docker container architecture

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `OrderExecutorProtocol` (src/pmtb/executor.py): Protocol + factory for paper/live switching — already implemented
- `LiveOrderExecutor` (src/pmtb/executor.py): Delegates to KalshiClient — needs fill tracking layer
- `PaperOrderExecutor` (src/pmtb/paper.py): Simple no-op — needs spread-aware enhancement
- `KalshiClient` (src/pmtb/kalshi/client.py): place_order, cancel_order, get_orders, get_positions — REST layer ready
- `KalshiWSClient` (src/pmtb/kalshi/ws_client.py): WebSocket with subscribe/unsubscribe/run — fill event subscription needed
- `MarketScanner.run_forever()` (src/pmtb/scanner/scanner.py): Existing scan loop pattern
- `DecisionPipeline.from_settings()` (src/pmtb/decision/pipeline.py): Factory returns configured pipeline, evaluate() outputs list[TradeDecision]
- `TradeDecision` (src/pmtb/decision/models.py): Has ticker, approved, side, quantity, edge, ev, kelly_f, p_model, p_market
- `Settings` (src/pmtb/config.py): Pydantic-settings with existing thresholds — add scan_interval, stale_order_timeout, price_offset
- `main.py`: Startup wiring exists (settings, logging, DB, kalshi, executor, reconciler, metrics) — needs pipeline loop

### Established Patterns
- Pydantic models as pipeline contracts between phases
- Settings class for all configurable thresholds
- Prometheus metrics (counters, histograms) for observability
- Loguru structured logging with .bind() and cycle_id correlation
- create_executor() factory for paper/live mode switching
- Graceful degradation on failures (Phase 3 research pipeline pattern)

### Integration Points
- main.py wires everything — replace stop_event.wait() with pipeline orchestrator loop
- Pipeline orchestrator consumes MarketScanner, ResearchPipeline, ProbabilityPipeline, DecisionPipeline, and Executor
- WebSocket client runs alongside main loop for fill tracking and price-change triggers
- Watchdog process starts alongside main bot process
- Docker compose starts both bot + PostgreSQL

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 06-execution-integration-and-deployment*
*Context gathered: 2026-03-10*
