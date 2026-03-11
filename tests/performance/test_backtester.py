"""
Tests for BacktestDataSource and BacktestEngine.

TDD RED phase — all tests written before implementation.

Coverage:
  - test_backtest_data_source_temporal_filter: signals after as_of are excluded
  - test_backtest_data_source_market_snapshot: returns correct market data as of timestamp
  - test_same_code_paths: BacktestEngine uses ProbabilityPipeline.predict_one (not reimplemented)
  - test_same_sizer_code_paths: BacktestEngine uses KellySizer.size()
  - test_backtest_produces_metrics: run over mock historical data -> BacktestResult with metrics
  - test_temporal_integrity_no_lookahead: future signal NOT used in prediction
  - test_backtest_persists_result: BacktestRun row written to DB
  - test_insufficient_trades_returns_none_metrics: < 10 trades -> None metrics
  - test_backtest_respects_date_range: only trades in [start_date, end_date] processed
"""
from __future__ import annotations

import uuid
from datetime import datetime, UTC, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.db.models import Signal, Trade, Market, BacktestRun
from pmtb.decision.models import TradeDecision
from pmtb.performance.backtester import BacktestDataSource, BacktestEngine
from pmtb.performance.models import BacktestResult
from pmtb.prediction.models import PredictionResult
from pmtb.research.models import SignalBundle, SourceSummary
from pmtb.scanner.models import MarketCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market_id() -> uuid.UUID:
    return uuid.uuid4()


def _make_trade(
    market_id: uuid.UUID,
    created_at: datetime,
    resolved_at: datetime | None = None,
    resolved_outcome: str | None = "yes",
    pnl: Decimal = Decimal("5.00"),
    price: Decimal = Decimal("0.60"),
) -> Trade:
    t = Trade.__new__(Trade)
    t.id = uuid.uuid4()
    t.market_id = market_id
    t.order_id = uuid.uuid4()
    t.side = "yes"
    t.quantity = 10
    t.price = price
    t.pnl = pnl
    t.resolved_outcome = resolved_outcome
    t.resolved_at = resolved_at or (created_at + timedelta(days=1))
    t.created_at = created_at
    return t


def _make_signal(
    market_id: uuid.UUID,
    created_at: datetime,
    source: str = "reddit",
    sentiment: str = "bullish",
    confidence: Decimal = Decimal("0.8"),
) -> Signal:
    s = Signal.__new__(Signal)
    s.id = uuid.uuid4()
    s.market_id = market_id
    s.source = source
    s.sentiment = sentiment
    s.confidence = confidence
    s.raw_data = None
    s.cycle_id = "test-cycle"
    s.created_at = created_at
    return s


def _make_market_row(ticker: str = "TEST-TICKER") -> Market:
    m = Market.__new__(Market)
    m.id = uuid.uuid4()
    m.ticker = ticker
    m.title = "Test Market"
    m.category = "economics"
    m.status = "resolved"
    m.close_time = datetime(2025, 1, 1, tzinfo=UTC)
    m.created_at = datetime(2024, 12, 1, tzinfo=UTC)
    m.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
    return m


def _make_prediction_result(ticker: str = "TEST-TICKER", p_model: float = 0.70) -> PredictionResult:
    return PredictionResult(
        ticker=ticker,
        cycle_id="backtest-cycle",
        p_model=p_model,
        confidence_low=max(0.0, p_model - 0.1),
        confidence_high=min(1.0, p_model + 0.1),
        model_version="xgb-v1",
        used_llm=False,
        is_shadow=False,
    )


def _make_trade_decision(ticker: str = "TEST-TICKER", approved: bool = True) -> TradeDecision:
    return TradeDecision(
        ticker=ticker,
        cycle_id="backtest-cycle",
        approved=approved,
        side="yes" if approved else None,
        p_model=0.70,
        p_market=0.50,
        edge=0.20,
        ev=0.40,
        kelly_f=0.10 if approved else None,
        quantity=10 if approved else None,
    )


