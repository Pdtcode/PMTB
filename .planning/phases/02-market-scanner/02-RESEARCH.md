# Phase 2: Market Scanner - Research

**Researched:** 2026-03-10
**Domain:** Kalshi REST API pagination, Pydantic models, async scanner loop, rolling statistics
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **Scan Scheduling**: Fixed interval async loop with configurable sleep — default 5 minutes. Runs 24/7. All discovered markets persisted to DB (upsert on ticker) — not just candidates. Per-market rejection reason logged at DEBUG level for threshold tuning.
- **Filter Design**: Hard gate filters applied sequentially — market must pass ALL filters. No scoring or soft ranking at filter stage. Filters: liquidity, volume, spread, time-to-resolution, volatility. Time-to-resolution window: exclude markets resolving within 1 hour or beyond 30 days.
- **Volatility Measurement**: Price movement over time — track yes_price per market each scan cycle, compute standard deviation from rolling history. Rolling history built from accumulated scan snapshots (no extra API call for trade history). Volatility filter skipped during warmup period (until ~6+ snapshots accumulated).
- **Market Data Enrichment**: After filtering, fetch orderbook snapshot for each passing candidate (bid/ask, depth). Fetch event-level context for each candidate. Enrichment only for candidates that pass all filters.
- **Candidate Output Shape**: MarketCandidate is a Pydantic model: ticker, title, category, event_context, close_time, yes_bid, yes_ask, implied_probability (mid-price), spread, volume_24h, volatility_score. ScanResult wrapper: list of MarketCandidates + metadata (total_markets, per-filter rejection counts, scan_duration, cycle_id). Sorted by distance from 50% implied probability.
- **Code patterns**: httpx.AsyncClient, @kalshi_retry decorator, Loguru structured logging with .bind(), Prometheus API_CALLS counter.

### Claude's Discretion
- Exact default threshold values for liquidity, volume, spread
- Number of orderbook depth levels to fetch
- Volatility warmup threshold (suggested ~6 snapshots but flexible)
- Price snapshot storage mechanism (in-memory rolling window vs. DB table)
- Exact sorting heuristic for edge potential ranking

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SCAN-01 | Scanner retrieves all active Kalshi markets across all categories | Cursor-based pagination pattern confirmed; `status="active"` filter + `limit=1000` per page |
| SCAN-02 | Scanner filters markets by minimum liquidity threshold | `open_interest_fp` field available on all market objects; `liquidity_dollars` is deprecated and always 0 — use open_interest_fp |
| SCAN-03 | Scanner filters markets by minimum 24h volume threshold | `volume_24h_fp` field confirmed on all market objects |
| SCAN-04 | Scanner filters markets by time-to-resolution window | `close_time` field confirmed; compute timedelta from now; apply 1h–30d window |
| SCAN-05 | Scanner filters markets by maximum bid-ask spread | `yes_bid_dollars` and `yes_ask_dollars` confirmed; spread = ask - bid; computed inline |
| SCAN-06 | Scanner filters markets by volatility criteria | Rolling yes_bid_dollars snapshots in-memory; std dev computed from collections.deque; warmup skip confirmed |
| SCAN-07 | Scanner outputs typed candidate market objects for downstream pipeline | MarketCandidate Pydantic model; ScanResult wrapper; imported by Phase 3 and Phase 4 |
</phase_requirements>

---

## Summary

The market scanner is an async polling loop that: (1) fetches all active Kalshi markets across pages using cursor-based pagination, (2) upserts every market to the DB, (3) applies sequential hard-gate filters, (4) enriches passing candidates with orderbook snapshots and event context, and (5) returns a typed ScanResult for downstream consumption. Phase 1 built all the required infrastructure — KalshiClient, DB session, Settings, Loguru logging, and the @kalshi_retry decorator — so Phase 2 is almost entirely new business logic with no new infrastructure dependencies.

The Kalshi REST API v2 uses cursor-based pagination. The `liquidity_dollars` field is documented as deprecated and always returns "0.0" — do not use it. Use `open_interest_fp` (total contracts purchased) as the proxy for liquidity. All price fields use fixed-point decimal strings representing dollar values (e.g., "0.62" = 62 cents = 62% implied probability). The orderbook endpoint returns only bids (yes bids + no bids), not asks — because yes_bid at price X implies no_ask at (1 - X), so Kalshi avoids the duplication.

