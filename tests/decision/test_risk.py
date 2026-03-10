"""
Tests for RiskManager.

TDD RED phase — all tests written before implementation.
Tests cover:
  - max_exposure check (RISK-01)
  - max_single_bet check (RISK-02)
  - VaR computation at 95% CI (RISK-03)
  - Drawdown halt via TradingState key (RISK-04)
  - Halt flag in TradingState (RISK-04)
  - Duplicate position detection (RISK-08)
  - Auto-hedge trigger on edge reversal (RISK-07)
"""
from __future__ import annotations

import statistics
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.decision.models import RejectionReason, TradeDecision
from pmtb.decision.tracker import PositionTracker


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_decision(
    ticker: str = "ABC",
    cycle_id: str = "cycle-001",
    approved: bool = True,
    quantity: int = 10,
    p_model: float = 0.70,
    p_market: float = 0.60,
    edge: float = 0.10,
    ev: float = 0.15,
    kelly_f: float = 0.05,
    side: str = "yes",
) -> TradeDecision:
    return TradeDecision(
        ticker=ticker,
        cycle_id=cycle_id,
        approved=approved,
        quantity=quantity,
        p_model=p_model,
        p_market=p_market,
        edge=edge,
        ev=ev,
        kelly_f=kelly_f,
        side=side,
    )


def _make_tracker(
    has_position_result: bool = False,
    total_exposure_result: float = 0.0,
    positions: list | None = None,
) -> AsyncMock:
    """Build a mock PositionTracker with controlled return values."""
    tracker = AsyncMock(spec=PositionTracker)
    tracker.has_position = AsyncMock(return_value=has_position_result)
    tracker.total_exposure = AsyncMock(return_value=total_exposure_result)

    if positions is None:
        positions = []
    tracker.get_all = AsyncMock(return_value=positions)
    return tracker


def _make_session_factory_with_state(state_rows: dict[str, str]) -> MagicMock:
    """
    Build a mock session factory where TradingState queries return rows from state_rows dict.
    session.get(TradingState, key) returns a mock with .value, or None if missing.
    """
    session = AsyncMock()

    async def mock_get(model_cls, key):
        val = state_rows.get(key)
        if val is None:
            return None
        row = MagicMock()
        row.value = val
        return row

    session.get = mock_get

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = session_cm
    return factory


def _build_risk_manager(
    tracker,
    factory,
    *,
    max_exposure: float = 0.80,
    max_single_bet: float = 0.05,
    var_limit: float = 0.20,
    max_drawdown: float = 0.08,
    hedge_shift_threshold: float = 0.03,
    portfolio_value: float = 10000.0,
):
    from pmtb.decision.risk import RiskManager
    return RiskManager(
        tracker=tracker,
        session_factory=factory,
        max_exposure=max_exposure,
        max_single_bet=max_single_bet,
        var_limit=var_limit,
        max_drawdown=max_drawdown,
        hedge_shift_threshold=hedge_shift_threshold,
        portfolio_value=portfolio_value,
    )


# ---------------------------------------------------------------------------
# Tests: max_exposure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_exposure_approves_within_limit():
    """Trade that keeps total exposure under 80% of portfolio is approved."""
    # current exposure 7000, trade cost 10 * 0.60 = 6, new total 7006 < 8000
    tracker = _make_tracker(has_position_result=False, total_exposure_result=7000.0)
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory, portfolio_value=10000.0)
    decision = _make_decision(quantity=10, p_market=0.60)
    result = await rm.check(decision)

    assert result.approved is True


@pytest.mark.asyncio
async def test_max_exposure_blocks_trade():
    """Trade that would push total exposure over 80% is rejected with MAX_EXPOSURE."""
    # current exposure 7995, trade cost 10 * 0.60 = 6, new total 8001 > 8000
    tracker = _make_tracker(has_position_result=False, total_exposure_result=7995.0)
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory, portfolio_value=10000.0)
    decision = _make_decision(quantity=10, p_market=0.60)
    result = await rm.check(decision)

    assert result.approved is False
    assert result.rejection_reason == RejectionReason.MAX_EXPOSURE


