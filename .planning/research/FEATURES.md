# Feature Research

**Domain:** AI-powered prediction market trading bot (Kalshi)
**Researched:** 2026-03-09
**Confidence:** MEDIUM-HIGH (primary sources from Kalshi docs + existing open-source bots + quantitative trading literature)

---

## Feature Landscape

### Table Stakes (Users Expect These)

These are non-negotiable for any functional trading bot. A system missing these is not a trading bot — it is a script.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Kalshi API integration (REST + WebSocket) | Cannot trade without market access | LOW | Kalshi offers Basic tier (20 read/10 write req/s) on signup; use WebSocket for real-time orderbook feeds, REST for order placement |
| Market scanning and filtering | Without filtering, the bot attempts to trade every open market including illiquid or near-expiry positions | MEDIUM | Filter by: liquidity, volume_24h, bid-ask spread, time-to-resolution. Kalshi API exposes all required fields |
| Order execution (market + limit orders) | Core function of the bot | LOW-MEDIUM | Must handle create, cancel, amend; partial fills; batch operations count toward write rate limits |
| Position tracking | Know what you own at all times | LOW | Track open positions, average entry, current P&L; duplicate bet prevention is critical |
| Risk controls (max exposure, bet size caps, drawdown halt) | Prevent catastrophic loss; this is the safety layer | MEDIUM | Hard drawdown limit (8% in PROJECT.md); max per-trade cap; VaR; circuit breaker that halts all trading when breached |
| Fractional Kelly position sizing | Well-established optimal sizing under uncertainty; full Kelly is ruin-prone in practice | MEDIUM | Quarter-Kelly (alpha=0.25) is the standard; requires calibrated probability input — garbage probabilities produce dangerous sizes |
| Trade and order history persistence | Audit trail, performance analysis, model feedback | LOW | PostgreSQL; every order, fill, and resolution stored |
| Logging and run-state visibility | Operators must know what the bot is doing without a UI | LOW | Structured logging (JSON) to stdout + file; log every decision with rationale |
| Paper trading / dry-run mode | Test strategies without risking capital | LOW | Essential for development and strategy iteration; Kalshi provides a demo environment |
| Configuration management | Parameters must be tunable without code changes | LOW | Environment variables or YAML config: edge threshold, Kelly alpha, max drawdown, rate limit values |
| Error handling and recovery | APIs fail; network drops; orders partially fill | MEDIUM | Exponential backoff on 429/5xx; position reconciliation on restart; no orphaned orders |

### Differentiators (Competitive Advantage)

