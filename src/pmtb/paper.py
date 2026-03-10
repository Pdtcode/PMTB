"""
Paper trading executor for PMTB.

PaperOrderExecutor implements OrderExecutorProtocol with no-op order handling.
All orders are simulated: stored in-memory, assigned paper-{uuid} IDs, and
logged via loguru. No real orders are placed.

Used when Settings.trading_mode == "paper" (the default).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger


class PaperOrderExecutor:
    """
    Simulated order executor for paper trading.

    Stores all simulated orders in _orders (in-memory list). Orders get
    unique "paper-{uuid4}" IDs. No real exchange calls are made.
    """

    def __init__(self) -> None:
        self._orders: list[dict] = []

    async def place_order(
        self,
        market_ticker: str,
        side: str,
        quantity: int,
        price: int,
        order_type: str = "limit",
    ) -> dict:
        """
        Simulate placing an order.

        Generates a unique paper-{uuid4} order_id, records the order
        in-memory, logs via loguru, and returns the order dict.
        """
        order_id = f"paper-{uuid4()}"
        order = {
            "order_id": order_id,
            "market_ticker": market_ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "order_type": order_type,
            "status": "simulated",
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
        )

        return order

    async def cancel_order(self, order_id: str) -> dict:
        """
        Simulate cancelling an order.

        Finds the order in _orders by order_id, updates status to "cancelled",
        and returns a status dict. Returns {"status": "not_found"} if unknown.
        """
        for order in self._orders:
            if order["order_id"] == order_id:
                order["status"] = "cancelled"
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
