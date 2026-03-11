"""
Tests for LossClassifier — rule-based heuristics and Claude fallback.

Coverage:
  - edge_decay: p_model had edge but market moved against before resolution
  - signal_error: majority signals were wrong direction
  - llm_error: used_llm=True and Claude was the error source
  - sizing_error: direction correct but position too large
  - market_shock: neutral signals and p_model near 0.5
  - unknown: no rules match
  - Claude fallback: invoked only when rules return unknown
  - Claude not called when rules match
  - Only classifies losing trades (pnl < 0)
  - Persist writes LossAnalysis DB row
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.performance.loss_classifier import LossClassifier
from pmtb.performance.models import ErrorType, LossAnalysisResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade(
    *,
    market_id: uuid.UUID | None = None,
    side: str = "yes",
    quantity: int = 10,
    price: Decimal = Decimal("0.55"),
    pnl: Decimal = Decimal("-5.00"),
    resolved_outcome: str | None = "no",
    **kwargs,
) -> MagicMock:
    """Build a mock Trade with sensible defaults for a losing trade."""
    trade = MagicMock()
    trade.id = uuid.uuid4()
    trade.market_id = market_id or uuid.uuid4()
    trade.side = side
    trade.quantity = quantity
    trade.price = price
    trade.pnl = pnl
    trade.resolved_outcome = resolved_outcome
    for k, v in kwargs.items():
        setattr(trade, k, v)
    return trade


def _make_model_output(
    *,
    p_model: Decimal = Decimal("0.60"),
    p_market: Decimal | None = Decimal("0.40"),
    signal_weights: dict | None = None,
    used_llm: bool = False,
) -> MagicMock:
    mo = MagicMock()
    mo.p_model = p_model
    mo.p_market = p_market
    mo.signal_weights = signal_weights or {}
    mo.used_llm = used_llm
    return mo


def _make_signal(sentiment: str, confidence: Decimal = Decimal("0.70")) -> MagicMock:
    sig = MagicMock()
    sig.sentiment = sentiment
    sig.confidence = confidence
    return sig


def _make_async_cm_factory(session_mock):
    """Return a callable that produces an async context manager yielding session_mock."""
    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=session_mock)
    async_cm.__aexit__ = AsyncMock(return_value=False)

    def factory():
        return async_cm

    return factory


def _make_settings(anthropic_api_key: str | None = None) -> MagicMock:
    settings = MagicMock()
    settings.anthropic_api_key = anthropic_api_key
    return settings


# ---------------------------------------------------------------------------
# edge_decay
# ---------------------------------------------------------------------------


class TestEdgeDecayClassification:
    """
    edge_decay: resolved_outcome disagrees with trade side AND
    p_market at prediction time was closer to the resolved outcome than p_model.

    Trade side=yes, resolved_outcome=no.
    p_model=0.70 (confident YES), p_market=0.35 (closer to NO).
    => p_market was already pointing toward resolution (0.35 < 0.5),
       while p_model was overconfident.
    """

    @pytest.mark.asyncio
    async def test_edge_decay_classification(self):
        trade = _make_trade(
            side="yes",
            resolved_outcome="no",
            pnl=Decimal("-10.00"),
        )
        model_output = _make_model_output(
            p_model=Decimal("0.70"),
            p_market=Decimal("0.35"),
        )
        # Majority signals bullish (aligned with yes side) — to avoid signal_error
        signals = [_make_signal("bullish"), _make_signal("bullish"), _make_signal("bullish")]

        session_mock = MagicMock()
        session_mock.execute = AsyncMock()
        session_mock.execute.return_value.scalar_one_or_none = MagicMock(
            return_value=model_output
        )
        session_mock.scalars = AsyncMock()
        session_mock.scalars.return_value.all = MagicMock(return_value=signals)
        session_mock.add = MagicMock()
        session_mock.commit = AsyncMock()

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(session_mock),
            settings=_make_settings(),
        )

        # Inject model_output and signals directly for rule testing
        error_type, reasoning = classifier._apply_rules(trade, model_output, signals)

        assert error_type == ErrorType.edge_decay
        assert reasoning  # non-empty string


# ---------------------------------------------------------------------------
# signal_error
# ---------------------------------------------------------------------------


class TestSignalErrorClassification:
    """
    signal_error: majority signals aligned with trade side, outcome was opposite.

    Trade side=yes, resolved_outcome=no.
    Majority signals bullish (aligned with yes).
    => model was pointing wrong direction; signals were the source of error.
    """

    @pytest.mark.asyncio
    async def test_signal_error_classification(self):
        trade = _make_trade(
            side="yes",
            resolved_outcome="no",
            pnl=Decimal("-8.00"),
        )
        # p_model > 0.5 but p_market also > 0.5 → edge_decay condition NOT met
        # (p_market closer to yes, not to resolution=no)
        model_output = _make_model_output(
            p_model=Decimal("0.60"),
            p_market=Decimal("0.62"),  # both wrong side → no edge_decay
        )
        # Majority bullish → signal_error
        signals = [
            _make_signal("bullish"),
            _make_signal("bullish"),
            _make_signal("bearish"),
        ]

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(MagicMock()),
            settings=_make_settings(),
        )

        error_type, reasoning = classifier._apply_rules(trade, model_output, signals)

        assert error_type == ErrorType.signal_error
        assert reasoning


# ---------------------------------------------------------------------------
# llm_error
# ---------------------------------------------------------------------------


class TestLlmErrorClassification:
    """
    llm_error: used_llm=True AND removing Claude's contribution would have
    flipped the decision. Heuristic: used_llm=True and p_model > 0.5 (YES side)
    but p_market < 0.5 (market disagreed), and outcome was NO.
    Signal weights show XGBoost base was already on the wrong side.
    """

    @pytest.mark.asyncio
    async def test_llm_error_classification(self):
        trade = _make_trade(
            side="yes",
            resolved_outcome="no",
            pnl=Decimal("-6.00"),
        )
        # p_market also says yes-ish (0.60) — no edge_decay
        # used_llm=True and signal_weights indicate Claude shifted the final p_model
        model_output = _make_model_output(
            p_model=Decimal("0.60"),
            p_market=Decimal("0.60"),
            used_llm=True,
            signal_weights={"xgboost_base": 0.45, "claude_adjustment": 0.15},
        )
        # Majority signals bearish → would have hit signal_error first.
        # Use mixed signals to avoid signal_error.
        signals = [
            _make_signal("bullish"),
            _make_signal("bearish"),
            _make_signal("neutral"),
        ]

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(MagicMock()),
            settings=_make_settings(),
        )

        error_type, reasoning = classifier._apply_rules(trade, model_output, signals)

        assert error_type == ErrorType.llm_error
        assert reasoning


# ---------------------------------------------------------------------------
# sizing_error
# ---------------------------------------------------------------------------


class TestSizingErrorClassification:
    """
    sizing_error: direction was correct (resolved_outcome matches trade side)
    but pnl < 0 (overleveraged on narrow edge).
    """

    @pytest.mark.asyncio
    async def test_sizing_error_classification(self):
        # Correct direction — yes side won — but still lost money (e.g. bought at 0.95)
        trade = _make_trade(
            side="yes",
            resolved_outcome="yes",
            pnl=Decimal("-2.00"),
        )
        model_output = _make_model_output(
            p_model=Decimal("0.55"),
            p_market=Decimal("0.55"),
            used_llm=False,
        )
        signals = [_make_signal("neutral"), _make_signal("neutral")]

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(MagicMock()),
            settings=_make_settings(),
        )

        error_type, reasoning = classifier._apply_rules(trade, model_output, signals)

        assert error_type == ErrorType.sizing_error
        assert reasoning


# ---------------------------------------------------------------------------
# market_shock
# ---------------------------------------------------------------------------


class TestMarketShockClassification:
    """
    market_shock: all signals neutral or weak AND p_model near 0.5 (within 0.1).
    """

    @pytest.mark.asyncio
    async def test_market_shock_classification(self):
        trade = _make_trade(
            side="yes",
            resolved_outcome="no",
            pnl=Decimal("-3.00"),
        )
        model_output = _make_model_output(
            p_model=Decimal("0.52"),
            p_market=Decimal("0.50"),
            used_llm=False,
        )
        signals = [
            _make_signal("neutral", Decimal("0.30")),
            _make_signal("neutral", Decimal("0.25")),
        ]

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(MagicMock()),
            settings=_make_settings(),
        )

        error_type, reasoning = classifier._apply_rules(trade, model_output, signals)

        assert error_type == ErrorType.market_shock
        assert reasoning


# ---------------------------------------------------------------------------
# unknown
# ---------------------------------------------------------------------------


class TestUnknownFallthrough:
    """
    unknown: no rule matches. Ambiguous scenario.
    """

    @pytest.mark.asyncio
    async def test_unknown_falls_through(self):
        trade = _make_trade(
            side="yes",
            resolved_outcome="no",
            pnl=Decimal("-4.00"),
        )
        # p_market same side as p_model (no edge_decay)
        # exactly 50/50 signals (no majority → no signal_error)
        # used_llm=False (no llm_error)
        # resolved_outcome != trade.side (no sizing_error)
        # p_model far from 0.5 (no market_shock)
        model_output = _make_model_output(
            p_model=Decimal("0.65"),
            p_market=Decimal("0.65"),
            used_llm=False,
        )
        signals = [_make_signal("bullish"), _make_signal("bearish")]

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(MagicMock()),
            settings=_make_settings(),
        )

        error_type, reasoning = classifier._apply_rules(trade, model_output, signals)

        assert error_type == ErrorType.unknown


# ---------------------------------------------------------------------------
# Claude fallback
# ---------------------------------------------------------------------------


class TestClaudeFallback:
    """
    Claude is invoked when rule-based classification returns unknown AND
    anthropic_api_key is set.
    """

    @pytest.mark.asyncio
    async def test_claude_fallback_on_unknown(self):
        trade = _make_trade(
            side="yes",
            resolved_outcome="no",
            pnl=Decimal("-4.00"),
        )
        model_output = _make_model_output(
            p_model=Decimal("0.65"),
            p_market=Decimal("0.65"),
            used_llm=False,
        )
        signals = [_make_signal("bullish"), _make_signal("bearish")]

        session_mock = MagicMock()
        # Simulate DB returning the trade
        session_mock.get = AsyncMock(return_value=trade)
        # _get_model_output returns model_output
        session_mock.execute = AsyncMock()
        session_mock.execute.return_value.scalar_one_or_none = MagicMock(
            return_value=model_output
        )
        session_mock.scalars = AsyncMock()
        session_mock.scalars.return_value.all = MagicMock(return_value=signals)
        session_mock.add = MagicMock()
        session_mock.commit = AsyncMock()

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(session_mock),
            settings=_make_settings(anthropic_api_key="test-key"),
        )

        # Patch the Claude client
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text='{"error_type": "edge_decay", "reasoning": "Market moved fast"}')]
        mock_client.messages.create = AsyncMock(return_value=mock_message)
        classifier._client = mock_client

        result = await classifier.classify_trade(trade.id)

        # Claude was called (rules returned unknown, Claude overrode it)
        mock_client.messages.create.assert_called_once()
        assert result.classified_by == "claude"
        assert result.error_type != ErrorType.unknown

    @pytest.mark.asyncio
    async def test_claude_not_called_when_rules_match(self):
        """Claude must NOT be invoked when rule-based classifier resolves the type."""
        # sizing_error scenario: correct direction but pnl < 0
        trade = _make_trade(
            side="yes",
            resolved_outcome="yes",
            pnl=Decimal("-2.00"),
        )
        model_output = _make_model_output(
            p_model=Decimal("0.55"),
            p_market=Decimal("0.55"),
            used_llm=False,
        )
        signals = [_make_signal("neutral")]

        session_mock = MagicMock()
        session_mock.get = AsyncMock(return_value=trade)
        session_mock.execute = AsyncMock()
        session_mock.execute.return_value.scalar_one_or_none = MagicMock(
            return_value=model_output
        )
        session_mock.scalars = AsyncMock()
        session_mock.scalars.return_value.all = MagicMock(return_value=signals)
        session_mock.add = MagicMock()
        session_mock.commit = AsyncMock()

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(session_mock),
            settings=_make_settings(anthropic_api_key="test-key"),
        )

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock()
        classifier._client = mock_client

        result = await classifier.classify_trade(trade.id)

        mock_client.messages.create.assert_not_called()
        assert result.classified_by == "rules"
        assert result.error_type == ErrorType.sizing_error


# ---------------------------------------------------------------------------
# Guard: only classifies losing trades
# ---------------------------------------------------------------------------


class TestOnlyClassifiesLosingTrades:
    @pytest.mark.asyncio
    async def test_only_classifies_losing_trades(self):
        """Profitable trade (pnl >= 0) should raise ValueError."""
        trade = _make_trade(pnl=Decimal("5.00"))

        session_mock = MagicMock()
        session_mock.get = AsyncMock(return_value=trade)

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(session_mock),
            settings=_make_settings(),
        )

        with pytest.raises(ValueError, match="not a losing trade"):
            await classifier.classify_trade(trade.id)


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------


class TestPersistWritesLossAnalysisRow:
    @pytest.mark.asyncio
    async def test_persist_writes_loss_analysis_row(self):
        """classify_and_persist must write a LossAnalysis row to the DB."""
        trade = _make_trade(
            side="yes",
            resolved_outcome="yes",
            pnl=Decimal("-2.00"),
        )
        model_output = _make_model_output(
            p_model=Decimal("0.55"),
            p_market=Decimal("0.55"),
        )
        signals = [_make_signal("neutral")]

        session_mock = MagicMock()
        session_mock.get = AsyncMock(return_value=trade)
        session_mock.execute = AsyncMock()
        session_mock.execute.return_value.scalar_one_or_none = MagicMock(
            return_value=model_output
        )
        session_mock.scalars = AsyncMock()
        session_mock.scalars.return_value.all = MagicMock(return_value=signals)
        session_mock.add = MagicMock()
        session_mock.commit = AsyncMock()

        classifier = LossClassifier(
            session_factory=_make_async_cm_factory(session_mock),
            settings=_make_settings(),
        )

        result = await classifier.classify_and_persist(trade.id)

        # DB row was added and committed
        session_mock.add.assert_called_once()
        session_mock.commit.assert_called_once()

        # Return value is a LossAnalysisResult
        assert isinstance(result, LossAnalysisResult)
        assert result.trade_id == trade.id
        assert result.error_type == ErrorType.sizing_error
        assert result.classified_by == "rules"
