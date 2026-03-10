---
phase: 02-market-scanner
plan: "01"
subsystem: scanner
tags: [pydantic, filters, models, configuration, tdd]
dependency_graph:
  requires:
    - 01-04 (config.py Settings class)
    - pydantic v2
  provides:
    - MarketCandidate Pydantic model
    - ScanResult Pydantic model
    - filter_liquidity, filter_volume, filter_spread, filter_ttr, filter_volatility
    - VolatilityTracker with warmup and rolling window
    - Settings scanner threshold fields
  affects:
    - 02-02 (scanner class will consume these contracts)
tech_stack:
  added: []
  patterns:
    - TDD (RED → GREEN for both tasks)
    - Pure functions with (list[dict], threshold) → (list[dict], int) signature
    - Pydantic v2 field constraints (ge, le) for price validation
    - collections.deque(maxlen=50) for O(1) sliding window
    - statistics.stdev for volatility computation
key_files:
  created:
    - src/pmtb/scanner/__init__.py
    - src/pmtb/scanner/models.py
    - src/pmtb/scanner/filters.py
    - tests/scanner/__init__.py
    - tests/scanner/test_models.py
    - tests/scanner/test_filters.py
  modified:
    - src/pmtb/config.py (added 8 scanner threshold fields)
decisions:
  - "open_interest_fp used for liquidity proxy — liquidity_dollars is deprecated/always 0"
  - "Warmup markets pass volatility filter (benefit of the doubt) — safer than false rejection"
  - "statistics.stdev over manual rolling mean — stdlib, correct, simpler"
  - "deque(maxlen=50) per ticker — bounded memory, O(1) append, automatic eviction"
metrics:
  duration: "2 min"
  completed: "2026-03-10"
  tasks_completed: 2
  files_created: 6
  files_modified: 1
---

# Phase 2 Plan 01: Scanner Models and Filter Functions Summary

**One-liner:** Pydantic contracts (MarketCandidate, ScanResult) and five pure filter functions with VolatilityTracker warmup logic, all thresholds configurable via Settings.

## What Was Built

### Task 1: MarketCandidate and ScanResult models + Settings fields

Created `src/pmtb/scanner/models.py` with two Pydantic v2 models:

- `MarketCandidate` — output contract for a market that passed all filters. Fields include ticker/title/category/event_context/close_time, price fields constrained to [0, 1] (yes_bid, yes_ask, implied_probability), spread/volume constrained to >= 0, and optional volatility_score (None during warmup).
- `ScanResult` — scan cycle output wrapper with candidates list, total_markets, per-filter rejection counts (rejected_liquidity/volume/spread/ttr/volatility), scan_duration_seconds, and cycle_id.

Added 8 scanner threshold fields to `Settings` in `src/pmtb/config.py`:

| Field | Default | Description |
|---|---|---|
| scanner_min_open_interest | 100.0 | Liquidity proxy threshold |
| scanner_min_volume_24h | 50.0 | 24h volume threshold |
| scanner_max_spread | 0.15 | Max bid-ask spread |
| scanner_min_ttr_hours | 1.0 | Min hours to close |
| scanner_max_ttr_days | 30.0 | Max days to close |
| scanner_min_volatility | 0.005 | Min price stdev |
| scanner_volatility_warmup | 6 | Snapshots before computing stdev |
| scanner_enrichment_concurrency | 5 | Concurrent enrichment API calls |

### Task 2: Filter functions with rejection counting and VolatilityTracker

Created `src/pmtb/scanner/filters.py` with five pure filter functions:

- `filter_liquidity(markets, min_open_interest)` — uses `open_interest_fp`, NOT `liquidity_dollars` (deprecated)
- `filter_volume(markets, min_volume_24h)` — uses `volume_24h_fp`
- `filter_spread(markets, max_spread)` — rejects wide spreads and markets missing bid/ask
- `filter_ttr(markets, min_hours, max_days)` — rejects markets outside the trading horizon window
- `filter_volatility(markets, min_volatility, tracker, warmup)` — warmup markets pass, low-stdev markets are rejected

`VolatilityTracker` class: per-ticker `deque(maxlen=50)` rolling window. `record_and_get(ticker, price, warmup)` returns `None` during warmup, then `statistics.stdev(history)` after warmup threshold is reached (requires >= 2 samples even if warmup is set low).

## Deviations from Plan

None — plan executed exactly as written.

## Test Results

```
tests/scanner/test_models.py  — 9 tests passed
tests/scanner/test_filters.py — 20 tests passed
Full test suite               — 97 passed, 3 skipped (no regressions)
```

## Self-Check: PASSED

Files verified present:
- src/pmtb/scanner/__init__.py: FOUND
- src/pmtb/scanner/models.py: FOUND
- src/pmtb/scanner/filters.py: FOUND
- tests/scanner/__init__.py: FOUND
- tests/scanner/test_models.py: FOUND
- tests/scanner/test_filters.py: FOUND
- src/pmtb/config.py (modified): FOUND

Commits verified:
- 216812b: test(02-01): add failing tests for MarketCandidate and ScanResult models
- fd6b655: feat(02-01): MarketCandidate and ScanResult models plus Settings scanner fields
- 3e6a4bb: test(02-01): add failing tests for filter functions and VolatilityTracker
- 13c80fb: feat(02-01): filter functions with rejection counting and VolatilityTracker
