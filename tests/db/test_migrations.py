"""
Tests for ORM models and Alembic migrations.

Covers:
    - Test 3: All ORM models have expected columns
    - Test 4: Alembic migration generates tables matching model metadata

Model column tests run without a database (introspection only).
Migration tests require a live PostgreSQL database.
"""

from __future__ import annotations

import os
import pytest


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://localhost:5432/pmtb_test",
)

HAS_TEST_DB = bool(os.environ.get("TEST_DATABASE_URL"))


# --- Test 3: ORM model column introspection (no DB required) ---

def test_market_model_has_expected_columns():
    """Market model defines all required columns."""
    from pmtb.db.models import Market
    from sqlalchemy import inspect

    mapper = inspect(Market)
    column_names = {col.key for col in mapper.mapper.columns}

    assert "id" in column_names
    assert "ticker" in column_names
    assert "title" in column_names
    assert "category" in column_names
    assert "status" in column_names
    assert "close_time" in column_names
    assert "created_at" in column_names
    assert "updated_at" in column_names


def test_order_model_has_expected_columns():
    """Order model defines all required columns."""
    from pmtb.db.models import Order
    from sqlalchemy import inspect

    mapper = inspect(Order)
    column_names = {col.key for col in mapper.mapper.columns}

    assert "id" in column_names
    assert "market_id" in column_names
    assert "side" in column_names
    assert "quantity" in column_names
    assert "price" in column_names
    assert "order_type" in column_names
    assert "status" in column_names
    assert "kalshi_order_id" in column_names
    assert "fill_price" in column_names
    assert "filled_quantity" in column_names
    assert "placed_at" in column_names
    assert "updated_at" in column_names


def test_position_model_has_expected_columns():
    """Position model defines all required columns."""
    from pmtb.db.models import Position
    from sqlalchemy import inspect

    mapper = inspect(Position)
    column_names = {col.key for col in mapper.mapper.columns}

    assert "id" in column_names
    assert "market_id" in column_names
    assert "side" in column_names
    assert "quantity" in column_names
    assert "avg_price" in column_names
    assert "current_value" in column_names
    assert "status" in column_names
    assert "opened_at" in column_names
    assert "closed_at" in column_names
    assert "updated_at" in column_names


def test_trade_model_has_expected_columns():
    """Trade model defines all required columns."""
    from pmtb.db.models import Trade
    from sqlalchemy import inspect

    mapper = inspect(Trade)
    column_names = {col.key for col in mapper.mapper.columns}

    assert "id" in column_names
    assert "order_id" in column_names
    assert "market_id" in column_names
    assert "side" in column_names
    assert "quantity" in column_names
    assert "price" in column_names
    assert "pnl" in column_names
    assert "resolved_outcome" in column_names
    assert "resolved_at" in column_names
    assert "created_at" in column_names


def test_signal_model_has_expected_columns():
    """Signal model defines all required columns."""
    from pmtb.db.models import Signal
    from sqlalchemy import inspect

    mapper = inspect(Signal)
    column_names = {col.key for col in mapper.mapper.columns}

    assert "id" in column_names
    assert "market_id" in column_names
    assert "source" in column_names
    assert "sentiment" in column_names
    assert "confidence" in column_names
    assert "raw_data" in column_names
    assert "cycle_id" in column_names
    assert "created_at" in column_names


def test_model_output_has_expected_columns():
    """ModelOutput model defines all required columns."""
    from pmtb.db.models import ModelOutput
    from sqlalchemy import inspect

    mapper = inspect(ModelOutput)
    column_names = {col.key for col in mapper.mapper.columns}

    assert "id" in column_names
    assert "market_id" in column_names
    assert "p_model" in column_names
    assert "p_market" in column_names
    assert "confidence_low" in column_names
    assert "confidence_high" in column_names
    assert "signal_weights" in column_names
    assert "model_version" in column_names
    assert "used_llm" in column_names
    assert "cycle_id" in column_names
    assert "created_at" in column_names


def test_performance_metric_has_expected_columns():
    """PerformanceMetric model defines all required columns."""
    from pmtb.db.models import PerformanceMetric
    from sqlalchemy import inspect

    mapper = inspect(PerformanceMetric)
    column_names = {col.key for col in mapper.mapper.columns}

    assert "id" in column_names
    assert "metric_name" in column_names
    assert "metric_value" in column_names
    assert "period" in column_names
    assert "computed_at" in column_names


def test_all_required_tables_in_metadata():
    """Base.metadata contains all 7 required tables."""
    from pmtb.db.models import Base

    table_names = set(Base.metadata.tables.keys())
    required = {
        "markets",
        "orders",
        "positions",
        "trades",
        "signals",
        "model_outputs",
        "performance_metrics",
    }
    assert required.issubset(table_names), f"Missing tables: {required - table_names}"


def test_market_timestamps_are_timezone_aware():
    """Market model uses DateTime(timezone=True) for all timestamps."""
    from pmtb.db.models import Market

    table = Market.__table__
    for col_name in ("created_at", "updated_at", "close_time"):
        col = table.c[col_name]
        assert col.type.timezone is True, f"{col_name} must have timezone=True"


def test_naming_convention_applied():
    """Base.metadata has naming_convention applied."""
    from pmtb.db.models import Base

    nc = Base.metadata.naming_convention
    assert "ix" in nc
    assert "uq" in nc
    assert "fk" in nc
    assert "pk" in nc


# --- Test 4: Alembic migration generates tables (requires live DB) ---

@pytest.mark.skipif(
    not HAS_TEST_DB,
    reason="Requires TEST_DATABASE_URL env var pointing to a running PostgreSQL instance",
)
async def test_alembic_migrations_create_all_tables():
    """Running alembic upgrade head creates all 7 tables."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
    )
    assert result.returncode == 0, f"Alembic failed: {result.stderr}"

    # Connect to the test DB and verify tables exist
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
            tables = {row[0] for row in result}

        expected = {
            "markets",
            "orders",
            "positions",
            "trades",
            "signals",
            "model_outputs",
            "performance_metrics",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"
    finally:
        await engine.dispose()
