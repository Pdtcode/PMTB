# Pitfalls Research

**Domain:** AI-powered prediction market trading bot (Kalshi)
**Researched:** 2026-03-09
**Confidence:** MEDIUM-HIGH (core pitfalls verified via multiple sources; some LLM-in-trading details are emerging/MEDIUM)

---

## Critical Pitfalls

### Pitfall 1: Uncalibrated Probability Outputs from XGBoost

**What goes wrong:**
XGBoost classification outputs are discrimination scores, not calibrated probabilities. A model predicting 0.72 does not mean there is a 72% chance of the event occurring — the optimization process pushes scores toward 0 and 1 to maximize log loss, distorting the probabilistic interpretation. When these raw scores feed directly into Kelly sizing and edge detection, position sizes become systematically wrong. The bot may trade aggressively on "0.78 probability" when the true calibrated probability is closer to 0.61, producing an illusory edge.

**Why it happens:**
Developers see XGBoost output a float between 0 and 1 and assume it is a probability. It is not. Gradient boosting is not a generative model. This is an extremely common mistake documented in the ML calibration literature. Combined with imbalanced class distributions (rare market outcomes), calibration error compounds further.

**How to avoid:**
Apply post-hoc calibration before using XGBoost outputs as probabilities. Use `CalibratedClassifierCV` with `method='isotonic'` (for large validation sets) or `method='sigmoid'` (Platt scaling for small sets) from scikit-learn. Maintain a held-out calibration set that was never used for training. Measure calibration quality using the Brier score and reliability diagrams, not AUC alone. Treat calibration as a first-class deliverable alongside the model itself.

**Warning signs:**
- Brier score tracked but calibration curves (reliability diagrams) never plotted
- XGBoost `predict_proba()` output used directly in Kelly formula without a calibration step
- Model shows high AUC but poor Brier score on out-of-sample data
- Systematic bias: bot consistently trades one direction (overconfident in YES or NO)

**Phase to address:**
Probability model build phase (before any live trading). Calibration must be validated during backtesting, not retrofitted after live losses.

---

### Pitfall 2: Backtesting Look-Ahead Bias in Market Scanner and Signal Pipeline

**What goes wrong:**
The backtest shows strong performance, live trading shows losses. The most common cause: information available at evaluation time is leaked into decisions that should have been made without it. For this system, this manifests as: using a market's final resolution status to filter training examples, computing features (e.g., trend slopes, NLP sentiment aggregates) over windows that extend past the decision timestamp, or using Kalshi market metadata that changes after market close.

**Why it happens:**
Prediction market data is event-based, not tick-by-tick. Resolution is a hard binary event — it is tempting to load all historical data and train/test without carefully enforcing strict temporal boundaries. The multi-stage pipeline (scan → research → predict → execute) makes this worse: each stage can introduce subtle future leakage.

**How to avoid:**
Implement strict timestamp discipline throughout the pipeline. Every feature must be labeled with the latest information date it depends on. Use a walk-forward validation framework: train on T-N to T-k, test on T-k to T, slide forward. Never select or filter markets based on post-resolution information. Separate market scanner filters (liquidity, spread) from model training features.

**Warning signs:**
- Sharpe ratio above 3 in backtests (extremely rare in live trading; signals overfit or leakage)
- Bot performance degrades significantly in first week of live trading
- Feature computation code shares the same dataframe that contains resolution outcomes
- No explicit assertion in backtest code that `feature_date < decision_date < resolution_date`

**Phase to address:**
Backtesting system build phase. Enforce temporal integrity as an architectural constraint, not a post-hoc check.

---

### Pitfall 3: Kelly Criterion Overbetting Due to Edge Overestimation

**What goes wrong:**
Even with fractional Kelly (alpha = 0.25-0.5), if the estimated edge is wrong, the bot systematically overbets. Kelly is extremely sensitive to input accuracy: a 10% overestimate of true probability leads to Kelly fraction inflation that can still result in catastrophic drawdowns over a series of trades. Overestimating edge is the default failure mode — models tend to be overconfident because they are trained on features that worked historically.

**Why it happens:**
The Kelly formula amplifies estimation error. If p_model = 0.62 but true p = 0.52, the edge is 4x smaller than assumed. The minimum 4% edge threshold sounds conservative but only protects against small absolute mispricings — it does not protect against a systematically miscalibrated model generating false 4% edges across hundreds of trades.

