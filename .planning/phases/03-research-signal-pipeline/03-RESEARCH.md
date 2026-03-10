# Phase 3: Research Signal Pipeline - Research

**Researched:** 2026-03-10
**Domain:** Async research agents, NLP sentiment analysis, Reddit/RSS/Google Trends APIs, Claude LLM escalation
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Source Prioritization**
- Launch with 3 active sources: Reddit, RSS news feeds, Google Trends
- Twitter/X agent stubbed out with full interface (returns empty/no-op) — swap in real implementation later when API cost is justified
- Pipeline always expects all 4 source slots; stub sources return gracefully with no signals

**Reddit Strategy**
- Dual approach: category-mapped subreddits (e.g. politics → r/politics, economics → r/economics) + Reddit search API for broader discovery
- Check curated subreddits first for targeted signal, then run broader search for supplementary results

**RSS Feed Configuration**
- Claude selects sensible default feeds per market category (AP, Reuters, Bloomberg, etc.)
- All feed URLs stored in YAML config (follows Phase 1 pydantic-settings pattern) — user can override or extend without code changes

**Google Trends Signal**
- Use both interest-over-time (quantitative: rising/falling search interest) and related queries (qualitative: stored in raw_data)
- Interest-over-time is the primary sentiment signal; related queries available for Claude if signal is escalated

**NLP/Sentiment Approach**
- Hybrid: VADER for clear cases, Claude API for ambiguous signals
- VADER compound score threshold for Claude escalation: Claude's discretion (configurable in YAML)
- Skip topic classification entirely — use MarketCandidate's existing category field (redundant to re-classify)
- When Claude classifies a signal, it returns structured JSON with sentiment, confidence, and a 1-2 sentence reasoning string
- Reasoning stored in Signal.raw_data for debugging losing trades in Phase 7

**Query Construction**
- Hybrid: template-based keyword extraction for common market patterns + Claude LLM fallback for markets where templates don't match
- TTL-based query cache — generated queries cached per ticker, expire after configurable TTL. Saves Claude API cost on recurring scan cycles
- Result depth per source: Claude's discretion (configurable in YAML settings)

**Signal Aggregation**
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

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| RSRCH-01 | Research agents gather sentiment signals from Twitter/X API for each candidate market | Twitter/X agent is stubbed — interface defined, implementation is no-op; stub follows same Protocol as active agents |
| RSRCH-02 | Research agents gather sentiment signals from Reddit API for each candidate market | asyncpraw library; read-only OAuth script app; dual subreddit + search strategy |
| RSRCH-03 | Research agents gather relevant articles from RSS news feeds for each candidate market | httpx fetches feeds async; feedparser parses XML/Atom offline; YAML-configured feed URLs |
| RSRCH-04 | Research agents gather search interest data from Google Trends for each candidate market | pytrends or pytrends-modern; interest_over_time() as primary signal; related_queries() in raw_data |
| RSRCH-05 | All four research sources run in parallel using asyncio | asyncio.gather(return_exceptions=True) with asyncio.timeout() per source; same pattern as scanner enrichment |
| RSRCH-06 | NLP sentiment analysis classifies each signal as bullish/bearish/neutral with confidence score | VADER compound score for clear cases; Claude API (AsyncAnthropic) for ambiguous cases above escalation threshold |
| RSRCH-07 | Topic classification maps signals to relevant market categories | Satisfied by re-using MarketCandidate.category — no re-classification needed per locked decision |
| RSRCH-08 | Research pipeline continues with available signals if one source times out or fails | asyncio.gather(return_exceptions=True) + asyncio.timeout() per agent; exceptions logged, None returned for failed sources |
| RSRCH-09 | Research signals are persisted to PostgreSQL with timestamps for later analysis | Write Signal rows via existing SQLAlchemy async session pattern; index on (market_id, source, created_at) already exists |
</phase_requirements>

---

## Summary

Phase 3 builds a four-agent research pipeline that fires in parallel for each `MarketCandidate`. Three agents are active (Reddit, RSS, Google Trends); one is a typed stub (Twitter/X). The core async pattern is `asyncio.gather(return_exceptions=True)` wrapped with per-agent `asyncio.timeout()` — identical to how the scanner already handles concurrent enrichment. Failures from any single agent log the exception and return `None`; the pipeline continues with whatever signals are available.

