"""
Decision layer type contracts for PMTB.

TradeDecision is the central data object that flows through the decision pipeline:
    EdgeDetector -> KellySizer -> RiskManager -> Executor

RejectionReason tracks why a trade was not taken — critical for analysis/tuning.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RejectionReason(str, Enum):
    """Why a trade candidate was rejected by the decision pipeline."""

    SHADOW = "shadow"
    """Market is shadow-only — not eligible for live trading."""

    INSUFFICIENT_EDGE = "insufficient_edge"
    """Model edge (p_model - p_market) is below the minimum threshold."""

    KELLY_NEGATIVE = "kelly_negative"
    """Full Kelly fraction f* is zero or negative — no positive EV at this price."""

    MAX_EXPOSURE = "max_exposure"
    """Total portfolio exposure would exceed max_exposure limit."""

    MAX_SINGLE_BET = "max_single_bet"
    """Single bet size would exceed max_single_bet limit after Kelly cap."""

    DRAWDOWN_HALTED = "drawdown_halted"
    """Portfolio drawdown exceeded max_drawdown — trading halted."""

    DUPLICATE_POSITION = "duplicate_position"
    """An open position already exists for this ticker."""

    VAR_EXCEEDED = "var_exceeded"
    """Adding this trade would push portfolio VaR above var_limit."""


class TradeDecision(BaseModel):
    """
    Output of the decision pipeline for a single market candidate.

    Flows from EdgeDetector -> KellySizer -> RiskManager.
    Each stage either approves or rejects with a reason.
    """

    ticker: str
    """Kalshi market ticker."""

    cycle_id: str
    """Pipeline cycle identifier for traceability."""

    approved: bool
    """True iff all pipeline gates passed and trade should be executed."""

    rejection_reason: RejectionReason | None = None
    """Populated when approved=False — first gate that rejected the trade."""

    side: str | None = None
    """'yes' or 'no' — only set when approved=True. v1 only supports 'yes'."""

    quantity: int | None = None
    """Number of contracts to trade — set by KellySizer."""

    edge: float | None = None
    """p_model - p_market — set by EdgeDetector."""

    ev: float | None = None
    """Expected value = p_model * b - (1 - p_model) — set by EdgeDetector."""

    kelly_f: float | None = None
    """Fractional Kelly position size as fraction of portfolio — set by KellySizer."""

    p_model: float | None = None
    """Model-predicted probability of YES outcome."""

    p_market: float | None = None
    """Market-implied probability (from MarketCandidate.implied_probability)."""
