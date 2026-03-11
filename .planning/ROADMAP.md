# Roadmap: PMTB — Prediction Market Trading Bot

## Overview

PMTB is built as a linear multi-agent async pipeline: scan Kalshi markets, gather research signals in parallel, generate a calibrated probability estimate via XGBoost + Claude ensemble, detect edge against the market-implied price, gate through fractional Kelly sizing and hard risk controls, then execute and track. The roadmap follows strict dependency order — each phase delivers typed output contracts consumed by the next. Infrastructure and market scanning come first because every other component imports from them. The probability model and decision layer follow once their input types exist. Execution and deployment wire the running system together. Performance tracking and the model learning loop close the feedback cycle once resolved trade data accumulates.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Infrastructure Foundation** - Kalshi API client, PostgreSQL schema, async DB layer, configuration, paper trading mode, and structured logging (completed 2026-03-10)
- [x] **Phase 2: Market Scanner** - Scan all Kalshi markets and filter candidates by liquidity, volume, spread, time-to-resolution, and volatility (completed 2026-03-10)
- [x] **Phase 3: Research Signal Pipeline** - Parallel async research agents (Twitter/X, Reddit, RSS, Google Trends) with NLP classification and signal persistence (completed 2026-03-10)
- [x] **Phase 4: Probability Model** - XGBoost classifier with calibration, gated Claude LLM reasoning, and Bayesian updating into final p_model (completed 2026-03-10)
- [x] **Phase 5: Decision Layer** - Edge detection, fractional Kelly sizing, and multi-layer risk management with independent circuit breaker (completed 2026-03-10)
- [ ] **Phase 6: Execution, Integration, and Deployment** - Order placement, fill tracking, end-to-end pipeline wiring, paper trading validation, and Docker deployment
- [ ] **Phase 7: Performance Tracking and Learning Loop** - Brier score, Sharpe, losing trade analysis, automated XGBoost retraining, and backtesting engine

## Phase Details

### Phase 1: Infrastructure Foundation
**Goal**: The system can connect to Kalshi and PostgreSQL with production-grade reliability — token refresh, error categorization, schema migrations, paper trading mode, and configuration all work before any trading logic is written
**Depends on**: Nothing (first phase)
**Requirements**: INFR-01, INFR-02, INFR-03, INFR-04, INFR-05, INFR-06, INFR-07, INFR-08
**Success Criteria** (what must be TRUE):
  1. A script can authenticate to Kalshi REST API, make a live API call, and automatically refresh a token before it expires without manual intervention
  2. A WebSocket connection to Kalshi streams real-time orderbook events and does not silently drop on network interruptions
  3. Alembic migrations run cleanly from scratch and produce the full database schema with all tables for trades, signals, model outputs, and metrics
  4. Configuration values (edge threshold, Kelly alpha, max drawdown) are loaded from environment variables and YAML; changing a value in config takes effect without code changes
  5. Paper trading mode can be toggled via config and routes all order calls to a no-op handler instead of the live Kalshi API
**Plans:** 4/4 plans complete
Plans:
- [ ] 01-01-PLAN.md — Project scaffolding, config, DB layer, logging, metrics
- [ ] 01-02-PLAN.md — Kalshi REST client with RSA-PSS auth and retry
- [ ] 01-03-PLAN.md — Paper trading mode with executor protocol
- [ ] 01-04-PLAN.md — WebSocket client and position reconciliation

### Phase 2: Market Scanner
**Goal**: The pipeline has a filtered list of tradeable Kalshi market candidates ready for downstream research — only markets that meet all quality thresholds pass through
**Depends on**: Phase 1
**Requirements**: SCAN-01, SCAN-02, SCAN-03, SCAN-04, SCAN-05, SCAN-06, SCAN-07
**Success Criteria** (what must be TRUE):
  1. Running the scanner against live Kalshi returns all active markets across all categories with no missing pages
  2. Markets below liquidity, volume, or spread thresholds are filtered out and the filtered count is logged; threshold values are configurable without code changes
  3. Markets too close or too far from resolution are excluded by the time-to-resolution filter
  4. The scanner outputs a typed list of MarketCandidate objects that downstream pipeline stages consume without additional parsing or validation
