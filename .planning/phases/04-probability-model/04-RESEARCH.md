# Phase 4: Probability Model - Research

**Researched:** 2026-03-10
**Domain:** XGBoost probability calibration, LLM probability estimation, Bayesian combination, sklearn calibration
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **Cold Start Strategy:** Claude-only mode at launch. XGBoost runs in shadow mode during cold start — predictions recorded but not used for trading. Transition threshold to hybrid mode is configurable.
- **Claude Integration:** Structured analysis prompt (title, close time, current price, signal summaries) → Claude returns JSON `{p_estimate, confidence, reasoning, key_factors}`. Calibration system prompt included. Model tier configurable (Sonnet default, Haiku/Opus allowed).
- **Claude Gating:** XGBoost confidence band 0.4–0.6 triggers Claude calls once hybrid mode is active. Cold start gating is configurable.
- **PredictionResult minimum fields:** p_model, confidence_low, confidence_high, model_version, used_llm. Compatible with ModelOutput DB schema.
- **Model persistence:** joblib; load on startup, retrain from DB if missing or stale.
- **No schema changes:** ModelOutput DB model already exists with all needed fields.

### Claude's Discretion

- Transition threshold for cold start → hybrid mode
- XGBoost feature engineering beyond base SignalBundle features
- Combining strategy (Bayesian update, weighted average, or Claude-override) — make configurable
- Confidence interval computation method — make configurable
- PredictionResult fields beyond the minimum
- Cold start gating strategy for Claude API calls
- Calibration method selection (Platt/sigmoid vs isotonic vs auto-select by dataset size)
- Recalibration trigger and frequency

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PRED-01 | XGBoost binary classifier generates base probability estimates from market features and research signals | XGBClassifier with sklearn wrapper, `to_features()` + MarketCandidate metadata as input |
| PRED-02 | XGBoost probabilities calibrated using Platt scaling or isotonic regression (not raw predict_proba) | CalibratedClassifierCV with `method='sigmoid'` (small data) or `'isotonic'` (larger data) |
| PRED-03 | Claude API provides structured probability reasoning for markets routed through LLM analysis | Follow SentimentClassifier pattern: AsyncAnthropic, structured JSON output, async method |
| PRED-04 | LLM analysis gated — only markets with XGBoost confidence in 0.4–0.6 band get Claude calls | Confidence band check before Claude call; configurable in Settings |
| PRED-05 | Bayesian updating layer incorporates prior and signal evidence to produce final p_model | Log-odds Bayesian update or configurable weighted average combining XGBoost + Claude estimates |
| PRED-06 | Model outputs typed PredictionResult with p_model, confidence interval, contributing signal weights | Pydantic model; confidence interval via bootstrap or propagation method |
| PRED-07 | All predictions persisted to PostgreSQL with model version and timestamp | Async SQLAlchemy session writes to existing ModelOutput table |
</phase_requirements>

---

## Summary

Phase 4 builds the probability estimation layer that sits between the research signal pipeline (Phase 3) and the edge detection/sizing layer (Phase 5). Given a `SignalBundle` (8-feature dict of NaN-padded sentiment signals) and a `MarketCandidate` (market metadata), the module produces a calibrated `PredictionResult` and persists a `ModelOutput` DB row.

The cold start architecture is the central design challenge: no resolved trade history exists at launch, so Claude acts as the sole estimator while XGBoost predictions are recorded in shadow mode for future training. Once a configurable number of resolved outcomes accumulates, XGBoost enters hybrid mode as the primary estimator, with Claude gated to the 0.4–0.6 confidence band.

XGBoost's native NaN handling eliminates the need for explicit imputation of missing signals. CalibratedClassifierCV wraps the trained model and calibrates raw `predict_proba` outputs using Platt scaling (sigmoid) for small datasets or isotonic regression for larger ones. The calibrated probability, optionally combined with a Claude estimate, passes through a combining layer before being wrapped in a `PredictionResult` Pydantic model and written to the database.

