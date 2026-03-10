# Phase 2: Market Scanner - Context

**Gathered:** 2026-03-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Scan all Kalshi markets, filter candidates by liquidity, volume, spread, time-to-resolution, and volatility, and output typed MarketCandidate objects for downstream pipeline stages. No research, no prediction, no trading logic — just discovery and filtering.

</domain>

<decisions>
## Implementation Decisions

### Scan Scheduling
- Fixed interval async loop with configurable sleep — default 5 minutes
- Runs 24/7 as part of the autonomous pipeline
- All discovered markets persisted to DB (upsert on ticker) — not just candidates
- Per-market rejection reason logged at DEBUG level for threshold tuning

### Filter Design
- Hard gate filters applied sequentially — market must pass ALL filters
- No scoring or soft ranking at the filter stage
- Filters: liquidity, volume, spread, time-to-resolution, volatility
- Time-to-resolution window: exclude markets resolving within 1 hour or beyond 30 days
- Liquidity, volume, and spread thresholds: Claude researches Kalshi market distributions and sets sensible defaults. All values configurable via YAML (follows Phase 1 pydantic-settings pattern)

### Volatility Measurement
- Price movement over time: track yes_price per market each scan cycle, compute standard deviation from rolling history
- Rolling history built from accumulated scan snapshots — no extra API call for trade history
- Volatility filter skipped during warmup period (until ~6+ snapshots accumulated) — other filters apply immediately

### Market Data Enrichment
- After filtering, fetch orderbook snapshot for each passing candidate (bid/ask, depth)
- Fetch event-level context for each candidate (parent event, related markets)
- Enrichment happens only for candidates that pass all filters — not for every market

### Candidate Output Shape
- MarketCandidate is a Pydantic model: ticker, title, category, event_context, close_time, yes_bid, yes_ask, implied_probability (mid-price), spread, volume_24h, volatility_score
- Scanner returns a ScanResult wrapper: list of MarketCandidates + metadata (total_markets, per-filter rejection counts, scan_duration, cycle_id)
- Candidates sorted by implied edge potential (distance from 50% implied probability — markets near 50/50 ranked higher)

### Claude's Discretion
- Exact default threshold values for liquidity, volume, spread (after researching Kalshi distributions)
- Number of orderbook depth levels to fetch
- Volatility warmup threshold (suggested ~6 snapshots but flexible)
- Price snapshot storage mechanism (in-memory rolling window vs. DB table)
- Exact sorting heuristic for edge potential ranking

</decisions>

<specifics>
## Specific Ideas

- Scanner should upsert all markets to the existing `markets` DB table (Phase 1 model already has ticker, title, category, status, close_time)
- MarketCandidate Pydantic model is a pipeline contract — downstream phases (research, prediction, decision) import and consume it directly
- ScanResult metadata enables monitoring: if candidate count drops to zero, something is wrong with thresholds or Kalshi API

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `KalshiClient.get_markets(**kwargs)`: Already supports pagination params, returns list of market dicts. Scanner builds on this.
- `KalshiClient.get_market(ticker)`: Single market fetch — useful for enrichment
- `Market` DB model (src/pmtb/db/models.py): Has ticker, title, category, status, close_time — scanner upserts here
- `Settings` class (src/pmtb/config.py): Pydantic-settings with env + YAML — scanner thresholds follow this pattern
- `cycle_id` correlation (src/pmtb/logging_.py): Each scan cycle gets a unique ID for end-to-end tracing

### Established Patterns
- httpx.AsyncClient for all Kalshi API calls (not kalshi-python-async SDK)
- `@kalshi_retry` decorator for automatic retry on 429/5xx
- Loguru structured logging with `.bind()` for contextual fields
- Prometheus metrics via `API_CALLS` counter

### Integration Points
- Scanner imports `KalshiClient` from `pmtb.kalshi.client`
- Scanner imports `AsyncSession` from `pmtb.db.session` for market upserts
- Scanner config fields added to `Settings` class
- MarketCandidate becomes a shared type imported by Phase 3 (research) and Phase 4 (prediction)

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-market-scanner*
*Context gathered: 2026-03-10*
