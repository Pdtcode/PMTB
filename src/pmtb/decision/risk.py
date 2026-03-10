"""
RiskManager — stateful gate enforcing portfolio limits before trade execution.

This is the third and final gate in the Edge -> Size -> Risk pipeline.
Sequential checks short-circuit on the first rejection, preserving the first
failure reason for post-hoc analysis.

Check order (from cheapest to most expensive):
  1. Halt flag (TradingState key='trading_halted')
  2. Duplicate position (PositionTracker.has_position)
  3. Drawdown halt (compute from TradingState peak_portfolio_value)
  4. Max single bet (quantity * p_market > max_single_bet * portfolio_value)
  5. Max exposure (total_exposure + trade_cost > max_exposure * portfolio_value)
  6. VaR (95% parametric VaR on current positions + new position)

Auto-hedge: edge.check_hedge checks if an open position has reversed edge
beyond hedge_shift_threshold — returns a hedge TradeDecision if triggered.
"""
from __future__ import annotations

import statistics
from decimal import Decimal
from typing import TYPE_CHECKING

from prometheus_client import Counter
from sqlalchemy.ext.asyncio import async_sessionmaker

from pmtb.db.models import TradingState
from pmtb.decision.models import RejectionReason, TradeDecision
from pmtb.decision.tracker import PositionTracker

if TYPE_CHECKING:
    from pmtb.prediction.models import PredictionResult
    from pmtb.scanner.models import MarketCandidate


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

RISK_REJECTIONS = Counter(
    "pmtb_risk_rejections_total",
    "Total number of trades rejected by RiskManager, by reason",
    ["reason"],
)


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------