# ---------------------------------------------------------------------------
# Tests: max_single_bet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_single_bet_approves_within_limit():
    """Trade whose cost is within 5% of portfolio is approved."""
    # qty=10, price=0.60, cost=6, limit=0.05*1000=50 -> 6 < 50
    tracker = _make_tracker(has_position_result=False, total_exposure_result=0.0)
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory, portfolio_value=1000.0)
    decision = _make_decision(quantity=10, p_market=0.60)
    result = await rm.check(decision)

    assert result.approved is True


@pytest.mark.asyncio
async def test_max_single_bet_limit():
    """Trade whose cost exceeds max_single_bet * portfolio is rejected with MAX_SINGLE_BET."""
    # qty=100, price=0.60, cost=60, limit=0.05*1000=50 -> 60 > 50
    tracker = _make_tracker(has_position_result=False, total_exposure_result=0.0)
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory, portfolio_value=1000.0)
    decision = _make_decision(quantity=100, p_market=0.60)
    result = await rm.check(decision)

    assert result.approved is False
    assert result.rejection_reason == RejectionReason.MAX_SINGLE_BET


# ---------------------------------------------------------------------------
# Tests: VaR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_var_with_fewer_than_2_positions():
    """Single position yields VaR=0.0 — no VaR-based blocking."""
    tracker = _make_tracker(has_position_result=False, total_exposure_result=0.0, positions=[])
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory)
    decision = _make_decision()
    result = await rm.check(decision)

    # Should pass VaR check (VaR=0 when <2 positions)
    assert result.approved is True


@pytest.mark.asyncio
async def test_var_computation_blocks_when_exceeded():
    """With 3 positions having high variance, adding new one may push VaR over limit."""
    from unittest.mock import MagicMock
    from decimal import Decimal as D

    # Create 3 mock positions with spread-out values -> high stdev -> low VaR
    def _pos(qty, price):
        p = MagicMock()
        p.quantity = qty
        p.avg_price = D(str(price))
        return p

    # Values: 100, 200, 300 -> mu=200, sigma~100 -> VaR = 200 - 1.645*100 = 35.5
    # With var_limit=0.20 * 10000 = 2000, not exceeded
    # Use extreme spread: values [10, 500, 990] -> mu~500, sigma~490 -> VaR~500-806 negative
    pos_list = [_pos(1, 10.0), _pos(1, 500.0), _pos(1, 990.0)]
    tracker = _make_tracker(has_position_result=False, total_exposure_result=200.0, positions=pos_list)
    factory = _make_session_factory_with_state({})

    # Set var_limit very low so VaR check triggers
    rm = _build_risk_manager(tracker, factory, var_limit=0.001, portfolio_value=10000.0)
    # Add position with value 100 -> new values [10, 500, 990, 100]
    decision = _make_decision(quantity=100, p_market=0.60)  # cost=60

    result = await rm.check(decision)
    # The max_single_bet check (100*0.60=60, limit=0.05*10000=500) passes
    # max_exposure (200+60=260 < 0.80*10000=8000) passes
    # VaR check: values [10,500,990, new 100*0.60=60]
    # mu = (10+500+990+60)/4 = 390, sigma = stdev([10,500,990,60])
    # var_limit = 0.001 * 10000 = 10
    # VaR = mu - 1.645*sigma (will be large negative, so no VaR block since VaR < 0 means loss > limit)
    # Actually the VaR block is when VaR (at 95%) EXCEEDS var_limit as a loss...
    # VaR here represents expected loss at 95th percentile - when negative, it exceeds limit
    # The implementation should reject when |VaR| > var_limit * portfolio_value
    assert result.rejection_reason == RejectionReason.VAR_EXCEEDED


