"""
Tests for XGBoostPredictor.

RED phase: these tests should fail before implementation.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest
from sklearn.datasets import make_classification
from sklearn.metrics import brier_score_loss


def _make_synthetic_data(n_samples: int = 300, n_features: int = 13, nan_fraction: float = 0.15):
    """
    Generate synthetic binary classification data with injected NaN values.

    n_samples >= 200 required for CalibratedClassifierCV with cv=5 (40 per fold).
    nan_fraction: proportion of values to set to NaN (simulates missing signal sources).
    """
    X, y = make_classification(
        n_samples=n_samples,
        n_features=n_features,
        n_informative=8,
        n_redundant=3,
        n_repeated=0,
        random_state=42,
    )
    X = X.astype(np.float64)
    # Inject NaN values to simulate missing signal sources
    rng = np.random.RandomState(42)
    nan_mask = rng.random(X.shape) < nan_fraction
    X[nan_mask] = float("nan")
    return X, y.astype(np.float64)


class TestXGBoostPredictorInitialState:
    def test_is_ready_false_before_training(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib"
            )
            assert predictor.is_ready is False

    def test_model_version_contains_shadow_before_training(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib"
            )
            assert "shadow" in predictor.model_version.lower()

    def test_model_version_contains_xgb_before_training(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib"
            )
            assert "xgb" in predictor.model_version.lower()


class TestXGBoostPredictorTraining:
    def test_train_succeeds_with_nan_values(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
                min_training_samples=100,
            )
            # Should not raise
            metrics = predictor.train(X, y)
            assert isinstance(metrics, dict)

    def test_train_returns_brier_scores(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
            )
            metrics = predictor.train(X, y)
            assert "brier_calibrated" in metrics
            assert "brier_raw" in metrics

    def test_is_ready_true_after_training(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
            )
            predictor.train(X, y)
            assert predictor.is_ready is True

    def test_model_version_contains_xgb_after_training(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
            )
            predictor.train(X, y)
            assert "xgb" in predictor.model_version

    def test_model_version_no_shadow_after_training(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
            )
            predictor.train(X, y)
            assert "shadow" not in predictor.model_version

    def test_train_raises_insufficient_samples(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data(n_samples=50)
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
                min_training_samples=100,
            )
            with pytest.raises(ValueError, match="training samples"):
                predictor.train(X, y)

    def test_calibration_improves_or_equals_brier_score(self):
        """
        Calibrated Brier score <= raw Brier score on synthetic test data.

        Using >= 300 samples to give CalibratedClassifierCV cv=5 enough data.
        """
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data(n_samples=300)
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
            )
            metrics = predictor.train(X, y)
            # Calibrated Brier score should be <= raw (lower is better)
            assert metrics["brier_calibrated"] <= metrics["brier_raw"] + 0.05  # small tolerance


class TestXGBoostPredictorPredict:
    def test_predict_returns_float_in_range(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
            )
            predictor.train(X, y)
            x_test = X[:1]
            result = predictor.predict(x_test)
            assert isinstance(result, float)
            assert 0.0 < result < 1.0

    def test_shadow_predict_returns_nan(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
            )
            result = predictor.shadow_predict(X[:1])
            assert math.isnan(result)

    def test_shadow_predict_returns_nan_after_training(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
            )
            predictor.train(X, y)
            result = predictor.shadow_predict(X[:1])
            assert math.isnan(result)


class TestXGBoostPredictorPersistence:
    def test_save_writes_file_to_disk(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "subdir" / "model.joblib"
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(model_path=model_path)
            predictor.train(X, y)
            predictor.save()
            assert model_path.exists()

    def test_load_sets_is_ready_true(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.joblib"
            X, y = _make_synthetic_data()
            # Train and save
            predictor1 = XGBoostPredictor(model_path=model_path)
            predictor1.train(X, y)
            predictor1.save()
            # Load fresh predictor
            predictor2 = XGBoostPredictor(model_path=model_path)
            assert predictor2.is_ready is False
            result = predictor2.load()
            assert result is True
            assert predictor2.is_ready is True

    def test_load_returns_false_if_no_file(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "nonexistent.joblib"
            predictor = XGBoostPredictor(model_path=model_path)
            result = predictor.load()
            assert result is False

    def test_predict_same_value_before_and_after_save_load(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.joblib"
            X, y = _make_synthetic_data()
            x_test = X[:1]

            # Train and predict
            predictor1 = XGBoostPredictor(model_path=model_path)
            predictor1.train(X, y)
            pred_before = predictor1.predict(x_test)
            predictor1.save()

            # Load and predict
            predictor2 = XGBoostPredictor(model_path=model_path)
            predictor2.load()
            pred_after = predictor2.predict(x_test)

            assert abs(pred_before - pred_after) < 1e-9


class TestXGBoostPredictorCalibrationMethod:
    def test_isotonic_calibration_method_accepted(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
                calibration_method="isotonic",
            )
            # Should not raise
            predictor.train(X, y)
            assert predictor.is_ready is True

    def test_calibration_method_in_model_version(self):
        from pmtb.prediction.xgboost_model import XGBoostPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            X, y = _make_synthetic_data()
            predictor = XGBoostPredictor(
                model_path=Path(tmpdir) / "model.joblib",
                calibration_method="sigmoid",
            )
            predictor.train(X, y)
            assert "sigmoid" in predictor.model_version