**Primary recommendation:** Use `XGBClassifier(missing=float('nan'))` + `CalibratedClassifierCV(method='sigmoid', cv=5)` for cold-start shadow training, `AsyncAnthropic` following the existing `SentimentClassifier` pattern for Claude calls, and log-odds Bayesian combination as the default strategy (configurable). Persist the full sklearn Pipeline (XGBClassifier + calibrator) via `joblib.dump`.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| xgboost | 3.2.0 (stable) | Binary classifier producing `predict_proba` | Handles NaN natively (no imputation needed), sklearn wrapper compatible with CalibratedClassifierCV |
| scikit-learn | >=1.3 | `CalibratedClassifierCV`, `Brier score`, train/test split | Standard calibration API, same dependency chain as XGBoost sklearn wrapper |
| joblib | (bundled with sklearn) | Model persistence: `joblib.dump` / `joblib.load` | Sklearn-recommended, handles large numpy arrays efficiently |
| anthropic | >=0.84.0 (already in pyproject.toml) | `AsyncAnthropic` client for Claude probability estimates | Already used by SentimentClassifier, lazy-import pattern established |
| pydantic | v2 (already in project) | `PredictionResult` typed contract between phases | Project-wide contract pattern |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| numpy | (sklearn dependency) | NaN handling, bootstrap CI computation | During CI computation and feature array construction |
| scipy | (optional) | Beta distribution CI alternative | If implementing closed-form CI; not required |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| CalibratedClassifierCV | LightGBM's built-in calibration | LightGBM not in stack; XGBoost + sklearn is consistent |
| joblib.dump | xgboost.Booster.save_model() | XGBoost native format loses calibrator wrapper; joblib saves the whole sklearn Pipeline including calibrator |
| Log-odds Bayesian combine | Simple weighted average | Weighted average is linear in probability space (miscalibrated at extremes); log-odds respects the logistic structure of probabilities |
| Bootstrap CI | Beta distribution CI | Bootstrap more general, no distributional assumption needed for small N |

**Installation:**
```bash
# XGBoost is not yet in pyproject.toml — add to dependencies
uv add xgboost scikit-learn
# joblib is bundled with scikit-learn; no separate install needed
```

---

## Architecture Patterns

### Recommended Project Structure

```
src/pmtb/
├── prediction/
│   ├── __init__.py
│   ├── models.py          # PredictionResult Pydantic model
│   ├── xgboost_model.py   # XGBoostPredictor: train, shadow_predict, predict, calibrate
│   ├── llm_predictor.py   # ClaudePredictor: structured probability estimation
│   ├── combiner.py        # combining strategies (log-odds Bayesian, weighted avg, override)
│   ├── confidence.py      # CI computation methods
│   └── pipeline.py        # ProbabilityPipeline: orchestrates cold-start vs hybrid flow
tests/
└── prediction/
    ├── __init__.py
    ├── test_models.py
    ├── test_xgboost_model.py
    ├── test_llm_predictor.py
    ├── test_combiner.py
    └── test_pipeline.py
```

### Pattern 1: XGBClassifier + CalibratedClassifierCV Pipeline

**What:** Wrap XGBClassifier in CalibratedClassifierCV so `predict_proba` outputs are calibrated probabilities rather than raw scores.

**When to use:** Any time XGBoost is trained with sufficient resolved outcomes (configurable threshold).

**Example:**
```python
# Source: https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV

base_model = xgb.XGBClassifier(
    n_estimators=100,
    max_depth=4,
    use_label_encoder=False,
    eval_metric="logloss",
    missing=float("nan"),  # NaN signals from to_features() handled natively
)

# sigmoid (Platt scaling) recommended for small datasets (<1000 samples)
# isotonic preferred for larger datasets
calibrated = CalibratedClassifierCV(
    estimator=base_model,
    method="sigmoid",   # configurable: "sigmoid" | "isotonic"
    cv=5,
)

calibrated.fit(X_train, y_train)

# Returns calibrated probability of outcome=True
proba = calibrated.predict_proba(X_new)[:, 1]
```

