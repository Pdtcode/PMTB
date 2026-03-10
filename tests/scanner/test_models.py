"""
Tests for MarketCandidate and ScanResult Pydantic models.

TDD RED phase — these tests are written before the implementation exists.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError


def test_market_candidate_valid_construction():
    """MarketCandidate accepts all valid field values."""
    from pmtb.scanner.models import MarketCandidate

    candidate = MarketCandidate(
        ticker="SOME-MARKET-24",
        title="Will X happen?",
        category="politics",
        event_context={"event_ticker": "SOME-EVENT", "series": "US"},
        close_time=datetime(2026, 3, 20, 18, 0, 0, tzinfo=timezone.utc),
        yes_bid=0.62,
        yes_ask=0.65,
        implied_probability=0.635,
        spread=0.03,
        volume_24h=15000.0,
        volatility_score=0.02,
    )
    assert candidate.ticker == "SOME-MARKET-24"
    assert candidate.yes_bid == 0.62
    assert candidate.volatility_score == 0.02


def test_market_candidate_volatility_score_none():
    """MarketCandidate accepts None for optional volatility_score."""
    from pmtb.scanner.models import MarketCandidate

    candidate = MarketCandidate(
        ticker="SOME-MARKET-24",
        title="Will X happen?",
        category="politics",
        event_context={},
        close_time=datetime(2026, 3, 20, 18, 0, 0, tzinfo=timezone.utc),
        yes_bid=0.62,
        yes_ask=0.65,
        implied_probability=0.635,
        spread=0.03,
        volume_24h=15000.0,
        volatility_score=None,
    )
    assert candidate.volatility_score is None


def test_market_candidate_rejects_yes_bid_gt_one():
    """MarketCandidate rejects yes_bid > 1.0 with ValidationError."""
    from pmtb.scanner.models import MarketCandidate

    with pytest.raises(ValidationError):
        MarketCandidate(
            ticker="SOME-MARKET-24",
            title="Will X happen?",
            category="politics",
            event_context={},
            close_time=datetime(2026, 3, 20, 18, 0, 0, tzinfo=timezone.utc),
            yes_bid=1.5,  # invalid: > 1.0
            yes_ask=0.65,
            implied_probability=0.635,
            spread=0.03,
            volume_24h=15000.0,
        )


def test_market_candidate_rejects_negative_spread():
    """MarketCandidate rejects negative spread with ValidationError."""
    from pmtb.scanner.models import MarketCandidate

    with pytest.raises(ValidationError):
        MarketCandidate(
            ticker="SOME-MARKET-24",
            title="Will X happen?",
            category="politics",
            event_context={},
            close_time=datetime(2026, 3, 20, 18, 0, 0, tzinfo=timezone.utc),
            yes_bid=0.62,
            yes_ask=0.65,
            implied_probability=0.635,
            spread=-0.01,  # invalid: negative
            volume_24h=15000.0,
        )


def test_market_candidate_rejects_yes_ask_gt_one():
    """MarketCandidate rejects yes_ask > 1.0 with ValidationError."""
    from pmtb.scanner.models import MarketCandidate

    with pytest.raises(ValidationError):
        MarketCandidate(
            ticker="SOME-MARKET-24",
            title="Will X happen?",
            category="politics",
            event_context={},
            close_time=datetime(2026, 3, 20, 18, 0, 0, tzinfo=timezone.utc),
            yes_bid=0.62,
            yes_ask=1.1,  # invalid: > 1.0
            implied_probability=0.635,
            spread=0.03,
            volume_24h=15000.0,
        )


def test_market_candidate_rejects_negative_volume():
    """MarketCandidate rejects negative volume_24h with ValidationError."""
    from pmtb.scanner.models import MarketCandidate

    with pytest.raises(ValidationError):
        MarketCandidate(
            ticker="SOME-MARKET-24",
            title="Will X happen?",
            category="politics",
            event_context={},
            close_time=datetime(2026, 3, 20, 18, 0, 0, tzinfo=timezone.utc),
            yes_bid=0.62,
            yes_ask=0.65,
            implied_probability=0.635,
            spread=0.03,
            volume_24h=-1.0,  # invalid: negative
        )


def test_scan_result_with_candidates():
    """ScanResult holds candidates list plus rejection counts and metadata."""
    from pmtb.scanner.models import MarketCandidate, ScanResult

    candidate = MarketCandidate(
        ticker="SOME-MARKET-24",
        title="Will X happen?",
        category="politics",
        event_context={},
        close_time=datetime(2026, 3, 20, 18, 0, 0, tzinfo=timezone.utc),
        yes_bid=0.62,
        yes_ask=0.65,
        implied_probability=0.635,
        spread=0.03,
        volume_24h=15000.0,
    )

    result = ScanResult(
        candidates=[candidate],
        total_markets=100,
        rejected_liquidity=30,
        rejected_volume=20,
        rejected_spread=10,
        rejected_ttr=5,
        rejected_volatility=3,
        scan_duration_seconds=1.5,
        cycle_id="abc123",
    )
    assert len(result.candidates) == 1
    assert result.total_markets == 100
    assert result.rejected_liquidity == 30
    assert result.rejected_volume == 20
    assert result.rejected_spread == 10
    assert result.rejected_ttr == 5
    assert result.rejected_volatility == 3
    assert result.scan_duration_seconds == 1.5
    assert result.cycle_id == "abc123"


def test_scan_result_empty_candidates():
    """ScanResult with empty candidates list is valid."""
    from pmtb.scanner.models import ScanResult

    result = ScanResult(
        candidates=[],
        total_markets=50,
        rejected_liquidity=25,
        rejected_volume=15,
        rejected_spread=5,
        rejected_ttr=3,
        rejected_volatility=2,
        scan_duration_seconds=0.8,
        cycle_id="empty-cycle",
    )
    assert result.candidates == []
    assert result.total_markets == 50


def test_settings_scanner_fields():
    """Settings class has all scanner threshold fields with correct defaults."""
    from pmtb.config import Settings

    # Use TestSettings to avoid needing actual secrets
    class TestSettings(Settings):
        model_config = Settings.model_config
        database_url: str = "postgresql+asyncpg://test:test@localhost/test"
        kalshi_api_key_id: str = "test-key"
        kalshi_private_key_path: str = "/tmp/test.pem"

    s = TestSettings()
    assert s.scanner_min_open_interest == 100.0
    assert s.scanner_min_volume_24h == 50.0
    assert s.scanner_max_spread == 0.15
    assert s.scanner_min_ttr_hours == 1.0
    assert s.scanner_max_ttr_days == 30.0
    assert s.scanner_min_volatility == 0.005
    assert s.scanner_volatility_warmup == 6
    assert s.scanner_enrichment_concurrency == 5
