# Phase 1: Infrastructure Foundation - Research

**Researched:** 2026-03-09
**Domain:** Kalshi API (REST + WebSocket), PostgreSQL async stack, configuration management, structured logging, metrics
**Confidence:** HIGH (core stack), MEDIUM (Kalshi-specific internals)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Token & Auth Strategy**
- Tokens live in-memory only — re-authenticate on restart, no persistence risk
- Proactive token refresh (background task before expiry) with reactive 401 fallback as safety net
- WebSocket reconnection uses fixed 5-second interval retry
- Pin Python 3.13 if kalshi-python-async requires it — all deps (pandas, xgboost, sklearn) support 3.13

**DB Schema Structure**
- Pragmatic hybrid schema: normalized for core entities (orders, positions, markets) + denormalized wide tables for analytics queries
- All timestamps UTC everywhere — convert to local only for display
- No partitioning for now — standard indexes on timestamp columns, sufficient until millions of rows
- Soft delete with status flags for cancelled orders and expired markets — full audit trail, nothing is hard-deleted

**Config & Secrets Management**
- Pydantic Settings class that reads from .env + YAML — typed config, validated at startup, fail fast with clear errors
- Secrets in .env file for local dev, cloud secrets manager (AWS SSM / GCP Secret Manager) for production
- Paper/live mode toggle: env var TRADING_MODE=paper|live as default, CLI flag --paper overrides — flexible for both Docker and local dev
- Config validated at startup only — no hot-reload, restart to apply changes

**Logging & Observability**
- Configurable log levels: DEBUG = full decision trace (every pipeline stage), INFO = decisions only, WARNING = rejections and errors
- Output to stdout (JSON for Docker/cloud ingestion) + rotating log files for local dev debugging
- Correlation IDs: each scan cycle gets a cycle_id, each trade candidate gets a trade_id — full end-to-end tracing across pipeline stages
- Prometheus-style /metrics endpoint from day one: cycle count, latency, error rate, open positions — Grafana-ready

### Claude's Discretion
- Exact Alembic migration structure and naming convention
- PostgreSQL connection pool sizing
- Loguru sink configuration details
- Prometheus client library choice (prometheus_client vs aioprometheus)
- WebSocket heartbeat/ping interval

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INFR-01 | System connects to Kalshi REST API with token-based authentication and automatic token refresh | RSA-PSS key signing confirmed; tokens expire every 30 min; asyncio.create_task pattern for background refresh |
| INFR-02 | System connects to Kalshi WebSocket API for real-time orderbook and fill event feeds | WSS endpoint confirmed; auth via header signing at handshake; websockets library handles ping/pong |
| INFR-03 | PostgreSQL database stores all trade history, signals, model outputs, and performance metrics | SQLAlchemy 2.0 async + asyncpg driver; async_sessionmaker factory pattern |
| INFR-04 | Database schema supports migrations via Alembic | alembic init --template async; env.py async run_migrations_online; naming conventions via MetaData |
| INFR-05 | Configuration is managed via environment variables and YAML config files | pydantic-settings v2 with YamlConfigSettingsSource; validated at startup; fail-fast |
| INFR-06 | System implements exponential backoff on API rate limit (429) and server error (5xx) responses | tenacity library with wait_exponential_jitter; works on async functions natively |
| INFR-07 | System reconciles positions on restart to prevent orphaned orders | REST call to /portfolio/positions + /portfolio/orders on startup; compare with DB state |
| INFR-08 | System runs in paper trading mode that simulates execution without placing real orders | TRADING_MODE env var; paper handler no-op; injectable via dependency injection pattern |
</phase_requirements>

---

## Summary

This phase builds the foundation that every downstream phase imports from. The key technical domains are: Kalshi REST API authentication (RSA-PSS key signing, no session tokens, 30-minute token-equivalent expiry), Kalshi WebSocket (WSS connection with signed headers, JSON subscription protocol, orderbook_delta and fill channels), async PostgreSQL (SQLAlchemy 2.0 + asyncpg + Alembic async template), configuration management (pydantic-settings v2 with YAML + env var sources), structured logging (loguru with JSON serialization), and Prometheus metrics.

