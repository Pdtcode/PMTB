"""
Pure filter functions for the market scanner pipeline.

Each filter accepts a list of raw market dicts (Kalshi API format) and a threshold,
returning a (passing_list, rejected_count) tuple. This makes them independently
testable without any API mocks.

Design decisions:
- All price/volume fields in Kalshi market dicts are fixed-point strings — parsed here.
- filter_liquidity uses open_interest_fp, NOT liquidity_dollars (deprecated, always 0).
- Warmup semantics: markets in warmup PASS the volatility filter (benefit of the doubt).
"""
from __future__ import annotations

import statistics
from collections import deque
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_close_time(raw: str) -> datetime:
    """
    Parse a Kalshi ISO 8601 close_time string to an aware datetime.

    Handles the "Z" suffix that Python's fromisoformat() rejects before 3.11.
    Returns a UTC-aware datetime.
    """
    normalized = raw.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


# ---------------------------------------------------------------------------
# Filter functions
# ---------------------------------------------------------------------------


def filter_liquidity(
    markets: list[dict], min_open_interest: float
) -> tuple[list[dict], int]:
    """
    Reject markets where open_interest_fp < min_open_interest.

    Uses open_interest_fp (fixed-point string), NOT liquidity_dollars (deprecated).
    Missing field is treated as 0 and rejected.

    Returns:
        (passing_markets, rejected_count)
    """
    passing = []
    rejected = 0
    for m in markets:
        oi = float(m.get("open_interest_fp", "0"))
        if oi >= min_open_interest:
            passing.append(m)
        else:
            rejected += 1
    return passing, rejected


def filter_volume(
    markets: list[dict], min_volume_24h: float
) -> tuple[list[dict], int]:
    """
    Reject markets where volume_24h_fp < min_volume_24h.

    Missing field is treated as 0 and rejected.

    Returns:
        (passing_markets, rejected_count)
    """
    passing = []
    rejected = 0
    for m in markets:
        vol = float(m.get("volume_24h_fp", "0"))
        if vol >= min_volume_24h:
            passing.append(m)
        else:
            rejected += 1
    return passing, rejected


def filter_spread(
    markets: list[dict], max_spread: float
) -> tuple[list[dict], int]:
    """
    Reject markets where (yes_ask_dollars - yes_bid_dollars) > max_spread,
    or where bid/ask fields are missing.

    Returns:
        (passing_markets, rejected_count)
    """
    passing = []
    rejected = 0
    for m in markets:
        bid_raw = m.get("yes_bid_dollars")
        ask_raw = m.get("yes_ask_dollars")
        if bid_raw is None or ask_raw is None:
            rejected += 1
            continue
        try:
            spread = float(ask_raw) - float(bid_raw)
        except (ValueError, TypeError):
            rejected += 1
            continue
        if spread <= max_spread:
            passing.append(m)
        else:
            rejected += 1
    return passing, rejected


def filter_ttr(
    markets: list[dict], min_hours: float, max_days: float
) -> tuple[list[dict], int]:
    """
    Reject markets resolving too soon (< min_hours) or too far (> max_days) from now.

    Parses close_time ISO 8601 string from the market dict.

    Returns:
        (passing_markets, rejected_count)
    """
    now = datetime.now(timezone.utc)
    passing = []
    rejected = 0
    for m in markets:
        raw_ct = m.get("close_time")
        if not raw_ct:
            rejected += 1
            continue
        try:
            ct = parse_close_time(raw_ct)
        except (ValueError, TypeError):
            rejected += 1
            continue
        delta = ct - now
        hours_remaining = delta.total_seconds() / 3600
        days_remaining = hours_remaining / 24
        if hours_remaining >= min_hours and days_remaining <= max_days:
            passing.append(m)
        else:
            rejected += 1
    return passing, rejected


# ---------------------------------------------------------------------------
# Volatility tracker and filter
# ---------------------------------------------------------------------------


class VolatilityTracker:
    """
    Rolling price volatility tracker using a per-ticker deque.

    Maintains a sliding window of the last 50 price snapshots per ticker.
    Returns None during the warmup period (fewer than `warmup` snapshots),
    then returns the population stdev as a float.
    """

    def __init__(self) -> None:
        # Public-ish access used in tests for deque length verification
        self._histories: dict[str, deque[float]] = {}

    def record_and_get(self, ticker: str, price: float, warmup: int) -> float | None:
        """
        Record a price snapshot and return volatility if past warmup.

        Args:
            ticker: Market ticker string.
            price: Current price (e.g., yes_bid as float in [0, 1]).
            warmup: Number of snapshots required before computing stdev.

        Returns:
            float stdev if len >= warmup and len >= 2, else None.
        """
        if ticker not in self._histories:
            self._histories[ticker] = deque(maxlen=50)
        self._histories[ticker].append(price)
        history = self._histories[ticker]
        if len(history) >= warmup and len(history) >= 2:
            return statistics.stdev(history)
        return None


def filter_volatility(
    markets: list[dict],
    min_volatility: float,
    tracker: VolatilityTracker,
    warmup: int,
) -> tuple[list[dict], int]:
    """
    Reject markets with price stdev < min_volatility (after warmup).

    Markets in warmup (tracker returns None) PASS the filter — we give them
    the benefit of the doubt until enough snapshots are available.

    Price snapshot: float(yes_bid_dollars) — the most liquid observable price.

    Returns:
        (passing_markets, rejected_count)
    """
    passing = []
    rejected = 0
    for m in markets:
        ticker = m.get("ticker", "")
        bid_raw = m.get("yes_bid_dollars", "0")
        try:
            price = float(bid_raw)
        except (ValueError, TypeError):
            price = 0.0
        stdev = tracker.record_and_get(ticker, price, warmup=warmup)
        if stdev is None:
            # Warmup: market passes
            passing.append(m)
        elif stdev >= min_volatility:
            passing.append(m)
        else:
            rejected += 1
    return passing, rejected
