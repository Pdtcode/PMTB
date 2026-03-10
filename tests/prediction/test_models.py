"""
Tests for PredictionResult Pydantic model.

RED phase: these tests should fail before implementation.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pmtb.prediction.models import PredictionResult


class TestPredictionResultValidation:
    """PredictionResult field constraints."""

    def _valid_kwargs(self) -> dict:
        return {
            "ticker": "TICKER-YES",
            "cycle_id": "cycle-001",
            "p_model": 0.65,
            "confidence_low": 0.55,
            "confidence_high": 0.75,
            "model_version": "xgb-v1",
            "used_llm": False,
        }

    def test_valid_prediction_result(self):
        pr = PredictionResult(**self._valid_kwargs())
        assert pr.ticker == "TICKER-YES"
        assert pr.cycle_id == "cycle-001"
        assert pr.p_model == 0.65
        assert pr.confidence_low == 0.55
        assert pr.confidence_high == 0.75
        assert pr.model_version == "xgb-v1"
        assert pr.used_llm is False

    def test_is_shadow_defaults_false(self):
        pr = PredictionResult(**self._valid_kwargs())
        assert pr.is_shadow is False

    def test_signal_weights_defaults_none(self):
        pr = PredictionResult(**self._valid_kwargs())
        assert pr.signal_weights is None

    def test_signal_weights_can_be_set(self):
        kwargs = self._valid_kwargs()
        kwargs["signal_weights"] = {"reddit": 0.4, "rss": 0.3}
        pr = PredictionResult(**kwargs)
        assert pr.signal_weights == {"reddit": 0.4, "rss": 0.3}

    def test_used_llm_defaults_false(self):
        kwargs = self._valid_kwargs()
        del kwargs["used_llm"]
        pr = PredictionResult(**kwargs)
        assert pr.used_llm is False

    def test_p_model_boundary_zero(self):
        kwargs = self._valid_kwargs()
        kwargs["p_model"] = 0.0
        pr = PredictionResult(**kwargs)
        assert pr.p_model == 0.0

    def test_p_model_boundary_one(self):
        kwargs = self._valid_kwargs()
        kwargs["p_model"] = 1.0
        pr = PredictionResult(**kwargs)
        assert pr.p_model == 1.0

    def test_p_model_below_zero_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["p_model"] = -0.01
        with pytest.raises(ValidationError):
            PredictionResult(**kwargs)

    def test_p_model_above_one_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["p_model"] = 1.01
        with pytest.raises(ValidationError):
            PredictionResult(**kwargs)

    def test_confidence_low_below_zero_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["confidence_low"] = -0.1
        with pytest.raises(ValidationError):
            PredictionResult(**kwargs)

    def test_confidence_high_above_one_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["confidence_high"] = 1.1
        with pytest.raises(ValidationError):
            PredictionResult(**kwargs)

    def test_confidence_low_boundary(self):
        kwargs = self._valid_kwargs()
        kwargs["confidence_low"] = 0.0
        pr = PredictionResult(**kwargs)
        assert pr.confidence_low == 0.0

    def test_confidence_high_boundary(self):
        kwargs = self._valid_kwargs()
        kwargs["confidence_high"] = 1.0
        pr = PredictionResult(**kwargs)
        assert pr.confidence_high == 1.0

    def test_is_shadow_can_be_true(self):
        kwargs = self._valid_kwargs()
        kwargs["is_shadow"] = True
        pr = PredictionResult(**kwargs)
        assert pr.is_shadow is True
