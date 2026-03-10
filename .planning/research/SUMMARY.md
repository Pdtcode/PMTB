# Project Research Summary

**Project:** PMTB — AI-Powered Prediction Market Trading Bot (Kalshi)
**Domain:** Autonomous algorithmic trading bot for binary prediction markets
**Researched:** 2026-03-09
**Confidence:** MEDIUM-HIGH

## Executive Summary

PMTB is a 24/7 autonomous trading bot targeting the Kalshi prediction market exchange. The research consensus is clear: successful systems in this domain are built as multi-stage async pipelines — scan markets, gather research signals in parallel, generate a calibrated probability estimate via ML + LLM ensemble, detect edge against the market-implied price, size using fractional Kelly, gate through a hard risk check, and execute. The entire workload is I/O-bound (API calls, DB reads, LLM requests), making Python asyncio with `kalshi-python-async` the natural runtime. The stack is well-defined: Python 3.11+, XGBoost + scikit-learn for the probability model, Claude (`anthropic` SDK) as a gated LLM reasoning layer, PostgreSQL + SQLAlchemy async for persistence, APScheduler for the scan loop, and Pydantic for typed inter-component contracts.

The recommended approach is to build in strict dependency order: infrastructure first (DB schema, Kalshi client), then scanner, then research signal pipeline, then the prediction model, then the decision layer (edge detection, Kelly sizing, risk management), then execution and tracking, and finally the model learning loop. The Backtesting Engine defers naturally to after the live pipeline is working — it reuses the same predictor and sizer code with a swapped data source. Every component boundary should be a typed Pydantic dataclass, enabling clean testing and incremental development.

The dominant risks are not technical complexity but model integrity risks: using uncalibrated XGBoost probabilities in the Kelly formula, treating Claude's probability outputs as ground truth, and implementing a drawdown circuit breaker that can be bypassed by exceptions in the main trading loop. These three issues together account for the majority of known failure modes documented across open-source Kalshi bots and quantitative trading literature. All three must be addressed before any live capital is deployed.

---

## Key Findings

### Recommended Stack

The core runtime is Python 3.11 with a fully async pipeline — every component from scanner to executor uses `asyncio`. The official `kalshi-python-async` (3.8.0) SDK is the only viable Kalshi client; the sync variant blocks the event loop and the old `kalshi-python` package is deprecated. LLM reasoning is handled via the `anthropic` SDK (0.84.0) with structured outputs (`client.messages.parse()` via the `structured-outputs-2025-11-13` beta header) — no LangChain layer. PostgreSQL 16 with `asyncpg` (via SQLAlchemy 2.0 async engine) is the persistence layer; SQLite is explicitly excluded due to no concurrent write support and poor time-series query performance.

One version compatibility flag requires validation before locking: `kalshi-python-async` 3.8.0 has conflicting Python version metadata (PyPI metadata says >=3.13, description says >=3.9). The project targets Python 3.11; this must be verified before freezing the environment.

**Core technologies:**
- `kalshi-python-async` 3.8.0: Kalshi exchange client — only async-native official SDK; sync and deprecated variants are explicitly excluded
- `anthropic` 0.84.0: Claude API — native async, structured outputs for deterministic probability estimates from LLM reasoning
- `xgboost` 3.2.0 + `scikit-learn` 1.8.0: Probability model — XGBoost for binary classification, scikit-learn `CalibratedClassifierCV` for post-hoc probability calibration (required, not optional)
- `sqlalchemy` 2.0.48 + `asyncpg` 0.31.0: Async DB layer — type-safe ORM with `AsyncSession`, fastest Python PostgreSQL driver
- `pydantic` 2.12.5: Inter-component contracts — all data types crossing agent boundaries must be Pydantic models
- `apscheduler` 3.11.2: Job scheduling — `AsyncIOScheduler` runs in the existing event loop; APScheduler 4.x alpha is explicitly avoided
- `loguru` 0.7.3: Structured logging — `logger.bind(trade_id=...)` provides per-decision context for post-trade analysis
- `alembic` 1.18.4: Schema migrations — initialized with `alembic init -t async` for async compatibility
- `uv`: Dependency management — 10-100x faster than pip/Poetry; `uv.lock` for reproducible installs