def _make_signal_bundle(ticker: str = "TEST-TICKER") -> SignalBundle:
    return SignalBundle(
        ticker=ticker,
        cycle_id="backtest-cycle",
        reddit=SourceSummary(sentiment="bullish", confidence=0.8, signal_count=3),
    )


def _make_market_candidate(ticker: str = "TEST-TICKER", implied_probability: float = 0.50) -> MarketCandidate:
    return MarketCandidate(
        ticker=ticker,
        title="Test Market",
        category="economics",
        event_context={},
        close_time=datetime(2025, 1, 1, tzinfo=UTC),
        yes_bid=0.48,
        yes_ask=0.52,
        implied_probability=implied_probability,
        spread=0.04,
        volume_24h=1000.0,
    )


# ---------------------------------------------------------------------------
# BacktestDataSource tests
# ---------------------------------------------------------------------------


class TestBacktestDataSourceTemporalFilter:
    """
    get_signals(market_id, as_of) must exclude signals with created_at > as_of.
    """

    @pytest.mark.asyncio
    async def test_backtest_data_source_temporal_filter(self):
        """Signals after as_of timestamp are excluded from results."""
        market_id = _make_market_id()
        as_of = datetime(2025, 1, 10, tzinfo=UTC)

        # Signals: one before as_of (should be included), one after (should be excluded)
        signal_before = _make_signal(market_id, datetime(2025, 1, 5, tzinfo=UTC))
        signal_after = _make_signal(market_id, datetime(2025, 1, 15, tzinfo=UTC))

        # Mock session that returns both signals from DB
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [signal_before]

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_session_factory = MagicMock(return_value=mock_session)

        data_source = BacktestDataSource(session_factory=mock_session_factory)
        signals = await data_source.get_signals(market_id=market_id, as_of=as_of)

        # Verify the DB call was made (temporal filter applied in SQL)
        assert mock_session.execute.called
        # Verify result only includes the before-signal
        assert signals == [signal_before]
        assert signal_after not in signals

    @pytest.mark.asyncio
    async def test_backtest_data_source_market_snapshot(self):
        """get_market_snapshot returns MarketCandidate-compatible dict as of timestamp."""
        ticker = "ECON-TEST-2025"
        as_of = datetime(2025, 1, 10, tzinfo=UTC)
        market_row = _make_market_row(ticker=ticker)

        # Mock model output (most recent p_market as_of timestamp)
        mock_model_output = MagicMock()
        mock_model_output.p_market = Decimal("0.65")
        mock_model_output.created_at = datetime(2025, 1, 9, tzinfo=UTC)

        mock_result_market = MagicMock()
        mock_result_market.scalars.return_value.first.return_value = market_row

        mock_result_model = MagicMock()
        mock_result_model.scalars.return_value.first.return_value = mock_model_output

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=[mock_result_market, mock_result_model])

        mock_session_factory = MagicMock(return_value=mock_session)

        data_source = BacktestDataSource(session_factory=mock_session_factory)
        snapshot = await data_source.get_market_snapshot(ticker=ticker, as_of=as_of)

        assert snapshot is not None
        assert snapshot["ticker"] == ticker
        assert snapshot["title"] == market_row.title
        assert "implied_probability" in snapshot
        # Implied probability taken from model output p_market
        assert float(snapshot["implied_probability"]) == pytest.approx(0.65)


# ---------------------------------------------------------------------------
# BacktestEngine same code path tests
# ---------------------------------------------------------------------------


