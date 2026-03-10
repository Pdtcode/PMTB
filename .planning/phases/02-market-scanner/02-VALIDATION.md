---
phase: 2
slug: market-scanner
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-10
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` [tool.pytest.ini_options] |
| **Quick run command** | `python -m pytest tests/scanner/ -q` |
| **Full suite command** | `python -m pytest tests/ -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/scanner/ -q`
- **After every plan wave:** Run `python -m pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | SCAN-07 | unit | `python -m pytest tests/scanner/test_models.py -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | SCAN-01 | unit | `python -m pytest tests/scanner/test_scanner.py::test_pagination_fetches_all_pages -x` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | SCAN-01 | unit | `python -m pytest tests/scanner/test_scanner.py::test_pagination_stops_on_empty_cursor -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | SCAN-02 | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_liquidity -x` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 1 | SCAN-03 | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_volume -x` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 1 | SCAN-04 | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_ttr_too_soon -x` | ❌ W0 | ⬜ pending |
| 02-02-04 | 02 | 1 | SCAN-04 | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_ttr_too_far -x` | ❌ W0 | ⬜ pending |
| 02-02-05 | 02 | 1 | SCAN-05 | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_spread -x` | ❌ W0 | ⬜ pending |
| 02-02-06 | 02 | 1 | SCAN-06 | unit | `python -m pytest tests/scanner/test_filters.py::test_volatility_warmup_skip -x` | ❌ W0 | ⬜ pending |
| 02-02-07 | 02 | 1 | SCAN-06 | unit | `python -m pytest tests/scanner/test_filters.py::test_filter_volatility -x` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 2 | SCAN-07 | unit | `python -m pytest tests/scanner/test_scanner.py::test_run_cycle_returns_scan_result -x` | ❌ W0 | ⬜ pending |
| 02-03-02 | 03 | 2 | SCAN-07 | unit | `python -m pytest tests/scanner/test_scanner.py::test_candidates_sorted_by_edge_potential -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/scanner/__init__.py` — package init
- [ ] `tests/scanner/test_models.py` — MarketCandidate and ScanResult Pydantic model validation
- [ ] `tests/scanner/test_filters.py` — each filter function in isolation
- [ ] `tests/scanner/test_scanner.py` — MarketScanner cycle logic with mocked client + session

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Full pagination against live Kalshi | SCAN-01 | Requires live API credentials | Run scanner with KALSHI_API_KEY set, verify log shows multiple pages fetched |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