NLP sentiment classification uses a hybrid approach: VADER (via `vaderSentiment`) handles clear signals instantly with no API cost. When VADER's compound score falls in the ambiguous band (configurable threshold, Claude's discretion), the text is escalated to Claude via the `AsyncAnthropic` client. Claude returns a structured Pydantic model: `{sentiment, confidence, reasoning}`. This reasoning string is stored in `Signal.raw_data` for Phase 7 losing-trade debugging.

Individual signals write directly to the existing `Signal` DB model (source, sentiment, confidence, raw_data, cycle_id). The pipeline then computes a `SignalBundle` Pydantic model per market per cycle, with per-source summaries and a `.to_features()` method that outputs a flat numeric dict for Phase 4's XGBoost. Missing sources are `None` in the bundle — absence of data is not represented as neutral sentiment.

**Primary recommendation:** Build each research agent as a class implementing a shared `ResearchAgent` Protocol. Use `asyncio.gather(return_exceptions=True)` at the pipeline orchestrator layer with `asyncio.timeout()` wrapping each agent coroutine. Add `vaderSentiment` and `anthropic` to dependencies; use `asyncpraw` for Reddit and `feedparser` (with `httpx` for fetching) for RSS.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| asyncpraw | 7.8.x | Reddit API async client | Official async wrapper for PRAW; native asyncio support; context-manager session lifecycle |
| vaderSentiment | 3.3.x | Rule-based sentiment scoring | No training data needed; optimized for social media text; instant, zero-cost classification |
| feedparser | 6.0.x | RSS/Atom/RDF feed parsing | Industry standard, 15+ years; handles malformed feeds gracefully; parses all major formats |
| anthropic | 0.40+ | Claude API async client | Official SDK; `AsyncAnthropic` client; used for NLP escalation and query generation |
| pytrends | 4.9.x | Google Trends pseudo-API | Most widely used; interest_over_time() and related_queries() both available |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tenacity | 8.x | Retry decorator for HTTP calls | Already in project; use for Reddit and RSS HTTP retries (same pattern as kalshi_retry) |
| pyyaml | 6.x | YAML config for feed URLs | Already in project via pydantic-settings[yaml]; feeds and subreddit mappings live in config.yaml |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| asyncpraw | praw (sync) + asyncio.to_thread | asyncpraw is purpose-built for async; no thread overhead |
| vaderSentiment | transformers (BERT-based) | VADER is instant, no GPU/download; BERT would be overkill for initial classification |
| feedparser + httpx | aiohttp-feedparser | feedparser is more mature; httpx is already in project stack |
| pytrends | SerpAPI Google Trends endpoint | pytrends is free; SerpAPI charges per request |
| pytrends | pytrends-modern | pytrends-modern is newer but smaller ecosystem; pytrends 4.9.x has broader documentation |

**Installation:**
```bash
uv add asyncpraw vaderSentiment feedparser anthropic pytrends
```

---

## Architecture Patterns

### Recommended Project Structure
```
src/pmtb/research/
├── __init__.py
├── agent.py          # ResearchAgent Protocol definition
├── models.py         # SignalBundle, SourceSummary, SignalClassification Pydantic models
├── pipeline.py       # ResearchPipeline orchestrator — gather() all agents, write to DB
├── sentiment.py      # VADER + Claude hybrid classifier
├── query.py          # Query constructor with template + Claude fallback + TTL cache
├── agents/
│   ├── __init__.py
│   ├── reddit.py     # RedditAgent — asyncpraw, dual subreddit+search strategy
│   ├── rss.py        # RSSAgent — httpx fetch + feedparser parse
│   ├── trends.py     # TrendsAgent — pytrends interest_over_time + related_queries
│   └── twitter.py    # TwitterAgent — stub, full interface, no-op implementation
```

### Pattern 1: ResearchAgent Protocol
**What:** All four agents implement a common Protocol so the pipeline is agent-agnostic. Stub and real agents are drop-in interchangeable.
**When to use:** Always — this is the only agent contract.
**Example:**
```python
# Source: established pattern from src/pmtb/executor.py (runtime_checkable Protocol)
from typing import Protocol, runtime_checkable
from pmtb.scanner.models import MarketCandidate
from pmtb.research.models import SourceResult

@runtime_checkable
class ResearchAgent(Protocol):
    source_name: str

    async def fetch(self, candidate: MarketCandidate, query: str) -> SourceResult:
        """Fetch raw signals for a market candidate. Returns SourceResult or raises."""
        ...
```