### Expected Features

**Must have (table stakes — v1 launch blockers):**
- Kalshi API integration (REST + WebSocket) — without this, nothing functions
- Market scanner with liquidity/volume/spread/time-to-resolution filters — gates what the system touches
- Research signal pipeline (at minimum RSS + one social source) — provides input for probability model
- Probability model: XGBoost + Claude ensemble — core edge-generation component
- Edge detection with 4% minimum threshold — prevents trading on noise
- Fractional Kelly sizing (alpha = 0.25) — survivable position sizes under model uncertainty
- Risk controls: max exposure, max bet size, 8% drawdown hard halt — safety layer
- Trade execution with partial fill handling — places and manages orders on Kalshi
- Position tracking — prevents duplicate bets, tracks live exposure
- Trade and order history persistence (PostgreSQL) — required for performance analysis and model feedback
- Structured logging — operator visibility into every decision
- Paper trading / dry-run mode — validate pipeline before live capital

**Should have (v1.x after live validation):**
- Performance attribution (Brier score, Sharpe, profit factor, per-category breakdown) — trigger: need to understand which signals drive returns
- Losing trade analysis pipeline — trigger: systematic model improvement needed
- Model learning loop (automated retraining on resolved trades) — trigger: manual retraining becomes a bottleneck
- Auto-hedging on correlated markets — trigger: excessive directional exposure becomes a risk concern
- Full 4-source signal pipeline (Twitter/X, Reddit, RSS, Google Trends) — v1 can launch with 2-3 sources

**Defer (v2+):**
- Backtesting framework — Kalshi historical data is sparse; paper trading provides better early validation
- Advanced ML (GARCH volatility, cointegration-based stat arb) — defer until simpler ensemble is calibrated and profitable
- Telegram/email alerting — structured logs suffice for v1; add when operational monitoring becomes a burden

### Architecture Approach

The system is a linear agent pipeline running inside a single async event loop, with one important fan-out: the research stage fans out 4 concurrent I/O calls (Twitter/X, Reddit, RSS, Google Trends) via `asyncio.gather(return_exceptions=True)`, then aggregates into a typed `SignalBundle`. The pipeline order is non-negotiable — prediction before sizing before risk check before execution. The Risk Manager is a synchronous blocking gate between Sizer and Executor; it reads live portfolio state from Redis (fast, ephemeral) and vetoes orders before any Kalshi API call. PostgreSQL holds the durable audit trail. State management is split deliberately: Redis for hot operational state (drawdown, exposure counters, rate limit tracking), PostgreSQL for everything that must survive a restart.

**Major components:**
1. Scanner Agent — polls Kalshi REST/WebSocket, filters markets by liquidity, spread, volume, time-to-resolution
2. Research Agents (parallel) — `asyncio.gather()` across Twitter/X, Reddit, RSS, Google Trends; normalizes to shared `SignalBundle` schema
3. Predictor Agent — XGBoost base probability, gated Claude LLM reasoning (only when XGBoost confidence is 0.4-0.6), Bayesian update to final `p_model`
4. Edge Detector — stateless: `p_model - p_market >= 0.04`; rejects trade candidates that don't clear the bar
5. Sizer — fractional Kelly (`alpha=0.25`): pure function of `(p, odds, bankroll, alpha)`
6. Risk Manager — synchronous gate: checks max exposure, VaR, drawdown ceiling; reads Redis, halts system if breached
7. Executor Agent — Kalshi order placement, WebSocket fill monitoring, partial fill handling
8. Tracker Agent — async DB writes of trades, signals, predictions to PostgreSQL
9. Learner Agent — scheduled job: analyzes resolved trades, retrains XGBoost, computes Brier/Sharpe
10. Main Event Loop (APScheduler) — triggers scan cycles, coordinates pipeline, enforces global halt