### Pattern 2: Shadow Mode During Cold Start

**What:** XGBoost makes predictions on every market even before training data exists. Shadow predictions are recorded to `ModelOutput` with `model_version="shadow-xgb-v0"` (or similar). When the training threshold is met, the shadow predictions are replaced by calibrated predictions.

**When to use:** During cold start (no resolved outcomes yet).

**Example:**
```python
class XGBoostPredictor:
    def __init__(self, model_path: Path, min_training_samples: int = 100):
        self._model: CalibratedClassifierCV | None = None
        self._model_path = model_path
        self._min_training_samples = min_training_samples
        self._is_trained = False

    @property
    def is_ready(self) -> bool:
        return self._is_trained

    def load(self) -> None:
        """Load from disk if model file exists and is not stale."""
        if self._model_path.exists():
            self._model = joblib.load(self._model_path)
            self._is_trained = True

    def shadow_predict(self, X: np.ndarray) -> float:
        """Return NaN — no estimate available yet, for DB recording only."""
        return float("nan")

    def predict(self, X: np.ndarray) -> float:
        """Return calibrated probability. Only call if is_ready."""
        assert self._model is not None
        return float(self._model.predict_proba(X.reshape(1, -1))[:, 1])
```

### Pattern 3: Claude Probability Estimation

**What:** Follow the existing `SentimentClassifier._call_claude()` pattern — `AsyncAnthropic`, structured JSON output. The calibration system prompt instructs Claude to avoid rounding probabilities to common anchors (0.5, 0.25, 0.75).

**When to use:** Cold start (all markets) or hybrid mode when XGBoost confidence band falls in 0.4–0.6.

**Example:**
```python
# Follows pattern from src/pmtb/research/sentiment.py
SYSTEM_PROMPT = (
    "You are a calibrated probabilistic forecaster for prediction markets. "
    "Avoid anchoring probabilities to round numbers (0.5, 0.25, 0.75). "
    "Consider base rates carefully. Express genuine uncertainty — do not "
    "force confidence when the evidence is thin. Return only JSON."
)

async def _call_claude(self, market: MarketCandidate, bundle: SignalBundle) -> dict:
    prompt = (
        f"Market: {market.title}\n"
        f"Closes: {market.close_time.isoformat()}\n"
        f"Current implied probability: {market.implied_probability:.3f}\n"
        f"Research signals: {bundle.model_dump(exclude={'ticker', 'cycle_id'})}\n\n"
        "Respond with JSON only:\n"
        '{"p_estimate": 0.0-1.0, "confidence": 0.0-1.0, '
        '"reasoning": "2-3 sentences", "key_factors": ["factor1", "factor2"]}'
    )
    message = await self._client.messages.create(
        model=self._model,          # configurable: haiku / sonnet / opus
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(message.content[0].text)
```

### Pattern 4: Log-Odds Bayesian Combination

**What:** Combine XGBoost calibrated probability and Claude probability in log-odds space. Treats XGBoost as the posterior prior and Claude as a likelihood update.

**When to use:** Hybrid mode when both estimators have produced a prediction.

**Example:**
```python
import math

def combine_log_odds(
    p_xgb: float,
    p_claude: float,
    weight_xgb: float = 0.6,
    weight_claude: float = 0.4,
) -> float:
    """Weighted log-odds combination — respects probability boundaries."""
    # Clip to avoid log(0)
    eps = 1e-6
    p_xgb = max(eps, min(1 - eps, p_xgb))
    p_claude = max(eps, min(1 - eps, p_claude))

    logit_xgb = math.log(p_xgb / (1 - p_xgb))
    logit_claude = math.log(p_claude / (1 - p_claude))

    combined_logit = weight_xgb * logit_xgb + weight_claude * logit_claude
    return 1.0 / (1.0 + math.exp(-combined_logit))
```

### Pattern 5: Bootstrap Confidence Interval

**What:** For inference-time CI, use XGBoost's individual tree predictions to compute variance. Simpler alternative: propagate the calibrator's uncertainty.