**Plans:** 2/2 plans complete
Plans:
- [ ] 02-01-PLAN.md — Scanner type contracts (MarketCandidate, ScanResult), filter functions, and config fields
- [ ] 02-02-PLAN.md — MarketScanner class with pagination, DB upsert, filter chain, enrichment, and scan loop

### Phase 3: Research Signal Pipeline
**Goal**: For each candidate market, all four research sources run in parallel and produce a normalized SignalBundle — the pipeline continues gracefully when any single source fails or times out
**Depends on**: Phase 2
**Requirements**: RSRCH-01, RSRCH-02, RSRCH-03, RSRCH-04, RSRCH-05, RSRCH-06, RSRCH-07, RSRCH-08, RSRCH-09
**Success Criteria** (what must be TRUE):
  1. For a given candidate market, all four research agents (Twitter/X, Reddit, RSS, Google Trends) fire concurrently via asyncio and complete faster than sequential execution
  2. Each signal is classified as bullish, bearish, or neutral with a confidence score; the classification is stored in the database with a timestamp
  3. When one research source returns an error or times out, the pipeline proceeds with the remaining sources and logs the failure — it does not halt
  4. Research signals are visible in PostgreSQL after a scan cycle, queryable by market ticker and timestamp
**Plans:** 4/4 plans complete
Plans:
- [ ] 03-01-PLAN.md — Type contracts (ResearchAgent Protocol, SignalBundle, models) and research config fields
- [ ] 03-02-PLAN.md — Sentiment classifier (VADER + Claude hybrid) and query constructor with TTL cache
- [ ] 03-03-PLAN.md — Research agents (Reddit, RSS, Google Trends active; Twitter/X stub)
- [ ] 03-04-PLAN.md — ResearchPipeline orchestrator with parallel execution, DB persistence, and integration tests

### Phase 4: Probability Model
**Goal**: Given a SignalBundle, the system produces a calibrated p_model with confidence interval — XGBoost provides the base estimate, Claude supplements only for uncertain markets, and Bayesian updating produces the final prediction
**Depends on**: Phase 3
**Requirements**: PRED-01, PRED-02, PRED-03, PRED-04, PRED-05, PRED-06, PRED-07
**Success Criteria** (what must be TRUE):
  1. XGBoost predict_proba output passes through CalibratedClassifierCV before use; the Brier score on a held-out set confirms calibration is better than the uncalibrated baseline
  2. Claude API is only called for markets where XGBoost confidence falls in the 0.4–0.6 band; a run over 100 candidate markets shows a Claude call rate well below 100%
  3. The model outputs a typed PredictionResult with p_model, confidence interval, and contributing signal weights for every candidate market
  4. All predictions are persisted to PostgreSQL with the model version and timestamp, visible after a pipeline run
**Plans:** 3/3 plans complete
Plans:
- [ ] 04-01-PLAN.md — Prediction types (PredictionResult), feature builder, XGBoost classifier with calibration and persistence
- [ ] 04-02-PLAN.md — Claude LLM predictor, probability combiner (log-odds/weighted avg), confidence interval computation
- [ ] 04-03-PLAN.md — ProbabilityPipeline orchestrator with cold-start/hybrid mode, LLM gating, DB persistence

### Phase 5: Decision Layer
**Goal**: Every trade candidate passes through three sequential gates — edge detection rejects sub-threshold opportunities, Kelly sizing produces a survivable position size, and the risk manager enforces hard portfolio limits with an independent watchdog that cannot be bypassed by exceptions in the main loop
**Depends on**: Phase 4
**Requirements**: EDGE-01, EDGE-02, EDGE-03, EDGE-04, SIZE-01, SIZE-02, SIZE-03, RISK-01, RISK-02, RISK-03, RISK-04, RISK-05, RISK-06, RISK-07, RISK-08
**Success Criteria** (what must be TRUE):
  1. A market with p_model - p_market below 0.04 is rejected at the edge detector and never reaches the sizer or executor; edge threshold is configurable
  2. Position sizes produced by fractional Kelly are capped at the configured maximum independent of Kelly output; a test with a large computed f* confirms the cap applies
  3. When simulated portfolio drawdown is forced above 8%, all new orders are blocked at the risk manager — even if the main loop continues running
  4. An independent watchdog process detects the 8% drawdown breach and halts trading even when the main process is unresponsive or hung
  5. The position tracker reflects all open positions in real time; a duplicate bet on the same market is detected and blocked before order placement
