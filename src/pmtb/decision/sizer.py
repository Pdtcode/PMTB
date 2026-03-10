"""
KellySizer — fractional Kelly position sizing with position cap.

Implements SIZE-01 through SIZE-03:
  SIZE-01: f* = (p_model * b - q) / b   (full Kelly criterion)
  SIZE-02: f = alpha * f*                 (fractional Kelly scale-down)
  SIZE-03: f = min(f, max_single_bet)    (hard position cap)

This class is stateless and synchronous — no DB, no async, no I/O.

Kalshi contracts are priced at $0.01 per cent (i.e., a $1 contract is 1 unit).
Quantity = floor(dollar_amount), minimum 1 contract.
"""

from __future__ import annotations

from pmtb.decision.models import RejectionReason, TradeDecision


class KellySizer:
    """
    Computes fractional Kelly position size for an approved TradeDecision.

    Usage::

        sizer = KellySizer(
            kelly_alpha=settings.kelly_alpha,
            max_single_bet=settings.max_single_bet,
            portfolio_value=portfolio_state.total_value,
        )
        sized_decision = sizer.size(approved_decision)
    """

    def __init__(
        self,
        kelly_alpha: float,
        max_single_bet: float,
        portfolio_value: float,
    ) -> None:
        """
        Args:
            kelly_alpha:    Fractional Kelly multiplier in (0, 1].
                            0.25 = quarter Kelly — conservative default.
            max_single_bet: Hard cap on single bet size as fraction of portfolio.
                            Overrides Kelly output when Kelly suggests larger.
            portfolio_value: Current total portfolio value in dollars.
        """
        self.kelly_alpha = kelly_alpha
        self.max_single_bet = max_single_bet
        self.portfolio_value = portfolio_value

    def size(self, decision: TradeDecision) -> TradeDecision:
        """
        Compute fractional Kelly quantity for an approved decision.

        Args:
            decision: An approved TradeDecision (approved=True) from EdgeDetector.
                      Must have p_model and p_market set.

        Returns:
            Updated TradeDecision with kelly_f and quantity set if f* > 0,
            or rejected TradeDecision with KELLY_NEGATIVE if f* <= 0.
        """
        assert decision.approved, "KellySizer.size() called on non-approved decision"

        p_model: float = decision.p_model  # type: ignore[assignment]
        p_market: float = decision.p_market  # type: ignore[assignment]

        # b = net payout per dollar risked on a binary YES bet
        b = (1.0 - p_market) / p_market  # safe: p_market > 0 guaranteed by EdgeDetector
        q = 1.0 - p_model

        # SIZE-01: Full Kelly fraction
        f_star = (p_model * b - q) / b

        if f_star <= 0.0:
            return decision.model_copy(
                update={
                    "approved": False,
                    "rejection_reason": RejectionReason.KELLY_NEGATIVE,
                }
            )

        # SIZE-02: Fractional Kelly scale-down
        f = self.kelly_alpha * f_star

        # SIZE-03: Hard position cap
        f = min(f, self.max_single_bet)

        # Convert fractional size to integer contracts
        # Kalshi YES contracts are effectively $1 each (Kalshi prices in cents but
        # our portfolio_value is stored in dollar terms).
        dollar_amount = f * self.portfolio_value
        quantity = max(1, int(dollar_amount))

        return decision.model_copy(update={"kelly_f": f, "quantity": quantity})
