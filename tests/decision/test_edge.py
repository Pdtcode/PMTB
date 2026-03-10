"""
Tests for EdgeDetector — pure-math edge/EV computation with threshold gating.

Covers EDGE-01 through EDGE-04:
  EDGE-01: p_market sourced from MarketCandidate.implied_probability
  EDGE-02: EV = p_model * b - (1 - p_model) where b = (1 - p_market) / p_market
  EDGE-03: edge = p_model - p_market
  EDGE-04: reject if edge <= threshold with INSUFFICIENT_EDGE
"""

import pytest

from pmtb.decision.models import RejectionReason, TradeDecision
from pmtb.decision.edge import EdgeDetector
from pmtb.prediction.models import PredictionResult
from pmtb.scanner.models import MarketCandidate


def _make_prediction(p_model: float, ticker: str = "TEST-001") -> PredictionResult:
    return PredictionResult(
        ticker=ticker,
        cycle_id="cycle-001",
        p_model=p_model,
        confidence_low=max(0.0, p_model - 0.05),
        confidence_high=min(1.0, p_model + 0.05),
        model_version="v1",
    )


def _make_candidate(implied_probability: float, ticker: str = "TEST-001") -> MarketCandidate:
    from datetime import datetime, UTC, timedelta

    yes_bid = max(0.0, implied_probability - 0.01)
    yes_ask = min(1.0, implied_probability + 0.01)
    return MarketCandidate(
        ticker=ticker,
        title="Test Market",
        category="test",
        event_context={},
        close_time=datetime.now(UTC) + timedelta(days=7),
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        implied_probability=implied_probability,
        spread=yes_ask - yes_bid,
        volume_24h=100.0,
    )


class TestEdgeDetectorModels:
    """Test TradeDecision and RejectionReason model definitions."""

    def test_trade_decision_model(self):
        """TradeDecision has all required fields with correct types."""
        decision = TradeDecision(
            ticker="TEST-001",
            cycle_id="cycle-001",
            approved=True,
            side="yes",
            edge=0.10,
            ev=0.167,
            kelly_f=None,
            p_model=0.70,
            p_market=0.60,
        )
        assert decision.ticker == "TEST-001"
        assert decision.cycle_id == "cycle-001"
        assert decision.approved is True
        assert decision.rejection_reason is None
        assert decision.side == "yes"
        assert decision.edge == pytest.approx(0.10)
        assert decision.ev == pytest.approx(0.167)

    def test_rejection_reason_enum_values(self):
        """RejectionReason enum has all required rejection types."""
        assert RejectionReason.SHADOW == "shadow"
        assert RejectionReason.INSUFFICIENT_EDGE == "insufficient_edge"
        assert RejectionReason.KELLY_NEGATIVE == "kelly_negative"
        assert RejectionReason.MAX_EXPOSURE == "max_exposure"
        assert RejectionReason.MAX_SINGLE_BET == "max_single_bet"
        assert RejectionReason.DRAWDOWN_HALTED == "drawdown_halted"
        assert RejectionReason.DUPLICATE_POSITION == "duplicate_position"
        assert RejectionReason.VAR_EXCEEDED == "var_exceeded"


