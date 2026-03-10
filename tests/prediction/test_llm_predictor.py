"""
Tests for ClaudePredictor — LLM-based probability estimation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.prediction.llm_predictor import ClaudePredictor
from pmtb.research.models import SignalBundle
from pmtb.scanner.models import MarketCandidate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_market() -> MarketCandidate:
    return MarketCandidate(
        ticker="KXBTC-23DEC",
        title="Will BTC close above $40k on Dec 23?",
        category="crypto",
        close_time=datetime(2023, 12, 23, 16, 0, 0, tzinfo=timezone.utc),
        implied_probability=0.62,
    )


def make_bundle() -> SignalBundle:
    return SignalBundle(
        ticker="KXBTC-23DEC",
        cycle_id="cycle-001",
    )


def make_response_content(payload: dict) -> MagicMock:
    content_block = MagicMock()
    content_block.text = json.dumps(payload)
    message = MagicMock()
    message.content = [content_block]
    return message


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


def test_is_available_false_when_no_key():
    predictor = ClaudePredictor(anthropic_api_key=None)
    assert predictor.is_available is False


def test_is_available_true_when_key_provided():
    with patch("anthropic.AsyncAnthropic"):
        predictor = ClaudePredictor(anthropic_api_key="sk-test-key")
        assert predictor.is_available is True


# ---------------------------------------------------------------------------
# predict — valid response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predict_returns_structured_dict():
    payload = {
        "p_estimate": 0.67,
        "confidence": 0.8,
        "reasoning": "Strong upward momentum in BTC.",
        "key_factors": ["momentum", "volume"],
    }
    mock_client = MagicMock()
    mock_client.messages = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=make_response_content(payload))

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        predictor = ClaudePredictor(anthropic_api_key="sk-test")
        result = await predictor.predict(make_market(), make_bundle())

    assert result["p_estimate"] == pytest.approx(0.67)
    assert result["confidence"] == pytest.approx(0.8)
    assert result["reasoning"] == "Strong upward momentum in BTC."
    assert result["key_factors"] == ["momentum", "volume"]


@pytest.mark.asyncio
async def test_predict_p_estimate_in_range():
    payload = {
        "p_estimate": 0.55,
        "confidence": 0.7,
        "reasoning": "Balanced signals.",
        "key_factors": ["trend"],
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=make_response_content(payload))

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        predictor = ClaudePredictor(anthropic_api_key="sk-test")
        result = await predictor.predict(make_market(), make_bundle())

    assert 0.0 <= result["p_estimate"] <= 1.0


# ---------------------------------------------------------------------------
# predict — p_estimate clamping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predict_clamps_p_estimate_above_1():
    payload = {
        "p_estimate": 1.3,  # out of range
        "confidence": 0.9,
        "reasoning": "Very bullish.",
        "key_factors": ["sentiment"],
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=make_response_content(payload))

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        predictor = ClaudePredictor(anthropic_api_key="sk-test")
        result = await predictor.predict(make_market(), make_bundle())

    assert result["p_estimate"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_predict_clamps_p_estimate_below_0():
    payload = {
        "p_estimate": -0.2,  # out of range
        "confidence": 0.5,
        "reasoning": "Very bearish.",
        "key_factors": ["news"],
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=make_response_content(payload))

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        predictor = ClaudePredictor(anthropic_api_key="sk-test")
        result = await predictor.predict(make_market(), make_bundle())

    assert result["p_estimate"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# predict — invalid JSON raises error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predict_raises_on_invalid_json():
    content_block = MagicMock()
    content_block.text = "not valid json at all"
    message = MagicMock()
    message.content = [content_block]

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=message)

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        predictor = ClaudePredictor(anthropic_api_key="sk-test")
        with pytest.raises(ValueError, match="[Ii]nvalid JSON"):
            await predictor.predict(make_market(), make_bundle())


# ---------------------------------------------------------------------------
# predict — unavailable when no key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predict_raises_when_not_available():
    predictor = ClaudePredictor(anthropic_api_key=None)
    with pytest.raises(RuntimeError, match="[Nn]ot available"):
        await predictor.predict(make_market(), make_bundle())


# ---------------------------------------------------------------------------
# System prompt content
# ---------------------------------------------------------------------------


def test_system_prompt_contains_calibration_instructions():
    predictor = ClaudePredictor(anthropic_api_key=None)
    prompt = predictor.SYSTEM_PROMPT
    assert "calibrated" in prompt.lower() or "probabilistic" in prompt.lower()
    assert "anchor" in prompt.lower()
    assert "Do not anchor to the current market price" in prompt
    assert "base rate" in prompt.lower()


def test_system_prompt_returns_json():
    predictor = ClaudePredictor(anthropic_api_key=None)
    assert "JSON" in predictor.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Model tier configurability
# ---------------------------------------------------------------------------


def test_default_model_is_sonnet():
    predictor = ClaudePredictor(anthropic_api_key=None)
    assert "sonnet" in predictor._model.lower()


def test_custom_model_is_stored():
    predictor = ClaudePredictor(anthropic_api_key=None, model="claude-3-opus-20240229")
    assert predictor._model == "claude-3-opus-20240229"


# ---------------------------------------------------------------------------
# Prometheus counter accessible
# ---------------------------------------------------------------------------


def test_prometheus_counter_exists():
    from pmtb.prediction.llm_predictor import PREDICTION_LLM_CALLS

    assert PREDICTION_LLM_CALLS is not None
