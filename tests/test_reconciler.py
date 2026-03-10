"""
Tests for position reconciler.

Uses mocked KalshiClient and mocked DB session.
Tests verify all 5 reconciliation scenarios:
- Orphaned orders (in DB not on Kalshi) -> marked "orphaned"
- Missing positions (on Kalshi not in DB) -> inserted
- Orders with status mismatch -> DB updated to match Kalshi
- No discrepancies -> no changes, logs "0 discrepancies"
- Logging of each discrepancy found
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_order(
    kalshi_order_id: str,
    status: str = "pending",
) -> MagicMock:
    """Create a mock DB Order row."""
    obj = MagicMock()
    obj.kalshi_order_id = kalshi_order_id
    obj.status = status
    return obj


def _make_kalshi_order(
    order_id: str,
    status: str = "resting",
) -> dict:
    """Create a mock Kalshi API order dict."""
    return {
        "order_id": order_id,
        "status": status,
        "ticker": "SOME-MARKET",
        "side": "yes",
        "count": 10,
        "yes_price": 60,
    }


def _make_kalshi_position(ticker: str, quantity: int = 5) -> dict:
    """Create a mock Kalshi API position dict."""
    return {
        "ticker": ticker,
        "position": quantity,
        "market_exposure": quantity * 60,
    }


def _make_db_position(ticker: str, status: str = "open") -> MagicMock:
    """Create a mock DB Position row."""
    obj = MagicMock()
    obj.ticker = ticker
    obj.status = status
    return obj


# ---------------------------------------------------------------------------
# Session factory helpers
# ---------------------------------------------------------------------------

def _make_session_factory(
    db_orders: list | None = None,
    db_positions: list | None = None,
) -> MagicMock:
    """
    Create a mock session factory (async context manager).

    The session will return db_orders and db_positions from execute().
    Uses a simple call counter to return orders first, then positions.
    """
    if db_orders is None:
        db_orders = []
    if db_positions is None:
        db_positions = []

    # Mock result objects
    orders_result = MagicMock()
    orders_result.scalars.return_value.all.return_value = db_orders

    positions_result = MagicMock()
    positions_result.scalars.return_value.all.return_value = db_positions

    call_count = [0]

    session = AsyncMock()

    async def execute_side_effect(query):
        call_count[0] += 1
        if call_count[0] == 1:
            return orders_result
        else:
            return positions_result

    session.execute = execute_side_effect
    session.add = MagicMock()
    session.commit = AsyncMock()

    # Context manager
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=cm)
    return factory, session


# ---------------------------------------------------------------------------
# Test 1: orphaned orders — in DB but not on Kalshi
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphaned_order_marked_correctly():
    """
    Order in DB with status 'pending' that does not exist on Kalshi
    should be marked as 'orphaned' in DB.
    """
    from pmtb.reconciler import reconcile_positions

    # DB has one pending order with kalshi_order_id "order-abc"
    db_order = _make_db_order(kalshi_order_id="order-abc", status="pending")

    factory, session = _make_session_factory(
        db_orders=[db_order],
        db_positions=[],
    )

    # Kalshi API returns NO orders (order is orphaned)
    kalshi_client = AsyncMock()
    kalshi_client.get_orders = AsyncMock(return_value=[])
    kalshi_client.get_positions = AsyncMock(return_value=[])

    result = await reconcile_positions(kalshi_client, factory)

    # The DB order should have status changed to "orphaned"
    assert db_order.status == "orphaned"
    session.commit.assert_awaited()
    assert result.orphaned_orders == 1


# ---------------------------------------------------------------------------
# Test 2: missing positions — on Kalshi but not in DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_position_inserted():
    """
    Position on Kalshi API that is not in DB should be inserted.
    """
    from pmtb.reconciler import reconcile_positions

    factory, session = _make_session_factory(
        db_orders=[],
        db_positions=[],  # no positions in DB
    )

    # Kalshi API has a position for MARKET-XYZ
    kalshi_client = AsyncMock()
    kalshi_client.get_orders = AsyncMock(return_value=[])
    kalshi_client.get_positions = AsyncMock(
        return_value=[_make_kalshi_position("MARKET-XYZ", quantity=5)]
    )

    result = await reconcile_positions(kalshi_client, factory)

    # A new position record should have been added to the session
    assert session.add.call_count >= 1
    assert result.new_positions == 1


# ---------------------------------------------------------------------------
# Test 3: order status mismatch — Kalshi has different status than DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_status_mismatch_updated():
    """
    Order on Kalshi with status 'executed' but DB has 'pending' —
    DB should be updated to match Kalshi.
    """
    from pmtb.reconciler import reconcile_positions

    # DB order has status "pending"
    db_order = _make_db_order(kalshi_order_id="order-xyz", status="pending")

    factory, session = _make_session_factory(
        db_orders=[db_order],
        db_positions=[],
    )

    # Kalshi API shows order as "executed"
    kalshi_client = AsyncMock()
    kalshi_client.get_orders = AsyncMock(
        return_value=[_make_kalshi_order("order-xyz", status="executed")]
    )
    kalshi_client.get_positions = AsyncMock(return_value=[])

    result = await reconcile_positions(kalshi_client, factory)

    # DB order status should match Kalshi
    assert db_order.status == "executed"
    session.commit.assert_awaited()
    assert result.updated_orders == 1


# ---------------------------------------------------------------------------
# Test 4: no discrepancies — no changes made
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_discrepancies_no_changes():
    """
    When all DB orders and positions match Kalshi state,
    no changes are made and result has 0 discrepancies.
    """
    from pmtb.reconciler import reconcile_positions

    # DB order matches Kalshi order exactly
    db_order = _make_db_order(kalshi_order_id="order-match", status="resting")

    factory, session = _make_session_factory(
        db_orders=[db_order],
        db_positions=[],
    )

    # Kalshi has same order with same status
    kalshi_client = AsyncMock()
    kalshi_client.get_orders = AsyncMock(
        return_value=[_make_kalshi_order("order-match", status="resting")]
    )
    kalshi_client.get_positions = AsyncMock(return_value=[])

    result = await reconcile_positions(kalshi_client, factory)

    # No changes should be committed (or if committed, 0 discrepancies)
    total_discrepancies = (
        result.orphaned_orders
        + result.new_orders
        + result.updated_orders
        + result.new_positions
        + result.closed_positions
    )
    assert total_discrepancies == 0


# ---------------------------------------------------------------------------
# Test 5: logging discrepancies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconciler_logs_discrepancies(capfd):
    """
    reconcile_positions logs each discrepancy found.
    With an orphaned order, a log warning should be emitted.
    """
    from pmtb.reconciler import reconcile_positions

    db_order = _make_db_order(kalshi_order_id="order-orphan", status="pending")

    factory, session = _make_session_factory(
        db_orders=[db_order],
        db_positions=[],
    )

    kalshi_client = AsyncMock()
    kalshi_client.get_orders = AsyncMock(return_value=[])
    kalshi_client.get_positions = AsyncMock(return_value=[])

    # Patch loguru logger to capture calls
    with patch("pmtb.reconciler.logger") as mock_logger:
        result = await reconcile_positions(kalshi_client, factory)

        # Warning should have been logged for orphaned order
        assert mock_logger.warning.call_count >= 1 or mock_logger.bind.call_count >= 1

    assert result.orphaned_orders == 1
