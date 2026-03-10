# PMTB — Prediction Market Trading Bot

## What This Is

An AI-powered autonomous trading bot that detects mispriced probabilities on Kalshi prediction markets and trades them using Kelly-optimal sizing. The system operates as a multi-agent pipeline — scanning markets, gathering research signals, building probability models, and executing trades — all within strict risk controls. Built for a quant trading engineer who wants 24/7 automated alpha generation.

## Core Value

Reliably identify and exploit mispricings between model-predicted probabilities and market-implied probabilities, with risk controls that prevent catastrophic drawdowns.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Multi-agent pipeline: scan → research → predict → edge detect → size → risk check → execute → learn
- [ ] Market scanner that filters Kalshi markets by liquidity, volume, time-to-resolution, spread, and volatility
- [ ] Research agents that gather signals from Twitter/X, Reddit, RSS news feeds, and Google Trends in parallel
- [ ] NLP sentiment analysis and topic classification on research signals
- [ ] Probability model combining XGBoost classifier, Claude API LLM reasoning, and Bayesian updating
- [ ] Edge detection requiring minimum 4% edge (p_model - p_market > 0.04)
- [ ] Kelly Criterion position sizing with fractional Kelly (alpha = 0.25–0.5)
- [ ] Risk management: max exposure, max bet size, 95% VaR, max drawdown 8%
- [ ] Trade execution with order placement, slippage monitoring, partial fill handling, and auto-hedging
- [ ] Performance tracking: Brier score, Sharpe ratio, win rate, profit factor
- [ ] Losing trade analysis and model improvement loop
- [ ] PostgreSQL storage for trade history, signals, model outputs, and performance metrics
- [ ] Fully autonomous operation within risk limits (no human approval required)
- [ ] Backtesting system for strategy validation
- [ ] Local development + cloud deployment (Docker)

### Out of Scope

- Manual/approval trading mode — v1 is fully autonomous only
- Mobile app or web dashboard — CLI/logs only for v1
- Multi-exchange support — Kalshi only for v1
- Options/derivatives strategies — binary prediction markets only
- Social/copy trading features — single-operator system

## Context

- User has active Kalshi API credentials and a funded account
- Targeting all Kalshi market categories (politics, economics, weather, etc.) — filters decide what to trade
- Claude API (Anthropic) is the LLM provider for prediction reasoning
- System must run locally for development and on cloud VPS (Docker) for 24/7 production
- Data sources for v1: Twitter/X API, Reddit API, RSS/news feeds, Google Trends
- PostgreSQL for all persistent storage (trade history, signals, model outputs)
- Python is the implementation language
- System must be modular, asynchronous, and scalable

## Constraints

- **Risk**: Max drawdown hard limit of 8% — system halts trading if breached
- **Edge threshold**: Minimum 4% edge required before any trade executes
- **Kelly sizing**: Fractional Kelly only (alpha 0.25–0.5), never full Kelly
- **API rate limits**: Must respect Kalshi, Twitter, Reddit, and Google Trends rate limits
- **Latency**: Research pipeline must complete within reasonable time to capture edge before it decays
- **Cost**: Claude API calls per prediction must be budgeted — not every market gets LLM analysis

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| PostgreSQL over SQLite | Production-grade, complex queries, time-series analysis, concurrent access | — Pending |
| Claude API as LLM provider | Strong reasoning, structured output, good for probability analysis | — Pending |
| Fully autonomous mode | User is experienced quant, wants 24/7 operation, risk limits provide safety net | — Pending |
| All market categories | Let quantitative filters decide rather than manual category selection | — Pending |
| Fractional Kelly sizing | Full Kelly too aggressive for prediction markets, fractional reduces ruin probability | — Pending |

---
*Last updated: 2026-03-09 after initialization*