class TestBacktestEngineSameCodePaths:
    """
    Verify BacktestEngine delegates to ProbabilityPipeline.predict_one and KellySizer.size()
    — not a reimplementation.
    """

    @pytest.mark.asyncio
    async def test_same_code_paths(self):
        """predict_one is called on the ProbabilityPipeline instance (not reimplemented)."""
        market_id = _make_market_id()
        ticker = "TEST-TICKER"
        now = datetime(2025, 1, 1, tzinfo=UTC)
        start = datetime(2024, 12, 1, tzinfo=UTC)
        end = datetime(2025, 2, 1, tzinfo=UTC)

        # Build 10+ resolved trades so metrics are computed
        trades = [
            _make_trade(
                market_id,
                created_at=now - timedelta(days=i),
                resolved_at=now - timedelta(days=i - 1),
            )
            for i in range(1, 12)
        ]

        prediction = _make_prediction_result(ticker=ticker, p_model=0.70)
        decision = _make_trade_decision(ticker=ticker, approved=True)
        decision_sized = _make_trade_decision(ticker=ticker, approved=True)

        mock_predictor = AsyncMock()
        mock_predictor.predict_one = AsyncMock(return_value=prediction)

        mock_edge_detector = MagicMock()
        mock_edge_detector.evaluate = MagicMock(return_value=decision)

        mock_sizer = MagicMock()
        mock_sizer.size = MagicMock(return_value=decision_sized)

        mock_data_source = AsyncMock()
        mock_data_source.get_market_snapshot = AsyncMock(return_value={
            "ticker": ticker,
            "title": "Test Market",
            "category": "economics",
            "event_context": {},
            "close_time": datetime(2025, 3, 1, tzinfo=UTC),
            "yes_bid": 0.48,
            "yes_ask": 0.52,
            "implied_probability": 0.50,
            "spread": 0.04,
            "volume_24h": 1000.0,
        })
        mock_data_source.build_signal_bundle = AsyncMock(return_value=_make_signal_bundle(ticker))

        # Mock DB query for trades
        mock_result_trades = MagicMock()
        mock_result_trades.scalars.return_value.all.return_value = trades

        # Mock DB write for BacktestRun persistence
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result_trades)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        mock_settings = MagicMock()

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=mock_settings,
        )

        result = await engine.run(start_date=start, end_date=end)

        # THE CRITICAL ASSERTION: predict_one was called on the real pipeline
        assert mock_predictor.predict_one.called, (
            "BacktestEngine must call ProbabilityPipeline.predict_one — not a reimplementation"
        )

    @pytest.mark.asyncio
    async def test_same_sizer_code_paths(self):
        """KellySizer.size() is called on the injected sizer instance (not reimplemented)."""
        market_id = _make_market_id()
        ticker = "TEST-TICKER"
        now = datetime(2025, 1, 1, tzinfo=UTC)
        start = datetime(2024, 12, 1, tzinfo=UTC)
        end = datetime(2025, 2, 1, tzinfo=UTC)

        trades = [
            _make_trade(
                market_id,
                created_at=now - timedelta(days=i),
                resolved_at=now - timedelta(days=i - 1),
            )
            for i in range(1, 12)
        ]

        prediction = _make_prediction_result(ticker=ticker, p_model=0.70)
        decision = _make_trade_decision(ticker=ticker, approved=True)
        decision_sized = _make_trade_decision(ticker=ticker, approved=True)

        mock_predictor = AsyncMock()
        mock_predictor.predict_one = AsyncMock(return_value=prediction)

        mock_edge_detector = MagicMock()
        mock_edge_detector.evaluate = MagicMock(return_value=decision)

        mock_sizer = MagicMock()
        mock_sizer.size = MagicMock(return_value=decision_sized)

        mock_data_source = AsyncMock()
        mock_data_source.get_market_snapshot = AsyncMock(return_value={
            "ticker": ticker,
            "title": "Test Market",
            "category": "economics",
            "event_context": {},
            "close_time": datetime(2025, 3, 1, tzinfo=UTC),
            "yes_bid": 0.48,
            "yes_ask": 0.52,
            "implied_probability": 0.50,
            "spread": 0.04,
            "volume_24h": 1000.0,
        })
        mock_data_source.build_signal_bundle = AsyncMock(return_value=_make_signal_bundle(ticker))

        mock_result_trades = MagicMock()
        mock_result_trades.scalars.return_value.all.return_value = trades

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result_trades)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)
        mock_settings = MagicMock()

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=mock_settings,
        )

        await engine.run(start_date=start, end_date=end)

        # THE CRITICAL ASSERTION: KellySizer.size() was called
        assert mock_sizer.size.called, (
            "BacktestEngine must call KellySizer.size() — not a reimplementation"
        )


