"""
Tests for performance module type contracts.

TDD RED phase — tests drive the implementation of:
  - ErrorType enum
  - MetricsSnapshot Pydantic model
  - LossAnalysisResult Pydantic model
  - BacktestResult Pydantic model
  - LossAnalysis and BacktestRun ORM models
"""

import uuid
from datetime import datetime, UTC

import pytest


class TestErrorType:
    def test_error_type_has_six_values(self):
        from pmtb.performance.models import ErrorType

        values = {e.value for e in ErrorType}
        assert values == {
            "edge_decay",
            "signal_error",
            "llm_error",
            "sizing_error",
            "market_shock",
            "unknown",
        }

    def test_error_type_is_string_enum(self):
        from pmtb.performance.models import ErrorType

        assert ErrorType.edge_decay == "edge_decay"
        assert ErrorType.unknown == "unknown"


class TestMetricsSnapshot:
    def test_metrics_snapshot_fields(self):
        from pmtb.performance.models import MetricsSnapshot

        snapshot = MetricsSnapshot(
            brier_score=0.15,
            sharpe_ratio=1.2,
            win_rate=0.6,
            profit_factor=2.5,
            trade_count=20,
            period="alltime",
            computed_at=datetime.now(UTC),
        )
        assert snapshot.brier_score == 0.15
        assert snapshot.sharpe_ratio == 1.2
        assert snapshot.win_rate == 0.6
        assert snapshot.profit_factor == 2.5
        assert snapshot.trade_count == 20
        assert snapshot.period == "alltime"

    def test_metrics_snapshot_nullable_fields(self):
        from pmtb.performance.models import MetricsSnapshot

        snapshot = MetricsSnapshot(
            brier_score=None,
            sharpe_ratio=None,
            win_rate=None,
            profit_factor=None,
            trade_count=5,
            period="30d",
            computed_at=datetime.now(UTC),
        )
        assert snapshot.brier_score is None
        assert snapshot.sharpe_ratio is None
        assert snapshot.win_rate is None
        assert snapshot.profit_factor is None


class TestLossAnalysisResult:
    def test_loss_analysis_result_fields(self):
        from pmtb.performance.models import LossAnalysisResult, ErrorType

        trade_id = uuid.uuid4()
        result = LossAnalysisResult(
            trade_id=trade_id,
            error_type=ErrorType.edge_decay,
            reasoning="Edge decayed before resolution",
            classified_by="rules",
        )
        assert result.trade_id == trade_id
        assert result.error_type == ErrorType.edge_decay
        assert result.reasoning == "Edge decayed before resolution"
        assert result.classified_by == "rules"

    def test_loss_analysis_result_reasoning_nullable(self):
        from pmtb.performance.models import LossAnalysisResult, ErrorType

        result = LossAnalysisResult(
            trade_id=uuid.uuid4(),
            error_type=ErrorType.unknown,
            reasoning=None,
            classified_by="claude",
        )
        assert result.reasoning is None


class TestBacktestResult:
    def test_backtest_result_fields(self):
        from pmtb.performance.models import BacktestResult

        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 3, 1, tzinfo=UTC)
        result = BacktestResult(
            start_date=start,
            end_date=end,
            trade_count=50,
            brier_score=0.12,
            sharpe_ratio=1.5,
            win_rate=0.62,
            profit_factor=3.0,
            parameters={"kelly_alpha": 0.25},
        )
        assert result.start_date == start
        assert result.end_date == end
        assert result.trade_count == 50
        assert result.parameters == {"kelly_alpha": 0.25}

    def test_backtest_result_nullable_metrics(self):
        from pmtb.performance.models import BacktestResult

        result = BacktestResult(
            start_date=datetime(2025, 1, 1, tzinfo=UTC),
            end_date=datetime(2025, 3, 1, tzinfo=UTC),
            trade_count=3,
            brier_score=None,
            sharpe_ratio=None,
            win_rate=None,
            profit_factor=None,
            parameters={},
        )
        assert result.brier_score is None


class TestOrmModels:
    def test_loss_analysis_orm_importable(self):
        from pmtb.db.models import LossAnalysis

        assert LossAnalysis.__tablename__ == "loss_analyses"

    def test_backtest_run_orm_importable(self):
        from pmtb.db.models import BacktestRun

        assert BacktestRun.__tablename__ == "backtest_runs"

    def test_loss_analysis_has_trade_id_fk(self):
        from pmtb.db.models import LossAnalysis
        import sqlalchemy

        # Check that trade_id column exists and has a FK
        cols = {c.name: c for c in LossAnalysis.__table__.columns}
        assert "trade_id" in cols
        fks = list(LossAnalysis.__table__.foreign_keys)
        fk_cols = {fk.column.table.name for fk in fks}
        assert "trades" in fk_cols

    def test_trade_has_loss_analysis_relationship(self):
        from pmtb.db.models import Trade

        assert hasattr(Trade, "loss_analyses")


class TestSettingsFields:
    def test_settings_has_new_performance_fields(self):
        from pmtb.config import Settings

        fields = Settings.model_fields
        assert "brier_degradation_threshold" in fields
        assert "retraining_schedule_hours" in fields
        assert "rolling_window_days" in fields
        assert "retraining_half_life_days" in fields
        assert "settlement_poll_interval_seconds" in fields

    def test_settings_defaults(self):
        from pmtb.config import Settings

        fields = Settings.model_fields
        assert fields["brier_degradation_threshold"].default == 0.05
        assert fields["retraining_schedule_hours"].default == 168
        assert fields["rolling_window_days"].default == 30
        assert fields["retraining_half_life_days"].default == 30.0
        assert fields["settlement_poll_interval_seconds"].default == 60