**How to avoid:**
Use the 4% edge threshold as a necessary but not sufficient condition. Additionally: apply a model uncertainty penalty (if confidence interval of p_model includes p_market, skip the trade), enforce a maximum position size cap independent of Kelly (e.g., max 5% of bankroll per trade regardless of Kelly output), and log expected value vs. realized value per trade to detect systematic overestimation early. Start with alpha = 0.25 (quarter-Kelly), not 0.5.

**Warning signs:**
- Kelly formula outputs position sizes that regularly approach the max exposure cap
- No confidence interval around probability estimates — point estimates fed directly to Kelly
- Positive expected value in backtest but negative returns in paper trading
- Win rate matches predictions but P&L does not

**Phase to address:**
Risk management build phase. Kelly implementation must include an uncertainty floor and hard cap, not just the fractional multiplier.

---

### Pitfall 4: Missing Hard Circuit Breaker That Cannot Be Bypassed by Prediction Logic

**What goes wrong:**
The bot enters a regime where its probability model is wrong (news event, market structure change, data feed failure) and continues trading autonomously, stacking losses until the 8% drawdown limit is hit. The problem: if the drawdown check is implemented inside the same prediction/execution loop it is supposed to constrain, bugs in the loop can bypass it. The system needs a hard, external circuit breaker that operates independently.

**Why it happens:**
Developers implement drawdown limits as a conditional inside the main trading function: `if drawdown < 0.08: execute_trade()`. A bug in the drawdown calculation, a stale account balance query, or an exception that skips the check can allow trading to continue. Autonomous systems have no human catching these failures in real time.

**How to avoid:**
Implement drawdown circuit breakers at two independent layers: (1) a risk check module that reads account state from the database and vetoes orders before Kalshi API calls — this runs as a separate function with its own error handling; (2) a heartbeat watchdog process that independently polls account equity and issues a halt signal if 8% drawdown is breached, even if the main trading process is hung or miscalculating. Test the circuit breaker explicitly: write a test that forces drawdown to 8.1% and verifies no orders are placed.

**Warning signs:**
- Drawdown check is a single `if` statement inline in the main execution path
- No automated test that verifies the halt condition works
- Bot has no concept of "halted state" that persists across restarts
- Account balance is fetched once at startup, not on each trade cycle

**Phase to address:**
Risk management phase, before any live trading. Circuit breaker must be the first risk feature, not the last.

---

### Pitfall 5: Kalshi API Token Expiry and Rate Limit Handling Causing Silent Failures

**What goes wrong:**
Kalshi authentication tokens expire periodically (the API requires re-authentication). If the bot does not handle token expiry, it enters a state where API calls return 401 errors. If these errors are logged but not treated as fatal, the bot silently stops executing trades while the main loop continues running. This is particularly dangerous: the bot appears healthy but is neither scanning nor executing. It may also accumulate stale open positions from previous sessions.

Rate limit breaches (429 errors) without exponential backoff cause retry storms that worsen the problem.

**Why it happens:**
Token expiry is not documented prominently. Developers test with short sessions and never encounter expiry. Error handling for HTTP errors in the order execution path is implemented as `print(error); continue` — which does not halt the process or alert the operator.

**How to avoid:**
Implement a token refresh manager as a singleton with automatic refresh before expiry (not on-demand after a 401). Treat any consecutive API authentication failure as a fatal error that halts trading and sends an alert. Implement structured error categorization: transient (retry with backoff), rate limit (backoff + throttle), fatal (halt + alert). Test token expiry explicitly using a mock that expires tokens after 30 seconds.

**Warning signs:**
- No token refresh logic in the API client
- HTTP errors caught with broad `except Exception` and logged without halting
- Bot restart does not check for stale open orders from the previous session
- No monitoring/alerting on consecutive API errors

**Phase to address:**
Kalshi API integration phase (early). Token management and error handling must be built into the API client before any trading logic is layered on top.

---

### Pitfall 6: LLM Probability Outputs Treated as Ground Truth Without Calibration or Uncertainty