The confirmed package is `kalshi-python-async` 3.8.0 (released Feb 2026), which requires Python >=3.13. This confirms the decision to pin Python 3.13. The Kalshi auth model is not session-token based — it is stateless RSA-PSS request signing with API key ID + private key per request. There is no "token to refresh." The CONTEXT.md decision to do "proactive token refresh" should be interpreted as re-signing each request with fresh timestamps (the 30-minute validity refers to how long a signed timestamp is accepted, not an OAuth-style bearer token).

The stack is mature and well-documented. Key risk: the official kalshi-python-async SDK does not clearly expose WebSocket in its REST-focused PyPI page; the WebSocket connection is documented separately and requires direct use of the `websockets` library with manual header signing, not a method on the SDK client.

**Primary recommendation:** Use `kalshi-python-async` for REST calls, implement WebSocket directly with `websockets` + manual RSA-PSS signing, use SQLAlchemy 2.0 async + asyncpg + Alembic async template, pydantic-settings v2 for config, loguru for logging, and prometheus_client for metrics.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| kalshi-python-async | 3.8.0 | Official Kalshi REST API client | Official Kalshi SDK, async-native, Python 3.13+ |
| websockets | 14.x | WebSocket client for Kalshi real-time feed | Handles ping/pong automatically; async for; recommended in Kalshi docs |
| SQLAlchemy | 2.0+ | Async ORM + query layer | Industry standard, async session, typed queries |
| asyncpg | 0.29+ | PostgreSQL async driver | Fastest Python PostgreSQL driver; required by SQLAlchemy async |
| alembic | 1.13+ | Database schema migrations | SQLAlchemy-native, async template available |
| pydantic-settings | 2.x | Config from .env + YAML | Type-safe, validated at startup, YamlConfigSettingsSource built-in |
| loguru | 0.7+ | Structured logging with JSON | serialize=True gives JSON to stdout; rotating file sink built-in |
| tenacity | 8.x+ | Retry with exponential backoff | Async-native, wait_exponential_jitter, works as decorator |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| prometheus_client | 0.20+ | Metrics endpoint | Simpler than aioprometheus; sufficient for /metrics HTTP endpoint |
| python-dotenv | 1.0+ | Load .env file | pydantic-settings uses it internally; explicit loading optional |
| cryptography | 42+ | RSA-PSS signing for Kalshi API | Signing requests/WebSocket handshake headers |
| PyYAML | 6.x | YAML parsing | Used by pydantic-settings YamlConfigSettingsSource |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| prometheus_client | aioprometheus | aioprometheus is fully async-native but adds complexity; prometheus_client with a simple aiohttp/threading handler is simpler for a single /metrics endpoint |
| websockets | aiohttp WebSocket | websockets is purpose-built; aiohttp adds HTTP server weight not needed here |
| loguru | structlog | structlog is more composable but more boilerplate; loguru's serialize=True achieves the same JSON output with less setup |

**Installation:**
```bash
uv add kalshi-python-async websockets sqlalchemy asyncpg alembic "pydantic-settings[yaml]" loguru tenacity prometheus_client cryptography pyyaml
uv add --dev pytest pytest-asyncio pytest-mock
```

---

## Architecture Patterns

### Recommended Project Structure
```
src/
├── pmtb/
│   ├── __init__.py
│   ├── main.py              # Entry point, startup/shutdown lifecycle
│   ├── config.py            # Settings class (pydantic-settings)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py        # create_async_engine, async_sessionmaker
│   │   ├── models.py        # SQLAlchemy ORM models (Base, all tables)
│   │   └── session.py       # get_session dependency / context manager
│   ├── kalshi/
│   │   ├── __init__.py
│   │   ├── client.py        # KalshiClient wrapping kalshi-python-async
│   │   ├── auth.py          # RSA-PSS signing helpers, header generation
│   │   ├── ws_client.py     # WebSocket client (websockets library)
│   │   └── errors.py        # Kalshi-specific error categories + retry logic
│   ├── logging_.py          # Logger configuration (loguru sinks)
│   ├── metrics.py           # Prometheus metrics registry, /metrics endpoint
│   └── paper.py             # PaperTradingHandler (no-op order router)
migrations/
├── env.py                   # Alembic async env.py
├── script.py.mako
└── versions/
pyproject.toml
.env                         # Local secrets (gitignored)
config.yaml                  # Non-secret config (edge_threshold, kelly_alpha, etc.)
```

### Pattern 1: RSA-PSS Request Signing

