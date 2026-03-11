---
phase: 7
slug: performance-tracking-and-learning-loop
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-11
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 1.x |
| **Config file** | pyproject.toml (`asyncio_mode = "auto"`, `testpaths = ["tests"]`) |
| **Quick run command** | `pytest tests/performance/ -x -q` |
| **Full suite command** | `pytest tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/performance/ -x -q`
- **After every plan wave:** Run `pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 0 | PERF-01 | unit | `pytest tests/performance/test_metrics.py::test_brier_score -x` | ❌ W0 | ⬜ pending |
| 07-01-02 | 01 | 0 | PERF-02 | unit | `pytest tests/performance/test_metrics.py::test_sharpe_ratio -x` | ❌ W0 | ⬜ pending |
| 07-01-03 | 01 | 0 | PERF-03 | unit | `pytest tests/performance/test_metrics.py::test_win_rate_profit_factor -x` | ❌ W0 | ⬜ pending |
| 07-01-04 | 01 | 0 | PERF-04 | unit | `pytest tests/performance/test_loss_classifier.py -x` | ❌ W0 | ⬜ pending |
| 07-01-05 | 01 | 0 | PERF-05 | unit | `pytest tests/performance/test_learning_loop.py::test_retrain_produces_new_version -x` | ❌ W0 | ⬜ pending |
| 07-01-06 | 01 | 0 | PERF-06 | unit | `pytest tests/performance/test_learning_loop.py::test_brier_degradation_trigger -x` | ❌ W0 | ⬜ pending |
| 07-01-07 | 01 | 0 | PERF-07 | unit | `pytest tests/performance/test_backtester.py -x` | ❌ W0 | ⬜ pending |
| 07-01-08 | 01 | 0 | PERF-08 | unit | `pytest tests/performance/test_backtester.py::test_same_code_paths -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/performance/__init__.py` — package init
- [ ] `tests/performance/test_metrics.py` — stubs for PERF-01, PERF-02, PERF-03
- [ ] `tests/performance/test_loss_classifier.py` — stubs for PERF-04
- [ ] `tests/performance/test_learning_loop.py` — stubs for PERF-05, PERF-06
- [ ] `tests/performance/test_backtester.py` — stubs for PERF-07, PERF-08
- [ ] `uv add apscheduler` — APScheduler not yet in pyproject.toml

*If none: "Existing infrastructure covers all phase requirements."*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Settlement polling against live Kalshi API | PERF-01 | Requires live API with settled markets | Run against demo API, verify settlements fetched |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