**What goes wrong:**
Claude API is asked to reason about an event and return a probability estimate (e.g., "0.65 probability of YES"). That number is used directly as p_model without any validation of its calibration or confidence. LLMs are not calibrated probability estimators — they generate plausible-sounding numbers. Research shows LLM-based agents perform dramatically differently across time periods and are poorly calibrated for bear/decline scenarios. Using LLM outputs as hard inputs to Kelly sizing is dangerous.

**Why it happens:**
LLMs return structured JSON that looks precise. The model says "0.65" — that precision implies measurement-level accuracy that does not exist. There is no mechanism in Claude's output that represents uncertainty or calibration error.

**How to avoid:**
Treat the LLM output as one weak signal among several, not as a probability estimate. Combine LLM reasoning with XGBoost output using ensemble methods (weighted average, stacking). Never size a position based on LLM output alone. Implement a cost gate: only invoke Claude API on markets that pass all pre-screening filters (liquidity, volume, spread, XGBoost edge). Track LLM-sourced edge vs. final realized P&L to calibrate the LLM signal weight over time.

**Warning signs:**
- Trade decision depends solely on Claude API response
- No ensemble combining LLM output with quantitative model
- LLM is called for every candidate market regardless of quality
- Claude API spend not tracked per-trade or per-day

**Phase to address:**
Probability model integration phase. LLM must be a weighted input to an ensemble, validated against out-of-sample performance before alpha is assigned.

---

### Pitfall 7: Social Media Signal Contamination from Bots, Spam, and Market Manipulation

**What goes wrong:**
The research pipeline ingests Twitter/X and Reddit data to generate NLP sentiment signals. On prediction market-relevant events (elections, major economic data), social media is heavily contaminated with bot activity, coordinated campaigns, and deliberate manipulation. The NLP sentiment signal becomes a noise vector, not a signal vector. Studies show wash trading accounts for an estimated 25% of some prediction market volume — the same actors may also manipulate social media sentiment to move prediction market prices.

**Why it happens:**
NLP pipelines are built and validated on clean corpus data. Real-time social media during high-stakes events is structurally different. Developers rarely include adversarial signal quality checks.

**How to avoid:**
Implement source credibility weighting (verified accounts, account age, follower count) before sentiment aggregation. Apply rate-of-change filters: a sudden spike in sentiment volume on a topic with low prior volume is more likely manipulation than genuine signal — treat it as a signal quality flag, not a trade trigger. Use multiple independent sources (RSS news, Google Trends) to corroborate social media signals before they influence prediction. Never trigger a trade from a single social media signal spike.

**Warning signs:**
- Sentiment aggregation is simple average without credibility weighting
- Pipeline has no anomaly detection for sudden signal volume spikes
- Research pipeline passes raw tweet/post count as a feature
- No cross-source corroboration requirement before elevated-confidence prediction

**Phase to address:**
Research signal pipeline phase. Signal quality filters must be designed before training the prediction model, not after.

---

### Pitfall 8: Contract Resolution Ambiguity Not Handled in Position Management

**What goes wrong:**
Kalshi markets can have ambiguous settlement outcomes — as documented by the Khamenei market incident (February 2026) where $21.7M in positions settled at last-traded price rather than $1 due to "grammatically ambiguous" contract language. The bot holds positions expecting binary $0/$1 resolution. Kalshi's edge-case settlement (at a price other than the binary outcome) breaks the P&L accounting, position closure logic, and historical training data integrity.

**Why it happens:**
Developers assume binary settlement is always exactly $0 or $1 per share. Settlement edge cases are not covered by most API documentation. Kalshi has had at least 5 public settlement disputes since 2025.

**How to avoid:**
Never assume resolution price is exactly 0 or 1. Read the actual settlement amount from the API/database rather than inferring it from the market resolution status. Mark resolved trades with their actual settlement price, not the expected outcome. Include settlement dispute handling: if a market is halted (trading suspended before resolution), the bot should close or not open new positions immediately, not wait for final resolution. Review contract language complexity as a market selection filter — prefer markets with unambiguous resolution sources.

**Warning signs:**
- P&L calculation code contains `if resolved == 'YES': pnl = shares * 1.0`
- No handling of market halt status (halted markets treated same as open)
- Kalshi API settlement price field read but not stored per trade
- Historical trade data assumes binary outcomes for all resolved markets