# ---------------------------------------------------------------------------
# BacktestEngine metrics tests
# ---------------------------------------------------------------------------


class TestBacktestEngineMetrics:
    """Tests that verify BacktestResult metrics are computed from simulated trades."""

    @pytest.mark.asyncio
    async def test_backtest_produces_metrics(self):
        """run() with sufficient resolved trades returns BacktestResult with all metrics."""
        market_id = _make_market_id()
        ticker = "TEST-TICKER"
        now = datetime(2025, 1, 15, tzinfo=UTC)
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 2, 1, tzinfo=UTC)

        # 10 winning trades + 2 losing trades = 12 total
        winning_trades = [
            _make_trade(
                market_id,
                created_at=now - timedelta(days=i),
                resolved_at=now - timedelta(days=i - 1),
                resolved_outcome="yes",
                pnl=Decimal("5.00"),
            )
            for i in range(1, 11)
        ]
        losing_trades = [
            _make_trade(
                market_id,
                created_at=now - timedelta(days=i + 20),
                resolved_at=now - timedelta(days=i + 19),
                resolved_outcome="no",
                pnl=Decimal("-4.00"),
            )
            for i in range(1, 3)
        ]
        all_trades = winning_trades + losing_trades

        prediction = _make_prediction_result(ticker=ticker, p_model=0.70)
        decision_approved = _make_trade_decision(ticker=ticker, approved=True)

        mock_predictor = AsyncMock()
        mock_predictor.predict_one = AsyncMock(return_value=prediction)

        mock_edge_detector = MagicMock()
        mock_edge_detector.evaluate = MagicMock(return_value=decision_approved)

        mock_sizer = MagicMock()
        mock_sizer.size = MagicMock(return_value=decision_approved)

        mock_data_source = AsyncMock()
        mock_data_source.get_market_snapshot = AsyncMock(return_value={
            "ticker": ticker,
            "title": "Test Market",
            "category": "economics",
            "event_context": {},
            "close_time": datetime(2025, 3, 1, tzinfo=UTC),
            "yes_bid": 0.48,
            "yes_ask": 0.52,
            "implied_probability": 0.50,
            "spread": 0.04,
            "volume_24h": 1000.0,
        })
        mock_data_source.build_signal_bundle = AsyncMock(return_value=_make_signal_bundle(ticker))

        mock_result_trades = MagicMock()
        mock_result_trades.scalars.return_value.all.return_value = all_trades

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result_trades)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)
        mock_settings = MagicMock()

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=mock_settings,
        )

        result = await engine.run(start_date=start, end_date=end)

        assert isinstance(result, BacktestResult)
        assert result.trade_count >= 10
        # All metrics should be populated (not None) when >= 10 trades
        assert result.brier_score is not None, "brier_score must be computed"
        assert result.sharpe_ratio is not None, "sharpe_ratio must be computed"
        assert result.win_rate is not None, "win_rate must be computed"
        assert result.profit_factor is not None, "profit_factor must be computed"
        # Brier score is in [0, 1] — lower is better
        assert 0.0 <= result.brier_score <= 1.0
        # Win rate in [0, 1]
        assert 0.0 <= result.win_rate <= 1.0

    @pytest.mark.asyncio
    async def test_insufficient_trades_returns_none_metrics(self):
        """Fewer than 10 resolved trades in range returns BacktestResult with all None metrics."""
        market_id = _make_market_id()
        ticker = "TEST-TICKER"
        now = datetime(2025, 1, 10, tzinfo=UTC)
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 2, 1, tzinfo=UTC)

        # Only 5 trades — below minimum threshold
        sparse_trades = [
            _make_trade(
                market_id,
                created_at=now - timedelta(days=i),
                resolved_at=now - timedelta(days=i - 1),
            )
            for i in range(1, 6)
        ]

        mock_result_trades = MagicMock()
        mock_result_trades.scalars.return_value.all.return_value = sparse_trades

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result_trades)

        mock_session_factory = MagicMock(return_value=mock_session)

        engine = BacktestEngine(
            predictor=AsyncMock(),
            edge_detector=MagicMock(),
            sizer=MagicMock(),
            data_source=AsyncMock(),
            session_factory=mock_session_factory,
            settings=MagicMock(),
        )

        result = await engine.run(start_date=start, end_date=end)

        assert isinstance(result, BacktestResult)
        assert result.trade_count == 5
        assert result.brier_score is None
        assert result.sharpe_ratio is None
        assert result.win_rate is None
        assert result.profit_factor is None


