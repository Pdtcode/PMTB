---
phase: 3
slug: research-signal-pipeline
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-10
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio (asyncio_mode = "auto") |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `pytest tests/research/ -x -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/research/ -x -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | RSRCH-01 | unit | `pytest tests/research/test_agents.py::test_twitter_stub -x` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | RSRCH-02 | unit (AsyncMock asyncpraw) | `pytest tests/research/test_agents.py::test_reddit_agent -x` | ❌ W0 | ⬜ pending |
| 03-01-03 | 01 | 1 | RSRCH-03 | unit (httpx mock) | `pytest tests/research/test_agents.py::test_rss_agent -x` | ❌ W0 | ⬜ pending |
| 03-01-04 | 01 | 1 | RSRCH-04 | unit (mock pytrends) | `pytest tests/research/test_agents.py::test_trends_agent -x` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 2 | RSRCH-05 | integration/timing | `pytest tests/research/test_pipeline.py::test_parallel_execution -x` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 2 | RSRCH-06 | unit | `pytest tests/research/test_sentiment.py -x` | ❌ W0 | ⬜ pending |
| 03-02-03 | 02 | 2 | RSRCH-07 | unit | `pytest tests/research/test_models.py::test_signal_bundle_category -x` | ❌ W0 | ⬜ pending |
| 03-02-04 | 02 | 2 | RSRCH-08 | unit | `pytest tests/research/test_pipeline.py::test_graceful_degradation -x` | ❌ W0 | ⬜ pending |
| 03-02-05 | 02 | 2 | RSRCH-09 | integration (mock session) | `pytest tests/research/test_pipeline.py::test_signal_persistence -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/research/__init__.py` — package init
- [ ] `tests/research/test_agents.py` — covers RSRCH-01 through RSRCH-04
- [ ] `tests/research/test_pipeline.py` — covers RSRCH-05, RSRCH-08, RSRCH-09
- [ ] `tests/research/test_sentiment.py` — covers RSRCH-06
- [ ] `tests/research/test_models.py` — covers RSRCH-07; SignalBundle, to_features(), SourceSummary validation

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
