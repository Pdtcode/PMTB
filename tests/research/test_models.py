"""
Unit tests for research pipeline Pydantic models and ResearchAgent Protocol.

Covers:
- SignalClassification validation (sentiment literals, confidence range)
- SourceResult with empty signals list
- SourceSummary with None fields (failed source)
- SignalBundle.to_features() — 8-key dict, NaN for missing sources, sentiment mapping
- ResearchAgent Protocol is runtime_checkable
"""
from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from pmtb.research.models import (
    SignalBundle,
    SignalClassification,
    SourceResult,
    SourceSummary,
)
from pmtb.research.agent import ResearchAgent


# ---------------------------------------------------------------------------
# SignalClassification
# ---------------------------------------------------------------------------


class TestSignalClassification:
    def test_valid_bullish(self):
        sc = SignalClassification(sentiment="bullish", confidence=0.8, reasoning=None)
        assert sc.sentiment == "bullish"
        assert sc.confidence == 0.8
        assert sc.reasoning is None

    def test_valid_bearish(self):
        sc = SignalClassification(sentiment="bearish", confidence=0.5, reasoning="rates rising")
        assert sc.sentiment == "bearish"
        assert sc.reasoning == "rates rising"

    def test_valid_neutral(self):
        sc = SignalClassification(sentiment="neutral", confidence=0.1)
        assert sc.sentiment == "neutral"

    def test_invalid_sentiment_raises(self):
        with pytest.raises(ValidationError):
            SignalClassification(sentiment="positive", confidence=0.5)

    def test_invalid_sentiment_empty_raises(self):
        with pytest.raises(ValidationError):
            SignalClassification(sentiment="", confidence=0.5)


# ---------------------------------------------------------------------------
# SourceResult
# ---------------------------------------------------------------------------


class TestSourceResult:
    def test_empty_signals_is_valid(self):
        """Source returning no data is a valid (not error) condition."""
        sr = SourceResult(source="reddit", signals=[])
        assert sr.source == "reddit"
        assert sr.signals == []
        assert sr.raw_data is None

    def test_with_signals(self):
        sc = SignalClassification(sentiment="bullish", confidence=0.7)
        sr = SourceResult(source="rss", signals=[sc], raw_data={"url": "http://example.com"})
        assert len(sr.signals) == 1
        assert sr.raw_data == {"url": "http://example.com"}


# ---------------------------------------------------------------------------
# SourceSummary
# ---------------------------------------------------------------------------


class TestSourceSummary:
    def test_failed_source_all_none_is_valid(self):
        """A source that timed out or failed has None sentiment/confidence."""
        ss = SourceSummary(sentiment=None, confidence=None, signal_count=0)
        assert ss.sentiment is None
        assert ss.confidence is None
        assert ss.signal_count == 0

    def test_successful_source(self):
        ss = SourceSummary(sentiment="bearish", confidence=0.65, signal_count=5)
        assert ss.sentiment == "bearish"
        assert ss.confidence == 0.65
        assert ss.signal_count == 5


# ---------------------------------------------------------------------------
# SignalBundle.to_features()
# ---------------------------------------------------------------------------


class TestSignalBundleToFeatures:
    def _make_bundle(self, **kwargs) -> SignalBundle:
        return SignalBundle(ticker="TEST-01", cycle_id="cycle-abc", **kwargs)

    def test_returns_exactly_8_keys(self):
        bundle = self._make_bundle()
        features = bundle.to_features()
        assert len(features) == 8
        expected_keys = {
            "reddit_sentiment", "reddit_confidence",
            "rss_sentiment", "rss_confidence",
            "trends_sentiment", "trends_confidence",
            "twitter_sentiment", "twitter_confidence",
        }
        assert set(features.keys()) == expected_keys

    def test_all_missing_sources_return_nan(self):
        """When all sources are None (not provided), all values must be NaN — not 0.0."""
        bundle = self._make_bundle()
        features = bundle.to_features()
        for key, val in features.items():
            assert math.isnan(val), f"Expected NaN for {key}, got {val}"

    def test_bullish_maps_to_1(self):
        ss = SourceSummary(sentiment="bullish", confidence=0.9, signal_count=3)
        bundle = self._make_bundle(reddit=ss)
        features = bundle.to_features()
        assert features["reddit_sentiment"] == 1.0
        assert features["reddit_confidence"] == 0.9

    def test_bearish_maps_to_minus_1(self):
        ss = SourceSummary(sentiment="bearish", confidence=0.6, signal_count=2)
        bundle = self._make_bundle(rss=ss)
        features = bundle.to_features()
        assert features["rss_sentiment"] == -1.0

    def test_neutral_maps_to_0(self):
        ss = SourceSummary(sentiment="neutral", confidence=0.4, signal_count=1)
        bundle = self._make_bundle(trends=ss)
        features = bundle.to_features()
        assert features["trends_sentiment"] == 0.0

    def test_source_with_none_sentiment_returns_nan(self):
        """SourceSummary with sentiment=None (failed source) → NaN, not 0.0."""
        ss = SourceSummary(sentiment=None, confidence=None, signal_count=0)
        bundle = self._make_bundle(reddit=ss)
        features = bundle.to_features()
        assert math.isnan(features["reddit_sentiment"])
        assert math.isnan(features["reddit_confidence"])

    def test_missing_twitter_returns_nan_even_with_other_sources(self):
        """Twitter is always None in Phase 3 (stub). Must remain NaN."""
        ss = SourceSummary(sentiment="bullish", confidence=0.8, signal_count=4)
        bundle = self._make_bundle(reddit=ss, rss=ss)
        features = bundle.to_features()
        assert math.isnan(features["twitter_sentiment"])
        assert math.isnan(features["twitter_confidence"])

    def test_partial_sources_mix_nan_and_real(self):
        reddit_ss = SourceSummary(sentiment="bullish", confidence=0.75, signal_count=5)
        bundle = self._make_bundle(reddit=reddit_ss)
        features = bundle.to_features()
        # reddit present
        assert features["reddit_sentiment"] == 1.0
        assert features["reddit_confidence"] == 0.75
        # others missing
        assert math.isnan(features["rss_sentiment"])
        assert math.isnan(features["rss_confidence"])
        assert math.isnan(features["trends_sentiment"])
        assert math.isnan(features["trends_confidence"])


# ---------------------------------------------------------------------------
# ResearchAgent Protocol
# ---------------------------------------------------------------------------


class TestResearchAgentProtocol:
    def test_protocol_is_runtime_checkable(self):
        """isinstance() on a conforming class must work at runtime."""

        class DummyAgent:
            source_name: str = "dummy"

            async def fetch(self, candidate, query):
                ...

        agent = DummyAgent()
        assert isinstance(agent, ResearchAgent)

    def test_non_conforming_class_is_not_instance(self):
        """Class missing fetch() method must not satisfy isinstance check."""

        class BrokenAgent:
            source_name: str = "broken"
            # no fetch method

        agent = BrokenAgent()
        assert not isinstance(agent, ResearchAgent)
