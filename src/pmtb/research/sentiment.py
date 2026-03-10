"""
Sentiment classifier for the research signal pipeline.

SentimentClassifier uses a two-tier approach:
  1. VADER (fast, free) — handles clear bullish/bearish text locally
  2. Claude (slow, cost) — handles ambiguous text above/below the escalation band

When no Anthropic API key is provided, the classifier runs in VADER-only mode:
  - Ambiguous text returns neutral with confidence=abs(compound) instead of calling Claude.

Prometheus counter tracks escalation rate for cost monitoring.
"""
from __future__ import annotations

import json

from loguru import logger
from prometheus_client import Counter
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from pmtb.research.models import SignalClassification

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

SENTIMENT_ESCALATIONS = Counter(
    "pmtb_sentiment_escalations_total",
    "Number of sentiment classifications escalated to Claude",
)


class SentimentClassifier:
    """
    Classify text sentiment using VADER with optional Claude escalation.

    Parameters
    ----------
    escalation_threshold : float
        VADER compound score |threshold|. Text with abs(compound) < threshold is
        considered ambiguous and escalated to Claude (if client available).
    anthropic_api_key : str | None
        Anthropic API key. If None, classifier runs VADER-only mode — Claude is
        never called.
    model : str
        Claude model to use for escalation.
    """

    def __init__(
        self,
        escalation_threshold: float = 0.3,
        anthropic_api_key: str | None = None,
        model: str = "claude-3-5-haiku-latest",
    ) -> None:
        self.escalation_threshold = escalation_threshold
        self._model = model
        self._analyzer = SentimentIntensityAnalyzer()
        self._client = None

        if anthropic_api_key is not None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=anthropic_api_key)

    async def classify(self, text: str) -> SignalClassification:
        """
        Classify the sentiment of the given text.

        Returns
        -------
        SignalClassification
            sentiment:  "bullish" | "bearish" | "neutral"
            confidence: 0.0–1.0
            reasoning:  None (VADER path) or explanation string (Claude path)
        """
        scores = self._analyzer.polarity_scores(text)
        compound: float = scores["compound"]

        log = logger.bind(compound=compound, text_snippet=text[:80])

        if compound >= self.escalation_threshold:
            log.debug("VADER bullish — skipping Claude", escalated=False)
            return SignalClassification(
                sentiment="bullish",
                confidence=float(compound),
            )

        if compound <= -self.escalation_threshold:
            log.debug("VADER bearish — skipping Claude", escalated=False)
            return SignalClassification(
                sentiment="bearish",
                confidence=float(abs(compound)),
            )

        # Ambiguous band — escalate if client available
        log.info("VADER ambiguous — escalating to Claude", escalated=True)
        SENTIMENT_ESCALATIONS.inc()

        if self._client is None:
            log.debug("No Claude client — returning neutral (VADER-only mode)")
            return SignalClassification(
                sentiment="neutral",
                confidence=float(abs(compound)),
                reasoning=None,
            )

        return await self._call_claude(text, compound)

    async def _call_claude(self, text: str, compound: float) -> SignalClassification:
        """Call Claude to classify ambiguous text."""
        prompt = (
            "Classify the sentiment of the following text as it relates to a financial "
            "prediction market. Respond with JSON only, no markdown.\n\n"
            f'Text: """{text}"""\n\n'
            'Required JSON format: {"sentiment": "bullish|bearish|neutral", '
            '"confidence": 0.0-1.0, "reasoning": "1-2 sentence explanation"}'
        )

        message = await self._client.messages.create(
            model=self._model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = message.content[0].text
        data = json.loads(raw_text)

        return SignalClassification(
            sentiment=data["sentiment"],
            confidence=float(data["confidence"]),
            reasoning=data.get("reasoning"),
        )
