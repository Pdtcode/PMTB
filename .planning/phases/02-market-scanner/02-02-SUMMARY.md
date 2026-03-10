---
phase: 02-market-scanner
plan: "02"
subsystem: scanner
tags: [scanner, pagination, db-upsert, filter-chain, enrichment, tdd]
dependency_graph:
  requires:
    - 02-01 (MarketCandidate, ScanResult models; filter functions; VolatilityTracker)
    - 01-02 (KalshiClient._request for pagination and enrichment)
    - 01-01 (DB session, Market ORM model, Settings)
  provides:
    - MarketScanner class with run_cycle() and run_forever()
    - Full scan loop: paginate -> upsert -> filter -> enrich -> ScanResult
  affects:
    - Phase 3 (signal collector consumes ScanResult.candidates)
    - Phase 4 (model pipeline receives MarketCandidate list)
tech_stack:
  added: []
  patterns:
    - Cursor-based pagination via _request() directly (not get_markets())
    - PostgreSQL INSERT ON CONFLICT DO UPDATE for all-market upsert
    - asyncio.Semaphore for bounded concurrent enrichment
    - TDD red-green cycle for all scanner behaviors
key_files:
  created:
    - src/pmtb/scanner/scanner.py
    - tests/scanner/test_scanner.py
  modified:
    - src/pmtb/scanner/__init__.py
decisions:
  - Patch asyncio.sleep directly (not whole asyncio module) in run_forever tests to avoid blocking infinite loop
  - Empty orderbook gracefully skipped — candidate dropped rather than constructed with zero prices
  - Upsert committed in single batch per cycle (not per-market) for efficiency
metrics:
  duration: "14 min"
  completed_date: "2026-03-10"
  tasks_completed: 1
  files_created: 2
  files_modified: 1
---

# Phase 02 Plan 02: MarketScanner Class Summary

**One-liner:** MarketScanner orchestrates cursor-paginated Kalshi market fetch, PostgreSQL bulk upsert of all markets, sequential five-filter chain with per-market DEBUG rejection logging, and concurrent orderbook+event enrichment — returning a typed ScanResult sorted by distance from 50% implied probability.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for MarketScanner | e77033b | tests/scanner/test_scanner.py |
| 1 (GREEN) | MarketScanner implementation | f60d92a | src/pmtb/scanner/scanner.py, src/pmtb/scanner/__init__.py, tests/scanner/test_scanner.py |

## What Was Built

### MarketScanner class (`src/pmtb/scanner/scanner.py`)

**`_fetch_all_markets()`**
Cursor-based pagination loop calling `self._client._request("GET", "/trade-api/v2/markets", params={"status": "active", "limit": 1000, "cursor": cursor})`. Continues until cursor is None/empty. Returns flat list of all market dicts.

**`_upsert_markets(markets)`**
Uses `get_session(session_factory)` + SQLAlchemy `pg_insert(Market).on_conflict_do_update(index_elements=["ticker"])`. Parses `close_time` via `parse_close_time()`. Calls `await session.commit()` explicitly.

**`_apply_filters(markets)`**
Applies five filters sequentially (liquidity -> volume -> spread -> ttr -> volatility). Logs per-market rejection reason at DEBUG via `logger.bind(ticker=...).debug(...)`. Returns `(passing_markets, rejection_counts_dict)`.

**`_enrich(markets)`**
Fetches orderbook (`/trade-api/v2/markets/{ticker}/orderbook?depth=3`) and event (`/trade-api/v2/events/{event_ticker}`) concurrently per candidate using `asyncio.gather()` gated by `asyncio.Semaphore(scanner_enrichment_concurrency)`. Parses yes_bid from `yes_dollars[0][0]`, yes_ask from `1 - no_dollars[0][0]`. Skips candidates with empty orderbooks. Sorts by `abs(implied_probability - 0.5)` ascending.

**`run_cycle()`**
Generates UUID cycle_id, calls all four private methods, builds and returns `ScanResult` with all metadata fields populated.

**`run_forever()`**
Infinite loop: `await self.run_cycle()` in try/except (errors logged, loop continues), then `await asyncio.sleep(settings.scan_interval_seconds)`.

### __init__.py update
Added `MarketScanner` to public exports alongside `MarketCandidate` and `ScanResult`.

## Test Coverage

10 new tests in `tests/scanner/test_scanner.py`:

- `test_pagination_fetches_all_pages` — two-page cursor response collects all markets
- `test_pagination_stops_on_empty_cursor` — single call when no cursor returned
- `test_pagination_uses_request_not_get_markets` — verifies _request() is called directly
- `test_run_cycle_returns_scan_result` — ScanResult has correct totals and rejection counts
- `test_candidates_sorted_by_edge_potential` — market closest to 50% implied prob is first
- `test_upsert_all_markets_not_just_candidates` — session.commit() called once for all 5 markets
- `test_enrichment_fetches_orderbook_and_event` — correct endpoints called, fields populated
- `test_rejection_logged_at_debug` — logger.bind and debug called for rejected markets
- `test_run_forever_sleeps_between_cycles` — asyncio.sleep called with scan_interval_seconds
- `test_run_forever_sleeps_on_success` — sleep value matches settings.scan_interval_seconds

**Full suite:** 107 passed, 3 skipped (DB integration tests require live PostgreSQL). No regressions.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] run_forever tests hung due to whole-asyncio-module patching**
- **Found during:** Task 1 (GREEN phase test run — test_run_forever_* hung indefinitely)
- **Issue:** Original tests patched `pmtb.scanner.scanner.asyncio` replacing the entire module, but `asyncio.gather` and `asyncio.Semaphore` also got mocked out, causing internal gather calls to stall. The `run_forever` try/except also caught `_StopTest` exceptions, making them ineffective as loop terminators.
- **Fix:** Changed to `patch("pmtb.scanner.scanner.asyncio.sleep", mock_sleep)` which patches only the sleep function while leaving all other asyncio functionality intact. Changed test termination: mock_sleep itself raises the stop exception rather than run_cycle.
- **Files modified:** tests/scanner/test_scanner.py
- **Commit:** f60d92a (included in implementation commit)

## Key Decisions

1. **Patch asyncio.sleep directly** (not whole asyncio module) in run_forever tests — patching the full asyncio module breaks asyncio.gather and asyncio.Semaphore used internally in _enrich().

2. **Empty orderbook skipped gracefully** — candidate dropped with DEBUG log rather than constructing a MarketCandidate with zero bid/ask prices that would be meaningless.

3. **Single batch upsert per cycle** — all market rows committed in one `session.execute(stmt); session.commit()` call rather than per-market commits, for efficiency.

## Verification

```
python -m pytest tests/scanner/ -q
# 39 passed in 0.20s

python -m pytest tests/ -q
# 107 passed, 3 skipped in 1.97s

python -c "from pmtb.scanner import MarketScanner, MarketCandidate, ScanResult; print('OK')"
# OK
```

## Self-Check: PASSED

- [x] `src/pmtb/scanner/scanner.py` — exists
- [x] `tests/scanner/test_scanner.py` — exists
- [x] `src/pmtb/scanner/__init__.py` — modified to export MarketScanner
- [x] Commit e77033b (RED tests) — exists
- [x] Commit f60d92a (GREEN implementation) — exists
- [x] Pagination uses _request() directly (verified in test_pagination_uses_request_not_get_markets)
- [x] Upsert commits explicitly via session.commit() (verified in test_upsert_all_markets_not_just_candidates)
- [x] All 107 tests pass, no regressions
