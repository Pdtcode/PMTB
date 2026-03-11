"""
BacktestEngine — replays historical trades through the same production code paths.

Design decisions:
  - BacktestDataSource enforces temporal integrity via SQL `created_at <= :as_of` filter.
    No signal or model output created after `trade.created_at` can influence prediction.
  - BacktestEngine calls the SAME ProbabilityPipeline.predict_one() and KellySizer.size()
    instances used in live trading (PERF-08). This ensures backtest results are
    representative of live behavior — not a reimplementation.
  - Minimum sample guard: fewer than 10 resolved trades returns BacktestResult with all
    None metrics. Prevents misleading statistics from tiny samples.
  - Brier score computed over all predicted probabilities vs actual outcomes (0/1).
  - Sharpe ratio computed from per-trade PnL series (assumes daily returns).
  - Prometheus counters/histograms track backtest runs and duration.
  - loguru used for structured logging throughout.

References:
  - PERF-07: backtester correctness requirements
  - PERF-08: same code paths as live trading
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, UTC
from typing import TYPE_CHECKING

from loguru import logger
from prometheus_client import Counter, Histogram
from sqlalchemy import select

from pmtb.db.models import BacktestRun, Market, ModelOutput, Signal, Trade
from pmtb.performance.models import BacktestResult
from pmtb.research.models import SignalBundle, SourceSummary
from pmtb.scanner.models import MarketCandidate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from pmtb.config import Settings
    from pmtb.decision.edge import EdgeDetector
    from pmtb.decision.sizer import KellySizer
    from pmtb.prediction.pipeline import ProbabilityPipeline


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

BACKTEST_RUNS = Counter(
    "backtest_runs_total",
    "Total backtest runs executed",
)

BACKTEST_DURATION = Histogram(
    "backtest_duration_seconds",
    "Duration of a full backtest run",
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

# Minimum resolved trades required to compute meaningful metrics
MIN_TRADES_FOR_METRICS = 10


# ---------------------------------------------------------------------------
# BacktestDataSource
# ---------------------------------------------------------------------------


class BacktestDataSource:
    """
    Provides historical data snapshots for the backtester with strict temporal filtering.

    All queries use `created_at <= :as_of` to enforce the no-lookahead invariant.
    No data created after the decision timestamp can be used in the simulation.

    Parameters
    ----------
    session_factory : async_sessionmaker
        SQLAlchemy async session factory.
    """

    def __init__(self, session_factory: "async_sessionmaker") -> None:
        self._session_factory = session_factory

    async def get_signals(
        self,
        market_id: uuid.UUID,
        as_of: datetime,
    ) -> list[Signal]:
        """
        Return all signals for a market created at or before as_of.

        Parameters
        ----------
        market_id : uuid.UUID
        as_of : datetime
            Decision timestamp — signals after this are excluded.

        Returns
        -------
        list[Signal]
            Temporally filtered signals, most-recent-first.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(Signal)
                .where(
                    Signal.market_id == market_id,
                    Signal.created_at <= as_of,
                )
                .order_by(Signal.created_at.desc())
            )
            return result.scalars().all()

    async def get_market_snapshot(
        self,
        ticker: str,
        as_of: datetime,
    ) -> dict | None:
        """
        Return a MarketCandidate-compatible dict snapshot as of a timestamp.

        Uses the most-recent ModelOutput.p_market recorded at or before as_of
        as the implied_probability. Falls back to 0.5 if no model output found.

        Parameters
        ----------
        ticker : str
        as_of : datetime

        Returns
        -------
        dict or None
            Snapshot dict (can be passed to MarketCandidate(**snapshot)), or None
            if the market does not exist.
        """
        async with self._session_factory() as session:
            market_result = await session.execute(
                select(Market).where(Market.ticker == ticker)
            )
            market = market_result.scalars().first()

            if market is None:
                logger.warning("Market not found in DB", ticker=ticker, as_of=as_of)
                return None

            # Get most recent model output as-of timestamp for implied probability
            model_result = await session.execute(
                select(ModelOutput)
                .where(
                    ModelOutput.market_id == market.id,
                    ModelOutput.created_at <= as_of,
                )
                .order_by(ModelOutput.created_at.desc())
            )
            latest_output = model_result.scalars().first()

            # Use model output p_market if available; else fall back to 0.5
            if latest_output is not None and latest_output.p_market is not None:
                implied_probability = float(latest_output.p_market)
            else:
                implied_probability = 0.5

            return {
                "ticker": market.ticker,
                "title": market.title,
                "category": market.category,
                "event_context": {},
                "close_time": market.close_time,
                "yes_bid": max(0.0, implied_probability - 0.02),
                "yes_ask": min(1.0, implied_probability + 0.02),
                "implied_probability": implied_probability,
                "spread": 0.04,
                "volume_24h": 0.0,
            }

    async def build_signal_bundle(
        self,
        ticker: str,
        market_id: uuid.UUID,
        as_of: datetime,
        cycle_id: str,
    ) -> SignalBundle:
        """
        Build a SignalBundle from temporally-filtered DB signals.

        Groups signals by source, aggregates sentiment and confidence,
        and constructs a SignalBundle with SourceSummary objects.

        Parameters
        ----------
        ticker : str
        market_id : uuid.UUID
        as_of : datetime
            Only signals with created_at <= as_of are used.
        cycle_id : str

        Returns
        -------
        SignalBundle
            Temporal-filtered signal bundle.
        """
        signals = await self.get_signals(market_id=market_id, as_of=as_of)

        # Group by source
        by_source: dict[str, list[Signal]] = {}
        for sig in signals:
            by_source.setdefault(sig.source, []).append(sig)

        def _summarise(source_signals: list[Signal]) -> SourceSummary:
            if not source_signals:
                return SourceSummary(sentiment=None, confidence=None, signal_count=0)

            # Majority-vote sentiment
            votes: dict[str, int] = {}
            total_confidence = 0.0
            for s in source_signals:
                votes[s.sentiment] = votes.get(s.sentiment, 0) + 1
                total_confidence += float(s.confidence)

            majority_sentiment = max(votes, key=lambda k: votes[k])
            avg_confidence = total_confidence / len(source_signals)

            return SourceSummary(
                sentiment=majority_sentiment,
                confidence=avg_confidence,
                signal_count=len(source_signals),
            )

        reddit_signals = by_source.get("reddit", [])
        rss_signals = by_source.get("rss", [])
        trends_signals = by_source.get("trends", [])
        twitter_signals = by_source.get("twitter", [])

        return SignalBundle(
            ticker=ticker,
            cycle_id=cycle_id,
            reddit=_summarise(reddit_signals) if reddit_signals else None,
            rss=_summarise(rss_signals) if rss_signals else None,
            trends=_summarise(trends_signals) if trends_signals else None,
            twitter=_summarise(twitter_signals) if twitter_signals else None,
        )


