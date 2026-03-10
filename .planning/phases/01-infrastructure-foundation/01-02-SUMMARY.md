---
phase: 01-infrastructure-foundation
plan: "02"
subsystem: kalshi
tags: [python, httpx, rsa-pss, cryptography, tenacity, prometheus, kalshi-api]

requires:
  - "01-01 — Settings, metrics, logging foundation"

provides:
  - "build_kalshi_headers: RSA-PSS signing generating KALSHI-ACCESS-* headers with fresh timestamp"
  - "load_private_key: loads PEM RSA key from file path"
  - "KalshiAPIError hierarchy: RateLimit (429), Server (5xx), Client (4xx)"
  - "classify_error: maps HTTP status codes to correct error types"
  - "kalshi_retry: tenacity decorator with wait_exponential_jitter, 5 attempts, reraise=True"
  - "KalshiClient: authenticated httpx REST client with 7 endpoints, retry, metrics, logging"

affects:
  - 01-03
  - 02-scanner
  - 05-execution
  - 06-monitoring

tech-stack:
  added:
    - "httpx 0.28.1 — async HTTP client (SDK fallback)"
  patterns:
    - "RSA-PSS signing: timestamp_ms + METHOD + clean_path, base64-encoded"
    - "Never cache auth headers — build_kalshi_headers called per request"
    - "tenacity retry with wait_exponential_jitter(initial=1, max=30, jitter=3), stop_after_attempt(5)"
    - "classify_error pattern: 429->RateLimit, 5xx->Server, 4xx->Client"

key-files:
  created:
    - "src/pmtb/kalshi/__init__.py — package marker"
    - "src/pmtb/kalshi/auth.py — load_private_key, build_kalshi_headers"
    - "src/pmtb/kalshi/errors.py — KalshiAPIError hierarchy, classify_error, kalshi_retry"
    - "src/pmtb/kalshi/client.py — KalshiClient with 7 REST methods"
    - "tests/kalshi/__init__.py — test package marker"
    - "tests/kalshi/test_auth.py — 5 auth signing tests"
    - "tests/kalshi/test_retry.py — 9 retry and error classification tests"
    - "tests/kalshi/test_client.py — 9 client construction and method tests"
  modified:
    - "pyproject.toml — added httpx dependency"

key-decisions:
  - "kalshi-python-async SDK not used directly — it imports urllib3 which is not installed, causing ModuleNotFoundError; httpx.AsyncClient used as specified in plan fallback"
  - "httpx added as explicit dependency (uv add httpx) — Rule 3 deviation, required for client implementation"
  - "PSS salt_length=DIGEST_LENGTH — matches SHA-256 digest size (32 bytes), consistent with Kalshi spec"

duration: 8min
completed: 2026-03-10
---

# Phase 1 Plan 2: Kalshi REST Client Summary

**RSA-PSS signed Kalshi REST client using httpx with tenacity retry (exponential backoff + jitter), error classification by HTTP status, and Prometheus metrics per request**

## Performance

- **Duration:** ~8 minutes
- **Started:** 2026-03-10
- **Completed:** 2026-03-10
- **Tasks:** 2 of 2
- **Files modified:** 9

## Accomplishments

- `build_kalshi_headers` generates fresh KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP, KALSHI-ACCESS-SIGNATURE on every call; query params stripped before signing
- `load_private_key` reads PEM RSA key via cryptography library
- Error hierarchy with `classify_error`: 429 -> KalshiRateLimitError (retry), 5xx -> KalshiServerError (retry), 4xx -> KalshiClientError (raise immediately)
- `kalshi_retry` tenacity decorator: wait_exponential_jitter(initial=1, max=30, jitter=3), 5 attempts, reraise=True
- `KalshiClient` uses httpx.AsyncClient with manual header signing per request; 7 endpoints covered; demo/production URL selected by trading_mode; API_CALLS Prometheus counter updated per request

## Task Commits

1. **Task 1: RSA-PSS auth signing and error categorization** - `c6c3f5b` (feat)
2. **Task 2: KalshiClient wrapping SDK with authenticated methods** - `cd93d88` (feat)

## Files Created/Modified

- `src/pmtb/kalshi/__init__.py` — package marker
- `src/pmtb/kalshi/auth.py` — RSA-PSS signing with cryptography library
- `src/pmtb/kalshi/errors.py` — error hierarchy and tenacity retry decorator
- `src/pmtb/kalshi/client.py` — httpx-based REST client with 7 authenticated methods
- `tests/kalshi/test_auth.py` — 5 auth signing tests
- `tests/kalshi/test_retry.py` — 9 retry and error classification tests
- `tests/kalshi/test_client.py` — 9 client construction and REST method tests
- `pyproject.toml` — httpx added as dependency

## Decisions Made

- `kalshi-python-async` SDK cannot be imported due to missing `urllib3` dependency. Per plan's fallback instruction, implemented REST calls directly using `httpx.AsyncClient` with manual RSA-PSS header signing. This is equivalent in behavior and simpler.
- `httpx` added via `uv add httpx` — Rule 3 deviation (blocking dependency required for implementation).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] kalshi-python-async SDK import fails (missing urllib3)**
- **Found during:** Task 2 implementation (SDK inspection)
- **Issue:** `import kalshi_python_async` raises `ModuleNotFoundError: No module named 'urllib3'`; SDK cannot be used
- **Fix:** Implemented REST calls directly with `httpx.AsyncClient` as specified in plan's fallback note: "If the SDK does not support custom header injection cleanly, use `httpx.AsyncClient` as fallback"
- **Files modified:** `src/pmtb/kalshi/client.py`
- **Commit:** `cd93d88`

**2. [Rule 3 - Blocking] httpx not installed**
- **Found during:** Task 2 (after deciding on httpx fallback)
- **Issue:** `import httpx` raises ModuleNotFoundError
- **Fix:** `uv add httpx` — added httpx 0.28.1 and dependencies (httpcore, h11, certifi)
- **Files modified:** `pyproject.toml`
- **Commit:** `cd93d88`

---

**Total deviations:** 2 auto-fixed (1 SDK incompatibility resolved via planned fallback, 1 missing dependency)
**Impact on plan:** No scope change. The plan explicitly anticipated this scenario with the httpx fallback instruction.

## Self-Check: PASSED

- `src/pmtb/kalshi/auth.py` — exists
- `src/pmtb/kalshi/errors.py` — exists
- `src/pmtb/kalshi/client.py` — exists
- `tests/kalshi/test_auth.py` — exists
- `tests/kalshi/test_retry.py` — exists
- `tests/kalshi/test_client.py` — exists
- Commit `c6c3f5b` — verified (feat(01-02): RSA-PSS auth signing)
- Commit `cd93d88` — verified (feat(01-02): KalshiClient wrapping REST endpoints)
- All 23 tests pass: `uv run pytest tests/kalshi/ -x -q` → 23 passed

---
*Phase: 01-infrastructure-foundation*
*Completed: 2026-03-10*
