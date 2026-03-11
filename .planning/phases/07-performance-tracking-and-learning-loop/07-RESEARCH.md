# Phase 7: Performance Tracking and Learning Loop - Research

**Researched:** 2026-03-11
**Domain:** Trading performance metrics, ML retraining loops, backtesting, trade resolution detection
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Metrics Computation**
- Dual trigger: incremental update on each trade resolution + full daily recomputation for consistency
- Dual window: all-time metrics for overall performance + rolling window (configurable, default 30 days) for trend detection
- Rolling window metrics are what trigger retraining decisions
- Sharpe ratio: daily returns, annualized with sqrt(252) — standard quant approach
- Brier score, win rate, profit factor computed on resolved trades
- All metrics persisted to PerformanceMetric DB table (already exists)

**Losing Trade Analysis**
- Hybrid classification: rule-based heuristics first, Claude for ambiguous cases
- Fixed error taxonomy (enum): edge_decay, signal_error, llm_error, sizing_error, market_shock, unknown
- Rule-based heuristics: edge_decay if market moved against before resolution, signal_error if majority of research signals were wrong, llm_error if Claude's p_estimate was the outlier, sizing_error if edge correct but position too large
- Claude invoked only when rule-based classifier returns 'unknown' — keeps API cost low
- Classification only — no actionable recommendations in v1 (insufficient sample size early on)
- Error type and reasoning persisted to DB for pattern tracking over time

**Retraining Trigger**
- Dual trigger: periodic schedule (configurable via Settings, default weekly) AND Brier score degradation beyond configurable threshold
- Training data: all resolved trades with weighted recency — recent trades weighted higher to adapt to market regime changes
- Auto-replace if improved: retrained model replaces live model only if hold-out Brier score improves; if worse, keep current model and log failed attempt
- XGBoost.train() and save()/load() via joblib already implemented (Phase 4)
- Retraining event logged with before/after Brier scores, sample count, model version

**Backtesting Engine**
- Data sources: PostgreSQL (resolved trades, predictions, signals) + Kalshi historical API for backfill
- Same code paths as live trading: backtester calls ProbabilityPipeline.predict_all() and KellySizer.size() with swapped data source (PERF-08)
- Temporal integrity enforced: must prevent lookahead bias
- Results: structured log to stdout + backtest_runs table in PostgreSQL (metrics, parameters, date range)
- No visualization — CLI/logs only per PROJECT.md scope

### Claude's Discretion
- Trade resolution detection approach (polling vs WS vs both)
- Brier score degradation threshold default value
- Rolling window size default (suggested 30 days)
- Recency weighting function for retraining data
- Temporal integrity enforcement approach (timestamp filtering vs data source protocol)
- Backtesting engine invocation method (CLI command, scheduled job, or manual trigger)
- Kalshi historical API integration approach for backfill
- Hold-out split strategy for model comparison during retraining

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PERF-01 | System tracks Brier score across all resolved predictions | Kalshi GET /portfolio/settlements provides market_result; brier_score_loss already in sklearn, PerformanceMetric table already exists |
| PERF-02 | System tracks Sharpe ratio of the portfolio | Daily PnL from resolved Trade rows; annualize with sqrt(252); PerformanceMetric table stores result |
| PERF-03 | System tracks win rate and profit factor | Win rate = wins/total; profit factor = gross_profit/gross_loss; both computable from Trade.pnl + resolved_outcome |
| PERF-04 | System classifies losing trades by error type | Hybrid rule-based + Claude fallback; ModelOutput.signal_weights, PredictionResult.used_llm, Trade fields provide all needed inputs |
| PERF-05 | Model learning loop feeds resolved outcomes back into XGBoost retraining pipeline | XGBoostPredictor.train() + joblib save/load already implemented; sample_weight param for recency weighting |
| PERF-06 | Learning loop triggers retraining when Brier score degrades beyond threshold | Rolling Brier score from PerformanceMetric rows; threshold comparison in learning loop; dual trigger (periodic + threshold) |
| PERF-07 | Backtesting engine validates strategies against historical market data | Kalshi GET /portfolio/settlements + GET /markets for history; PostgreSQL for stored trades/signals/predictions |
| PERF-08 | Backtesting uses same model/sizer code paths as live trading | ProbabilityPipeline.predict_all() + KellySizer.size() called with BacktestDataSource adapter; temporal filter enforced |
</phase_requirements>

