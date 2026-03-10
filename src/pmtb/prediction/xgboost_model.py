"""
XGBoost prediction model with CalibratedClassifierCV calibration and joblib persistence.

XGBoostPredictor — wraps XGBClassifier with:
    - NaN-native training (XGBoost handles missing values natively via `missing` param)
    - CalibratedClassifierCV for probability calibration (sigmoid or isotonic)
    - joblib save/load for model persistence
    - Shadow mode — returns float("nan") when not trained (logs without executing)

Design decisions:
- XGBClassifier(missing=float("nan")) tells XGBoost to treat NaN as missing,
  using its internal gain-based imputation. No pre-imputation needed.
- use_label_encoder removed — deprecated/removed in XGBoost 2.0+.
- CalibratedClassifierCV cv=5 requires >= 5 samples per fold — enforce
  min_training_samples >= 5*2 = 10 at minimum (plan requires 100 default).
- model_version encodes calibration method and timestamp for audit trail.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss
from xgboost import XGBClassifier


class XGBoostPredictor:
    """
    XGBoost binary classifier with probability calibration and joblib persistence.

    Usage:
        predictor = XGBoostPredictor(model_path=Path("models/xgb_calibrated.joblib"))
        metrics = predictor.train(X_train, y_train)
        prob = predictor.predict(X_test[:1])
        predictor.save()

        # Later:
        predictor2 = XGBoostPredictor(model_path=Path("models/xgb_calibrated.joblib"))
        predictor2.load()
        prob = predictor2.predict(X_test[:1])
    """

    def __init__(
        self,
        model_path: Path,
        min_training_samples: int = 100,
        calibration_method: str = "sigmoid",
    ) -> None:
        self._model: CalibratedClassifierCV | None = None
        self._model_path = Path(model_path)
        self._min_training_samples = min_training_samples
        self._calibration_method = calibration_method
        self._is_trained = False
        self._train_timestamp: str | None = None

    @property
    def is_ready(self) -> bool:
        """True if the model has been trained or loaded from disk."""
        return self._is_trained and self._model is not None

    @property
    def model_version(self) -> str:
        """
        Human-readable model version string.

        Before training: "shadow-xgb-v0"
        After training:  "xgb-v1-{calibration_method}-{train_timestamp}"
        """
        if not self._is_trained or self._train_timestamp is None:
            return "shadow-xgb-v0"
        return f"xgb-v1-{self._calibration_method}-{self._train_timestamp}"

    def train(self, X: np.ndarray, y: np.ndarray) -> dict[str, float]:
        """
        Train a calibrated XGBoost classifier.

        Args:
            X: Feature matrix of shape (n_samples, n_features). NaN values are allowed.
            y: Binary label vector of shape (n_samples,). Values should be 0 or 1.

        Returns:
            dict with keys "brier_calibrated" and "brier_raw" for logging.

        Raises:
            ValueError: If len(y) < min_training_samples.
        """
        n_samples = len(y)
        if n_samples < self._min_training_samples:
            raise ValueError(
                f"Insufficient training samples: {n_samples} < {self._min_training_samples}. "
                f"Requires at least {self._min_training_samples} training samples."
            )

        # Raw XGBClassifier for computing pre-calibration Brier score
        raw_clf = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            eval_metric="logloss",
            missing=float("nan"),
            random_state=42,
        )
        raw_clf.fit(X, y)
        y_prob_raw = raw_clf.predict_proba(X)[:, 1]
        brier_raw = float(brier_score_loss(y, y_prob_raw))

        # Calibrated classifier
        base_clf = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            eval_metric="logloss",
            missing=float("nan"),
            random_state=42,
        )
        calibrated = CalibratedClassifierCV(
            estimator=base_clf,
            method=self._calibration_method,
            cv=5,
        )
        calibrated.fit(X, y)
        y_prob_calibrated = calibrated.predict_proba(X)[:, 1]
        brier_calibrated = float(brier_score_loss(y, y_prob_calibrated))

        self._model = calibrated
        self._is_trained = True
        self._train_timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")

        return {
            "brier_calibrated": brier_calibrated,
            "brier_raw": brier_raw,
        }

    def predict(self, X: np.ndarray) -> float:
        """
        Predict calibrated YES probability.

        Args:
            X: Feature matrix of shape (1, n_features). NaN values are allowed.

        Returns:
            float in (0, 1) — calibrated probability of YES outcome.

        Raises:
            AssertionError: If model is not trained.
        """
        assert self._model is not None, "Model not trained. Call train() or load() first."
        proba = self._model.predict_proba(X)
        return float(proba[0, 1])

    def shadow_predict(self, X: np.ndarray) -> float:
        """
        Shadow mode prediction — always returns NaN.

        Used when model is not yet ready for execution (insufficient training data).
        The caller records this for future labeling without acting on it.

        Args:
            X: Feature matrix (ignored).

        Returns:
            float("nan") — always.
        """
        return float("nan")

    def save(self) -> None:
        """
        Persist the calibrated model to disk via joblib.

        Creates parent directories if needed.

        Raises:
            AssertionError: If model is not trained.
        """
        assert self._model is not None, "Cannot save: model not trained."
        self._model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._model, self._model_path, compress=3)

    def load(self) -> bool:
        """
        Load a previously saved model from disk.

        Returns:
            True if model loaded successfully, False if file does not exist.
        """
        if not self._model_path.exists():
            return False
        self._model = joblib.load(self._model_path)
        self._is_trained = True
        # Restore a generic timestamp for loaded models (no original timestamp available)
        if self._train_timestamp is None:
            self._train_timestamp = "loaded"
        return True