The rolling volatility implementation does not require a DB table. A plain `collections.deque(maxlen=N)` keyed by ticker in a module-level or class-level dict is sufficient, persists across scan cycles within a process lifetime, and requires zero schema changes. The warmup skip (fewer than 6 snapshots) is straightforward to implement as a length check before computing std dev.

**Primary recommendation:** Build the scanner as `src/pmtb/scanner/scanner.py` with MarketCandidate and ScanResult Pydantic models in `src/pmtb/scanner/models.py`. The filter chain lives in `src/pmtb/scanner/filters.py`. Settings fields are added directly to the existing `Settings` class. All tests mock KalshiClient and DB session — no live API calls needed.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic v2 | Already in project | MarketCandidate and ScanResult model validation | Project-wide type contract pattern |
| httpx + AsyncClient | Already in project | KalshiClient already built on this | Established in Phase 1 |
| SQLAlchemy asyncpg | Already in project | Upsert via `merge()` or INSERT ON CONFLICT | Established in Phase 1 |
| loguru | Already in project | Structured logging with `.bind(cycle_id=...)` | Established in Phase 1 |
| tenacity (@kalshi_retry) | Already in project | API call retry decorator | Established in Phase 1 |
| collections.deque | stdlib | Rolling price snapshot window per ticker | Zero-dependency rolling buffer |
| statistics.stdev | stdlib | Standard deviation for volatility score | Sufficient for rolling window std dev |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio.sleep | stdlib | Scan interval loop pause | Between each scan cycle |
| datetime / timezone | stdlib | Time-to-resolution computation | Comparing close_time to now() |
| uuid | stdlib | cycle_id generation per scan | Correlation ID for end-to-end tracing |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| collections.deque (in-memory) | DB price_snapshots table | DB table survives restarts but adds schema + migration; in-memory is simpler and volatility warmup is acceptable after restart |
| statistics.stdev | numpy std | numpy is a heavy dependency not yet in the project; stdlib is sufficient for deques of 6–50 items |
| open_interest_fp as liquidity proxy | order depth sum from orderbook | Orderbook fetch adds per-market API calls during scan; open_interest_fp is free in the markets list response |

**Installation:** No new packages required. All dependencies are already present.

---

## Architecture Patterns

### Recommended Project Structure
```
src/pmtb/
├── scanner/
│   ├── __init__.py          # exports MarketCandidate, ScanResult, MarketScanner
│   ├── models.py            # MarketCandidate, ScanResult Pydantic models
│   ├── filters.py           # FilterChain, individual filter functions
│   └── scanner.py           # MarketScanner class with run_cycle() and run_forever()
tests/
└── scanner/
    ├── __init__.py
    ├── test_models.py        # MarketCandidate and ScanResult validation
    ├── test_filters.py       # Each filter function in isolation
    └── test_scanner.py       # MarketScanner integration: pagination, upsert, enrichment
```

### Pattern 1: Cursor-Based Full Pagination
**What:** Loop over all market pages until cursor is None/empty
**When to use:** Every scan cycle to guarantee no markets are missed (SCAN-01)
**Example:**
```python
# Source: https://docs.kalshi.com/getting_started/pagination
async def _fetch_all_markets(self) -> list[dict]:
    markets: list[dict] = []
    cursor: str | None = None
    while True:
        params = {"status": "active", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data = await self._client._request("GET", "/trade-api/v2/markets", params=params)
        markets.extend(data.get("markets", []))
        cursor = data.get("cursor") or None
        if not cursor:
            break
    return markets
```

### Pattern 2: Sequential Hard-Gate Filter Chain
**What:** Each filter receives a list, returns a filtered list + rejection count
**When to use:** Applying all five filters (SCAN-02 through SCAN-06)
**Example:**
```python
# Each filter is a pure function for testability
def filter_volume(markets: list[dict], min_volume: float) -> tuple[list[dict], int]:
    passing = [m for m in markets if float(m.get("volume_24h_fp", 0)) >= min_volume]
    rejected = len(markets) - len(passing)
    return passing, rejected
```

