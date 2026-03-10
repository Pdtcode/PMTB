---
phase: 03-research-signal-pipeline
plan: "04"
subsystem: research-pipeline-orchestrator
tags: [research, pipeline, orchestrator, asyncio, prometheus, postgresql, signal-persistence]
dependency_graph:
  requires:
    - "03-01"  # ResearchAgent Protocol, SourceResult, MarketCandidate, SignalBundle models
    - "03-02"  # SentimentClassifier (VADER + Claude escalation)
    - "03-03"  # All 4 research agents (Reddit, RSS, Trends, Twitter)
  provides:
    - ResearchPipeline orchestrator (parallel gather, fault tolerance, DB persistence)
  affects:
    - "04-xx"  # Phase 4 XGBoost model receives list[SignalBundle] from pipeline.run()
tech_stack:
  added: []
  patterns:
    - asyncio.gather for concurrent agent dispatch (all 4 agents per candidate, per cycle)
    - asyncio.timeout context manager for bounded agent execution
    - Majority vote aggregation for sentiment (Counter.most_common)
    - Mean confidence aggregation across signals
    - Prometheus Counter + Histogram for signals collected, agent failures, cycle duration
    - get_session(session_factory) pattern for async DB access
    - One Signal row per SignalClassification (fine-grained DB storage for post-trade analysis)
key_files:
  created:
    - src/pmtb/research/pipeline.py
    - tests/research/test_pipeline.py
  modified: []
decisions:
  - "Failed/timed-out agents produce None in SignalBundle — absence of data is not neutral sentiment"
  - "asyncio.timeout context manager (Python 3.11+) used instead of asyncio.wait_for — cleaner API"
  - "DB persistence skipped per-market if market_id not resolved — signals still aggregated and returned"
  - "One Signal row per SignalClassification — preserves individual signals for Phase 7 post-trade analysis"
  - "Prometheus RESEARCH_CYCLE_DURATION histogram times entire batch (not per-candidate) — aligns with cycle-level observability"
metrics:
  duration: "3 min"
  completed_date: "2026-03-10"
  tasks_completed: 2
  files_created: 2
  tests_added: 9
---

# Phase 03 Plan 04: ResearchPipeline Orchestrator Summary

**One-liner:** ResearchPipeline orchestrates all 4 agents concurrently via asyncio.gather with asyncio.timeout fault isolation, persists individual Signal rows to PostgreSQL, and assembles per-market SignalBundles (None for failed sources, not neutral).

## What Was Built

`src/pmtb/research/pipeline.py` — `ResearchPipeline` class:

**Parallel execution:** `run()` fires all configured agents concurrently per candidate using `asyncio.gather`. Each agent call is wrapped in `_run_agent_safe()` which applies `asyncio.timeout(self.timeout)` — timed-out or exception-raising agents return `None` instead of propagating.

**Graceful degradation:** Each agent's `None` result maps to a `None` SourceSummary in the `SignalBundle`. A failed agent does NOT produce `SourceSummary(sentiment="neutral")` — absence of data propagates as `None` (and eventually `float("nan")` via `to_features()`).

**DB persistence:** `_resolve_market_id()` looks up the market UUID by ticker. `_persist_signals()` writes one `Signal` ORM row per `SignalClassification`, with `market_id`, `source`, `sentiment`, `confidence` (Decimal), `raw_data` (includes reasoning if present), and `cycle_id`. Commits after each source's signals are added.

**SignalBundle assembly:** After gathering, each agent's `SourceResult | None` is aggregated via `_aggregate_source()` (majority vote sentiment, mean confidence, signal_count). Results are mapped by `agent.source_name` to `SignalBundle` fields (`reddit`, `rss`, `trends`, `twitter`).

**Prometheus metrics:**
- `research_signals_collected_total` (labels: source) — incremented per signal classification
- `research_agent_failures_total` (labels: source, reason=timeout|error) — incremented on agent failure
- `research_cycle_duration_seconds` — histogram timing the full `run()` call

## Tests

`tests/research/test_pipeline.py` — 9 tests:

| Test | What it verifies |
|------|-----------------|
| `test_parallel_execution` | 4 agents each sleeping 0.1s complete in < 0.3s total |
| `test_graceful_degradation` | One raising agent, other 3 still produce SourceSummary |
| `test_timeout_handling` | Agent with 100s sleep, timeout=0.05s produces None |
| `test_failed_source_is_none_not_neutral` | bundle.reddit is None, not SourceSummary(sentiment="neutral") |
| `test_signal_persistence` | session.add called for each Signal, correct fields |
| `test_bundle_assembly` | bundle.ticker, cycle_id, per-source sentiment/signal_count correct |
| `test_aggregate_source_empty_signals` | SourceSummary(sentinel=None, confidence=None, signal_count=0) |
| `test_aggregate_source_none_returns_none` | None -> None |
| `test_aggregate_source_majority_sentiment` | 2 bullish + 1 bearish = bullish, mean confidence correct |

## Integration Results

- `uv run pytest tests/research/ -q`: **61 passed** (all research plans)
- `uv run pytest tests/ -q`: **168 passed, 3 skipped** (DB integration, expected — no live DB)
- All research module imports verified end-to-end

## Deviations from Plan

**[Rule 1 - Bug] Fixed MarketCandidate constructor fields in test fixture**
- **Found during:** Task 1 GREEN phase
- **Issue:** Plan's interface section showed `yes_price`, `no_price`, `open_interest_fp`, `liquidity_score` as MarketCandidate fields, but the actual model has `yes_bid`, `yes_ask`, `implied_probability`, `spread`, `volume_24h`, `event_context`
- **Fix:** Updated `make_candidate()` test helper to use correct MarketCandidate fields
- **Files modified:** `tests/research/test_pipeline.py`
- **Commit:** a763fde

## Self-Check

- [x] `src/pmtb/research/pipeline.py` exists
- [x] `tests/research/test_pipeline.py` exists (9 tests)
- [x] `ResearchPipeline` instantiation with all 4 agents works
- [x] Commits: c7b4947 (test RED), a763fde (feat GREEN)
- [x] Full research suite: 61 passed
- [x] Full project suite: 168 passed, 3 skipped

## Self-Check: PASSED