**What:** Every Kalshi REST request and WebSocket handshake requires three custom headers generated from API key ID + private key.
**When to use:** All KalshiClient method calls and WebSocket connect.

```python
# Source: https://docs.kalshi.com/getting_started/quick_start_websockets
import base64
import time
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

def build_kalshi_headers(method: str, path: str, private_key, api_key_id: str) -> dict:
    timestamp_ms = str(int(time.time() * 1000))
    # Strip query parameters from path
    clean_path = path.split("?")[0]
    message = (timestamp_ms + method.upper() + clean_path).encode()
    signature = private_key.sign(message, padding.PSS(
        mgf=padding.MGF1(hashes.SHA256()),
        salt_length=padding.PSS.DIGEST_LENGTH,
    ), hashes.SHA256())
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }
```

### Pattern 2: Proactive Token Expiry Guard

**What:** Kalshi's signed timestamps are valid for ~30 minutes. Because signing is stateless (per-request), there is no OAuth token to refresh. "Proactive refresh" means the KalshiClient re-generates headers on every call (no caching of signed headers). An asyncio.create_task background loop can re-validate credentials or re-fetch a session token if Kalshi introduces session-based auth in a future API version.
**When to use:** KalshiClient wraps the SDK; headers are always freshly signed per request.

```python
# Pattern: fresh headers per request (no header caching)
class KalshiClient:
    def __init__(self, settings: Settings):
        self._api_key_id = settings.kalshi_api_key_id
        self._private_key = load_private_key(settings.kalshi_private_key_path)

    def _headers(self, method: str, path: str) -> dict:
        return build_kalshi_headers(method, path, self._private_key, self._api_key_id)

    async def get_balance(self) -> dict:
        # SDK call — headers injected per-request
        ...
```

### Pattern 3: WebSocket with Auto-Reconnect

**What:** Use `async for websocket in websockets.connect(...)` for automatic reconnection. Manual fixed 5-second backoff per user decision (override library default exponential).
**When to use:** ws_client.py — the single WebSocket connection to Kalshi.

```python
# Source: https://docs.kalshi.com/getting_started/quick_start_websockets
import asyncio
import websockets
import json

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

async def run_ws_client(client: KalshiClient, on_message):
    while True:
        try:
            headers = client._headers("GET", "/trade-api/ws/v2")
            async with websockets.connect(WS_URL, additional_headers=headers) as ws:
                await ws.send(json.dumps({
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {"channels": ["orderbook_delta", "fill"], "market_ticker": "..."}
                }))
                async for message in ws:
                    await on_message(json.loads(message))
        except (websockets.ConnectionClosed, OSError):
            await asyncio.sleep(5)  # Fixed 5s per user decision
```

### Pattern 4: Alembic Async Migration Setup

**What:** Use `alembic init --template async` to generate an async-compatible env.py. Set naming_convention on MetaData so constraint names are deterministic.
**When to use:** Initial Alembic setup in Wave 0/first task.

```python
# Source: https://alembic.sqlalchemy.org/en/latest/naming.html
from sqlalchemy import MetaData

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Base = DeclarativeBase()
Base.metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

### Pattern 5: Pydantic Settings with YAML + .env

**What:** Single Settings class with layered sources: defaults → YAML file → .env file → environment variables. Startup validation raises immediately on missing required values.
**When to use:** config.py — imported by all modules via dependency injection.

```python
# Source: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
from pydantic import Field
from pydantic_settings import BaseSettings, YamlConfigSettingsSource, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        yaml_file="config.yaml",
    )
    trading_mode: str = Field("paper", pattern="^(paper|live)$")
    database_url: str
    kalshi_api_key_id: str
    kalshi_private_key_path: str
    edge_threshold: float = 0.04
    kelly_alpha: float = 0.25
    max_drawdown: float = 0.08

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs):
        return (
            kwargs["env_settings"],
            YamlConfigSettingsSource(settings_cls),
            kwargs["dotenv_settings"],
            kwargs["init_settings"],
        )
```

### Pattern 6: Async Session Factory

**What:** Single async engine and session factory created at startup; injected as a context manager.
**When to use:** db/engine.py — all DB access goes through this factory.

```python
# Source: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