**When to use:** Whenever a `PredictionResult` is produced.

**Example:**
```python
def compute_ci_from_trees(
    model: CalibratedClassifierCV,
    X: np.ndarray,
    ci_width: float = 0.95,
) -> tuple[float, float]:
    """
    Estimate CI from spread of individual tree predictions.
    Falls back to fixed half-width if model not ensemble-based.
    """
    import numpy as np
    # XGBoost with staged_predict or tree margin variance
    # Simpler: use ±std_dev of calibration fold predictions
    # or fixed heuristic (p ± 0.1) as configurable fallback
    alpha = (1 - ci_width) / 2
    # Bootstrap or tree-level variance implementation here
    mean_p = float(model.predict_proba(X.reshape(1, -1))[:, 1])
    half_width = 0.05  # configurable fallback
    return max(0.0, mean_p - half_width), min(1.0, mean_p + half_width)
```

### Pattern 6: Model Persistence via joblib

**What:** Save the complete sklearn Pipeline (XGBClassifier + CalibratedClassifierCV) to disk. Reload on startup; retrain if file missing or staleness threshold exceeded.

```python
import joblib
from pathlib import Path

MODEL_PATH = Path("models/xgb_calibrated.joblib")

def save_model(model: CalibratedClassifierCV, path: Path = MODEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path, compress=3)

def load_model(path: Path = MODEL_PATH) -> CalibratedClassifierCV | None:
    if path.exists():
        return joblib.load(path)
    return None
```

### Anti-Patterns to Avoid

- **Using raw XGBoost predict_proba without calibration:** XGBoost's raw probabilities are systematically over-confident near 0 and 1. Always wrap with CalibratedClassifierCV before use.
- **Imputing NaN signals to 0 (neutral):** The project decision is that NaN means "no data", not neutral sentiment. XGBoost with `missing=float('nan')` handles this correctly. Do NOT impute.
- **Calling Claude for all markets regardless of XGBoost confidence:** Claude calls are expensive. Gate behind the 0.4–0.6 confidence band check in hybrid mode.
- **Combining probabilities with simple arithmetic average in probability space:** Use log-odds combination instead — arithmetic averaging is miscalibrated at probability extremes.
- **Hard-coding model version string:** The `model_version` field in `ModelOutput` should be derived from a configured constant or git hash to enable reproducibility tracking.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Probability calibration | Custom sigmoid fitter | `CalibratedClassifierCV` | Cross-validated calibration, handles edge cases, Brier score validation built-in |
| NaN imputation in features | Custom imputer | XGBoost native `missing=float('nan')` | XGBoost learns optimal direction for missing values during training — imputation destroys this signal |
| Model file serialization | Custom pickle scheme | `joblib.dump` / `joblib.load` | Handles large numpy arrays efficiently, sklearn-recommended approach |
| Brier score evaluation | Custom MSE computation | `sklearn.metrics.brier_score_loss` | Standard implementation, verified edge cases |
| Train/test split | Custom array slicing | `sklearn.model_selection.train_test_split` | Stratified split for imbalanced binary outcomes |

**Key insight:** The sklearn calibration ecosystem (`CalibratedClassifierCV`, `brier_score_loss`, `calibration_curve`) handles all the numerical edge cases in probability calibration. Do not replicate this logic.

---

## Common Pitfalls

### Pitfall 1: Calibration Leakage
**What goes wrong:** Training XGBoost on the full dataset and then calibrating on the same data — calibration parameters overfit.
**Why it happens:** Missing the `cv` parameter logic in `CalibratedClassifierCV`; using `cv='prefit'` with the full training set.
**How to avoid:** Use `CalibratedClassifierCV(cv=5)` (not `cv='prefit'`) so calibration folds are held out from the base model's training.
**Warning signs:** Calibration curve looks perfect in-sample but degrades on held-out data.

