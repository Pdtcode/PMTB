---
phase: 03-research-signal-pipeline
plan: "01"
subsystem: research
tags: [models, protocol, config, pydantic, tdd]
dependency_graph:
  requires:
    - src/pmtb/scanner/models.py  # MarketCandidate.ticker and .category
    - src/pmtb/config.py          # Settings base class for new research fields
  provides:
    - src/pmtb/research/models.py     # SignalBundle, SourceSummary, SourceResult, SignalClassification
    - src/pmtb/research/agent.py      # ResearchAgent Protocol
  affects:
    - src/pmtb/config.py              # New research fields added to Settings
    - config.yaml                     # RSS feed defaults per category added
tech_stack:
  added: []
  patterns:
    - "@runtime_checkable Protocol (mirrors OrderExecutorProtocol from Phase 1)"
    - "Pydantic Literal for constrained enum validation"
    - "NaN sentinel for missing/failed sources (not 0.0 or neutral)"
    - "TDD: RED (failing tests) → GREEN (implementation) commit flow"
key_files:
  created:
    - src/pmtb/research/__init__.py
    - src/pmtb/research/agent.py
    - src/pmtb/research/models.py
    - tests/research/__init__.py
    - tests/research/test_models.py
  modified:
    - src/pmtb/config.py
    - config.yaml
decisions:
  - "NaN (not neutral 0.0) for missing sources in to_features() — absence of data is not neutral sentiment"
  - "Literal['bullish','bearish','neutral'] on SignalClassification enforces valid sentiments at construction"
  - "anthropic_api_key: str | None = None enables VADER-only mode when Claude API key absent"
  - "reddit_client_id/secret NOT in config.yaml — secrets are .env-only per project convention"
  - "twitter slot reserved in SignalBundle even though always None in Phase 3 — stub slot for Phase 5+"
metrics:
  duration: "2 min"
  completed_date: "2026-03-10"
  tasks_completed: 2
  files_created: 5
  files_modified: 2
---

# Phase 03 Plan 01: Research Type Contracts Summary

**One-liner:** ResearchAgent @runtime_checkable Protocol + SignalBundle/SourceSummary/SourceResult/SignalClassification Pydantic models + 10 research Settings fields with RSS feed YAML defaults.

## What Was Built

Task 1 (TDD) established the type contracts for the entire Phase 3 pipeline:

- `src/pmtb/research/agent.py` — `@runtime_checkable` ResearchAgent Protocol with `source_name: str` and `async fetch(candidate, query) -> SourceResult`. Follows the OrderExecutorProtocol pattern from Phase 1 (structural typing, no inheritance required).
- `src/pmtb/research/models.py` — four Pydantic models:
  - `SignalClassification`: per-signal output with `Literal["bullish","bearish","neutral"]` validation
  - `SourceResult`: raw agent output (list of classifications + optional raw_data for debugging)
  - `SourceSummary`: aggregated per-source result (sentiment/confidence can be None for failed sources)
  - `SignalBundle`: per-market per-cycle bundle with `to_features()` producing a flat 8-key numeric dict for XGBoost — missing sources yield `float("nan")` not `0.0`
- `tests/research/test_models.py` — 19 unit tests covering all behavior scenarios

Task 2 extended the Settings class with 10 new research config fields and updated config.yaml with sensible RSS feed defaults per market category.

## Verification

```
tests/research/test_models.py: 19 passed
tests/ (full suite): 126 passed, 3 skipped
```

All must-have contracts satisfied:
- `ResearchAgent` is importable and runtime_checkable
- `SignalBundle.to_features()` returns 8-key dict with NaN for missing sources
- `SourceSummary(sentiment=None, confidence=None, signal_count=0)` is valid
- `Settings` loads with `research_agent_timeout` and all other research fields

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 2b5f805 | test | Add failing tests for research models and ResearchAgent Protocol (RED) |
| c9d92ca | feat | Implement research models and ResearchAgent Protocol (GREEN) |
| 41d9daa | feat | Add research config fields to Settings and config.yaml |

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

**Files created:**
- `src/pmtb/research/__init__.py` — created
- `src/pmtb/research/agent.py` — created
- `src/pmtb/research/models.py` — created
- `tests/research/__init__.py` — created
- `tests/research/test_models.py` — created

**Files modified:**
- `src/pmtb/config.py` — research settings block added
- `config.yaml` — research defaults and RSS feeds added

**Commits verified:** 2b5f805, c9d92ca, 41d9daa

## Self-Check: PASSED