**Phase to address:**
Trade execution and P&L tracking phase. Also affects the backtesting system when ingesting historical settlement data.

---

### Pitfall 9: Model Stagnation — No Regime Change Detection or Retraining Cadence

**What goes wrong:**
The prediction model is trained, deployed, and left static. Prediction markets change in character — new market categories open, political events conclude, economic regimes shift, and the distribution of features that predict outcomes drifts from the training distribution. A model with no retraining schedule slowly degrades from competitive to unprofitable while appearing to function normally.

**Why it happens:**
Retraining pipelines are harder to build than the initial model. Developers ship the model and defer the retraining system. Performance metrics show slow degradation that is attributed to "bad luck" rather than model drift.

**How to avoid:**
Build a Brier score monitoring pipeline that computes rolling performance over the last N resolved markets. Define a retraining trigger threshold (e.g., 7-day rolling Brier score degrades by more than 15% from baseline). Implement walk-forward retraining: when triggered, retrain on the most recent 90-180 days of resolved trades, validate on a held-out set, and deploy only if performance is better than or equal to the current model. Log model version per trade so performance can be attributed to specific model versions.

**Warning signs:**
- No scheduled retraining job in production
- Brier score is computed and logged but no alert threshold defined
- Model version is not stored per trade in the database
- Degrading P&L attributed to "tough market conditions" without investigating calibration drift

**Phase to address:**
Learning loop / model improvement phase. Retraining infrastructure should be planned in the initial model architecture, not retrofitted.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Hardcode edge threshold at 4% forever | Simpler code | Cannot adapt if market becomes more/fewer competitive without code changes; threshold should be derived from model calibration data | Never — parameterize from config |
| Use `predict_proba()` directly from XGBoost without calibration | Faster to ship | Systematically miscalibrated positions, silent P&L drag | Never for live trading |
| Single-file drawdown check (no watchdog) | Simpler architecture | Circuit breaker can be bypassed by exceptions in main loop | Only in development/paper trading |
| Polling Kalshi REST every N seconds instead of WebSocket for live markets | Easier to implement | Stale order book data, slower edge capture, misses real-time fills | Acceptable for MVP scanner if N is small; not for execution |
| Run all agents in a single Python process | Simpler deployment | GIL limits true parallelism; one crashing agent kills the system | Only if pipeline is sequential, not parallel |
| Skip calibration set — use train/test split only | Faster iteration | Calibration overfitted to test set, degrades in production | Never for production model |
| Store LLM reasoning as free text, not structured schema | Faster to build | Cannot programmatically analyze which reasoning patterns correlate with good/bad trades | MVP only if schema is planned for v2 |

---

## Integration Gotchas

Common mistakes when connecting to external services.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Kalshi API auth | No token refresh; bot silently fails after token expiry | Implement a token manager that refreshes proactively before expiry; treat 401 as fatal halt |
| Kalshi order placement | Batch request count too high, hitting rate limits silently | Limit parallel requests to 1-20 per batch; implement 429 exponential backoff (2s, 4s, 8s) |
| Kalshi market data | Assuming market structure (contract multiplier, tick size) is fixed | Read contract specs from API at runtime; Kalshi market structures have changed without notice |
| Twitter/X API v2 | Free tier rate limits hit immediately with high-volume scanning; streaming assumed to be always-on | Budget API tier before architecting research cadence; implement reconnect logic for streaming endpoints |
| Reddit API | `pushshift` deprecated; raw Reddit API throttles heavily on large historical queries | Use official Reddit API with proper OAuth; rate-limit research to avoid 429s; cache results in PostgreSQL |
| Claude API | Every candidate market routed to Claude, runaway token costs | Gate LLM calls behind pre-screening filters (liquidity, XGBoost edge > threshold); enforce daily token budget with hard kill switch |
| Google Trends | Rate limits enforced informally but not documented; IP bans for high-frequency scraping | Use `pytrends` with delays between requests (60+ seconds); cache trend data in PostgreSQL; do not query per-market-scan cycle |

---

## Performance Traps