---

## Summary

Phase 7 closes the learning loop by adding three orthogonal subsystems on top of the fully-functional live trading pipeline from Phases 1-6. The core infrastructure — DB models, XGBoost training/loading, async session patterns, Prometheus counters, Loguru structured logging, Pydantic contracts — is already in place. Phase 7 builds three new components: a MetricsService that computes Brier/Sharpe/win rate/profit factor on resolved trades; a LossClassifier that diagnoses losing trades using rule-based heuristics with Claude fallback; and a BacktestEngine that replays ProbabilityPipeline and KellySizer against historical data with a swapped data source.

The key architectural decision for discretion areas: trade resolution detection should use polling `GET /portfolio/settlements` rather than WebSocket events. The WS feed supports `fill` and `orderbook_delta` channels but does not emit settlement events; polling on a configurable interval (default 60s) with cursor-based pagination is the correct approach. For backtesting temporal integrity, a `BacktestDataSource` protocol that only returns signals/prices with `created_at <= decision_timestamp` is cleaner than pervasive timestamp filtering, keeps the violation detectable at a single boundary, and requires zero changes to ProbabilityPipeline internals.

The retraining trigger should use APScheduler `AsyncIOScheduler` with an `IntervalTrigger` (default weekly) running in the same asyncio event loop as the main orchestrator. This avoids introducing threading complexity and fits the existing `asyncio.gather` pattern in orchestrator.py. Hold-out split should be a fixed 20% temporal tail (most recent 20% of resolved trades) — never random split, which would introduce lookahead bias.

**Primary recommendation:** Use polling `GET /portfolio/settlements` for resolution detection, APScheduler AsyncIOScheduler for retraining scheduling, `BacktestDataSource` protocol for temporal integrity, and a `LossAnalysis` DB table (new) for persisting error classifications.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| scikit-learn | >=1.8.0 (already installed) | `brier_score_loss` computation | Already used in xgboost_model.py; authoritative implementation |
| numpy | (transitive, already present) | Array ops for Sharpe/profit factor math | Brier score and Sharpe ratio require array operations |
| APScheduler | 3.x (new dependency) | Periodic retraining job scheduling | Provides AsyncIOScheduler that integrates with existing asyncio event loop without threads |
| pandas | (optional, avoid if possible) | Rolling window aggregation | Only needed if rolling Brier over hundreds of trades — SQL window functions can substitute |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| joblib | (transitive via scikit-learn, already installed) | Model persistence for save/load before/after comparison | Already used by XGBoostPredictor; no new dependency |
| anthropic | >=0.84.0 (already installed) | Claude fallback for ambiguous loss classification | Only invoked when rule-based classifier returns 'unknown' |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| APScheduler | asyncio.sleep loop | APScheduler gives cron/interval triggers declaratively; sleep loop is simpler but harder to inspect/adjust at runtime |
| APScheduler | Celery | Celery requires Redis/RabbitMQ broker — overkill for a single-process bot |
| SQL window functions | pandas rolling | Keeps computation in DB, no new library dependency; pandas worth it only if rolling Brier needs offline analysis |

**Installation:**
```bash
uv add apscheduler
```

---

## Architecture Patterns

### Recommended Project Structure
```
src/pmtb/
├── performance/           # All phase 7 code
│   ├── __init__.py
│   ├── metrics.py         # MetricsService: Brier, Sharpe, win rate, profit factor
│   ├── loss_classifier.py # LossClassifier: rule-based + Claude fallback
│   ├── learning_loop.py   # LearningLoop: retraining trigger, scheduler
│   ├── backtester.py      # BacktestEngine + BacktestDataSource protocol
│   └── models.py          # Pydantic models: MetricsSnapshot, LossAnalysis, BacktestResult
db/
├── models.py              # Add LossAnalysis, BacktestRun tables (migration needed)
├── migrations/            # New Alembic migration for new tables
config.py                  # Add: brier_threshold, retraining_schedule_hours, rolling_window_days
```