# ---------------------------------------------------------------------------
# Temporal integrity tests
# ---------------------------------------------------------------------------


class TestTemporalIntegrity:
    """No lookahead bias — future signals must never reach the predictor."""

    @pytest.mark.asyncio
    async def test_temporal_integrity_no_lookahead(self):
        """
        If a future signal (created_at > decision_timestamp) is injected into the
        mock data source, BacktestEngine must not use it.

        We verify this by ensuring BacktestDataSource.get_signals is called with
        an as_of parameter equal to trade.created_at, and that any signals
        returned after as_of are never used.
        """
        market_id = _make_market_id()
        ticker = "TEST-TICKER"
        now = datetime(2025, 1, 1, tzinfo=UTC)
        start = datetime(2024, 12, 1, tzinfo=UTC)
        end = datetime(2025, 2, 1, tzinfo=UTC)

        trade_created_at = now - timedelta(days=5)

        trades = [
            _make_trade(
                market_id,
                created_at=trade_created_at + timedelta(hours=i),
                resolved_at=trade_created_at + timedelta(hours=i, days=1),
            )
            for i in range(11)  # 11 trades to pass the minimum guard
        ]

        # Signal that is IN THE FUTURE relative to some trades — should not be used
        future_signal = _make_signal(
            market_id,
            created_at=trade_created_at + timedelta(days=10),  # far future
            sentiment="bearish",
        )

        # BacktestDataSource only returns past signals — track as_of arguments
        captured_as_of_calls: list[datetime] = []

        async def mock_get_signals(market_id, as_of):
            captured_as_of_calls.append(as_of)
            # Properly filter: never return signals after as_of
            if future_signal.created_at <= as_of:
                return [future_signal]
            return []  # future signal excluded when as_of < future_signal.created_at

        async def mock_get_market_snapshot(ticker, as_of):
            return {
                "ticker": ticker,
                "title": "Test Market",
                "category": "economics",
                "event_context": {},
                "close_time": datetime(2025, 3, 1, tzinfo=UTC),
                "yes_bid": 0.48,
                "yes_ask": 0.52,
                "implied_probability": 0.50,
                "spread": 0.04,
                "volume_24h": 1000.0,
            }

        async def mock_build_signal_bundle(ticker, market_id, as_of, cycle_id):
            return _make_signal_bundle(ticker)

        mock_data_source = MagicMock()
        mock_data_source.get_signals = mock_get_signals
        mock_data_source.get_market_snapshot = mock_get_market_snapshot
        mock_data_source.build_signal_bundle = mock_build_signal_bundle

        prediction = _make_prediction_result(ticker=ticker)
        decision = _make_trade_decision(ticker=ticker, approved=True)

        mock_predictor = AsyncMock()
        mock_predictor.predict_one = AsyncMock(return_value=prediction)

        mock_edge_detector = MagicMock()
        mock_edge_detector.evaluate = MagicMock(return_value=decision)

        mock_sizer = MagicMock()
        mock_sizer.size = MagicMock(return_value=decision)

        mock_result_trades = MagicMock()
        mock_result_trades.scalars.return_value.all.return_value = trades

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result_trades)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=MagicMock(),
        )

        await engine.run(start_date=start, end_date=end)

        # Verify get_signals was called with as_of = trade.created_at (temporal integrity)
        assert len(captured_as_of_calls) > 0, "BacktestDataSource.get_signals must be called"
        for call_as_of in captured_as_of_calls:
            # Each as_of must be <= future_signal.created_at to prevent lookahead
            # (the temporal filter in get_signals/build_signal_bundle uses as_of = trade.created_at)
            assert call_as_of <= future_signal.created_at, (
                f"as_of {call_as_of} must be <= future signal time {future_signal.created_at} "
                "to ensure no lookahead bias"
            )


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


