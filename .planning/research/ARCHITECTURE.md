# Architecture Research

**Domain:** AI-powered prediction market trading bot (Kalshi)
**Researched:** 2026-03-09
**Confidence:** MEDIUM-HIGH (patterns from open-source Kalshi bots + multi-agent trading literature + Kalshi official API docs)

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATION LAYER                        │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │              Main Event Loop / Scheduler                  │    │
│  │  (asyncio — triggers scan cycle every N minutes)         │    │
│  └──────────┬───────────────────────────────────────────────┘    │
└─────────────┼────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────┐
│                         PIPELINE LAYER                            │
│                                                                    │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐     │
│  │  Scanner  │→ │ Research  │→ │Predictor  │→ │   Edge    │     │
│  │  Agent    │  │  Agents   │  │  Agent    │  │ Detector  │     │
│  └───────────┘  └───────────┘  └───────────┘  └─────┬─────┘     │
│                                                       │           │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐         │           │
│  │  Learner  │← │ Tracker   │← │ Executor  │←────────┘           │
│  │  Agent    │  │  Agent    │  │  Agent    │                      │
│  └───────────┘  └───────────┘  └─────┬─────┘                     │
│                                       │                           │
│                              ┌────────┴────────┐                 │
│                              │  Risk Manager   │                 │
│                              │  (gate keeper)  │                 │
│                              └─────────────────┘                 │
└──────────────────────────────────────────────────────────────────┘
              │                          │
              ▼                          ▼
┌─────────────────────┐    ┌─────────────────────────────────┐
│   EXTERNAL APIs     │    │       INFRASTRUCTURE LAYER       │
│  ┌───────────────┐  │    │  ┌──────────┐  ┌─────────────┐  │
│  │  Kalshi REST  │  │    │  │PostgreSQL│  │  Redis      │  │
│  │  Kalshi WS    │  │    │  │(primary) │  │  (cache/    │  │
│  ├───────────────┤  │    │  └──────────┘  │   state)    │  │
│  │  Twitter/X    │  │    │  ┌──────────┐  └─────────────┘  │
│  │  Reddit       │  │    │  │  XGBoost │                    │
│  │  RSS/News     │  │    │  │  models  │                    │
│  │  Google Trends│  │    │  │  (files) │                    │
│  ├───────────────┤  │    │  └──────────┘                    │
│  │  Claude API   │  │    └─────────────────────────────────┘
│  └───────────────┘  │
└─────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| Main Event Loop | Triggers scan cycles, coordinates pipeline, enforces global halt on drawdown breach | Python asyncio + APScheduler or simple while-loop with sleep |
| Scanner Agent | Polls Kalshi REST/WS for active markets, filters by liquidity, spread, volume, time-to-resolution | kalshi-python SDK + async HTTP |
| Research Agents | Parallel data gathering from Twitter/X, Reddit, RSS, Google Trends; returns raw signal bundles | asyncio.gather() across 4 parallel sub-agents |
| Predictor Agent | Combines NLP sentiment, XGBoost classifier, Claude LLM reasoning, Bayesian update → p_model | scikit-learn pipeline + Anthropic SDK |
| Edge Detector | Computes p_model - p_market; gates on >= 4% threshold | Pure arithmetic, stateless |
| Risk Manager | Checks exposure limits, VaR, drawdown ceiling before any order; can halt system | Reads live positions from DB/Redis |
| Sizer | Fractional Kelly calculation (alpha 0.25–0.5) → contract quantity | Pure function: f(p, odds, bankroll, alpha) |
| Executor Agent | Places orders on Kalshi, monitors fills, handles partial fills, triggers hedges | kalshi-python SDK + WebSocket for fill events |
| Tracker Agent | Records trade outcomes, signals, model outputs to PostgreSQL | SQLAlchemy async |
| Learner Agent | Analyzes losing trades, retrains/updates models, computes Brier/Sharpe/win-rate | Scheduled job, scikit-learn, pandas |
| Backtesting Engine | Simulates pipeline on historical data with same strategy logic; no live calls | Separate runtime mode, same predictor/sizer code |

## Recommended Project Structure