### Pattern 1: Trade Resolution Detection via Polling
**What:** Periodic async task polls `GET /portfolio/settlements` with cursor pagination; for each settlement, queries local Trade/ModelOutput rows by ticker to match and updates `resolved_outcome`, `resolved_at`, `pnl`
**When to use:** Always — the Kalshi WS `fill` channel does not emit settlement events
**Example:**
```python
# Source: https://docs.kalshi.com/api-reference/portfolio/get-settlements
async def poll_settlements(self, since: datetime) -> list[dict]:
    """
    Fetch settlements after `since` using min_ts filter.
    Returns list of settlement dicts with: ticker, market_result, settled_time, revenue.
    """
    params = {"min_ts": int(since.timestamp()), "limit": 200}
    data = await self._client._request("GET", "/trade-api/v2/portfolio/settlements", params=params)
    return data.get("settlements", [])
```

### Pattern 2: Metrics Computation — Dual Window
**What:** After each resolution event, compute incremental update to running all-time metrics. Daily job recomputes full rolling window metrics from scratch for consistency.
**When to use:** Both triggers are always active; incremental is fast, daily batch catches any inconsistencies
**Example:**
```python
# Brier score over resolved trades
from sklearn.metrics import brier_score_loss
import numpy as np

def compute_brier(p_models: list[float], outcomes: list[int]) -> float:
    """outcomes: 1=yes resolved, 0=no resolved"""
    return float(brier_score_loss(outcomes, p_models))

def compute_sharpe(daily_pnl: list[float]) -> float:
    """Annualized Sharpe using sqrt(252). Returns nan if std==0."""
    arr = np.array(daily_pnl, dtype=float)
    if arr.std() == 0:
        return float("nan")
    return float((arr.mean() / arr.std()) * np.sqrt(252))

def compute_profit_factor(pnl_values: list[float]) -> float:
    """gross_profit / gross_loss. Returns inf if no losses."""
    wins = [p for p in pnl_values if p > 0]
    losses = [abs(p) for p in pnl_values if p < 0]
    if not losses:
        return float("inf")
    return sum(wins) / sum(losses)
```

### Pattern 3: Recency-Weighted Retraining
**What:** Recent resolved trades get higher sample weights; exponential decay by days-since-resolution
**When to use:** Every retraining call to `XGBoostPredictor.train()`
**Example:**
```python
import numpy as np
from datetime import datetime, UTC

def compute_recency_weights(resolved_ats: list[datetime], half_life_days: float = 30.0) -> np.ndarray:
    """
    Exponential decay: weight = exp(-lambda * age_days)
    where lambda = ln(2) / half_life_days
    """
    now = datetime.now(UTC)
    ages = np.array([(now - t).total_seconds() / 86400.0 for t in resolved_ats])
    lam = np.log(2) / half_life_days
    weights = np.exp(-lam * ages)
    return weights / weights.sum()  # normalize

# Pass to XGBoostPredictor.train:
# metrics = predictor.train(X, y, sample_weight=weights)
# NOTE: CalibratedClassifierCV.fit() accepts sample_weight kwarg
```

### Pattern 4: BacktestDataSource Protocol
**What:** Protocol class with the same interface as live data sources but filters all returned data to `created_at <= decision_timestamp`. Backtester passes an instance to ProbabilityPipeline instead of live sources.
**When to use:** Backtesting only — enforces temporal integrity at data source boundary
**Example:**
```python
from typing import Protocol
from datetime import datetime

class DataSource(Protocol):
    """Contract for data sources used by ProbabilityPipeline."""
    async def get_signals(self, ticker: str, as_of: datetime) -> list: ...
    async def get_market_price(self, ticker: str, as_of: datetime) -> float: ...

class BacktestDataSource:
    """Reads from PostgreSQL; enforces as_of temporal filter on all queries."""
    def __init__(self, session_factory, as_of: datetime):
        self._session_factory = session_factory
        self._as_of = as_of

    async def get_signals(self, ticker: str, as_of: datetime) -> list:
        # SELECT * FROM signals WHERE market_ticker = :ticker AND created_at <= :as_of
        ...
```

