"""
LLM-based probability estimation using Claude.

ClaudePredictor follows the same optional-dependency pattern as SentimentClassifier:
  - When no API key is provided, is_available=False and predict() raises RuntimeError.
  - When an API key is provided, AsyncAnthropic is lazily imported inside __init__.

Prometheus counter PREDICTION_LLM_CALLS tracks Claude API calls for cost monitoring.
"""
from __future__ import annotations

import json

from loguru import logger
from prometheus_client import Counter

from pmtb.research.models import SignalBundle
from pmtb.scanner.models import MarketCandidate

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

PREDICTION_LLM_CALLS = Counter(
    "prediction_llm_calls_total",
    "Claude prediction API calls",
)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a calibrated probabilistic forecaster for prediction markets.

Guidelines:
- Avoid anchoring probabilities to round numbers (0.5, 0.25, 0.75). Use precise values.
- Consider base rates carefully. Express genuine uncertainty.
- Do not anchor to the current market price. Your estimate should be based on fundamentals and signals only.
- Evaluate each signal source independently before combining them.
- Return only valid JSON with no markdown, no code fences, no commentary.

Required JSON format:
{"p_estimate": 0.0-1.0, "confidence": 0.0-1.0, "reasoning": "1-3 sentence explanation", "key_factors": ["factor1", "factor2"]}
"""


class ClaudePredictor:
    """
    Probabilistic predictor using Claude as an LLM forecaster.

    Parameters
    ----------
    anthropic_api_key : str | None
        Anthropic API key. If None, predictor is unavailable (is_available=False)
        and predict() raises RuntimeError.
    model : str
        Claude model to use. Defaults to Sonnet tier for balanced cost/quality.
    """

    SYSTEM_PROMPT: str = _SYSTEM_PROMPT

    def __init__(
        self,
        anthropic_api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._model = model
        self._client = None

        if anthropic_api_key is not None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=anthropic_api_key)

    @property
    def is_available(self) -> bool:
        """True if a Claude client has been configured."""
        return self._client is not None

    async def predict(self, market: MarketCandidate, bundle: SignalBundle) -> dict:
        """
        Estimate probability for a market using Claude.

        Parameters
        ----------
        market : MarketCandidate
            The prediction market to evaluate.
        bundle : SignalBundle
            Research signals bundle for the market.

        Returns
        -------
        dict
            Keys: p_estimate (float), confidence (float), reasoning (str),
            key_factors (list[str]).

        Raises
        ------
        RuntimeError
            If no API key was provided (is_available=False).
        ValueError
            If Claude returns invalid JSON.
        """
        if not self.is_available:
            raise RuntimeError(
                "ClaudePredictor is not available — no anthropic_api_key was provided."
            )

        # Build user prompt
        close_time_iso = market.close_time.isoformat()
        # Exclude ticker and cycle_id from bundle signals — those are identifiers, not signals
        bundle_data = bundle.model_dump(exclude={"ticker", "cycle_id"})
        user_prompt = (
            f"Market: {market.title}\n"
            f"Close time: {close_time_iso}\n"
            f"Current implied probability: {market.implied_probability:.4f}\n"
            f"Research signals: {json.dumps(bundle_data, default=str)}\n\n"
            "Provide your calibrated probability estimate."
        )

        log = logger.bind(ticker=market.ticker, model=self._model)
        log.info("Calling Claude for probability estimation")

        PREDICTION_LLM_CALLS.inc()

        message = await self._client.messages.create(  # type: ignore[union-attr]
            model=self._model,
            max_tokens=512,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = message.content[0].text

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON from Claude: {exc!r}\nRaw response: {raw_text!r}"
            ) from exc

        # Clamp p_estimate to [0, 1]
        raw_p = float(data["p_estimate"])
        if raw_p < 0.0 or raw_p > 1.0:
            logger.warning(
                "Claude returned p_estimate={raw_p} outside [0,1] — clamping",
                raw_p=raw_p,
                ticker=market.ticker,
            )
            data["p_estimate"] = max(0.0, min(1.0, raw_p))

        log.info(
            "Claude prediction complete",
            p_estimate=data["p_estimate"],
            confidence=data.get("confidence"),
        )

        return data
