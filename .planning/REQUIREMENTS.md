# Requirements: PMTB — Prediction Market Trading Bot

**Defined:** 2026-03-09
**Core Value:** Reliably identify and exploit mispricings between model-predicted probabilities and market-implied probabilities, with risk controls that prevent catastrophic drawdowns.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Infrastructure

- [x] **INFR-01**: System connects to Kalshi REST API with token-based authentication and automatic token refresh
- [x] **INFR-02**: System connects to Kalshi WebSocket API for real-time orderbook and fill event feeds
- [x] **INFR-03**: PostgreSQL database stores all trade history, signals, model outputs, and performance metrics
- [x] **INFR-04**: Database schema supports migrations via Alembic
- [x] **INFR-05**: Configuration is managed via environment variables and YAML config files (edge threshold, Kelly alpha, max drawdown, rate limits)
- [x] **INFR-06**: System implements exponential backoff on API rate limit (429) and server error (5xx) responses
- [x] **INFR-07**: System reconciles positions on restart to prevent orphaned orders
- [x] **INFR-08**: System runs in paper trading mode that simulates execution without placing real orders

### Market Scanning

- [x] **SCAN-01**: Scanner retrieves all active Kalshi markets across all categories
- [x] **SCAN-02**: Scanner filters markets by minimum liquidity threshold
- [x] **SCAN-03**: Scanner filters markets by minimum 24h volume threshold
- [x] **SCAN-04**: Scanner filters markets by time-to-resolution window (not too close, not too far)
- [x] **SCAN-05**: Scanner filters markets by maximum bid-ask spread
- [x] **SCAN-06**: Scanner filters markets by volatility criteria
- [x] **SCAN-07**: Scanner outputs typed candidate market objects consumed by downstream pipeline stages

### Research Signal Pipeline

- [x] **RSRCH-01**: Research agents gather sentiment signals from Twitter/X API for each candidate market
- [x] **RSRCH-02**: Research agents gather sentiment signals from Reddit API for each candidate market
- [x] **RSRCH-03**: Research agents gather relevant articles from RSS news feeds for each candidate market
- [x] **RSRCH-04**: Research agents gather search interest data from Google Trends for each candidate market
- [ ] **RSRCH-05**: All four research sources run in parallel using asyncio
- [x] **RSRCH-06**: NLP sentiment analysis classifies each signal as bullish/bearish/neutral with confidence score
- [x] **RSRCH-07**: Topic classification maps signals to relevant market categories
- [ ] **RSRCH-08**: Research pipeline continues with available signals if one source times out or fails
- [ ] **RSRCH-09**: Research signals are persisted to PostgreSQL with timestamps for later analysis

### Probability Modeling

- [ ] **PRED-01**: XGBoost binary classifier generates base probability estimates from market features and research signals
- [ ] **PRED-02**: XGBoost probabilities are calibrated using Platt scaling or isotonic regression (not raw predict_proba)
- [ ] **PRED-03**: Claude API provides structured probability reasoning for markets routed through LLM analysis
- [ ] **PRED-04**: LLM analysis is gated — only markets with sufficient edge potential or low XGBoost confidence get Claude calls
- [ ] **PRED-05**: Bayesian updating layer incorporates prior probability and new signal evidence to produce final p_model
- [ ] **PRED-06**: Model outputs typed prediction objects with p_model, confidence interval, and contributing signal weights
- [ ] **PRED-07**: All model predictions are persisted to PostgreSQL for performance tracking

### Edge Detection

- [ ] **EDGE-01**: System computes market-implied probability (p_market) from current Kalshi bid/ask prices
- [ ] **EDGE-02**: System computes expected value: EV = p_model * b - (1 - p_model)
- [ ] **EDGE-03**: System computes edge: edge = p_model - p_market
- [ ] **EDGE-04**: System only passes trades to sizing when edge > 0.04 (4% minimum)

### Position Sizing

- [ ] **SIZE-01**: System computes Kelly optimal fraction: f* = (p*b - q) / b
- [ ] **SIZE-02**: System applies fractional Kelly: f = alpha * f* with configurable alpha (0.25–0.5)
- [ ] **SIZE-03**: Position size is capped by risk management limits before order placement

### Risk Management

- [ ] **RISK-01**: System enforces maximum total portfolio exposure limit
- [ ] **RISK-02**: System enforces maximum single-bet size limit
- [ ] **RISK-03**: System computes 95% Value at Risk: VaR = μ − 1.645σ
- [ ] **RISK-04**: System halts all trading when portfolio drawdown exceeds 8%
- [ ] **RISK-05**: Circuit breaker is architecturally independent — watchdog process that can halt trading even if main loop is hung
- [ ] **RISK-06**: Position tracker maintains real-time view of all open positions and total exposure
- [ ] **RISK-07**: System auto-hedges when odds shift significantly against an open position
- [ ] **RISK-08**: System detects and prevents duplicate bets on the same market

### Trade Execution

- [ ] **EXEC-01**: System places limit orders on Kalshi via REST API
- [ ] **EXEC-02**: System handles partial fills and tracks fill status
- [ ] **EXEC-03**: System monitors slippage between expected and actual execution price
- [ ] **EXEC-04**: System cancels stale unfilled orders after configurable timeout
- [ ] **EXEC-05**: Every order, fill, and cancellation is persisted to PostgreSQL

### Performance & Learning

