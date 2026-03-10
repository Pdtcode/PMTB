---
phase: 04-probability-model
plan: 01
subsystem: prediction
tags: [xgboost, scikit-learn, pydantic, calibration, feature-engineering, joblib, numpy]

# Dependency graph
requires:
  - phase: 03-research-signal-pipeline
    provides: SignalBundle with to_features() returning 8 signal features with NaN for missing sources
  - phase: 02-market-scanner
    provides: MarketCandidate with implied_probability, spread, volume_24h, volatility_score
provides:
  - PredictionResult Pydantic model — typed output contract for Phase 5 execution engine
  - build_feature_vector() — merges 8 signal + 5 market metadata features into 13-element numpy array
  - FEATURE_NAMES — sorted list for consistent XGBoost column ordering
  - XGBoostPredictor — train/predict/shadow_predict/save/load with CalibratedClassifierCV
  - Prediction config fields in Settings (10 prediction_* fields)
affects:
  - 04-02-llm-combiner (consumes XGBoostPredictor.predict() and PredictionResult)
  - 04-03-pipeline (orchestrates XGBoostPredictor + LLM combiner into full prediction pipeline)
  - 05-execution-engine (consumes PredictionResult as input contract)

# Tech tracking
tech-stack:
  added:
    - xgboost 3.2.0 (native NaN support via missing=float("nan") parameter)
    - scikit-learn 1.8.0 (CalibratedClassifierCV, brier_score_loss, make_classification for tests)
    - joblib (model persistence with compress=3)
    - scipy 1.17.1 (transitive via scikit-learn)
  patterns:
    - NaN-native feature vectors (no pre-imputation — XGBoost handles natively)
    - CalibratedClassifierCV wrapping base classifier for Platt scaling / isotonic regression
    - Sorted FEATURE_NAMES for reproducible array ordering across training and inference
    - Shadow mode via float("nan") return — prediction logged without execution
    - TDD RED-GREEN cycle with pytest

key-files:
  created:
    - src/pmtb/prediction/__init__.py
    - src/pmtb/prediction/models.py
    - src/pmtb/prediction/features.py
    - src/pmtb/prediction/xgboost_model.py
    - tests/prediction/__init__.py
    - tests/prediction/test_models.py
    - tests/prediction/test_features.py
    - tests/prediction/test_xgboost_model.py
  modified:
    - src/pmtb/config.py (added 10 prediction_* config fields)
    - pyproject.toml (added xgboost, scikit-learn dependencies)

key-decisions:
  - "XGBClassifier(missing=float('nan')) uses XGBoost's native missing-value handling — no pre-imputation needed, preserving information in absence-of-data semantics from Phase 3"
  - "use_label_encoder omitted — deprecated and removed in XGBoost 2.0+, causes error if included"
  - "Brier score comparison: raw XGBClassifier trained on full data vs CalibratedClassifierCV — calibration consistently improves or equals raw on synthetic data"
  - "model_version encodes calibration method and UTC timestamp for audit trail: xgb-v1-sigmoid-20260310T..."
  - "test_reproducible_for_same_inputs uses tolerance of 0.01 for hours_to_close — two sequential datetime.now() calls differ by microseconds"

patterns-established:
  - "NaN propagation pattern: missing sources -> NaN in feature vector -> XGBoost handles natively (consistent with Phase 3 decision)"
  - "TDD cycle: write failing tests -> implement minimal code -> verify green -> commit"
  - "Shadow mode: float('nan') return indicates prediction not yet actionable (model not trained)"

requirements-completed: [PRED-01, PRED-02, PRED-06]

# Metrics
duration: 4min
completed: 2026-03-10
---

# Phase 4 Plan 01: Prediction Types, Feature Builder, and XGBoost Model Summary

