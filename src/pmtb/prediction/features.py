"""
Feature vector construction for XGBoost prediction model.

Merges SignalBundle (8 signal features from 4 sources × 2 metrics) with
MarketCandidate metadata (5 market features) into a consistent 13-element
numpy array.

Design decisions:
- FEATURE_NAMES is sorted lexicographically — ensures consistent array ordering
  regardless of dict insertion order.
- Missing sources produce float("nan"), NOT 0.0 — absence of data is not neutral.
  XGBoost handles NaN natively via the `missing` parameter.
- hours_to_close is clipped at 0 — past-close markets are not negative.
- volatility_score: None -> float("nan"), do NOT impute to 0.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from pmtb.research.models import SignalBundle
from pmtb.scanner.models import MarketCandidate

# --- Signal feature keys (from SignalBundle.to_features()) ---
_SIGNAL_KEYS = [
    "reddit_confidence",
    "reddit_sentiment",
    "rss_confidence",
    "rss_sentiment",
    "trends_confidence",
    "trends_sentiment",
    "twitter_confidence",
    "twitter_sentiment",
]

# --- Market metadata feature keys ---
_MARKET_KEYS = [
    "hours_to_close",
    "implied_prob",
    "spread",
    "volume_24h",
    "volatility_score",
]

# FEATURE_NAMES: sorted list of all 13 feature keys (8 signal + 5 market).
# Used by XGBoostPredictor for consistent column ordering.
FEATURE_NAMES: list[str] = sorted(_SIGNAL_KEYS + _MARKET_KEYS)


def build_feature_vector(bundle: SignalBundle, market: MarketCandidate) -> np.ndarray:
    """
    Construct a 13-element numpy feature array from a SignalBundle + MarketCandidate.

    The array is ordered by FEATURE_NAMES (lexicographic sort). NaN values are
    preserved for missing sources and None metadata.

    Args:
        bundle: Research signal bundle with per-source sentiment/confidence summaries.
        market: Market candidate with price, volume, and timing metadata.

    Returns:
        np.ndarray of shape (13,) with dtype float64.
        NaN indicates missing/unavailable data for a feature.
    """
    # Signal features (8): produced by SignalBundle.to_features()
    signal_features: dict[str, float] = bundle.to_features()

    # Market metadata features (5)
    now_utc = datetime.now(tz=timezone.utc)
    hours_to_close = max(
        0.0,
        (market.close_time - now_utc).total_seconds() / 3600.0,
    )
    volatility_score = (
        float(market.volatility_score)
        if market.volatility_score is not None
        else float("nan")
    )
    market_features: dict[str, float] = {
        "implied_prob": float(market.implied_probability),
        "spread": float(market.spread),
        "volume_24h": float(market.volume_24h),
        "hours_to_close": hours_to_close,
        "volatility_score": volatility_score,
    }

    # Merge all features and build array in FEATURE_NAMES order
    all_features = {**signal_features, **market_features}
    return np.array([all_features[key] for key in FEATURE_NAMES], dtype=np.float64)
