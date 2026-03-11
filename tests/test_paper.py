"""
Tests for enhanced PaperOrderExecutor with spread-aware simulation and DB persistence.

Covers:
    - Legacy mode (no session_factory): backward-compatible in-memory behavior
    - DB persistence mode (with session_factory): persists to DB with is_paper=True
    - Fill simulation: spread-aware fills at requested price, probabilistic partial fills
    - cancel_order: updates in-memory list and DB (when repo present)
    - create_executor factory: passes session_factory through to PaperOrderExecutor
    - LiveOrderExecutor and live/error routing (kept from original test suite)

Uses in-memory SQLite aiosqlite for DB tests (same pattern as test_order_repo.py).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from pmtb.db.models import Base, Order as DBOrder, Trade
from pmtb.paper import PaperOrderExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def engine():
    """In-memory SQLite async engine with all tables created."""
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    """Async session factory bound to the test engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def paper_executor():
    """Fresh PaperOrderExecutor with no session_factory (legacy mode)."""
    return PaperOrderExecutor()


# ---------------------------------------------------------------------------
# Legacy mode: no session_factory (backward compatible)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_order_no_session_factory_in_memory_only(paper_executor):
    """PaperOrderExecutor without session_factory stays in-memory (backward compat)."""
    result = await paper_executor.place_order(
        market_ticker="NASDAQ-UP",
        side="yes",
        quantity=5,
        price=55,
        order_type="limit",
    )

    assert result["order_id"].startswith("paper-")
    assert result["market_ticker"] == "NASDAQ-UP"
    assert result["side"] == "yes"
    assert result["quantity"] == 5
    assert result["status"] in ("filled", "partial")

    orders = await paper_executor.get_orders()
    assert len(orders) == 1


@pytest.mark.asyncio
async def test_cancel_order_no_session_factory(paper_executor):
    """cancel_order updates in-memory list when no session_factory."""
    order = await paper_executor.place_order(
        market_ticker="CANCEL-TEST",
        side="no",
        quantity=3,
        price=45,
    )
    result = await paper_executor.cancel_order(order["order_id"])

    assert result["status"] == "cancelled"
    orders = await paper_executor.get_orders(status="cancelled")
    assert len(orders) == 1


@pytest.mark.asyncio
async def test_cancel_order_unknown_id_returns_not_found(paper_executor):
    """cancel_order returns not_found for unknown order_id."""
    result = await paper_executor.cancel_order("paper-nonexistent-id")
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_get_positions_returns_empty(paper_executor):
    """Paper mode get_positions always returns empty list."""
    positions = await paper_executor.get_positions()
    assert positions == []


@pytest.mark.asyncio
async def test_get_orders_filtered_by_status(paper_executor):
    """get_orders filtered by status returns only matching orders."""
    placed1 = await paper_executor.place_order("MKT-A", "yes", 1, 50)
    placed2 = await paper_executor.place_order("MKT-B", "no", 2, 45)

    await paper_executor.cancel_order(placed1["order_id"])

    cancelled = await paper_executor.get_orders(status="cancelled")
    assert len(cancelled) == 1
    assert cancelled[0]["order_id"] == placed1["order_id"]


@pytest.mark.asyncio
async def test_orders_stored_in_memory(paper_executor):
    """Orders are stored in _orders internal list."""
    result = await paper_executor.place_order("MEM-TEST", "yes", 7, 40)
    order_id = result["order_id"]

    assert hasattr(paper_executor, "_orders")
    assert any(o["order_id"] == order_id for o in paper_executor._orders)


@pytest.mark.asyncio
async def test_place_order_logs_via_loguru(paper_executor):
    """place_order logs simulated order via loguru."""
    from loguru import logger

    log_messages = []

    def capture_sink(message):
        log_messages.append(str(message))

    logger.add(capture_sink, level="INFO", format="{message}")
    await paper_executor.place_order("LOG-TEST", "yes", 3, 55)
    logger.remove()

    assert any("Simulated order placed" in msg for msg in log_messages), (
        f"Expected 'Simulated order placed' in log. Got: {log_messages}"
    )


# ---------------------------------------------------------------------------
# DB persistence mode (with session_factory)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_order_with_session_factory_persists_to_db(session_factory):
    """PaperOrderExecutor with session_factory writes Order row to DB."""
    executor = PaperOrderExecutor(session_factory=session_factory)
    result = await executor.place_order(
        market_ticker="DB-TEST",
        side="yes",
        quantity=10,
        price=60,
    )

    assert result["order_id"].startswith("paper-")
    assert result["status"] in ("filled", "partial")

    async with session_factory() as session:
        db_result = await session.execute(
            select(DBOrder).where(DBOrder.kalshi_order_id == result["order_id"])
        )
        order_row = db_result.scalar_one_or_none()

    assert order_row is not None
    assert order_row.is_paper is True
    assert order_row.side == "yes"
    assert order_row.quantity == 10


@pytest.mark.asyncio
async def test_paper_order_creates_trade_row_in_db(session_factory):
    """place_order should create a Trade audit row when session_factory is provided."""
    executor = PaperOrderExecutor(session_factory=session_factory)
    result = await executor.place_order(
        market_ticker="TRADE-TEST",
        side="yes",
        quantity=5,
        price=50,
    )

    async with session_factory() as session:
        order_result = await session.execute(
            select(DBOrder).where(DBOrder.kalshi_order_id == result["order_id"])
        )
        order_row = order_result.scalar_one()

        trade_result = await session.execute(
            select(Trade).where(Trade.order_id == order_row.id)
        )
        trades = trade_result.scalars().all()

    assert len(trades) == 1


