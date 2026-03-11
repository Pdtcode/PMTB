"""
SQLAlchemy ORM models for PMTB.

All tables follow:
    - UUID primary keys
    - All timestamps use DateTime(timezone=True) with datetime.now(datetime.UTC)
    - Soft delete via status fields (never hard delete — full audit trail)
    - Deterministic constraint naming via NAMING_CONVENTION
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# Deterministic constraint naming — required for reliable Alembic autogenerate
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Market(Base):
    """
    Kalshi prediction market.
    Represents a tradable market with a binary yes/no outcome.
    """

    __tablename__ = "markets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_markets_ticker", "ticker"),
        Index("ix_markets_status", "status"),
        Index("ix_markets_close_time", "close_time"),
    )

    orders: Mapped[list["Order"]] = relationship("Order", back_populates="market")
    positions: Mapped[list["Position"]] = relationship("Position", back_populates="market")
    trades: Mapped[list["Trade"]] = relationship("Trade", back_populates="market")
    signals: Mapped[list["Signal"]] = relationship("Signal", back_populates="market")
    model_outputs: Mapped[list["ModelOutput"]] = relationship(
        "ModelOutput", back_populates="market"
    )


class Order(Base):
    """
    Order placed on a Kalshi market.
    Status transitions: pending -> filled/cancelled/rejected.
    Soft delete via status field — never hard delete.
    """

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id"),
        nullable=False,
    )
    side: Mapped[str] = mapped_column(String, nullable=False)  # "yes" or "no"
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    order_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    kalshi_order_id: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True
    )
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_orders_market_id", "market_id"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_kalshi_order_id", "kalshi_order_id"),
    )

    market: Mapped["Market"] = relationship("Market", back_populates="orders")
    trades: Mapped[list["Trade"]] = relationship("Trade", back_populates="order")


class Position(Base):
    """
    Current position in a market.
    One position per market (unique constraint on market_id).
    Soft delete via status field ("open" or "closed").
    """

    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id"),
        nullable=False,
        unique=True,
    )
    side: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    current_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    market: Mapped["Market"] = relationship("Market", back_populates="positions")


class Trade(Base):
    """
    Executed trade record — immutable audit log.
    Created when an order fills or resolves.
    """

    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id"),
        nullable=False,
    )
    market_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id"),
        nullable=False,
    )
    side: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    pnl: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    resolved_outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    order: Mapped["Order"] = relationship("Order", back_populates="trades")
    market: Mapped["Market"] = relationship("Market", back_populates="trades")
    loss_analyses: Mapped[list["LossAnalysis"]] = relationship(
        "LossAnalysis", back_populates="trade"
    )


class Signal(Base):
    """
    Signal from a data source (news, Reddit, RSS, etc.).
    Multiple signals per market per cycle.
    """

    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String, nullable=False)
    sentiment: Mapped[str] = mapped_column(String, nullable=False)  # bullish/bearish/neutral
    confidence: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cycle_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_signals_market_source_created", "market_id", "source", "created_at"),
    )

    market: Mapped["Market"] = relationship("Market", back_populates="signals")


class ModelOutput(Base):
    """
    Model prediction output for a market in a cycle.
    Records XGBoost (and optionally LLM) probability estimates.
    """

    __tablename__ = "model_outputs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id"),
        nullable=False,
    )
    p_model: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    p_market: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    confidence_low: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    confidence_high: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    signal_weights: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    used_llm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cycle_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_model_outputs_market_created", "market_id", "created_at"),
    )

    market: Mapped["Market"] = relationship("Market", back_populates="model_outputs")


class TradingState(Base):
    """
    Key-value store for trading system halt/resume signaling and peak portfolio tracking.

    Designed for a small number of singleton rows (e.g., 'halted', 'peak_value').
    Primary key is the key string — O(1) lookup, no UUID needed.
    """

    __tablename__ = "trading_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    """State key — e.g. 'halted', 'peak_portfolio_value'."""

    value: Mapped[str] = mapped_column(String, nullable=False)
    """Serialized state value — booleans as 'true'/'false', floats as string."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PerformanceMetric(Base):
    """
    Aggregated performance metrics (Sharpe ratio, win rate, PnL, etc.).
    Written by the metrics computation job after each cycle or period.
    """

    __tablename__ = "performance_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    metric_name: Mapped[str] = mapped_column(String, nullable=False)
    metric_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    period: Mapped[str | None] = mapped_column(String, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_perf_metrics_name_computed", "metric_name", "computed_at"),
    )


class LossAnalysis(Base):
    """
    Classification of a losing trade into an error category.

    Each row links a resolved losing trade to a diagnostic error type,
    produced either by rule-based or LLM-assisted classification.
    """

    __tablename__ = "loss_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trade_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trades.id"),
        nullable=False,
    )
    error_type: Mapped[str] = mapped_column(String, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(String, nullable=True)
    classified_by: Mapped[str] = mapped_column(String, nullable=False)  # "rules" or "claude"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_loss_analyses_trade_id", "trade_id"),
    )

    trade: Mapped["Trade"] = relationship("Trade", back_populates="loss_analyses")


class BacktestRun(Base):
    """
    Record of a historical backtest simulation run.

    Stores metrics and parameters for each backtest for comparison and audit.
    """

    __tablename__ = "backtest_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    brier_score: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_backtest_runs_run_at", "run_at"),
    )
