---
phase: 1
slug: infrastructure-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-09
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 1.x |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` — Wave 0 installs |
| **Quick run command** | `uv run pytest tests/ -x -q --ignore=tests/kalshi/test_client_integration.py --ignore=tests/kalshi/test_ws_client.py` |
| **Full suite command** | `uv run pytest tests/ -v --tb=short` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/ -x -q --ignore=tests/kalshi/test_client_integration.py --ignore=tests/kalshi/test_ws_client.py`
- **After every plan wave:** Run `uv run pytest tests/ -v --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01 | 01 | 0 | INFR-01 | unit | `uv run pytest tests/kalshi/test_auth.py -x` | ❌ W0 | ⬜ pending |
| 01-02 | 01 | 0 | INFR-01 | integration | `uv run pytest tests/kalshi/test_client_integration.py -x -m demo` | ❌ W0 | ⬜ pending |
| 01-03 | 01 | 0 | INFR-02 | integration | `uv run pytest tests/kalshi/test_ws_client.py -x -m demo` | ❌ W0 | ⬜ pending |
| 01-04 | 01 | 0 | INFR-02 | unit | `uv run pytest tests/kalshi/test_ws_reconnect.py -x` | ❌ W0 | ⬜ pending |
| 01-05 | 01 | 0 | INFR-03 | integration | `uv run pytest tests/db/test_session.py -x` | ❌ W0 | ⬜ pending |
| 01-06 | 01 | 0 | INFR-04 | integration | `uv run pytest tests/db/test_migrations.py -x` | ❌ W0 | ⬜ pending |
| 01-07 | 01 | 0 | INFR-05 | unit | `uv run pytest tests/test_config.py -x` | ❌ W0 | ⬜ pending |
| 01-08 | 01 | 0 | INFR-06 | unit | `uv run pytest tests/kalshi/test_retry.py -x` | ❌ W0 | ⬜ pending |
| 01-09 | 01 | 0 | INFR-07 | unit | `uv run pytest tests/test_reconciler.py -x` | ❌ W0 | ⬜ pending |
| 01-10 | 01 | 0 | INFR-08 | unit | `uv run pytest tests/test_paper.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/__init__.py` — package marker
- [ ] `tests/conftest.py` — shared fixtures: test Settings, test DB engine, mock KalshiClient
- [ ] `tests/kalshi/__init__.py` — package marker
- [ ] `tests/kalshi/test_auth.py` — covers INFR-01 unit (RSA-PSS signature format)
- [ ] `tests/kalshi/test_client_integration.py` — covers INFR-01 integration (demo API)
- [ ] `tests/kalshi/test_ws_client.py` — covers INFR-02 (demo)
- [ ] `tests/kalshi/test_ws_reconnect.py` — covers INFR-02 reconnect (mock)
- [ ] `tests/kalshi/test_retry.py` — covers INFR-06
- [ ] `tests/db/__init__.py` — package marker
- [ ] `tests/db/test_session.py` — covers INFR-03
- [ ] `tests/db/test_migrations.py` — covers INFR-04
- [ ] `tests/test_config.py` — covers INFR-05
- [ ] `tests/test_reconciler.py` — covers INFR-07
- [ ] `tests/test_paper.py` — covers INFR-08
- [ ] Framework install: `uv add --dev pytest pytest-asyncio pytest-mock`
- [ ] `pyproject.toml` pytest config: `asyncio_mode = "auto"`, demo marker

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| WebSocket streams real-time events without silent drops | INFR-02 | Requires live market hours + sustained connection | Connect to demo WS, subscribe to orderbook, verify messages arrive for 5+ minutes |
| Token refresh works against live Kalshi API | INFR-01 | Requires real API credentials + timing | Authenticate, wait for refresh window, verify request succeeds after refresh |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
