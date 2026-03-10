"""
TrendsAgent — derives momentum signal from Google Trends interest-over-time.

Strategy:
  1. Build a pytrends payload for the query keyword
  2. Call interest_over_time() via asyncio.to_thread() (pytrends is synchronous)
  3. Compare last 7 days average vs. previous 7 days to derive momentum
     - Rising  → bullish signal
     - Falling → bearish signal
     - Flat    → neutral signal
  4. Also fetch related_queries via asyncio.to_thread()
  5. Return SourceResult with single SignalClassification (momentum-based)

Uses tenacity retry for 429 rate-limit responses from Google.
Adds 1-second sleep between pytrends requests.
"""
from __future__ import annotations

import asyncio

from loguru import logger
from pytrends.request import TrendReq
from tenacity import retry, stop_after_attempt, wait_exponential

from pmtb.research.models import SignalClassification, SourceResult
from pmtb.research.sentiment import SentimentClassifier
from pmtb.scanner.models import MarketCandidate

# Momentum thresholds — difference between recent and prior average
_BULLISH_THRESHOLD = 5.0
_BEARISH_THRESHOLD = -5.0


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _build_and_fetch(trend_req: TrendReq, kw: str):
    """Synchronous pytrends call (run via asyncio.to_thread)."""
    trend_req.build_payload([kw], timeframe="today 1-m")
    df = trend_req.interest_over_time()
    return df


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _fetch_related(trend_req: TrendReq):
    """Fetch related queries (synchronous, run via asyncio.to_thread)."""
    return trend_req.related_queries()


class TrendsAgent:
    """
    Derives a momentum signal from Google Trends interest-over-time.

    Implements the ResearchAgent Protocol.
    """

    source_name = "trends"

    def __init__(self, classifier: SentimentClassifier) -> None:
        self._classifier = classifier

    async def fetch(self, candidate: MarketCandidate, query: str) -> SourceResult:
        """
        Derive trend momentum signal for the query.

        Returns empty SourceResult if pytrends returns no data or encounters a 429.
        """
        log = logger.bind(source="trends", ticker=candidate.ticker, query=query)
        log.debug("Fetching Google Trends data")

        trend_req = TrendReq(hl="en-US", tz=360)

        # --- interest_over_time ---
        try:
            df = await asyncio.to_thread(_build_and_fetch, trend_req, query)
        except Exception as exc:
            log.warning("Trends interest_over_time error — returning empty", error=str(exc))
            return SourceResult(source="trends", signals=[])

        if df is None or df.empty:
            log.debug("Trends returned empty DataFrame")
            return SourceResult(source="trends", signals=[])

        # 1-second sleep between requests (Google Trends rate limiting)
        await asyncio.sleep(1)

        # --- related_queries ---
        related: dict = {}
        try:
            related = await asyncio.to_thread(_fetch_related, trend_req)
        except Exception as exc:
            log.warning("Trends related_queries error — continuing without", error=str(exc))

        # --- Derive momentum ---
        if query in df.columns:
            series = df[query].astype(float)
        else:
            # Use first column if query name not in columns (pytrends may normalize)
            numeric_cols = [c for c in df.columns if c != "isPartial"]
            if not numeric_cols:
                log.debug("No usable columns in trends DataFrame")
                return SourceResult(source="trends", signals=[])
            series = df[numeric_cols[0]].astype(float)

        n = len(series)
        if n < 14:
            # Not enough data for comparison — use overall trend direction
            slope = series.iloc[-1] - series.iloc[0] if n >= 2 else 0.0
            momentum = slope
        else:
            recent_avg = series.iloc[-7:].mean()
            prior_avg = series.iloc[-14:-7].mean()
            momentum = float(recent_avg - prior_avg)

        if momentum >= _BULLISH_THRESHOLD:
            sentiment = "bullish"
            confidence = min(1.0, momentum / 50.0)
        elif momentum <= _BEARISH_THRESHOLD:
            sentiment = "bearish"
            confidence = min(1.0, abs(momentum) / 50.0)
        else:
            sentiment = "neutral"
            confidence = 1.0 - abs(momentum) / _BULLISH_THRESHOLD

        confidence = max(0.0, min(1.0, confidence))

        signal = SignalClassification(
            sentiment=sentiment,
            confidence=confidence,
            reasoning=f"Google Trends momentum: {momentum:+.1f} (recent vs prior 7-day avg)",
        )

        log.info(
            "Trends fetch complete",
            sentiment=sentiment,
            confidence=confidence,
            momentum=momentum,
        )

        return SourceResult(
            source="trends",
            signals=[signal],
            raw_data={
                "interest_data": series.to_dict(),
                "related_queries": related,
                "momentum": momentum,
            },
        )