```
pmtb/
├── agents/                  # one file per pipeline stage
│   ├── scanner.py           # market filtering logic
│   ├── researcher.py        # parallel signal gathering (orchestrates sub-agents)
│   ├── predictor.py         # probability model: XGBoost + Claude + Bayesian
│   ├── edge_detector.py     # threshold gate
│   ├── sizer.py             # Kelly calculation
│   ├── executor.py          # order placement and fill handling
│   ├── tracker.py           # trade recording
│   └── learner.py           # model improvement loop
├── risk/
│   ├── manager.py           # pre-trade risk checks
│   ├── limits.py            # constants: max exposure, drawdown, bet size
│   └── var.py               # VaR calculation
├── models/
│   ├── xgboost_model.py     # feature engineering + XGBoost wrapper
│   ├── llm_reasoner.py      # Claude API prompt builder + response parser
│   ├── bayesian_updater.py  # prior + likelihood → posterior probability
│   └── artifacts/           # saved XGBoost model files (.json / .pkl)
├── data/
│   ├── kalshi_client.py     # REST + WebSocket client wrapper
│   ├── twitter_client.py
│   ├── reddit_client.py
│   ├── rss_client.py
│   ├── trends_client.py
│   └── normalizer.py        # canonicalize signals to shared schema
├── db/
│   ├── models.py            # SQLAlchemy ORM: trades, signals, predictions, metrics
│   ├── migrations/          # Alembic migrations
│   └── queries.py           # typed query helpers
├── backtest/
│   ├── engine.py            # replay historical data through pipeline
│   ├── data_loader.py       # load historical Kalshi market snapshots
│   └── reporter.py          # performance stats output
├── core/
│   ├── loop.py              # main orchestration loop (asyncio)
│   ├── config.py            # settings from env vars (pydantic-settings)
│   └── logger.py            # structured logging setup
├── tests/
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── main.py                  # entrypoint: live | backtest | dry-run modes
```

### Structure Rationale

- **agents/:** Each pipeline stage is isolated; easy to stub for testing, replace, or disable without touching other stages.
- **risk/:** Risk is kept orthogonal — the manager is called as a synchronous gate before execution, not embedded inside executor logic.
- **models/:** ML code is separated from agent orchestration; models can be retrained or swapped without touching pipeline wiring.
- **data/:** Each external API gets its own client; the normalizer converts heterogeneous responses to a shared `SignalBundle` type.
- **backtest/:** Physically separate from live code but imports the same `agents/predictor.py` and `risk/` modules to ensure parity.
- **core/:** Infrastructure concerns (loop, config, logging) are not scattered across business logic.

## Architectural Patterns

### Pattern 1: Linear Agent Pipeline with a Risk Gate

**What:** Each stage passes a typed data object to the next. The Risk Manager sits between Sizer and Executor as a blocking synchronous check — if it fails, the pipeline short-circuits and no order is placed.

**When to use:** Always for this system. Prediction → sizing → risk check → execution is the non-negotiable order.

**Trade-offs:** Simple to reason about; no parallel execution of trade decisions. Acceptable here because latency tolerance is minutes, not milliseconds.

**Example:**
```python
async def run_pipeline(market: Market) -> TradeResult | None:
    signals = await researcher.gather(market)
    prediction = await predictor.predict(market, signals)
    edge = edge_detector.check(prediction, market.implied_prob)
    if not edge:
        return None
    size = sizer.compute(prediction, portfolio.bankroll)
    if not risk_manager.approve(market, size, portfolio):
        return None
    result = await executor.place_order(market, size, prediction)
    await tracker.record(market, signals, prediction, result)
    return result
```

### Pattern 2: Parallel Research Sub-Agents

**What:** The researcher agent fans out 4 concurrent coroutines (Twitter, Reddit, RSS, Google Trends) using `asyncio.gather()`, then aggregates results into a single `SignalBundle`.

**When to use:** Any time you have multiple independent I/O-bound calls. Research latency is the bottleneck for edge decay — parallelism is critical.

**Trade-offs:** One slow or failing source does not block others if you use `asyncio.gather(return_exceptions=True)`. Requires handling partial failures gracefully.

**Example:**
```python
async def gather(market: Market) -> SignalBundle:
    results = await asyncio.gather(
        twitter_client.search(market.topic),
        reddit_client.search(market.topic),
        rss_client.fetch(market.categories),
        trends_client.query(market.keywords),
        return_exceptions=True,
    )
    return normalizer.merge(market, results)
```

### Pattern 3: Probability Ensemble with Gated LLM

**What:** XGBoost produces a base probability from structured features. Claude is only called when XGBoost confidence is in the uncertain range (e.g., 0.4–0.6) to avoid burning API budget. Bayesian updating combines both.

**When to use:** When LLM calls are expensive and not every market justifies them. Claude adds value on ambiguous predictions, not on high-confidence ones.

**Trade-offs:** Requires a threshold decision for when to invoke LLM. XGBoost-only path is fast and cheap; LLM path adds ~2–5 seconds latency and API cost.

### Pattern 4: Dual Execution Mode (Live vs Backtest)

**What:** `main.py` accepts a `--mode` flag. Backtest mode swaps the Kalshi client for a historical replay client that feeds the same pipeline with past data. All agent logic is identical — only the data source and executor differ.

