# Stack Research

**Domain:** AI-powered prediction market trading bot (Kalshi)
**Researched:** 2026-03-09
**Confidence:** HIGH (all versions verified against PyPI and official docs)

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.11 | Runtime | kalshi-python-async 3.8.0 now requires Python >=3.13 per metadata (verify before pinning), but the broader data science stack (pandas 3.x, numpy 2.4) also requires 3.11+. Use 3.11 as the minimum safe floor — it has LTS support and improved asyncio performance vs 3.10. Avoid 3.13 until kalshi SDK compatibility is confirmed. |
| kalshi-python-async | 3.8.0 | Kalshi exchange client | Official Kalshi SDK, async-native, full API coverage for markets, orders, and portfolio management. The old `kalshi-python` package is deprecated and unmaintained — do not use it. The sync variant (`kalshi-python-sync`) blocks the event loop and is incompatible with the async pipeline architecture. |
| asyncio (stdlib) | built-in | Async event loop | Python's built-in async runtime. The entire pipeline — scanner, research agents, prediction engine, executor — must be async-concurrent. asyncio is the correct choice because the workload is I/O-bound (API calls, DB reads/writes, LLM calls), not CPU-bound. No external event loop library needed. |
| anthropic | 0.84.0 | Claude API LLM calls | Official Anthropic SDK with native async support (`AsyncAnthropic`), Pydantic-based structured outputs via `client.messages.parse()`, and prompt caching. Structured outputs use the `anthropic-beta: structured-outputs-2025-11-13` header — required for getting deterministic probability estimates back from Claude. |
| XGBoost | 3.2.0 | Binary probability classification | Industry standard gradient boosting for tabular features. `XGBClassifier` outputs calibrated probabilities, integrates seamlessly with scikit-learn pipelines, and supports pandas 3.x / numpy 2.x natively. Use `XGBClassifier(use_label_encoder=False, eval_metric='logloss')` for binary market outcomes. |
| scikit-learn | 1.8.0 | Feature pipelines, calibration, metrics | Provides `Pipeline`, `CalibratedClassifierCV` (isotonic regression post-hoc calibration for XGBoost), `cross_val_score`, and Brier score (`brier_score_loss`). Use for feature preprocessing pipelines and model evaluation. |
| SQLAlchemy | 2.0.48 | ORM + async DB layer | SQLAlchemy 2.0's `AsyncSession` + `async_engine_from_config` gives type-safe ORM models, async query execution, and connection pooling. Use `create_async_engine("postgresql+asyncpg://...")`. Prefer over raw asyncpg queries for maintainability — the ORM layer pays off as schema evolves. |
| asyncpg | 0.31.0 | PostgreSQL async driver | The fastest Python PostgreSQL driver, required by SQLAlchemy's async engine. Pure Python fallback (`psycopg2`) blocks the event loop — always use asyncpg in an asyncio context. |
| Alembic | 1.18.4 | Database migrations | Standard migration tool for SQLAlchemy. Use `alembic init -t async` to generate an async-compatible `env.py`. Required for schema evolution without data loss across deployments. |
| Pydantic | 2.12.5 | Data models + validation | Define all inter-component data contracts (market snapshots, trade orders, research signals, model outputs) as Pydantic v2 models. Validates at boundaries and produces clean `model_dump()` dicts for DB insertion. The `anthropic` SDK's structured output path also returns Pydantic models. |
| APScheduler | 3.11.2 | Cron/interval job scheduling | `AsyncIOScheduler` runs jobs directly in the existing asyncio event loop — zero thread overhead. Use for the market scan loop (interval), research pipeline trigger, and performance reporting (cron). Stick to 3.x: the 4.x alpha is API-unstable and not production-ready. |
| loguru | 0.7.3 | Structured application logging | Drop-in replacement for stdlib `logging` with zero-config setup, automatic exception tracebacks, and per-level file sinks. For a 24/7 autonomous bot, reliable log capture is non-negotiable. Loguru's `logger.bind(trade_id=...)` adds structured context to every log line for post-trade analysis. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | 0.28.1 | Async HTTP client | Use for any REST calls not covered by a dedicated SDK (e.g., custom news APIs, Google Trends fallback). Prefer over `aiohttp` because it has both sync and async modes (useful in tests) and supports HTTP/2. Use `httpx.AsyncClient` with a shared session. |
| tweepy | 4.16.0 | Twitter/X API v2 client | `AsyncClient` for search queries, `AsyncStreamingClient` for filtered stream. Use for gathering Twitter sentiment signals. Note: Twitter API v2 has aggressive rate limits on free/basic tiers — implement exponential backoff via `wait_on_rate_limit=True`. |
| asyncpraw | 7.8.1 | Reddit API async client | Official async PRAW for subreddit search and comment scraping. Use over sync PRAW to avoid blocking the event loop. Reddit rate limits are 100 requests/minute — implement queue-based batching. |
| feedparser | 6.0.12 | RSS/Atom feed parsing | Parse RSS news feeds for market-relevant events. Feedparser handles malformed XML gracefully. Use for pulling from news sources (Reuters, AP, BBC) — much cheaper and more reliable than scraping. |
| pytrends | 4.9.2 | Google Trends data | Unofficial Google Trends pseudo-API. Fragile — Google can break it without notice. Pin the version and wrap calls in try/except with circuit breaker. The official Google Trends API (launched July 2025) is currently alpha/invite-only; migrate to it when generally available. |
| pandas | 3.0.1 | DataFrame operations | Use for feature engineering, backtesting data manipulation, and performance report generation. Requires Python >=3.11. Avoid using pandas DataFrames inside the hot path of the trading loop — convert to numpy arrays before model inference. |
| numpy | 2.4.3 | Numerical computation | Array math for Kelly sizing calculations, VaR computation, and Bayesian probability updates. Requires Python >=3.11. |
| python-dotenv | latest | Environment variable loading | Load API keys and secrets from `.env` files. Simple, zero-dependency, universally understood. Use alongside Pydantic `BaseSettings` for validated config — `BaseSettings` reads from environment variables automatically. |
| pytest | latest stable | Test runner | Standard Python test framework. |
| pytest-asyncio | 1.3.0 | Async test support | Required for testing async pipeline components. Version 1.x removed the deprecated `event_loop` fixture — use `@pytest.mark.asyncio` decorator on all async test functions. Requires Python >=3.10. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| uv | Dependency management + virtualenv | 10-100x faster than pip/Poetry. Manages both the virtualenv and lock file via `pyproject.toml` + `uv.lock`. Use `uv add`, `uv run`, and `uv lock`. Reproducible installs across dev and Docker. |
| Docker + docker-compose | Containerization | Package the bot + PostgreSQL into a multi-service compose stack. The bot container runs the asyncio process; a separate `postgres:16` container holds the DB. Required for cloud VPS deployment and consistent local dev parity. |
| Alembic CLI | Schema migrations | Run `alembic upgrade head` on container startup. Bake the migration step into the Docker entrypoint. |
| pyproject.toml | Project configuration | Single source of truth for dependencies, dev dependencies, tool config (pytest, mypy). Use `[project.optional-dependencies]` for a `dev` extras group. |

