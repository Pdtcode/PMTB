"""
Tests for watchdog standalone process.

TDD RED phase — all tests written before implementation.
Tests cover:
  - Drawdown breach detection (RISK-05)
  - No-breach scenario (no halt)
  - Peak portfolio value update when new high
  - Watchdog creates its own DB pool (accepts settings dict)
  - Halt flag written to TradingState
"""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_with_state(state_rows: dict[str, str], positions: list | None = None):
    """
    Build a mock async session that:
      - session.get(TradingState, key) returns a mock row from state_rows (or None)
      - session.execute(select(...)) returns positions list
      - session.merge(), session.commit() are AsyncMocks
    """
    session = AsyncMock()

    async def mock_get(model_cls, key):
        val = state_rows.get(key)
        if val is None:
            return None
        row = MagicMock()
        row.value = val
        return row

    session.get = mock_get
    session.merge = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()

    # Simulate execute() for position queries
    if positions is None:
        positions = []

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = positions
    session.execute = AsyncMock(return_value=result_mock)

    return session


def _make_session_factory(session):
    """Wrap an async session in a context manager factory."""
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = session_cm
    return factory


def _make_position(quantity: int, avg_price: float) -> MagicMock:
    """Create a mock Position object."""
    pos = MagicMock()
    pos.quantity = quantity
    pos.avg_price = Decimal(str(avg_price))
    pos.status = "open"
    pos.market_id = MagicMock()
    return pos


# ---------------------------------------------------------------------------
# Tests: drawdown breach detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_watchdog_detects_drawdown_breach():
    """
    Peak=10000, current portfolio value=9100 -> drawdown=0.09 > 0.08
    -> sets trading_halted=true in TradingState.
    """
    from pmtb.decision.watchdog import _check_and_act

    # Portfolio: positions summing to 9100
    positions = [_make_position(9100, 1.0)]

    session = _make_session_with_state(
        {"peak_portfolio_value": "10000.0"},
        positions=positions,
    )
    factory = _make_session_factory(session)

    settings = MagicMock()
    settings.max_drawdown = 0.08

    await _check_and_act(factory, settings)

    # Should have written trading_halted = true
    # Check that merge was called with a TradingState object for trading_halted
    merge_calls = session.merge.call_args_list
    halt_written = any(
        getattr(c.args[0], "key", None) == "trading_halted"
        and getattr(c.args[0], "value", None) == "true"
        for c in merge_calls
    )
    assert halt_written, f"Expected trading_halted=true to be written. merge calls: {merge_calls}"


@pytest.mark.asyncio
async def test_watchdog_no_breach():
    """
    Peak=10000, current=9500 -> drawdown=0.05 < 0.08 -> no halt flag set.
    """
    from pmtb.decision.watchdog import _check_and_act

    positions = [_make_position(9500, 1.0)]

    session = _make_session_with_state(
        {"peak_portfolio_value": "10000.0"},
        positions=positions,
    )
    factory = _make_session_factory(session)

    settings = MagicMock()
    settings.max_drawdown = 0.08

    await _check_and_act(factory, settings)

    # Should NOT have written trading_halted = true
    merge_calls = session.merge.call_args_list
    halt_written = any(
        getattr(c.args[0], "key", None) == "trading_halted"
        and getattr(c.args[0], "value", None) == "true"
        for c in merge_calls
    )
    assert not halt_written, "Expected no halt flag written on no-breach scenario"


@pytest.mark.asyncio
async def test_watchdog_updates_peak():
    """
    current=11000 > peak=10000 -> updates peak_portfolio_value to 11000 in TradingState.
    """
    from pmtb.decision.watchdog import _check_and_act

    positions = [_make_position(11000, 1.0)]

    session = _make_session_with_state(
        {"peak_portfolio_value": "10000.0"},
        positions=positions,
    )
    factory = _make_session_factory(session)

    settings = MagicMock()
    settings.max_drawdown = 0.08

    await _check_and_act(factory, settings)

    # Should have updated peak_portfolio_value
    merge_calls = session.merge.call_args_list
    peak_updated = any(
        getattr(c.args[0], "key", None) == "peak_portfolio_value"
        and float(getattr(c.args[0], "value", "0")) >= 11000.0
        for c in merge_calls
    )
    assert peak_updated, f"Expected peak_portfolio_value update. merge calls: {merge_calls}"


@pytest.mark.asyncio
async def test_watchdog_sets_halt_flag():
    """
    After breach detection, TradingState row with key='trading_halted', value='true'
    is written via session.merge.
    """
    from pmtb.decision.watchdog import _check_and_act
    from pmtb.db.models import TradingState

    positions = [_make_position(9100, 1.0)]

    session = _make_session_with_state(
        {"peak_portfolio_value": "10000.0"},
        positions=positions,
    )
    factory = _make_session_factory(session)

    settings = MagicMock()
    settings.max_drawdown = 0.08

    await _check_and_act(factory, settings)

    # Verify the merged object is a TradingState instance with correct values
    merge_calls = session.merge.call_args_list
    halt_rows = [
        c.args[0] for c in merge_calls
        if isinstance(c.args[0], TradingState) and c.args[0].key == "trading_halted"
    ]
    assert len(halt_rows) >= 1, "Expected TradingState(key='trading_halted') to be merged"
    assert halt_rows[0].value == "true"


# ---------------------------------------------------------------------------
# Tests: process launch
# ---------------------------------------------------------------------------

def test_launch_watchdog_creates_non_daemon_process():
    """
    launch_watchdog creates a Process with daemon=False and starts it.
    """
    import multiprocessing
    from pmtb.decision.watchdog import launch_watchdog

    settings = MagicMock()
    settings.model_dump.return_value = {
        "database_url": "postgresql+asyncpg://test/test",
        "max_drawdown": 0.08,
        "kalshi_api_key_id": "key_id",
        "kalshi_private_key_path": "/path/to/key.pem",
    }

    with patch("pmtb.decision.watchdog.multiprocessing.Process") as mock_proc_cls:
        mock_proc = MagicMock()
        mock_proc_cls.return_value = mock_proc

        proc = launch_watchdog(settings)

        # Verify daemon=False is set
        call_kwargs = mock_proc_cls.call_args[1]
        assert call_kwargs.get("daemon") is False, "Process must have daemon=False"

        # Verify process was started
        mock_proc.start.assert_called_once()

        # Verify the returned object is the process
        assert proc is mock_proc
