"""
Tests for OrderRepository CRUD operations.

Uses an in-memory SQLite async engine (aiosqlite) for isolation — no PostgreSQL needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from pmtb.db.models import Base
from pmtb.order_repo import OrderRepository


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


@pytest_asyncio.fixture
async def repo(session_factory):
    """OrderRepository instance backed by in-memory SQLite."""
    return OrderRepository(session_factory)


# ---------------------------------------------------------------------------
# Test: create_order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_order_persists_row(repo):
    """create_order should persist an Order row with correct field values."""
    order = await repo.create_order(
        market_ticker="NASDAQ-2024-UP",
        side="yes",
        quantity=10,
        price=Decimal("0.55"),
        kalshi_order_id="kalshi-abc-123",
        is_paper=False,
    )

    assert order.id is not None
    assert order.side == "yes"
    assert order.quantity == 10
    assert order.price == Decimal("0.55")
    assert order.kalshi_order_id == "kalshi-abc-123"
    assert order.status == "pending"
    assert order.is_paper is False
    assert order.filled_quantity == 0
    assert order.market_id is not None


@pytest.mark.asyncio
async def test_create_order_resolves_market_by_ticker(repo):
    """create_order with the same ticker twice should reuse the same Market row."""
    order1 = await repo.create_order(
        market_ticker="NASDAQ-2024-UP",
        side="yes",
        quantity=5,
        price=Decimal("0.40"),
        kalshi_order_id="kalshi-001",
    )
    order2 = await repo.create_order(
        market_ticker="NASDAQ-2024-UP",
        side="no",
        quantity=3,
        price=Decimal("0.60"),
        kalshi_order_id="kalshi-002",
    )

    assert order1.market_id == order2.market_id


@pytest.mark.asyncio
async def test_create_order_is_paper_flag(repo):
    """create_order with is_paper=True should set is_paper on the row."""
    order = await repo.create_order(
        market_ticker="TEST-MARKET",
        side="yes",
        quantity=1,
        price=Decimal("0.50"),
        kalshi_order_id="paper-xyz-999",
        is_paper=True,
    )

    assert order.is_paper is True


# ---------------------------------------------------------------------------
# Test: update_fill
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_fill_updates_order_and_creates_trade(repo):
    """update_fill should update Order fields and create a Trade row."""
    from sqlalchemy import select
    from pmtb.db.models import Trade

    order = await repo.create_order(
        market_ticker="FILL-TEST",
        side="yes",
        quantity=10,
        price=Decimal("0.50"),
        kalshi_order_id="kalshi-fill-001",
    )

    await repo.update_fill(
        order_id=order.id,
        fill_price=Decimal("0.51"),
        filled_qty=10,
        status="filled",
    )

    # Verify Order was updated
    updated = await repo.get_by_kalshi_id("kalshi-fill-001")
    assert updated is not None
    assert updated.fill_price == Decimal("0.51")
    assert updated.filled_quantity == 10
    assert updated.status == "filled"

    # Verify Trade row was created
    async with repo._session_factory() as session:
        result = await session.execute(
            select(Trade).where(Trade.order_id == order.id)
        )
        trades = result.scalars().all()

    assert len(trades) == 1
    trade = trades[0]
    assert trade.price == Decimal("0.51")
    assert trade.quantity == 10
    assert trade.side == "yes"
    assert trade.market_id == order.market_id


# ---------------------------------------------------------------------------
# Test: cancel_order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_order_sets_status_cancelled(repo):
    """cancel_order should set Order.status to 'cancelled'."""
    order = await repo.create_order(
        market_ticker="CANCEL-TEST",
        side="no",
        quantity=5,
        price=Decimal("0.45"),
        kalshi_order_id="kalshi-cancel-001",
    )

    await repo.cancel_order(order.id)

    retrieved = await repo.get_by_kalshi_id("kalshi-cancel-001")
    assert retrieved is not None
    assert retrieved.status == "cancelled"


# ---------------------------------------------------------------------------
# Test: get_by_kalshi_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_by_kalshi_id_returns_order(repo):
    """get_by_kalshi_id should return the correct Order."""
    order = await repo.create_order(
        market_ticker="LOOKUP-TEST",
        side="yes",
        quantity=7,
        price=Decimal("0.60"),
        kalshi_order_id="kalshi-lookup-abc",
    )

    result = await repo.get_by_kalshi_id("kalshi-lookup-abc")

    assert result is not None
    assert result.id == order.id
    assert result.quantity == 7


@pytest.mark.asyncio
async def test_get_by_kalshi_id_returns_none_for_missing(repo):
    """get_by_kalshi_id should return None when order_id not found."""
    result = await repo.get_by_kalshi_id("nonexistent-order-id")
    assert result is None


# ---------------------------------------------------------------------------
# Test: get_stale_orders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_stale_orders_returns_old_pending_orders(repo):
    """get_stale_orders should return pending orders older than timeout_seconds."""
    from sqlalchemy import update as sa_update
    from pmtb.db.models import Order

    # Create an order that will be made stale
    order = await repo.create_order(
        market_ticker="STALE-TEST",
        side="yes",
        quantity=3,
        price=Decimal("0.55"),
        kalshi_order_id="kalshi-stale-001",
    )

    # Backdate placed_at to simulate staleness
    stale_time = datetime.now(UTC) - timedelta(seconds=1000)
    async with repo._session_factory() as session:
        async with session.begin():
            await session.execute(
                sa_update(Order)
                .where(Order.id == order.id)
                .values(placed_at=stale_time)
            )

    stale_orders = await repo.get_stale_orders(timeout_seconds=900)

    assert len(stale_orders) >= 1
    ids = [o.id for o in stale_orders]
    assert order.id in ids


@pytest.mark.asyncio
async def test_get_stale_orders_excludes_recent_orders(repo):
    """get_stale_orders should NOT return recently placed orders."""
    order = await repo.create_order(
        market_ticker="FRESH-TEST",
        side="yes",
        quantity=2,
        price=Decimal("0.50"),
        kalshi_order_id="kalshi-fresh-001",
    )

    stale_orders = await repo.get_stale_orders(timeout_seconds=900)

    ids = [o.id for o in stale_orders]
    assert order.id not in ids


@pytest.mark.asyncio
async def test_get_stale_orders_excludes_filled_orders(repo):
    """get_stale_orders should NOT return filled (non-pending) orders."""
    from sqlalchemy import update as sa_update
    from pmtb.db.models import Order

    order = await repo.create_order(
        market_ticker="FILLED-STALE-TEST",
        side="no",
        quantity=4,
        price=Decimal("0.48"),
        kalshi_order_id="kalshi-filled-stale-001",
    )

    # Mark as filled and backdate
    stale_time = datetime.now(UTC) - timedelta(seconds=2000)
    async with repo._session_factory() as session:
        async with session.begin():
            await session.execute(
                sa_update(Order)
                .where(Order.id == order.id)
                .values(placed_at=stale_time, status="filled")
            )

    stale_orders = await repo.get_stale_orders(timeout_seconds=900)

    ids = [o.id for o in stale_orders]
    assert order.id not in ids
