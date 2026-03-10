"""
EdgeDetector — pure-math edge and expected value computation.

Implements EDGE-01 through EDGE-04:
  EDGE-01: p_market = MarketCandidate.implied_probability
  EDGE-02: EV = p_model * b - (1 - p_model)  where b = (1 - p_market) / p_market
  EDGE-03: edge = p_model - p_market
  EDGE-04: reject if edge <= threshold

This class is stateless and synchronous — no DB, no async, no I/O.

Limitation (v1): Only YES-side bets are supported. A future version may detect
when betting NO has positive edge (p_model < 1 - p_market).
"""

from __future__ import annotations

from pmtb.decision.models import RejectionReason, TradeDecision
from pmtb.prediction.models import PredictionResult
from pmtb.scanner.models import MarketCandidate


class EdgeDetector:
    """
    Evaluates a (prediction, candidate) pair for positive edge.

    Usage::

        detector = EdgeDetector(edge_threshold=settings.edge_threshold)
        decision = detector.evaluate(prediction, candidate)
        if decision.approved:
            # pass to KellySizer
    """

    def __init__(self, edge_threshold: float) -> None:
        """
        Args:
            edge_threshold: Minimum edge (p_model - p_market) required to approve.
                            Must be strictly exceeded (edge > threshold).
        """
        self.edge_threshold = edge_threshold

    def evaluate(
        self,
        prediction: PredictionResult,
        candidate: MarketCandidate,
    ) -> TradeDecision:
        """
        Compute edge/EV and apply threshold gate.

        Args:
            prediction: Model output with p_model probability.
            candidate:  Scanner output with implied_probability (p_market).

        Returns:
            TradeDecision with approved=True and side='yes' if edge > threshold,
            or approved=False with INSUFFICIENT_EDGE reason otherwise.
        """
        p_model: float = prediction.p_model
        p_market: float = candidate.implied_probability  # EDGE-01

        # Compute binary bet payout ratio b = (1 - p_market) / p_market
        # At p_market=0 there is no meaningful market price — treat b=0 to avoid
        # ZeroDivisionError and let the edge check handle the outcome.
        if p_market > 0.0:
            b = (1.0 - p_market) / p_market
        else:
            b = 0.0

        ev: float = p_model * b - (1.0 - p_model)  # EDGE-02
        edge: float = p_model - p_market  # EDGE-03

        # EDGE-04: reject when edge does not strictly exceed threshold
        if edge <= self.edge_threshold:
            return TradeDecision(
                ticker=prediction.ticker,
                cycle_id=prediction.cycle_id,
                approved=False,
                rejection_reason=RejectionReason.INSUFFICIENT_EDGE,
                p_model=p_model,
                p_market=p_market,
                edge=edge,
                ev=ev,
            )

        return TradeDecision(
            ticker=prediction.ticker,
            cycle_id=prediction.cycle_id,
            approved=True,
            side="yes",  # v1: YES-side bets only
            p_model=p_model,
            p_market=p_market,
            edge=edge,
            ev=ev,
        )
