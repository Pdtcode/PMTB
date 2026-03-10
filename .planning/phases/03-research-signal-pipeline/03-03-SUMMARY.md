---
phase: 03-research-signal-pipeline
plan: "03"
subsystem: research-agents
tags: [research, agents, reddit, rss, trends, twitter, sentiment, asyncpraw, feedparser, pytrends]
dependency_graph:
  requires:
    - "03-01"  # ResearchAgent Protocol, SourceResult, MarketCandidate models
    - "03-02"  # SentimentClassifier (VADER + Claude escalation)
  provides:
    - RedditAgent (asyncpraw dual-strategy)
    - RSSAgent (httpx+feedparser)
    - TrendsAgent (pytrends momentum)
    - TwitterAgent (stub, full interface)
  affects:
    - "03-04"  # ResearchPipeline orchestrator will wire these agents together
tech_stack:
  added:
    - asyncpraw==7.8.1
    - feedparser==6.0.12
    - pytrends==4.9.2
    - lxml==6.0.2 (feedparser dependency)
    - pandas==3.0.1 (pytrends dependency)
    - numpy==2.4.3 (pandas dependency)
  patterns:
    - asyncio.to_thread for synchronous pytrends calls
    - tenacity exponential backoff retry for HTTP and pytrends 429s
    - asyncpraw async context manager for Reddit session lifecycle
    - feedparser.parse(text) pattern — never pass URL directly (blocks event loop)
    - Protocol structural typing (isinstance checks without inheritance)
key_files:
  created:
    - src/pmtb/research/agents/__init__.py
    - src/pmtb/research/agents/reddit.py
    - src/pmtb/research/agents/rss.py
    - src/pmtb/research/agents/trends.py
    - src/pmtb/research/agents/twitter.py
    - tests/research/test_agents.py
  modified: []
decisions:
  - "asyncpraw async context manager used for Reddit — ensures session cleanup on error"
  - "feedparser.parse(text) not feedparser.parse(url) — URL path uses urllib.request which blocks event loop"
  - "TrendsAgent derives momentum from last 7 vs prior 7 day avg — simple robust signal without LLM cost"
  - "TwitterAgent stub logs once at __init__ — visible in production logs that Twitter is deferred"
metrics:
  duration: "2 min"
  completed_date: "2026-03-10"
  tasks_completed: 2
  files_created: 6
  tests_added: 17
---

# Phase 03 Plan 03: Research Agents Summary

**One-liner:** Four research agents (Reddit dual-strategy via asyncpraw, RSS via httpx+feedparser, Google Trends momentum via pytrends, Twitter stub) all implementing ResearchAgent Protocol with graceful degradation.

## What Was Built

Four agent classes in `src/pmtb/research/agents/` each implementing the `ResearchAgent` Protocol:

**RedditAgent** (`reddit.py`): Dual-strategy approach — fetches hot posts from category-mapped subreddits (e.g., "politics" → ["politics", "PoliticalDiscussion"]) and also searches r/all for the query string. Uses asyncpraw async context manager. Returns empty SourceResult immediately when `client_id=None`.

**RSSAgent** (`rss.py`): Fetches RSS feeds by category, parses with `feedparser.parse(text)` (never via URL to avoid blocking the event loop), filters entries by keyword substring match, classifies title+summary. tenacity retry with exponential backoff handles transient HTTP failures.

**TrendsAgent** (`trends.py`): Wraps synchronous pytrends in `asyncio.to_thread()`. Derives momentum by comparing the last 7 days' average interest to the prior 7 days. Bullish if +5 or more, bearish if -5 or less, neutral otherwise. Uses tenacity retry for 429 rate-limit responses; adds 1-second `asyncio.sleep` between requests.

**TwitterAgent** (`twitter.py`): Stub implementing full Protocol interface. Returns `SourceResult(source="twitter", signals=[], raw_data={"stub": True, ...})` on every call. Logs once at init that Twitter is deferred.

## Tests

`tests/research/test_agents.py` — 17 tests covering:
- isinstance(agent, ResearchAgent) for all 4 agents
- source_name attribute correct for each
- Empty SourceResult on missing credentials (Reddit, RSS)
- Empty SourceResult on empty DataFrame (Trends)
- Non-empty SourceResult with mocked data (Reddit, RSS, Trends)
- Twitter stub: returns empty, no exceptions, raw_data has stub flag

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

- [x] `src/pmtb/research/agents/__init__.py` exists
- [x] `src/pmtb/research/agents/reddit.py` exists
- [x] `src/pmtb/research/agents/rss.py` exists
- [x] `src/pmtb/research/agents/trends.py` exists
- [x] `src/pmtb/research/agents/twitter.py` exists
- [x] `tests/research/test_agents.py` exists (17 tests)
- [x] All 4 agents pass isinstance(agent, ResearchAgent)
- [x] Commits: 912e79e (test RED), c05c50e (Task 1 agents), 5ad5b7e (Task 2 Twitter stub)
- [x] Full test suite: 159 passed, 3 skipped (DB integration), 0 failures

## Self-Check: PASSED