class TestEdgeDetectorEvaluate:
    """Test EdgeDetector.evaluate math and gating logic."""

    def test_p_market_from_candidate(self):
        """EDGE-01: p_market is sourced from MarketCandidate.implied_probability."""
        detector = EdgeDetector(edge_threshold=0.04)
        prediction = _make_prediction(0.70)
        candidate = _make_candidate(0.60)
        decision = detector.evaluate(prediction, candidate)
        assert decision.p_market == pytest.approx(0.60)

    def test_ev_computation(self):
        """EDGE-02: EV = p_model * b - (1 - p_model). With p=0.70, p_mkt=0.60."""
        # b = (1 - 0.60) / 0.60 = 0.6667
        # EV = 0.70 * 0.6667 - 0.30 = 0.4667 - 0.30 = 0.1667
        detector = EdgeDetector(edge_threshold=0.04)
        prediction = _make_prediction(0.70)
        candidate = _make_candidate(0.60)
        decision = detector.evaluate(prediction, candidate)
        expected_b = (1.0 - 0.60) / 0.60
        expected_ev = 0.70 * expected_b - (1.0 - 0.70)
        assert decision.ev == pytest.approx(expected_ev, rel=1e-4)

    def test_edge_computation(self):
        """EDGE-03: edge = p_model - p_market. With 0.70 - 0.60 = 0.10."""
        detector = EdgeDetector(edge_threshold=0.04)
        prediction = _make_prediction(0.70)
        candidate = _make_candidate(0.60)
        decision = detector.evaluate(prediction, candidate)
        assert decision.edge == pytest.approx(0.10)

    def test_edge_gate_rejects_below_threshold(self):
        """EDGE-04: edge=0.02 < threshold=0.04 -> rejected with INSUFFICIENT_EDGE."""
        detector = EdgeDetector(edge_threshold=0.04)
        prediction = _make_prediction(0.62)
        candidate = _make_candidate(0.60)
        decision = detector.evaluate(prediction, candidate)
        assert decision.approved is False
        assert decision.rejection_reason == RejectionReason.INSUFFICIENT_EDGE

    def test_edge_gate_rejects_at_threshold(self):
        """edge <= threshold -> rejected (boundary is exclusive: must be strictly above)."""
        # Set threshold equal to computed edge value to test the boundary.
        # p_model=0.70, p_market=0.60: computed_edge = 0.70 - 0.60 (floating point result)
        prediction = _make_prediction(0.70)
        candidate = _make_candidate(0.60)
        computed_edge = prediction.p_model - candidate.implied_probability
        # Threshold set exactly equal to computed edge: should be rejected
        detector = EdgeDetector(edge_threshold=computed_edge)
        decision = detector.evaluate(prediction, candidate)
        assert decision.approved is False
        assert decision.rejection_reason == RejectionReason.INSUFFICIENT_EDGE

    def test_edge_gate_passes_above_threshold(self):
        """EDGE-04: edge=0.10 > threshold=0.04 -> approved=True with side='yes'."""
        detector = EdgeDetector(edge_threshold=0.04)
        prediction = _make_prediction(0.70)
        candidate = _make_candidate(0.60)
        decision = detector.evaluate(prediction, candidate)
        assert decision.approved is True
        assert decision.side == "yes"
        assert decision.rejection_reason is None

    def test_edge_zero_p_market(self):
        """p_market=0 -> b=0, handled gracefully without ZeroDivisionError."""
        from datetime import datetime, UTC, timedelta

        detector = EdgeDetector(edge_threshold=0.04)
        prediction = _make_prediction(0.70)
        candidate = MarketCandidate(
            ticker="TEST-001",
            title="Test Market",
            category="test",
            event_context={},
            close_time=datetime.now(UTC) + timedelta(days=7),
            yes_bid=0.0,
            yes_ask=0.0,
            implied_probability=0.0,
            spread=0.0,
            volume_24h=100.0,
        )
        # Should not raise
        decision = detector.evaluate(prediction, candidate)
        assert isinstance(decision, TradeDecision)
        assert decision.p_market == pytest.approx(0.0)

    def test_approved_decision_has_model_context(self):
        """Approved decisions include p_model and p_market for downstream use."""
        detector = EdgeDetector(edge_threshold=0.04)
        prediction = _make_prediction(0.70)
        candidate = _make_candidate(0.60)
        decision = detector.evaluate(prediction, candidate)
        assert decision.p_model == pytest.approx(0.70)
        assert decision.p_market == pytest.approx(0.60)

    def test_ticker_and_cycle_id_propagated(self):
        """Ticker and cycle_id are copied from inputs to TradeDecision."""
        detector = EdgeDetector(edge_threshold=0.04)
        prediction = _make_prediction(0.70, ticker="MKTX-999")
        candidate = _make_candidate(0.60, ticker="MKTX-999")
        decision = detector.evaluate(prediction, candidate)
        assert decision.ticker == "MKTX-999"
        assert decision.cycle_id == "cycle-001"
