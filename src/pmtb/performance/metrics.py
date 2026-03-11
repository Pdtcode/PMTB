"""
MetricsService — computes and persists performance metrics for resolved trades.

Computes:
  - Brier score: calibration accuracy via sklearn.metrics.brier_score_loss
  - Sharpe ratio: annualized daily PnL (mean/std * sqrt(252))
  - Win rate: fraction of profitable resolved trades
  - Profit factor: gross profit / gross loss

Supports dual-window computation:
  - "alltime": all resolved trades since inception
  - "30d": rolling window based on rolling_window_days setting

Anti-patterns guarded:
  - Minimum sample guard: None returned for metrics when < 10 resolved trades
  - Zero-std guard: NaN returned for Sharpe when std is zero
  - Concurrent metric writes prevented via asyncio.Lock
  - Stale rows deleted before full recomputation (recompute_all_windows)
"""

from __future__ import annotations

import asyncio
import math
import uuid
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from typing import Any

from loguru import logger
from prometheus_client import Counter, Histogram
from sklearn.metrics import brier_score_loss
from sqlalchemy import delete, select

from pmtb.db.models import ModelOutput, PerformanceMetric, Trade
from pmtb.performance.models import MetricsSnapshot

# ---------------------------------------------------------------------------
# Prometheus instrumentation
# ---------------------------------------------------------------------------

METRICS_COMPUTED = Counter(
    "pmtb_metrics_computed_total",
    "Total number of metric computation runs",
    ["period"],
)

METRICS_COMPUTE_DURATION = Histogram(
    "pmtb_metrics_compute_duration_seconds",
    "Duration of metric computation in seconds",
    ["period"],
)

# Minimum resolved trade count before metrics are meaningful
MIN_SAMPLE_COUNT = 10

# Windows supported by recompute_all_windows
ALL_WINDOWS = ["alltime", "30d"]