**PredictionResult Pydantic contract, 13-feature NaN-native feature builder, and XGBoostPredictor with CalibratedClassifierCV calibration and joblib persistence**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-10T22:00:21Z
- **Completed:** 2026-03-10T22:04:35Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments
- PredictionResult typed output contract matching ModelOutput DB schema — consumed by Phase 5 execution engine
- build_feature_vector() merges SignalBundle.to_features() (8 signal features) with 5 market metadata features into 13-element sorted numpy array; NaN preserved for missing sources and None volatility_score
- XGBoostPredictor wraps XGBClassifier + CalibratedClassifierCV with native NaN handling, shadow mode, joblib save/load, and model_version audit trail
- 50 tests passing across 3 test modules (31 for models + features, 19 for XGBoostPredictor)
- 10 prediction_* config fields added to Settings (calibration method, confidence thresholds, combine method, weights, etc.)

## Task Commits

Each task was committed atomically:

1. **Task 1: PredictionResult, build_feature_vector, prediction config** - `eb458e3` (feat)
2. **Task 2: XGBoostPredictor with calibration and persistence** - `5797101` (feat)

_Note: TDD tasks — tests written first (RED), then implementation (GREEN)._

## Files Created/Modified
- `src/pmtb/prediction/__init__.py` - Empty package init
- `src/pmtb/prediction/models.py` - PredictionResult Pydantic model with [0,1] field constraints
- `src/pmtb/prediction/features.py` - build_feature_vector(), FEATURE_NAMES (13 sorted feature keys)
- `src/pmtb/prediction/xgboost_model.py` - XGBoostPredictor: train/predict/shadow/save/load
- `src/pmtb/config.py` - Added 10 prediction_* Settings fields
- `pyproject.toml` - Added xgboost, scikit-learn dependencies
- `tests/prediction/__init__.py` - Empty test package init
- `tests/prediction/test_models.py` - 15 tests for PredictionResult validation
- `tests/prediction/test_features.py` - 16 tests for feature builder
- `tests/prediction/test_xgboost_model.py` - 19 tests for XGBoostPredictor

## Decisions Made
- XGBClassifier(missing=float("nan")) uses XGBoost's native missing-value handling — no pre-imputation, consistent with Phase 3 NaN-not-neutral semantics
- use_label_encoder omitted — removed in XGBoost 2.0+, causes error if included
- CalibratedClassifierCV trains 5 folds internally, requiring >= 100 samples by default (configurable)
- model_version encodes calibration_method and UTC timestamp for audit trail
- test_reproducible_for_same_inputs uses 0.01 tolerance — datetime.now() differs by microseconds between two sequential calls

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_reproducible_for_same_inputs test tolerance**
- **Found during:** Task 1 (feature builder implementation)
- **Issue:** Test asserted `result1[i] == result2[i]` for all features, but hours_to_close calls datetime.now() inside build_feature_vector() — two sequential calls return slightly different timestamps (microsecond difference)
- **Fix:** Changed exact equality check to `abs(result1[i] - result2[i]) < 0.01` tolerance for the test
- **Files modified:** tests/prediction/test_features.py
- **Verification:** 31/31 tests pass after fix
- **Committed in:** eb458e3 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - test bug)
**Impact on plan:** Necessary correctness fix in test logic. No scope creep.

## Issues Encountered
None beyond the test tolerance fix above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- PredictionResult contract ready for Phase 5 execution engine consumption
- XGBoostPredictor ready for Plan 04-02 (LLM combiner — uses predict() output)
- Plan 04-03 (pipeline orchestrator) can wire XGBoostPredictor into full prediction cycle
- Requires labeled training data before is_ready=True (prediction_min_training_samples=100 default)
- Shadow mode handles cold-start: predictions logged with float("nan") until training threshold reached

---
*Phase: 04-probability-model*
*Completed: 2026-03-10*

## Self-Check: PASSED
- src/pmtb/prediction/models.py — FOUND
- src/pmtb/prediction/features.py — FOUND
- src/pmtb/prediction/xgboost_model.py — FOUND
- .planning/phases/04-probability-model/04-01-SUMMARY.md — FOUND
- Commit eb458e3 — FOUND
- Commit 5797101 — FOUND
