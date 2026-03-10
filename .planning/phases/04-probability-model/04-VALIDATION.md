---
phase: 4
slug: probability-model
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-10
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `pytest tests/prediction/ -x -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/prediction/ -x -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 4-01-01 | 01 | 1 | PRED-01 | unit | `pytest tests/prediction/test_xgboost_model.py::test_train_with_nan_features -x` | ❌ W0 | ⬜ pending |
| 4-01-02 | 01 | 1 | PRED-02 | unit | `pytest tests/prediction/test_xgboost_model.py::test_calibration_improves_brier -x` | ❌ W0 | ⬜ pending |
| 4-02-01 | 02 | 1 | PRED-03 | unit (mocked) | `pytest tests/prediction/test_llm_predictor.py::test_claude_returns_valid_json -x` | ❌ W0 | ⬜ pending |
| 4-02-02 | 02 | 1 | PRED-04 | unit | `pytest tests/prediction/test_pipeline.py::test_llm_gating_outside_confidence_band -x` | ❌ W0 | ⬜ pending |
| 4-03-01 | 03 | 2 | PRED-05 | unit | `pytest tests/prediction/test_combiner.py::test_log_odds_combine -x` | ❌ W0 | ⬜ pending |
| 4-03-02 | 03 | 2 | PRED-06 | unit | `pytest tests/prediction/test_models.py::test_prediction_result_fields -x` | ❌ W0 | ⬜ pending |
| 4-04-01 | 04 | 3 | PRED-07 | integration (DB) | `pytest tests/prediction/test_pipeline.py::test_persist_model_output -x -m "not demo"` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/prediction/__init__.py` — package init
- [ ] `tests/prediction/test_models.py` — stubs for PRED-06
- [ ] `tests/prediction/test_xgboost_model.py` — stubs for PRED-01, PRED-02
- [ ] `tests/prediction/test_llm_predictor.py` — stubs for PRED-03
- [ ] `tests/prediction/test_combiner.py` — stubs for PRED-05
- [ ] `tests/prediction/test_pipeline.py` — stubs for PRED-04, PRED-07
- [ ] `uv add xgboost scikit-learn` — xgboost not yet in pyproject.toml

*Existing pytest infrastructure from prior phases covers framework install.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Claude API anti-anchoring prompt effectiveness | PRED-03 | Requires subjective evaluation of prompt quality | Review Claude prompt template for anti-anchoring language; verify in shadow mode logs |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
