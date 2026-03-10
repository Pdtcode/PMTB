---
phase: 03-research-signal-pipeline
plan: "02"
subsystem: research
tags: [vader, sentiment, nlp, anthropic, claude, query-cache, ttl-cache]

# Dependency graph
requires:
  - phase: 03-research-signal-pipeline
    provides: SignalClassification, SourceResult, SignalBundle models (Plan 01)
  - phase: 02-market-scanner
    provides: MarketCandidate model consumed by QueryConstructor

provides:
  - SentimentClassifier (VADER + Claude hybrid) in src/pmtb/research/sentiment.py
  - QueryConstructor with template matching + Claude fallback in src/pmtb/research/query.py
  - QueryCache (TTL in-process cache) in src/pmtb/research/query.py

affects:
  - 03-research-signal-pipeline plan 03 (agents import SentimentClassifier and QueryConstructor directly)

# Tech tracking
tech-stack:
  added:
    - vadersentiment==3.3.2 (local VADER sentiment analysis)
    - anthropic==0.84.0 (Claude API client for escalation)
  patterns:
    - Two-tier classifier: fast local model (VADER) + expensive LLM (Claude) only for ambiguous cases
    - Graceful API-key-absent degradation: VADER-only mode when no key provided
    - TTL cache pattern: in-process dict with expires_at datetime per entry
    - Template-first query generation: pattern matching before any API calls

key-files:
  created:
    - src/pmtb/research/sentiment.py
    - src/pmtb/research/query.py
    - tests/research/test_sentiment.py
    - tests/research/test_query.py
  modified:
    - pyproject.toml (added vadersentiment and anthropic dependencies)

key-decisions:
  - "anthropic import is lazy (inside __init__ body) to avoid hard import failure when key is None"
  - "SENTIMENT_ESCALATIONS Prometheus counter tracks Claude escalation rate for cost monitoring"
  - "Template _is_meaningful() requires >2 non-stopword words to prevent single-word or stopword-only queries"
  - "QueryCache uses UTC datetime.now(tz=timezone.utc) for expiry — no clock skew issues across timezones"

patterns-established:
  - "Two-tier classifier pattern: cheap local tool first, expensive LLM only for ambiguous band"
  - "Lazy optional client init: only import and create AsyncAnthropic when API key is provided"
  - "_CacheEntry dataclass with expires_at field for TTL-based in-process caching"

requirements-completed:
  - RSRCH-06

# Metrics
duration: 2min
completed: "2026-03-10"
---

# Phase 3 Plan 02: Sentiment Classifier and Query Constructor Summary

**VADER + Claude hybrid sentiment classifier and TTL-cached query constructor — shared utilities consumed by all Phase 3 research agents**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-10T21:21:28Z
- **Completed:** 2026-03-10T21:23:19Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- SentimentClassifier routes clear bullish/bearish VADER scores locally (no API cost), escalates ambiguous text to Claude only when needed
- VADER-only mode when no Anthropic API key is present — zero hard dependencies on external services at startup
- QueryConstructor extracts search queries via template patterns (Will-X, price, election) with Claude fallback for unusual titles and keyword extraction as last resort
- QueryCache provides in-process TTL caching per ticker to eliminate redundant query generation within a scan cycle

## Task Commits

Each task was committed atomically (TDD: test → feat):

1. **Task 1: RED — SentimentClassifier tests** - `b4e95b9` (test)
2. **Task 1: GREEN — SentimentClassifier implementation** - `a48030b` (feat)
3. **Task 2: RED — QueryConstructor/QueryCache tests** - `bf32677` (test)
4. **Task 2: GREEN — QueryConstructor/QueryCache implementation** - `1749309` (feat)

**Plan metadata:** (docs commit follows)

_Note: TDD tasks have separate test → feat commits per the TDD execution flow_

## Files Created/Modified

- `src/pmtb/research/sentiment.py` - SentimentClassifier with VADER + Claude hybrid, Prometheus escalation counter
- `src/pmtb/research/query.py` - QueryConstructor with template matching, Claude fallback, and QueryCache TTL caching
- `tests/research/test_sentiment.py` - 7 tests: VADER routing, Claude escalation, VADER-only mode, confidence bounds
- `tests/research/test_query.py` - 9 tests: cache hit/miss/expiry, template patterns, Claude fallback, keyword fallback
- `pyproject.toml` - Added vadersentiment and anthropic dependencies

## Decisions Made

- Lazy `from anthropic import AsyncAnthropic` inside `__init__` body so the module can be imported without the package installed (optional dependency pattern)
- `SENTIMENT_ESCALATIONS` Prometheus counter added for production cost visibility on Claude escalation rate
- Template `_is_meaningful()` requires >2 non-stopword words to avoid generating single-keyword queries that would return poor search results
- `QueryCache` stores `_CacheEntry` dataclasses with UTC `expires_at` for reliable cross-timezone expiry without external dependencies

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed missing anthropic package**
- **Found during:** Task 1 GREEN phase (SentimentClassifier implementation)
- **Issue:** `from anthropic import AsyncAnthropic` raised ModuleNotFoundError — package not in pyproject.toml
- **Fix:** Ran `uv add anthropic` — installed anthropic==0.84.0
- **Files modified:** pyproject.toml
- **Verification:** All 7 sentiment tests pass after install
- **Committed in:** a48030b (Task 1 feat commit)

---

**Total deviations:** 1 auto-fixed (1 blocking dependency)
**Impact on plan:** Missing dependency install; no scope creep or design changes.

## Issues Encountered

None beyond the missing anthropic package (handled as Rule 3 blocking deviation above).

## User Setup Required

None - no external service configuration required for VADER-only mode. Set `anthropic_api_key` in config.yaml or environment to enable Claude escalation.

## Next Phase Readiness

- Plan 03 research agents can import `SentimentClassifier` and `QueryConstructor` directly
- Both utilities degrade gracefully when Claude API key is absent (safe for testing without API access)
- 142 tests passing, 3 skipped (DB integration) — no regressions

---
*Phase: 03-research-signal-pipeline*
*Completed: 2026-03-10*