### Critical Pitfalls

1. **Uncalibrated XGBoost probabilities** — `predict_proba()` output is not a calibrated probability; it must go through `CalibratedClassifierCV(method='isotonic')` before any use in Kelly sizing or edge detection. Measure with Brier score and reliability diagrams. This is the single most common silent failure mode.

2. **Missing hard circuit breaker (two-layer)** — a single `if drawdown > 0.08` inline in the main loop can be bypassed by exceptions. Requires two independent layers: (1) a Risk Manager module with its own error handling that runs before every order, and (2) an independent watchdog process that polls account equity and halts trading even if the main process is hung. Must be integration-tested with a forced 8.1% drawdown scenario.

3. **LLM probability outputs treated as ground truth** — Claude returns a plausible-sounding float, but LLMs are not calibrated probability estimators. The LLM output must be a weighted input to the XGBoost ensemble, not the decision authority. Gate Claude API calls behind XGBoost uncertainty band (0.4-0.6 confidence) to control both cost and over-reliance.

4. **Kalshi API token expiry causing silent halt** — tokens expire periodically; if the bot catches 401 errors and continues the main loop, it silently stops trading while appearing healthy. Implement a proactive token refresh manager and treat consecutive auth failures as fatal (halt + alert).

5. **Kelly overbetting under edge overestimation** — a systematically miscalibrated model producing false 4% edges across hundreds of trades can still cause catastrophic drawdown even with fractional Kelly. Enforce a hard maximum position cap (e.g., 5% of bankroll) independent of Kelly output, and require model confidence intervals to exclude `p_market` before trading.

---

## Implications for Roadmap

Based on the component dependency graph from ARCHITECTURE.md and the pitfall-to-phase mapping from PITFALLS.md, the suggested phase structure is:

### Phase 1: Infrastructure Foundation
**Rationale:** Everything in the pipeline depends on the Kalshi client and database schema. Building this first provides the foundation all subsequent phases build on. Token management and error categorization (transient/rate-limit/fatal) must be correct here — they cannot be retrofitted later without touching every agent.
**Delivers:** Working Kalshi API client (REST + WebSocket) with proactive token refresh, PostgreSQL schema with Alembic migrations, async DB layer, project skeleton (`agents/`, `risk/`, `models/`, `data/`, `db/`, `core/`), configuration management (Pydantic `BaseSettings`), structured logging
**Addresses:** Kalshi API integration, configuration management, trade history persistence (schema)
**Avoids:** Kalshi API token expiry silent halt (Pitfall 5); establishes `.env` in `.gitignore` from day one (security)

### Phase 2: Market Scanner
**Rationale:** The scanner is the entry point for every trade cycle. Without it, no other pipeline component has input. Its output (a filtered `MarketCandidate` list) is the typed interface that gates all downstream work.
**Delivers:** Scanner Agent filtering markets by liquidity, bid-ask spread, volume, time-to-resolution; `Market` Pydantic type; basic paper trading dry-run mode
**Addresses:** Market scanning and filtering (table stakes)
**Avoids:** Fetching all Kalshi markets on every scan cycle (performance trap — cache market metadata)

### Phase 3: Research Signal Pipeline
**Rationale:** The predictor cannot produce meaningful probability estimates without signal inputs. Research agents can be built and tested independently of the model, and their output schema (`SignalBundle`) defines the predictor's input interface. Start with 2 sources (RSS + Reddit) to validate the async fan-out pattern before adding rate-limited sources.
**Delivers:** Parallel research agents (RSS + Reddit at minimum), `SignalBundle` type, signal normalizer, source credibility weighting, anomalous volume spike detection
**Addresses:** Research signal pipeline (v1 with 2+ sources)
**Avoids:** Social media signal contamination from bots/spam (Pitfall 7); blocking event loop with synchronous clients