### Pattern 5: APScheduler AsyncIOScheduler Integration
**What:** Retraining loop runs as a scheduled async job inside the same asyncio event loop
**When to use:** When wiring LearningLoop into PipelineOrchestrator
**Example:**
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

class LearningLoop:
    def __init__(self, predictor, session_factory, settings):
        self._predictor = predictor
        self._session_factory = session_factory
        self._settings = settings
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.add_job(
            self._maybe_retrain,
            trigger=IntervalTrigger(hours=self._settings.retraining_schedule_hours),
            id="retraining_loop",
            replace_existing=True,
        )
        self._scheduler.start()

    async def _maybe_retrain(self) -> None:
        # Check rolling Brier score; retrain if degraded or schedule trigger
        ...

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
```

### Pattern 6: Hold-out Split for Model Comparison
**What:** Reserve the most recent 20% of resolved trades (by resolved_at) as hold-out set; train on 80%, evaluate both old and new model on the 20% hold-out
**When to use:** Every retraining event; never random split (would leak future data into training)
**Example:**
```python
def temporal_train_test_split(
    X: np.ndarray, y: np.ndarray, resolved_ats: list[datetime], test_fraction: float = 0.2
) -> tuple:
    """Sort by resolved_at ascending; last test_fraction% is hold-out."""
    n = len(y)
    split_idx = int(n * (1 - test_fraction))
    # X, y, resolved_ats already sorted by resolved_at ascending from DB query
    return X[:split_idx], X[split_idx:], y[:split_idx], y[split_idx:]