engine = create_async_engine(
    settings.database_url,  # postgresql+asyncpg://...
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=300,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

### Pattern 7: Paper Trading Mode

**What:** At startup, read `TRADING_MODE`. Inject either `KalshiOrderExecutor` (live) or `PaperOrderExecutor` (no-op) via a protocol/interface.
**When to use:** main.py wires up the executor; downstream phases only see the interface.

```python
class OrderExecutorProtocol(Protocol):
    async def place_order(self, market: str, side: str, quantity: int, price: float) -> dict: ...
    async def cancel_order(self, order_id: str) -> dict: ...

class PaperOrderExecutor:
    async def place_order(self, market, side, quantity, price):
        return {"order_id": f"paper-{uuid4()}", "status": "simulated"}

    async def cancel_order(self, order_id):
        return {"status": "cancelled"}
```

### Anti-Patterns to Avoid

- **Caching signed headers:** RSA-PSS timestamps expire. Always generate fresh headers per request — never cache a signed header tuple.
- **Using SQLite for development:** asyncpg behavior differs from aiosqlite in ways that mask migration and constraint issues. Use PostgreSQL in dev too (Docker Compose).
- **Calling `alembic upgrade head` without reviewing autogenerated migrations:** Alembic autogenerate misses some changes (indexes on expressions, partial indexes, custom types). Always review before applying.
- **Single global logger without context binding:** Bind `cycle_id` and `trade_id` to a contextualized logger copy using `logger.bind(cycle_id=...)` — don't pass context as extra kwargs on every call.
- **Blocking I/O in async handlers:** Never use `requests` library or `time.sleep` in async code. Use `httpx.AsyncClient` or `asyncio.sleep`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry with backoff | Custom sleep-and-retry loops | tenacity | Handles jitter, max attempts, exception filtering, async natively |
| DB migrations | Custom SQL migration scripts | Alembic | Autogenerate, rollback, dependency graph, async template |
| Config validation | os.environ.get() + manual checks | pydantic-settings | Type coercion, secret masking, validation errors with field names |
| JSON structured logging | Custom logging.Formatter | loguru serialize=True | Record includes all metadata, no format string edge cases |
| WebSocket ping/pong | Manual ping task | websockets built-in | websockets sends ping frames automatically, tracks pong timeouts |
| RSA signing | Pure Python math | cryptography library | Side-channel resistant, PSS salt handling, FIPS-validated |

**Key insight:** Every hand-rolled solution in this list has at least one hard-to-reproduce edge case (race condition in retry, migration ordering, config override precedence, log record thread-safety, WebSocket backpressure, RSA signature malleability). Use the battle-tested libraries.

---

## Common Pitfalls

### Pitfall 1: kalshi-python-async WebSocket Gap

**What goes wrong:** Developers assume the official SDK has a `.connect_ws()` method and spend time searching for it. The SDK focuses on REST; WebSocket requires a separate implementation.
**Why it happens:** The PyPI README and quick start only show REST examples.
**How to avoid:** Implement `ws_client.py` independently using the `websockets` library with manual RSA-PSS header generation following the Kalshi WebSocket quickstart docs.
**Warning signs:** Any import like `from kalshi_python_async import WebSocketClient` will fail.

### Pitfall 2: Python 3.13 Requirement

**What goes wrong:** Developer creates virtualenv with Python 3.11 or 3.12, `uv add kalshi-python-async` fails or installs an old version.
**Why it happens:** Package metadata on PyPI declares `Requires-Python: >=3.13`.
**How to avoid:** Set `.python-version` to `3.13` at project root before `uv sync`. Verify with `python --version` inside the venv.
**Warning signs:** `pip install kalshi-python-async` succeeds but imports fail; resolver picks an older version.

### Pitfall 3: Alembic Async env.py Without `run_sync`

**What goes wrong:** Alembic migrations hang or fail silently when env.py uses async engine without the `run_sync` wrapper.
**Why it happens:** Alembic's migration runner is synchronous; the async engine needs `conn.run_sync(do_run_migrations)` to bridge.
**How to avoid:** Use `alembic init --template async` which generates the correct bridging code. Do not manually convert a sync env.py.
**Warning signs:** `alembic upgrade head` hangs indefinitely without error.

### Pitfall 4: Timestamp Timezone Confusion

**What goes wrong:** Some rows stored with timezone-aware datetimes, some naive, causing comparison failures and incorrect ordering.
**Why it happens:** Python's `datetime.datetime.now()` returns naive datetimes; asyncpg stores TIMESTAMPTZ and returns aware datetimes.
**How to avoid:** Use `datetime.datetime.now(datetime.UTC)` everywhere. Define all SQLAlchemy `Column(DateTime(timezone=True))`. Never use `datetime.utcnow()` (deprecated in Python 3.12+).
**Warning signs:** `TypeError: can't compare offset-naive and offset-aware datetimes` in query filters.

### Pitfall 5: Connection Pool Exhaustion

**What goes wrong:** All DB connections are checked out, new requests timeout with `TimeoutError: QueuePool limit of size X overflow Y reached`.
**Why it happens:** Sessions not closed properly (missing `async with`, generator not exhausted), or pool too small for concurrent tasks.
**How to avoid:** Always use `async with AsyncSessionLocal() as session:` — never manually call `session.close()`. Set `pool_pre_ping=True` to discard dead connections. Start with `pool_size=5, max_overflow=10` (Phase 1 load is light).
**Warning signs:** Intermittent DB timeouts that correlate with request concurrency spikes.

### Pitfall 6: WebSocket Silent Drop

**What goes wrong:** WebSocket connection drops silently (no exception raised), and the consumer loop exits without reconnecting.
**Why it happens:** Some network conditions close the TCP connection without a WebSocket close frame; `websockets` raises `ConnectionClosedOK` or `ConnectionClosedError` inside the `async for` loop.
**How to avoid:** Wrap the entire `async with websockets.connect(...) as ws: async for msg in ws:` block in a `while True:` loop with `except (ConnectionClosed, OSError)`. Log every reconnect event.
**Warning signs:** WS message handler goes silent; no exceptions logged; application appears healthy.

### Pitfall 7: prometheus_client Default Metrics Conflict

**What goes wrong:** Default process metrics from `prometheus_client` include POSIX-only metrics that fail on macOS dev machines.
**Why it happens:** `prometheus_client` auto-registers process collectors that read `/proc/`.
**How to avoid:** On non-Linux platforms, disable default collectors: `import prometheus_client; prometheus_client.REGISTRY.unregister(prometheus_client.PROCESS_COLLECTOR)` or use `disable_created_metrics()`.
**Warning signs:** `OSError: [Errno 2] No such file or directory: '/proc/self/stat'` on macOS.

---

## Code Examples

### Loguru Configuration (JSON stdout + rotating file)
```python
# Source: https://loguru.readthedocs.io/en/stable/api/logger.html
import sys
from loguru import logger

def configure_logging(settings):
    logger.remove()  # Remove default stderr sink
    # JSON to stdout for Docker/cloud
    logger.add(sys.stdout, serialize=True, level=settings.log_level)
    # Human-readable rotating file for local dev
    logger.add(
        "logs/pmtb_{time:YYYY-MM-DD}.log",
        rotation="100 MB",
        retention="7 days",
        level="DEBUG",
        format="{time} | {level} | {name}:{function}:{line} | {message}",
    )

# Correlation ID binding
cycle_logger = logger.bind(cycle_id="cycle-abc123")
cycle_logger.info("Scan started", markets_checked=42)
```

### Tenacity Retry for Kalshi REST
```python
# Source: https://tenacity.readthedocs.io/
from tenacity import retry, wait_exponential_jitter, stop_after_attempt, retry_if_exception

def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, (KalshiRateLimitError, KalshiServerError))

@retry(
    wait=wait_exponential_jitter(initial=1, max=30, jitter=3),
    stop=stop_after_attempt(5),
    retry=retry_if_exception(is_retryable),
    reraise=True,
)
async def get_markets(client: KalshiClient) -> list:
    return await client.markets.get_markets()
```

### Alembic async env.py Pattern
```python
# Source: https://github.com/sqlalchemy/alembic/blob/main/alembic/templates/async/env.py
import asyncio
from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

def run_migrations_online():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async def do_run_migrations(connection):
        context.configure(connection=connection, target_metadata=target_metadata)
        async with context.begin_transaction():
            await context.run_migrations()

    async def run_async_migrations():
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    asyncio.run(run_async_migrations())
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| kalshi-python (v1) | kalshi-python-async 3.8.0 | Deprecated ~2024 | Must use new package; Python 3.13 required |
| SQLAlchemy 1.x session | SQLAlchemy 2.0 AsyncSession | 2022 (stable 2023) | `async with session:` pattern; `select()` not `Query` |
| alembic sync env.py | alembic async template | Alembic 1.11+ | `--template async` flag; `run_sync` bridge required |
| pydantic v1 BaseSettings | pydantic-settings v2 (separate package) | 2023 | `pip install pydantic-settings` separate from pydantic |
| event_loop fixture (pytest-asyncio) | asyncio_mode="auto", no event_loop fixture | pytest-asyncio 1.0 (May 2025) | Use `@pytest_asyncio.fixture` for async fixtures; configure `asyncio_mode = "auto"` in pyproject.toml |
| `datetime.utcnow()` | `datetime.now(datetime.UTC)` | Python 3.12 | `utcnow()` deprecated; naive datetimes cause timezone bugs |

**Deprecated/outdated:**
- `kalshi-python` (old SDK): replaced by `kalshi-python-async` + `kalshi-python-sync`
- `pydantic.BaseSettings`: moved to separate `pydantic-settings` package in v2
- `pytest-asyncio` `event_loop` fixture: removed in 1.0.0 (May 2025)
- `datetime.utcnow()`: deprecated Python 3.12, removed Python 3.14

---

## Open Questions

1. **Does kalshi-python-async expose all REST endpoints needed for position reconciliation (INFR-07)?**
   - What we know: SDK wraps /portfolio/positions, /portfolio/orders endpoints per PyPI description
   - What's unclear: Whether all order status fields are typed in the SDK response models
   - Recommendation: Test in Wave 0 spike; fall back to raw httpx calls if SDK response models are incomplete

2. **What is the exact Kalshi WebSocket message format for `orderbook_snapshot` vs `orderbook_delta`?**
   - What we know: Subscribe with `{"cmd": "subscribe", "params": {"channels": ["orderbook_delta"]}}`. Response types confirmed: `subscribed`, `orderbook_snapshot`, `orderbook_delta`, `error`.
   - What's unclear: Exact JSON field names and types in delta messages (bid/ask structure, sequence numbers)
   - Recommendation: Connect to demo environment in Wave 0, print raw messages, document schema before building models

3. **Kalshi demo vs production WebSocket URL for paper trading mode?**
   - What we know: Production `wss://api.elections.kalshi.com/trade-api/ws/v2`, Demo `wss://demo-api.kalshi.co/trade-api/ws/v2`
   - What's unclear: Whether demo API requires separate API key credentials
   - Recommendation: Paper trading mode should route WS to demo URL; live mode to production. Both configured in Settings.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 1.x |
| Config file | pyproject.toml `[tool.pytest.ini_options]` — see Wave 0 |
| Quick run command | `uv run pytest tests/ -x -q` |
| Full suite command | `uv run pytest tests/ -v --tb=short` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INFR-01 | REST auth headers pass signature validation | unit | `uv run pytest tests/kalshi/test_auth.py -x` | Wave 0 |
| INFR-01 | KalshiClient makes authenticated REST call | integration (demo) | `uv run pytest tests/kalshi/test_client_integration.py -x -m demo` | Wave 0 |
| INFR-02 | WebSocket connects and receives subscribed message | integration (demo) | `uv run pytest tests/kalshi/test_ws_client.py -x -m demo` | Wave 0 |
| INFR-02 | WebSocket reconnects after forced disconnect | unit (mock) | `uv run pytest tests/kalshi/test_ws_reconnect.py -x` | Wave 0 |
| INFR-03 | DB session factory connects to test PostgreSQL | integration | `uv run pytest tests/db/test_session.py -x` | Wave 0 |
| INFR-04 | Alembic migrations run from scratch and produce all tables | integration | `uv run pytest tests/db/test_migrations.py -x` | Wave 0 |
| INFR-05 | Settings loads from YAML + .env, fails on missing required field | unit | `uv run pytest tests/test_config.py -x` | Wave 0 |
| INFR-05 | TRADING_MODE env var overrides config.yaml value | unit | `uv run pytest tests/test_config.py::test_env_override -x` | Wave 0 |
| INFR-06 | Retry decorator fires on 429, backs off, succeeds on retry | unit (mock) | `uv run pytest tests/kalshi/test_retry.py -x` | Wave 0 |
| INFR-07 | Position reconciler detects orphaned order on restart | unit (mock DB + mock API) | `uv run pytest tests/test_reconciler.py -x` | Wave 0 |
| INFR-08 | Paper mode routes order call to no-op handler | unit | `uv run pytest tests/test_paper.py -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/ -x -q --ignore=tests/kalshi/test_client_integration.py --ignore=tests/kalshi/test_ws_client.py`
- **Per wave merge:** `uv run pytest tests/ -v --tb=short`
- **Phase gate:** Full suite green (including demo integration tests) before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/__init__.py` — package marker
- [ ] `tests/conftest.py` — shared fixtures: test Settings, test DB engine (points to test schema), mock KalshiClient
- [ ] `tests/kalshi/test_auth.py` — covers INFR-01 unit (RSA-PSS signature format)
- [ ] `tests/kalshi/test_client_integration.py` — covers INFR-01 integration (requires `KALSHI_DEMO_API_KEY_ID` env var; marked `@pytest.mark.demo`)
- [ ] `tests/kalshi/test_ws_client.py` — covers INFR-02 (demo env; marked `@pytest.mark.demo`)
- [ ] `tests/kalshi/test_ws_reconnect.py` — covers INFR-02 reconnect (mock websockets)
- [ ] `tests/kalshi/test_retry.py` — covers INFR-06
- [ ] `tests/db/test_session.py` — covers INFR-03
- [ ] `tests/db/test_migrations.py` — covers INFR-04
- [ ] `tests/test_config.py` — covers INFR-05
- [ ] `tests/test_reconciler.py` — covers INFR-07
- [ ] `tests/test_paper.py` — covers INFR-08
- [ ] Framework install: `uv add --dev pytest pytest-asyncio pytest-mock` — none detected yet
- [ ] `pyproject.toml` pytest config:
  ```toml
  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  markers = ["demo: requires demo API credentials"]
  ```

---

## Sources

### Primary (HIGH confidence)
- [Kalshi WebSocket Quickstart](https://docs.kalshi.com/getting_started/quick_start_websockets) — auth headers, subscription format, channel types, message types
- [Kalshi Rate Limits](https://docs.kalshi.com/getting_started/rate_limits) — tier table, write limit scope, Basic/Advanced/Premier/Prime
- [kalshi-python-async PyPI](https://pypi.org/project/kalshi-python-async/) — version 3.8.0, Python >=3.13 confirmed
- [SQLAlchemy 2.0 Async Docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) — AsyncSession, async_sessionmaker
- [Alembic Cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html) — naming conventions, async patterns
- [pydantic-settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — YamlConfigSettingsSource, env_nested_delimiter
- [loguru docs](https://loguru.readthedocs.io/en/stable/api/logger.html) — serialize=True, add(), bind()
- [tenacity docs](https://tenacity.readthedocs.io/) — wait_exponential_jitter, async support
- [pytest-asyncio 1.x docs](https://pytest-asyncio.readthedocs.io/en/stable/) — event_loop removal, asyncio_mode=auto

### Secondary (MEDIUM confidence)
- [Kalshi llms.txt](https://docs.kalshi.com/llms.txt) — WebSocket endpoint URLs, auth requirement, rate limit references (not all values confirmed in detail)
- [websockets 14.x docs](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html) — auto ping/pong, ConnectionClosed exceptions
- Multiple SQLAlchemy async + Alembic guides (berkkaraal.com, dev.to/matib, kokopi.dev) — corroborating async env.py pattern

### Tertiary (LOW confidence)
- WebSearch results describing "tokens expire every 30 minutes" — not found in official Kalshi docs; may refer to signed timestamp validity window. Treat as guidance, verify in implementation.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — verified on PyPI and official docs (versions confirmed Feb 2026)
- Architecture: HIGH — SQLAlchemy/Alembic/pydantic-settings patterns from official docs
- Kalshi auth (REST): HIGH — RSA-PSS signing confirmed in Kalshi quickstart
- Kalshi WebSocket: MEDIUM — URL and subscription format confirmed; field-level message schema not fully documented
- Pitfalls: MEDIUM-HIGH — most from official docs/changelogs; WebSocket silent drop from ecosystem patterns

**Research date:** 2026-03-09
**Valid until:** 2026-04-09 (Kalshi API is actively developed; re-verify SDK version before starting)
