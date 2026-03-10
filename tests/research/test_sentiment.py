"""
Tests for SentimentClassifier (VADER + Claude hybrid).

TDD RED phase — tests written before implementation.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.research.models import SignalClassification


@pytest.fixture
def classifier_no_key():
    """SentimentClassifier with no Anthropic API key (VADER-only mode)."""
    from pmtb.research.sentiment import SentimentClassifier

    return SentimentClassifier(escalation_threshold=0.3, anthropic_api_key=None)


@pytest.fixture
def classifier_with_key():
    """SentimentClassifier with a (fake) Anthropic API key."""
    from pmtb.research.sentiment import SentimentClassifier

    return SentimentClassifier(
        escalation_threshold=0.3, anthropic_api_key="test-key-123"
    )


# ---------------------------------------------------------------------------
# VADER routing — no Claude call expected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_bullish_no_claude_call(classifier_with_key):
    """Very positive text should return bullish via VADER without calling Claude."""
    with patch.object(
        classifier_with_key, "_client", new_callable=lambda: type(None)
    ):
        # Patch the client to detect if it's ever used
        mock_client = AsyncMock()
        classifier_with_key._client = mock_client

        result = await classifier_with_key.classify(
            "This is amazing! Huge win! Outstanding performance!"
        )

    assert result.sentiment == "bullish"
    assert 0.0 <= result.confidence <= 1.0
    # Claude should NOT have been called
    mock_client.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_clear_bearish_no_claude_call(classifier_with_key):
    """Very negative text should return bearish via VADER without calling Claude."""
    mock_client = AsyncMock()
    classifier_with_key._client = mock_client

    result = await classifier_with_key.classify(
        "Terrible loss. Everything crashed. Complete disaster and failure."
    )

    assert result.sentiment == "bearish"
    assert 0.0 <= result.confidence <= 1.0
    mock_client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# Claude escalation — ambiguous text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_escalates_to_claude(classifier_with_key):
    """Text with abs(compound) < threshold should call Claude for classification."""
    # Build a mock Claude response
    mock_message = MagicMock()
    mock_message.content = [
        MagicMock(
            text='{"sentiment": "neutral", "confidence": 0.55, "reasoning": "Committee meeting is factual, no strong sentiment."}'
        )
    ]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)
    classifier_with_key._client = mock_client

    result = await classifier_with_key.classify("The committee met today.")

    assert result.sentiment == "neutral"
    assert 0.0 <= result.confidence <= 1.0
    assert result.reasoning is not None
    mock_client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_ambiguous_no_api_key_returns_neutral(classifier_no_key):
    """Ambiguous text with no API key returns neutral, no error raised."""
    result = await classifier_no_key.classify("The committee met today.")

    assert result.sentiment == "neutral"
    assert 0.0 <= result.confidence <= 1.0
    assert result.reasoning is None


# ---------------------------------------------------------------------------
# Confidence bounds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confidence_always_valid_bullish(classifier_no_key):
    """Confidence for VADER bullish result is always in [0, 1]."""
    result = await classifier_no_key.classify(
        "This is amazing! Huge win! Outstanding performance!"
    )
    assert 0.0 <= result.confidence <= 1.0


@pytest.mark.asyncio
async def test_confidence_always_valid_bearish(classifier_no_key):
    """Confidence for VADER bearish result is always in [0, 1]."""
    result = await classifier_no_key.classify(
        "Terrible loss. Everything crashed. Complete disaster and failure."
    )
    assert 0.0 <= result.confidence <= 1.0


@pytest.mark.asyncio
async def test_claude_escalation_populates_reasoning(classifier_with_key):
    """Claude escalation should populate the reasoning field."""
    mock_message = MagicMock()
    mock_message.content = [
        MagicMock(
            text='{"sentiment": "bullish", "confidence": 0.7, "reasoning": "Positive outlook despite neutral VADER score."}'
        )
    ]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)
    classifier_with_key._client = mock_client

    result = await classifier_with_key.classify("The committee met today.")

    assert isinstance(result, SignalClassification)
    assert result.reasoning is not None
    assert len(result.reasoning) > 0