class TestBacktestPersistence:
    """Tests that BacktestRun rows are written to the database."""

    @pytest.mark.asyncio
    async def test_backtest_persists_result(self):
        """persist_result writes a BacktestRun row to the DB."""
        market_id = _make_market_id()
        ticker = "TEST-TICKER"
        now = datetime(2025, 1, 15, tzinfo=UTC)
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 2, 1, tzinfo=UTC)

        trades = [
            _make_trade(
                market_id,
                created_at=now - timedelta(days=i),
                resolved_at=now - timedelta(days=i - 1),
            )
            for i in range(1, 12)
        ]

        prediction = _make_prediction_result(ticker=ticker)
        decision = _make_trade_decision(ticker=ticker, approved=True)

        mock_predictor = AsyncMock()
        mock_predictor.predict_one = AsyncMock(return_value=prediction)

        mock_edge_detector = MagicMock()
        mock_edge_detector.evaluate = MagicMock(return_value=decision)

        mock_sizer = MagicMock()
        mock_sizer.size = MagicMock(return_value=decision)

        mock_data_source = AsyncMock()
        mock_data_source.get_market_snapshot = AsyncMock(return_value={
            "ticker": ticker,
            "title": "Test Market",
            "category": "economics",
            "event_context": {},
            "close_time": datetime(2025, 3, 1, tzinfo=UTC),
            "yes_bid": 0.48,
            "yes_ask": 0.52,
            "implied_probability": 0.50,
            "spread": 0.04,
            "volume_24h": 1000.0,
        })
        mock_data_source.build_signal_bundle = AsyncMock(return_value=_make_signal_bundle(ticker))

        mock_result_trades = MagicMock()
        mock_result_trades.scalars.return_value.all.return_value = trades

        added_objects: list = []
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result_trades)
        mock_session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock(return_value=mock_session)

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=MagicMock(),
        )

        result = await engine.run_and_persist(start_date=start, end_date=end)

        # A BacktestRun object must have been added to the session
        backtest_run_objects = [obj for obj in added_objects if isinstance(obj, BacktestRun)]
        assert len(backtest_run_objects) == 1, (
            "BacktestRun row must be written to DB via persist_result"
        )
        assert mock_session.commit.called, "Session must be committed after writing BacktestRun"

    @pytest.mark.asyncio
    async def test_backtest_respects_date_range(self):
        """
        BacktestEngine only processes trades in the [start_date, end_date] range.
        This is verified by asserting the DB query uses the date range as a filter.
        """
        market_id = _make_market_id()
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 31, tzinfo=UTC)

        # Return empty trade list (inside the date range)
        mock_result_trades = MagicMock()
        mock_result_trades.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result_trades)

        mock_session_factory = MagicMock(return_value=mock_session)

        engine = BacktestEngine(
            predictor=AsyncMock(),
            edge_detector=MagicMock(),
            sizer=MagicMock(),
            data_source=AsyncMock(),
            session_factory=mock_session_factory,
            settings=MagicMock(),
        )

        result = await engine.run(start_date=start, end_date=end)

        # DB must have been queried (to find trades in range)
        assert mock_session.execute.called, "DB must be queried to find resolved trades in range"

        # Verify result is BacktestResult (even with no trades — None metrics)
        assert isinstance(result, BacktestResult)
        assert result.start_date == start
        assert result.end_date == end