These separate a system that has edge from one that does not. The project's core value ("identify and exploit mispricings") lives here.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Multi-source research signal pipeline | Single sources (e.g., news only) miss corroborating signals; parallel aggregation from Twitter/X, Reddit, RSS, Google Trends gives fuller probability picture | HIGH | Requires async pipeline; each source has its own rate limits and failure modes; signals must be normalized and timestamped |
| LLM-augmented probability estimation (Claude) | LLM reasoning captures qualitative context (legal ambiguity, policy nuance, event context) that statistical models miss | HIGH | Not every market warrants LLM cost; route only high-opportunity or low-confidence markets through Claude; prompt must ask for calibrated probability, not just direction |
| Hybrid ML + LLM model (XGBoost + Claude + Bayesian updating) | Ensemble outperforms any single model; XGBoost handles quantitative features; Claude handles qualitative; Bayesian updating incorporates prior knowledge and new signals | HIGH | Feature alignment is a critical failure mode (see PITFALLS); XGBoost trained on historical market + signal data; Bayesian layer updates prior as new signals arrive |
| Edge detection with hard minimum threshold | Prevents trading on noise; 4% minimum edge means the model must be significantly more confident than the market before any capital is committed | LOW | Threshold is configurable; start conservative (4-5%), tune based on calibration results |
| Automated model learning loop | Bot improves over time by feeding resolved market outcomes back into the model | HIGH | Requires: trade logging → resolution tracking → feature reconstruction → model retraining trigger; Brier score is the primary calibration metric |
| Performance attribution (Brier score, Sharpe, profit factor) | Distinguish luck from skill; know which market categories and signal sources actually contribute edge | MEDIUM | Rolling Brier score per category; Sharpe ratio overall; win rate and profit factor; per-source signal attribution |
| Losing trade analysis pipeline | Systematic identification of why the model was wrong; feeds model improvement | MEDIUM | On resolution: compare model prediction vs outcome; classify error type (wrong signal weighting, LLM hallucination, edge decay before execution); log for retraining |
| Backtesting framework | Validate strategy on historical data before deploying capital | HIGH | Backtesting prediction markets is harder than equities: sparse historical data, resolution-timing effects, survivorship bias; needs realistic slippage and fill assumptions |
| Async parallel research pipeline | Research latency is a first-class constraint; edge decays as other bots reprice the market | MEDIUM | asyncio with bounded concurrency; timeout per source; continue with available signals if one source times out |
| Auto-hedging on correlated markets | Reduce directional exposure when model is uncertain; hedge with opposing position in correlated contract | HIGH | Kalshi has many related markets (e.g., "Fed rate cut March" + "Fed rate cut May"); auto-hedger needs correlation mapping |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Real-time web dashboard / UI | Visibility into bot state is desirable | Adds significant engineering overhead (frontend, auth, websocket server) with no trading value in v1; PROJECT.md explicitly excludes it | Structured JSON logs + CLI commands for status queries; add Telegram alerts for critical events (optional) |
| Full Kelly sizing | Maximizes expected log-wealth in theory | Kelly is derived from perfectly calibrated probabilities — real model probabilities are never perfectly calibrated, and full Kelly produces extreme bet sizes that cause rapid ruin | Fractional Kelly (alpha 0.25-0.5); cap per-trade size absolutely |
| Multi-exchange support (Polymarket, etc.) | Broader opportunity set | Cross-exchange arbitrage requires market matching logic, different settlement mechanics, different APIs, and legal jurisdiction complexity; doubles integration surface area | Single-exchange focus; Kalshi covers politics, economics, weather — sufficient opportunity |
| Copy trading / strategy marketplace | Monetization appeal | Requires user accounts, strategy attribution, legal compliance, and a fundamentally different product architecture | Not applicable to single-operator autonomous bot |
| Fully autonomous LLM decision-making | Appealing to reduce engineering | LLMs hallucinate, over- or under-calibrate, and cannot be backtested reliably; using LLM output directly as a trade signal without quantitative gating is dangerous | LLM provides probability estimate as one input; quantitative edge gate (4%) is the final decision authority |
| High-frequency / sub-second execution | Speed = edge in theory | Kalshi is not an HFT market; binary event outcomes are determined by external events, not microstructure; optimizing for microseconds buys nothing and creates operational complexity | Focus latency budget on research pipeline completion time (minutes, not milliseconds) |
| Automatic strategy discovery / genetic optimization | "Let the bot find its own strategies" | Without proper controls, this produces overfit strategies with no real edge; compounding model uncertainty with strategy uncertainty is a path to ruin | Manual strategy definition with quantitative parameters; tune parameters with held-out validation sets |
| Social / copy trading features | Community engagement | Single-operator system by design (PROJECT.md); social features require user accounts, legal review, regulatory concerns | Out of scope for v1 |

---

## Feature Dependencies

```
[Market Scanner]
    └──requires──> [Kalshi API Integration]
                       └──requires──> [Credentials & Config Management]

[Trade Execution]
    └──requires──> [Kalshi API Integration]
    └──requires──> [Position Tracker]
    └──requires──> [Risk Controls]

[Probability Model (XGBoost + Claude + Bayesian)]
    └──requires──> [Research Signal Pipeline]
    └──requires──> [Market Scanner] (provides market context)

[Edge Detection]
    └──requires──> [Probability Model]
    └──requires──> [Market Scanner] (provides market-implied probability)

[Kelly Position Sizing]
    └──requires──> [Edge Detection] (requires model probability + market probability)
    └──requires──> [Risk Controls] (Kelly output capped by risk limits)

[Risk Controls]
    └──requires──> [Position Tracker]
    └──requires──> [Trade and Order History Persistence]

[Performance Tracking (Brier, Sharpe, etc.)]
    └──requires──> [Trade and Order History Persistence]
    └──requires──> [Market resolution data from Kalshi API]

[Model Learning Loop]
    └──requires──> [Performance Tracking]
    └──requires──> [Losing Trade Analysis]
    └──requires──> [Probability Model] (retraining target)

[Backtesting Framework]
    └──requires──> [Probability Model] (same interface)
    └──requires──> [Kelly Position Sizing] (same interface)
    └──requires──> [Historical market data]

[Auto-Hedging]
    └──requires──> [Trade Execution]
    └──requires──> [Position Tracker]
    └──enhances──> [Risk Controls]

[Research Signal Pipeline]
    └──requires──> [External API credentials] (Twitter/X, Reddit, RSS, Google Trends)
    └──enhances──> [Probability Model]
```