### Pattern 3: DB Upsert via SQLAlchemy merge/ON CONFLICT
**What:** Upsert all markets to the `markets` table on ticker uniqueness
**When to use:** After pagination, before filtering (upsert ALL, not just candidates)
**Example:**
```python
# Uses SQLAlchemy INSERT ... ON CONFLICT DO UPDATE (PostgreSQL dialect)
from sqlalchemy.dialects.postgresql import insert as pg_insert

stmt = pg_insert(Market).values(
    ticker=mkt["ticker"],
    title=mkt["title"],
    category=mkt.get("category", ""),
    status=mkt["status"],
    close_time=parse_dt(mkt["close_time"]),
    updated_at=datetime.now(UTC),
).on_conflict_do_update(
    index_elements=["ticker"],
    set_={
        "title": mkt["title"],
        "status": mkt["status"],
        "close_time": parse_dt(mkt["close_time"]),
        "updated_at": datetime.now(UTC),
    }
)
await session.execute(stmt)
```

### Pattern 4: Rolling Volatility with deque
**What:** In-memory dict of deques, one per ticker, accumulate yes_price per cycle
**When to use:** SCAN-06 volatility filter
**Example:**
```python
from collections import deque
import statistics

# Class-level state — persists across scan cycles
_price_history: dict[str, deque] = {}
VOLATILITY_WARMUP = 6  # configurable

def compute_volatility(ticker: str, current_price: float) -> float | None:
    if ticker not in _price_history:
        _price_history[ticker] = deque(maxlen=50)
    _price_history[ticker].append(current_price)
    if len(_price_history[ticker]) < VOLATILITY_WARMUP:
        return None  # warmup — filter skipped
    return statistics.stdev(_price_history[ticker])
```

### Pattern 5: Enrichment Phase (candidates only)
**What:** After filtering, fetch orderbook + event context for each candidate
**When to use:** Only for markets that passed all filters (avoid per-market API calls at scale)
**Example:**
```python
async def _enrich(self, ticker: str, event_ticker: str) -> dict:
    orderbook_data = await self._client._request(
        "GET", f"/trade-api/v2/markets/{ticker}/orderbook",
        params={"depth": 3}  # top 3 levels sufficient for bid/ask depth
    )
    event_data = await self._client._request(
        "GET", f"/trade-api/v2/events/{event_ticker}",
        params={"with_nested_markets": False}
    )
    return {"orderbook": orderbook_data, "event": event_data}
```

### Anti-Patterns to Avoid
- **Using `liquidity_dollars` field:** Documented as deprecated and always returns "0.0". Use `open_interest_fp` as the liquidity proxy instead.
- **Enriching before filtering:** Fetching orderbook for every scanned market would multiply API calls by 2x at minimum. Enrich only passing candidates.
- **Hard-coding threshold values in filter logic:** All thresholds (min_volume, min_open_interest, max_spread, min_ttl_hours, max_ttl_days, min_volatility) must come from Settings fields so they are configurable via YAML without code changes.
- **Using `statistics.stdev` with fewer than 2 data points:** It raises `StatisticsError`. Always guard with the warmup length check first.
- **Assuming close_time is always a datetime:** Kalshi returns ISO 8601 strings. Parse with `datetime.fromisoformat()` or `dateutil.parser.parse()` before comparison.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry on 429/5xx | Custom retry loop | `@kalshi_retry` (already exists) | Handles exponential backoff + jitter, already tested in Phase 1 |
| Settings validation | Custom config class | Extend `Settings` in `config.py` | pydantic-settings handles type coercion, env override, YAML source |
| Async DB session | Manual session lifecycle | `get_session()` context manager | Already handles rollback on exception |
| Structured logging with correlation | Custom formatter | `logger.bind(cycle_id=...)` | Loguru contextual binding already established |
| Pydantic model field validation | Manual isinstance checks | Pydantic `Field(ge=0, le=1)` validators | Free runtime validation on construction |

**Key insight:** Phase 1 built the entire infrastructure layer. Phase 2 only needs to write business logic (filter functions, scanner loop, Pydantic models) — no new infrastructure is required.

---

## Common Pitfalls

### Pitfall 1: liquidity_dollars Always Zero
**What goes wrong:** Code filters on `float(market["liquidity_dollars"]) >= threshold` — always passes because field is always "0.0".
**Why it happens:** Kalshi documented this field as deprecated in API v2; it was removed from actual data but still appears in responses.
**How to avoid:** Filter on `open_interest_fp` (total contracts bought) as the liquidity proxy. Verified by API docs and confirmed deprecated.
**Warning signs:** Liquidity filter never rejects any market regardless of threshold value.