**Plans:** 3/3 plans complete
Plans:
- [ ] 05-01-PLAN.md — Decision types, EdgeDetector, KellySizer, Settings fields, TradingState migration
- [ ] 05-02-PLAN.md — PositionTracker and RiskManager with portfolio limits, VaR, drawdown, hedge, duplicate detection
- [ ] 05-03-PLAN.md — Watchdog process and DecisionPipeline orchestrator

### Phase 6: Execution, Integration, and Deployment
**Goal**: The complete pipeline runs end-to-end on a schedule — scanner feeds research feeds predictor feeds decision layer feeds executor — with paper trading confirming the data flow is correct before any live capital is deployed, and the system ships to a cloud VPS via Docker
**Depends on**: Phase 5
**Requirements**: EXEC-01, EXEC-02, EXEC-03, EXEC-04, EXEC-05, DEPL-01, DEPL-02, DEPL-03
**Success Criteria** (what must be TRUE):
  1. In paper trading mode, a full scan cycle completes without errors — scanner finds candidates, research runs, predictions are generated, edge is evaluated, sizing is computed, and simulated orders are logged to the database
  2. A limit order placed on Kalshi is tracked through partial fills via WebSocket; stale unfilled orders are cancelled after the configured timeout
  3. Every order, fill, and cancellation is persisted to PostgreSQL and queryable after the cycle completes
  4. `docker compose up` starts the full system (bot + PostgreSQL) locally with a single command; the system starts and begins scan cycles
  5. The Docker image deploys to a cloud VPS and the bot runs 24/7 with structured JSON logs confirming each scan cycle
**Plans:** 4 plans
Plans:
- [ ] 06-01-PLAN.md — OrderRepository, enhanced PaperOrderExecutor, is_paper migration, Settings additions
- [ ] 06-02-PLAN.md — FillTracker with WS fills, stale cancellation, REST polling fallback
- [ ] 06-03-PLAN.md — PipelineOrchestrator and main.py wiring
- [ ] 06-04-PLAN.md — Docker deployment (Dockerfile, docker-compose, .env.example)

### Phase 7: Performance Tracking and Learning Loop
**Goal**: The system knows whether its predictions are improving or degrading — Brier score, Sharpe ratio, and win rate are computed on resolved trades, losing trades are classified by error type, and the XGBoost model is automatically retrained when calibration degrades, with a backtesting engine validating strategy changes before deployment
**Depends on**: Phase 6
**Requirements**: PERF-01, PERF-02, PERF-03, PERF-04, PERF-05, PERF-06, PERF-07, PERF-08
**Success Criteria** (what must be TRUE):
  1. After trades resolve, Brier score, Sharpe ratio, win rate, and profit factor are computed and stored; the values are queryable from PostgreSQL
  2. Each losing trade is classified by error type (wrong signal weighting, LLM error, edge decay, etc.) and the classification is persisted
  3. When Brier score degrades beyond the configured threshold, the learning loop automatically triggers XGBoost retraining on recent resolved trade data and logs the retraining event
  4. The backtesting engine runs the same predictor and sizer code paths against historical data with a swapped data source; temporal integrity is enforced (no feature timestamps after decision timestamp)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Infrastructure Foundation | 4/4 | Complete   | 2026-03-10 |
| 2. Market Scanner | 2/2 | Complete   | 2026-03-10 |
| 3. Research Signal Pipeline | 4/4 | Complete   | 2026-03-10 |
| 4. Probability Model | 3/3 | Complete   | 2026-03-10 |
| 5. Decision Layer | 3/3 | Complete   | 2026-03-10 |
| 6. Execution, Integration, and Deployment | 0/4 | Planning complete | - |
| 7. Performance Tracking and Learning Loop | 0/TBD | Not started | - |
