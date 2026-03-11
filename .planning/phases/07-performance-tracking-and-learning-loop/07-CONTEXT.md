# Phase 7: Performance Tracking and Learning Loop - Context

**Gathered:** 2026-03-11
**Status:** Ready for planning

<domain>
## Phase Boundary

The system knows whether its predictions are improving or degrading — Brier score, Sharpe ratio, win rate, and profit factor are computed on resolved trades, losing trades are classified by error type, XGBoost is automatically retrained when calibration degrades, and a backtesting engine validates strategy changes before deployment. No new trading strategies, no dashboard, no notifications — just measurement, classification, retraining, and validation.

</domain>

<decisions>
## Implementation Decisions

### Metrics Computation
- Dual trigger: incremental update on each trade resolution + full daily recomputation for consistency
- Dual window: all-time metrics for overall performance + rolling window (configurable, default 30 days) for trend detection
- Rolling window metrics are what trigger retraining decisions
- Sharpe ratio: daily returns, annualized with sqrt(252) — standard quant approach
- Brier score, win rate, profit factor computed on resolved trades
- All metrics persisted to PerformanceMetric DB table (already exists)
- Trade resolution detection: Claude's discretion (poll Kalshi market status vs WS settlement events)

### Losing Trade Analysis
- Hybrid classification: rule-based heuristics first, Claude for ambiguous cases
- Fixed error taxonomy (enum): edge_decay, signal_error, llm_error, sizing_error, market_shock, unknown
- Rule-based heuristics: edge_decay if market moved against before resolution, signal_error if majority of research signals were wrong, llm_error if Claude's p_estimate was the outlier, sizing_error if edge correct but position too large
- Claude invoked only when rule-based classifier returns 'unknown' — keeps API cost low
- Classification only — no actionable recommendations in v1 (insufficient sample size early on)
- Error type and reasoning persisted to DB for pattern tracking over time

### Retraining Trigger
- Dual trigger: periodic schedule (configurable via Settings, default weekly) AND Brier score degradation beyond configurable threshold
- Training data: all resolved trades with weighted recency — recent trades weighted higher to adapt to market regime changes
- Auto-replace if improved: retrained model replaces live model only if hold-out Brier score improves; if worse, keep current model and log failed attempt
- XGBoost.train() and save()/load() via joblib already implemented (Phase 4)
- Retraining event logged with before/after Brier scores, sample count, model version

### Backtesting Engine
- Data sources: PostgreSQL (resolved trades, predictions, signals) + Kalshi historical API for backfill
- Same code paths as live trading: backtester calls ProbabilityPipeline.predict_all() and KellySizer.size() with swapped data source (PERF-08)
- Temporal integrity: Claude's discretion on enforcement approach (timestamp filtering or data source protocol) — must prevent lookahead bias
- Results: structured log to stdout + backtest_runs table in PostgreSQL (metrics, parameters, date range)
- No visualization — CLI/logs only per PROJECT.md scope

### Claude's Discretion
- Trade resolution detection approach (polling vs WS vs both)
- Brier score degradation threshold default value
- Rolling window size default (suggested 30 days)
- Recency weighting function for retraining data
- Temporal integrity enforcement approach (timestamp filtering vs data source protocol)
- Backtesting engine invocation method (CLI command, scheduled job, or manual trigger)
- Kalshi historical API integration approach for backfill
- Hold-out split strategy for model comparison during retraining

</decisions>

<specifics>
## Specific Ideas

- XGBoostPredictor already computes brier_calibrated and brier_raw during training — reuse this for retraining comparison
- PerformanceMetric DB model already exists with fields for aggregated metrics
- Trade.resolved_outcome and resolved_at fields already in schema — ready for outcome tracking
- ModelOutput has signal_weights — useful for diagnosing signal_error in losing trade analysis
- PredictionResult.used_llm tracks Claude involvement — key for identifying llm_error cases

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `XGBoostPredictor` (src/pmtb/prediction/xgboost_model.py): .train(), .save(), .load() with joblib, brier_score_loss computed internally
- `ProbabilityPipeline.predict_all()` (src/pmtb/prediction/pipeline.py): Full prediction path — backtester reuses this
- `KellySizer.size()` (src/pmtb/decision/sizer.py): Sizing logic — backtester reuses this
- `EdgeDetector.evaluate()` (src/pmtb/decision/edge.py): Edge computation — backtester reuses this
- `Trade` DB model (src/pmtb/db/models.py): Has resolved_outcome, resolved_at, p_model, entry_price, quantity
- `ModelOutput` DB model: Has p_model, p_market, signal_weights, model_version, used_llm, cycle_id
- `PerformanceMetric` DB model: Ready for metrics storage
- `OrderRepository` (src/pmtb/order_repo.py): DB access patterns for trades and orders
- `Settings` (src/pmtb/config.py): Add retraining_schedule, brier_threshold, rolling_window_days

### Established Patterns
- Pydantic models as pipeline contracts
- Settings class for all configurable thresholds
- Prometheus metrics (counters, histograms)
- Loguru structured logging with .bind() and cycle_id
- Async session factory for DB access
- Graceful degradation on failures

### Integration Points
- Metrics computation triggers after Trade.resolved_outcome is set
- Retraining loop reads resolved trades from DB, calls XGBoostPredictor.train(), compares Brier scores
- Backtester imports ProbabilityPipeline, KellySizer, EdgeDetector — same code paths
- Learning loop runs alongside main pipeline (separate async task or scheduled job)

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 07-performance-tracking-and-learning-loop*
*Context gathered: 2026-03-11*