### Pitfall 2: Fixed-Point String Price Fields
**What goes wrong:** Comparing `market["yes_bid_dollars"] >= 0.05` raises TypeError because the field is a string like `"0.0500"`.
**Why it happens:** Kalshi API v2 uses fixed-point decimal strings for price fields, not native floats.
**How to avoid:** Always parse: `float(market.get("yes_bid_dollars", "0"))`. Document this in the models module.
**Warning signs:** TypeError or unexpected comparison results in filter functions.

### Pitfall 3: statistics.stdev with Single Sample
**What goes wrong:** Calling `statistics.stdev([0.62])` raises `StatisticsError: stdev requires at least two data points`.
**Why it happens:** Rolling history has fewer than 2 values (e.g., second scan cycle, first market seen).
**How to avoid:** Warmup guard must check `len(history) >= max(VOLATILITY_WARMUP, 2)` before calling stdev.
**Warning signs:** StatisticsError crash during second scan cycle.

### Pitfall 4: close_time Parsing
**What goes wrong:** `datetime.now(UTC) - market["close_time"]` raises TypeError because close_time is a string.
**Why it happens:** Kalshi returns ISO 8601 timestamps as strings, not Python datetime objects.
**How to avoid:** Parse on ingestion: `datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))`. Centralize this parse in a helper function used by both the upsert and the TTR filter.
**Warning signs:** TypeError in the time-to-resolution filter.

### Pitfall 5: Scan Cycle Memory Leaks from price_history
**What goes wrong:** `_price_history` dict grows unboundedly as Kalshi lists new tickers. Markets that resolve and are removed from the active feed still hold history entries.
**Why it happens:** No eviction logic for tickers that no longer appear in scan results.
**How to avoid:** Use `deque(maxlen=50)` so old snapshots auto-evict. Periodically (e.g., once per 100 cycles) remove tickers not seen in the last N cycles from the dict. OR accept bounded memory growth — with ~1000 markets, 50 floats each = ~50KB, acceptable.
**Warning signs:** Process memory grows without bound after days of running.

### Pitfall 6: Upsert Silently Not Committing
**What goes wrong:** Upserted rows never appear in the DB because `session.commit()` was not awaited.
**Why it happens:** AsyncSession is not auto-commit by default; the context manager only rolls back on exception, does not auto-commit.
**How to avoid:** Always `await session.commit()` inside the `get_session()` context manager after execute calls. Verify with an integration test against real DB.
**Warning signs:** DB tables remain empty despite scanner logging success.

---

## Code Examples

Verified patterns from official sources and existing Phase 1 codebase:

### Full Pagination Loop (SCAN-01)
```python
# Source: https://docs.kalshi.com/getting_started/pagination + existing client pattern
async def _fetch_all_active_markets(self) -> list[dict]:
    all_markets: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict = {"status": "active", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data = await self._client._request(
            "GET", "/trade-api/v2/markets", params=params
        )
        page = data.get("markets", [])
        all_markets.extend(page)
        cursor = data.get("cursor") or None
        if not cursor:
            break
    return all_markets
```

### Kalshi API Price Fields Reference
```python
# Source: https://docs.kalshi.com/api-reference/market/get-markets
# All price fields are fixed-point decimal strings
market = {
    "ticker": "SOME-MARKET-24",
    "yes_bid_dollars": "0.6200",   # highest YES buy offer (62 cents = 62%)
    "yes_ask_dollars": "0.6500",   # lowest YES sell offer (65 cents)
    "volume_24h_fp": "15000.0",    # 24h contract volume (fixed point)
    "open_interest_fp": "42000.0", # total contracts bought (use as liquidity proxy)
    "close_time": "2026-03-20T18:00:00Z",
    "status": "active",
    "event_ticker": "SOME-EVENT",
}
# Parse for use:
yes_bid = float(market["yes_bid_dollars"])   # 0.62
yes_ask = float(market["yes_ask_dollars"])   # 0.65
spread = yes_ask - yes_bid                   # 0.03 (3 cents)
implied_prob = (yes_bid + yes_ask) / 2       # 0.635 mid-price
volume_24h = float(market["volume_24h_fp"])  # 15000.0
open_interest = float(market["open_interest_fp"])  # 42000.0
```

