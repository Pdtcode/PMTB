"""
Paper trading executor for PMTB.

PaperOrderExecutor implements OrderExecutorProtocol with spread-aware fill simulation.
Orders are persisted to the same DB tables as live trading (with is_paper=True) when
a session_factory is provided, enabling accurate paper P&L tracking alongside real data.

Without a session_factory, the executor operates in legacy in-memory-only mode —
all order state is stored in _orders list. This preserves backward compatibility.

Used when Settings.trading_mode == "paper" (the default).
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from loguru import logger


class PaperOrderExecutor:
    """
    Simulated order executor with spread-aware fill simulation and optional DB persistence.

    Fill model:
        - fill_price = requested price (zero slippage for paper orders)
        - filled_quantity = max(1, int(quantity * random.uniform(0.5, 1.0)))
        - status = "filled" if filled_quantity == quantity else "partial"

    When session_factory is provided:
        - Orders are persisted to DB via OrderRepository with is_paper=True
        - A Trade audit row is created on fill (via update_fill)
        - cancel_order updates DB status as well as in-memory list

    Without session_factory (legacy mode):
        - All state is in-memory only; no DB writes
        - Backward compatible with pre-Plan-06-01 callers
    """

    def __init__(self, session_factory=None) -> None:
        self._orders: list[dict] = []
        self._repo = None
        if session_factory is not None:
            from pmtb.order_repo import OrderRepository
            self._repo = OrderRepository(session_factory)

    async def place_order(
        self,
        market_ticker: str,
        side: str,
        quantity: int,
        price: int,
        order_type: str = "limit",
    ) -> dict:
        """
        Simulate placing a limit order with spread-aware fill.

        Generates a unique paper-{uuid4} order_id, simulates a fill using
        _simulate_fill(), stores the order in-memory, persists to DB if repo
        is present, and returns the order dict.

        Args:
            market_ticker: Kalshi ticker string.
            side: "yes" or "no".
            quantity: Number of contracts.
            price: Limit price in cents.
            order_type: Order type (default "limit").

        Returns:
            Order dict with order_id, status, fill_price, filled_quantity, etc.
        """
        order_id = f"paper-{uuid4()}"
        fill_result = self._simulate_fill(quantity=quantity, price=price)

        order = {
            "order_id": order_id,
            "market_ticker": market_ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "order_type": order_type,
            "status": fill_result["status"],
            "fill_price": fill_result["fill_price"],
            "filled_quantity": fill_result["filled_quantity"],
            "slippage_cents": fill_result["slippage_cents"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._orders.append(order)

        logger.bind(paper_mode=True).info(
            "Simulated order placed",
            order_id=order_id,
            market_ticker=market_ticker,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            fill_status=fill_result["status"],
            filled_quantity=fill_result["filled_quantity"],
        )

        if self._repo is not None:
            db_order = await self._repo.create_order(
                market_ticker=market_ticker,
                side=side,
                quantity=quantity,
                price=Decimal(str(price)),
                kalshi_order_id=order_id,
                is_paper=True,
            )
            await self._repo.update_fill(
                order_id=db_order.id,
                fill_price=Decimal(str(fill_result["fill_price"])),
                filled_qty=fill_result["filled_quantity"],
                status=fill_result["status"],
            )

        return order

    def _simulate_fill(self, quantity: int, price: int) -> dict:
        """
        Simulate a spread-aware fill for a paper order.

        Paper mode semantics:
            - No slippage: fill_price == requested price
            - Partial fill probability: filled_qty = max(1, int(qty * U[0.5, 1.0]))
            - Status is "filled" iff filled_qty == quantity, otherwise "partial"

        Args:
            quantity: Requested contract quantity.
            price: Requested price in cents.

        Returns:
            dict with keys: status, fill_price, filled_quantity, slippage_cents.
        """
        filled_qty = max(1, int(quantity * random.uniform(0.5, 1.0)))
        status = "filled" if filled_qty == quantity else "partial"
        return {
            "status": status,
            "fill_price": price,
            "filled_quantity": filled_qty,
            "slippage_cents": 0,
        }

    async def cancel_order(self, order_id: str) -> dict:
        """
        Cancel an order by ID.

        Updates in-memory status and DB status (if repo present).

        Args:
            order_id: The paper order ID string.

        Returns:
            dict with status "cancelled" or "not_found".
        """
        for order in self._orders:
            if order["order_id"] == order_id:
                order["status"] = "cancelled"
                if self._repo is not None:
                    db_order = await self._repo.get_by_kalshi_id(order_id)
                    if db_order is not None:
                        await self._repo.cancel_order(db_order.id)
                return {"status": "cancelled", "order_id": order_id}
        return {"status": "not_found", "order_id": order_id}

    async def get_positions(self) -> list:
        """Paper mode has no real positions — always returns empty list."""
        return []

    async def get_orders(self, status: str | None = None) -> list:
        """
        Return simulated orders, optionally filtered by status.

        Args:
            status: If provided, filter to orders with this status value.

        Returns:
            List of order dicts (references to in-memory objects).
        """
        if status is None:
            return list(self._orders)
        return [o for o in self._orders if o["status"] == status]