**When to use:** Before any live deployment. Backtest must pass before enabling live mode.

**Trade-offs:** Requires careful market simulation (fill simulation, slippage model) to avoid overfitting to historical data. Risk of lookahead bias if historical signals are loaded naively.

## Data Flow

### Primary Trade Cycle Flow

```
Kalshi WebSocket/REST
    ↓ (market snapshot: ticker, yes_bid, yes_ask, volume, expiry)
Scanner Agent
    ↓ (filtered MarketCandidate list)
Research Agents (parallel)
    ↓ (SignalBundle: sentiment scores, trending topics, news headlines)
Predictor Agent
    ├── XGBoost (tabular features) → base_prob
    ├── Claude LLM (if base_prob ambiguous) → llm_prob
    └── Bayesian Updater → p_model
    ↓ (PredictionResult: p_model, confidence, sources_used)
Edge Detector
    ↓ (passes only if p_model - p_market > 0.04)
Sizer
    ↓ (TradeIntent: ticker, direction, quantity, kelly_fraction)
Risk Manager
    ↓ (approved TradeIntent or rejection)
Executor Agent
    ↓ (OrderResult: fill_price, filled_qty, order_id)
Tracker Agent
    ↓ (writes to PostgreSQL: trades, signals, predictions tables)
Learner Agent (scheduled, not on hot path)
    ↓ (model artifacts, performance metrics)
PostgreSQL
```

### State Management Flow

```
Redis (fast, ephemeral)
    ├── Current portfolio state: cash balance, open positions, total exposure
    ├── Running drawdown calculation vs peak
    ├── Rate limit counters (sliding window per API)
    └── Pipeline lock (prevents overlapping scan cycles)

PostgreSQL (durable)
    ├── trades: every order placed, fill price, result
    ├── signals: raw research data per market per cycle
    ├── predictions: p_model, p_market, confidence, LLM used
    ├── performance: Brier score, Sharpe, win_rate per rolling window
    └── markets: Kalshi market metadata cache
```

### Key Data Types (Interfaces Between Components)

```
Market         → scanner output, executor input
SignalBundle   → researcher output, predictor input
PredictionResult → predictor output, edge_detector input
TradeIntent    → sizer output, risk_manager input
OrderResult    → executor output, tracker input
PortfolioState → risk_manager reads, tracker writes
```

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Single operator, <50 markets/cycle | Monolith with asyncio is fine — no distributed infrastructure needed |
| Single operator, 50–200 markets/cycle | Add Redis for portfolio state caching; ensure DB queries are indexed by ticker and timestamp |
| Multi-operator or high-frequency | Split agents into separate processes with a message queue (Redis Streams or RabbitMQ); horizontally scale research workers |

### Scaling Priorities

1. **First bottleneck: Research latency.** Twitter/Reddit rate limits slow the pipeline. Fix with caching (TTL-keyed by topic hash in Redis) and pre-fetching during market scan.
2. **Second bottleneck: Claude API cost + latency.** Fix with the gated LLM pattern — only invoke for ambiguous predictions. Cache LLM responses keyed by market + signal fingerprint with short TTL.

## Anti-Patterns

### Anti-Pattern 1: Embedding Risk Checks Inside the Executor

**What people do:** Put drawdown checks and position limit logic inside the order placement function.
**Why it's wrong:** Risk logic becomes tangled with API integration code; hard to test independently; easy to bypass accidentally when refactoring executor.
**Do this instead:** Risk Manager is a dedicated, synchronous module called before the executor. The executor assumes the trade is already approved and only handles Kalshi API mechanics.

### Anti-Pattern 2: Polling Kalshi REST for Fill Updates

**What people do:** After placing an order, poll `GET /orders/{id}` in a loop to check fill status.
**Why it's wrong:** Wastes rate limit budget (Basic tier: 20 reads/sec total); introduces latency; can hit limits during busy periods.
**Do this instead:** Subscribe to Kalshi WebSocket order channel for real-time fill events. Fall back to polling only on WebSocket disconnect.

### Anti-Pattern 3: Shared Mutable State in Pipeline Agents

**What people do:** Use global dictionaries or class-level variables to share portfolio state between agents.
**Why it's wrong:** Concurrent async tasks can produce race conditions; hard to test; makes replay/backtest impossible.
**Do this instead:** Portfolio state lives in Redis with atomic operations (INCR, SET NX) for counters; agents read state at the start of their execution and do not mutate shared objects.

### Anti-Pattern 4: Using the Same Code Path for Backtesting and Adding Live I/O Side-Effects

**What people do:** Add `if backtest: skip_this` guards scattered through agent code.
**Why it's wrong:** Makes the codebase hard to reason about; easy to forget a guard and accidentally write to DB or call APIs during backtest.
**Do this instead:** Dependency injection — pass in a `KalshiClient` interface. Live mode gets the real client; backtest mode gets a `HistoricalReplayClient` implementing the same interface.