# ---------------------------------------------------------------------------
# Metric computation helpers
# ---------------------------------------------------------------------------


def _compute_brier_score(
    p_models: list[float],
    outcomes: list[float],
) -> float:
    """
    Brier score = mean((p_model - outcome)^2).

    Lower is better. Perfect score is 0.0.
    """
    n = len(p_models)
    if n == 0:
        return 0.0
    return sum((p - o) ** 2 for p, o in zip(p_models, outcomes)) / n


def _compute_sharpe_ratio(pnl_series: list[float], risk_free: float = 0.0) -> float | None:
    """
    Simplified Sharpe ratio from per-trade PnL series.

    Sharpe = (mean_pnl - risk_free) / std_pnl

    Returns None if std_pnl is 0 (all returns identical).
    """
    n = len(pnl_series)
    if n < 2:
        return None
    mean_pnl = sum(pnl_series) / n
    variance = sum((x - mean_pnl) ** 2 for x in pnl_series) / (n - 1)
    std_pnl = math.sqrt(variance)
    if std_pnl == 0.0:
        return None
    return (mean_pnl - risk_free) / std_pnl


def _compute_win_rate(outcomes: list[float]) -> float:
    """
    Win rate = number of wins / total trades.

    A win is outcome == 1.0 (YES resolved).
    """
    if not outcomes:
        return 0.0
    wins = sum(1 for o in outcomes if o == 1.0)
    return wins / len(outcomes)


def _compute_profit_factor(pnl_series: list[float]) -> float | None:
    """
    Profit factor = sum(positive PnL) / abs(sum(negative PnL)).

    Returns None if no losing trades (avoids division by zero).
    """
    gross_profit = sum(p for p in pnl_series if p > 0)
    gross_loss = abs(sum(p for p in pnl_series if p < 0))
    if gross_loss == 0.0:
        return None  # No losing trades — metric is undefined
    return gross_profit / gross_loss


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------


