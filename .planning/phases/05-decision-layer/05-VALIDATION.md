---
phase: 5
slug: decision-layer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-10
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `pytest tests/decision/ -x -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~20 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/decision/ -x -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 5-01-01 | 01 | 1 | EDGE-01 | unit | `pytest tests/decision/test_edge.py::test_p_market_from_candidate -x` | ❌ W0 | ⬜ pending |
| 5-01-02 | 01 | 1 | EDGE-02 | unit | `pytest tests/decision/test_edge.py::test_ev_computation -x` | ❌ W0 | ⬜ pending |
| 5-01-03 | 01 | 1 | EDGE-03 | unit | `pytest tests/decision/test_edge.py::test_edge_computation -x` | ❌ W0 | ⬜ pending |
| 5-01-04 | 01 | 1 | EDGE-04 | unit | `pytest tests/decision/test_edge.py::test_edge_gate_rejects_below_threshold -x` | ❌ W0 | ⬜ pending |
| 5-02-01 | 02 | 1 | SIZE-01 | unit | `pytest tests/decision/test_sizer.py::test_kelly_formula -x` | ❌ W0 | ⬜ pending |
| 5-02-02 | 02 | 1 | SIZE-02 | unit | `pytest tests/decision/test_sizer.py::test_fractional_kelly_alpha -x` | ❌ W0 | ⬜ pending |
| 5-02-03 | 02 | 1 | SIZE-03 | unit | `pytest tests/decision/test_sizer.py::test_position_cap_applies -x` | ❌ W0 | ⬜ pending |
| 5-03-01 | 03 | 1 | RISK-01 | unit | `pytest tests/decision/test_risk.py::test_max_exposure_blocks_trade -x` | ❌ W0 | ⬜ pending |
| 5-03-02 | 03 | 1 | RISK-02 | unit | `pytest tests/decision/test_risk.py::test_max_single_bet_limit -x` | ❌ W0 | ⬜ pending |
| 5-03-03 | 03 | 1 | RISK-03 | unit | `pytest tests/decision/test_risk.py::test_var_computation -x` | ❌ W0 | ⬜ pending |
| 5-03-04 | 03 | 1 | RISK-04 | unit | `pytest tests/decision/test_risk.py::test_drawdown_halt_blocks_orders -x` | ❌ W0 | ⬜ pending |
| 5-03-05 | 03 | 1 | RISK-06 | unit | `pytest tests/decision/test_tracker.py::test_tracker_load_and_update -x` | ❌ W0 | ⬜ pending |
| 5-03-06 | 03 | 1 | RISK-07 | unit | `pytest tests/decision/test_risk.py::test_auto_hedge_trigger -x` | ❌ W0 | ⬜ pending |
| 5-03-07 | 03 | 1 | RISK-08 | unit | `pytest tests/decision/test_risk.py::test_duplicate_position_blocked -x` | ❌ W0 | ⬜ pending |
| 5-04-01 | 04 | 2 | RISK-05 | integration | `pytest tests/decision/test_watchdog.py::test_watchdog_sets_halt_flag -x` | ❌ W0 | ⬜ pending |
| 5-05-01 | 05 | 3 | ALL | integration | `pytest tests/decision/test_pipeline.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/decision/__init__.py` — package init
- [ ] `tests/decision/test_edge.py` — stubs for EDGE-01 through EDGE-04
- [ ] `tests/decision/test_sizer.py` — stubs for SIZE-01 through SIZE-03
- [ ] `tests/decision/test_risk.py` — stubs for RISK-01, RISK-02, RISK-03, RISK-04, RISK-07, RISK-08
- [ ] `tests/decision/test_tracker.py` — stubs for RISK-06
- [ ] `tests/decision/test_watchdog.py` — stubs for RISK-05
- [ ] `tests/decision/test_pipeline.py` — end-to-end pipeline integration stubs
- [ ] `src/pmtb/decision/__init__.py` — package init
- [ ] New Settings fields: `max_exposure`, `max_single_bet`, `var_limit`, `hedge_shift_threshold`

*Existing pytest infrastructure from prior phases covers framework install.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Watchdog survives main process crash | RISK-05 | Process-level crash behavior hard to test deterministically | Kill main process with SIGKILL; verify watchdog continues polling and sets halt flag |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 20s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
