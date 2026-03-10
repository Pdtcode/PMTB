"""
Tests for scanner filter functions and VolatilityTracker.

TDD RED phase — written before implementation exists.
All fixtures use raw dict format matching Kalshi API response (string values for price fields).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def make_market(
    ticker: str = "MKT-01",
    open_interest_fp: str = "500.0",
    volume_24h_fp: str = "200.0",
    yes_bid_dollars: str = "0.6200",
    yes_ask_dollars: str = "0.6500",
    close_time: str | None = None,
    status: str = "active",
) -> dict:
    """Helper: create a market dict matching Kalshi API format."""
    if close_time is None:
        # Default: 5 days from now
        dt = datetime.now(timezone.utc) + timedelta(days=5)
        close_time = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "ticker": ticker,
        "title": f"Market {ticker}",
        "category": "politics",
        "event_ticker": "SOME-EVENT",
        "open_interest_fp": open_interest_fp,
        "volume_24h_fp": volume_24h_fp,
        "yes_bid_dollars": yes_bid_dollars,
        "yes_ask_dollars": yes_ask_dollars,
        "close_time": close_time,
        "status": status,
    }


# --- filter_liquidity tests ---

def test_filter_liquidity_passes_above_threshold():
    """Markets with open_interest_fp >= threshold pass."""
    from pmtb.scanner.filters import filter_liquidity

    markets = [
        make_market("MKT-01", open_interest_fp="200.0"),
        make_market("MKT-02", open_interest_fp="100.0"),
    ]
    passing, rejected = filter_liquidity(markets, min_open_interest=100.0)
    assert len(passing) == 2
    assert rejected == 0


def test_filter_liquidity_rejects_below_threshold():
    """Markets with open_interest_fp < threshold are rejected."""
    from pmtb.scanner.filters import filter_liquidity

    markets = [
        make_market("MKT-01", open_interest_fp="50.0"),   # rejected
        make_market("MKT-02", open_interest_fp="200.0"),  # passes
        make_market("MKT-03", open_interest_fp="99.9"),   # rejected
    ]
    passing, rejected = filter_liquidity(markets, min_open_interest=100.0)
    assert len(passing) == 1
    assert passing[0]["ticker"] == "MKT-02"
    assert rejected == 2


def test_filter_liquidity_empty_list():
    """Empty input list returns empty list with 0 rejections."""
    from pmtb.scanner.filters import filter_liquidity

    passing, rejected = filter_liquidity([], min_open_interest=100.0)
    assert passing == []
    assert rejected == 0


def test_filter_liquidity_missing_field_treated_as_zero():
    """Markets missing open_interest_fp are treated as 0 and rejected."""
    from pmtb.scanner.filters import filter_liquidity

    market = {"ticker": "MKT-01"}  # no open_interest_fp
    passing, rejected = filter_liquidity([market], min_open_interest=100.0)
    assert passing == []
    assert rejected == 1


# --- filter_volume tests ---

def test_filter_volume_passes_above_threshold():
    """Markets with volume_24h_fp >= threshold pass."""
    from pmtb.scanner.filters import filter_volume

    markets = [
        make_market("MKT-01", volume_24h_fp="100.0"),
        make_market("MKT-02", volume_24h_fp="50.0"),
    ]
    passing, rejected = filter_volume(markets, min_volume_24h=50.0)
    assert len(passing) == 2
    assert rejected == 0


def test_filter_volume_rejects_below_threshold():
    """Markets with volume_24h_fp < threshold are rejected."""
    from pmtb.scanner.filters import filter_volume

    markets = [
        make_market("MKT-01", volume_24h_fp="10.0"),   # rejected
        make_market("MKT-02", volume_24h_fp="100.0"),  # passes
    ]
    passing, rejected = filter_volume(markets, min_volume_24h=50.0)
    assert len(passing) == 1
    assert rejected == 1


# --- filter_spread tests ---

def test_filter_spread_passing():
    """Markets with spread <= max_spread pass."""
    from pmtb.scanner.filters import filter_spread

    markets = [
        make_market("MKT-01", yes_bid_dollars="0.6200", yes_ask_dollars="0.6500"),  # spread=0.03
    ]
    passing, rejected = filter_spread(markets, max_spread=0.15)
    assert len(passing) == 1
    assert rejected == 0


def test_filter_spread_rejects_wide_spread():
    """Markets with spread > max_spread are rejected."""
    from pmtb.scanner.filters import filter_spread

    markets = [
        make_market("MKT-01", yes_bid_dollars="0.3000", yes_ask_dollars="0.7000"),  # spread=0.40
    ]
    passing, rejected = filter_spread(markets, max_spread=0.15)
    assert passing == []
    assert rejected == 1


def test_filter_spread_rejects_missing_fields():
    """Markets missing bid/ask fields are rejected."""
    from pmtb.scanner.filters import filter_spread

    market = {"ticker": "MKT-01"}  # no bid/ask
    passing, rejected = filter_spread([market], max_spread=0.15)
    assert passing == []
    assert rejected == 1


def test_filter_spread_mixed():
    """Mix of passing and failing markets."""
    from pmtb.scanner.filters import filter_spread

    markets = [
        make_market("MKT-01", yes_bid_dollars="0.6200", yes_ask_dollars="0.6500"),  # spread=0.03, pass
        make_market("MKT-02", yes_bid_dollars="0.2000", yes_ask_dollars="0.8000"),  # spread=0.60, reject
    ]
    passing, rejected = filter_spread(markets, max_spread=0.15)
    assert len(passing) == 1
    assert passing[0]["ticker"] == "MKT-01"
    assert rejected == 1


# --- filter_ttr tests ---

def test_filter_ttr_too_soon():
    """Market resolving in 30 minutes is excluded (< min_ttr_hours)."""
    from pmtb.scanner.filters import filter_ttr

    soon = datetime.now(timezone.utc) + timedelta(minutes=30)
    market = make_market("MKT-01", close_time=soon.strftime("%Y-%m-%dT%H:%M:%SZ"))
    passing, rejected = filter_ttr([market], min_hours=1.0, max_days=30.0)
    assert passing == []
    assert rejected == 1


def test_filter_ttr_too_far():
    """Market resolving in 60 days is excluded (> max_ttr_days)."""
    from pmtb.scanner.filters import filter_ttr

    far = datetime.now(timezone.utc) + timedelta(days=60)
    market = make_market("MKT-01", close_time=far.strftime("%Y-%m-%dT%H:%M:%SZ"))
    passing, rejected = filter_ttr([market], min_hours=1.0, max_days=30.0)
    assert passing == []
    assert rejected == 1


def test_filter_ttr_passing():
    """Market resolving in 5 days passes the TTR filter."""
    from pmtb.scanner.filters import filter_ttr

    good = datetime.now(timezone.utc) + timedelta(days=5)
    market = make_market("MKT-01", close_time=good.strftime("%Y-%m-%dT%H:%M:%SZ"))
    passing, rejected = filter_ttr([market], min_hours=1.0, max_days=30.0)
    assert len(passing) == 1
    assert rejected == 0


def test_filter_ttr_mixed():
    """Mix of markets at different TTRs."""
    from pmtb.scanner.filters import filter_ttr

    markets = [
        make_market("MKT-01", close_time=(datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")),  # too soon
        make_market("MKT-02", close_time=(datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")),     # passes
        make_market("MKT-03", close_time=(datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")),    # too far
    ]
    passing, rejected = filter_ttr(markets, min_hours=1.0, max_days=30.0)
    assert len(passing) == 1
    assert passing[0]["ticker"] == "MKT-02"
    assert rejected == 2


# --- VolatilityTracker tests ---

def test_volatility_tracker_returns_none_during_warmup():
    """VolatilityTracker returns None during warmup period."""
    from pmtb.scanner.filters import VolatilityTracker

    tracker = VolatilityTracker()
    # Record fewer than warmup (6) snapshots
    for price in [0.50, 0.52, 0.51, 0.53, 0.49]:
        result = tracker.record_and_get("MKT-01", price, warmup=6)
    assert result is None


def test_volatility_tracker_returns_stdev_after_warmup():
    """VolatilityTracker returns stdev after warmup snapshots are recorded."""
    from pmtb.scanner.filters import VolatilityTracker

    tracker = VolatilityTracker()
    prices = [0.50, 0.52, 0.51, 0.53, 0.49, 0.55, 0.48]
    result = None
    for p in prices:
        result = tracker.record_and_get("MKT-01", p, warmup=6)
    # After 7 snapshots (>= warmup of 6), should return a float
    assert result is not None
    assert isinstance(result, float)
    assert result > 0


def test_volatility_tracker_deque_maxlen():
    """VolatilityTracker deque does not grow beyond 50 entries."""
    from pmtb.scanner.filters import VolatilityTracker

    tracker = VolatilityTracker()
    for i in range(100):
        tracker.record_and_get("MKT-01", 0.5 + (i % 10) * 0.01, warmup=6)
    # Access internal state to verify maxlen=50
    history = tracker._histories["MKT-01"]
    assert len(history) == 50


# --- filter_volatility tests ---

def test_volatility_warmup_skip():
    """Markets with < warmup snapshots PASS the filter (not rejected)."""
    from pmtb.scanner.filters import VolatilityTracker, filter_volatility

    tracker = VolatilityTracker()
    market = make_market("MKT-01", yes_bid_dollars="0.5000")
    # Only 3 snapshots recorded (< warmup of 6)
    for _ in range(3):
        passing, rejected = filter_volatility([market], min_volatility=0.005, tracker=tracker, warmup=6)
    assert len(passing) == 1
    assert rejected == 0


def test_filter_volatility_low_stdev_rejected():
    """Market with sufficient snapshots but low stdev is rejected."""
    from pmtb.scanner.filters import VolatilityTracker, filter_volatility

    tracker = VolatilityTracker()
    market = make_market("MKT-01", yes_bid_dollars="0.5000")
    # Record 10 identical prices → stdev ≈ 0 → rejected
    for _ in range(10):
        passing, rejected = filter_volatility([market], min_volatility=0.005, tracker=tracker, warmup=6)
    assert len(passing) == 0
    assert rejected == 1


def test_filter_volatility_passing():
    """Market with sufficient snapshots and high stdev passes."""
    from pmtb.scanner.filters import VolatilityTracker, filter_volatility

    tracker = VolatilityTracker()
    # Alternate prices to create high stdev
    prices_cycle = ["0.4000", "0.6000", "0.4000", "0.6000", "0.4000", "0.6000",
                    "0.4000", "0.6000", "0.4000", "0.6000"]
    result_passing = None
    result_rejected = None
    for price_str in prices_cycle:
        market = make_market("MKT-01", yes_bid_dollars=price_str)
        result_passing, result_rejected = filter_volatility(
            [market], min_volatility=0.005, tracker=tracker, warmup=6
        )
    # After 10 snapshots with alternating 0.4/0.6, stdev >> 0.005
    assert len(result_passing) == 1
    assert result_rejected == 0