### Pattern 2: Parallel Gather with Return Exceptions
**What:** All four agent `fetch()` coroutines run concurrently via `asyncio.gather(return_exceptions=True)`. Each is individually wrapped in `asyncio.timeout()` before gather. Failed agents log the exception and contribute `None` to the bundle.
**When to use:** This is the only parallel execution strategy — required for RSRCH-05 and RSRCH-08.
**Example:**
```python
# Source: Python asyncio docs — gather + return_exceptions pattern
# Mirrors scanner._enrich() pattern in src/pmtb/scanner/scanner.py

import asyncio
from loguru import logger

async def _run_agent_safe(
    agent: ResearchAgent,
    candidate: MarketCandidate,
    query: str,
    timeout_seconds: float,
) -> SourceResult | None:
    try:
        async with asyncio.timeout(timeout_seconds):
            return await agent.fetch(candidate, query)
    except TimeoutError:
        logger.bind(source=agent.source_name, ticker=candidate.ticker).warning(
            "Research agent timed out"
        )
        return None
    except Exception as exc:
        logger.bind(source=agent.source_name, ticker=candidate.ticker).exception(
            "Research agent failed", error=str(exc)
        )
        return None

# In ResearchPipeline.run():
results = await asyncio.gather(
    _run_agent_safe(reddit_agent, candidate, query, timeout),
    _run_agent_safe(rss_agent, candidate, query, timeout),
    _run_agent_safe(trends_agent, candidate, query, timeout),
    _run_agent_safe(twitter_agent, candidate, query, timeout),  # always returns None stub
)
```

### Pattern 3: VADER + Claude Hybrid Sentiment Classification
**What:** VADER runs first (synchronous, instant). If the compound score is in the ambiguous band (e.g., -0.3 to +0.3, configurable), escalate to Claude for structured classification with reasoning.
**When to use:** For every piece of text content — Reddit posts/comments, RSS article titles/summaries.
**Example:**
```python
# Source: vaderSentiment PyPI docs; anthropic SDK README
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from anthropic import AsyncAnthropic
from pmtb.research.models import SignalClassification

_analyzer = SentimentIntensityAnalyzer()

async def classify(
    text: str,
    escalation_threshold: float,
    claude_client: AsyncAnthropic,
    model: str = "claude-3-5-haiku-latest",
) -> SignalClassification:
    scores = _analyzer.polarity_scores(text)
    compound = scores["compound"]

    if compound >= escalation_threshold:
        return SignalClassification(sentiment="bullish", confidence=compound, reasoning=None)
    elif compound <= -escalation_threshold:
        return SignalClassification(sentiment="bearish", confidence=abs(compound), reasoning=None)
    else:
        # Escalate to Claude
        response = await claude_client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": (
                    f"Classify this text as bullish, bearish, or neutral for a prediction market. "
                    f"Return JSON: {{\"sentiment\": \"bullish|bearish|neutral\", "
                    f"\"confidence\": 0.0-1.0, \"reasoning\": \"1-2 sentences\"}}.\n\nText: {text}"
                )
            }]
        )
        # Parse Claude's JSON response
        import json
        data = json.loads(response.content[0].text)
        return SignalClassification(**data)
```

### Pattern 4: TTL Query Cache
**What:** Generated search queries are cached per ticker in a dict with expiry timestamps. On cache hit within TTL, the cached query is returned immediately — no VADER/Claude cost.
**When to use:** Query construction for every market candidate.
**Example:**
```python
# Source: standard Python pattern
from datetime import datetime, UTC
from dataclasses import dataclass, field

@dataclass
class _CacheEntry:
    query: str
    expires_at: datetime

class QueryCache:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, _CacheEntry] = {}

    def get(self, ticker: str) -> str | None:
        entry = self._store.get(ticker)
        if entry and datetime.now(UTC) < entry.expires_at:
            return entry.query
        return None

    def set(self, ticker: str, query: str) -> None:
        from datetime import timedelta
        self._store[ticker] = _CacheEntry(
            query=query,
            expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl),
        )
```

