"""
Tests for KellySizer — fractional Kelly position sizing with cap enforcement.

Covers SIZE-01 through SIZE-03:
  SIZE-01: f* = (p_model * b - q) / b  (full Kelly)
  SIZE-02: f = alpha * f*  (fractional Kelly)
  SIZE-03: f = min(f, max_single_bet)  (position cap)
"""

import pytest

from pmtb.decision.models import RejectionReason, TradeDecision
from pmtb.decision.sizer import KellySizer


def _approved_decision(
    p_model: float,
    p_market: float,
    ticker: str = "TEST-001",
) -> TradeDecision:
    """Create a pre-approved TradeDecision (as if it came from EdgeDetector)."""
    b = (1.0 - p_market) / p_market
    ev = p_model * b - (1.0 - p_model)
    edge = p_model - p_market
    return TradeDecision(
        ticker=ticker,
        cycle_id="cycle-001",
        approved=True,
        side="yes",
        p_model=p_model,
        p_market=p_market,
        edge=edge,
        ev=ev,
    )


class TestKellyFormula:
    """Test full Kelly fraction f* computation (SIZE-01)."""

    def test_kelly_formula(self):
        """SIZE-01: f* = (p*b - q) / b with p=0.70, p_mkt=0.60."""
        # b = (1 - 0.60) / 0.60 = 0.6667
        # q = 1 - 0.70 = 0.30
        # f* = (0.70 * 0.6667 - 0.30) / 0.6667 = (0.4667 - 0.30) / 0.6667 = 0.25
        # alpha=0.25 -> f = 0.0625; max_single_bet=1.0 (no cap) -> kelly_f = 0.0625
        sizer = KellySizer(kelly_alpha=0.25, max_single_bet=1.0, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.70, p_market=0.60)
        result = sizer.size(decision)
        # f_star ~= 0.25; alpha=0.25 -> kelly_f = 0.0625
        assert result.kelly_f == pytest.approx(0.25 * 0.25, rel=1e-3)  # alpha * f_star

    def test_kelly_formula_raw_f_star(self):
        """Verify the raw f* value is 0.25 with alpha=1.0."""
        sizer = KellySizer(kelly_alpha=1.0, max_single_bet=1.0, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.70, p_market=0.60)
        result = sizer.size(decision)
        b = (1.0 - 0.60) / 0.60
        q = 1.0 - 0.70
        f_star = (0.70 * b - q) / b
        assert result.kelly_f == pytest.approx(f_star, rel=1e-3)


class TestFractionalKelly:
    """Test fractional Kelly alpha scaling (SIZE-02)."""

    def test_fractional_kelly_alpha_025(self):
        """SIZE-02: alpha=0.25, f*=0.25 -> f=0.0625."""
        sizer = KellySizer(kelly_alpha=0.25, max_single_bet=1.0, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.70, p_market=0.60)
        result = sizer.size(decision)
        assert result.kelly_f == pytest.approx(0.0625, rel=1e-3)

    def test_fractional_kelly_alpha_050(self):
        """SIZE-02: alpha=0.50, f*=0.25 -> f=0.125."""
        sizer = KellySizer(kelly_alpha=0.50, max_single_bet=1.0, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.70, p_market=0.60)
        result = sizer.size(decision)
        assert result.kelly_f == pytest.approx(0.125, rel=1e-3)


class TestPositionCap:
    """Test max_single_bet cap enforcement (SIZE-03)."""

    def test_position_cap_applies(self):
        """SIZE-03: f=0.40 > max_single_bet=0.05 -> f capped at 0.05."""
        # Need high edge to get f > 0.40 before cap
        # p_model=0.95, p_market=0.50: b=1.0, q=0.05, f*=(0.95-0.05)/1.0=0.90
        # alpha=0.50 -> f=0.45, capped to 0.05
        sizer = KellySizer(kelly_alpha=0.50, max_single_bet=0.05, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.95, p_market=0.50)
        result = sizer.size(decision)
        assert result.kelly_f == pytest.approx(0.05)

    def test_position_cap_not_applied_when_under(self):
        """Cap does not apply when fractional Kelly is already below max_single_bet."""
        # p=0.70, p_mkt=0.60: f_star=0.25, alpha=0.25 -> f=0.0625 > 0.05 -> cap applies
        # Use very small alpha to ensure f < max_single_bet
        sizer = KellySizer(kelly_alpha=0.10, max_single_bet=0.05, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.70, p_market=0.60)
        result = sizer.size(decision)
        # f_star=0.25, alpha=0.10 -> f=0.025 < max_single_bet=0.05 -> no cap
        assert result.kelly_f == pytest.approx(0.025, rel=1e-3)
        assert result.kelly_f < 0.05


class TestKellyNegativeRejection:
    """Test rejection when f* <= 0 (unfavorable bet)."""

    def test_kelly_negative_rejected(self):
        """p_model=0.30, p_market=0.60 -> negative f* -> rejected with KELLY_NEGATIVE."""
        sizer = KellySizer(kelly_alpha=0.25, max_single_bet=0.05, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.30, p_market=0.60)
        result = sizer.size(decision)
        assert result.approved is False
        assert result.rejection_reason == RejectionReason.KELLY_NEGATIVE

    def test_kelly_zero_rejected(self):
        """f*=0 (breakeven) -> rejected with KELLY_NEGATIVE."""
        # p_model == p_market -> edge=0, f*=0
        sizer = KellySizer(kelly_alpha=0.25, max_single_bet=0.05, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.60, p_market=0.60)
        # Manually set edge to 0 since helper would compute positive
        decision = decision.model_copy(update={"p_model": 0.60, "p_market": 0.60, "edge": 0.0})
        result = sizer.size(decision)
        assert result.approved is False
        assert result.rejection_reason == RejectionReason.KELLY_NEGATIVE


class TestQuantityComputation:
    """Test contract quantity computation from fractional Kelly."""

    def test_quantity_computation(self):
        """f=0.05, portfolio_value=10000 -> dollar_amount=500 -> quantity=500 contracts."""
        # Ensure f will be capped at exactly max_single_bet=0.05
        sizer = KellySizer(kelly_alpha=0.50, max_single_bet=0.05, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.95, p_market=0.50)
        result = sizer.size(decision)
        # dollar_amount = 0.05 * 10000 = 500; contracts at $1 each = 500
        assert result.quantity == 500

    def test_minimum_one_contract(self):
        """Very small f and portfolio -> quantity floored at 1."""
        # alpha=0.01, f_star~0.25 -> f~0.0025, portfolio=100 -> $0.25 -> floor to 1
        sizer = KellySizer(kelly_alpha=0.01, max_single_bet=1.0, portfolio_value=100)
        decision = _approved_decision(p_model=0.70, p_market=0.60)
        result = sizer.size(decision)
        assert result.quantity == 1

    def test_quantity_is_integer(self):
        """Quantity is always an integer (contracts are whole units)."""
        sizer = KellySizer(kelly_alpha=0.25, max_single_bet=0.05, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.70, p_market=0.60)
        result = sizer.size(decision)
        assert isinstance(result.quantity, int)

    def test_original_decision_fields_preserved(self):
        """size() returns a copy with updated fields; original decision fields preserved."""
        sizer = KellySizer(kelly_alpha=0.25, max_single_bet=0.05, portfolio_value=10_000)
        decision = _approved_decision(p_model=0.70, p_market=0.60)
        result = sizer.size(decision)
        assert result.ticker == decision.ticker
        assert result.cycle_id == decision.cycle_id
        assert result.p_model == decision.p_model
        assert result.p_market == decision.p_market
        assert result.side == decision.side