### Phase 4: Probability Model
**Rationale:** The model is the core value-generation component. Build XGBoost first (can train on any available data), validate calibration before wiring LLM, add Bayesian update last. The gated LLM pattern (only invoke Claude when XGBoost confidence is 0.4-0.6) must be implemented from day one to control API costs.
**Delivers:** XGBoost classifier with `CalibratedClassifierCV` post-hoc calibration, Brier score validation, gated Claude LLM integration (structured output via `client.messages.parse()`), Bayesian updater, `PredictionResult` type
**Addresses:** Probability model (XGBoost + Claude ensemble)
**Avoids:** Uncalibrated XGBoost probabilities (Pitfall 1 — critical); LLM output as ground truth (Pitfall 6); LLM called for every market (anti-pattern 5 / performance trap)

### Phase 5: Decision Layer (Edge, Sizing, Risk)
**Rationale:** These three components form a single logical gate that sits between prediction and execution. They are stateless or nearly stateless pure functions — build them together. The circuit breaker must be the first risk feature implemented, not the last.
**Delivers:** Edge Detector (4% configurable threshold), fractional Kelly Sizer (alpha=0.25, hard max cap), Risk Manager (max exposure, VaR, drawdown halt), two-layer circuit breaker (inline Risk Manager + independent watchdog process), `TradeIntent` type
**Addresses:** Edge detection, fractional Kelly sizing, risk controls (drawdown halt, max exposure)
**Avoids:** Kelly overbetting under edge overestimation (Pitfall 3); missing hard circuit breaker (Pitfall 2 — critical); drawdown check as single inline `if` statement

### Phase 6: Trade Execution and Tracking
**Rationale:** Execution requires Risk Manager approval to be wired in; Tracker writes the audit trail that every subsequent phase (performance, learning) depends on. Partial fill handling and contract resolution ambiguity handling must be correct here — they affect P&L accuracy for the entire lifetime of the system.
**Delivers:** Executor Agent (order placement, WebSocket fill monitoring, partial fill handling), Tracker Agent (async DB writes), correct settlement price storage (actual API value, not inferred), market halt status handling, `OrderResult` type
**Addresses:** Trade execution, position tracking, order history persistence
**Avoids:** Contract resolution ambiguity (Pitfall 8); polling REST for fill updates instead of WebSocket (anti-pattern 2); P&L code inferring binary outcomes

### Phase 7: Main Event Loop and End-to-End Integration
**Rationale:** This is the wiring phase — APScheduler connects all agents into a running system. Only possible after all pipeline components exist. Paper trading smoke test validates the complete data flow before any live capital.
**Delivers:** APScheduler-driven scan loop, complete `run_pipeline()` async function, paper trading / dry-run mode (full pipeline, no real orders), Docker + docker-compose deployment, end-to-end integration tests
**Addresses:** Complete MVP pipeline; paper trading mode
**Avoids:** Shared mutable state across agents (anti-pattern 3); event loop blocking from synchronous calls

### Phase 8: Performance Tracking and Analysis
**Rationale:** Once the live pipeline has generated resolved trades, performance attribution becomes possible. The Brier score monitoring pipeline must exist before the Learner Agent can be built with retraining triggers.
**Delivers:** Rolling Brier score per market category, Sharpe ratio, profit factor, win rate, calibration reliability diagrams, losing trade analysis pipeline, per-source signal attribution, model version stored per trade
**Addresses:** Performance attribution (v1.x), losing trade analysis (v1.x)
**Avoids:** Model stagnation with no retraining cadence (Pitfall 9 — requires Brier score alert thresholds)

### Phase 9: Model Learning Loop
**Rationale:** Automated retraining requires sufficient resolved trade history (at minimum 50 resolved markets with Brier skill score validation). Cannot be built or validated meaningfully until Phase 8 data exists.
**Delivers:** Retraining trigger (Brier score degrades 15%+ from baseline), walk-forward retraining on last 90-180 days, model versioning, champion/challenger deployment gate
**Addresses:** Model learning loop (v1.x), model stagnation prevention
**Avoids:** Model stagnation / no regime change detection (Pitfall 9)

