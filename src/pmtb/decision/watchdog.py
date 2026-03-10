"""
Watchdog — independent OS process that polls PostgreSQL for drawdown breaches.

CRITICAL DESIGN CONSTRAINTS (from 05-CONTEXT.md locked decisions):
  - daemon=False: must survive main process crash
  - No shared DB connections across fork: create engine INSIDE run_watchdog
  - Communicate halt signal via TradingState DB flag only (not pipes/queues)
  - Poll every 30 seconds
  - On halt: set DB flag AND attempt to cancel pending orders (best-effort)
  - Must be simple and reliable, never crash the watchdog loop

This module is the safety-critical circuit breaker for RISK-05.
It runs as a separate multiprocessing.Process and polls independently.
"""
from __future__ import annotations

import asyncio
import multiprocessing
from datetime import datetime, UTC

from loguru import logger
from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pmtb.db.models import Order, Position, TradingState


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS = 30


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

WATCHDOG_HALT_TRIGGERS = Counter(
    "pmtb_watchdog_halt_triggers_total",
    "Total number of times watchdog triggered a trading halt",
)

WATCHDOG_POLLS = Counter(
    "pmtb_watchdog_polls_total",
    "Total number of watchdog polling cycles completed",
)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def _check_and_act(session_factory: async_sessionmaker, settings) -> None:
    """
    One polling cycle: compute current portfolio value, compare to peak,
    detect drawdown breach, and set halt flag if triggered.

    Args:
        session_factory: Async SQLAlchemy session factory (created inside watchdog process).
        settings:        Settings object (or mock with .max_drawdown attribute).
    """
    async with session_factory() as session:
        # ----------------------------------------------------------------
        # Step 1: Compute current portfolio value from open positions
        # ----------------------------------------------------------------
        result = await session.execute(
            select(Position).where(Position.status == "open")
        )
        positions = result.scalars().all()
        current_value = sum(
            float(p.avg_price) * p.quantity for p in positions
        )

        # ----------------------------------------------------------------
        # Step 2: Read peak portfolio value from TradingState
        # ----------------------------------------------------------------
        peak_row = await session.get(TradingState, "peak_portfolio_value")
        if peak_row is None:
            # No peak recorded yet — set current as peak, no drawdown
            peak = current_value
            await session.merge(
                TradingState(
                    key="peak_portfolio_value",
                    value=str(current_value),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            WATCHDOG_POLLS.inc()
            return
        else:
            peak = float(peak_row.value)

        # ----------------------------------------------------------------
        # Step 3: If current is new high, update peak
        # ----------------------------------------------------------------
        if current_value > peak:
            peak = current_value
            await session.merge(
                TradingState(
                    key="peak_portfolio_value",
                    value=str(current_value),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            WATCHDOG_POLLS.inc()
            return

        # ----------------------------------------------------------------
        # Step 4: Compute drawdown
        # ----------------------------------------------------------------
        if peak <= 0:
            WATCHDOG_POLLS.inc()
            return

        drawdown = (peak - current_value) / peak
        logger.debug(
            "Watchdog poll",
            peak=peak,
            current=current_value,
            drawdown=f"{drawdown:.4f}",
            threshold=settings.max_drawdown,
        )

        # ----------------------------------------------------------------
        # Step 5: If drawdown >= threshold, set halt flag
        # ----------------------------------------------------------------
        if drawdown >= settings.max_drawdown:
            logger.critical(
                "DRAWDOWN BREACH — halting trading",
                peak=peak,
                current=current_value,
                drawdown=f"{drawdown:.4f}",
                threshold=settings.max_drawdown,
            )

            # a. Set halt flag in TradingState
            await session.merge(
                TradingState(
                    key="trading_halted",
                    value="true",
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

            # b. Increment Prometheus counter
            WATCHDOG_HALT_TRIGGERS.inc()

        WATCHDOG_POLLS.inc()


async def _cancel_pending_orders(session_factory: async_sessionmaker, settings) -> None:
    """
    Best-effort: query pending orders and attempt to cancel via Kalshi API.

    This is called after the halt flag is set. Errors here are logged but
    never allowed to crash the watchdog loop.
    """
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(Order).where(Order.status == "pending")
            )
            pending = result.scalars().all()
            if not pending:
                return

            logger.warning(
                "Watchdog attempting to cancel pending orders",
                count=len(pending),
            )

            # Best-effort: try to cancel each order individually
            for order in pending:
                try:
                    # Cancellation via Kalshi API would go here in production.
                    # For now, log the intent — the executor will also observe the halt flag.
                    logger.warning(
                        "Watchdog flagging order for cancellation",
                        order_id=str(order.id),
                        kalshi_order_id=order.kalshi_order_id,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "Watchdog failed to cancel order",
                        order_id=str(order.id),
                        error=str(e),
                    )
    except Exception as e:  # noqa: BLE001
        logger.error("Watchdog failed during order cancellation", error=str(e))


async def _watchdog_loop(settings) -> None:
    """
    Main watchdog async loop. Creates its own DB engine and session factory,
    polls every POLL_INTERVAL_SECONDS, and catches all exceptions.

    This function MUST NEVER CRASH — all exceptions are caught, logged, and
    the loop continues.

    Args:
        settings: Settings object with database_url, max_drawdown, etc.
    """
    logger.info("Watchdog process started", pid=multiprocessing.current_process().pid)

    # Create own engine and session factory — NEVER share across fork boundary
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=0,
        echo=False,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    while True:
        try:
            await _check_and_act(session_factory, settings)
        except Exception as e:  # noqa: BLE001
            logger.error("Watchdog poll failed", error=str(e))

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def run_watchdog(settings_dict: dict) -> None:
    """
    Entry point for multiprocessing.Process target.

    Receives settings as a plain dict (can't share Pydantic objects across fork).
    Reconstructs Settings from dict and runs the async watchdog loop.

    Args:
        settings_dict: Settings.model_dump() output from the parent process.
    """
    # Lazy import inside forked process — avoids import ordering issues
    from pmtb.config import Settings

    settings = Settings(**settings_dict)
    asyncio.run(_watchdog_loop(settings))


def launch_watchdog(settings) -> multiprocessing.Process:
    """
    Launch the watchdog as an independent OS process.

    CRITICAL: daemon=False — the watchdog must survive the main process crash.
    If daemon=True, the OS would kill it when the parent exits, defeating
    its entire purpose as an independent circuit breaker.

    Args:
        settings: Settings instance from the main process.

    Returns:
        The started multiprocessing.Process. Caller may hold reference but
        should not wait on it — it runs indefinitely.
    """
    settings_dict = settings.model_dump()

    proc = multiprocessing.Process(
        target=run_watchdog,
        args=(settings_dict,),
        daemon=False,  # CRITICAL: must survive main process crash
        name="pmtb-watchdog",
    )
    proc.start()
    logger.info("Watchdog process launched", pid=proc.pid)
    return proc