```

### Anti-Patterns to Avoid
- **Random train/test split for backtesting or model comparison:** Leaks future labels into training set; always use temporal split on resolved_at
- **Importing ProbabilityPipeline into BacktestEngine with different logic:** PERF-08 requires exact same code paths; use the protocol adapter pattern, not a reimplementation
- **Hardcoding Brier threshold:** Add `brier_degradation_threshold` to Settings so it is tunable without code changes
- **Writing PerformanceMetric rows from multiple concurrent tasks:** The daily batch and incremental trigger must coordinate; use a single MetricsService instance with an asyncio Lock
- **Calling `predictor.save()` before validating hold-out improvement:** Always compare old vs new Brier score on hold-out BEFORE saving the new model

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Brier score computation | Custom formula | `sklearn.metrics.brier_score_loss` | Edge cases in probability clipping; already a dependency |
| Model serialization | Custom pickle/json | `joblib.dump/load` (already in XGBoostPredictor) | Already implemented and tested in Phase 4 |
| Job scheduling | `asyncio.sleep` while-loop | APScheduler AsyncIOScheduler | Handles missed executions, cron syntax, concurrent job guards |
| Annualized Sharpe | Custom formula | numpy formula (`mean/std * sqrt(252)`) | Simple but needs zero-std guard; well-understood formula |

**Key insight:** The hardest part of this phase is temporal integrity in backtesting. The BacktestDataSource protocol approach is the right pattern — it centralizes the `as_of` filter at the data source boundary rather than sprinkling timestamp guards throughout ProbabilityPipeline internals.

---

## Common Pitfalls

### Pitfall 1: Lookahead Bias in Backtesting
**What goes wrong:** BacktestEngine queries signals or model outputs that were created after the decision timestamp, inflating backtested performance
**Why it happens:** Signals table is append-only; a JOIN without a timestamp filter returns all signals for a ticker, including future ones
**How to avoid:** All DB queries in BacktestDataSource must include `AND created_at <= :as_of` where `as_of` is the simulated decision timestamp
**Warning signs:** Backtested Brier score is unrealistically better than live Brier score

### Pitfall 2: CalibratedClassifierCV + sample_weight
**What goes wrong:** `CalibratedClassifierCV.fit()` passes `sample_weight` through to internal CV fold fits, but the kwarg must be passed as a kwargs dict in older sklearn versions
**Why it happens:** sklearn API changed between versions
**How to avoid:** Use `calibrated.fit(X, y, sample_weight=weights)` — confirmed valid in sklearn >=1.0; verify with existing min_training_samples guard (100 samples) which gives enough data for cv=5
**Warning signs:** TypeError or UserWarning about ignored sample_weight during calibration

### Pitfall 3: Brier Score on Empty or Constant Prediction Sets
**What goes wrong:** `brier_score_loss([1,1,1], [0.9,0.9,0.9])` raises no error but computing rolling Brier over fewer than ~10 trades gives unreliable estimates; a 0-trade window raises ZeroDivisionError in custom computations
**Why it happens:** Early in system life, resolved trade count is very low
**How to avoid:** Guard all metric computations with minimum sample count check (e.g., `if len(outcomes) < 10: return None`); store `None`/skip PerformanceMetric row rather than writing a misleading value
**Warning signs:** Retraining triggered on day 1 with 2 resolved trades

### Pitfall 4: Kalshi `market_result` Field Mapping
**What goes wrong:** `GET /portfolio/settlements` returns `market_result` as `"yes"`, `"no"`, `"scalar"`, or `"void"`. Void markets (disputed/amended) should not be included in Brier score computation
**Why it happens:** Void markets have no true binary outcome; including them distorts calibration metrics
**How to avoid:** Filter settlements to `market_result in ("yes", "no")` only; map to int: yes=1, no=0
**Warning signs:** Brier score suddenly jumps after a voided market is included

### Pitfall 5: PnL Field Nullable in Trade Model
**What goes wrong:** `Trade.pnl` is `Numeric | None` — Sharpe ratio and profit factor computations crash on None values if not handled
**Why it happens:** PnL is set at resolution time (Phase 7's job), not at fill time; early trades before Phase 7 deployed have pnl=None
**How to avoid:** Filter `WHERE pnl IS NOT NULL AND resolved_at IS NOT NULL` in all metrics queries; log count of skipped trades
**Warning signs:** `TypeError: unsupported operand type(s) for +: 'NoneType' and 'Decimal'`

### Pitfall 6: APScheduler Job Overlapping
**What goes wrong:** If retraining takes longer than `retraining_schedule_hours`, a second retraining job starts while the first is still running, potentially causing concurrent model writes
**Why it happens:** APScheduler's default `max_instances=1` per job, but this must be set explicitly
**How to avoid:** Use `max_instances=1` when adding the retraining job; APScheduler will skip overlapping executions and log a warning
**Warning signs:** Two simultaneous joblib saves to the same model file

---

## Code Examples

Verified patterns from official sources and existing codebase:

### Resolving Trades via GET /portfolio/settlements
```python
# Source: https://docs.kalshi.com/api-reference/portfolio/get-settlements
# market_result: "yes" | "no" | "scalar" | "void"
# settled_time: ISO 8601 timestamp
# revenue: payout in cents (divide by 100 for dollars)
# ticker: market ticker (matches Market.ticker in DB)

async def fetch_recent_settlements(client, since_ts: int) -> list[dict]:
    data = await client._request(
        "GET",
        "/trade-api/v2/portfolio/settlements",
        params={"min_ts": since_ts, "limit": 200},
    )
    return data.get("settlements", [])
```

### XGBoostPredictor.train() with sample_weight (recency weighting)
```python
# Source: existing src/pmtb/prediction/xgboost_model.py (Phase 4)
# CalibratedClassifierCV.fit() accepts sample_weight as positional kwarg
# XGBoostPredictor.train() needs a sample_weight parameter added

def train(self, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None) -> dict[str, float]:
    # ... existing guard on n_samples ...
    raw_clf.fit(X, y, sample_weight=sample_weight)
    calibrated.fit(X, y, sample_weight=sample_weight)
    # ... rest unchanged ...
```

### PerformanceMetric persistence (existing table)
```python
# Source: src/pmtb/db/models.py — PerformanceMetric already defined
# Fields: metric_name (str), metric_value (Numeric), period (str | None), computed_at (DateTime)
# Write pattern follows existing async session factory

async def persist_metric(session_factory, name: str, value: float, period: str) -> None:
    async with session_factory() as session:
        async with session.begin():
            row = PerformanceMetric(
                metric_name=name,
                metric_value=Decimal(str(value)),
                period=period,  # e.g. "alltime" or "30d"
            )
            session.add(row)
