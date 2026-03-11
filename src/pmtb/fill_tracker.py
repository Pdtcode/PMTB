"""
FillTracker — order fill lifecycle management.

Tracks order fills in real time via three concurrent async loops:

1. WS fill loop  — subscribes to Kalshi fill channel, processes fill events.
2. Stale canceller — every 60 s checks for pending orders past timeout and cancels.
3. REST polling  — safety net that polls REST every (timeout/2) s for missed fills.

On each fill event the slippage (fill_price - expected_price) is computed, logged
and observed in a Prometheus histogram. All three loops log errors without crashing
so a single transient failure cannot take down the trading bot.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from loguru import logger
from prometheus_client import Counter, Histogram


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

FILL_EVENTS_TOTAL = Counter(
    "pmtb_fill_events_total",
    "Fill events processed from WS or REST",
)

STALE_CANCELLATIONS_TOTAL = Counter(
    "pmtb_stale_cancellations_total",
    "Stale orders cancelled",
)

FILL_SLIPPAGE_CENTS = Histogram(
    "pmtb_fill_slippage_cents",
    "Slippage in cents per fill",
    buckets=[-10, -5, -3, -1, 0, 1, 3, 5, 10, 20, 50],
)


# ---------------------------------------------------------------------------
# FillTracker
# ---------------------------------------------------------------------------


class FillTracker:
    """
    Concurrent fill-tracking service for order lifecycle management.

    Args:
        ws_client:   KalshiWSClient — used for fill event subscription.
        kalshi_client: KalshiClient (REST) — used for REST cancel + polling.
        order_repo:  OrderRepository — DB persistence layer.
        settings:    Settings — provides stale_order_timeout_seconds.
    """

    def __init__(self, ws_client, kalshi_client, order_repo, settings) -> None:
        self._ws = ws_client
        self._rest = kalshi_client
        self._repo = order_repo
        self._settings = settings

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, stop_event: asyncio.Event) -> None:
        """
        Run three concurrent loops until stop_event is set.

        Loops:
            1. _ws_fill_loop        — real-time WS fill events
            2. _stale_canceller_loop — periodic stale order cleanup
            3. _rest_polling_loop   — periodic REST reconciliation fallback
        """
        await asyncio.gather(
            self._ws_fill_loop(stop_event),
            self._stale_canceller_loop(stop_event),
            self._rest_polling_loop(stop_event),
        )

    # ------------------------------------------------------------------
    # Loop 1: WebSocket fill events
    # ------------------------------------------------------------------

    async def _ws_fill_loop(self, stop_event: asyncio.Event) -> None:
        """
        Subscribe to Kalshi fill channel and process fill events.

        Uses KalshiWSClient.run() with an account-level subscription
        (empty market_tickers list). The loop exits cleanly when stop_event
        is set. Errors are logged without crashing.
        """
        try:
            async def on_message(msg: dict) -> None:
                if msg.get("type") == "fill":
                    await self._handle_fill_event(msg)

            ws_task = asyncio.create_task(
                self._ws.run(on_message, channels=["fill"], market_tickers=[])
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=None)
            finally:
                ws_task.cancel()
                try:
                    await ws_task
                except (asyncio.CancelledError, Exception):
                    pass
        except Exception:
            logger.exception("FillTracker WS fill loop error")

    # ------------------------------------------------------------------
    # Loop 2: Stale order cancellation
    # ------------------------------------------------------------------

    async def _stale_canceller_loop(self, stop_event: asyncio.Event) -> None:
        """
        Every 60 seconds query for stale orders and cancel them.

        An order is considered stale if it has been pending for longer than
        settings.stale_order_timeout_seconds. Each stale order is cancelled
        via REST first (404 handled gracefully — means already filled) then
        marked cancelled in the DB.
        """
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60.0)
                # stop_event fired — exit loop
                break
            except asyncio.TimeoutError:
                # 60 s elapsed — run cancellation pass
                pass

            try:
                await self._cancel_stale_orders()
            except Exception:
                logger.exception("FillTracker stale canceller loop error")

    # ------------------------------------------------------------------
    # Loop 3: REST polling (safety net)
    # ------------------------------------------------------------------

    async def _rest_polling_loop(self, stop_event: asyncio.Event) -> None:
        """
        Every (stale_order_timeout_seconds // 2) seconds fetch REST fill list
        and reconcile against DB state to catch any WS-missed fills.
        """
        interval = self._settings.stale_order_timeout_seconds // 2

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=float(interval))
                break
            except asyncio.TimeoutError:
                pass

            try:
                await self._sync_orders_from_rest()
            except Exception:
                logger.exception("FillTracker REST polling loop error")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _handle_fill_event(self, msg: dict) -> None:
        """
        Process a single fill event message.

        Extracts order_id, fill_price, and filled_qty from the message,
        looks up the order in DB, computes slippage, and calls update_fill.

        Args:
            msg: Raw fill event dict from Kalshi WebSocket or REST.
        """
        kalshi_order_id = msg.get("order_id")
        fill_price = msg.get("yes_price", msg.get("fill_price"))
        filled_qty = msg.get("count", 0)

        order = await self._repo.get_by_kalshi_id(kalshi_order_id)
        if order is None:
            logger.warning(
                "FillTracker: fill event for unknown order",
                kalshi_order_id=kalshi_order_id,
            )
            return

        # Slippage: positive = filled at higher price than expected (good for seller)
        slippage_cents = fill_price - float(order.price)
        logger.bind(
            kalshi_order_id=kalshi_order_id,
            fill_price=fill_price,
            expected_price=float(order.price),
            slippage_cents=slippage_cents,
            filled_qty=filled_qty,
        ).info("FillTracker: fill event processed")

        FILL_SLIPPAGE_CENTS.observe(slippage_cents)
        FILL_EVENTS_TOTAL.inc()

        status = "filled" if filled_qty >= order.quantity else "partial"
        await self._repo.update_fill(
            order_id=order.id,
            fill_price=fill_price,
            filled_qty=filled_qty,
            status=status,
        )

    async def _cancel_stale_orders(self) -> None:
        """
        Query stale pending orders and cancel each via REST + DB.

        Handles REST 404 gracefully — a 404 means the order was already filled
        on Kalshi's side; the DB cancel still proceeds so our state stays consistent.
        """
        stale_orders = await self._repo.get_stale_orders(
            self._settings.stale_order_timeout_seconds
        )

        for order in stale_orders:
            try:
                await self._rest.cancel_order(order.kalshi_order_id)
            except Exception:
                logger.warning(
                    "FillTracker: REST cancel failed (possibly already filled)",
                    kalshi_order_id=order.kalshi_order_id,
                )

            await self._repo.cancel_order(order.id)
            STALE_CANCELLATIONS_TOTAL.inc()
            logger.info(
                "FillTracker: stale order cancelled",
                order_id=str(order.id),
                kalshi_order_id=order.kalshi_order_id,
            )

    async def _sync_orders_from_rest(self) -> None:
        """
        Fetch filled orders from REST and update DB for any that are still
        showing as pending.

        This is a safety net for WebSocket messages that were lost during
        reconnects or outages.
        """
        rest_filled = await self._rest.get_orders(status="filled")

        for rest_order in rest_filled:
            # REST order objects may use .order_id or .id — try both
            kalshi_order_id = getattr(rest_order, "order_id", None) or getattr(
                rest_order, "id", None
            )
            if kalshi_order_id is None:
                continue

            db_order = await self._repo.get_by_kalshi_id(kalshi_order_id)
            if db_order is None or db_order.status != "pending":
                # Already reconciled or not tracked
                continue

            fill_price = getattr(rest_order, "yes_price", None) or getattr(
                rest_order, "fill_price", None
            )
            filled_qty = getattr(rest_order, "count", None) or getattr(
                rest_order, "filled_quantity", 0
            )

            logger.info(
                "FillTracker: REST reconciliation — updating missed fill",
                kalshi_order_id=kalshi_order_id,
                fill_price=fill_price,
                filled_qty=filled_qty,
            )

            FILL_EVENTS_TOTAL.inc()
            status = "filled" if (filled_qty or 0) >= db_order.quantity else "partial"
            await self._repo.update_fill(
                order_id=db_order.id,
                fill_price=fill_price,
                filled_qty=filled_qty,
                status=status,
            )