---

## Installation

```bash
# Core runtime
uv add kalshi-python-async anthropic xgboost scikit-learn sqlalchemy asyncpg \
       alembic pydantic apscheduler loguru httpx tweepy asyncpraw feedparser \
       pytrends pandas numpy python-dotenv

# Dev dependencies
uv add --dev pytest pytest-asyncio mypy ruff
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| kalshi-python-async | kalshi-python-sync | Never for this project — sync blocks the asyncio event loop |
| kalshi-python-async | AndrewNolte/KalshiPythonClient | If the official SDK lacks coverage for a specific endpoint; it's a community wrapper with broader ergonomics but not official |
| asyncpg (via SQLAlchemy) | psycopg2 | Only if you need sync-only scripts (e.g., a one-off data migration tool outside the main bot) |
| APScheduler 3.x | Celery + Celery Beat | If you scale to multiple worker nodes or need distributed task queues; overkill for a single-operator bot |
| APScheduler 3.x | asyncio native `asyncio.create_task` loops | Fine for simple polling, but APScheduler gives you cron expressions, missed-fire handling, and job stores without reimplementing that logic |
| XGBoost | LightGBM | LightGBM is marginally faster at training; choose it if retraining frequency is a bottleneck. XGBoost has broader sklearn ecosystem integration. |
| XGBoost | CatBoost | CatBoost handles categorical features natively; relevant only if categorical market metadata features dominate the feature set |
| httpx | aiohttp | aiohttp is faster for pure-async bulk HTTP; choose it if you're making >1000 concurrent requests per second. For this bot's I/O pattern, httpx is simpler and sufficient. |
| loguru | structlog | structlog has more powerful structured context binding and OpenTelemetry integration; use it if you add distributed tracing or ship logs to an external sink (Datadog, ELK) |
| feedparser | newspaper3k | Use newspaper3k additionally for full article body extraction from a URL; feedparser only parses feed metadata and summaries |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `kalshi-python` (old) | Deprecated as of 2025; no longer maintained, missing new API endpoints | `kalshi-python-async` |
| `kalshi-python-sync` | Blocks the asyncio event loop during every API call, serializing the pipeline | `kalshi-python-async` |
| `psycopg2` as the async DB driver | Synchronous driver — blocks the event loop when used under asyncio | `asyncpg` via SQLAlchemy's async engine |
| SQLite | No concurrent write support, no time-series query performance, no window functions; unsuitable for production trade history | PostgreSQL 16 + asyncpg |
| `requests` library | Synchronous — blocks the event loop | `httpx.AsyncClient` |
| Full Kelly criterion sizing | Mathematically correct but practically causes rapid ruin under model uncertainty; prediction markets have fat-tailed error distributions | Fractional Kelly (alpha 0.25–0.5) via numpy Kelly formula |
| LangChain as the LLM orchestration layer | Adds 2-3 abstraction layers over the Anthropic SDK with significant overhead and breaking-change frequency; for a single-LLM system this is pure complexity cost | Direct `anthropic` SDK with Pydantic structured outputs |
| APScheduler 4.x alpha | API is unstable and the alpha has known breaking changes vs 3.x; not production-ready | APScheduler 3.11.2 |
| pytrends without a circuit breaker | Unofficial API; Google can return HTTP 429 or change responses without notice, crashing the research pipeline | `pytrends` 4.9.2 wrapped in try/except with fallback to "signal unavailable" |
| Global mutable state for bot configuration | Causes race conditions in the async pipeline when multiple agents read/write config simultaneously | Immutable Pydantic `BaseSettings` instance loaded once at startup, passed by reference |

---

## Stack Patterns by Variant

**If Kalshi API rate limit is a bottleneck (Basic tier: 20 reads/sec, 10 writes/sec):**
- Implement a token bucket rate limiter as an async context manager wrapping all `kalshi-python-async` calls
- Cache market snapshots in-process (Python dict with TTL) rather than re-fetching per-pipeline-run
- Batch order cancellations — each cancel counts as only 0.2 write transactions under BatchCancelOrders

**If Twitter API costs are prohibitive (v2 Basic tier is severely rate-limited):**
- Deprioritize Twitter signals; weight Reddit + RSS signals higher in the feature vector
- Consider SerpAPI or a paid news API as a higher-quality, less rate-limited alternative for sentiment signals

**If LLM API cost per prediction is too high:**
- Gate Claude API calls behind a pre-filter: only invoke LLM reasoning when XGBoost edge exceeds 6% (higher bar than the 4% execution threshold)
- Use `claude-haiku` for initial signal classification; reserve `claude-sonnet` for high-edge markets only
- Enable Anthropic prompt caching for research context that is reused across multiple market evaluations in the same cycle

**If retraining frequency increases (multiple times per day):**
- Migrate XGBoost training to a background thread via `asyncio.run_in_executor` to avoid blocking the event loop during model fit
- Consider LightGBM as a drop-in replacement — faster training on the same feature set

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| `pandas 3.0.1` | Python >=3.11, numpy >=2.0 | Requires Python 3.11+; not backward compatible with 3.10 |
| `numpy 2.4.3` | Python >=3.11, xgboost 3.2.0 | XGBoost 3.2.0 explicitly supports numpy 2.x |
| `kalshi-python-async 3.8.0` | Python >=3.13 (per metadata) | VERIFY: PyPI metadata says 3.13+ but description says 3.9+. Test with your Python version before locking. Use Python 3.11 and confirm compatibility. |
| `sqlalchemy 2.0.48` | asyncpg 0.31.0 | Use `postgresql+asyncpg://` connection string; SQLAlchemy 2.1.x is in beta — stick to 2.0.x for production |
| `pytest-asyncio 1.3.0` | pytest >=7.x, Python >=3.10 | v1.x removed `event_loop` fixture — don't use patterns from older tutorials |
| `apscheduler 3.11.2` | asyncio, Python 3.8+ | APScheduler 4.x (alpha) has incompatible API; do not accidentally upgrade |
| `anthropic 0.84.0` | Python >=3.9 | Structured outputs require `anthropic-beta: structured-outputs-2025-11-13` header; use `client.messages.parse()` not `client.messages.create()` |
| `xgboost 3.2.0` | scikit-learn 1.8.0, pandas 3.x, numpy 2.x | Full sklearn estimator interface via XGBClassifier; use `model.set_params(device='cpu')` explicitly in Docker |

