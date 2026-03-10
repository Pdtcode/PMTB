# Phase 4: Probability Model - Context

**Gathered:** 2026-03-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Given a SignalBundle per market, produce a calibrated p_model with confidence interval. XGBoost provides the base estimate (when trained), Claude supplements for uncertain markets, and a combining strategy produces the final prediction. All predictions persist to PostgreSQL. No edge detection, no sizing, no trading — just probability estimation.

</domain>

<decisions>
## Implementation Decisions

### Cold Start Strategy
- Claude-only mode at launch — no resolved trade history exists to train XGBoost
- XGBoost runs in shadow mode during cold start: predictions recorded but not used for trading decisions
- Shadow predictions enable backtesting XGBoost accuracy before switching to hybrid mode
- Transition threshold from Claude-only to XGBoost+Claude hybrid: Claude's discretion (configurable)
- Once threshold is met, XGBoost becomes the primary estimator with Claude gated by confidence band (0.4–0.6 per PROJECT.md)

### Claude LLM Prediction Integration
- Structured analysis prompt: market title, close time, current price, research signal summaries → Claude returns structured JSON (p_estimate, confidence, reasoning, key_factors)
- Calibration system prompt included: instructs Claude to avoid anchoring, consider base rates, express genuine uncertainty
- Claude model tier configurable in YAML settings — default Sonnet, allow routing to Haiku (cheap) or Opus (quality) per use case
- Cold start gating strategy for Claude calls: Claude's discretion (configurable — balance API cost vs training data richness)

### Combining Strategy (XGBoost + Claude)
- Claude's discretion on combining method (Bayesian update, weighted average, or Claude-override) — make it configurable to experiment
- When only one estimate is available (cold start = Claude-only, or Claude not called = XGBoost-only), use that estimate directly

### Confidence Interval
- Claude's discretion on confidence interval method — make it configurable
- confidence_low and confidence_high must populate ModelOutput DB fields

### PredictionResult Output
- Claude designs PredictionResult to match what downstream Phase 5 (edge detection, Kelly sizing) needs
- Must include at minimum: p_model, confidence_low, confidence_high, model_version, used_llm
- Must be compatible with existing ModelOutput DB schema (p_model, p_market, confidence_low/high, signal_weights, model_version, used_llm, cycle_id)

### XGBoost Feature Set
- Claude's discretion on feature set — SignalBundle.to_features() provides 8 base features; market metadata features (implied_prob, spread, volume, time_to_resolution, category, volatility) are available from MarketCandidate
- Make feature set configurable for experimentation

### Calibration Method
- Claude's discretion on calibration approach (Platt scaling, isotonic, or auto-select based on dataset size)
- Recalibration trigger: Claude's discretion (configurable)
- Model persistence: save trained XGBoost + calibrator to disk (joblib); load on startup, retrain from DB if model file missing or stale

### Claude's Discretion
- Transition threshold for cold start → hybrid mode
- XGBoost feature engineering beyond base SignalBundle features
- Combining strategy (Bayesian, weighted average, or override)
- Confidence interval computation method
- PredictionResult fields beyond minimum required
- Cold start gating strategy for Claude API calls
- Calibration method selection (Platt vs isotonic vs auto)
- Recalibration trigger and frequency

</decisions>

<specifics>
## Specific Ideas

- Shadow mode during cold start is critical — it builds the dataset needed for XGBoost training while Claude handles actual predictions
- The calibration system prompt for Claude should reference prediction market research on LLM calibration (e.g. "avoid probability anchoring to 50%, 25%, 75% round numbers")
- ModelOutput DB model already exists with all needed fields — no schema changes required
- Research flag from STATE.md: "XGBoost initial training data strategy not yet decided" — this phase resolves it with Claude-only cold start + shadow mode

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `SignalBundle.to_features()` (src/pmtb/research/models.py): Returns 8-key flat dict with NaN for missing sources — direct XGBoost input
- `ModelOutput` DB model (src/pmtb/db/models.py): p_model, p_market, confidence_low/high, signal_weights, model_version, used_llm, cycle_id — prediction rows write here
- `MarketCandidate` (src/pmtb/scanner/models.py): Has implied_probability, spread, volume_24h, close_time, category — additional features for XGBoost
- `Settings` class (src/pmtb/config.py): Pydantic-settings pattern — model config fields follow this
- `SentimentClassifier` (src/pmtb/research/sentiment.py): Claude API call pattern with structured JSON output — prediction prompt follows similar pattern
- Anthropic client pattern from sentiment.py: `anthropic.AsyncAnthropic` with structured JSON parsing

### Established Patterns
- Pydantic models as pipeline contracts between phases
- Loguru structured logging with `.bind()` for contextual fields
- Prometheus metrics via counters/histograms
- cycle_id correlation for end-to-end tracing
- Optional API keys with graceful degradation (anthropic_api_key pattern from Phase 3)

### Integration Points
- Prediction module receives `list[MarketCandidate]` + `list[SignalBundle]` from research pipeline
- Prediction module writes `ModelOutput` rows to PostgreSQL via async session
- PredictionResult becomes a shared type imported by Phase 5 (decision layer)
- cycle_id flows from scanner through research through prediction

</code_context>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-probability-model*
*Context gathered: 2026-03-10*
