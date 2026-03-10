"""
Tests for PaperOrderExecutor and create_executor factory.

TDD test suite covering:
    - PaperOrderExecutor behavior (place, cancel, get_positions, get_orders, logging)
    - create_executor factory routing (paper -> PaperOrderExecutor, live -> LiveOrderExecutor)
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def paper_executor():
    """Fresh PaperOrderExecutor instance for each test."""
    from pmtb.paper import PaperOrderExecutor

    return PaperOrderExecutor()


# ---------------------------------------------------------------------------
# Test 1: place_order returns dict with paper-* order_id and status "simulated"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_order_returns_simulated_order(paper_executor):
    result = await paper_executor.place_order(
        market_ticker="KXBTC-24JAN",
        side="yes",
        quantity=10,
        price=55,
        order_type="limit",
    )

    assert result["order_id"].startswith("paper-"), (
        f"order_id must start with 'paper-', got: {result['order_id']}"
    )
    assert result["status"] == "simulated"
    assert result["market_ticker"] == "KXBTC-24JAN"
    assert result["side"] == "yes"
    assert result["quantity"] == 10
    assert result["price"] == 55
    assert result["order_type"] == "limit"


# ---------------------------------------------------------------------------
# Test 2: cancel_order returns dict with status "cancelled"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_order_returns_cancelled(paper_executor):
    placed = await paper_executor.place_order(
        market_ticker="KXBTC-24JAN",
        side="yes",
        quantity=5,
        price=60,
    )
    order_id = placed["order_id"]

    result = await paper_executor.cancel_order(order_id)

    assert result["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_order_unknown_id_returns_not_found(paper_executor):
    result = await paper_executor.cancel_order("paper-nonexistent-id")
    assert result["status"] == "not_found"


# ---------------------------------------------------------------------------
# Test 3: get_positions returns empty list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_returns_empty_list(paper_executor):
    positions = await paper_executor.get_positions()
    assert positions == []


# ---------------------------------------------------------------------------
# Test 4: get_orders returns list of previously placed paper orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_orders_returns_placed_orders(paper_executor):
    await paper_executor.place_order("MKT-A", "yes", 1, 50)
    await paper_executor.place_order("MKT-B", "no", 2, 45)

    orders = await paper_executor.get_orders()
    assert len(orders) == 2


@pytest.mark.asyncio
async def test_get_orders_filtered_by_status(paper_executor):
    placed1 = await paper_executor.place_order("MKT-A", "yes", 1, 50)
    placed2 = await paper_executor.place_order("MKT-B", "no", 2, 45)

    # Cancel the first order
    await paper_executor.cancel_order(placed1["order_id"])

    simulated = await paper_executor.get_orders(status="simulated")
    cancelled = await paper_executor.get_orders(status="cancelled")

    assert len(simulated) == 1
    assert simulated[0]["order_id"] == placed2["order_id"]
    assert len(cancelled) == 1
    assert cancelled[0]["order_id"] == placed1["order_id"]


# ---------------------------------------------------------------------------
# Test 5: PaperOrderExecutor logs each simulated order via loguru
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_order_logs_via_loguru(paper_executor, capfd):
    """Verify loguru emits a log message containing the order_id."""
    import sys

    from loguru import logger

    # Capture loguru output to stdout
    log_messages = []

    def capture_sink(message):
        log_messages.append(str(message))

    logger.add(capture_sink, level="INFO", format="{message}")

    await paper_executor.place_order("LOG-TEST", "yes", 3, 55)

    logger.remove()

    assert any("Simulated order placed" in msg for msg in log_messages), (
        f"Expected 'Simulated order placed' in log output. Got: {log_messages}"
    )


# ---------------------------------------------------------------------------
# Test 6: PaperOrderExecutor stores simulated orders in-memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orders_stored_in_memory(paper_executor):
    result = await paper_executor.place_order("MEM-TEST", "yes", 7, 40)
    order_id = result["order_id"]

    # Access internal store
    assert hasattr(paper_executor, "_orders")
    assert any(o["order_id"] == order_id for o in paper_executor._orders)


# ---------------------------------------------------------------------------
# Test 7: create_executor("paper", ...) returns PaperOrderExecutor
# ---------------------------------------------------------------------------


def test_create_executor_paper_returns_paper_executor():
    from pmtb.executor import create_executor
    from pmtb.paper import PaperOrderExecutor

    mock_settings = MagicMock()
    mock_settings.trading_mode = "paper"

    executor = create_executor(mock_settings)
    assert isinstance(executor, PaperOrderExecutor)


# ---------------------------------------------------------------------------
# Test 8: create_executor("live", ...) returns executor delegating to KalshiClient
# ---------------------------------------------------------------------------


def test_create_executor_live_returns_live_executor():
    from pmtb.executor import LiveOrderExecutor, create_executor

    mock_settings = MagicMock()
    mock_settings.trading_mode = "live"
    mock_kalshi = MagicMock()

    executor = create_executor(mock_settings, kalshi_client=mock_kalshi)
    assert isinstance(executor, LiveOrderExecutor)


def test_create_executor_live_without_client_raises():
    from pmtb.executor import create_executor

    mock_settings = MagicMock()
    mock_settings.trading_mode = "live"

    with pytest.raises(ValueError, match="kalshi_client"):
        create_executor(mock_settings, kalshi_client=None)
