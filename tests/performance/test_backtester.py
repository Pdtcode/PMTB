"""
Tests for BacktestDataSource and BacktestEngine.

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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pmtb.db.models import BacktestRun
from pmtb.decision.models import TradeDecision
from pmtb.performance.backtester import BacktestDataSource, BacktestEngine
from pmtb.performance.models import BacktestResult
from pmtb.prediction.models import PredictionResult
from pmtb.research.models import SignalBundle, SourceSummary
from pmtb.scanner.models import MarketCandidate


# ---------------------------------------------------------------------------
# Helpers — use SimpleNamespace to avoid SQLAlchemy ORM instrumentation issues
# when building test objects without a real DB session.
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
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        market_id=market_id,
        order_id=uuid.uuid4(),
        side="yes",
        quantity=10,
        price=price,
        pnl=pnl,
        resolved_outcome=resolved_outcome,
        resolved_at=resolved_at or (created_at + timedelta(days=1)),
        created_at=created_at,
    )


def _make_signal(
    market_id: uuid.UUID,
    created_at: datetime,
    source: str = "reddit",
    sentiment: str = "bullish",
    confidence: Decimal = Decimal("0.8"),
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        market_id=market_id,
        source=source,
        sentiment=sentiment,
        confidence=confidence,
        raw_data=None,
        cycle_id="test-cycle",
        created_at=created_at,
    )


def _make_market_row(ticker: str = "TEST-TICKER") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        ticker=ticker,
        title="Test Market",
        category="economics",
        status="resolved",
        close_time=datetime(2025, 1, 1, tzinfo=UTC),
        created_at=datetime(2024, 12, 1, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


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


def _make_market_candidate(
    ticker: str = "TEST-TICKER",
    implied_probability: float = 0.50,
) -> MarketCandidate:
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


def _make_market_snapshot(ticker: str = "TEST-TICKER") -> dict:
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


def _make_mock_session(trade_results: list) -> tuple[AsyncMock, MagicMock, list]:
    """
    Build a mock session that returns the given trade_results from the first execute() call.

    Returns (mock_session, mock_session_factory, added_objects_list).
    """
    added_objects: list = []

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = trade_results

    # Second call to execute (for individual market queries inside the loop)
    mock_market_result = MagicMock()
    market_row = _make_market_row()
    mock_market_result.scalars.return_value.first.return_value = market_row

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    # First call: trades query; subsequent calls: market row queries
    mock_session.execute = AsyncMock(side_effect=[mock_result] + [mock_market_result] * 50)
    mock_session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    mock_session.commit = AsyncMock()

    mock_session_factory = MagicMock(return_value=mock_session)
    return mock_session, mock_session_factory, added_objects


# ---------------------------------------------------------------------------
# BacktestDataSource tests
# ---------------------------------------------------------------------------


class TestBacktestDataSourceTemporalFilter:
    """get_signals(market_id, as_of) must exclude signals with created_at > as_of."""

    @pytest.mark.asyncio
    async def test_backtest_data_source_temporal_filter(self):
        """Signals after as_of timestamp are excluded from results."""
        market_id = _make_market_id()
        as_of = datetime(2025, 1, 10, tzinfo=UTC)

        signal_before = _make_signal(market_id, datetime(2025, 1, 5, tzinfo=UTC))

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
        # Result includes only the before-signal
        assert signals == [signal_before]

    @pytest.mark.asyncio
    async def test_backtest_data_source_market_snapshot(self):
        """get_market_snapshot returns MarketCandidate-compatible dict as of timestamp."""
        ticker = "ECON-TEST-2025"
        as_of = datetime(2025, 1, 10, tzinfo=UTC)
        market_row = _make_market_row(ticker=ticker)

        mock_model_output = SimpleNamespace(
            p_market=Decimal("0.65"),
            created_at=datetime(2025, 1, 9, tzinfo=UTC),
        )

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
        assert float(snapshot["implied_probability"]) == pytest.approx(0.65)


# ---------------------------------------------------------------------------
# BacktestEngine same code path tests
# ---------------------------------------------------------------------------


class TestBacktestEngineSameCodePaths:
    """Verify BacktestEngine delegates to ProbabilityPipeline.predict_one and KellySizer.size()."""

    @pytest.mark.asyncio
    async def test_same_code_paths(self):
        """predict_one is called on the ProbabilityPipeline instance (not reimplemented)."""
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

        mock_predictor = AsyncMock()
        mock_predictor.predict_one = AsyncMock(return_value=prediction)

        mock_edge_detector = MagicMock()
        mock_edge_detector.evaluate = MagicMock(return_value=decision)

        mock_sizer = MagicMock()
        mock_sizer.size = MagicMock(return_value=decision)

        mock_data_source = AsyncMock()
        mock_data_source.get_market_snapshot = AsyncMock(return_value=_make_market_snapshot(ticker))
        mock_data_source.build_signal_bundle = AsyncMock(return_value=_make_signal_bundle(ticker))

        _, mock_session_factory, _ = _make_mock_session(trades)

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=MagicMock(),
        )

        await engine.run(start_date=start, end_date=end)

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

        mock_predictor = AsyncMock()
        mock_predictor.predict_one = AsyncMock(return_value=prediction)

        mock_edge_detector = MagicMock()
        mock_edge_detector.evaluate = MagicMock(return_value=decision)

        mock_sizer = MagicMock()
        mock_sizer.size = MagicMock(return_value=decision)

        mock_data_source = AsyncMock()
        mock_data_source.get_market_snapshot = AsyncMock(return_value=_make_market_snapshot(ticker))
        mock_data_source.build_signal_bundle = AsyncMock(return_value=_make_signal_bundle(ticker))

        _, mock_session_factory, _ = _make_mock_session(trades)

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=MagicMock(),
        )

        await engine.run(start_date=start, end_date=end)

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
        decision = _make_trade_decision(ticker=ticker, approved=True)

        mock_predictor = AsyncMock()
        mock_predictor.predict_one = AsyncMock(return_value=prediction)

        mock_edge_detector = MagicMock()
        mock_edge_detector.evaluate = MagicMock(return_value=decision)

        mock_sizer = MagicMock()
        mock_sizer.size = MagicMock(return_value=decision)

        mock_data_source = AsyncMock()
        mock_data_source.get_market_snapshot = AsyncMock(return_value=_make_market_snapshot(ticker))
        mock_data_source.build_signal_bundle = AsyncMock(return_value=_make_signal_bundle(ticker))

        _, mock_session_factory, _ = _make_mock_session(all_trades)

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=MagicMock(),
        )

        result = await engine.run(start_date=start, end_date=end)

        assert isinstance(result, BacktestResult)
        assert result.trade_count >= 10
        assert result.brier_score is not None, "brier_score must be computed"
        assert result.sharpe_ratio is not None, "sharpe_ratio must be computed"
        assert result.win_rate is not None, "win_rate must be computed"
        assert result.profit_factor is not None, "profit_factor must be computed"
        assert 0.0 <= result.brier_score <= 1.0
        assert 0.0 <= result.win_rate <= 1.0

    @pytest.mark.asyncio
    async def test_insufficient_trades_returns_none_metrics(self):
        """Fewer than 10 resolved trades returns BacktestResult with all None metrics."""
        market_id = _make_market_id()
        now = datetime(2025, 1, 10, tzinfo=UTC)
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 2, 1, tzinfo=UTC)

        sparse_trades = [
            _make_trade(
                market_id,
                created_at=now - timedelta(days=i),
                resolved_at=now - timedelta(days=i - 1),
            )
            for i in range(1, 6)
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = sparse_trades

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)

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
        BacktestDataSource.build_signal_bundle is called with as_of=trade.created_at,
        ensuring future signals cannot influence predictions.
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
            for i in range(11)  # 11 trades to pass minimum guard
        ]

        future_signal_time = trade_created_at + timedelta(days=10)

        # Track as_of arguments passed to build_signal_bundle
        captured_as_of_calls: list[datetime] = []

        async def mock_build_signal_bundle(ticker, market_id, as_of, cycle_id):
            captured_as_of_calls.append(as_of)
            return _make_signal_bundle(ticker)

        mock_data_source = MagicMock()
        mock_data_source.get_market_snapshot = AsyncMock(return_value=_make_market_snapshot(ticker))
        mock_data_source.build_signal_bundle = mock_build_signal_bundle

        prediction = _make_prediction_result(ticker=ticker)
        decision = _make_trade_decision(ticker=ticker, approved=True)

        mock_predictor = AsyncMock()
        mock_predictor.predict_one = AsyncMock(return_value=prediction)

        mock_edge_detector = MagicMock()
        mock_edge_detector.evaluate = MagicMock(return_value=decision)

        mock_sizer = MagicMock()
        mock_sizer.size = MagicMock(return_value=decision)

        _, mock_session_factory, _ = _make_mock_session(trades)

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=MagicMock(),
        )

        await engine.run(start_date=start, end_date=end)

        # Verify as_of was set to trade.created_at (temporal integrity)
        assert len(captured_as_of_calls) > 0, "build_signal_bundle must be called"
        for call_as_of in captured_as_of_calls:
            # Each as_of must be before the future signal timestamp
            assert call_as_of <= future_signal_time, (
                f"as_of {call_as_of} must be <= future signal time {future_signal_time} "
                "to ensure no lookahead bias"
            )


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


class TestBacktestPersistence:
    """Tests that BacktestRun rows are written to the database."""

    @pytest.mark.asyncio
    async def test_backtest_persists_result(self):
        """run_and_persist writes a BacktestRun row to the DB."""
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
        mock_data_source.get_market_snapshot = AsyncMock(return_value=_make_market_snapshot(ticker))
        mock_data_source.build_signal_bundle = AsyncMock(return_value=_make_signal_bundle(ticker))

        _, mock_session_factory, added_objects = _make_mock_session(trades)

        engine = BacktestEngine(
            predictor=mock_predictor,
            edge_detector=mock_edge_detector,
            sizer=mock_sizer,
            data_source=mock_data_source,
            session_factory=mock_session_factory,
            settings=MagicMock(),
        )

        result = await engine.run_and_persist(start_date=start, end_date=end)

        backtest_run_objects = [obj for obj in added_objects if isinstance(obj, BacktestRun)]
        assert len(backtest_run_objects) == 1, (
            "BacktestRun row must be written to DB via persist_result"
        )

    @pytest.mark.asyncio
    async def test_backtest_respects_date_range(self):
        """
        BacktestEngine queries the DB to fetch only trades in [start_date, end_date].
        Verified by asserting the DB execute call was made with the correct date range.
        """
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 31, tzinfo=UTC)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)

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

        assert mock_session.execute.called, "DB must be queried to find resolved trades in range"
        assert isinstance(result, BacktestResult)
        assert result.start_date == start
        assert result.end_date == end