@pytest.mark.asyncio
async def test_var_passes_with_low_variance():
    """Low-variance portfolio VaR stays within limit — no blocking."""
    from unittest.mock import MagicMock
    from decimal import Decimal as D

    # Positions all with similar values -> low stdev -> VaR within limit
    def _pos(qty, price):
        p = MagicMock()
        p.quantity = qty
        p.avg_price = D(str(price))
        return p

    pos_list = [_pos(10, 0.50), _pos(10, 0.51), _pos(10, 0.52)]
    tracker = _make_tracker(has_position_result=False, total_exposure_result=15.0, positions=pos_list)
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory, var_limit=0.20, portfolio_value=10000.0)
    decision = _make_decision(quantity=10, p_market=0.50)

    result = await rm.check(decision)
    assert result.approved is True


# ---------------------------------------------------------------------------
# Tests: drawdown halt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drawdown_halt_blocks_orders():
    """Peak=10000, current=9100 -> drawdown=9% > 8% -> rejected with DRAWDOWN_HALTED."""
    tracker = _make_tracker(has_position_result=False)
    # Peak stored in DB as 10000, current portfolio_value passed in as 9100
    factory = _make_session_factory_with_state({"peak_portfolio_value": "10000.0"})

    rm = _build_risk_manager(tracker, factory, max_drawdown=0.08, portfolio_value=9100.0)
    decision = _make_decision()
    result = await rm.check(decision)

    assert result.approved is False
    assert result.rejection_reason == RejectionReason.DRAWDOWN_HALTED


@pytest.mark.asyncio
async def test_drawdown_within_limit():
    """Peak=10000, current=9300 -> drawdown=7% < 8% -> approved."""
    tracker = _make_tracker(has_position_result=False, total_exposure_result=0.0)
    factory = _make_session_factory_with_state({"peak_portfolio_value": "10000.0"})

    rm = _build_risk_manager(tracker, factory, max_drawdown=0.08, portfolio_value=9300.0)
    decision = _make_decision()
    result = await rm.check(decision)

    assert result.approved is True


# ---------------------------------------------------------------------------
# Tests: halt flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_halt_flag_blocks_trade():
    """TradingState key='trading_halted' value='true' -> all trades rejected with DRAWDOWN_HALTED."""
    tracker = _make_tracker(has_position_result=False)
    factory = _make_session_factory_with_state({"trading_halted": "true"})

    rm = _build_risk_manager(tracker, factory)
    decision = _make_decision()
    result = await rm.check(decision)

    assert result.approved is False
    assert result.rejection_reason == RejectionReason.DRAWDOWN_HALTED


@pytest.mark.asyncio
async def test_halt_flag_false_allows_trade():
    """TradingState key='trading_halted' value='false' -> not halted by flag."""
    tracker = _make_tracker(has_position_result=False, total_exposure_result=0.0)
    factory = _make_session_factory_with_state({"trading_halted": "false"})

    rm = _build_risk_manager(tracker, factory)
    decision = _make_decision()
    result = await rm.check(decision)

    # May still be approved if other checks pass
    assert result.rejection_reason != RejectionReason.DRAWDOWN_HALTED


# ---------------------------------------------------------------------------
# Tests: duplicate detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_position_blocked():
    """Existing position on 'ABC' -> new trade for 'ABC' rejected with DUPLICATE_POSITION."""
    tracker = _make_tracker(has_position_result=True)  # has_position returns True
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory)
    decision = _make_decision(ticker="ABC")
    result = await rm.check(decision)

    assert result.approved is False
    assert result.rejection_reason == RejectionReason.DUPLICATE_POSITION


@pytest.mark.asyncio
async def test_no_duplicate_approved():
    """No existing position -> duplicate check passes."""
    tracker = _make_tracker(has_position_result=False, total_exposure_result=0.0)
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory)
    decision = _make_decision(ticker="NEW")
    result = await rm.check(decision)

    assert result.rejection_reason != RejectionReason.DUPLICATE_POSITION


