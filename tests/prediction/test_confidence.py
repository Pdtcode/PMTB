"""
Tests for confidence interval computation.
"""
from __future__ import annotations

import pytest

from pmtb.prediction.confidence import compute_confidence_interval


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------


def test_basic_ci_returns_tuple():
    result = compute_confidence_interval(0.5)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_basic_ci_symmetrical():
    low, high = compute_confidence_interval(0.5, half_width=0.1)
    assert low == pytest.approx(0.4)
    assert high == pytest.approx(0.6)


def test_ci_default_half_width():
    low, high = compute_confidence_interval(0.5)
    # Default half_width=0.1
    assert low == pytest.approx(0.4)
    assert high == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Boundary clamping
# ---------------------------------------------------------------------------


def test_ci_clamps_high_above_1():
    low, high = compute_confidence_interval(0.95, half_width=0.1)
    assert low == pytest.approx(0.85)
    assert high == pytest.approx(1.0)  # not 1.05


def test_ci_clamps_low_below_0():
    low, high = compute_confidence_interval(0.05, half_width=0.1)
    assert low == pytest.approx(0.0)  # not -0.05
    assert high == pytest.approx(0.15)


def test_ci_at_zero():
    low, high = compute_confidence_interval(0.0, half_width=0.1)
    assert low == pytest.approx(0.0)
    assert high == pytest.approx(0.1)


def test_ci_at_one():
    low, high = compute_confidence_interval(1.0, half_width=0.1)
    assert low == pytest.approx(0.9)
    assert high == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Output always in [0, 1]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("p", [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0])
def test_ci_always_in_unit_interval(p: float):
    low, high = compute_confidence_interval(p, half_width=0.15)
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert low <= high


def test_ci_wide_half_width_clamped():
    # half_width=0.5 should still clamp within [0, 1]
    low, high = compute_confidence_interval(0.5, half_width=0.5)
    assert low == pytest.approx(0.0)
    assert high == pytest.approx(1.0)
