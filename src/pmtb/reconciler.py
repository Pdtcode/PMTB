"""
Position reconciler for PMTB startup.

Compares Kalshi API state (open orders + positions) with DB state and resolves
all discrepancies to ensure the application starts with a consistent view of
portfolio state after crashes or restarts.

Reconciliation logic:
    - DB orders not found on Kalshi API -> mark status = "orphaned" (warning)
    - Kalshi orders not in DB -> insert into DB (info)
    - DB order status mismatches Kalshi status -> update DB to match (info)
    - Kalshi positions not in DB -> insert into DB (info)
    - DB positions not on Kalshi (closed externally) -> mark status = "closed" (warning)

Returns ReconciliationResult dataclass with counts of each action taken.
Logs a summary on completion.

Usage:
    result = await reconcile_positions(kalshi_client, session_factory)
    logger.info("Reconciliation complete", result=str(result))
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

from loguru import logger
from sqlalchemy import select

from pmtb.db.models import Order, Position


# Terminal order statuses — these are never on Kalshi's active list
_TERMINAL_STATUSES = {"filled", "cancelled", "expired", "orphaned"}


@dataclass
class ReconciliationResult:
    """Counts of actions taken during reconciliation."""

    orphaned_orders: int = 0    # In DB but not on Kalshi -> marked "orphaned"
    new_orders: int = 0         # On Kalshi but not in DB -> inserted
    updated_orders: int = 0     # Status mismatch -> DB updated
    new_positions: int = 0      # On Kalshi but not in DB -> inserted
    closed_positions: int = 0   # In DB but not on Kalshi -> marked "closed"

    def total_discrepancies(self) -> int:
        return (
            self.orphaned_orders
            + self.new_orders
            + self.updated_orders
            + self.new_positions
            + self.closed_positions
        )

    def __str__(self) -> str:
        return (
            f"ReconciliationResult("
            f"orphaned_orders={self.orphaned_orders}, "
            f"new_orders={self.new_orders}, "
            f"updated_orders={self.updated_orders}, "
            f"new_positions={self.new_positions}, "
            f"closed_positions={self.closed_positions})"
        )


async def reconcile_positions(
    kalshi_client,
    session_factory,
) -> ReconciliationResult:
    """
    Reconcile Kalshi API state with DB state.

    Fetches all open orders and positions from Kalshi API and all non-terminal
    DB orders + open DB positions, then resolves discrepancies.

    Args:
        kalshi_client:   KalshiClient instance to fetch API state.
        session_factory: Async session factory (callable returning context manager).

    Returns:
        ReconciliationResult with counts of each action taken.
    """
    result = ReconciliationResult()
    bound_logger = logger.bind(component="reconciler")

    bound_logger.info("Starting position reconciliation")

    # --- Fetch API state ---
    api_orders: list[dict] = await kalshi_client.get_orders()
    api_positions: list[dict] = await kalshi_client.get_positions()

    # Build lookup maps by order_id and ticker
    api_orders_by_id: dict[str, dict] = {
        o["order_id"]: o for o in api_orders
    }
    api_positions_by_ticker: dict[str, dict] = {
        p["ticker"]: p for p in api_positions
    }

    # --- Fetch DB state ---
    async with session_factory() as session:
        # Fetch all non-terminal orders (pending, resting, etc.)
        orders_query = select(Order).where(
            Order.status.not_in(list(_TERMINAL_STATUSES))
        )
        db_orders_result = await session.execute(orders_query)
        db_orders: list[Order] = db_orders_result.scalars().all()

        # Fetch all open positions
        positions_query = select(Position).where(Position.status == "open")
        db_positions_result = await session.execute(positions_query)
        db_positions: list[Position] = db_positions_result.scalars().all()

        # --- Reconcile orders ---
        db_order_ids = {o.kalshi_order_id for o in db_orders if o.kalshi_order_id}

        for db_order in db_orders:
            if db_order.kalshi_order_id is None:
                # Order was created locally (paper mode) — skip
                continue

            if db_order.kalshi_order_id not in api_orders_by_id:
                # Orphaned: in DB but not on Kalshi
                bound_logger.warning(
                    "Orphaned order detected — marking as orphaned",
                    kalshi_order_id=db_order.kalshi_order_id,
                    current_status=db_order.status,
                )
                db_order.status = "orphaned"
                result.orphaned_orders += 1
            else:
                # Check for status mismatch
                api_order = api_orders_by_id[db_order.kalshi_order_id]
                api_status = api_order.get("status", "")
                if api_status and db_order.status != api_status:
                    bound_logger.info(
                        "Order status mismatch — updating DB",
                        kalshi_order_id=db_order.kalshi_order_id,
                        db_status=db_order.status,
                        api_status=api_status,
                    )
                    db_order.status = api_status
                    result.updated_orders += 1

        # Check for API orders not in DB
        for order_id, api_order in api_orders_by_id.items():
            if order_id not in db_order_ids:
                bound_logger.info(
                    "New order on Kalshi not in DB — inserting",
                    kalshi_order_id=order_id,
                )
                # Insert minimal order record — full data will be filled by execution layer
                new_order = Order(
                    id=uuid.uuid4(),
                    market_id=uuid.uuid4(),  # placeholder — no market link available at reconciliation
                    side=api_order.get("side", "yes"),
                    quantity=api_order.get("count", 0),
                    price=api_order.get("yes_price", 0),
                    order_type=api_order.get("type", "limit"),
                    status=api_order.get("status", "resting"),
                    kalshi_order_id=order_id,
                    placed_at=datetime.now(UTC),
                )
                session.add(new_order)
                result.new_orders += 1

        # --- Reconcile positions ---
        db_position_tickers = {
            getattr(p, "ticker", None) for p in db_positions
        }

        for db_pos in db_positions:
            pos_ticker = getattr(db_pos, "ticker", None)
            if pos_ticker and pos_ticker not in api_positions_by_ticker:
                # Position closed externally
                bound_logger.warning(
                    "Position closed externally — marking as closed",
                    ticker=pos_ticker,
                )
                db_pos.status = "closed"
                result.closed_positions += 1

        # Check for API positions not in DB
        for ticker, api_pos in api_positions_by_ticker.items():
            if ticker not in db_position_tickers:
                bound_logger.info(
                    "New position on Kalshi not in DB — inserting",
                    ticker=ticker,
                )
                new_position = Position(
                    id=uuid.uuid4(),
                    market_id=uuid.uuid4(),  # placeholder
                    side="yes",  # default — position delta will correct
                    quantity=api_pos.get("position", 0),
                    avg_price=0,
                    status="open",
                    opened_at=datetime.now(UTC),
                )
                session.add(new_position)
                result.new_positions += 1

        await session.commit()

    total = result.total_discrepancies()
    bound_logger.info(
        "Position reconciliation complete",
        total_discrepancies=total,
        **{k: v for k, v in result.__dict__.items()},
    )

    return result
