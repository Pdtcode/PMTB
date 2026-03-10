"""
Tests for feature vector builder.

RED phase: these tests should fail before implementation.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

import numpy as np
import pytest

from pmtb.prediction.features import build_feature_vector, FEATURE_NAMES
from pmtb.research.models import SignalBundle, SourceSummary
from pmtb.scanner.models import MarketCandidate


def _make_market(
    *,
    implied_probability: float = 0.6,
    spread: float = 0.05,
    volume_24h: float = 1000.0,
    hours_until_close: float = 24.0,
    volatility_score: float | None = 0.02,
) -> MarketCandidate:
    close_time = datetime.now(tz=timezone.utc) + timedelta(hours=hours_until_close)
    return MarketCandidate(
        ticker="TEST-YES",
        title="Test Market",
        category="politics",
        event_context={},
        close_time=close_time,
        yes_bid=implied_probability - spread / 2,
        yes_ask=implied_probability + spread / 2,
        implied_probability=implied_probability,
        spread=spread,
        volume_24h=volume_24h,
        volatility_score=volatility_score,
    )


def _make_bundle(
    *,
    reddit_sentiment: str | None = "bullish",
    reddit_confidence: float | None = 0.8,
    rss_sentiment: str | None = "bearish",
    rss_confidence: float | None = 0.7,
    trends_sentiment: str | None = None,
    trends_confidence: float | None = None,
    twitter_sentiment: str | None = None,
    twitter_confidence: float | None = None,
) -> SignalBundle:
    def make_source(sentiment, confidence):
        if sentiment is None:
            return None
        return SourceSummary(sentiment=sentiment, confidence=confidence, signal_count=3)

    return SignalBundle(
        ticker="TEST-YES",
        cycle_id="cycle-001",
        reddit=make_source(reddit_sentiment, reddit_confidence),
        rss=make_source(rss_sentiment, rss_confidence),
        trends=make_source(trends_sentiment, trends_confidence),
        twitter=make_source(twitter_sentiment, twitter_confidence),
    )


class TestFeatureNames:
    def test_feature_names_is_sorted(self):
        assert FEATURE_NAMES == sorted(FEATURE_NAMES)

    def test_feature_names_has_13_elements(self):
        assert len(FEATURE_NAMES) == 13

    def test_feature_names_contains_signal_features(self):
        for source in ["reddit", "rss", "trends", "twitter"]:
            assert f"{source}_sentiment" in FEATURE_NAMES
            assert f"{source}_confidence" in FEATURE_NAMES

    def test_feature_names_contains_market_metadata(self):
        for name in ["hours_to_close", "implied_prob", "spread", "volume_24h", "volatility_score"]:
            assert name in FEATURE_NAMES


class TestBuildFeatureVector:
    def test_returns_numpy_array(self):
        bundle = _make_bundle()
        market = _make_market()
        result = build_feature_vector(bundle, market)
        assert isinstance(result, np.ndarray)

    def test_returns_13_elements(self):
        bundle = _make_bundle()
        market = _make_market()
        result = build_feature_vector(bundle, market)
        assert result.shape == (13,)

    def test_consistent_ordering_matches_feature_names(self):
        bundle = _make_bundle()
        market = _make_market(implied_probability=0.6, spread=0.05)
        result = build_feature_vector(bundle, market)
        # The index of "implied_prob" in FEATURE_NAMES
        idx = FEATURE_NAMES.index("implied_prob")
        assert abs(result[idx] - 0.6) < 1e-9

    def test_missing_source_produces_nan(self):
        # trends and twitter are None
        bundle = _make_bundle()
        market = _make_market()
        result = build_feature_vector(bundle, market)
        trends_sentiment_idx = FEATURE_NAMES.index("trends_sentiment")
        trends_confidence_idx = FEATURE_NAMES.index("trends_confidence")
        twitter_sentiment_idx = FEATURE_NAMES.index("twitter_sentiment")
        twitter_confidence_idx = FEATURE_NAMES.index("twitter_confidence")
        assert math.isnan(result[trends_sentiment_idx])
        assert math.isnan(result[trends_confidence_idx])
        assert math.isnan(result[twitter_sentiment_idx])
        assert math.isnan(result[twitter_confidence_idx])

    def test_present_source_not_nan(self):
        bundle = _make_bundle()
        market = _make_market()
        result = build_feature_vector(bundle, market)
        reddit_sentiment_idx = FEATURE_NAMES.index("reddit_sentiment")
        assert not math.isnan(result[reddit_sentiment_idx])
        assert result[reddit_sentiment_idx] == 1.0  # bullish = 1.0

    def test_volatility_none_produces_nan(self):
        bundle = _make_bundle()
        market = _make_market(volatility_score=None)
        result = build_feature_vector(bundle, market)
        idx = FEATURE_NAMES.index("volatility_score")
        assert math.isnan(result[idx])

    def test_volatility_set_not_nan(self):
        bundle = _make_bundle()
        market = _make_market(volatility_score=0.03)
        result = build_feature_vector(bundle, market)
        idx = FEATURE_NAMES.index("volatility_score")
        assert not math.isnan(result[idx])
        assert abs(result[idx] - 0.03) < 1e-9

    def test_hours_to_close_positive(self):
        bundle = _make_bundle()
        market = _make_market(hours_until_close=48.0)
        result = build_feature_vector(bundle, market)
        idx = FEATURE_NAMES.index("hours_to_close")
        # Should be approximately 48 hours
        assert result[idx] > 40.0

    def test_hours_to_close_non_negative_for_past_market(self):
        bundle = _make_bundle()
        market = _make_market(hours_until_close=-1.0)
        result = build_feature_vector(bundle, market)
        idx = FEATURE_NAMES.index("hours_to_close")
        assert result[idx] == 0.0

    def test_spread_value(self):
        bundle = _make_bundle()
        market = _make_market(spread=0.1)
        result = build_feature_vector(bundle, market)
        idx = FEATURE_NAMES.index("spread")
        assert abs(result[idx] - 0.1) < 1e-9

    def test_volume_24h_value(self):
        bundle = _make_bundle()
        market = _make_market(volume_24h=2500.0)
        result = build_feature_vector(bundle, market)
        idx = FEATURE_NAMES.index("volume_24h")
        assert abs(result[idx] - 2500.0) < 1e-9

    def test_reproducible_for_same_inputs(self):
        bundle = _make_bundle()
        market = _make_market()
        result1 = build_feature_vector(bundle, market)
        result2 = build_feature_vector(bundle, market)
        # All non-NaN values should be approximately equal.
        # hours_to_close can differ by microseconds between two calls — use tolerance.
        for i in range(len(FEATURE_NAMES)):
            if not math.isnan(result1[i]):
                assert abs(result1[i] - result2[i]) < 0.01, (
                    f"Feature {FEATURE_NAMES[i]} not reproducible: {result1[i]} vs {result2[i]}"
                )

    def test_all_sources_missing_produces_all_signal_nans(self):
        bundle = SignalBundle(ticker="TEST-YES", cycle_id="cycle-001")
        market = _make_market()
        result = build_feature_vector(bundle, market)
        for source in ["reddit", "rss", "trends", "twitter"]:
            idx_s = FEATURE_NAMES.index(f"{source}_sentiment")
            idx_c = FEATURE_NAMES.index(f"{source}_confidence")
            assert math.isnan(result[idx_s])
            assert math.isnan(result[idx_c])