```

### New Settings fields to add
```python
# Source: src/pmtb/config.py — follow existing Field pattern
brier_degradation_threshold: float = Field(
    default=0.05,
    description="Brier score increase (degradation) above rolling baseline that triggers retraining",
)
retraining_schedule_hours: int = Field(
    default=168,  # 7 days
    description="Hours between periodic retraining runs",
)
rolling_window_days: int = Field(
    default=30,
    description="Days of resolved trades used for rolling performance metrics",
)
retraining_half_life_days: float = Field(
    default=30.0,
    description="Half-life in days for exponential recency weighting during retraining",
)
```

---

## New DB Tables Required

Two new tables are needed and require an Alembic migration:

### LossAnalysis
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| trade_id | UUID FK → trades.id | |
| error_type | String | enum: edge_decay, signal_error, llm_error, sizing_error, market_shock, unknown |
| reasoning | String (nullable) | Claude's explanation if invoked |
| classified_by | String | "rules" or "claude" |
| created_at | DateTime(tz=True) | |

### BacktestRun
| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| run_at | DateTime(tz=True) | |
| start_date | DateTime(tz=True) | Backtest period start |
| end_date | DateTime(tz=True) | Backtest period end |
| trade_count | Integer | Number of simulated trades |
| brier_score | Numeric (nullable) | |
| sharpe_ratio | Numeric (nullable) | |
| win_rate | Numeric (nullable) | |
| profit_factor | Numeric (nullable) | |
| parameters | JSON | Strategy parameters used |
| created_at | DateTime(tz=True) | |

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Celery for ML job scheduling | APScheduler AsyncIOScheduler | ~2022 for single-process bots | No broker dependency; native asyncio integration |
| Random train/test split for temporal models | Temporal split on sorted timestamps | Standard since 2018 | Eliminates lookahead bias in calibration metrics |
| `model.fit()` without sample_weight | `model.fit(X, y, sample_weight=weights)` for recency | sklearn >=0.9 | Adapts model to market regime changes |
| Polling market status endpoint | Polling `GET /portfolio/settlements` with `min_ts` | Kalshi API v2 | Settlement endpoint returns resolved outcome directly; avoids N×market status calls |

**Deprecated/outdated:**
- `XGBClassifier.use_label_encoder`: Removed in XGBoost 2.0+ — already excluded in Phase 4 implementation

---

## Open Questions

1. **Trade-to-ModelOutput matching for loss classification**
   - What we know: Trade has `market_id`, ModelOutput has `market_id` + `cycle_id`. No direct foreign key.
   - What's unclear: The most recent ModelOutput before `Trade.created_at` for the same `market_id` is the correct one to use for diagnosis. Need to verify this JOIN is reliable.
   - Recommendation: Use `SELECT ... WHERE market_id = :mid AND created_at <= :trade_created_at ORDER BY created_at DESC LIMIT 1` as the matching strategy; log warning if no ModelOutput found

2. **Brier score degradation threshold default**
   - What we know: Brier score ranges 0-1 (lower is better); a perfectly calibrated model on 50/50 markets scores ~0.25
   - What's unclear: What constitutes "degradation" for a prediction market bot — depends on baseline Brier at launch
   - Recommendation: Default threshold of 0.05 (5 percentage points absolute increase from 30-day baseline); make this tunable via Settings

3. **Kalshi GET /portfolio/settlements `min_ts` parameter type**
   - What we know: API docs show `min_ts` and `max_ts` as available filters
   - What's unclear: Whether `min_ts` is Unix epoch integer (seconds), milliseconds, or nanoseconds
   - Recommendation: Follow existing client pattern of using ISO timestamps where available; test with a small `limit=1` call to confirm format; treat as Unix epoch seconds (standard Kalshi convention) per the `settled_time` ISO 8601 response field

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 1.x |
| Config file | pyproject.toml (`asyncio_mode = "auto"`, `testpaths = ["tests"]`) |
| Quick run command | `pytest tests/performance/ -x -q` |
| Full suite command | `pytest tests/ -x -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PERF-01 | Brier score computed correctly from resolved trades | unit | `pytest tests/performance/test_metrics.py::test_brier_score -x` | ❌ Wave 0 |
| PERF-02 | Sharpe ratio computed with sqrt(252) annualization | unit | `pytest tests/performance/test_metrics.py::test_sharpe_ratio -x` | ❌ Wave 0 |
| PERF-03 | Win rate and profit factor computed correctly | unit | `pytest tests/performance/test_metrics.py::test_win_rate_profit_factor -x` | ❌ Wave 0 |
| PERF-04 | Losing trade classified by correct error type | unit | `pytest tests/performance/test_loss_classifier.py -x` | ❌ Wave 0 |
| PERF-05 | Retraining runs and produces new model version | unit | `pytest tests/performance/test_learning_loop.py::test_retrain_produces_new_version -x` | ❌ Wave 0 |
| PERF-06 | Retraining triggered when rolling Brier degrades past threshold | unit | `pytest tests/performance/test_learning_loop.py::test_brier_degradation_trigger -x` | ❌ Wave 0 |
| PERF-07 | Backtester runs over historical date range and produces metrics | unit | `pytest tests/performance/test_backtester.py -x` | ❌ Wave 0 |
| PERF-08 | Backtest calls ProbabilityPipeline and KellySizer (same code paths) | unit | `pytest tests/performance/test_backtester.py::test_same_code_paths -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/performance/ -x -q`
- **Per wave merge:** `pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/performance/__init__.py` — package init
- [ ] `tests/performance/test_metrics.py` — covers PERF-01, PERF-02, PERF-03
- [ ] `tests/performance/test_loss_classifier.py` — covers PERF-04
- [ ] `tests/performance/test_learning_loop.py` — covers PERF-05, PERF-06
- [ ] `tests/performance/test_backtester.py` — covers PERF-07, PERF-08
- [ ] Framework install: `uv add apscheduler` — APScheduler not yet in pyproject.toml