### Dependency Notes

- **Risk Controls require Position Tracker:** You cannot enforce exposure limits without knowing current positions.
- **Kelly Sizing requires Edge Detection (not raw model output):** Sizing before confirming edge produces positions on noise signals.
- **Model Learning Loop requires resolved trade history:** Brier score computation requires knowing the actual outcome, which is available only after market resolution. The feedback loop has inherent latency (days to months depending on market).
- **Backtesting requires same model interface as live trading:** If the backtest uses a different code path than live execution, results are misleading. A clean abstraction layer (strategy interface) is essential.
- **Auto-Hedging conflicts with aggressive position sizing:** Running Kelly-optimal sizing and then hedging reduces expected value. Hedging should be a risk-management override, not a default behavior.

---

## MVP Definition

### Launch With (v1)

Minimum viable product — what's needed to validate that the core pipeline (scan → research → predict → edge detect → size → risk check → execute) produces live edge with real capital.

- [ ] Kalshi API integration (REST + WebSocket) — without this, nothing works
- [ ] Market scanner with liquidity/volume/spread/time-to-resolution filters — gates what the system touches
- [ ] Research signal pipeline (at minimum: RSS/news + one social source) — provides signal for probability model
- [ ] Probability model (XGBoost + Claude Claude as ensemble; Bayesian updating optional at v1) — core edge-generation component
- [ ] Edge detection with 4% minimum threshold — prevents trading on noise
- [ ] Fractional Kelly sizing (alpha=0.25) — ensures survivable position sizes
- [ ] Risk controls: max exposure, max bet size, 8% drawdown halt — prevents catastrophic loss
- [ ] Trade execution with partial fill handling — places and manages orders
- [ ] Position tracking — prevents duplicate bets, tracks exposure
- [ ] Trade and order history persistence (PostgreSQL) — required for performance analysis
- [ ] Structured logging — operators must see what the bot is doing
- [ ] Paper trading / dry-run mode — validate pipeline before live capital

### Add After Validation (v1.x)

Add once the core pipeline is confirmed to produce real edge (i.e., positive Brier skill score on 50+ resolved markets).

- [ ] Performance attribution dashboard (Brier score, Sharpe, profit factor, per-category breakdown) — trigger: need to understand which markets and signal sources drive performance
- [ ] Losing trade analysis pipeline — trigger: deployed capital and need systematic model improvement
- [ ] Model learning loop (automated retraining on resolved trades) — trigger: manual retraining becomes a bottleneck
- [ ] Auto-hedging — trigger: excessive directional exposure in correlated markets becomes a risk concern
- [ ] Full async parallel signal pipeline (all four sources: Twitter/X, Reddit, RSS, Google Trends) — v1 can launch with 2-3 sources and add the rest

### Future Consideration (v2+)

Defer until product-market fit is established and the v1 pipeline is operationally stable.

- [ ] Backtesting framework — defer because Kalshi historical data is sparse and backtesting prediction markets requires careful methodology to avoid look-ahead bias; validate on live paper trading first
- [ ] Advanced ML features (GARCH volatility modeling, cointegration-based stat arb across correlated markets) — defer until simpler ensemble is calibrated
- [ ] Alerting / notifications (Telegram, email) — nice-to-have for monitoring; structured logs suffice for v1
- [ ] API tier upgrade (Advanced/Premier) for higher rate limits — trigger: hitting Basic tier limits (20 read/10 write per second) in production

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Kalshi API integration | HIGH | LOW | P1 |
| Market scanner + filters | HIGH | LOW | P1 |
| Risk controls + drawdown halt | HIGH | MEDIUM | P1 |
| Trade execution | HIGH | MEDIUM | P1 |
| Position tracking | HIGH | LOW | P1 |
| Fractional Kelly sizing | HIGH | LOW | P1 |
| Edge detection (4% threshold) | HIGH | LOW | P1 |
| Research signal pipeline (2+ sources) | HIGH | HIGH | P1 |
| Probability model (XGBoost + Claude) | HIGH | HIGH | P1 |
| Trade history persistence (PostgreSQL) | HIGH | LOW | P1 |
| Structured logging | HIGH | LOW | P1 |
| Paper trading mode | MEDIUM | LOW | P1 |
| Performance attribution (Brier, Sharpe) | HIGH | MEDIUM | P2 |
| Losing trade analysis | HIGH | MEDIUM | P2 |
| Full 4-source signal pipeline | MEDIUM | MEDIUM | P2 |
| Auto-hedging | MEDIUM | HIGH | P2 |
| Model learning loop | HIGH | HIGH | P2 |
| Backtesting framework | MEDIUM | HIGH | P3 |
| Telegram / notification alerts | LOW | LOW | P3 |
| Advanced ML (GARCH, stat arb) | MEDIUM | HIGH | P3 |

