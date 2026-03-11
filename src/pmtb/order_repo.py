"""
OrderRepository — CRUD layer for Order and Trade persistence.

All methods open their own session via the injected session_factory.
This allows callers (PaperOrderExecutor, FillTracker, PipelineOrchestrator)
to depend on a single factory reference without managing sessions themselves.
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from pmtb.db.models import Market, Order, Trade


class OrderRepository:
    """
    CRUD interface for Order and Trade lifecycle management.

    Handles market row resolution by ticker (get-or-create), order creation,
    fill updates (including Trade audit row), cancellation, and stale order scanning.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def _get_or_create_market(self, session, ticker: str) -> Market:
        """
        Return Market for ticker, creating a placeholder row if it doesn't exist yet.

        The placeholder uses ticker as title, "unknown" category, and a far-future
        close_time. Real market data is written by the scanner/enrichment layer;
        this ensures order rows can always be persisted without a dependency on
        the scanner completing first.
        """
        result = await session.execute(
            select(Market).where(Market.ticker == ticker)
        )
        market = result.scalar_one_or_none()
        if market is None:
            market = Market(
                ticker=ticker,
                title=ticker,
                category="unknown",
                status="active",
                close_time=datetime(2099, 12, 31, tzinfo=UTC),
            )
            session.add(market)
            await session.flush()  # assign market.id before we reference it
        return market

    async def create_order(
        self,
        market_ticker: str,
        side: str,
        quantity: int,
        price: Decimal,
        kalshi_order_id: str,
        is_paper: bool = False,
    ) -> Order:
        """
        Persist a new Order row with status="pending".

        Resolves the market_id by ticker (creating a placeholder Market if needed).

        Args:
            market_ticker: Kalshi ticker string used to resolve/create the Market row.
            side: "yes" or "no".
            quantity: Number of contracts.
            price: Limit price as Decimal.
            kalshi_order_id: Kalshi-assigned order ID (or "paper-{uuid}" for paper orders).
            is_paper: True when created by PaperOrderExecutor.

        Returns:
            The persisted Order instance (with id and market_id populated).
        """
        async with self._session_factory() as session:
            async with session.begin():
                market = await self._get_or_create_market(session, market_ticker)
                order = Order(
                    market_id=market.id,
                    side=side,
                    quantity=quantity,
                    price=price,
                    order_type="limit",
                    status="pending",
                    kalshi_order_id=kalshi_order_id,
                    fill_price=None,
                    filled_quantity=0,
                    is_paper=is_paper,
                    placed_at=datetime.now(UTC),
                )
                session.add(order)
                await session.flush()
                # Detach from session so callers can use it without an open session
                await session.refresh(order)
        return order

    async def update_fill(
        self,
        order_id: uuid.UUID,
        fill_price: Decimal,
        filled_qty: int,
        status: str,
    ) -> None:
        """
        Update Order fill fields and create an immutable Trade audit row.

        Args:
            order_id: Internal UUID of the Order row.
            fill_price: Execution price.
            filled_qty: Number of contracts filled.
            status: New Order status ("filled" or "partial").
        """
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(Order).where(Order.id == order_id)
                )
                order = result.scalar_one()
                order.fill_price = fill_price
                order.filled_quantity = filled_qty
                order.status = status
                order.updated_at = datetime.now(UTC)

                trade = Trade(
                    order_id=order.id,
                    market_id=order.market_id,
                    side=order.side,
                    quantity=filled_qty,
                    price=fill_price,
                    pnl=None,
                )
                session.add(trade)

    async def cancel_order(self, order_id: uuid.UUID) -> None:
        """
        Set Order.status to "cancelled" and update updated_at.

        Args:
            order_id: Internal UUID of the Order row.
        """
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(Order).where(Order.id == order_id)
                )
                order = result.scalar_one()
                order.status = "cancelled"
                order.updated_at = datetime.now(UTC)

    async def get_by_kalshi_id(self, kalshi_order_id: str) -> Order | None:
        """
        Fetch an Order by its Kalshi-assigned order ID.

        Args:
            kalshi_order_id: The external order ID string.

        Returns:
            Order instance or None if not found.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(Order).where(Order.kalshi_order_id == kalshi_order_id)
            )
            return result.scalar_one_or_none()

    async def get_stale_orders(self, timeout_seconds: int) -> list[Order]:
        """
        Return pending orders whose placed_at is older than timeout_seconds ago.

        Used by the watchdog / FillTracker to cancel orders that never filled.

        Args:
            timeout_seconds: Age threshold in seconds.

        Returns:
            List of Order instances with status="pending" and placed_at before threshold.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
        async with self._session_factory() as session:
            result = await session.execute(
                select(Order).where(
                    Order.status == "pending",
                    Order.placed_at < cutoff,
                )
            )
            return list(result.scalars().all())