### Pattern 5: asyncpraw Read-Only Session
**What:** asyncpraw is used in read-only "script" OAuth mode. The Reddit instance is created per-cycle or shared as a class attribute with `async with` context management.
**When to use:** All Reddit API calls.
**Example:**
```python
# Source: asyncpraw docs — quick_start.rst
import asyncpraw

async def _make_reddit(settings) -> asyncpraw.Reddit:
    return asyncpraw.Reddit(
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
        # Read-only mode — no username/password needed
    )

# Usage in RedditAgent.fetch():
async with asyncpraw.Reddit(...) as reddit:
    subreddit = await reddit.subreddit("politics")
    async for post in subreddit.hot(limit=10):
        ...
```

### Pattern 6: RSS Fetch with httpx + feedparser Parse
**What:** httpx.AsyncClient fetches the raw feed bytes; feedparser parses them synchronously. feedparser has no network dependency when given pre-fetched content.
**When to use:** All RSS feed fetches.
**Example:**
```python
# Source: feedparser PyPI docs + httpx project patterns
import feedparser
import httpx

async def _fetch_feed(url: str, client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(url, follow_redirects=True, timeout=10.0)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    return [
        {"title": e.get("title", ""), "summary": e.get("summary", "")}
        for e in parsed.entries
    ]
```

### Anti-Patterns to Avoid
- **Calling `asyncio.gather()` without `return_exceptions=True` or timeout wrappers:** One failing agent will raise an exception that cancels the entire gather and halts the pipeline. Use `_run_agent_safe()` wrapper consistently.
- **Storing Claude's raw text response directly:** Always parse and validate into a Pydantic model before writing to DB. Raw text in `Signal.raw_data` is fine, but `sentiment` and `confidence` fields must be typed.
- **Representing timed-out sources as neutral sentiment:** The locked decision is explicit — `None` in SignalBundle means no data, not neutral. Do not substitute a default 0.5 confidence or "neutral" sentinel.
- **Sharing a single `asyncpraw.Reddit` instance across concurrent coroutines without care:** asyncpraw is asyncio-safe but Reddit instances should be closed properly. Use context manager.
- **Calling `pytrends` in a tight loop without delay:** Google Trends blocks scrapers with 429s. Apply a small backoff between requests; wrap with tenacity retry.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Reddit API pagination + OAuth | Custom Reddit HTTP client | asyncpraw | Token refresh, rate limit headers, PRAW's pagination abstraction |
| RSS feed XML parsing | xml.etree.ElementTree feed parser | feedparser | Handles malformed XML, encoding detection, Atom/RSS/RDF dialects, 15 years of edge cases |
| Sentiment scoring | Keyword counting / regex rules | vaderSentiment | VADER's 7,500-word lexicon with modifier rules; outperforms human raters on tweets (0.96 vs 0.84 F1) |
| LLM JSON extraction | String splitting / regex on Claude output | Parse `response.content[0].text` with `json.loads()` after prompting for JSON | Claude reliably emits valid JSON when instructed; structured outputs beta (`.parse()`) also available |
| Google Trends data | Direct HTTP to trends.google.com | pytrends | Google's unofficial API is reverse-engineered and version-locked; pytrends maintains compatibility |
| Async timeout per agent | Manual `asyncio.wait_for()` nesting | `asyncio.timeout()` context manager (Python 3.11+) | Cleaner API, no nesting; project is on Python 3.13 |

**Key insight:** Every external API in this phase has a maintained Python wrapper. The project's value is in the orchestration, sentiment routing logic, and SignalBundle contract — not in re-implementing API clients.

---

## Common Pitfalls

### Pitfall 1: pytrends 429 / TooManyRequestsError
**What goes wrong:** Google silently rate-limits pytrends requests with HTTP 429 or returns empty DataFrames. No clear error is raised in some versions.
**Why it happens:** Google Trends is a scraping target, not a real API. They throttle aggressively.
**How to avoid:** Wrap pytrends calls with tenacity retry using exponential backoff. Check that `interest_over_time()` returns a non-empty DataFrame before processing. Add a 1–2 second sleep between requests across markets.
**Warning signs:** Empty DataFrame returned, `ResponseError` raised from pytrends internals.