**Priority key:**
- P1: Must have for launch — MVP incomplete without it
- P2: Should have — add when core pipeline is validated
- P3: Nice to have — future consideration

---

## Competitor Feature Analysis

| Feature | OctagonAI Kalshi Bot | Kalshi-Quant-TeleBot | polymarket-kalshi-weather-bot | PMTB (This Project) |
|---------|---------------------|---------------------|-------------------------------|----------------------|
| Multi-agent research pipeline | Deep Research API | News sentiment NLP | GFS ensemble forecasts | Twitter/X + Reddit + RSS + Trends |
| LLM integration | OpenAI for decision making | Not present | Not present | Claude for probability reasoning |
| Position sizing | Fixed cap ($25 default) | Kelly Criterion | Fractional Kelly (15%) | Fractional Kelly (alpha 0.25-0.5) |
| Edge threshold | Confidence filter only | Not explicit | 2-8% depending on market | 4% hard minimum |
| Risk management | Auto-hedging (25%) | Multi-layer stop-loss | Daily loss limits, circuit breakers | 8% drawdown halt, VaR, max exposure |
| Performance tracking | Trade summaries | Sharpe, win rate, drawdown | Brier score, P&L curves | Brier score, Sharpe, profit factor |
| Model learning loop | None | Not described | Not described | Losing trade analysis + retraining |
| Backtesting | Not present | Not described | Not described | Planned (v1 or v2) |
| Database | Not described | Not described | SQLite | PostgreSQL |
| Deployment | Not described | Docker (microservices) | Local | Docker, cloud VPS |

---

## Sources

- [Kalshi API Rate Limits Documentation](https://docs.kalshi.com/getting_started/rate_limits) — HIGH confidence (official)
- [Kalshi API Get Markets Reference](https://docs.kalshi.com/api-reference/market/get-markets) — HIGH confidence (official)
- [OctagonAI Kalshi Deep Trading Bot](https://github.com/OctagonAI/kalshi-deep-trading-bot) — MEDIUM confidence (open source reference)
- [Kalshi Quant TeleBot](https://github.com/yllvar/Kalshi-Quant-TeleBot) — MEDIUM confidence (open source reference)
- [polymarket-kalshi-weather-bot](https://github.com/suislanchez/polymarket-kalshi-weather-bot) — MEDIUM confidence (open source reference)
- [Building a Quantitative Prediction System for Polymarket](https://navnoorbawa.substack.com/p/building-a-quantitative-prediction) — MEDIUM confidence (practitioner writeup)
- [Prediction Markets are Learning Algorithms — Gensyn](https://blog.gensyn.ai/prediction-markets-are-learning-algorithms/) — MEDIUM confidence
- [Bot for Kalshi Platform](https://www.botforkalshi.com/) — MEDIUM confidence (commercial reference)
- [Why Most Trading Bots Lose Money — ForTraders](https://www.fortraders.com/blog/trading-bots-lose-money) — MEDIUM confidence (practitioner)
- [The New Financial Oracle — FinancialContent/PredictStreet](https://markets.financialcontent.com/stocks/article/predictstreet-2026-1-30-the-new-financial-oracle-how-algorithmic-bots-turned-prediction-markets-into-the-worlds-fastest-data-feed) — LOW confidence (editorial)
- [AI Polymarket Trading Agents — DEV Community](https://dev.to/marvin_railey/ai-polymarket-trading-agents-how-autonomous-bots-are-reshaping-prediction-market-strategy-51l) — LOW confidence (community post)

---
*Feature research for: AI-powered prediction market trading bot (Kalshi)*
*Researched: 2026-03-09*
