"""
Pydantic models for the research signal pipeline.

SignalClassification  — per-signal sentiment output (bullish/bearish/neutral + confidence)
SourceResult          — raw output from a single research agent (list of classifications)
SourceSummary         — aggregated per-source summary (one sentiment/confidence per source per cycle)
SignalBundle          — per-market per-cycle bundle of all source summaries + .to_features()

Design decisions:
- Missing/failed sources are represented as None in SignalBundle (not neutral 0.5).
  Absence of data is NOT the same as neutral sentiment — the downstream XGBoost model
  handles NaN appropriately.
- to_features() maps bullish=1.0, neutral=0.0, bearish=-1.0 for numeric ML consumption.
- Twitter source is always None in Phase 3 (stub); slot is reserved for Phase 5+.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class SignalClassification(BaseModel):
    """
    The output of classifying a single piece of text (post, article, search trend).

    sentiment:  bullish | bearish | neutral (validated at construction)
    confidence: 0.0–1.0 — how certain the classifier is
    reasoning:  optional 1-2 sentence explanation (set by Claude, None for VADER)
    """

    sentiment: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None


class SourceResult(BaseModel):
    """
    Raw output from a single research agent for one market candidate.

    source:   identifier matching Signal.source in the DB ("reddit", "rss", "trends", "twitter")
    signals:  list of individual signal classifications — empty list means source returned no data
              (not an error; some markets have no relevant posts/articles)
    raw_data: full API response for debugging losing trades in Phase 7
    """

    source: str
    signals: list[SignalClassification]
    raw_data: dict | None = None


class SourceSummary(BaseModel):
    """
    Aggregated summary for one source after processing its SourceResult.

    sentiment:    overall sentiment for this source this cycle, or None if source failed/timed out
    confidence:   average/weighted confidence, or None if source failed/timed out
    signal_count: number of individual SignalClassification objects processed
    """

    sentiment: str | None
    confidence: float | None
    signal_count: int


class SignalBundle(BaseModel):
    """
    Per-market per-cycle bundle of all source summaries.

    One SignalBundle is produced per MarketCandidate per scan cycle. The bundle is NOT
    persisted directly to the DB — individual Signal rows are written separately. The
    bundle is passed downstream to Phase 4's XGBoost model via to_features().

    ticker:   matches MarketCandidate.ticker (and indirectly Signal.market_id via lookup)
    cycle_id: matches Signal.cycle_id for cross-referencing individual signals
    reddit/rss/trends/twitter: SourceSummary or None (None = source failed or timed out)
    """

    ticker: str
    cycle_id: str
    reddit: SourceSummary | None = None
    rss: SourceSummary | None = None
    trends: SourceSummary | None = None
    twitter: SourceSummary | None = None  # Always None in Phase 3 (Twitter agent is a stub)

    def to_features(self) -> dict[str, float]:
        """
        Produce a flat numeric feature dict for XGBoost consumption.

        Missing sources produce float("nan") — the caller (XGBoost wrapper) handles NaN
        imputation. NaN explicitly signals "no data", which is different from neutral (0.0).

        Returns a dict with exactly 8 keys:
            {reddit,rss,trends,twitter}_{sentiment,confidence}
        """
        _NAN = float("nan")

        def _sentiment_score(s: SourceSummary | None) -> float:
            if s is None or s.sentiment is None:
                return _NAN
            return {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}.get(s.sentiment, _NAN)

        def _confidence(s: SourceSummary | None) -> float:
            return s.confidence if s is not None and s.confidence is not None else _NAN

        return {
            "reddit_sentiment": _sentiment_score(self.reddit),
            "reddit_confidence": _confidence(self.reddit),
            "rss_sentiment": _sentiment_score(self.rss),
            "rss_confidence": _confidence(self.rss),
            "trends_sentiment": _sentiment_score(self.trends),
            "trends_confidence": _confidence(self.trends),
            "twitter_sentiment": _sentiment_score(self.twitter),
            "twitter_confidence": _confidence(self.twitter),
        }