Patterns that work at small scale but fail as usage grows.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Fetching all Kalshi markets on every scan cycle | Slow scans (>30s), high API load, rate limit hits | Cache market metadata; only re-fetch markets with recent activity or approaching resolution | At ~100+ candidate markets per scan |
| Blocking database writes in async prediction pipeline | Pipeline stalls during market activity spikes | Use async DB drivers (asyncpg); queue writes; never block prediction path on DB I/O | At ~10 concurrent market evaluations |
| Loading full historical signal data per market for feature engineering | Memory exhaustion; scan cycle takes minutes | Pre-compute and cache feature vectors; only compute incremental updates | At ~500+ resolved market history rows per market |
| Synchronous LLM API calls in main prediction loop | Pipeline latency depends on Claude API response time (1-5s per call); misses trading windows | Async LLM calls; enforce timeout; fail-fast if LLM response > N seconds | At >5 markets being evaluated simultaneously |
| Using pandas DataFrames for tick-by-tick order book updates | DataFrame append is O(n); memory grows unbounded | Use deque or ring buffer for streaming order book state | At >1000 order book updates per minute |

---

## Security Mistakes

Domain-specific security issues beyond general web security.

| Mistake | Risk | Prevention |
|---------|------|------------|
| Storing Kalshi API credentials in `.env` file committed to git | Attacker drains trading account | Use a secrets manager (AWS Secrets Manager, HashiCorp Vault) or OS keychain; `.env` in `.gitignore` from day one |
| Kalshi API key with full account permissions (not scoped) | Compromised key can withdraw funds, not just trade | Request minimum-scope API key (trading only, no withdrawal); verify Kalshi offers scoped permissions |
| No request signing or HMAC verification on Kalshi webhooks | Attacker injects fake market resolution events or order confirmations | Verify all webhook payloads using Kalshi's signature header before processing |
| Claude API key stored in application config file | Runaway API costs if key is leaked; no spend control | Use environment variable injection at runtime; set hard monthly spend limits on Anthropic console |
| Trading account credentials logged in plaintext for debugging | Credentials in log aggregation systems (Datadog, CloudWatch) exposed | Sanitize all log messages to never include tokens, passwords, or account identifiers |

---

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- [ ] **Edge detection:** 4% threshold is implemented — but verify that p_model is calibrated probability, not raw XGBoost score. If XGBoost is not calibrated, the edge calculation is invalid.
- [ ] **Kelly sizing:** Formula is implemented — but verify that hard max position cap (independent of Kelly output) is enforced AND that fractional alpha cannot be set above 0.5 by config error.
- [ ] **Drawdown circuit breaker:** Check `if drawdown > 0.08` is in the code — but verify the check runs on fresh account data (not cached), runs before every order, and is covered by an automated test that simulates 8.1% drawdown.
- [ ] **Backtesting system:** Shows P&L and Sharpe — but verify no look-ahead bias exists. Check that features are computed with only data available at decision time, not the full history slice.
- [ ] **Kalshi API client:** Makes successful requests in testing — but verify token refresh logic exists, 429 responses trigger exponential backoff, and 401 responses halt the trading process.
- [ ] **Model performance tracking:** Brier score is logged — but verify that a calibration reliability diagram is generated periodically, not just the aggregate score.
- [ ] **Trade execution:** Orders are placed and confirmed — but verify partial fill handling exists (Kalshi orders may partially fill), and the bot correctly tracks open exposure for partial positions.
- [ ] **PostgreSQL storage:** Schema exists and inserts work — but verify all resolved trades store the actual settlement price from Kalshi (not inferred from YES/NO outcome), and that market halt status is tracked.
- [ ] **Research pipeline:** Sentiment signals are generated — but verify source credibility weighting is applied before aggregation, and anomalous volume spikes are flagged rather than amplified.

