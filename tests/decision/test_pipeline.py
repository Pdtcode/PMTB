"""
Tests for DecisionPipeline orchestrator.

TDD RED phase — all tests written before implementation.
Tests cover:
  - Shadow predictions excluded before pipeline (SHADOW rejection)
  - Full pipeline: approved path (Edge -> Size -> Risk all pass)
  - Rejection at edge gate (INSUFFICIENT_EDGE)
  - Rejection at Kelly sizer (KELLY_NEGATIVE)
  - Rejection at risk gate (DUPLICATE_POSITION)
  - Hedge check for open positions
  - Batch processing (list of predictions -> list of decisions)
  - Prometheus metrics incremented on approvals and rejections
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.decision.models import RejectionReason, TradeDecision
from pmtb.prediction.models import PredictionResult
from pmtb.scanner.models import MarketCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prediction(
    ticker: str = "ABC",
    cycle_id: str = "cycle-001",
    p_model: float = 0.75,
    is_shadow: bool = False,
) -> PredictionResult:
    return PredictionResult(
        ticker=ticker,
        cycle_id=cycle_id,
        p_model=p_model,
        confidence_low=0.65,
        confidence_high=0.85,
        model_version="v1",
        is_shadow=is_shadow,
    )


def _make_candidate(
    ticker: str = "ABC",
    implied_probability: float = 0.60,
) -> MarketCandidate:
    return MarketCandidate(
        ticker=ticker,
        title=f"Test market {ticker}",
        category="test",
        event_context={},
        close_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        yes_bid=implied_probability - 0.01,
        yes_ask=implied_probability + 0.01,
        implied_probability=implied_probability,
        spread=0.02,
        volume_24h=5000.0,
    )


def _make_approved_decision(ticker: str = "ABC", quantity: int = 10) -> TradeDecision:
    return TradeDecision(
        ticker=ticker,
        cycle_id="cycle-001",
        approved=True,
        side="yes",
        quantity=quantity,
        p_model=0.75,
        p_market=0.60,
        edge=0.15,
        ev=0.25,
        kelly_f=0.05,
    )


def _make_rejected_decision(
    ticker: str = "ABC",
    reason: RejectionReason = RejectionReason.INSUFFICIENT_EDGE,
) -> TradeDecision:
    return TradeDecision(
        ticker=ticker,
        cycle_id="cycle-001",
        approved=False,
        rejection_reason=reason,
        p_model=0.62,
        p_market=0.60,
    )


def _build_pipeline(
    edge_decision: TradeDecision | None = None,
    sizer_decision: TradeDecision | None = None,
    risk_decision: TradeDecision | None = None,
    hedge_decision: TradeDecision | None = None,
    has_position: bool = False,
):
    """
    Build a DecisionPipeline with fully mocked sub-components.
    Controls exactly what each gate returns for test isolation.
    """
    from pmtb.decision.pipeline import DecisionPipeline
    from pmtb.decision.edge import EdgeDetector
    from pmtb.decision.sizer import KellySizer
    from pmtb.decision.risk import RiskManager
    from pmtb.decision.tracker import PositionTracker

    edge_detector = MagicMock(spec=EdgeDetector)
    sizer = MagicMock(spec=KellySizer)
    risk_manager = AsyncMock(spec=RiskManager)
    tracker = AsyncMock(spec=PositionTracker)

    if edge_decision is not None:
        edge_detector.evaluate.return_value = edge_decision
    if sizer_decision is not None:
        sizer.size.return_value = sizer_decision
    if risk_decision is not None:
        risk_manager.check = AsyncMock(return_value=risk_decision)
    if hedge_decision is not None:
        risk_manager.check_hedge = AsyncMock(return_value=hedge_decision)
    else:
        risk_manager.check_hedge = AsyncMock(return_value=None)

    tracker.has_position = AsyncMock(return_value=has_position)

    return DecisionPipeline(
        edge_detector=edge_detector,
        sizer=sizer,
        risk_manager=risk_manager,
        tracker=tracker,
    )


# ---------------------------------------------------------------------------
# Tests: shadow prediction filtering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shadow_predictions_excluded():
    """
    PredictionResult with is_shadow=True -> immediately rejected with SHADOW reason.
    Edge detector must never be called.
    """
    from pmtb.decision.pipeline import DecisionPipeline

    pipeline = _build_pipeline(
        edge_decision=_make_approved_decision(),
        sizer_decision=_make_approved_decision(),
        risk_decision=_make_approved_decision(),
    )

    prediction = _make_prediction(is_shadow=True)
    candidate = _make_candidate()

    results = await pipeline.evaluate([prediction], [candidate])

    assert len(results) == 1
    assert results[0].approved is False
    assert results[0].rejection_reason == RejectionReason.SHADOW

    # Edge detector should NOT have been called
    pipeline._edge_detector.evaluate.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: full pipeline approved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_approved():
    """
    p_model=0.75, p_market=0.60, edge=0.15 > 0.04, Kelly positive, no risk violations
    -> approved TradeDecision with quantity set.
    """
    approved = _make_approved_decision(quantity=25)

    pipeline = _build_pipeline(
        edge_decision=_make_approved_decision(quantity=None),
        sizer_decision=_make_approved_decision(quantity=25),
        risk_decision=approved,
    )
    # Need to set edge_decision with approved=True
    pipeline._edge_detector.evaluate.return_value = TradeDecision(
        ticker="ABC", cycle_id="cycle-001", approved=True, side="yes",
        p_model=0.75, p_market=0.60, edge=0.15, ev=0.25,
    )
    pipeline._sizer.size.return_value = approved
    pipeline._risk_manager.check = AsyncMock(return_value=approved)

    prediction = _make_prediction(p_model=0.75)
    candidate = _make_candidate(implied_probability=0.60)

    results = await pipeline.evaluate([prediction], [candidate])

    assert len(results) == 1
    assert results[0].approved is True
    assert results[0].quantity == 25


# ---------------------------------------------------------------------------
# Tests: rejection at edge gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_rejects_at_edge():
    """
    p_model=0.62, p_market=0.60 -> edge=0.02 < 0.04 -> rejected at edge gate.
    Sizer must never be called.
    """
    rejected = _make_rejected_decision(reason=RejectionReason.INSUFFICIENT_EDGE)

    pipeline = _build_pipeline(
        edge_decision=rejected,
    )

    prediction = _make_prediction(p_model=0.62)
    candidate = _make_candidate(implied_probability=0.60)

    results = await pipeline.evaluate([prediction], [candidate])

    assert len(results) == 1
    assert results[0].approved is False
    assert results[0].rejection_reason == RejectionReason.INSUFFICIENT_EDGE

    # Sizer should NOT have been called
    pipeline._sizer.size.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: rejection at Kelly sizer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_rejects_at_kelly():
    """
    Edge passes but Kelly f*<=0 -> rejected at sizer.
    Risk manager must never be called.
    """
    edge_ok = _make_approved_decision()
    edge_ok = edge_ok.model_copy(update={"quantity": None})

    kelly_rejected = _make_rejected_decision(reason=RejectionReason.KELLY_NEGATIVE)

    pipeline = _build_pipeline(
        edge_decision=edge_ok,
        sizer_decision=kelly_rejected,
    )
    pipeline._edge_detector.evaluate.return_value = edge_ok
    pipeline._sizer.size.return_value = kelly_rejected

    prediction = _make_prediction()
    candidate = _make_candidate()

    results = await pipeline.evaluate([prediction], [candidate])

    assert len(results) == 1
    assert results[0].approved is False
    assert results[0].rejection_reason == RejectionReason.KELLY_NEGATIVE

    # Risk manager check should NOT have been called
    pipeline._risk_manager.check.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: rejection at risk gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_rejects_at_risk():
    """
    Edge and Kelly pass but duplicate position exists -> rejected at risk gate.
    """
    edge_ok = _make_approved_decision()
    sized_ok = _make_approved_decision(quantity=10)
    risk_rejected = _make_rejected_decision(reason=RejectionReason.DUPLICATE_POSITION)

    pipeline = _build_pipeline(
        edge_decision=edge_ok,
        sizer_decision=sized_ok,
        risk_decision=risk_rejected,
    )
    pipeline._edge_detector.evaluate.return_value = edge_ok
    pipeline._sizer.size.return_value = sized_ok
    pipeline._risk_manager.check = AsyncMock(return_value=risk_rejected)

    prediction = _make_prediction()
    candidate = _make_candidate()

    results = await pipeline.evaluate([prediction], [candidate])

    assert len(results) == 1
    assert results[0].approved is False
    assert results[0].rejection_reason == RejectionReason.DUPLICATE_POSITION


# ---------------------------------------------------------------------------
# Tests: hedge check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_checks_hedges():
    """
    Open position on ticker, new prediction shows edge reversal
    -> hedge decision returned alongside regular decision.
    """
    # Hedge triggered for ABC
    hedge = TradeDecision(
        ticker="ABC",
        cycle_id="cycle-001",
        approved=True,
        side="sell",
        edge=-0.04,
        p_model=0.46,
        p_market=0.50,
    )

    # Regular pipeline also runs — but let's say edge gate rejects
    edge_rejected = _make_rejected_decision(reason=RejectionReason.INSUFFICIENT_EDGE)

    pipeline = _build_pipeline(
        edge_decision=edge_rejected,
        hedge_decision=hedge,
    )

    prediction = _make_prediction()
    candidate = _make_candidate()

    results = await pipeline.evaluate([prediction], [candidate])

    # Should include both hedge and edge-rejected decisions
    assert any(r.side == "sell" and r.approved for r in results), \
        "Hedge decision should be in results"
    assert any(r.rejection_reason == RejectionReason.INSUFFICIENT_EDGE for r in results), \
        "Edge rejection should be in results"


# ---------------------------------------------------------------------------
# Tests: batch processing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_processes_batch():
    """
    List of 3 predictions + candidates -> returns list of 3 TradeDecisions (mix of results).
    """
    from pmtb.decision.pipeline import DecisionPipeline
    from pmtb.decision.edge import EdgeDetector
    from pmtb.decision.sizer import KellySizer
    from pmtb.decision.risk import RiskManager
    from pmtb.decision.tracker import PositionTracker

    edge_detector = MagicMock(spec=EdgeDetector)
    sizer = MagicMock(spec=KellySizer)
    risk_manager = AsyncMock(spec=RiskManager)
    tracker = AsyncMock(spec=PositionTracker)

    # Prediction 1: approved (shadow=False, edge passes, Kelly passes, risk passes)
    # Prediction 2: edge rejected
    # Prediction 3: shadow

    def edge_side_effect(prediction, candidate):
        if prediction.ticker == "ABC":
            return _make_approved_decision(ticker="ABC")
        elif prediction.ticker == "XYZ":
            return _make_rejected_decision(ticker="XYZ", reason=RejectionReason.INSUFFICIENT_EDGE)
        return _make_approved_decision(ticker=prediction.ticker)

    edge_detector.evaluate.side_effect = edge_side_effect
    sizer.size.return_value = _make_approved_decision(quantity=10)
    risk_manager.check = AsyncMock(return_value=_make_approved_decision(quantity=10))
    risk_manager.check_hedge = AsyncMock(return_value=None)

    pipeline = DecisionPipeline(
        edge_detector=edge_detector,
        sizer=sizer,
        risk_manager=risk_manager,
        tracker=tracker,
    )

    predictions = [
        _make_prediction(ticker="ABC", is_shadow=False),
        _make_prediction(ticker="XYZ", is_shadow=False),
        _make_prediction(ticker="SHADOW_MKT", is_shadow=True),
    ]
    candidates = [
        _make_candidate(ticker="ABC"),
        _make_candidate(ticker="XYZ"),
        _make_candidate(ticker="SHADOW_MKT"),
    ]

    results = await pipeline.evaluate(predictions, candidates)

    assert len(results) == 3

    abc_result = next(r for r in results if r.ticker == "ABC")
    xyz_result = next(r for r in results if r.ticker == "XYZ")
    shadow_result = next(r for r in results if r.ticker == "SHADOW_MKT")

    assert abc_result.approved is True
    assert xyz_result.approved is False
    assert xyz_result.rejection_reason == RejectionReason.INSUFFICIENT_EDGE
    assert shadow_result.approved is False
    assert shadow_result.rejection_reason == RejectionReason.SHADOW


# ---------------------------------------------------------------------------
# Tests: Prometheus metrics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_metrics_approvals():
    """Approved decisions increment DECISION_APPROVALS counter."""
    from pmtb.decision.pipeline import DECISION_APPROVALS

    approved = _make_approved_decision(quantity=10)

    pipeline = _build_pipeline(
        edge_decision=approved,
        sizer_decision=approved,
        risk_decision=approved,
    )
    pipeline._edge_detector.evaluate.return_value = approved
    pipeline._sizer.size.return_value = approved
    pipeline._risk_manager.check = AsyncMock(return_value=approved)

    before = DECISION_APPROVALS._value.get()

    await pipeline.evaluate([_make_prediction()], [_make_candidate()])

    after = DECISION_APPROVALS._value.get()
    assert after > before


@pytest.mark.asyncio
async def test_pipeline_metrics_rejections():
    """Rejected decisions increment DECISION_REJECTIONS counter."""
    from pmtb.decision.pipeline import DECISION_REJECTIONS

    rejected = _make_rejected_decision(reason=RejectionReason.INSUFFICIENT_EDGE)

    pipeline = _build_pipeline(edge_decision=rejected)

    before = DECISION_REJECTIONS.labels(
        reason=RejectionReason.INSUFFICIENT_EDGE.value
    )._value.get()

    await pipeline.evaluate([_make_prediction()], [_make_candidate()])

    after = DECISION_REJECTIONS.labels(
        reason=RejectionReason.INSUFFICIENT_EDGE.value
    )._value.get()
    assert after > before
