"""
Tests for QueryConstructor and QueryCache.

TDD RED phase — tests written before implementation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from pmtb.scanner.models import MarketCandidate


def make_candidate(ticker: str, title: str, category: str = "economics") -> MarketCandidate:
    """Factory for MarketCandidate fixtures."""
    return MarketCandidate(
        ticker=ticker,
        title=title,
        category=category,
        event_context={},
        close_time=datetime(2025, 12, 31, tzinfo=timezone.utc),
        yes_bid=0.45,
        yes_ask=0.50,
        implied_probability=0.475,
        spread=0.05,
        volume_24h=1000.0,
        volatility_score=0.02,
    )


# ---------------------------------------------------------------------------
# QueryCache tests
# ---------------------------------------------------------------------------


def test_cache_miss_returns_none():
    """Cache returns None for an uncached ticker."""
    from pmtb.research.query import QueryCache

    cache = QueryCache(ttl_seconds=3600)
    assert cache.get("TICKER-1") is None


def test_cache_hit_within_ttl():
    """Cache returns the stored query within TTL."""
    from pmtb.research.query import QueryCache

    cache = QueryCache(ttl_seconds=3600)
    cache.set("TICKER-1", "Fed interest rates June 2025")
    result = cache.get("TICKER-1")
    assert result == "Fed interest rates June 2025"


def test_cache_miss_after_ttl(monkeypatch):
    """Cache returns None after TTL has expired."""
    from pmtb.research.query import QueryCache
    from datetime import timedelta

    cache = QueryCache(ttl_seconds=1)
    cache.set("TICKER-1", "Fed interest rates")

    # Advance time past expiry using a frozen datetime
    from pmtb.research import query as query_module

    # Manipulate expires_at directly for test reliability
    entry = cache._store["TICKER-1"]
    # Set expires_at to the past
    cache._store["TICKER-1"] = type(entry)(
        query=entry.query,
        expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )

    assert cache.get("TICKER-1") is None


# ---------------------------------------------------------------------------
# QueryConstructor template extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_will_x_happen():
    """'Will X happen' pattern produces a template query without Claude."""
    from pmtb.research.query import QueryConstructor

    qc = QueryConstructor(cache_ttl=3600, anthropic_api_key=None)
    candidate = make_candidate("FED-JUN25", "Will the Fed raise rates in June 2025?")

    result = await qc.build_query(candidate)

    # Should not be empty and should contain meaningful terms
    assert isinstance(result, str)
    assert len(result.strip()) > 0


@pytest.mark.asyncio
async def test_template_price_pattern():
    """'Price of X above Y' pattern produces a price forecast query."""
    from pmtb.research.query import QueryConstructor

    qc = QueryConstructor(cache_ttl=3600, anthropic_api_key=None)
    candidate = make_candidate("BTC-100K", "Will Bitcoin price be above $100,000?", category="crypto")

    result = await qc.build_query(candidate)

    assert isinstance(result, str)
    assert len(result.strip()) > 0


@pytest.mark.asyncio
async def test_template_election_pattern():
    """'X election' pattern produces an election query."""
    from pmtb.research.query import QueryConstructor

    qc = QueryConstructor(cache_ttl=3600, anthropic_api_key=None)
    candidate = make_candidate("PRES-2024", "Will Democrat win the 2024 presidential election?", category="politics")

    result = await qc.build_query(candidate)

    assert isinstance(result, str)
    assert len(result.strip()) > 0


# ---------------------------------------------------------------------------
# Claude fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_fallback_when_template_fails():
    """Unusual market title falls back to Claude when key is available."""
    from pmtb.research.query import QueryConstructor

    # A title that doesn't match any template pattern cleanly
    # We'll force template to fail by passing a very short title
    candidate = make_candidate("WEIRD-001", "X", category="other")

    # Mock Claude client
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="X market outcome prediction")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)

    qc = QueryConstructor(cache_ttl=3600, anthropic_api_key="test-key")
    qc._client = mock_client

    result = await qc.build_query(candidate)

    assert isinstance(result, str)
    assert len(result.strip()) > 0


@pytest.mark.asyncio
async def test_keyword_fallback_no_api_key():
    """When no API key and template fails, falls back to keyword extraction."""
    from pmtb.research.query import QueryConstructor

    candidate = make_candidate("WEIRD-001", "X", category="other")
    qc = QueryConstructor(cache_ttl=3600, anthropic_api_key=None)

    result = await qc.build_query(candidate)

    # Should return something (even if just the ticker/title derived)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Cache integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_prevents_duplicate_generation():
    """Second call for same ticker uses cache, not template/Claude."""
    from pmtb.research.query import QueryConstructor

    qc = QueryConstructor(cache_ttl=3600, anthropic_api_key=None)
    candidate = make_candidate("FED-JUN25", "Will the Fed raise rates in June 2025?")

    first = await qc.build_query(candidate)
    second = await qc.build_query(candidate)

    assert first == second