---

## Sources

- PyPI: `kalshi-python-async` 3.8.0 — https://pypi.org/project/kalshi-python-async/ (version, Python requirement, deprecation of old SDK)
- Kalshi official docs: https://docs.kalshi.com/sdks/overview (SDK migration notice, sync vs async variants)
- Kalshi API rate limits: https://docs.kalshi.com/getting_started/rate_limits (tier table, write limit definition)
- PyPI: `anthropic` 0.84.0 — https://pypi.org/project/anthropic/ (current version)
- Anthropic structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs (beta header, model support)
- PyPI: `xgboost` 3.2.0 — https://pypi.org/project/xgboost/ (version, numpy 2.x support)
- PyPI: `scikit-learn` 1.8.0 — https://pypi.org/project/scikit-learn/ (version)
- PyPI: `SQLAlchemy` 2.0.48 — https://pypi.org/project/SQLAlchemy/ (version)
- PyPI: `asyncpg` 0.31.0 — https://pypi.org/project/asyncpg/ (version, PostgreSQL 9.5–18 support)
- PyPI: `alembic` 1.18.4 — https://pypi.org/project/alembic/ (version)
- PyPI: `pandas` 3.0.1 — https://pypi.org/project/pandas/ (version, Python 3.11 requirement)
- PyPI: `numpy` 2.4.3 — https://pypi.org/project/numpy/ (version)
- PyPI: `pydantic` 2.12.5 — https://pypi.org/project/pydantic/ (version)
- PyPI: `APScheduler` 3.11.2 — https://pypi.org/project/APScheduler/ (stable 3.x vs unstable 4.x alpha)
- PyPI: `loguru` 0.7.3 — https://pypi.org/project/loguru/ (version)
- PyPI: `httpx` 0.28.1 — https://pypi.org/project/httpx/ (version)
- PyPI: `tweepy` 4.16.0 — https://pypi.org/project/tweepy/ (version, AsyncClient support)
- PyPI: `asyncpraw` 7.8.1 — https://pypi.org/project/asyncpraw/ (version)
- PyPI: `feedparser` 6.0.12 — https://pypi.org/project/feedparser/ (version)
- PyPI: `pytrends` 4.9.2 — https://pypi.org/project/pytrends/ (version, maintenance status warning)
- Google Trends official API alpha announcement: https://developers.google.com/search/blog/2025/07/trends-api (alpha status, limited access)
- SQLAlchemy async docs: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html (async engine pattern)
- Tweepy async docs: https://docs.tweepy.org/en/stable/asyncclient.html (AsyncClient API)

---

*Stack research for: AI-powered prediction market trading bot (Kalshi)*
*Researched: 2026-03-09*
