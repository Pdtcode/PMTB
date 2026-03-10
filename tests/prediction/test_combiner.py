"""
Tests for probability combining strategies.
"""
from __future__ import annotations

import pytest

from pmtb.prediction.combiner import (
    combine_estimates,
    combine_log_odds,
    combine_weighted_average,
)


# ---------------------------------------------------------------------------
# combine_log_odds
# ---------------------------------------------------------------------------


def test_log_odds_returns_float_in_open_interval():
    result = combine_log_odds(0.6, 0.7)
    assert 0.0 < result < 1.0


def test_log_odds_result_differs_from_both_inputs():
    p_xgb = 0.6
    p_claude = 0.7
    result = combine_log_odds(p_xgb, p_claude)
    assert result != pytest.approx(p_xgb)
    assert result != pytest.approx(p_claude)


def test_log_odds_clips_extreme_inputs():
    # Should not raise, even with boundary values
    result_low = combine_log_odds(0.0, 0.5)
    result_high = combine_log_odds(1.0, 0.5)
    assert 0.0 < result_low < 1.0
    assert 0.0 < result_high < 1.0


def test_log_odds_respects_weights():
    # Heavy XGBoost weighting should pull result towards p_xgb
    result_xgb_heavy = combine_log_odds(0.8, 0.2, weight_xgb=0.9, weight_claude=0.1)
    result_claude_heavy = combine_log_odds(0.8, 0.2, weight_xgb=0.1, weight_claude=0.9)
    assert result_xgb_heavy > result_claude_heavy


def test_log_odds_default_weights():
    result = combine_log_odds(0.6, 0.4)
    # With default weights 0.6/0.4, result should be between inputs
    assert 0.0 < result < 1.0


# ---------------------------------------------------------------------------
# combine_weighted_average
# ---------------------------------------------------------------------------


def test_weighted_average_basic():
    result = combine_weighted_average(0.6, 0.4, 0.5, 0.5)
    assert result == pytest.approx(0.5)


def test_weighted_average_asymmetric():
    result = combine_weighted_average(0.8, 0.2, 0.6, 0.4)
    assert result == pytest.approx(0.6 * 0.8 + 0.4 * 0.2)


def test_weighted_average_clamped_low():
    # Should clamp to 0.0 if result would be negative
    result = combine_weighted_average(-0.5, -0.5, 0.5, 0.5)
    assert result == pytest.approx(0.0)


def test_weighted_average_clamped_high():
    # Should clamp to 1.0 if result would exceed 1.0
    result = combine_weighted_average(1.5, 1.5, 0.5, 0.5)
    assert result == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# combine_estimates — dispatch
# ---------------------------------------------------------------------------


def test_combine_estimates_log_odds_dispatch():
    result = combine_estimates(0.6, 0.7, method="log_odds")
    assert 0.0 < result < 1.0


def test_combine_estimates_weighted_avg_dispatch():
    result = combine_estimates(0.6, 0.4, method="weighted_average", weight_xgb=0.5, weight_claude=0.5)
    assert result == pytest.approx(0.5)


def test_combine_estimates_cold_start_no_xgb():
    # Only Claude estimate — return it directly
    result = combine_estimates(None, 0.65, method="log_odds")
    assert result == pytest.approx(0.65)


def test_combine_estimates_claude_skipped_no_claude():
    # Only XGBoost estimate — return it directly
    result = combine_estimates(0.72, None, method="log_odds")
    assert result == pytest.approx(0.72)


def test_combine_estimates_both_none_raises():
    with pytest.raises(ValueError, match="[Aa]t least one"):
        combine_estimates(None, None)


def test_combine_estimates_unknown_method_raises():
    with pytest.raises(ValueError, match="[Uu]nknown method"):
        combine_estimates(0.5, 0.5, method="invalid_method")
