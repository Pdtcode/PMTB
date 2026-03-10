"""
DecisionPipeline — orchestrates Edge -> Size -> Risk gates in sequence.

This is the single entry point that Phase 6 calls to evaluate trade candidates
for a given prediction cycle. It wires all three decision components into a
clean sequential pipeline with short-circuit rejection and observability.

Pipeline flow for each (prediction, candidate) pair:
  1. Shadow filter — exclude is_shadow predictions immediately
  2. Hedge check   — detect edge reversal on open positions (returns separate decision)
  3. Edge gate     — EdgeDetector.evaluate (pure math, synchronous)
  4. Size gate     — KellySizer.size (pure math, synchronous)
  5. Risk gate     — RiskManager.check (DB-consulting, async)

All gates short-circuit on rejection: once a gate rejects, later gates are skipped
and the rejection reason is preserved in the TradeDecision.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger
from prometheus_client import Counter, Histogram
from sqlalchemy.ext.asyncio import async_sessionmaker

from pmtb.decision.edge import EdgeDetector
from pmtb.decision.models import RejectionReason, TradeDecision
from pmtb.decision.risk import RiskManager
from pmtb.decision.sizer import KellySizer
from pmtb.decision.tracker import PositionTracker

if TYPE_CHECKING:
    from pmtb.config import Settings
    from pmtb.prediction.models import PredictionResult
    from pmtb.scanner.models import MarketCandidate


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

DECISION_REJECTIONS = Counter(
    "pmtb_decision_rejections_total",
    "Total number of trades rejected by DecisionPipeline, by reason",
    ["reason"],
)

DECISION_APPROVALS = Counter(
    "pmtb_decision_approvals_total",
    "Total number of trades approved by DecisionPipeline",
)

DECISION_LATENCY = Histogram(
    "pmtb_decision_latency_seconds",
    "Time spent evaluating a full batch of predictions through the pipeline",
)


# ---------------------------------------------------------------------------
# DecisionPipeline
# ---------------------------------------------------------------------------

class DecisionPipeline:
    """
    Orchestrates the complete Edge -> Size -> Risk decision pipeline.

    Dependencies are injected at construction, making each individually testable.
    Use `from_settings` factory for production construction from a Settings object.

    Usage::

        pipeline = DecisionPipeline.from_settings(settings, session_factory, portfolio_value)
        decisions = await pipeline.evaluate(predictions, candidates)
    """

    def __init__(
        self,
        edge_detector: EdgeDetector,
        sizer: KellySizer,
        risk_manager: RiskManager,
        tracker: PositionTracker,
    ) -> None:
        """
        Args:
            edge_detector: EdgeDetector instance (pure math gate).
            sizer:         KellySizer instance (Kelly criterion gate).
            risk_manager:  RiskManager instance (portfolio risk gate).
            tracker:       PositionTracker instance (shared in-memory position cache).
        """
        self._edge_detector = edge_detector
        self._sizer = sizer
        self._risk_manager = risk_manager
        self._tracker = tracker

    async def evaluate(
        self,
        predictions: list["PredictionResult"],
        candidates: list["MarketCandidate"],
    ) -> list[TradeDecision]:
        """
        Evaluate a batch of predictions against candidates through all pipeline gates.

        Each prediction is matched to its corresponding candidate by ticker.
        Missing candidates produce a log warning and are skipped.

        Args:
            predictions: Model predictions from Phase 4 probability model.
            candidates:  Market candidates from Phase 2 scanner.

        Returns:
            List of TradeDecision, one per prediction (approved or rejected).
            Hedge decisions are appended if triggered by open positions.
        """
        start = time.perf_counter()

        # Build O(1) ticker -> candidate lookup
        candidate_map: dict[str, "MarketCandidate"] = {
            c.ticker: c for c in candidates
        }

        results: list[TradeDecision] = []

        for prediction in predictions:
            ticker = prediction.ticker
            log = logger.bind(ticker=ticker, cycle_id=prediction.cycle_id)

            # ----------------------------------------------------------------
            # Step 1: Shadow filter
            # ----------------------------------------------------------------
            if prediction.is_shadow:
                log.debug("Shadow prediction excluded from pipeline")
                results.append(
                    TradeDecision(
                        ticker=ticker,
                        cycle_id=prediction.cycle_id,
                        approved=False,
                        rejection_reason=RejectionReason.SHADOW,
                        p_model=prediction.p_model,
                    )
                )
                DECISION_REJECTIONS.labels(reason=RejectionReason.SHADOW.value).inc()
                continue

            # ----------------------------------------------------------------
            # Step 2: Match to candidate
            # ----------------------------------------------------------------
            candidate = candidate_map.get(ticker)
            if candidate is None:
                log.warning("No matching candidate found for prediction — skipping")
                continue

            # ----------------------------------------------------------------
            # Step 3: Hedge check (independent of main pipeline)
            # ----------------------------------------------------------------
            hedge = await self._risk_manager.check_hedge(prediction, candidate)
            if hedge is not None:
                log.info("Hedge opportunity detected", side=hedge.side, edge=hedge.edge)
                results.append(hedge)

            # ----------------------------------------------------------------
            # Step 4: Edge gate (synchronous)
            # ----------------------------------------------------------------
            decision = self._edge_detector.evaluate(prediction, candidate)
            if not decision.approved:
                log.debug(
                    "Rejected at edge gate",
                    reason=decision.rejection_reason,
                    edge=decision.edge,
                )
                DECISION_REJECTIONS.labels(
                    reason=decision.rejection_reason.value  # type: ignore[union-attr]
                ).inc()
                results.append(decision)
                continue

            # ----------------------------------------------------------------
            # Step 5: Size gate (synchronous)
            # ----------------------------------------------------------------
            decision = self._sizer.size(decision)
            if not decision.approved:
                log.debug(
                    "Rejected at sizer gate",
                    reason=decision.rejection_reason,
                    kelly_f=decision.kelly_f,
                )
                DECISION_REJECTIONS.labels(
                    reason=decision.rejection_reason.value  # type: ignore[union-attr]
                ).inc()
                results.append(decision)
                continue

            # ----------------------------------------------------------------
            # Step 6: Risk gate (async)
            # ----------------------------------------------------------------
            decision = await self._risk_manager.check(decision)
            if not decision.approved:
                log.debug(
                    "Rejected at risk gate",
                    reason=decision.rejection_reason,
                )
                DECISION_REJECTIONS.labels(
                    reason=decision.rejection_reason.value  # type: ignore[union-attr]
                ).inc()
                results.append(decision)
                continue

            # ----------------------------------------------------------------
            # Step 7: Approved
            # ----------------------------------------------------------------
            log.info(
                "Trade approved",
                quantity=decision.quantity,
                edge=decision.edge,
                kelly_f=decision.kelly_f,
            )
            DECISION_APPROVALS.inc()
            results.append(decision)

        elapsed = time.perf_counter() - start
        DECISION_LATENCY.observe(elapsed)

        logger.info(
            "Pipeline evaluation complete",
            total=len(results),
            approved=sum(1 for r in results if r.approved),
            rejected=sum(1 for r in results if not r.approved),
            latency_s=f"{elapsed:.3f}",
        )

        return results

    @classmethod
    def from_settings(
        cls,
        settings: "Settings",
        session_factory: async_sessionmaker,
        portfolio_value: float,
    ) -> "DecisionPipeline":
        """
        Factory: construct all pipeline sub-components from Settings.

        Args:
            settings:        Application settings (thresholds, limits).
            session_factory: Async SQLAlchemy session factory.
            portfolio_value: Current total portfolio value in dollars.

        Returns:
            Fully configured DecisionPipeline ready for evaluate() calls.
        """
        edge_detector = EdgeDetector(settings.edge_threshold)
        sizer = KellySizer(
            kelly_alpha=settings.kelly_alpha,
            max_single_bet=settings.max_single_bet,
            portfolio_value=portfolio_value,
        )
        tracker = PositionTracker(session_factory)
        risk_manager = RiskManager(
            tracker=tracker,
            session_factory=session_factory,
            max_exposure=settings.max_exposure,
            max_single_bet=settings.max_single_bet,
            var_limit=settings.var_limit,
            max_drawdown=settings.max_drawdown,
            hedge_shift_threshold=settings.hedge_shift_threshold,
            portfolio_value=portfolio_value,
        )
        return cls(
            edge_detector=edge_detector,
            sizer=sizer,
            risk_manager=risk_manager,
            tracker=tracker,
        )