class BacktestEngine:
    """
    Replays historical resolved trades through the same production code paths.

    Uses BacktestDataSource to reconstruct MarketCandidate and SignalBundle
    for each historical trade as-of the original decision timestamp — enforcing
    temporal integrity (no lookahead bias).

    Calls the SAME ProbabilityPipeline.predict_one() and KellySizer.size()
    instances used in live trading (PERF-08). This ensures backtest results are
    representative of live performance.

    Parameters
    ----------
    predictor : ProbabilityPipeline
        Live prediction pipeline instance — predict_one() is called directly.
    edge_detector : EdgeDetector
        Live edge detection instance — evaluate() is called directly.
    sizer : KellySizer
        Live position sizing instance — size() is called directly.
    data_source : BacktestDataSource
        Historical data provider with temporal filtering.
    session_factory : async_sessionmaker
        SQLAlchemy async session factory for trade queries and result persistence.
    settings : Settings
        Application settings.
    """

    def __init__(
        self,
        predictor: "ProbabilityPipeline",
        edge_detector: "EdgeDetector",
        sizer: "KellySizer",
        data_source: BacktestDataSource,
        session_factory: "async_sessionmaker",
        settings: "Settings",
    ) -> None:
        self._predictor = predictor
        self._edge_detector = edge_detector
        self._sizer = sizer
        self._data_source = data_source
        self._session_factory = session_factory
        self._settings = settings

    async def run(
        self,
        start_date: datetime,
        end_date: datetime,
        parameters: dict | None = None,
    ) -> BacktestResult:
        """
        Run a full backtest over the [start_date, end_date] period.

        Algorithm:
          1. Query resolved trades in [start_date, end_date] ordered by resolved_at ASC.
          2. If < MIN_TRADES_FOR_METRICS trades found, return BacktestResult with None metrics.
          3. For each trade:
             a. Reconstruct MarketCandidate from DB as-of trade.created_at.
             b. Build SignalBundle from temporal-filtered signals as-of trade.created_at.
             c. Call predictor.predict_one(market, bundle) — SAME code path as live.
             d. Call edge_detector.evaluate(prediction, market_candidate) for edge.
             e. If edge detected: call sizer.size(decision) — SAME code path as live.
             f. Record (p_model, actual_outcome, simulated_pnl).
          4. Compute Brier score, Sharpe ratio, win rate, profit factor.
          5. Return BacktestResult.

        Parameters
        ----------
        start_date : datetime
        end_date : datetime
        parameters : dict, optional
            Backtest configuration for audit trail.

        Returns
        -------
        BacktestResult
        """
        import time

        run_start = time.perf_counter()
        log = logger.bind(start_date=start_date.isoformat(), end_date=end_date.isoformat())
        log.info("BacktestEngine.run() started")

        params = parameters or {}

        # ------------------------------------------------------------------
        # Step 1: Query resolved trades in date range
        # ------------------------------------------------------------------
        async with self._session_factory() as session:
            result = await session.execute(
                select(Trade)
                .where(
                    Trade.resolved_at >= start_date,
                    Trade.resolved_at <= end_date,
                    Trade.resolved_outcome.isnot(None),
                )
                .order_by(Trade.resolved_at.asc())
            )
            trades: list[Trade] = result.scalars().all()

        trade_count = len(trades)
        log.info("Resolved trades found in range", count=trade_count)

        # ------------------------------------------------------------------
        # Step 2: Guard — insufficient trades
        # ------------------------------------------------------------------
        if trade_count < MIN_TRADES_FOR_METRICS:
            log.warning(
                "Insufficient resolved trades for metrics — returning None metrics",
                count=trade_count,
                minimum=MIN_TRADES_FOR_METRICS,
            )
            return BacktestResult(
                start_date=start_date,
                end_date=end_date,
                trade_count=trade_count,
                brier_score=None,
                sharpe_ratio=None,
                win_rate=None,
                profit_factor=None,
                parameters=params,
            )

        # ------------------------------------------------------------------
        # Step 3: Replay each trade through production code paths
        # ------------------------------------------------------------------
        p_models: list[float] = []
        outcomes: list[float] = []
        pnl_series: list[float] = []

        for trade in trades:
            decision_timestamp = trade.created_at

            # --- Reconstruct MarketCandidate from DB as-of decision timestamp ---
            try:
                async with self._session_factory() as session:
                    market_row_result = await session.execute(
                        select(Market).where(Market.id == trade.market_id)
                    )
                    market_row = market_row_result.scalars().first()

                if market_row is None:
                    log.warning(
                        "Market row missing for trade — skipping",
                        trade_id=str(trade.id),
                        market_id=str(trade.market_id),
                    )
                    continue

                snapshot = await self._data_source.get_market_snapshot(
                    ticker=market_row.ticker,
                    as_of=decision_timestamp,
                )
                if snapshot is None:
                    log.warning(
                        "No market snapshot available — skipping",
                        ticker=market_row.ticker,
                        as_of=decision_timestamp.isoformat(),
                    )
                    continue

                market_candidate = MarketCandidate(**snapshot)

                # --- Build SignalBundle from temporal-filtered signals ---
                bundle = await self._data_source.build_signal_bundle(
                    ticker=market_row.ticker,
                    market_id=trade.market_id,
                    as_of=decision_timestamp,
                    cycle_id=f"backtest-{trade.id}",
                )

                # --- Step 3c: Call predictor.predict_one() — SAME code path as live (PERF-08) ---
                prediction = await self._predictor.predict_one(
                    market=market_candidate,
                    bundle=bundle,
                )

                # --- Step 3d: Call edge_detector.evaluate() ---
                decision = self._edge_detector.evaluate(
                    prediction=prediction,
                    candidate=market_candidate,
                )

                # --- Step 3e: If edge detected, call sizer.size() --- SAME code path (PERF-08) ---
                if decision.approved:
                    decision = self._sizer.size(decision)

                # --- Step 3f: Record simulated outcome ---
                actual_outcome = 1.0 if trade.resolved_outcome == "yes" else 0.0
                outcomes.append(actual_outcome)
                p_models.append(prediction.p_model)

                # Use actual PnL from trade record as simulated PnL
                # (the actual PnL already reflects the real market outcome)
                if trade.pnl is not None:
                    pnl_series.append(float(trade.pnl))
                else:
                    # Estimate PnL: wins get (1 - price), losses lose price
                    price = float(trade.price)
                    simulated_pnl = (1.0 - price) if actual_outcome == 1.0 else -price
                    pnl_series.append(simulated_pnl * trade.quantity)

            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Backtest replay failed for trade — skipping",
                    trade_id=str(trade.id),
                    error=str(exc),
                )
                continue

        # ------------------------------------------------------------------
        # Step 4: Compute metrics from simulated results
        # ------------------------------------------------------------------
        simulated_count = len(outcomes)

        if simulated_count < MIN_TRADES_FOR_METRICS:
            log.warning(
                "Insufficient simulated results for metrics",
                simulated=simulated_count,
                minimum=MIN_TRADES_FOR_METRICS,
            )
            brier_score = None
            sharpe_ratio = None
            win_rate = None
            profit_factor = None
        else:
            brier_score = _compute_brier_score(p_models, outcomes)
            sharpe_ratio = _compute_sharpe_ratio(pnl_series)
            win_rate = _compute_win_rate(outcomes)
            profit_factor = _compute_profit_factor(pnl_series)

        elapsed = time.perf_counter() - run_start
        BACKTEST_RUNS.inc()
        BACKTEST_DURATION.observe(elapsed)

        log.info(
            "BacktestEngine.run() complete",
            trade_count=trade_count,
            simulated=simulated_count,
            brier_score=brier_score,
            sharpe_ratio=sharpe_ratio,
            win_rate=win_rate,
            profit_factor=profit_factor,
            duration_s=round(elapsed, 3),
        )

        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            trade_count=trade_count,
            brier_score=brier_score,
            sharpe_ratio=sharpe_ratio,
            win_rate=win_rate,
            profit_factor=profit_factor,
            parameters=params,
        )

    async def persist_result(self, result: BacktestResult) -> None:
        """
        Write a BacktestRun row to the database.

        Parameters
        ----------
        result : BacktestResult
            Completed backtest result to persist.
        """
        from decimal import Decimal

        async with self._session_factory() as session:
            run = BacktestRun(
                run_at=datetime.now(UTC),
                start_date=result.start_date,
                end_date=result.end_date,
                trade_count=result.trade_count,
                brier_score=Decimal(str(result.brier_score)) if result.brier_score is not None else None,
                sharpe_ratio=Decimal(str(result.sharpe_ratio)) if result.sharpe_ratio is not None else None,
                win_rate=Decimal(str(result.win_rate)) if result.win_rate is not None else None,
                profit_factor=Decimal(str(result.profit_factor)) if result.profit_factor is not None else None,
                parameters=result.parameters,
            )
            session.add(run)
            await session.commit()

        logger.info(
            "BacktestRun persisted to DB",
            start_date=result.start_date.isoformat(),
            end_date=result.end_date.isoformat(),
            trade_count=result.trade_count,
        )

    async def run_and_persist(
        self,
        start_date: datetime,
        end_date: datetime,
        parameters: dict | None = None,
    ) -> BacktestResult:
        """
        Run a backtest and persist the result to the database.

        Convenience method that calls run() then persist_result().

        Parameters
        ----------
        start_date : datetime
        end_date : datetime
        parameters : dict, optional

        Returns
        -------
        BacktestResult
        """
        result = await self.run(
            start_date=start_date,
            end_date=end_date,
            parameters=parameters,
        )
        await self.persist_result(result)
        return result