- [ ] **PERF-01**: System tracks Brier score across all resolved predictions
- [ ] **PERF-02**: System tracks Sharpe ratio of the portfolio
- [ ] **PERF-03**: System tracks win rate and profit factor
- [ ] **PERF-04**: System classifies losing trades by error type (wrong signal weighting, LLM error, edge decay, etc.)
- [ ] **PERF-05**: Model learning loop feeds resolved outcomes back into XGBoost retraining pipeline
- [ ] **PERF-06**: Learning loop triggers retraining when Brier score degrades beyond threshold
- [ ] **PERF-07**: Backtesting engine validates strategies against historical market data
- [ ] **PERF-08**: Backtesting uses same model/sizer code paths as live trading (no separate implementation)

### Deployment

- [ ] **DEPL-01**: System runs locally for development with single-command startup
- [ ] **DEPL-02**: System deploys to cloud VPS via Docker for 24/7 operation
- [ ] **DEPL-03**: Structured JSON logging to stdout for both local and cloud operation

## v2 Requirements

### Notifications

- **NOTF-01**: System sends Telegram alerts for critical events (drawdown warnings, circuit breaker activation, large wins/losses)
- **NOTF-02**: System sends daily P&L summary via email or Telegram

### Advanced Modeling

- **ADVML-01**: GARCH volatility modeling for improved VaR estimation
- **ADVML-02**: Cointegration-based statistical arbitrage across correlated Kalshi markets

### Operations

- **OPS-01**: Web dashboard showing portfolio state, P&L, and active positions
- **OPS-02**: API tier upgrade to Kalshi Advanced/Premier for higher rate limits

## Out of Scope

| Feature | Reason |
|---------|--------|
| Multi-exchange support (Polymarket, etc.) | Doubles integration surface area; Kalshi alone provides sufficient opportunity |
| Mobile app | CLI/logs sufficient for single-operator system |
| Social/copy trading | Single-operator autonomous bot, not a platform |
| Full Kelly sizing | Provably ruin-prone with imperfect calibration — fractional Kelly only |
| HFT/sub-second optimization | Kalshi is event-driven, not microstructure-driven; edge is in prediction quality, not speed |
| Real-time web dashboard (v1) | Engineering overhead with no trading value; structured logs suffice |
| Automatic strategy discovery | Produces overfit strategies without proper controls |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFR-01 | Phase 1 | Complete |
| INFR-02 | Phase 1 | Complete |
| INFR-03 | Phase 1 | Complete |
| INFR-04 | Phase 1 | Complete |
| INFR-05 | Phase 1 | Complete |
| INFR-06 | Phase 1 | Complete |
| INFR-07 | Phase 1 | Complete |
| INFR-08 | Phase 1 | Complete |
| SCAN-01 | Phase 2 | Complete |
| SCAN-02 | Phase 2 | Complete |
| SCAN-03 | Phase 2 | Complete |
| SCAN-04 | Phase 2 | Complete |
| SCAN-05 | Phase 2 | Complete |
| SCAN-06 | Phase 2 | Complete |
| SCAN-07 | Phase 2 | Complete |
| RSRCH-01 | Phase 3 | Complete |
| RSRCH-02 | Phase 3 | Complete |
| RSRCH-03 | Phase 3 | Complete |
| RSRCH-04 | Phase 3 | Complete |
| RSRCH-05 | Phase 3 | Pending |
| RSRCH-06 | Phase 3 | Complete |
| RSRCH-07 | Phase 3 | Complete |
| RSRCH-08 | Phase 3 | Pending |
| RSRCH-09 | Phase 3 | Pending |
| PRED-01 | Phase 4 | Pending |
| PRED-02 | Phase 4 | Pending |
| PRED-03 | Phase 4 | Pending |
| PRED-04 | Phase 4 | Pending |
| PRED-05 | Phase 4 | Pending |
| PRED-06 | Phase 4 | Pending |
| PRED-07 | Phase 4 | Pending |
| EDGE-01 | Phase 5 | Pending |
| EDGE-02 | Phase 5 | Pending |
| EDGE-03 | Phase 5 | Pending |
| EDGE-04 | Phase 5 | Pending |
| SIZE-01 | Phase 5 | Pending |
| SIZE-02 | Phase 5 | Pending |
| SIZE-03 | Phase 5 | Pending |
| RISK-01 | Phase 5 | Pending |
| RISK-02 | Phase 5 | Pending |
| RISK-03 | Phase 5 | Pending |
| RISK-04 | Phase 5 | Pending |
| RISK-05 | Phase 5 | Pending |
| RISK-06 | Phase 5 | Pending |
| RISK-07 | Phase 5 | Pending |
| RISK-08 | Phase 5 | Pending |
| EXEC-01 | Phase 6 | Pending |
| EXEC-02 | Phase 6 | Pending |
| EXEC-03 | Phase 6 | Pending |
| EXEC-04 | Phase 6 | Pending |
| EXEC-05 | Phase 6 | Pending |
| DEPL-01 | Phase 6 | Pending |
| DEPL-02 | Phase 6 | Pending |
| DEPL-03 | Phase 6 | Pending |
| PERF-01 | Phase 7 | Pending |
| PERF-02 | Phase 7 | Pending |
| PERF-03 | Phase 7 | Pending |
| PERF-04 | Phase 7 | Pending |
| PERF-05 | Phase 7 | Pending |
| PERF-06 | Phase 7 | Pending |
| PERF-07 | Phase 7 | Pending |
| PERF-08 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 62 total
- Mapped to phases: 62
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-09*
*Last updated: 2026-03-09 after roadmap creation*