### Pitfall 2: asyncpraw Session Not Closed
**What goes wrong:** asyncpraw Reddit sessions are not closed → resource warnings, potential connection pool exhaustion.
**Why it happens:** asyncpraw requires explicit `await reddit.close()` or use of `async with asyncpraw.Reddit(...) as reddit:` context manager.
**How to avoid:** Always use `async with` for asyncpraw.Reddit in agent `fetch()` methods. Do not store the Reddit instance as a long-lived class attribute without managing its lifecycle.
**Warning signs:** `ResourceWarning: Unclosed client session` in test output.

### Pitfall 3: VADER Compound Score Misread as Confidence
**What goes wrong:** `compound` ranges from -1 to +1, not 0 to 1. Storing it directly as `confidence` (which should be 0–1) produces negative confidence values.
**Why it happens:** VADER returns four keys: `neg`, `neu`, `pos` (all 0–1) and `compound` (-1 to +1).
**How to avoid:** Use `abs(compound)` as the confidence value. Store `compound` in `Signal.raw_data` for debugging. Map to sentiment with threshold logic (≥ threshold → bullish, ≤ -threshold → bearish).
**Warning signs:** `Signal.confidence` values less than 0 in the database.

### Pitfall 4: feedparser Blocking the Event Loop
**What goes wrong:** `feedparser.parse(url)` with a URL argument makes a blocking HTTP call inside the event loop, halting all concurrent coroutines.
**Why it happens:** feedparser's built-in HTTP fetching is synchronous (uses `urllib`).
**How to avoid:** Always fetch feed content with `httpx.AsyncClient` first, then pass the response text to `feedparser.parse(text)`. Never pass a URL string directly to feedparser in async code.
**Warning signs:** All RSS fetches appear sequential; event loop has noticeable pauses.

### Pitfall 5: Reddit API Pre-Approval Requirement (2024+)
**What goes wrong:** Reddit API credentials cannot be self-service registered for new apps; Reddit requires submission and approval for API access.
**Why it happens:** Reddit changed their API access policy in 2024 — removed self-service access entirely.
**How to avoid:** Register the Reddit app early in development. Document required credentials in `.env.example`. Plan for the agent to gracefully degrade if credentials are absent (same pattern as Twitter/X stub).
**Warning signs:** 403 or 401 errors during RedditAgent initialization.

### Pitfall 6: Claude API Cost on Every Signal
**What goes wrong:** Without the VADER pre-filter, every piece of text triggers a Claude API call, making research prohibitively expensive at scale.
**Why it happens:** Escalation threshold not set, or threshold set too broadly.
**How to avoid:** VADER handles the clear cases (which are the majority of social media text). Only escalate text where VADER's compound is in the ambiguous band. Log escalation rate as a Prometheus metric to detect regressions.
**Warning signs:** Claude API cost spikes; escalation rate > 30% of processed texts.

---

## Code Examples

Verified patterns from official sources and existing project code:

### SignalBundle Pydantic Model with to_features()
```python
# Pattern follows existing scanner/models.py Pydantic convention
from __future__ import annotations
from pydantic import BaseModel

class SourceSummary(BaseModel):
    sentiment: str | None       # "bullish" | "bearish" | "neutral" | None
    confidence: float | None    # 0.0–1.0 | None if source failed/timed out
    signal_count: int           # number of individual signals aggregated

class SignalBundle(BaseModel):
    ticker: str
    cycle_id: str
    reddit: SourceSummary | None = None
    rss: SourceSummary | None = None
    trends: SourceSummary | None = None
    twitter: SourceSummary | None = None   # Always None in Phase 3 (stubbed)

    def to_features(self) -> dict[str, float]:
        """
        Produce a flat numeric feature dict for XGBoost consumption.
        Missing sources produce NaN sentinels (float("nan")) — the caller handles.
        """
        _NAN = float("nan")
        def _sentiment_score(s: SourceSummary | None) -> float:
            if s is None or s.sentiment is None:
                return _NAN
            return {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}.get(s.sentiment, _NAN)

        def _confidence(s: SourceSummary | None) -> float:
            return s.confidence if s and s.confidence is not None else _NAN

        return {
            "reddit_sentiment": _sentiment_score(self.reddit),
            "reddit_confidence": _confidence(self.reddit),
            "rss_sentiment": _sentiment_score(self.rss),
            "rss_confidence": _confidence(self.rss),
            "trends_sentiment": _sentiment_score(self.trends),
            "trends_confidence": _confidence(self.trends),
            "twitter_sentiment": _sentiment_score(self.twitter),
            "twitter_confidence": _confidence(self.twitter),
        }
```