### Orderbook Endpoint Response
```python
# Source: https://docs.kalshi.com/api-reference/market/get-market-orderbook
# GET /trade-api/v2/markets/{ticker}/orderbook?depth=3
{
    "orderbook_fp": {
        "yes_dollars": [
            ["0.6200", "500.00"],   # [price, quantity] at best bid
            ["0.6100", "300.00"],
            ["0.6000", "200.00"],
        ],
        "no_dollars": [
            ["0.3500", "400.00"],   # no_bid at 0.35 implies yes_ask at 0.65
            ["0.3400", "250.00"],
            ["0.3300", "150.00"],
        ]
    }
}
# yes_ask = 1 - best_no_bid = 1 - 0.35 = 0.65
```

### MarketCandidate Pydantic Model (pattern)
```python
# Source: CONTEXT.md decision + Pydantic v2 patterns
from datetime import datetime
from pydantic import BaseModel, Field

class MarketCandidate(BaseModel):
    ticker: str
    title: str
    category: str
    event_context: dict        # event title, related market count
    close_time: datetime
    yes_bid: float = Field(ge=0, le=1)
    yes_ask: float = Field(ge=0, le=1)
    implied_probability: float = Field(ge=0, le=1)  # (bid+ask)/2
    spread: float = Field(ge=0)                      # ask - bid
    volume_24h: float = Field(ge=0)
    volatility_score: float | None = None            # None during warmup

class ScanResult(BaseModel):
    candidates: list[MarketCandidate]
    total_markets: int
    rejected_liquidity: int
    rejected_volume: int
    rejected_spread: int
    rejected_ttr: int
    rejected_volatility: int
    scan_duration_seconds: float
    cycle_id: str
```

### Threshold Settings Extension
```python
# Source: existing config.py pattern + CONTEXT.md decision
# Add these fields to the existing Settings class in src/pmtb/config.py:

# --- Scanner filter thresholds ---
scanner_min_open_interest: float = Field(
    default=500.0,
    description="Minimum open interest (contracts) to pass liquidity filter",
)
scanner_min_volume_24h: float = Field(
    default=200.0,
    description="Minimum 24h volume (contracts) to pass volume filter",
)
scanner_max_spread: float = Field(
    default=0.10,
    description="Maximum bid-ask spread (dollars, 0-1) to pass spread filter",
)
scanner_min_ttr_hours: float = Field(
    default=1.0,
    description="Minimum hours to resolution (exclude near-expiry markets)",
)
scanner_max_ttr_days: float = Field(
    default=30.0,
    description="Maximum days to resolution (exclude far-future markets)",
)
scanner_min_volatility: float = Field(
    default=0.005,
    description="Minimum price std dev to pass volatility filter (skip during warmup)",
)
scanner_volatility_warmup: int = Field(
    default=6,
    description="Minimum snapshots before volatility filter applies",
)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| kalshi-python-async SDK | httpx.AsyncClient with manual RSA-PSS | Phase 1 decision | Avoids urllib3 incompatibility; established in codebase |
| `liquidity_dollars` for liquidity filter | `open_interest_fp` | API v2 release | `liquidity_dollars` always returns 0; open_interest is the real metric |
| Separate yes/no price asks | Only bids returned from orderbook | API v2 design | yes_ask = 1 - best_no_bid; no duplication in response |

**Deprecated/outdated:**
- `liquidity_dollars`: Always returns "0.0000" per API docs — do not use for filtering.
- `yes_bid` / `yes_ask` (integer cents): API v2 uses `yes_bid_dollars` / `yes_ask_dollars` (string decimals). The non-`_dollars` variants may exist for backwards compatibility but are not the canonical v2 fields.

---

## Open Questions

1. **Exact Kalshi market count per scan**
   - What we know: API paginates up to 1000 per page; active market count is unknown without live credentials
   - What's unclear: Whether active market count ever exceeds 1000 (single page) or requires multiple pages regularly
   - Recommendation: Implement full pagination regardless — cursor loop has no cost if single page

2. **Recommended default thresholds for liquidity/volume/spread**
   - What we know: `open_interest_fp` is the liquidity proxy; `volume_24h_fp` is the volume field. No public distribution data available without live API access.
   - What's unclear: Whether 500 contracts open interest and 200 contracts 24h volume are too aggressive or too lax for Kalshi's actual market distribution
   - Recommendation: Set conservative defaults (low open interest / volume thresholds, wide spread tolerance) and tune via DEBUG logs of rejection counts over first few live cycles. Suggested starting defaults: open_interest >= 100, volume_24h >= 50, spread <= 0.15

3. **Enrichment concurrency**
   - What we know: Enrichment fetches orderbook + event per candidate; @kalshi_retry handles 429
   - What's unclear: Whether fetching enrichments sequentially is fast enough or whether asyncio.gather() is needed
   - Recommendation: Use asyncio.gather() with a semaphore (e.g., limit=5 concurrent) for candidates. Respects rate limits without fully serializing.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `python -m pytest tests/scanner/ -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCAN-01 | Pagination fetches all pages until cursor is None | unit | `python -m pytest tests/scanner/test_scanner.py::test_pagination_fetches_all_pages -x` | Wave 0 |
