"""
Tests for PositionTracker.

TDD RED phase — all tests written before implementation.
Tests cover:
  - load_from_db: populate internal dict from DB query
  - has_position: True/False lookup
  - total_exposure: sum(qty * avg_price)
  - add_position / remove_position
  - get_all
  - concurrent access (asyncio.Lock)
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from pmtb.decision.tracker import PositionTracker


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_position(ticker: str, quantity: int, avg_price: float) -> MagicMock:
    """Build a mock Position ORM object with an embedded Market relationship."""
    pos = MagicMock()
    pos.quantity = quantity
    pos.avg_price = Decimal(str(avg_price))
    pos.status = "open"
    market = MagicMock()
    market.ticker = ticker
    pos.market = market
    return pos


def _make_session_factory(positions: list) -> AsyncMock:
    """
    Build a mock async_sessionmaker that yields a context-managed session.
    The session's execute() returns a result whose scalars().all() = positions.
    """
    result = MagicMock()
    result.scalars.return_value.all.return_value = positions

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = session_cm
    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tracker_load_from_db():
    """load() queries open positions and populates the internal dict keyed by ticker."""
    pos_a = _make_position("ABC", 10, 0.60)
    pos_b = _make_position("XYZ", 5, 0.40)
    factory = _make_session_factory([pos_a, pos_b])

    tracker = PositionTracker(factory)
    await tracker.load()

    assert await tracker.has_position("ABC")
    assert await tracker.has_position("XYZ")


@pytest.mark.asyncio
async def test_has_position_true():
    """has_position returns True after loading a position for that ticker."""
    pos = _make_position("ABC", 10, 0.60)
    factory = _make_session_factory([pos])

    tracker = PositionTracker(factory)
    await tracker.load()

    assert await tracker.has_position("ABC") is True


@pytest.mark.asyncio
async def test_has_position_false():
    """has_position returns False when ticker is not loaded."""
    factory = _make_session_factory([])

    tracker = PositionTracker(factory)
    await tracker.load()

    assert await tracker.has_position("XYZ") is False


@pytest.mark.asyncio
async def test_total_exposure():
    """total_exposure sums qty * avg_price for all tracked positions."""
    # 10 * 0.60 + 5 * 0.40 = 6.0 + 2.0 = 8.0
    pos_a = _make_position("ABC", 10, 0.60)
    pos_b = _make_position("XYZ", 5, 0.40)
    factory = _make_session_factory([pos_a, pos_b])

    tracker = PositionTracker(factory)
    await tracker.load()

    exposure = await tracker.total_exposure()
    assert abs(exposure - 8.0) < 1e-9


@pytest.mark.asyncio
async def test_add_position():
    """add_position makes has_position return True for the new ticker."""
    factory = _make_session_factory([])
    tracker = PositionTracker(factory)
    await tracker.load()

    new_pos = _make_position("NEW", 3, 0.50)
    await tracker.add_position("NEW", new_pos)

    assert await tracker.has_position("NEW") is True


@pytest.mark.asyncio
async def test_remove_position():
    """remove_position makes has_position return False for that ticker."""
    pos = _make_position("ABC", 10, 0.60)
    factory = _make_session_factory([pos])

    tracker = PositionTracker(factory)
    await tracker.load()

    assert await tracker.has_position("ABC") is True
    await tracker.remove_position("ABC")
    assert await tracker.has_position("ABC") is False


@pytest.mark.asyncio
async def test_remove_position_missing_no_error():
    """remove_position on non-existent ticker does not raise."""
    factory = _make_session_factory([])
    tracker = PositionTracker(factory)
    await tracker.load()

    # Should not raise
    await tracker.remove_position("DOES_NOT_EXIST")


@pytest.mark.asyncio
async def test_get_all():
    """get_all returns all currently tracked positions as a list."""
    pos_a = _make_position("ABC", 10, 0.60)
    pos_b = _make_position("XYZ", 5, 0.40)
    factory = _make_session_factory([pos_a, pos_b])

    tracker = PositionTracker(factory)
    await tracker.load()

    positions = await tracker.get_all()
    assert len(positions) == 2


@pytest.mark.asyncio
async def test_position_count():
    """position_count returns the number of tracked positions."""
    pos_a = _make_position("ABC", 10, 0.60)
    pos_b = _make_position("XYZ", 5, 0.40)
    factory = _make_session_factory([pos_a, pos_b])

    tracker = PositionTracker(factory)
    await tracker.load()

    assert await tracker.position_count() == 2


@pytest.mark.asyncio
async def test_concurrent_access():
    """Multiple concurrent async tasks can read/write without corruption."""
    factory = _make_session_factory([])
    tracker = PositionTracker(factory)
    await tracker.load()

    async def add_and_check(ticker: str) -> bool:
        pos = _make_position(ticker, 1, 0.50)
        await tracker.add_position(ticker, pos)
        return await tracker.has_position(ticker)

    # Run 10 concurrent operations
    tickers = [f"TICKER_{i}" for i in range(10)]
    results = await asyncio.gather(*[add_and_check(t) for t in tickers])

    # All additions should be reflected
    assert all(results)
    assert await tracker.position_count() == 10
