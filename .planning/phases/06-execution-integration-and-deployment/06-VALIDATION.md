---
phase: 6
slug: execution-integration-and-deployment
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-10
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 1.x |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`, `asyncio_mode = "auto"`) |
| **Quick run command** | `uv run pytest tests/test_paper.py tests/test_orchestrator.py tests/test_fill_tracker.py tests/test_order_repo.py -x` |
| **Full suite command** | `uv run pytest tests/ -x` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_paper.py tests/test_orchestrator.py tests/test_fill_tracker.py tests/test_order_repo.py -x`
- **After every plan wave:** Run `uv run pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | EXEC-01 | unit | `uv run pytest tests/test_orchestrator.py::test_approved_decision_places_order -x` | ❌ W0 | ⬜ pending |
| 06-01-02 | 01 | 1 | EXEC-02 | unit | `uv run pytest tests/test_fill_tracker.py::test_fill_event_updates_order -x` | ❌ W0 | ⬜ pending |
| 06-01-03 | 01 | 1 | EXEC-03 | unit | `uv run pytest tests/test_fill_tracker.py::test_slippage_logged -x` | ❌ W0 | ⬜ pending |
| 06-01-04 | 01 | 1 | EXEC-04 | unit | `uv run pytest tests/test_fill_tracker.py::test_stale_order_cancelled -x` | ❌ W0 | ⬜ pending |
| 06-01-05 | 01 | 1 | EXEC-05 | integration | `uv run pytest tests/test_order_repo.py -x` | ❌ W0 | ⬜ pending |
| 06-02-01 | 02 | 2 | DEPL-01 | smoke/manual | `docker compose up --wait && curl -f http://localhost:9090/metrics` | manual | ⬜ pending |
| 06-02-02 | 02 | 2 | DEPL-02 | manual | SSH + `docker compose up -d` | manual | ⬜ pending |
| 06-02-03 | 02 | 2 | DEPL-03 | smoke | `docker logs pmtb-pmtb-1 | head -5 | python3 -c "import sys,json;[json.loads(l) for l in sys.stdin]"` | manual | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_orchestrator.py` — stubs for EXEC-01: full cycle, approved decision execution
- [ ] `tests/test_fill_tracker.py` — stubs for EXEC-02, EXEC-03, EXEC-04: fill events, slippage, stale cancellation
- [ ] `tests/test_order_repo.py` — stubs for EXEC-05: DB persistence queries
- [ ] `tests/conftest.py` — verify existing fixtures, add mock session_factory if needed
- [ ] Alembic migration for `orders.is_paper` column

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| docker compose up starts full system | DEPL-01 | Requires Docker daemon and port binding | Run `docker compose up --wait`, verify containers healthy, curl /metrics |
| Docker image deploys to VPS | DEPL-02 | Requires cloud VPS access | SSH to VPS, `docker compose up -d`, verify scan cycle in logs |
| JSON logs from Docker | DEPL-03 | Requires running container | `docker logs` and parse as JSON |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
