# Phase 3: Research Signal Pipeline - Context

**Gathered:** 2026-03-10
**Status:** Ready for planning

<domain>
## Phase Boundary

For each MarketCandidate from the scanner, run research agents in parallel across multiple data sources, classify sentiment with NLP, and persist signals to PostgreSQL. Output a typed SignalBundle for downstream probability modeling. Graceful degradation when any source fails or times out. No prediction, no trading logic — just signal collection and classification.

</domain>

<decisions>
## Implementation Decisions

### Source Prioritization
- Launch with 3 active sources: Reddit, RSS news feeds, Google Trends
- Twitter/X agent stubbed out with full interface (returns empty/no-op) — swap in real implementation later when API cost is justified
- Pipeline always expects all 4 source slots; stub sources return gracefully with no signals

### Reddit Strategy
- Dual approach: category-mapped subreddits (e.g. politics → r/politics, economics → r/economics) + Reddit search API for broader discovery
- Check curated subreddits first for targeted signal, then run broader search for supplementary results

### RSS Feed Configuration
- Claude selects sensible default feeds per market category (AP, Reuters, Bloomberg, etc.)
- All feed URLs stored in YAML config (follows Phase 1 pydantic-settings pattern) — user can override or extend without code changes

### Google Trends Signal
- Use both interest-over-time (quantitative: rising/falling search interest) and related queries (qualitative: stored in raw_data)
- Interest-over-time is the primary sentiment signal; related queries available for Claude if signal is escalated

### NLP/Sentiment Approach
- Hybrid: VADER for clear cases, Claude API for ambiguous signals
- VADER compound score threshold for Claude escalation: Claude's discretion (configurable in YAML)
- Skip topic classification entirely — use MarketCandidate's existing category field (redundant to re-classify)
- When Claude classifies a signal, it returns structured JSON with sentiment, confidence, and a 1-2 sentence reasoning string
- Reasoning stored in Signal.raw_data for debugging losing trades in Phase 7

### Query Construction
- Hybrid: template-based keyword extraction for common market patterns + Claude LLM fallback for markets where templates don't match
- TTL-based query cache — generated queries cached per ticker, expire after configurable TTL. Saves Claude API cost on recurring scan cycles
- Result depth per source: Claude's discretion (configurable in YAML settings)

### Signal Aggregation
- Individual signals stored separately in DB (matches existing Signal model: source, sentiment, confidence, raw_data, cycle_id)
- Also compute a SignalBundle summary per market per cycle for convenient downstream consumption
- Conflict handling: pass-through — SignalBundle includes per-source sentiment without resolving disagreements. Phase 4's XGBoost/Claude handles conflict
- SignalBundle is a structured Pydantic model with per-source summaries + a `.to_features()` method that outputs a flat numeric feature vector for XGBoost
- Failed/timed-out sources marked as None/missing in SignalBundle (not filled with neutral — absence of data ≠ neutral sentiment)

### Claude's Discretion
- VADER compound score threshold for Claude escalation
- Default RSS feeds per market category
- Number of search results to fetch per source per market
- Query cache TTL default
- Reddit subreddit-to-category mapping details
- Template patterns for common market query types

</decisions>

<specifics>
## Specific Ideas

- Twitter/X stub should implement the same agent interface as active sources — when the real implementation is ready, it's a drop-in replacement with no pipeline changes
- SignalBundle.to_features() produces a flat dict like: `{"reddit_sentiment": 0.7, "rss_sentiment": -0.3, "trends_momentum": 1.2, "reddit_confidence": 0.8, ...}` — directly consumable as XGBoost feature columns
- Claude reasoning on escalated signals (e.g. "Fed rate hike language suggests bearish for inflation market") feeds into Phase 7's losing trade analysis

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Signal` DB model (src/pmtb/db/models.py): Already has source, sentiment, confidence, raw_data, cycle_id — research agents write directly to this
- `MarketCandidate` (src/pmtb/scanner/models.py): Input contract — has ticker, title, category, event_context, close_time, implied_probability
- `KalshiClient` (src/pmtb/kalshi/client.py): httpx.AsyncClient pattern — research agents follow this for Reddit/RSS/Trends HTTP calls
- `Settings` class (src/pmtb/config.py): Pydantic-settings with env + YAML — research config fields (thresholds, feed URLs, TTLs) follow this pattern
- `@kalshi_retry` decorator: Retry pattern for HTTP calls — generalize or create similar for research API calls

### Established Patterns
- httpx.AsyncClient for all external HTTP calls
- Loguru structured logging with `.bind()` for contextual fields
- Prometheus metrics via counters (API_CALLS pattern)
- cycle_id correlation for end-to-end tracing
- Pydantic models as pipeline contracts between phases

### Integration Points
- Research pipeline receives `list[MarketCandidate]` from scanner's `ScanResult.candidates`
- Research agents write `Signal` rows to PostgreSQL via async session
- SignalBundle becomes a shared type imported by Phase 4 (probability model)
- cycle_id flows from scanner through research for full tracing

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-research-signal-pipeline*
*Context gathered: 2026-03-10*