### Phase 10: Backtesting Engine (v2)
**Rationale:** Deferred intentionally. Kalshi historical data is sparse; paper trading in phases 2-7 provides better early validation than a potentially look-ahead-biased backtest. When built, the backtesting engine reuses the same `predictor.py`, `sizer.py`, and `risk/` modules — only the `KalshiClient` is swapped for a `HistoricalReplayClient` via dependency injection.
**Delivers:** Historical replay client, walk-forward simulation, temporal integrity assertions (`all(feature_ts < decision_ts)`), performance reporter
**Addresses:** Backtesting framework (v2+)
**Avoids:** Backtesting look-ahead bias (Pitfall 2); scattered `if backtest: skip_this` guards (anti-pattern 4)

### Phase Ordering Rationale

- Phases 1-2 are pure infrastructure with no business logic — they must come first because every later phase imports from them.
- Phase 3 precedes Phase 4 because the signal pipeline defines the predictor's input contract (`SignalBundle`).
- Phase 4 must be complete before Phase 5 because edge detection requires a calibrated `p_model` output.
- Phase 5 must be complete before Phase 6 because the executor requires Risk Manager approval as a hard gate.
- Phase 7 integration deliberately follows all pipeline components to avoid wiring against incomplete interfaces.
- Phases 8-9 require live resolved trade data — they have an inherent latency dependency on Phase 7 running in production.
- Phase 10 (backtesting) defers because paper trading provides faster, less bias-prone early validation for this domain.

### Research Flags

Phases likely needing deeper research during planning:

- **Phase 4 (Probability Model):** XGBoost calibration methodology for small prediction market datasets (few hundred resolved markets vs. millions in typical ML settings) needs validation. The Bayesian updating approach with combined XGBoost and LLM inputs needs a concrete implementation strategy. Recommend `/gsd:research-phase` here.
- **Phase 5 (Decision Layer):** The two-layer circuit breaker watchdog architecture has multiple valid implementation patterns (separate process, supervisor, OS-level monitor). The specific approach should be chosen during planning with operational constraints in mind.
- **Phase 9 (Learning Loop):** Walk-forward retraining cadence for prediction markets (which have slower resolution cycles than equities) is not well-documented. The minimum resolved trade count for valid retraining requires empirical judgment.

Phases with standard patterns (skip research-phase):

- **Phase 1 (Infrastructure):** SQLAlchemy async + asyncpg + Alembic is a well-documented standard pattern. kalshi-python-async SDK usage follows official Kalshi docs.
- **Phase 2 (Scanner):** Market filtering logic is straightforward Kalshi API integration.
- **Phase 6 (Execution):** Order placement and WebSocket fill handling follows standard patterns from Kalshi docs.
- **Phase 7 (Integration):** APScheduler `AsyncIOScheduler` and Docker compose patterns are well-documented.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified against PyPI; official SDK confirmed; compatibility matrix documented with one caveat (kalshi-python-async Python version metadata conflict needs empirical verification) |
| Features | MEDIUM-HIGH | Table stakes and dependency ordering are well-defined from Kalshi docs + open-source references; v1.x/v2 boundaries are judgment calls that may shift based on early live performance |
| Architecture | MEDIUM-HIGH | Pipeline structure validated across multiple open-source Kalshi bots and quantitative trading literature; Redis for hot state is a common pattern but adds operational complexity that may not be needed at MVP scale |
| Pitfalls | MEDIUM-HIGH | Core pitfalls (XGBoost calibration, circuit breaker, LLM trust) are verified against ML literature and production incident reports; social media manipulation scope is emerging/harder to quantify |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **kalshi-python-async Python version compatibility:** PyPI metadata says Python >=3.13, description says >=3.9. Must test with Python 3.11 before locking the environment. If incompatible, evaluate the community `AndrewNolte/KalshiPythonClient` wrapper as fallback.
- **Redis necessity at MVP scale:** ARCHITECTURE.md recommends Redis for hot portfolio state. At single-operator scale with <50 markets per cycle, an in-process async dict with atomic operations may suffice for MVP, deferring Redis until a concrete bottleneck is observed. This is a planning-phase decision.
- **XGBoost training data availability:** The prediction model requires historical Kalshi market + signal data for initial training. The strategy for bootstrapping the model before sufficient resolved trade history exists needs to be defined in planning. Options include synthetic data, market-implied priors, or starting with a rules-based edge detector and transitioning to ML once data accumulates.
- **Twitter/X API tier cost:** The v2 Basic tier is severely rate-limited. Planning must decide whether to include Twitter/X in the v1 signal pipeline (and budget for the API tier) or launch with Reddit + RSS only and add Twitter/X in v1.x.
- **Kalshi rate limit tier:** The Basic tier (20 reads/sec, 10 writes/sec) is sufficient for initial development; rate limit headroom should be monitored in paper trading and API tier upgrade planned if the scanner + execution pattern approaches limits.