class MetricsService:
    """
    Computes, persists, and recomputes performance metrics from resolved trades.

    Usage:
        svc = MetricsService(session_factory=async_session_factory, settings=settings)
        snapshot = await svc.compute_all(period="alltime")
        await svc.persist_metrics(snapshot)
        # or full daily recompute:
        await svc.recompute_all_windows()
    """

    def __init__(self, session_factory: Any, settings: Any) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Pure computation methods (synchronous, no DB)
    # ------------------------------------------------------------------

    def compute_brier(
        self, p_models: list[float], outcomes: list[int]
    ) -> float | None:
        """
        Compute Brier score using sklearn.metrics.brier_score_loss.

        Returns None if fewer than MIN_SAMPLE_COUNT data points are provided
        to prevent meaningless metrics from small samples.
        """
        if len(p_models) < MIN_SAMPLE_COUNT:
            return None
        return float(brier_score_loss(outcomes, p_models))

    def compute_sharpe(self, daily_pnl: list[float]) -> float:
        """
        Compute annualized Sharpe ratio from daily PnL values.

        Annualization factor: sqrt(252) trading days per year.
        Returns NaN when std is zero (all-constant or all-zero PnL).
        """
        if len(daily_pnl) < 2:
            return float("nan")

        import statistics

        mean = statistics.mean(daily_pnl)
        try:
            std = statistics.stdev(daily_pnl)
        except statistics.StatisticsError:
            return float("nan")

        if std == 0.0:
            return float("nan")

        return mean / std * math.sqrt(252)

    def compute_win_rate(self, wins: int, total: int) -> float | None:
        """
        Compute win rate as wins/total.

        Returns None if total < MIN_SAMPLE_COUNT.
        """
        if total < MIN_SAMPLE_COUNT:
            return None
        return wins / total

    def compute_profit_factor(self, pnl_values: list[float]) -> float | None:
        """
        Compute profit factor as gross_profit / gross_loss.

        Returns:
          - None if len(pnl_values) < MIN_SAMPLE_COUNT
          - float("inf") if there are no losses (gross_loss == 0)
          - gross_profit / gross_loss otherwise
        """
        if len(pnl_values) < MIN_SAMPLE_COUNT:
            return None

        gross_profit = sum(p for p in pnl_values if p > 0)
        gross_loss = abs(sum(p for p in pnl_values if p < 0))

        if gross_loss == 0:
            return float("inf")

        return gross_profit / gross_loss

    # ------------------------------------------------------------------
    # DB query helpers
    # ------------------------------------------------------------------

    async def _query_resolved_trades(
        self,
        session: Any,
        cutoff_date: datetime | None = None,
    ) -> tuple[list[Any], list[float]]:
        """
        Query trades WHERE pnl IS NOT NULL AND resolved_at IS NOT NULL.

        For each resolved trade, finds the most recent ModelOutput for that
        market (created_at <= trade.created_at) to get p_model.

        Returns:
            (list of Trade objects, list of corresponding p_model floats)
        """
        stmt = select(Trade).where(
            Trade.pnl.is_not(None),
            Trade.resolved_at.is_not(None),
        )
        if cutoff_date is not None:
            stmt = stmt.where(Trade.resolved_at >= cutoff_date)

        result = await session.execute(stmt)
        trades = result.scalars().all()

        p_models: list[float] = []
        for trade in trades:
            # Find most recent model output for this market at/before trade creation
            mo_stmt = (
                select(ModelOutput)
                .where(
                    ModelOutput.market_id == trade.market_id,
                    ModelOutput.created_at <= trade.created_at,
                )
                .order_by(ModelOutput.created_at.desc())
                .limit(1)
            )
            mo_result = await session.execute(mo_stmt)
            mo = mo_result.scalar_one_or_none()
            p_models.append(float(mo.p_model) if mo else 0.5)

        return trades, p_models

    async def _delete_stale_metrics(self, session: Any, period: str) -> None:
        """Delete existing PerformanceMetric rows for a given period."""
        stmt = delete(PerformanceMetric).where(PerformanceMetric.period == period)
        await session.execute(stmt)

    async def _persist_snapshot(self, snapshot: MetricsSnapshot) -> None:
        """Write a MetricsSnapshot to PerformanceMetric table rows."""
        async with self._session_factory() as session:
            async with session.begin():
                metric_fields = {
                    "brier_score": snapshot.brier_score,
                    "sharpe_ratio": snapshot.sharpe_ratio,
                    "win_rate": snapshot.win_rate,
                    "profit_factor": snapshot.profit_factor,
                }
                for name, value in metric_fields.items():
                    if value is None or (isinstance(value, float) and math.isnan(value)):
                        continue
                    row = PerformanceMetric(
                        id=uuid.uuid4(),
                        metric_name=name,
                        metric_value=Decimal(str(value)),
                        period=snapshot.period,
                        computed_at=snapshot.computed_at,
                    )
                    session.add(row)

    # ------------------------------------------------------------------
    # Public async methods
    # ------------------------------------------------------------------

    async def compute_all(self, period: str = "alltime") -> MetricsSnapshot:
        """
        Compute all 4 metrics for the given period from resolved trades.

        Queries the DB for trades WHERE pnl IS NOT NULL AND resolved_at IS NOT NULL,
        applies rolling window filter if period="30d", then computes all metrics.

        Returns a MetricsSnapshot with None values where trade_count < 10.
        """
        cutoff_date: datetime | None = None
        if period == "30d":
            cutoff_date = datetime.now(UTC) - timedelta(
                days=self._settings.rolling_window_days
            )

        with METRICS_COMPUTE_DURATION.labels(period=period).time():
            async with self._session_factory() as session:
                trades, p_models = await self._query_resolved_trades(
                    session, cutoff_date=cutoff_date
                )

        trade_count = len(trades)

        pnl_values = [float(t.pnl) for t in trades]
        outcomes = [1 if float(t.pnl) > 0 else 0 for t in trades]
        wins = sum(1 for t in trades if float(t.pnl) > 0)

        brier = self.compute_brier(p_models, outcomes)
        sharpe = self.compute_sharpe(pnl_values) if trade_count >= MIN_SAMPLE_COUNT else None
        win_rate = self.compute_win_rate(wins=wins, total=trade_count)
        profit_factor = self.compute_profit_factor(pnl_values)

        snapshot = MetricsSnapshot(
            brier_score=brier,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            profit_factor=profit_factor,
            trade_count=trade_count,
            period=period,
            computed_at=datetime.now(UTC),
        )

        METRICS_COMPUTED.labels(period=period).inc()
        logger.info(
            "Metrics computed",
            period=period,
            trade_count=trade_count,
            brier_score=brier,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            profit_factor=profit_factor,
        )

        return snapshot

    async def persist_metrics(self, snapshot: MetricsSnapshot) -> None:
        """
        Persist a MetricsSnapshot to the PerformanceMetric table.

        Writes one row per non-None metric. NaN values (Sharpe zero-std) are skipped.
        """
        async with self._lock:
            await self._persist_snapshot(snapshot)

    async def update_on_resolution(self) -> None:
        """
        Incremental trigger: compute and persist both alltime and rolling windows.

        Called after each market settlement/resolution event.
        """
        for period in ALL_WINDOWS:
            snapshot = await self.compute_all(period=period)
            await self.persist_metrics(snapshot)

    async def recompute_all_windows(self) -> None:
        """
        Full daily recomputation for consistency.

        Per user decision: dual trigger with incremental + daily full recompute.
        This catches any missed incremental updates and ensures rolling window
        Brier values driving retraining decisions are accurate.

        Steps for each window:
          1. Delete existing PerformanceMetric rows for that period
          2. Compute fresh MetricsSnapshot
          3. Persist new rows
        """
        async with self._lock:
            async with self._session_factory() as session:
                async with session.begin():
                    for period in ALL_WINDOWS:
                        await self._delete_stale_metrics(session, period)

            for period in ALL_WINDOWS:
                snapshot = await self.compute_all(period=period)
                await self._persist_snapshot(snapshot)

        logger.info("Full metric recomputation complete", windows=ALL_WINDOWS)