@pytest.mark.asyncio
async def test_paper_order_is_paper_flag_set_in_db(session_factory):
    """Order rows created by PaperOrderExecutor must have is_paper=True."""
    executor = PaperOrderExecutor(session_factory=session_factory)
    result = await executor.place_order(
        market_ticker="FLAG-TEST",
        side="no",
        quantity=2,
        price=40,
    )

    async with session_factory() as session:
        db_result = await session.execute(
            select(DBOrder).where(DBOrder.kalshi_order_id == result["order_id"])
        )
        order_row = db_result.scalar_one()

    assert order_row.is_paper is True


@pytest.mark.asyncio
async def test_cancel_order_with_session_factory_updates_db(session_factory):
    """cancel_order should set DB Order.status to 'cancelled' when session_factory is provided."""
    executor = PaperOrderExecutor(session_factory=session_factory)
    order = await executor.place_order(
        market_ticker="CANCEL-DB-TEST",
        side="yes",
        quantity=3,
        price=55,
    )

    cancel_result = await executor.cancel_order(order["order_id"])
    assert cancel_result["status"] == "cancelled"

    async with session_factory() as session:
        db_result = await session.execute(
            select(DBOrder).where(DBOrder.kalshi_order_id == order["order_id"])
        )
        order_row = db_result.scalar_one()

    assert order_row.status == "cancelled"


# ---------------------------------------------------------------------------
# Fill simulation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fill_price_equals_requested_price(paper_executor):
    """Paper mode fills at the requested price (zero slippage)."""
    result = await paper_executor.place_order(
        market_ticker="FILL-PRICE-TEST",
        side="yes",
        quantity=10,
        price=65,
    )

    assert result["fill_price"] == 65


@pytest.mark.asyncio
async def test_partial_fill_simulation_range(paper_executor):
    """Filled quantity should be between 50% and 100% of requested across 100 trials."""
    import random
    random.seed(42)

    quantity = 100
    for _ in range(100):
        result = await paper_executor.place_order(
            market_ticker="RANGE-TEST",
            side="yes",
            quantity=quantity,
            price=50,
        )
        filled_qty = result["filled_quantity"]
        assert filled_qty >= quantity * 0.5, f"filled_quantity {filled_qty} < 50% of {quantity}"
        assert filled_qty <= quantity, f"filled_quantity {filled_qty} > {quantity}"


@pytest.mark.asyncio
async def test_fill_status_filled_for_quantity_one(paper_executor):
    """quantity=1 always results in status='filled' (floor(0.5*1)=0, but max(1,0)=1 == 1)."""
    result = await paper_executor.place_order(
        market_ticker="STATUS-FILL",
        side="yes",
        quantity=1,
        price=50,
    )
    assert result["filled_quantity"] == 1
    assert result["status"] == "filled"


@pytest.mark.asyncio
async def test_fill_status_partial_when_not_full():
    """Status should be 'partial' when filled_quantity < quantity."""
    import random
    original_uniform = random.uniform
    random.uniform = lambda a, b: 0.5

    try:
        executor = PaperOrderExecutor()
        result = await executor.place_order(
            market_ticker="PARTIAL-TEST",
            side="yes",
            quantity=10,
            price=50,
        )
        assert result["filled_quantity"] == 5
        assert result["status"] == "partial"
    finally:
        random.uniform = original_uniform


# ---------------------------------------------------------------------------
# create_executor factory integration
# ---------------------------------------------------------------------------

def test_create_executor_paper_returns_paper_executor():
    """create_executor with trading_mode='paper' returns PaperOrderExecutor."""
    from pmtb.executor import create_executor

    mock_settings = MagicMock()
    mock_settings.trading_mode = "paper"

    executor = create_executor(mock_settings)
    assert isinstance(executor, PaperOrderExecutor)
    assert executor._repo is None


def test_create_executor_live_returns_live_executor():
    """create_executor with trading_mode='live' returns LiveOrderExecutor."""
    from pmtb.executor import LiveOrderExecutor, create_executor

    mock_settings = MagicMock()
    mock_settings.trading_mode = "live"
    mock_kalshi = MagicMock()

    executor = create_executor(mock_settings, kalshi_client=mock_kalshi)
    assert isinstance(executor, LiveOrderExecutor)


def test_create_executor_live_without_client_raises():
    """create_executor with live mode but no kalshi_client raises ValueError."""
    from pmtb.executor import create_executor

    mock_settings = MagicMock()
    mock_settings.trading_mode = "live"

    with pytest.raises(ValueError, match="kalshi_client"):
        create_executor(mock_settings, kalshi_client=None)


@pytest.mark.asyncio
async def test_create_executor_passes_session_factory(session_factory):
    """create_executor passes session_factory to PaperOrderExecutor."""
    from pmtb.executor import create_executor

    settings = MagicMock()
    settings.trading_mode = "paper"

    executor = create_executor(settings, session_factory=session_factory)
    assert isinstance(executor, PaperOrderExecutor)
    assert executor._repo is not None
