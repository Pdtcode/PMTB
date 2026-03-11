"""
Pydantic type contracts for Phase 7 performance tracking.

Provides:
  - ErrorType: enum of loss classification categories
  - MetricsSnapshot: snapshot of all computed metrics for a given period
  - LossAnalysisResult: result of classifying a losing trade
  - BacktestResult: result of a backtest simulation run
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class ErrorType(str, Enum):
    """Categories for classifying losing trades."""

    edge_decay = "edge_decay"
    signal_error = "signal_error"
    llm_error = "llm_error"
    sizing_error = "sizing_error"
    market_shock = "market_shock"
    unknown = "unknown"


class MetricsSnapshot(BaseModel):
    """
    Snapshot of all computed performance metrics for a period.

    Fields are None when insufficient data (< 10 resolved trades).
    """

    brier_score: float | None
    sharpe_ratio: float | None
    win_rate: float | None
    profit_factor: float | None
    trade_count: int
    period: str  # "alltime" or "30d"
    computed_at: datetime


class LossAnalysisResult(BaseModel):
    """Result of classifying a single losing trade."""

    trade_id: uuid.UUID
    error_type: ErrorType
    reasoning: str | None
    classified_by: str  # "rules" or "claude"


class BacktestResult(BaseModel):
    """Result of a backtest simulation run over a historical period."""

    start_date: datetime
    end_date: datetime
    trade_count: int
    brier_score: float | None
    sharpe_ratio: float | None
    win_rate: float | None
    profit_factor: float | None
    parameters: dict[str, Any]