### Signal DB Write Pattern
```python
# Mirrors scanner._upsert_markets() pattern — async session, explicit commit
# Source: src/pmtb/scanner/scanner.py + src/pmtb/db/session.py
from pmtb.db.models import Signal
from pmtb.db.session import get_session

async def _persist_signal(
    market_id,
    source: str,
    sentiment: str,
    confidence: float,
    raw_data: dict,
    cycle_id: str,
    session_factory=None,
) -> None:
    async with get_session(session_factory) as session:
        signal = Signal(
            market_id=market_id,
            source=source,
            sentiment=sentiment,
            confidence=confidence,
            raw_data=raw_data,
            cycle_id=cycle_id,
        )
        session.add(signal)
        await session.commit()
```

### VADER Classification Thresholds
```python
# Source: VADER documentation — standard thresholds from research paper
# compound >= 0.05 → positive; compound <= -0.05 → negative; otherwise neutral
# For escalation: use a configurable wider band (e.g., 0.3) to catch ambiguous cases

VADER_THRESHOLDS = {
    "bullish": 0.05,    # compound >= this → bullish without Claude
    "bearish": -0.05,   # compound <= this → bearish without Claude
    "escalation_band": 0.3,  # abs(compound) < this → escalate to Claude (Claude's discretion)
}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Twitter API free tier | Twitter/X API now costs $100–$42,000/month for research access | 2023 | Stub for Phase 3; real implementation deferred |
| asyncio.wait_for() for timeouts | asyncio.timeout() context manager | Python 3.11 (2022) | Cleaner code; project on Python 3.13 should use this |
| Reddit free API | Reddit API requires pre-approval + rate limit is 100 req/min for OAuth | 2024 | Credentials must be pre-registered; graceful degradation if absent |
| Claude text response parsing | `client.beta.messages.parse()` with Pydantic model (Structured Outputs beta) | Nov 2025 | Optional upgrade path; `json.loads()` on prompt-instructed JSON is sufficient and stable for Phase 3 |
| pytrends synchronous | pytrends is synchronous; must be wrapped or run in thread | Ongoing | Use `asyncio.to_thread(trends_client.interest_over_time)` in async context |

**Deprecated/outdated:**
- `asyncio.wait_for()` for per-task timeouts: replaced by `asyncio.timeout()` context manager in Python 3.11+
- Passing URL strings to `feedparser.parse()`: blocks event loop; always pre-fetch with httpx
- PRAW (synchronous) in async contexts: use asyncpraw instead

---

## Open Questions

1. **pytrends blocking — asyncio.to_thread vs pytrends-modern**
   - What we know: pytrends 4.9.x is synchronous; calling it directly blocks the event loop. pytrends-modern claims async support but is a smaller project.
   - What's unclear: Whether pytrends-modern's async implementation is stable and feature-complete for `interest_over_time()` and `related_queries()`.
   - Recommendation: Use `asyncio.to_thread(pytrends_fn)` with synchronous pytrends. This is safe, predictable, and avoids dependency on a low-activity fork. Planner should bake this in.

2. **Reddit credential availability**
   - What we know: Reddit now requires pre-approval for new API apps (2024). Credentials may not be immediately available.
   - What's unclear: Whether the project owner already has Reddit API credentials.
   - Recommendation: Design RedditAgent to gracefully return an empty SourceResult (not an exception) if `reddit_client_id` is absent from settings. Add it to `.env.example`. Planner should add this to Wave 0 setup notes.

3. **Claude API key in research settings**
   - What we know: `anthropic` SDK requires `ANTHROPIC_API_KEY`. Not currently in `Settings` class.
   - What's unclear: Whether to add it as a required or optional field (with fallback to VADER-only mode).
   - Recommendation: Add `anthropic_api_key: str | None = None` to Settings. When None, sentiment classifier runs VADER-only mode. This enables the pipeline to function without a Claude API key during testing.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio (asyncio_mode = "auto") |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/research/ -x -q` |