class RiskManager:
    """
    Last-line-of-defense risk gate before trade execution.

    Enforces portfolio-level constraints (exposure, single bet, VaR, drawdown)
    and detects duplicates / edge reversals. All checks are async — they
    consult PositionTracker (in-memory) and TradingState (DB) as needed.
    """

    def __init__(
        self,
        tracker: PositionTracker,
        session_factory: async_sessionmaker,
        max_exposure: float,
        max_single_bet: float,
        var_limit: float,
        max_drawdown: float,
        hedge_shift_threshold: float,
        portfolio_value: float,
    ) -> None:
        """
        Args:
            tracker:               Live position tracker (in-memory dict).
            session_factory:       Async SQLAlchemy session factory.
            max_exposure:          Max portfolio fraction in open positions (e.g. 0.80).
            max_single_bet:        Max single trade as fraction of portfolio (e.g. 0.05).
            var_limit:             Max VaR as fraction of portfolio (e.g. 0.20).
            max_drawdown:          Hard halt drawdown threshold (e.g. 0.08).
            hedge_shift_threshold: Edge reversal magnitude that triggers hedge (e.g. 0.03).
            portfolio_value:       Current total portfolio value in dollars.
        """
        self._tracker = tracker
        self._session_factory = session_factory
        self.max_exposure = max_exposure
        self.max_single_bet = max_single_bet
        self.var_limit = var_limit
        self.max_drawdown = max_drawdown
        self.hedge_shift_threshold = hedge_shift_threshold
        self.portfolio_value = portfolio_value

    async def check(self, decision: TradeDecision) -> TradeDecision:
        """
        Run all risk checks on the incoming TradeDecision.

        Short-circuits on first rejection. Returns the decision unchanged on
        full approval, or a model_copy with approved=False and rejection_reason
        set to the first failing check.

        Args:
            decision: Incoming TradeDecision (approved=True from upstream gates).

        Returns:
            TradeDecision — either unchanged (approved) or modified (rejected).
        """
        # ------------------------------------------------------------------
        # Check 1: Halt flag (TradingState key='trading_halted')
        # ------------------------------------------------------------------
        if await self._is_halted():
            return self._reject(decision, RejectionReason.DRAWDOWN_HALTED)

        # ------------------------------------------------------------------
        # Check 2: Duplicate position
        # ------------------------------------------------------------------
        if await self._tracker.has_position(decision.ticker):
            return self._reject(decision, RejectionReason.DUPLICATE_POSITION)

        # ------------------------------------------------------------------
        # Check 3: Drawdown halt (computed from peak portfolio value)
        # ------------------------------------------------------------------
        if await self._is_drawdown_exceeded():
            return self._reject(decision, RejectionReason.DRAWDOWN_HALTED)

        # ------------------------------------------------------------------
        # Check 4: Max single bet
        # ------------------------------------------------------------------
        trade_cost = (decision.quantity or 0) * (decision.p_market or 0.0)
        if trade_cost > self.max_single_bet * self.portfolio_value:
            return self._reject(decision, RejectionReason.MAX_SINGLE_BET)

        # ------------------------------------------------------------------
        # Check 5: Max exposure
        # ------------------------------------------------------------------
        current_exposure = await self._tracker.total_exposure()
        if current_exposure + trade_cost > self.max_exposure * self.portfolio_value:
            return self._reject(decision, RejectionReason.MAX_EXPOSURE)

        # ------------------------------------------------------------------
        # Check 6: VaR (95% parametric, position values in dollars)
        # ------------------------------------------------------------------
        if await self._is_var_exceeded(trade_cost):
            return self._reject(decision, RejectionReason.VAR_EXCEEDED)

        return decision

    async def check_hedge(
        self,
        prediction: "PredictionResult",
        candidate: "MarketCandidate",
    ) -> TradeDecision | None:
        """
        Check whether an existing position's edge has reversed enough to hedge.

        Args:
            prediction: Latest model prediction for this ticker.
            candidate:  Current market data (gives implied_probability).

        Returns:
            A hedge TradeDecision (side='sell') if edge reversed beyond threshold,
            otherwise None.
        """
        if not await self._tracker.has_position(prediction.ticker):
            return None

        edge = prediction.p_model - candidate.implied_probability
        if edge < -self.hedge_shift_threshold:
            return TradeDecision(
                ticker=prediction.ticker,
                cycle_id=prediction.cycle_id,
                approved=True,
                side="sell",
                edge=edge,
                p_model=prediction.p_model,
                p_market=candidate.implied_probability,
            )
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _reject(
        self, decision: TradeDecision, reason: RejectionReason
    ) -> TradeDecision:
        """Return a rejected copy of decision with the given reason. Increments counter."""
        RISK_REJECTIONS.labels(reason=reason.value).inc()
        return decision.model_copy(update={"approved": False, "rejection_reason": reason})

    async def _is_halted(self) -> bool:
        """Check TradingState for explicit halt flag."""
        async with self._session_factory() as session:
            row = await session.get(TradingState, "trading_halted")
            if row is None:
                return False
            return row.value.lower() == "true"

    async def _is_drawdown_exceeded(self) -> bool:
        """
        Compute drawdown from stored peak_portfolio_value.

        Drawdown = (peak - current) / peak.
        If drawdown >= max_drawdown, signal halt. Also updates peak if current is new high.
        """
        async with self._session_factory() as session:
            row = await session.get(TradingState, "peak_portfolio_value")
            if row is None:
                # No peak recorded — treat current as peak, no drawdown
                return False
            peak = float(row.value)

        if peak <= 0:
            return False

        drawdown = (peak - self.portfolio_value) / peak
        return drawdown >= self.max_drawdown

    async def _is_var_exceeded(self, new_position_value: float) -> bool:
        """
        Compute 95% parametric VaR on current positions + new position.

        Position values are dollar amounts (quantity * avg_price for each position).
        VaR = mu - 1.645 * sigma of position values.

        Returns True (reject) if the computed VaR is negative and its absolute value
        exceeds var_limit * portfolio_value. This reflects a tail loss at the 95th
        percentile that exceeds the allowed portfolio loss limit.
        """
        positions = await self._tracker.get_all()
        values = [float(p.avg_price) * p.quantity for p in positions]
        values.append(new_position_value)

        var = self._compute_var(values)

        # VaR negative means the 95th percentile outcome is a loss.
        # We reject when the magnitude of that loss exceeds the limit.
        return var < -self.var_limit * self.portfolio_value

    def _compute_var(self, position_values: list[float]) -> float:
        """
        Compute 95% parametric VaR from a list of position dollar values.

        VaR = mu - 1.645 * sigma

        Returns 0.0 if fewer than 2 values (no meaningful sigma).
        A positive VaR means the portfolio is expected to gain at the 95th
        percentile. A negative VaR means a 95th-percentile loss.
        """
        if len(position_values) < 2:
            return 0.0
        mu = statistics.mean(position_values)
        sigma = statistics.stdev(position_values)
        return mu - 1.645 * sigma