### Pitfall 2: XGBoost Model Incompatibility After joblib Save
**What goes wrong:** Loading a joblib-saved XGBoost model with a different xgboost version causes errors or silent miscalibration.
**Why it happens:** XGBoost's internal booster format may change across versions.
**How to avoid:** Pin xgboost version in pyproject.toml. Log the model's training timestamp and xgboost version in the `model_version` string.
**Warning signs:** Load succeeds but predict_proba returns unexpected values.

### Pitfall 3: Cold Start Data Scarcity
**What goes wrong:** Attempting to train XGBoost with fewer than ~50–100 resolved binary outcomes results in an underfit model that performs worse than Claude alone.
**Why it happens:** The configurable `min_training_samples` threshold is set too low.
**How to avoid:** Default to `min_training_samples=100` with a comment explaining the rationale. Keep XGBoost in shadow mode until threshold is met.
**Warning signs:** XGBoost calibrated probability hovers near 0.5 regardless of signal strength.

### Pitfall 4: Claude Anchoring on Implied Probability
**What goes wrong:** Claude receives the current Kalshi implied_probability in the prompt and anchors its estimate to the market price, reducing the model's independence from p_market.
**Why it happens:** Including `implied_probability` in the prompt without a calibration instruction to avoid anchoring.
**How to avoid:** Include explicit system prompt instruction: "Do not anchor to the current market price. Your estimate should be based on fundamentals and signals only."
**Warning signs:** Claude estimates correlate too closely with implied_probability (Pearson r > 0.9).

### Pitfall 5: Confidence Interval Reversal at Extremes
**What goes wrong:** A simple symmetric CI (p ± half_width) produces `confidence_low < 0` or `confidence_high > 1`.
**Why it happens:** No boundary clipping applied.
**How to avoid:** Always `max(0.0, low)` and `min(1.0, high)` after computing CI. Use log-odds CI for better behavior at extremes.
**Warning signs:** ModelOutput rows with confidence_low < 0 or confidence_high > 1 in the DB.

