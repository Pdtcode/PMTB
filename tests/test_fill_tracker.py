"""
Tests for FillTracker — fill event handling, slippage tracking, stale cancellation,
and REST polling fallback.

All dependencies (ws_client, kalshi_client, order_repo) are AsyncMocked.
asyncio_mode="auto" is set in pyproject.toml — no explicit @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.fill_tracker import FillTracker


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_settings(stale_order_timeout_seconds: int = 900) -> MagicMock:
    """Return a minimal Settings-like mock."""
    s = MagicMock()
    s.stale_order_timeout_seconds = stale_order_timeout_seconds
    return s


def _make_order(
    order_id: uuid.UUID | None = None,
    kalshi_order_id: str = "kalshi-order-001",
    price: Decimal = Decimal("0.50"),
    quantity: int = 10,
    status: str = "pending",
) -> MagicMock:
    """Return a mock Order object with standard attributes."""
    order = MagicMock()
    order.id = order_id or uuid.uuid4()
    order.kalshi_order_id = kalshi_order_id
    order.price = price
    order.quantity = quantity
    order.status = status
    return order


@pytest.fixture
def ws_client():
    return AsyncMock()


@pytest.fixture
def kalshi_client():
    return AsyncMock()


@pytest.fixture
def order_repo():
    return AsyncMock()


@pytest.fixture
def settings():
    return _make_settings()


@pytest.fixture
def tracker(ws_client, kalshi_client, order_repo, settings):
    return FillTracker(ws_client, kalshi_client, order_repo, settings)


# ---------------------------------------------------------------------------
# Test: _handle_fill_event — known order
# ---------------------------------------------------------------------------


async def test_handle_fill_event_updates_order_and_logs_slippage(
    tracker, order_repo
):
    """
    _handle_fill_event should look up order, compute slippage, call update_fill
    with correct args, and not crash.
    """
    order = _make_order(price=Decimal("50"), quantity=10)  # price in cents
    order_repo.get_by_kalshi_id.return_value = order

    msg = {
        "type": "fill",
        "order_id": order.kalshi_order_id,
        "yes_price": 52,   # fill_price in cents
        "count": 10,
    }

    await tracker._handle_fill_event(msg)

    order_repo.get_by_kalshi_id.assert_awaited_once_with(order.kalshi_order_id)
    order_repo.update_fill.assert_awaited_once()
    call_args = order_repo.update_fill.call_args
    # Verify fill_price and filled_qty forwarded correctly
    assert call_args.kwargs.get("fill_price", call_args.args[1] if len(call_args.args) > 1 else None) == 52 or \
           call_args.args[1] == 52 or \
           call_args.kwargs.get("fill_price") == 52


async def test_handle_fill_event_status_filled_when_fully_filled(
    tracker, order_repo
):
    """
    _handle_fill_event sets status='filled' when filled_qty >= order.quantity.
    """
    order = _make_order(price=Decimal("50"), quantity=5)
    order_repo.get_by_kalshi_id.return_value = order

    msg = {
        "type": "fill",
        "order_id": order.kalshi_order_id,
        "yes_price": 51,
        "count": 5,  # exactly fills the order
    }

    await tracker._handle_fill_event(msg)

    # update_fill must be called with status="filled"
    order_repo.update_fill.assert_awaited_once()
    call_kwargs = order_repo.update_fill.call_args
    # status is 4th positional or keyword
    positional = call_kwargs.args
    keyword = call_kwargs.kwargs
    status = keyword.get("status") or (positional[3] if len(positional) > 3 else None)
    assert status == "filled"


async def test_handle_fill_event_status_partial_when_partially_filled(
    tracker, order_repo
):
    """
    _handle_fill_event sets status='partial' when filled_qty < order.quantity.
    """
    order = _make_order(price=Decimal("50"), quantity=10)
    order_repo.get_by_kalshi_id.return_value = order

    msg = {
        "type": "fill",
        "order_id": order.kalshi_order_id,
        "yes_price": 51,
        "count": 3,  # partial fill
    }

    await tracker._handle_fill_event(msg)

    call_kwargs = order_repo.update_fill.call_args
    positional = call_kwargs.args
    keyword = call_kwargs.kwargs
    status = keyword.get("status") or (positional[3] if len(positional) > 3 else None)
    assert status == "partial"


# ---------------------------------------------------------------------------
# Test: _handle_fill_event — unknown order (warning, no crash)
# ---------------------------------------------------------------------------


async def test_handle_fill_event_unknown_order_id_logs_warning_no_crash(
    tracker, order_repo
):
    """
    _handle_fill_event with an unknown order_id should log a warning and return
    without calling update_fill or raising.
    """
    order_repo.get_by_kalshi_id.return_value = None

    msg = {
        "type": "fill",
        "order_id": "nonexistent-order-id",
        "yes_price": 55,
        "count": 2,
    }

    # Should not raise
    await tracker._handle_fill_event(msg)

    order_repo.update_fill.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test: _cancel_stale_orders — normal path
# ---------------------------------------------------------------------------


async def test_cancel_stale_orders_cancels_each_order(
    tracker, kalshi_client, order_repo
):
    """
    _cancel_stale_orders should call REST cancel + DB cancel for each stale order.
    """
    stale1 = _make_order(kalshi_order_id="kalshi-stale-001")
    stale2 = _make_order(kalshi_order_id="kalshi-stale-002")
    order_repo.get_stale_orders.return_value = [stale1, stale2]
    kalshi_client.cancel_order.return_value = {}

    await tracker._cancel_stale_orders()

    order_repo.get_stale_orders.assert_awaited_once_with(900)
    assert kalshi_client.cancel_order.await_count == 2
    assert order_repo.cancel_order.await_count == 2

    # Verify correct Kalshi IDs were used for REST cancel
    rest_cancel_ids = [
        call.args[0] for call in kalshi_client.cancel_order.call_args_list
    ]
    assert "kalshi-stale-001" in rest_cancel_ids
    assert "kalshi-stale-002" in rest_cancel_ids


# ---------------------------------------------------------------------------
# Test: _cancel_stale_orders — REST 404 handled gracefully
# ---------------------------------------------------------------------------


async def test_cancel_stale_orders_handles_rest_404_gracefully(
    tracker, kalshi_client, order_repo
):
    """
    _cancel_stale_orders should still cancel in DB even if REST cancel raises
    (e.g. 404 = already filled).
    """
    stale = _make_order(kalshi_order_id="kalshi-already-filled")
    order_repo.get_stale_orders.return_value = [stale]
    kalshi_client.cancel_order.side_effect = Exception("404 Not Found")

    # Should not raise
    await tracker._cancel_stale_orders()

    # DB cancel should still be attempted
    order_repo.cancel_order.assert_awaited_once_with(stale.id)


# ---------------------------------------------------------------------------
# Test: _sync_orders_from_rest — reconciles missed fills
# ---------------------------------------------------------------------------


async def test_sync_orders_from_rest_updates_pending_orders_to_filled(
    tracker, kalshi_client, order_repo
):
    """
    _sync_orders_from_rest should update DB for orders that REST reports as filled
    but DB still shows pending.
    """
    pending_order = _make_order(
        kalshi_order_id="kalshi-missed-fill",
        price=Decimal("50"),
        quantity=5,
        status="pending",
    )

    # REST returns a filled order
    rest_order = MagicMock()
    rest_order.order_id = "kalshi-missed-fill"
    rest_order.status = "filled"
    rest_order.yes_price = 51
    rest_order.count = 5
    kalshi_client.get_orders.return_value = [rest_order]

    # DB lookup finds the pending order
    order_repo.get_by_kalshi_id.return_value = pending_order

    await tracker._sync_orders_from_rest()

    kalshi_client.get_orders.assert_awaited_once_with(status="filled")
    order_repo.get_by_kalshi_id.assert_awaited_once_with("kalshi-missed-fill")
    order_repo.update_fill.assert_awaited_once()


async def test_sync_orders_from_rest_skips_already_filled_db_orders(
    tracker, kalshi_client, order_repo
):
    """
    _sync_orders_from_rest should skip orders that are already filled in DB.
    """
    already_filled = _make_order(
        kalshi_order_id="kalshi-already-in-db",
        status="filled",
    )

    rest_order = MagicMock()
    rest_order.order_id = "kalshi-already-in-db"
    rest_order.status = "filled"
    rest_order.yes_price = 50
    rest_order.count = 5
    kalshi_client.get_orders.return_value = [rest_order]

    # DB shows already filled
    order_repo.get_by_kalshi_id.return_value = already_filled

    await tracker._sync_orders_from_rest()

    order_repo.update_fill.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test: run() starts all three loops concurrently
# ---------------------------------------------------------------------------


async def test_run_starts_all_three_loops(tracker, ws_client):
    """
    run() should start all three loops concurrently. We verify by immediately
    setting stop_event and confirming the method returns cleanly.
    """
    stop_event = asyncio.Event()
    stop_event.set()  # immediately stop

    # ws_client.run should be called; make it return quickly
    ws_client.run = AsyncMock(return_value=None)

    # Patch internal loops to avoid actual async I/O
    with patch.object(tracker, "_ws_fill_loop", new=AsyncMock()) as mock_ws, \
         patch.object(tracker, "_stale_canceller_loop", new=AsyncMock()) as mock_stale, \
         patch.object(tracker, "_rest_polling_loop", new=AsyncMock()) as mock_rest:

        await tracker.run(stop_event)

        mock_ws.assert_awaited_once_with(stop_event)
        mock_stale.assert_awaited_once_with(stop_event)
        mock_rest.assert_awaited_once_with(stop_event)
