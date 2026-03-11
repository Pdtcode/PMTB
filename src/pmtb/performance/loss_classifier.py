"""
LossClassifier — diagnoses losing trades using rule-based heuristics with
Claude fallback for ambiguous cases.

Six error types (in priority order applied by _apply_rules):
  1. edge_decay   — Model had edge but market price moved against position before resolution
  2. signal_error — Majority of research signals pointed wrong direction
  3. llm_error    — Claude was used (used_llm=True) and was the cause of the error
  4. sizing_error — Direction was correct but position was too large for the edge
  5. market_shock — All signals neutral/weak AND p_model near 0.5 — unpredictable shock
  6. unknown      — No rule matches; queued for Claude analysis

Claude fallback:
  - Only invoked when rules return unknown AND anthropic_api_key is set (lazy import pattern)
  - If Claude fails or is unavailable, returns (unknown, "Claude unavailable")

Prometheus instrumentation:
  - LOSS_CLASSIFICATIONS counter with labels: error_type, classified_by

Only losing trades (pnl < 0) are classified. Profitable trades raise ValueError.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from loguru import logger
from prometheus_client import Counter
from sqlalchemy import select

from pmtb.db.models import LossAnalysis, ModelOutput, Signal, Trade
from pmtb.performance.models import ErrorType, LossAnalysisResult

if TYPE_CHECKING:
    from pmtb.config import Settings

# ---------------------------------------------------------------------------
# Prometheus instrumentation
# ---------------------------------------------------------------------------

LOSS_CLASSIFICATIONS = Counter(
    "pmtb_loss_classifications_total",
    "Total number of loss classifications",
    ["error_type", "classified_by"],
)

# How close p_model must be to 0.5 to qualify as market_shock
_MARKET_SHOCK_THRESHOLD = Decimal("0.1")


class LossClassifier:
    """
    Classify losing trades into error categories using rule-based heuristics.

    Parameters
    ----------
    session_factory : callable
        Async context manager factory that yields a SQLAlchemy AsyncSession.
    settings : Settings
        Application settings. Used to read anthropic_api_key for Claude fallback.
    model : str
        Claude model name used for fallback classification.
    """

    def __init__(
        self,
        session_factory,
        settings: "Settings",
        model: str = "claude-3-5-haiku-latest",
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._model = model
        self._client = None

        if getattr(settings, "anthropic_api_key", None) is not None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _get_model_output(self, session, trade: Trade) -> ModelOutput | None:
        """Return most recent ModelOutput for the trade's market at or before trade time."""
        stmt = (
            select(ModelOutput)
            .where(
                ModelOutput.market_id == trade.market_id,
                ModelOutput.created_at <= trade.created_at,
            )
            .order_by(ModelOutput.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_signals(self, session, trade: Trade) -> list[Signal]:
        """Return all signals for the trade's market at or before trade time."""
        stmt = (
            select(Signal)
            .where(
                Signal.market_id == trade.market_id,
                Signal.created_at <= trade.created_at,
            )
        )
        result = await session.scalars(stmt)
        return result.all()

    # ------------------------------------------------------------------
    # Rule engine
    # ------------------------------------------------------------------

    def _apply_rules(
        self,
        trade: Trade,
        model_output: ModelOutput | None,
        signals: list[Signal],
    ) -> tuple[ErrorType, str]:
        """
        Apply rule-based heuristics in priority order.

        Returns
        -------
        (ErrorType, reasoning_string)
        """
        # ----------------------------------------------------------
        # 1. edge_decay
        # ----------------------------------------------------------
        # Heuristic: resolved_outcome disagrees with trade side AND
        # p_market at prediction time was closer to the resolved outcome
        # than p_model.
        #
        # "Closer to resolution" — for outcome=no (i.e. < 0.5 is correct):
        #   p_market < p_model  (p_market already leaning toward no)
        # For outcome=yes (i.e. > 0.5 is correct):
        #   p_market > p_model  (p_market already leaning toward yes)
        if model_output is not None:
            p_model = float(model_output.p_model)
            p_market = float(model_output.p_market) if model_output.p_market is not None else None

            outcome_is_yes = trade.resolved_outcome == "yes"
            trade_side_yes = trade.side == "yes"
            outcome_disagrees = outcome_is_yes != trade_side_yes

            if outcome_disagrees and p_market is not None:
                # Model predicted wrong direction
                if outcome_is_yes:
                    # Resolution = yes → higher probability is "closer to correct"
                    market_closer = p_market > p_model
                else:
                    # Resolution = no → lower probability is "closer to correct"
                    market_closer = p_market < p_model

                if market_closer:
                    return (
                        ErrorType.edge_decay,
                        f"Model p_model={p_model:.3f} had edge but market p_market={p_market:.3f} "
                        f"was already pricing toward resolved_outcome={trade.resolved_outcome}. "
                        "Edge decayed before resolution.",
                    )

        # ----------------------------------------------------------
        # 2. signal_error
        # ----------------------------------------------------------
        # Heuristic: majority signals aligned with trade side but outcome was opposite.
        if signals and model_output is not None:
            trade_side_yes = trade.side == "yes"
            outcome_disagrees = (trade.resolved_outcome == "yes") != trade_side_yes

            if outcome_disagrees:
                bullish_count = sum(1 for s in signals if s.sentiment == "bullish")
                bearish_count = sum(1 for s in signals if s.sentiment == "bearish")
                total_directional = bullish_count + bearish_count

                if total_directional > 0:
                    # "Majority aligned with trade side"
                    if trade_side_yes and bullish_count > bearish_count:
                        return (
                            ErrorType.signal_error,
                            f"Majority signals bullish ({bullish_count}/{total_directional}) "
                            f"aligned with YES trade but outcome was {trade.resolved_outcome}.",
                        )
                    elif not trade_side_yes and bearish_count > bullish_count:
                        return (
                            ErrorType.signal_error,
                            f"Majority signals bearish ({bearish_count}/{total_directional}) "
                            f"aligned with NO trade but outcome was {trade.resolved_outcome}.",
                        )

        # ----------------------------------------------------------
        # 3. llm_error
        # ----------------------------------------------------------
        # Heuristic: used_llm=True AND signal_weights show xgboost_base < 0.5
        # (XGBoost alone would have been on correct side) but Claude shifted
        # final p_model above 0.5 for the wrong side.
        if model_output is not None and model_output.used_llm:
            signal_weights = model_output.signal_weights or {}
            xgboost_base = signal_weights.get("xgboost_base")
            claude_adjustment = signal_weights.get("claude_adjustment")

            if xgboost_base is not None and claude_adjustment is not None:
                # XGBoost base was near 0.5 or opposite side, Claude shifted it
                xgboost_would_decide_yes = float(xgboost_base) > 0.5
                final_decides_yes = float(model_output.p_model) > 0.5
                outcome_is_yes = trade.resolved_outcome == "yes"

                # Claude changed the decision AND the original XGBoost would have been right
                if xgboost_would_decide_yes != final_decides_yes:
                    xgboost_correct = xgboost_would_decide_yes == outcome_is_yes
                    if xgboost_correct:
                        return (
                            ErrorType.llm_error,
                            f"LLM (Claude) adjusted decision: xgboost_base={xgboost_base:.3f} "
                            f"would predict {'YES' if xgboost_would_decide_yes else 'NO'} "
                            f"(correct), but Claude adjustment={claude_adjustment:.3f} shifted "
                            f"final p_model={float(model_output.p_model):.3f} to wrong side.",
                        )
            elif model_output.used_llm:
                # used_llm=True but no detailed weights — check if p_model was on wrong side
                # and outcome_disagrees with trade
                trade_side_yes = trade.side == "yes"
                outcome_is_yes = trade.resolved_outcome == "yes"
                if outcome_is_yes != trade_side_yes:
                    return (
                        ErrorType.llm_error,
                        "LLM (Claude) was used and trade direction was incorrect. "
                        "Signal weights not available to confirm XGBoost baseline.",
                    )

        # ----------------------------------------------------------
        # 4. sizing_error
        # ----------------------------------------------------------
        # Heuristic: resolved_outcome matches trade side (correct direction) but pnl < 0.
        if trade.resolved_outcome is not None:
            outcome_is_yes = trade.resolved_outcome == "yes"
            trade_side_yes = trade.side == "yes"
            direction_correct = outcome_is_yes == trade_side_yes

            if direction_correct:
                return (
                    ErrorType.sizing_error,
                    f"Trade direction was correct (side={trade.side}, "
                    f"resolved_outcome={trade.resolved_outcome}) but pnl={float(trade.pnl):.4f} < 0. "
                    "Position was likely too large relative to edge magnitude.",
                )

        # ----------------------------------------------------------
        # 5. market_shock
        # ----------------------------------------------------------
        # Heuristic: all signals neutral/weakly directional AND p_model within 0.1 of 0.5.
        if model_output is not None:
            p_model_val = model_output.p_model
            near_half = abs(p_model_val - Decimal("0.5")) <= _MARKET_SHOCK_THRESHOLD

            signals_neutral = all(
                s.sentiment == "neutral" for s in signals
            ) if signals else True

            if near_half and signals_neutral:
                return (
                    ErrorType.market_shock,
                    f"All signals neutral and p_model={float(p_model_val):.3f} near 0.5. "
                    "Rapid unexpected price movement inconsistent with all signals.",
                )

        # ----------------------------------------------------------
        # 6. unknown — no rule matched
        # ----------------------------------------------------------
        return (ErrorType.unknown, "")

    # ------------------------------------------------------------------
    # Claude fallback
    # ------------------------------------------------------------------

    async def _claude_classify(
        self,
        trade: Trade,
        model_output: ModelOutput | None,
        signals: list[Signal],
    ) -> tuple[ErrorType, str]:
        """
        Ask Claude to classify this losing trade.

        Returns (ErrorType, reasoning). Falls back to (unknown, "Claude unavailable")
        on any failure.
        """
        if self._client is None:
            return (ErrorType.unknown, "Claude unavailable — no API key configured")

        context = {
            "trade": {
                "id": str(trade.id),
                "side": trade.side,
                "quantity": trade.quantity,
                "price": str(trade.price),
                "pnl": str(trade.pnl),
                "resolved_outcome": trade.resolved_outcome,
            },
            "model_output": {
                "p_model": str(model_output.p_model) if model_output else None,
                "p_market": str(model_output.p_market) if model_output else None,
                "used_llm": model_output.used_llm if model_output else None,
                "signal_weights": model_output.signal_weights if model_output else None,
            },
            "signals": [
                {"sentiment": s.sentiment, "confidence": str(s.confidence)}
                for s in signals
            ],
        }

        prompt = (
            "You are analyzing a losing prediction market trade to classify the root cause.\n\n"
            f"Trade context:\n{json.dumps(context, indent=2)}\n\n"
            "Classify this loss into exactly one of these error types:\n"
            "- edge_decay: model had edge but market moved against position before resolution\n"
            "- signal_error: majority research signals pointed wrong direction\n"
            "- llm_error: LLM (Claude) was used and was the source of the directional error\n"
            "- sizing_error: direction was correct but position was too large for the edge\n"
            "- market_shock: unpredictable rapid price movement, signals and model were near neutral\n"
            "- unknown: cannot confidently classify\n\n"
            'Respond with ONLY a JSON object: {"error_type": "<type>", "reasoning": "<explanation>"}'
        )

        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            parsed = json.loads(raw)
            error_type = ErrorType(parsed["error_type"])
            reasoning = parsed.get("reasoning", "")
            return (error_type, reasoning)
        except Exception as exc:
            logger.warning(
                "Claude classification failed",
                trade_id=str(trade.id),
                error=str(exc),
            )
            return (ErrorType.unknown, f"Claude unavailable: {exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def classify_trade(self, trade_id: uuid.UUID) -> LossAnalysisResult:
        """
        Load the trade, classify the loss, and return LossAnalysisResult.

        Raises
        ------
        ValueError
            If the trade does not exist or has pnl >= 0.
        """
        async with self._session_factory() as session:
            trade = await session.get(Trade, trade_id)
            if trade is None:
                raise ValueError(f"Trade {trade_id} not found")

            if trade.pnl is None or trade.pnl >= 0:
                raise ValueError(
                    f"Trade {trade_id} is not a losing trade (pnl={trade.pnl})"
                )

            model_output = await self._get_model_output(session, trade)
            signals = await self._get_signals(session, trade)

        error_type, reasoning = self._apply_rules(trade, model_output, signals)
        classified_by = "rules"

        if error_type == ErrorType.unknown and self._client is not None:
            error_type, reasoning = await self._claude_classify(
                trade, model_output, signals
            )
            classified_by = "claude"

        result = LossAnalysisResult(
            trade_id=trade.id,
            error_type=error_type,
            reasoning=reasoning or None,
            classified_by=classified_by,
        )

        LOSS_CLASSIFICATIONS.labels(
            error_type=error_type.value,
            classified_by=classified_by,
        ).inc()

        logger.info(
            "Loss classified",
            trade_id=str(trade.id),
            error_type=error_type.value,
            classified_by=classified_by,
            reasoning=reasoning[:120] if reasoning else None,
        )

        return result

    async def classify_and_persist(self, trade_id: uuid.UUID) -> LossAnalysisResult:
        """
        Classify the losing trade and write a LossAnalysis row to the database.

        Returns the LossAnalysisResult.
        """
        result = await self.classify_trade(trade_id)

        async with self._session_factory() as session:
            row = LossAnalysis(
                trade_id=result.trade_id,
                error_type=result.error_type.value,
                reasoning=result.reasoning,
                classified_by=result.classified_by,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            await session.commit()

        logger.debug(
            "LossAnalysis persisted",
            trade_id=str(result.trade_id),
            error_type=result.error_type.value,
        )

        return result