| SCAN-01 | Empty cursor stops pagination | unit | `python -m pytest tests/scanner/test_scanner.py::test_pagination_stops_on_empty_cursor -x` | Wave 0 |
| SCAN-02 | Markets below open_interest threshold are rejected | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_liquidity -x` | Wave 0 |
| SCAN-03 | Markets below volume_24h threshold are rejected | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_volume -x` | Wave 0 |
| SCAN-04 | Markets resolving in < 1h are excluded | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_ttr_too_soon -x` | Wave 0 |
| SCAN-04 | Markets resolving in > 30d are excluded | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_ttr_too_far -x` | Wave 0 |
| SCAN-05 | Markets with spread > max_spread are rejected | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_spread -x` | Wave 0 |
| SCAN-06 | Volatility filter skipped during warmup | unit | `python -m pytest tests/scanner/test_filters.py::test_volatility_warmup_skip -x` | Wave 0 |
| SCAN-06 | Low volatility markets rejected after warmup | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_volatility -x` | Wave 0 |
| SCAN-07 | ScanResult contains valid MarketCandidate objects | unit | `python -m pytest tests/scanner/test_models.py -x` | Wave 0 |
| SCAN-07 | run_cycle() returns ScanResult with correct rejection counts | unit | `python -m pytest tests/scanner/test_scanner.py::test_run_cycle_returns_scan_result -x` | Wave 0 |
| SCAN-07 | Candidates sorted by distance from 50% | unit | `python -m pytest tests/scanner/test_scanner.py::test_candidates_sorted_by_edge_potential -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/scanner/ -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/scanner/__init__.py` — package init
- [ ] `tests/scanner/test_models.py` — MarketCandidate and ScanResult validation
- [ ] `tests/scanner/test_filters.py` — each filter function in isolation
- [ ] `tests/scanner/test_scanner.py` — MarketScanner cycle logic with mocked client + session

---

## Sources

### Primary (HIGH confidence)
- [Kalshi Get Markets API](https://docs.kalshi.com/api-reference/market/get-markets) — query parameters, pagination, all field names including `yes_bid_dollars`, `yes_ask_dollars`, `volume_24h_fp`, `open_interest_fp`, `liquidity_dollars` deprecation
- [Kalshi Pagination Docs](https://docs.kalshi.com/getting_started/pagination) — cursor-based pagination pattern with Python example
- [Kalshi Get Market Orderbook](https://docs.kalshi.com/api-reference/market/get-market-orderbook) — endpoint path, `depth` parameter (0=all, 1-100=specific), response format (`orderbook_fp` with yes_dollars/no_dollars bid arrays)
- [Kalshi Get Event](https://docs.kalshi.com/api-reference/events/get-event) — event endpoint, `with_nested_markets` parameter
- Phase 1 source code — `KalshiClient`, `Settings`, `get_session`, `@kalshi_retry`, `Market` ORM model, Loguru patterns

### Secondary (MEDIUM confidence)
- [Kalshi API Developer Guide - Zuplo](https://zuplo.com/learning-center/kalshi-api) — general API patterns corroborating official docs
- [Kalshi Orderbook Responses](https://docs.kalshi.com/getting_started/orderbook_responses) — orderbook response format details

### Tertiary (LOW confidence)
None — all critical claims verified from official Kalshi documentation.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; all Phase 1 libraries confirmed
- Architecture: HIGH — pagination pattern verified from official Kalshi docs; all field names confirmed
- Pitfalls: HIGH — `liquidity_dollars` deprecation confirmed from official docs; string-type price fields confirmed; statistics.stdev behavior is stdlib documented behavior
- Default threshold values: LOW — no live market distribution data available; starting values are conservative estimates

**Research date:** 2026-03-10
**Valid until:** 2026-04-10 (Kalshi API is stable; field changes would be in release notes)
