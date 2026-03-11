"""
PipelineOrchestrator — end-to-end pipeline loop for PMTB.

Responsibilities:
- Run full scan cycles every scan_interval_seconds:
    scanner -> research -> prediction -> decision -> execution
- Process WebSocket-triggered re-evaluations via a price event queue
- Run FillTracker concurrently via asyncio.gather
- Check trading halt flag before every order placement
- Handle stage failures gracefully (log and abort cycle, no crash)

Design decisions:
- asyncio.wait_for wraps each pipeline stage with stage_timeout_seconds
- _last_predictions and _last_candidates cache the most recent cycle results
  for WS re-evaluation without re-running the full expensive pipeline
- Limit price = int(p_market * 100) + price_offset_cents, clamped to 1-99
- TradingState "trading_halted" key is checked per-order (not per-cycle)
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from loguru import logger
from prometheus_client import Counter, Histogram

from pmtb.db.models import TradingState

if TYPE_CHECKING:
    from pmtb.config import Settings
    from pmtb.decision.pipeline import DecisionPipeline
    from pmtb.fill_tracker import FillTracker
    from pmtb.order_repo import OrderRepository
    from pmtb.scanner.scanner import MarketScanner
    from pmtb.research.pipeline import ResearchPipeline
    from pmtb.prediction.pipeline import ProbabilityPipeline


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

CYCLE_TOTAL = Counter(
    "pmtb_pipeline_cycles_total",
    "Total pipeline cycles completed",
)

CYCLE_DURATION = Histogram(
    "pmtb_pipeline_cycle_duration_seconds",
    "Full cycle duration in seconds",
)


# ---------------------------------------------------------------------------
# PipelineOrchestrator
# ---------------------------------------------------------------------------


class PipelineOrchestrator:
    """
    End-to-end pipeline orchestrator.

    Runs three concurrent async tasks via asyncio.gather:
      1. _full_cycle_loop — full pipeline every scan_interval_seconds
      2. _ws_reeval_loop  — decision re-evaluation on WS price events
      3. fill_tracker.run — fill tracking, stale cancellation, REST polling

    Constructor:
        scanner:          MarketScanner
        research:         ResearchPipeline
        predictor:        ProbabilityPipeline
        decision_pipeline: DecisionPipeline
        executor:         OrderExecutorProtocol
        fill_tracker:     FillTracker
        order_repo:       OrderRepository
        settings:         Settings
        session_factory:  SQLAlchemy async_sessionmaker
    """

    def __init__(
        self,
        scanner: "MarketScanner",
        research: "ResearchPipeline",
        predictor: "ProbabilityPipeline",
        decision_pipeline: "DecisionPipeline",
        executor,
        fill_tracker: "FillTracker",
        order_repo: "OrderRepository",
        settings: "Settings",
        session_factory,
    ) -> None:
        self._scanner = scanner
        self._research = research
        self._predictor = predictor
        self._decision = decision_pipeline
        self._executor = executor
        self._fill_tracker = fill_tracker
        self._repo = order_repo
        self._settings = settings
        self._session_factory = session_factory

        self._price_event_queue: asyncio.Queue = asyncio.Queue()
        self._last_predictions: list = []
        self._last_candidates: list = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, stop_event: asyncio.Event) -> None:
        """
        Run all three concurrent loops until stop_event is set.

        Loops:
            1. _full_cycle_loop  — full pipeline every scan_interval_seconds
            2. _ws_reeval_loop   — re-evaluate on WS price events
            3. fill_tracker.run  — fill lifecycle management
        """
        await asyncio.gather(
            self._full_cycle_loop(stop_event),
            self._ws_reeval_loop(stop_event),
            self._fill_tracker.run(stop_event),
        )

    # ------------------------------------------------------------------
    # Public: WS price event injection
    # ------------------------------------------------------------------

    def feed_price_event(self, event: dict) -> None:
        """
        Inject a WebSocket price-change event into the re-evaluation queue.

        Called by the WS price-change handler. Non-blocking.

        Args:
            event: Raw price event dict from Kalshi WebSocket.
        """
        self._price_event_queue.put_nowait(event)

    # ------------------------------------------------------------------
    # Loop 1: Full pipeline cycle
    # ------------------------------------------------------------------

    async def _full_cycle_loop(self, stop_event: asyncio.Event) -> None:
        """
        Infinite loop: run _run_full_cycle() then sleep scan_interval_seconds.

        Uses asyncio.wait_for on stop_event.wait() for the sleep so the loop
        exits cleanly the moment stop_event is set rather than waiting for the
        full interval.
        """
        while not stop_event.is_set():
            await self._run_full_cycle()

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._settings.scan_interval_seconds,
                )
                # stop_event fired — exit loop
                break
            except asyncio.TimeoutError:
                # Normal: interval elapsed, run the next cycle
                pass

    # ------------------------------------------------------------------
    # Loop 2: WebSocket re-evaluation
    # ------------------------------------------------------------------

    async def _ws_reeval_loop(self, stop_event: asyncio.Event) -> None:
        """
        Process price events from the WS queue and re-run decision pipeline.

        Only re-evaluates when there are cached predictions from the last
        full cycle. A 1-second timeout on queue.get() ensures the loop
        checks stop_event frequently.
        """
        while not stop_event.is_set():
            try:
                event = await asyncio.wait_for(
                    self._price_event_queue.get(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            if not self._last_predictions:
                logger.debug(
                    "WS re-eval: no cached predictions yet, skipping",
                    event=event,
                )
                continue

            log = logger.bind(ws_event=True)
            log.debug("WS price event received — re-evaluating decisions", event=event)

            try:
                decisions = await self._decision.evaluate(
                    self._last_predictions,
                    self._last_candidates,
                )
                for decision in decisions:
                    if decision.approved:
                        await self._execute_decision(decision, log)
            except Exception:
                logger.exception("WS re-eval loop error")

    # ------------------------------------------------------------------
    # Core: full pipeline cycle
    # ------------------------------------------------------------------

    async def _run_full_cycle(self) -> None:
        """
        Execute one complete pipeline cycle:
            Stage 1: Scanner  — fetch + filter market candidates
            Stage 2: Research — gather signals per candidate
            Stage 3: Prediction — model probabilities
            Stage 4: Decision — edge/size/risk gates
            Stage 5: Execution — place approved orders

        Each stage is wrapped with stage_timeout_seconds. If any stage fails,
        the cycle is aborted and the error is logged. The loop continues.
        """
        cycle_id = str(uuid.uuid4())
        log = logger.bind(cycle_id=cycle_id)
        log.info("Pipeline cycle starting")

        timeout = self._settings.stage_timeout_seconds

        # ----------------------------------------------------------------
        # Stage 1: Scanner
        # ----------------------------------------------------------------
        try:
            with CYCLE_DURATION.time():
                scan_result = await asyncio.wait_for(
                    self._scanner.run_cycle(),
                    timeout=timeout,
                )
        except Exception:
            log.exception("Stage 1 (Scanner) failed — aborting cycle")
            return

        candidates = scan_result.candidates
        if not candidates:
            log.info("Scanner returned no candidates — skipping cycle")
            return

        # ----------------------------------------------------------------
        # Stage 2: Research
        # ----------------------------------------------------------------
        try:
            signal_bundles = await asyncio.wait_for(
                self._research.run(candidates, cycle_id),
                timeout=timeout,
            )
        except Exception:
            log.exception("Stage 2 (Research) failed — aborting cycle")
            return

        # ----------------------------------------------------------------
        # Stage 3: Prediction
        # ----------------------------------------------------------------
        try:
            predictions = await asyncio.wait_for(
                self._predictor.predict_all(candidates, signal_bundles),
                timeout=timeout,
            )
        except Exception:
            log.exception("Stage 3 (Prediction) failed — aborting cycle")
            return

        # Cache for WS re-evaluation
        self._last_predictions = predictions
        self._last_candidates = candidates

        # ----------------------------------------------------------------
        # Stage 4: Decision (fast, pure computation — no timeout needed)
        # ----------------------------------------------------------------
        try:
            decisions = await self._decision.evaluate(predictions, candidates)
        except Exception:
            log.exception("Stage 4 (Decision) failed — aborting cycle")
            return

        # ----------------------------------------------------------------
        # Stage 5: Execution
        # ----------------------------------------------------------------
        approved = [d for d in decisions if d.approved]
        log.info(
            "Pipeline cycle decisions complete",
            total=len(decisions),
            approved=len(approved),
        )

        for decision in approved:
            try:
                await self._execute_decision(decision, log)
            except Exception:
                log.exception(
                    "Failed to execute approved decision",
                    ticker=decision.ticker,
                )

        CYCLE_TOTAL.inc()
        log.info("Pipeline cycle complete")

    # ------------------------------------------------------------------
    # Execution helper
    # ------------------------------------------------------------------

    async def _execute_decision(self, decision, log) -> None:
        """
        Execute a single approved trade decision.

        Steps:
            1. Check trading halt flag (TradingState key="trading_halted")
            2. Compute limit price: int(p_market * 100) + price_offset_cents
               Clamped to [1, 99] (Kalshi cent range)
            3. Place limit order via executor
            4. Persist to DB via order_repo

        Args:
            decision: Approved TradeDecision from DecisionPipeline.
            log:      Bound loguru logger.
        """
        # ----------------------------------------------------------------
        # Step 1: Check halt flag
        # ----------------------------------------------------------------
        async with self._session_factory() as session:
            halt_state = await session.get(TradingState, "trading_halted")

        if halt_state is not None and halt_state.value == "true":
            log.warning(
                "Trading halted — skipping order placement",
                ticker=decision.ticker,
            )
            return

        # ----------------------------------------------------------------
        # Step 2: Compute limit price
        # ----------------------------------------------------------------
        raw_price = int(decision.p_market * 100) + self._settings.price_offset_cents
        price = max(1, min(99, raw_price))  # clamp to Kalshi valid range

        # ----------------------------------------------------------------
        # Step 3: Place order
        # ----------------------------------------------------------------
        result = await self._executor.place_order(
            market_ticker=decision.ticker,
            side=decision.side,
            quantity=decision.quantity,
            price=price,
        )

        kalshi_order_id = result.get("order_id") if result else None

        log.info(
            "Order placed",
            ticker=decision.ticker,
            side=decision.side,
            quantity=decision.quantity,
            price=price,
            p_market=decision.p_market,
            edge=decision.edge,
            kelly_f=decision.kelly_f,
            p_model=decision.p_model,
            kalshi_order_id=kalshi_order_id,
        )

        # ----------------------------------------------------------------
        # Step 4: Persist to DB
        # ----------------------------------------------------------------
        await self._repo.create_order(
            market_ticker=decision.ticker,
            side=decision.side,
            quantity=decision.quantity,
            price=Decimal(str(price)),
            kalshi_order_id=kalshi_order_id,
            is_paper=(self._settings.trading_mode == "paper"),
        )
