"""
Tests for MetricsService — performance metrics computation layer.

TDD RED phase — tests drive the implementation of:
  - compute_brier: Brier score via sklearn, min sample guard
  - compute_sharpe: annualized Sharpe ratio with zero-std guard
  - compute_win_rate: wins/total ratio
  - compute_profit_factor: gross_profit/gross_loss edge cases
  - compute_all: async DB query for resolved trades
  - persist_metrics: write MetricsSnapshot to PerformanceMetric table
  - recompute_all_windows: full daily recomputation for consistency
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sklearn.metrics import brier_score_loss


# ---------------------------------------------------------------------------
# Pure computation tests — no DB needed
# ---------------------------------------------------------------------------

class TestComputeBrier:
    def _make_service(self):
        from pmtb.performance.metrics import MetricsService
        settings = MagicMock()
        settings.rolling_window_days = 30
        return MetricsService(session_factory=AsyncMock(), settings=settings)

    def test_brier_score_known_inputs(self):
        svc = self._make_service()
        # sklearn brier_score_loss: mean((y_prob - y_true)^2)
        # For [0.9, 0.1] and [1, 0]: (0.01 + 0.01) / 2 = 0.01
        p_models = [0.9] * 10  # need >= 10 samples
        outcomes = [1] * 10
        result = svc.compute_brier(p_models, outcomes)
        expected = brier_score_loss(outcomes, p_models)
        assert result == pytest.approx(expected, abs=1e-9)

    def test_brier_score_below_minimum_returns_none(self):
        svc = self._make_service()
        result = svc.compute_brier([0.9, 0.1], [1, 0])  # only 2 samples
        assert result is None

    def test_brier_score_exactly_minimum(self):
        svc = self._make_service()
        p_models = [0.7] * 10
        outcomes = [1] * 10
        result = svc.compute_brier(p_models, outcomes)
        assert result is not None

    def test_brier_score_nine_samples_returns_none(self):
        svc = self._make_service()
        result = svc.compute_brier([0.5] * 9, [1] * 9)
        assert result is None


class TestComputeSharpe:
    def _make_service(self):
        from pmtb.performance.metrics import MetricsService
        settings = MagicMock()
        settings.rolling_window_days = 30
        return MetricsService(session_factory=AsyncMock(), settings=settings)

    def test_sharpe_ratio_known_inputs(self):
        svc = self._make_service()
        import statistics
        daily_pnl = [100.0, -50.0, 200.0, -30.0, 150.0]
        mean = statistics.mean(daily_pnl)
        std = statistics.stdev(daily_pnl)
        expected = mean / std * math.sqrt(252)
        result = svc.compute_sharpe(daily_pnl)
        assert result == pytest.approx(expected, rel=1e-6)

    def test_sharpe_ratio_zero_std_returns_nan(self):
        svc = self._make_service()
        # All same values -> zero std
        daily_pnl = [100.0] * 5
        result = svc.compute_sharpe(daily_pnl)
        assert math.isnan(result)

    def test_sharpe_ratio_all_zeros_returns_nan(self):
        svc = self._make_service()
        daily_pnl = [0.0] * 10
        result = svc.compute_sharpe(daily_pnl)
        assert math.isnan(result)


class TestComputeWinRate:
    def _make_service(self):
        from pmtb.performance.metrics import MetricsService
        settings = MagicMock()
        settings.rolling_window_days = 30
        return MetricsService(session_factory=AsyncMock(), settings=settings)

    def test_win_rate_basic(self):
        svc = self._make_service()
        result = svc.compute_win_rate(wins=7, total=10)
        assert result == pytest.approx(0.7)

    def test_win_rate_below_minimum_returns_none(self):
        svc = self._make_service()
        result = svc.compute_win_rate(wins=5, total=9)
        assert result is None

    def test_win_rate_exactly_minimum(self):
        svc = self._make_service()
        result = svc.compute_win_rate(wins=10, total=10)
        assert result == pytest.approx(1.0)

    def test_win_rate_zero_wins(self):
        svc = self._make_service()
        result = svc.compute_win_rate(wins=0, total=10)
        assert result == pytest.approx(0.0)


class TestComputeProfitFactor:
    def _make_service(self):
        from pmtb.performance.metrics import MetricsService
        settings = MagicMock()
        settings.rolling_window_days = 30
        return MetricsService(session_factory=AsyncMock(), settings=settings)

    def test_profit_factor_basic(self):
        svc = self._make_service()
        pnl_values = [100.0, 200.0, -50.0, -30.0] * 3  # 12 total, >=10
        result = svc.compute_profit_factor(pnl_values)
        gross_profit = sum(p for p in pnl_values if p > 0)  # 900
        gross_loss = abs(sum(p for p in pnl_values if p < 0))  # 240
        expected = gross_profit / gross_loss
        assert result == pytest.approx(expected)

    def test_profit_factor_no_losses_returns_inf(self):
        svc = self._make_service()
        pnl_values = [100.0] * 10
        result = svc.compute_profit_factor(pnl_values)
        assert result == float("inf")

    def test_profit_factor_below_minimum_returns_none(self):
        svc = self._make_service()
        pnl_values = [100.0, -50.0, 200.0]  # only 3
        result = svc.compute_profit_factor(pnl_values)
        assert result is None

    def test_profit_factor_three_point_seven_five(self):
        svc = self._make_service()
        # gross_profit=300, gross_loss=80 => 3.75
        # need 10 total entries to pass the guard
        pnl_values = [100.0, 200.0, -50.0, -30.0] + [0.01] * 6
        result = svc.compute_profit_factor(pnl_values)
        # zeros don't count as losses; gross_profit=300+0.06, gross_loss=80
        gross_profit = sum(p for p in pnl_values if p > 0)
        gross_loss = abs(sum(p for p in pnl_values if p < 0))
        expected = gross_profit / gross_loss
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Async integration tests — mock DB
# ---------------------------------------------------------------------------

def _make_mock_trade(
    trade_id: uuid.UUID,
    pnl: float,
    p_model: float,
    resolved_outcome: str,
    created_at: datetime | None = None,
    resolved_at: datetime | None = None,
):
    trade = MagicMock()
    trade.id = trade_id
    trade.pnl = Decimal(str(pnl))
    trade.market_id = uuid.uuid4()
    trade.resolved_outcome = resolved_outcome
    trade.resolved_at = resolved_at or datetime.now(UTC)
    trade.created_at = created_at or datetime.now(UTC)
    return trade, p_model


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.rolling_window_days = 30
    return s


@pytest.fixture
def mock_session_factory():
    return AsyncMock()


def _make_async_cm_factory(session_mock):
    """Create a session_factory that works as async context manager."""
    factory = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = cm
    return factory


class TestComputeAll:
    @pytest.mark.asyncio
    async def test_compute_all_returns_metrics_snapshot(self, mock_settings):
        from pmtb.performance.metrics import MetricsService

        session_mock = AsyncMock()
        factory = _make_async_cm_factory(session_mock)
        svc = MetricsService(session_factory=factory, settings=mock_settings)

        # 15 resolved trades with pnl and p_model values
        now = datetime.now(UTC)
        resolved_trades = []
        p_models = []
        for i in range(15):
            pnl = 50.0 if i % 2 == 0 else -20.0
            p_model = 0.8 if i % 2 == 0 else 0.3
            t = MagicMock()
            t.pnl = Decimal(str(pnl))
            t.market_id = uuid.uuid4()
            t.resolved_outcome = "yes" if pnl > 0 else "no"
            t.resolved_at = now
            t.created_at = now
            resolved_trades.append(t)
            p_models.append(p_model)

        # Mock _query_resolved_trades to bypass DB
        async def mock_query(session, cutoff_date=None):
            return resolved_trades, p_models

        svc._query_resolved_trades = mock_query

        result = await svc.compute_all(period="alltime")

        from pmtb.performance.models import MetricsSnapshot
        assert isinstance(result, MetricsSnapshot)
        assert result.period == "alltime"
        assert result.trade_count == 15

    @pytest.mark.asyncio
    async def test_compute_all_below_minimum_returns_none_metrics(self, mock_settings):
        from pmtb.performance.metrics import MetricsService

        session_mock = AsyncMock()
        factory = _make_async_cm_factory(session_mock)
        svc = MetricsService(session_factory=factory, settings=mock_settings)

        # Fewer than 10 resolved trades
        resolved_trades = []
        p_models = []
        for i in range(5):
            t = MagicMock()
            t.pnl = Decimal("30.0")
            t.market_id = uuid.uuid4()
            t.resolved_outcome = "yes"
            t.resolved_at = datetime.now(UTC)
            t.created_at = datetime.now(UTC)
            resolved_trades.append(t)
            p_models.append(0.7)

        async def mock_query(session, cutoff_date=None):
            return resolved_trades, p_models

        svc._query_resolved_trades = mock_query

        result = await svc.compute_all(period="alltime")
        assert result.brier_score is None
        assert result.sharpe_ratio is None
        assert result.win_rate is None
        assert result.profit_factor is None
        assert result.trade_count == 5

    @pytest.mark.asyncio
    async def test_compute_all_rolling_window_passes_cutoff(self, mock_settings):
        from pmtb.performance.metrics import MetricsService

        session_mock = AsyncMock()
        factory = _make_async_cm_factory(session_mock)
        svc = MetricsService(session_factory=factory, settings=mock_settings)

        cutoff_received = {}

        async def mock_query(session, cutoff_date=None):
            cutoff_received["cutoff"] = cutoff_date
            return [], []

        svc._query_resolved_trades = mock_query

        await svc.compute_all(period="30d")

        assert cutoff_received["cutoff"] is not None
        # Cutoff should be approximately now - rolling_window_days
        expected_cutoff = datetime.now(UTC) - timedelta(days=mock_settings.rolling_window_days)
        diff = abs((cutoff_received["cutoff"] - expected_cutoff).total_seconds())
        assert diff < 5  # within 5 seconds


class TestRecomputeAllWindows:
    @pytest.mark.asyncio
    async def test_recompute_all_windows_computes_both_periods(self, mock_settings):
        from pmtb.performance.metrics import MetricsService

        session_mock = AsyncMock()
        session_mock.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False)))
        factory = _make_async_cm_factory(session_mock)
        svc = MetricsService(session_factory=factory, settings=mock_settings)

        called_periods = []

        async def mock_compute_all(period="alltime"):
            called_periods.append(period)
            from pmtb.performance.models import MetricsSnapshot
            return MetricsSnapshot(
                brier_score=None,
                sharpe_ratio=None,
                win_rate=None,
                profit_factor=None,
                trade_count=0,
                period=period,
                computed_at=datetime.now(UTC),
            )

        async def mock_persist(snapshot):
            pass

        async def mock_delete_stale(session, period):
            pass

        svc.compute_all = mock_compute_all
        svc._persist_snapshot = mock_persist
        svc._delete_stale_metrics = mock_delete_stale

        await svc.recompute_all_windows()

        assert "alltime" in called_periods
        assert "30d" in called_periods

    @pytest.mark.asyncio
    async def test_recompute_all_windows_deletes_stale_rows(self, mock_settings):
        from pmtb.performance.metrics import MetricsService

        session_mock = AsyncMock()
        session_mock.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False)))
        factory = _make_async_cm_factory(session_mock)
        svc = MetricsService(session_factory=factory, settings=mock_settings)

        deleted_periods = []

        async def mock_compute_all(period="alltime"):
            from pmtb.performance.models import MetricsSnapshot
            return MetricsSnapshot(
                brier_score=None, sharpe_ratio=None, win_rate=None, profit_factor=None,
                trade_count=0, period=period, computed_at=datetime.now(UTC),
            )

        async def mock_persist(snapshot):
            pass

        async def mock_delete_stale(session, period):
            deleted_periods.append(period)

        svc.compute_all = mock_compute_all
        svc._persist_snapshot = mock_persist
        svc._delete_stale_metrics = mock_delete_stale

        await svc.recompute_all_windows()

        assert "alltime" in deleted_periods
        assert "30d" in deleted_periods

    @pytest.mark.asyncio
    async def test_recompute_all_windows_is_idempotent(self, mock_settings):
        """Running recompute_all_windows twice produces same result (stale rows deleted first)."""
        from pmtb.performance.metrics import MetricsService

        session_mock = AsyncMock()
        session_mock.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False)))
        factory = _make_async_cm_factory(session_mock)
        svc = MetricsService(session_factory=factory, settings=mock_settings)

        compute_call_count = [0]
        delete_call_count = [0]

        async def mock_compute_all(period="alltime"):
            compute_call_count[0] += 1
            from pmtb.performance.models import MetricsSnapshot
            return MetricsSnapshot(
                brier_score=None, sharpe_ratio=None, win_rate=None, profit_factor=None,
                trade_count=0, period=period, computed_at=datetime.now(UTC),
            )

        async def mock_persist(snapshot):
            pass

        async def mock_delete_stale(session, period):
            delete_call_count[0] += 1

        svc.compute_all = mock_compute_all
        svc._persist_snapshot = mock_persist
        svc._delete_stale_metrics = mock_delete_stale

        await svc.recompute_all_windows()
        first_compute = compute_call_count[0]
        first_delete = delete_call_count[0]

        await svc.recompute_all_windows()

        # Second run should call the same number of times — idempotent
        assert compute_call_count[0] == first_compute * 2
        assert delete_call_count[0] == first_delete * 2