---

## Sources

### Primary (HIGH confidence)
- Kalshi official API documentation: https://docs.kalshi.com/welcome
- Kalshi rate limits: https://docs.kalshi.com/getting_started/rate_limits
- Kalshi SDK overview and migration: https://docs.kalshi.com/sdks/overview
- PyPI: `kalshi-python-async`, `anthropic`, `xgboost`, `scikit-learn`, `sqlalchemy`, `asyncpg`, `alembic`, `pydantic`, `APScheduler`, `loguru`, `httpx`, `tweepy`, `asyncpraw`, `feedparser`, `pytrends`, `pandas`, `numpy` — all versions and compatibility verified
- Anthropic structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- SQLAlchemy async docs: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html

### Secondary (MEDIUM confidence)
- OctagonAI kalshi-deep-trading-bot (open source): https://github.com/OctagonAI/kalshi-deep-trading-bot
- Kalshi Quant TeleBot (open source): https://github.com/yllvar/Kalshi-Quant-TeleBot
- polymarket-kalshi-weather-bot (open source): https://github.com/suislanchez/polymarket-kalshi-weather-bot
- kalshi-ai-trading-bot (open source): https://github.com/ryanfrigo/kalshi-ai-trading-bot
- TradingAgents multi-agent LLM paper: https://arxiv.org/abs/2412.20138
- XGBoost probability calibration: https://xgboosting.com/predict-calibrated-probabilities-with-xgboost/
- QuantStart backtesting pitfalls: https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-I/
- QuantStart Kelly Criterion pitfalls: https://www.quantstart.com/articles/Money-Management-via-the-Kelly-Criterion/
- TradeTrap LLM trading agent reliability: https://arxiv.org/html/2512.02261v1
- Kalshi settlement dispute / Khamenei market: https://bettingscanner.com/prediction-markets/news/kalshi-khamenei-market-payout-backlash-explained
- QuantVPS news-driven Polymarket bot architecture: https://www.quantvps.com/blog/news-driven-polymarket-bots
- Building a quantitative prediction system for Polymarket: https://navnoorbawa.substack.com/p/building-a-quantitative-prediction

### Tertiary (LOW confidence)
- Prediction markets as learning algorithms (Gensyn): https://blog.gensyn.ai/prediction-markets-are-learning-algorithms/ — framing reference
- ForTraders trading bots lose money: https://www.fortraders.com/blog/trading-bots-lose-money — practitioner caution
- DEV Community AI Polymarket trading agents: https://dev.to/marvin_railey/ai-polymarket-trading-agents-how-autonomous-bots-are-reshaping-prediction-market-strategy-51l — editorial reference

---
*Research completed: 2026-03-09*
*Ready for roadmap: yes*