| Full suite command | `pytest tests/ -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| RSRCH-01 | Twitter stub returns empty SourceResult, no exceptions | unit | `pytest tests/research/test_agents.py::test_twitter_stub -x` | Wave 0 |
| RSRCH-02 | RedditAgent fetches posts from mapped subreddit and search, returns SourceResult | unit (AsyncMock asyncpraw) | `pytest tests/research/test_agents.py::test_reddit_agent -x` | Wave 0 |
| RSRCH-03 | RSSAgent fetches feed via httpx, parses entries, returns SourceResult | unit (httpx mock) | `pytest tests/research/test_agents.py::test_rss_agent -x` | Wave 0 |
| RSRCH-04 | TrendsAgent calls interest_over_time, returns momentum signal | unit (mock pytrends) | `pytest tests/research/test_agents.py::test_trends_agent -x` | Wave 0 |
| RSRCH-05 | Pipeline runs all 4 agents concurrently and completes faster than sequential | integration/timing | `pytest tests/research/test_pipeline.py::test_parallel_execution -x` | Wave 0 |
| RSRCH-06 | VADER classifies clear bullish/bearish text without Claude call; ambiguous text escalates | unit | `pytest tests/research/test_sentiment.py -x` | Wave 0 |
| RSRCH-07 | SignalBundle.ticker/category matches input MarketCandidate | unit | `pytest tests/research/test_models.py::test_signal_bundle_category -x` | Wave 0 |
| RSRCH-08 | Pipeline continues and returns partial SignalBundle when one agent raises or times out | unit | `pytest tests/research/test_pipeline.py::test_graceful_degradation -x` | Wave 0 |
| RSRCH-09 | Signal rows appear in DB after pipeline run; queryable by market_id + cycle_id | integration (mock session) | `pytest tests/research/test_pipeline.py::test_signal_persistence -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/research/ -x -q`
- **Per wave merge:** `pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/research/__init__.py` — package init
- [ ] `tests/research/test_agents.py` — covers RSRCH-01 through RSRCH-04
- [ ] `tests/research/test_pipeline.py` — covers RSRCH-05, RSRCH-08, RSRCH-09
- [ ] `tests/research/test_sentiment.py` — covers RSRCH-06
- [ ] `tests/research/test_models.py` — covers RSRCH-07; SignalBundle, to_features(), SourceSummary validation

---

## Sources

### Primary (HIGH confidence)
- asyncpraw GitHub (praw-dev/asyncpraw) — authentication, context manager session, read-only mode
- asyncpraw readthedocs (v7.8.1) — quick_start, subreddit hot/search, limit parameter
- vaderSentiment (cjhutto/vaderSentiment via PyPI) — compound score thresholds, polarity_scores() API
- anthropic SDK GitHub (anthropics/anthropic-sdk-python) — AsyncAnthropic usage, messages.create()
- feedparser PyPI (kurtmckee/feedparser) — parse(text) API, supported formats
- Python asyncio docs — asyncio.gather(return_exceptions=True), asyncio.timeout() (3.11+)
- Existing project code (src/pmtb/scanner/scanner.py) — asyncio.gather pattern, Semaphore, Loguru .bind()
- Existing project code (src/pmtb/db/models.py) — Signal model fields confirmed
- Existing project code (src/pmtb/config.py) — Settings pydantic-settings pattern for new config fields

### Secondary (MEDIUM confidence)
- pytrends PyPI (GeneralMills/pytrends) — interest_over_time(), related_queries(), build_payload() (synchronous; asyncio.to_thread wrapper needed)
- Reddit API rate limit sources (data365.co, postiz.com) — 100 req/min OAuth, pre-approval requirement
- vaderSentiment best practices (GeeksforGeeks, quantinsti.com) — threshold guidance (±0.05 standard)

### Tertiary (LOW confidence)
- pytrends-modern (yiromo/pytrends-modern) — claimed async support; not verified with Context7; recommend asyncio.to_thread with pytrends instead
- pytrends 429 behavior — community reports of throttling; no official documentation; treat as LOW confidence, mitigate with retry + sleep

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — asyncpraw, vaderSentiment, feedparser, anthropic are well-documented with official sources; pytrends synchronous behavior confirmed
- Architecture: HIGH — patterns derived directly from existing project code (scanner.py asyncio.gather, Protocol from executor.py, Settings pattern)
- Pitfalls: MEDIUM-HIGH — asyncpraw session lifecycle and feedparser blocking confirmed from official docs; pytrends 429 behavior is community-reported (MEDIUM)

**Research date:** 2026-03-10
**Valid until:** 2026-04-10 (stable ecosystem; Reddit API policy could change)