### Pitfall 6: Missing market_id Lookup
**What goes wrong:** Writing `ModelOutput` rows requires a `market_id` (UUID FK to `markets` table), but `MarketCandidate` only has a `ticker`.
**Why it happens:** Missing a DB lookup step to resolve ticker → market UUID before persistence.
**How to avoid:** Implement a `get_or_create_market()` helper (same pattern used in Phase 3's pipeline) that looks up or inserts the market before writing `ModelOutput`.
**Warning signs:** FK constraint violation on model_outputs.market_id insert.

---

## Code Examples

### XGBoost Feature Array Construction

```python
# Source: SignalBundle.to_features() in src/pmtb/research/models.py + MarketCandidate fields
import numpy as np
from datetime import datetime, UTC

def build_feature_vector(
    bundle: SignalBundle,
    market: MarketCandidate,
) -> np.ndarray:
    """Build combined feature array from signal bundle + market metadata."""
    signal_feats = bundle.to_features()  # 8 keys, NaN for missing sources

    now = datetime.now(UTC)
    hours_to_close = max(0.0, (market.close_time - now).total_seconds() / 3600)

    meta_feats = {
        "implied_prob": market.implied_probability,
        "spread": market.spread,
        "volume_24h": market.volume_24h,
        "hours_to_close": hours_to_close,
        "volatility_score": market.volatility_score if market.volatility_score is not None else float("nan"),
    }

    combined = {**signal_feats, **meta_feats}
    # Keep consistent key order for reproducible feature matrix
    keys = sorted(combined.keys())
    return np.array([combined[k] for k in keys], dtype=np.float64)
```

### Brier Score Evaluation

```python
# Source: https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html
from sklearn.metrics import brier_score_loss

# Lower is better (0.0 = perfect, 0.25 = random/uninformative)
brier_calibrated = brier_score_loss(y_test, proba_calibrated)
brier_raw = brier_score_loss(y_test, proba_raw)

assert brier_calibrated < brier_raw, (
    f"Calibration did not improve Brier score: "
    f"raw={brier_raw:.4f} calibrated={brier_calibrated:.4f}"
)
```

### PredictionResult Pydantic Model

```python
# src/pmtb/prediction/models.py — Claude designs this to match Phase 5 needs
from __future__ import annotations
from pydantic import BaseModel, Field

class PredictionResult(BaseModel):
    """
    Output contract from Phase 4 to Phase 5 (edge detection, Kelly sizing).

    Required by ModelOutput DB schema: p_model, confidence_low, confidence_high,
    signal_weights, model_version, used_llm, cycle_id.
    p_market is populated by Phase 5 from the live Kalshi orderbook.
    """
    ticker: str
    cycle_id: str
    p_model: float = Field(ge=0.0, le=1.0)
    confidence_low: float = Field(ge=0.0, le=1.0)
    confidence_high: float = Field(ge=0.0, le=1.0)
    signal_weights: dict[str, float] | None = None
    model_version: str
    used_llm: bool = False
    # Shadow-mode flag — if True, this prediction should NOT be used for trading
    is_shadow: bool = False
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Raw XGBoost predict_proba for trading | CalibratedClassifierCV wrapping | Standard practice since sklearn 0.18 | Brier scores typically improve 10–30% on financial binary classification |
| Pickle for sklearn model persistence | joblib.dump with compress | sklearn >=0.18 | Efficient for large numpy arrays, officially recommended |
| Calling LLM for every prediction | Confidence-band gating (0.4–0.6) | Best practice for cost-controlled LLM integration | 60–80% reduction in API calls vs unconstrained |
| use_label_encoder=True in XGBClassifier | use_label_encoder removed/deprecated | XGBoost >=1.6 | Removed in newer versions; do not pass this parameter |

**Deprecated/outdated:**
- `use_label_encoder=False` kwarg: Was required in XGBoost ~1.3–1.5 to suppress warnings. Removed in XGBoost 2.0+ — do not pass this parameter with xgboost>=2.0.
- `XGBClassifier.get_booster().save_model()` for saving calibrated models: Loses the calibrator layer. Use `joblib.dump` for the full `CalibratedClassifierCV` object.

---

## Open Questions

1. **Category encoding for XGBoost**
   - What we know: `MarketCandidate.category` is a string (e.g., "politics", "sports"). XGBoost does not natively accept strings.
   - What's unclear: Whether a simple ordinal encoder or one-hot encoding is better given the number of categories in Kalshi markets.
   - Recommendation: Implement ordinal encoding initially (keeps feature dimensionality low); make the encoding approach configurable.

2. **Recalibration trigger threshold**
   - What we know: Model performance degrades as market dynamics shift. `workflow.nyquist_validation` requires test coverage.
   - What's unclear: Whether to trigger on elapsed time (e.g., weekly) or Brier score degradation.
   - Recommendation: Implement both: refit if `model_age_days > 7` OR if `brier_rolling_7d > brier_initial * 1.15`. Make configurable.

3. **Shadow mode DB writes and data volume**
   - What we know: Shadow predictions write `ModelOutput` rows with `model_version="shadow-*"`. At high scan frequency, this could generate significant data volume.
   - What's unclear: Whether shadow rows should be partitioned or pruned.
   - Recommendation: Keep shadow rows for now (Phase 7 backtesting uses them). Add a `is_shadow` boolean to `PredictionResult` but store all rows in `model_outputs` with a distinguishable `model_version` prefix.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/prediction/ -x -q` |
| Full suite command | `pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PRED-01 | XGBClassifier trains on feature matrix with NaN values | unit | `pytest tests/prediction/test_xgboost_model.py::test_train_with_nan_features -x` | ❌ Wave 0 |
| PRED-02 | CalibratedClassifierCV Brier score improves over raw predict_proba | unit | `pytest tests/prediction/test_xgboost_model.py::test_calibration_improves_brier -x` | ❌ Wave 0 |
| PRED-03 | ClaudePredictor returns valid JSON with p_estimate in [0,1] | unit (mocked) | `pytest tests/prediction/test_llm_predictor.py::test_claude_returns_valid_json -x` | ❌ Wave 0 |
| PRED-04 | Claude NOT called when XGBoost confidence outside 0.4–0.6 band | unit | `pytest tests/prediction/test_pipeline.py::test_llm_gating_outside_confidence_band -x` | ❌ Wave 0 |
| PRED-05 | Log-odds combiner produces p in (0,1), not equal to either input when both present | unit | `pytest tests/prediction/test_combiner.py::test_log_odds_combine -x` | ❌ Wave 0 |
| PRED-06 | PredictionResult has all required fields and validates constraints | unit | `pytest tests/prediction/test_models.py::test_prediction_result_fields -x` | ❌ Wave 0 |
| PRED-07 | ModelOutput row persisted with correct market_id and model_version | integration (DB) | `pytest tests/prediction/test_pipeline.py::test_persist_model_output -x -m "not demo"` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/prediction/ -x -q`
- **Per wave merge:** `pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/prediction/__init__.py` — package init
- [ ] `tests/prediction/test_models.py` — covers PRED-06
- [ ] `tests/prediction/test_xgboost_model.py` — covers PRED-01, PRED-02
- [ ] `tests/prediction/test_llm_predictor.py` — covers PRED-03
- [ ] `tests/prediction/test_combiner.py` — covers PRED-05
- [ ] `tests/prediction/test_pipeline.py` — covers PRED-04, PRED-07
- [ ] Framework install: `uv add xgboost scikit-learn` — xgboost not yet in pyproject.toml

---

## Sources

### Primary (HIGH confidence)

- [scikit-learn CalibratedClassifierCV docs](https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html) — calibration API, isotonic vs sigmoid guidance
- [scikit-learn Probability Calibration guide](https://scikit-learn.org/stable/modules/calibration.html) — dataset size recommendation for sigmoid vs isotonic
- [xgboost 3.2.0 Python API docs](https://xgboost.readthedocs.io/en/stable/python/python_api.html) — XGBClassifier parameters, missing value handling
- [xgboost FAQ — missing values](https://xgboost.readthedocs.io/en/stable/faq.html) — NaN handling in training and inference
- [scikit-learn Model persistence docs](https://scikit-learn.org/stable/model_persistence.html) — joblib recommendation
- Existing codebase: `src/pmtb/research/sentiment.py`, `src/pmtb/research/models.py`, `src/pmtb/db/models.py`, `src/pmtb/scanner/models.py`, `src/pmtb/config.py`

### Secondary (MEDIUM confidence)

- [XGBoosting: Predict Calibrated Probabilities](https://xgboosting.com/predict-calibrated-probabilities-with-xgboost/) — practical CalibratedClassifierCV + XGBoost walkthrough
- [XGBoosting: Confidence Intervals via Bootstrap](https://xgboosting.com/xgboost-confidence-interval-using-bootstrap-and-percentiles/) — bootstrap CI pattern
- [learnprompting.org: Calibrating LLMs](https://learnprompting.org/docs/reliability/calibration) — LLM calibration and anchor avoidance techniques
- [NAACL 2024: Survey of LLM Confidence Calibration](https://aclanthology.org/2024.naacl-long.366.pdf) — current state of LLM calibration research

### Tertiary (LOW confidence)

- Multiple secondary sources on log-odds Bayesian combination pattern (no single canonical Python reference found; the math is standard and well-established)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — XGBoost, sklearn, joblib are the established standard; anthropic client already in project
- Architecture: HIGH — follows established project patterns (Pydantic contracts, AsyncAnthropic, Settings); cold start pattern well-defined in CONTEXT.md
- Pitfalls: HIGH — calibration leakage, NaN imputation, CI boundary clipping are verified documented issues in sklearn and XGBoost official docs
- CI methods: MEDIUM — bootstrap is documented; specific implementation choices for low-data scenarios require validation

**Research date:** 2026-03-10
**Valid until:** 2026-04-10 (stable ML library ecosystem; 30 days)