---

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Live trading reveals uncalibrated XGBoost probabilities | MEDIUM | Halt trading. Collect resolved trades as calibration data. Apply Platt scaling on validation set. Re-deploy with calibrated model. Review last N trades for systematic bias direction. |
| Look-ahead bias discovered in backtest | HIGH | Backtest results are invalid — discard them entirely. Audit every feature computation function for temporal leakage. Rebuild backtest with strict timestamp enforcement. Re-run from scratch. |
| Kelly overbetting caused >5% drawdown in first week | HIGH | Halt immediately. Reduce alpha to 0.25 or lower. Investigate if edge threshold 4% was producing false positives. Require 2+ independent signal confirmation before resuming. |
| Circuit breaker bypassed by exception; runaway trading | CRITICAL | Emergency halt (stop process, cancel all open orders on Kalshi). Audit exception handling in execution path. Implement external watchdog before restarting. Treat as a production incident. |
| Kalshi API token expiry caused silent trading halt | LOW | Implement token refresh (documented above). Restart bot with refresh manager in place. Verify no missed trades affected open positions. |
| Claude API runaway costs ($500+ unexpected bill) | LOW-MEDIUM | Rotate API key. Implement pre-screening gate immediately. Set hard daily token budget. Audit which markets were triggering LLM calls and why. |
| Model drift — 30-day Brier score degrades 25%+ | MEDIUM | Freeze trading until model is retrained. Collect last 90 days of resolved markets. Retrain on fresh data. Compare out-of-sample Brier score before re-deploying. |

---

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Uncalibrated XGBoost probabilities | Probability model build | Calibration reliability diagram shows near-diagonal curve; Brier score < 0.25 on held-out set |
| Backtesting look-ahead bias | Backtesting system build | Automated assertion: `all(feature_ts < decision_ts)` for every row in training data |
| Kelly overbetting / edge overestimation | Risk management build | Unit test: position size never exceeds hard cap regardless of Kelly output; paper trading shows realized edge within 20% of predicted |
| Missing hard circuit breaker | Risk management build | Integration test: simulate 8.1% drawdown, verify zero orders placed; watchdog process tested independently |
| Kalshi API token expiry | Kalshi API integration (early phase) | Test: force token to expire mid-session, verify refresh happens and trading continues |
| LLM output as ground truth | Probability model integration | LLM is gated behind pre-screening filter; ensemble weight assigned by empirical validation, not assumption |
| Social media signal contamination | Research signal pipeline | Source credibility weighting in code; spike detection test with synthetic manipulation data |
| Contract resolution ambiguity | Trade execution + P&L tracking | P&L code reads settlement price from API, not inferred from outcome; test with non-binary settlement value |
| Model stagnation / no retraining | Learning loop / model improvement phase | Retraining trigger alert fires in staging when Brier score threshold is crossed; model version stored per trade |

---

## Sources

- Kalshi API Rate Limits documentation: https://docs.kalshi.com/getting_started/rate_limits
- XGBoost probability calibration (XGBoosting): https://xgboosting.com/predict-calibrated-probabilities-with-xgboost/
- Common prediction market trading mistakes (Whales Market): https://whales.market/blog/common-mistakes-on-prediction-market/
- Backtesting pitfalls for algo traders (QuantStart): https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-I/
- Kelly Criterion implementation pitfalls (QuantStart): https://www.quantstart.com/articles/Money-Management-via-the-Kelly-Criterion/
- TradeTrap — LLM trading agent reliability: https://arxiv.org/html/2512.02261v1
- Concurrent agentic AI systems (DEV Community): https://dev.to/yeahiasarker/how-to-build-concurrent-agentic-ai-systems-without-losing-control-5ag0
- Kalshi settlement disputes and Khamenei market: https://bettingscanner.com/prediction-markets/news/kalshi-khamenei-market-payout-backlash-explained
- Prediction market liquidity constraints (Finance Magnates): https://www.financemagnates.com/fintech/prediction-markets-scale-up-as-volumes-surge-but-regulation-and-liquidity-remain-key-constraints/
- NLP sentiment signal pitfalls (LuxAlgo): https://www.luxalgo.com/blog/nlp-in-trading-can-news-and-tweets-predict-prices/
- Claude API cost management (CloudZero): https://www.cloudzero.com/blog/finops-for-claude/
- Model drift detection in trading (QuantInsti): https://blog.quantinsti.com/autoregressive-drift-detection-method/
- Systemic failures in algorithmic trading: https://pmc.ncbi.nlm.nih.gov/articles/PMC8978471/
- Backtesting common errors (ForTraders): https://www.fortraders.com/blog/10-backtesting-mistakes-in-trading
- Kalshi trading bot engineering guide (Alphascope): https://www.alphascope.app/blog/kalshi-api

---
*Pitfalls research for: AI-powered prediction market trading bot (Kalshi)*
*Researched: 2026-03-09*
