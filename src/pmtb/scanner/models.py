"""
Pydantic models for the market scanner pipeline.

MarketCandidate: output contract for a market that has passed all filters.
ScanResult: wrapper around a scan cycle's output with rejection accounting.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MarketCandidate(BaseModel):
    """
    A market that has passed all scanner filters and is ready for signal evaluation.

    Price fields (yes_bid, yes_ask, implied_probability) are already-parsed floats —
    the parsing from Kalshi's fixed-point string format happens in the filter layer.
    """

    ticker: str
    title: str
    category: str
    event_context: dict
    close_time: datetime

    # Price fields — constrained to [0, 1] (probability / dollar price on a binary)
    yes_bid: float = Field(ge=0.0, le=1.0)
    yes_ask: float = Field(ge=0.0, le=1.0)
    implied_probability: float = Field(ge=0.0, le=1.0)

    # Derived metrics
    spread: float = Field(ge=0.0)
    volume_24h: float = Field(ge=0.0)

    # Optional — None during warmup period
    volatility_score: float | None = None


class ScanResult(BaseModel):
    """
    Result of a single scanner cycle.

    Contains all markets that passed every filter, along with per-filter rejection
    counts for observability and tuning.
    """

    candidates: list[MarketCandidate]
    total_markets: int
    rejected_liquidity: int
    rejected_volume: int
    rejected_spread: int
    rejected_ttr: int
    rejected_volatility: int
    scan_duration_seconds: float
    cycle_id: str
