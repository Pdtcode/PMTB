# Phase 1: Infrastructure Foundation - Context

**Gathered:** 2026-03-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Kalshi API client (REST + WebSocket), PostgreSQL schema with async DB layer, configuration management, paper trading mode, error handling/recovery, and structured logging. This phase delivers the foundation that every downstream phase imports from — no trading logic, no market scanning, no signal processing.

</domain>

<decisions>
## Implementation Decisions

### Token & Auth Strategy
- Tokens live in-memory only — re-authenticate on restart, no persistence risk
- Proactive token refresh (background task before expiry) with reactive 401 fallback as safety net
- WebSocket reconnection uses fixed 5-second interval retry
- Pin Python 3.13 if kalshi-python-async requires it — all deps (pandas, xgboost, sklearn) support 3.13

### DB Schema Structure
- Pragmatic hybrid schema: normalized for core entities (orders, positions, markets) + denormalized wide tables for analytics queries
- All timestamps UTC everywhere — convert to local only for display
- No partitioning for now — standard indexes on timestamp columns, sufficient until millions of rows
- Soft delete with status flags for cancelled orders and expired markets — full audit trail, nothing is hard-deleted

### Config & Secrets Management
- Pydantic Settings class that reads from .env + YAML — typed config, validated at startup, fail fast with clear errors
- Secrets in .env file for local dev, cloud secrets manager (AWS SSM / GCP Secret Manager) for production
- Paper/live mode toggle: env var TRADING_MODE=paper|live as default, CLI flag --paper overrides — flexible for both Docker and local dev
- Config validated at startup only — no hot-reload, restart to apply changes

### Logging & Observability
- Configurable log levels: DEBUG = full decision trace (every pipeline stage), INFO = decisions only, WARNING = rejections and errors
- Output to stdout (JSON for Docker/cloud ingestion) + rotating log files for local dev debugging
- Correlation IDs: each scan cycle gets a cycle_id, each trade candidate gets a trade_id — full end-to-end tracing across pipeline stages
- Prometheus-style /metrics endpoint from day one: cycle count, latency, error rate, open positions — Grafana-ready

### Claude's Discretion
- Exact Alembic migration structure and naming convention
- PostgreSQL connection pool sizing
- Loguru sink configuration details
- Prometheus client library choice (prometheus_client vs aioprometheus)
- WebSocket heartbeat/ping interval

</decisions>

<specifics>
## Specific Ideas

- Research flagged kalshi-python-async 3.8.0 has conflicting Python version metadata (PyPI says >=3.13, description says >=3.9) — test early in phase, pin 3.13 if needed
- Research flagged Redis vs. in-process state for hot portfolio state — defer Redis unless concrete bottleneck. Start with in-process async dict, add Redis in Phase 5 if needed
- Use `uv` for dependency management per stack research recommendation

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — greenfield project, no existing code

### Established Patterns
- None — this phase establishes the patterns all downstream phases follow

### Integration Points
- This phase creates the KalshiClient, AsyncSession factory, Settings class, and Logger configuration that every subsequent phase imports
- Scanner (Phase 2) will import KalshiClient
- All phases import the DB session factory and models
- All phases use the structured logger with correlation ID binding

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-infrastructure-foundation*
*Context gathered: 2026-03-09*