---

## Sources

### Primary (HIGH confidence)
- Kalshi official API docs: https://docs.kalshi.com/api-reference/portfolio/get-settlements — verified settlement response fields (`market_result`, `settled_time`, `ticker`, `revenue`, cursor pagination)
- Kalshi official API docs: https://docs.kalshi.com/api-reference/market/get-market — verified `status` values (`determined`, `finalized`), `result` field values (`yes`, `no`, `scalar`, void)
- Existing codebase: `src/pmtb/prediction/xgboost_model.py` — `XGBoostPredictor.train()` signature, brier_score_loss usage, joblib save/load
- Existing codebase: `src/pmtb/db/models.py` — `PerformanceMetric`, `Trade`, `ModelOutput` field definitions
- Existing codebase: `src/pmtb/config.py` — Settings pattern for new config fields
- Existing codebase: `src/pmtb/orchestrator.py` — asyncio.gather pattern for integrating new async tasks
- sklearn docs (via codebase usage): `brier_score_loss` from `sklearn.metrics`, `CalibratedClassifierCV.fit(sample_weight=...)`

### Secondary (MEDIUM confidence)
- APScheduler docs: https://apscheduler.readthedocs.io/en/3.x — AsyncIOScheduler, IntervalTrigger, max_instances
- QuantStart: Annualized Sharpe ratio formula `(mean/std) * sqrt(252)` — standard quant convention, multiple authoritative sources agree

### Tertiary (LOW confidence)
- Kalshi `min_ts` parameter type (Unix epoch seconds) — inferred from API convention; should be validated in Wave 0 test against live demo API

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all core dependencies already installed; only APScheduler is new
- Architecture: HIGH — patterns follow established codebase conventions (async session factory, Pydantic models, Prometheus metrics, Loguru); Kalshi settlement API verified against official docs
- Pitfalls: HIGH — temporal integrity, CalibratedClassifierCV sample_weight, and pnl=None guards are verified against codebase and official sources
- Backtesting temporal integrity: HIGH — BacktestDataSource protocol approach is clean and well-understood
- `min_ts` parameter format: LOW — needs validation against live API

**Research date:** 2026-03-11
**Valid until:** 2026-04-11 (stable APIs; APScheduler 3.x is mature)
