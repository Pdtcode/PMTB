"""
Order executor protocol and factory for PMTB.

Defines OrderExecutorProtocol — the interface all downstream phases use for
order placement. Never import KalshiClient directly for order operations;
use create_executor() instead.

Factory selects executor based on Settings.trading_mode:
    "paper" -> PaperOrderExecutor (no-op, simulated)
    "live"  -> LiveOrderExecutor (delegates to KalshiClient)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pmtb.config import Settings


@runtime_checkable
class OrderExecutorProtocol(Protocol):
    """
    Protocol defining the interface for order execution.

    All executors (paper and live) must implement these async methods.
    Downstream code depends only on this protocol — never on concrete classes.
    """

    async def place_order(
        self,
        market_ticker: str,
        side: str,
        quantity: int,
        price: int,
        order_type: str = "limit",
    ) -> dict:
        """Place an order. Returns order dict with order_id and status."""
        ...

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an order by ID. Returns status dict."""
        ...

    async def get_positions(self) -> list:
        """Return current open positions."""
        ...

    async def get_orders(self, status: str | None = None) -> list:
        """Return orders, optionally filtered by status."""
        ...


class LiveOrderExecutor:
    """
    Delegates all order operations to a real KalshiClient instance.

    Used when trading_mode == "live". Requires a KalshiClient to be provided
    via create_executor().
    """

    def __init__(self, kalshi_client) -> None:
        self._client = kalshi_client

    async def place_order(
        self,
        market_ticker: str,
        side: str,
        quantity: int,
        price: int,
        order_type: str = "limit",
    ) -> dict:
        return await self._client.place_order(
            market_ticker=market_ticker,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
        )

    async def cancel_order(self, order_id: str) -> dict:
        return await self._client.cancel_order(order_id)

    async def get_positions(self) -> list:
        return await self._client.get_positions()

    async def get_orders(self, status: str | None = None) -> list:
        return await self._client.get_orders(status=status)


def create_executor(
    settings: "Settings",
    kalshi_client=None,
    session_factory=None,
) -> OrderExecutorProtocol:
    """
    Factory: returns the correct executor based on settings.trading_mode.

    Args:
        settings: Application Settings instance.
        kalshi_client: KalshiClient instance required when trading_mode=="live".
        session_factory: Optional async_sessionmaker for DB persistence.
            When provided and trading_mode=="paper", PaperOrderExecutor will
            persist orders to DB with is_paper=True.

    Returns:
        PaperOrderExecutor if trading_mode=="paper".
        LiveOrderExecutor if trading_mode=="live".

    Raises:
        ValueError: if trading_mode=="live" and kalshi_client is None.
    """
    from pmtb.paper import PaperOrderExecutor

    if settings.trading_mode == "paper":
        return PaperOrderExecutor(session_factory=session_factory)

    if settings.trading_mode == "live":
        if kalshi_client is None:
            raise ValueError(
                "kalshi_client must be provided when trading_mode is 'live'"
            )
        return LiveOrderExecutor(kalshi_client)

    raise ValueError(f"Unknown trading_mode: {settings.trading_mode!r}")