# ---------------------------------------------------------------------------
# Tests: auto-hedge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_hedge_trigger():
    """Open position on 'ABC', edge=-0.04 (< -0.03 threshold) -> hedge TradeDecision returned."""
    from pmtb.scanner.models import MarketCandidate
    from pmtb.prediction.models import PredictionResult
    from datetime import datetime, timezone

    tracker = _make_tracker(has_position_result=True)
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory, hedge_shift_threshold=0.03)

    prediction = PredictionResult(
        ticker="ABC",
        cycle_id="cycle-001",
        p_model=0.46,  # model says 46%
        confidence_low=0.40,
        confidence_high=0.52,
        model_version="v1",
    )
    candidate = MarketCandidate(
        ticker="ABC",
        title="Test market",
        category="test",
        event_context={},
        close_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        yes_bid=0.49,
        yes_ask=0.51,
        implied_probability=0.50,  # market says 50%
        spread=0.02,
        volume_24h=1000.0,
    )
    # edge = p_model - implied = 0.46 - 0.50 = -0.04 < -0.03 -> hedge triggers

    result = await rm.check_hedge(prediction, candidate)

    assert result is not None
    assert result.approved is True
    assert result.side == "sell"
    assert result.ticker == "ABC"


@pytest.mark.asyncio
async def test_auto_hedge_no_trigger():
    """Edge=-0.01 (above -0.03 threshold) -> no hedge triggered, returns None."""
    from pmtb.scanner.models import MarketCandidate
    from pmtb.prediction.models import PredictionResult
    from datetime import datetime, timezone

    tracker = _make_tracker(has_position_result=True)
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory, hedge_shift_threshold=0.03)

    prediction = PredictionResult(
        ticker="ABC",
        cycle_id="cycle-001",
        p_model=0.49,  # model says 49%
        confidence_low=0.43,
        confidence_high=0.55,
        model_version="v1",
    )
    candidate = MarketCandidate(
        ticker="ABC",
        title="Test market",
        category="test",
        event_context={},
        close_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        yes_bid=0.49,
        yes_ask=0.51,
        implied_probability=0.50,  # market says 50%
        spread=0.02,
        volume_24h=1000.0,
    )
    # edge = 0.49 - 0.50 = -0.01 > -0.03 -> no hedge

    result = await rm.check_hedge(prediction, candidate)

    assert result is None


@pytest.mark.asyncio
async def test_auto_hedge_no_position_returns_none():
    """No open position for ticker -> check_hedge returns None immediately."""
    from pmtb.scanner.models import MarketCandidate
    from pmtb.prediction.models import PredictionResult
    from datetime import datetime, timezone

    tracker = _make_tracker(has_position_result=False)  # no position
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory)

    prediction = PredictionResult(
        ticker="ABC",
        cycle_id="cycle-001",
        p_model=0.40,
        confidence_low=0.34,
        confidence_high=0.46,
        model_version="v1",
    )
    candidate = MarketCandidate(
        ticker="ABC",
        title="Test market",
        category="test",
        event_context={},
        close_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        yes_bid=0.49,
        yes_ask=0.51,
        implied_probability=0.50,
        spread=0.02,
        volume_24h=1000.0,
    )

    result = await rm.check_hedge(prediction, candidate)
    assert result is None


# ---------------------------------------------------------------------------
# Tests: Prometheus metrics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rejection_increments_prometheus_counter():
    """Each rejection increments the RISK_REJECTIONS counter for that reason."""
    from pmtb.decision.risk import RISK_REJECTIONS

    tracker = _make_tracker(has_position_result=True)  # will trigger DUPLICATE_POSITION
    factory = _make_session_factory_with_state({})

    rm = _build_risk_manager(tracker, factory)
    decision = _make_decision(ticker="ABC")

    before = RISK_REJECTIONS.labels(reason=RejectionReason.DUPLICATE_POSITION.value)._value.get()
    await rm.check(decision)
    after = RISK_REJECTIONS.labels(reason=RejectionReason.DUPLICATE_POSITION.value)._value.get()

    assert after > before
