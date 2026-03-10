"""
PositionTracker — async in-memory position dictionary synced from DB.

Provides fast O(1) position lookup without DB round-trips for the hot path
(RiskManager checks). DB is the source of truth at startup; in-process dict
tracks live state as trades execute.

Thread safety: all mutations go through asyncio.Lock. Safe for concurrent
async tasks within a single event loop.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from pmtb.db.models import Position

if TYPE_CHECKING:
    pass


class PositionTracker:
    """
    In-memory position dictionary keyed by market ticker.

    Loaded from DB at startup and kept in sync as the executor opens/closes positions.
    RiskManager queries this instead of the DB for low-latency risk checks.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        """
        Args:
            session_factory: Async SQLAlchemy session factory (async_sessionmaker).
        """
        self._session_factory = session_factory
        self._positions: dict[str, Position] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """
        Load all open positions from the DB and populate the internal dict.

        Queries Position (status="open") with Market eagerly loaded via selectinload
        to avoid lazy-loading issues in async context. Keyed by market ticker.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(Position)
                .options(selectinload(Position.market))
                .where(Position.status == "open")
            )
            open_positions = result.scalars().all()

        async with self._lock:
            self._positions = {p.market.ticker: p for p in open_positions}

    async def has_position(self, ticker: str) -> bool:
        """Return True if an open position exists for the given ticker."""
        async with self._lock:
            return ticker in self._positions

    async def get_all(self) -> list[Position]:
        """Return all currently tracked open positions as a list."""
        async with self._lock:
            return list(self._positions.values())

    async def total_exposure(self) -> float:
        """
        Compute total portfolio exposure in dollars.

        Returns sum of (quantity * avg_price) for all tracked positions.
        Result is float (not Decimal) for compatibility with float-based risk math.
        """
        async with self._lock:
            return sum(
                float(p.avg_price) * p.quantity for p in self._positions.values()
            )

    async def add_position(self, ticker: str, position: Position) -> None:
        """Add or replace a position for the given ticker."""
        async with self._lock:
            self._positions[ticker] = position

    async def remove_position(self, ticker: str) -> None:
        """Remove a position for the given ticker. No-op if not present."""
        async with self._lock:
            self._positions.pop(ticker, None)

    async def position_count(self) -> int:
        """Return the number of currently tracked positions."""
        async with self._lock:
            return len(self._positions)