### Anti-Pattern 5: Calling Claude for Every Market

**What people do:** Route all markets through the LLM reasoning step for maximum signal quality.
**Why it's wrong:** Claude API costs accumulate rapidly at scale; latency increases; most markets with clear XGBoost signals don't benefit from LLM reasoning.
**Do this instead:** Implement the gated LLM pattern — XGBoost first, Claude only when base model confidence falls in an uncertain band (configurable threshold).

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Kalshi REST API | `kalshi-python` SDK (official PyPI package); RSA-PSS auth | Basic tier: 20 read/sec, 10 write/sec. Respect per-endpoint limits. |
| Kalshi WebSocket | AsyncAPI subscription for market data and fill events | Reconnect logic required; feeds Scanner and Executor |
| Twitter/X API | v2 filtered stream or search recent via tweepy | Rate-limited; cache recent results by topic hash |
| Reddit API | PRAW async wrapper or PSAW for pushshift data | Respect 60 req/min for OAuth clients |
| RSS/News feeds | feedparser or aiohttp fetch + BeautifulSoup | Cheapest signal source; poll every 5–10 minutes |
| Google Trends | pytrends (unofficial) | No official API; throttles aggressively; cache heavily |
| Claude API | anthropic Python SDK; structured output via JSON mode | Budget per-call; use async client for non-blocking I/O |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Scanner → Research Agents | Direct async function call, passes `Market` object | In-process; no queue needed at single-operator scale |
| Research Agents → Predictor | Direct call, passes `SignalBundle` dataclass | Normalizer ensures consistent schema across all sources |
| Predictor → Edge Detector | Direct call, passes `PredictionResult` | Stateless computation; no I/O |
| Edge Detector → Risk Manager | Direct call, passes `TradeIntent` | Risk Manager reads `PortfolioState` from Redis |
| Risk Manager → Executor | Direct call if approved; raises exception to abort pipeline if rejected | Hard gate — no approval = no execution |
| Executor → Tracker | Direct async call post-fill | Non-blocking; Tracker writes to DB in background task |
| Tracker → PostgreSQL | SQLAlchemy async (asyncpg driver) | Write all events; queries indexed by ticker, timestamp, trade_id |
| Risk Manager → Redis | Read-only during trade decision; Tracker writes portfolio state updates | Redis as source of truth for live portfolio state |

## Build Order (Phase Dependencies)

The component dependency graph dictates this build sequence:

```
1. Infrastructure (DB schema + Kalshi client)
   └── Everything depends on this

2. Scanner Agent
   └── Needed to identify markets before any other work

3. Research Agents (parallel, can build concurrently)
   └── Needed to generate signals for predictor

4. Predictor Agent (XGBoost first, LLM second, Bayesian third)
   └── Depends on Research; each sub-model can ship incrementally

5. Edge Detector + Sizer + Risk Manager
   └── Stateless/pure logic; build together as "decision layer"

6. Executor Agent
   └── Depends on Risk Manager approval; requires Kalshi write access

7. Tracker + Performance Monitoring
   └── Can trail execution; needed before Learner

8. Main Event Loop (wires all agents together)
   └── Integration point; only works when all components exist

9. Backtesting Engine
   └── Reuses Predictor, Sizer, Risk Manager; swap client only

10. Learner Agent (model retraining loop)
    └── Depends on sufficient trade history in DB; build last
```

## Sources

- Kalshi official API documentation: https://docs.kalshi.com/welcome
- Kalshi rate limits: https://docs.kalshi.com/getting_started/rate_limits
- TradingAgents multi-agent LLM framework: https://tradingagents-ai.github.io/
- TradingAgents paper: https://arxiv.org/abs/2412.20138
- News-driven Polymarket bot architecture: https://www.quantvps.com/blog/news-driven-polymarket-bots
- Multi-agent financial intelligence system: https://medium.com/@ZainDataAI/beyond-simple-trading-bots-architecting-a-multi-agent-financial-intelligence-system-39342abfab50
- Kalshi AI trading bot (open source reference): https://github.com/ryanfrigo/kalshi-ai-trading-bot
- Polymarket bot with Kelly Criterion: https://github.com/djienne/Polymarket-bot
- Asynchronous event-driven trading (aat framework): https://github.com/AsyncAlgoTrading/aat
- Backtesting architecture patterns: https://www.quantstart.com/articles/backtesting-systematic-trading-strategies-in-python-considerations-and-open-source-frameworks/

---
*Architecture research for: AI-powered prediction market trading bot (Kalshi)*
*Researched: 2026-03-09*
