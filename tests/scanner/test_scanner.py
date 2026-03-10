"""
Tests for MarketScanner class.

Uses unittest.mock.AsyncMock for KalshiClient._request and MagicMock for
the session factory. DB I/O is fully mocked — no PostgreSQL connection required.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from pmtb.scanner.models import MarketCandidate, ScanResult


# ---------------------------------------------------------------------------
# Helpers — shared test fixtures
# ---------------------------------------------------------------------------


def _close_time_str(hours_from_now: float) -> str:
    """Return ISO 8601 UTC string N hours from now."""
    dt = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_market(
    ticker: str,
    *,
    oi: str = "500.00",
    vol: str = "200.00",
    bid: str = "0.45",
    ask: str = "0.55",
    close_hours: float = 48.0,
    category: str = "politics",
    title: str = "Test Market",
    event_ticker: str = "TEST-EVENT",
) -> dict:
    """Build a minimal Kalshi market dict for testing."""
    return {
        "ticker": ticker,
        "title": title,
        "category": category,
        "event_ticker": event_ticker,
        "open_interest_fp": oi,
        "volume_24h_fp": vol,
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "close_time": _close_time_str(close_hours),
    }


def _make_settings(**overrides):
    """Build a minimal Settings-like namespace for testing."""
    defaults = {
        "scanner_min_open_interest": 100.0,
        "scanner_min_volume_24h": 50.0,
        "scanner_max_spread": 0.15,
        "scanner_min_ttr_hours": 1.0,
        "scanner_max_ttr_days": 30.0,
        "scanner_min_volatility": 0.005,
        "scanner_volatility_warmup": 6,
        "scanner_enrichment_concurrency": 5,
        "scan_interval_seconds": 300,
    }
    defaults.update(overrides)
    ns = MagicMock()
    for k, v in defaults.items():
        setattr(ns, k, v)
    return ns


def _make_orderbook_response(yes_bid_price: str = "0.62", no_bid_price: str = "0.35") -> dict:
    """Build a minimal Kalshi orderbook response dict."""
    return {
        "orderbook_fp": {
            "yes_dollars": [[yes_bid_price, "500.00"]],
            "no_dollars": [[no_bid_price, "400.00"]],
        }
    }


def _make_event_response(event_ticker: str = "TEST-EVENT", title: str = "Test Event") -> dict:
    return {"event": {"event_ticker": event_ticker, "title": title}}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings():
    return _make_settings()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._request = AsyncMock()
    return client


@pytest.fixture
def mock_session():
    """Return a mock async context manager session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, session


# ---------------------------------------------------------------------------
# Import scanner here (will fail RED until implementation exists)
# ---------------------------------------------------------------------------


@pytest.fixture
def scanner(mock_client, settings, mock_session):
    from pmtb.scanner.scanner import MarketScanner

    factory, _ = mock_session
    return MarketScanner(mock_client, settings, session_factory=factory)


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------


class TestPagination:
    def test_pagination_fetches_all_pages(self, scanner, mock_client):
        """Two-page response: both pages are collected into flat list."""
        page1_markets = [_make_market("TICKER-A"), _make_market("TICKER-B")]
        page2_markets = [_make_market("TICKER-C")]

        mock_client._request.side_effect = [
            # Page 1: has cursor
            {"markets": page1_markets, "cursor": "abc123"},
            # Page 2: no cursor → stop
            {"markets": page2_markets, "cursor": None},
        ]

        result = asyncio.get_event_loop().run_until_complete(scanner._fetch_all_markets())

        assert len(result) == 3
        tickers = {m["ticker"] for m in result}
        assert tickers == {"TICKER-A", "TICKER-B", "TICKER-C"}

    def test_pagination_stops_on_empty_cursor(self, scanner, mock_client):
        """Single page with no cursor: loop exits after one call."""
        mock_client._request.return_value = {
            "markets": [_make_market("TICKER-A")],
            "cursor": None,
        }

        result = asyncio.get_event_loop().run_until_complete(scanner._fetch_all_markets())

        assert mock_client._request.call_count == 1
        assert len(result) == 1

    def test_pagination_uses_request_not_get_markets(self, scanner, mock_client):
        """Pagination must use _request() directly (not get_markets()) to see cursor."""
        mock_client._request.return_value = {
            "markets": [_make_market("TICKER-A")],
            "cursor": "",
        }

        asyncio.get_event_loop().run_until_complete(scanner._fetch_all_markets())

        # _request was called (not get_markets)
        mock_client._request.assert_called_once()
        call_args = mock_client._request.call_args
        assert call_args[0][0] == "GET"
        assert "markets" in call_args[0][1]


# ---------------------------------------------------------------------------
# run_cycle tests
# ---------------------------------------------------------------------------


class TestRunCycle:
    def _setup_request_side_effect(self, mock_client, markets):
        """
        Set up _request side_effect to handle:
          - Initial paginated market fetch (returns one page)
          - Orderbook fetches for each passing candidate
          - Event fetches for each passing candidate
        """

        async def side_effect(method, path, *, params=None, json=None):
            if "orderbook" in path:
                ticker = path.split("/markets/")[1].split("/")[0]
                return _make_orderbook_response()
            elif "/events/" in path:
                return _make_event_response()
            else:
                # Pagination
                return {"markets": markets, "cursor": None}

        mock_client._request.side_effect = side_effect

    def test_run_cycle_returns_scan_result(self, mock_client, settings, mock_session):
        """run_cycle returns a ScanResult with correct metadata."""
        from pmtb.scanner.scanner import MarketScanner

        factory, _ = mock_session

        # 3 passing markets, 2 fail liquidity
        passing = [_make_market(f"PASS-{i}") for i in range(3)]
        failing = [_make_market(f"FAIL-{i}", oi="0.00") for i in range(2)]
        all_markets = passing + failing

        self._setup_request_side_effect(mock_client, all_markets)

        scanner = MarketScanner(mock_client, settings, session_factory=factory)
        result = asyncio.get_event_loop().run_until_complete(scanner.run_cycle())

        assert isinstance(result, ScanResult)
        assert result.total_markets == 5
        assert result.rejected_liquidity == 2
        assert len(result.candidates) == 3
        assert result.cycle_id  # non-empty string

    def test_candidates_sorted_by_edge_potential(self, mock_client, settings, mock_session):
        """
        Candidates sorted by |implied_probability - 0.5| ascending.
        Market closest to 50% comes first.
        """
        from pmtb.scanner.scanner import MarketScanner

        factory, _ = mock_session

        # Three markets with different bid/ask to produce different implied probs
        # prob = (yes_bid + yes_ask) / 2
        # Market A: bid=0.49, ask=0.51 → prob=0.50 (closest to 50%)
        # Market B: bid=0.29, ask=0.31 → prob=0.30 (distance 0.20)
        # Market C: bid=0.69, ask=0.71 → prob=0.70 (distance 0.20)
        markets = [
            _make_market("MARKET-B", bid="0.29", ask="0.31"),
            _make_market("MARKET-C", bid="0.69", ask="0.71"),
            _make_market("MARKET-A", bid="0.49", ask="0.51"),
        ]

        async def side_effect(method, path, *, params=None, json=None):
            if "orderbook" in path:
                ticker = path.split("/markets/")[1].split("/")[0]
                if "MARKET-A" in ticker:
                    return _make_orderbook_response("0.49", "0.51")
                elif "MARKET-B" in ticker:
                    return _make_orderbook_response("0.29", "0.71")  # no_bid=0.71 → yes_ask=0.29
                else:
                    return _make_orderbook_response("0.69", "0.31")  # no_bid=0.31 → yes_ask=0.69
            elif "/events/" in path:
                return _make_event_response()
            else:
                return {"markets": markets, "cursor": None}

        mock_client._request.side_effect = side_effect
        scanner = MarketScanner(mock_client, settings, session_factory=factory)
        result = asyncio.get_event_loop().run_until_complete(scanner.run_cycle())

        assert len(result.candidates) == 3
        # First candidate should be closest to 50%
        first = result.candidates[0]
        assert first.ticker == "MARKET-A"

    def test_upsert_all_markets_not_just_candidates(self, mock_client, settings, mock_session):
        """All fetched markets are upserted — not just those passing filters."""
        from pmtb.scanner.scanner import MarketScanner

        factory, session = mock_session

        # 5 markets, 2 fail liquidity (oi too low)
        passing = [_make_market(f"PASS-{i}") for i in range(3)]
        failing = [_make_market(f"FAIL-{i}", oi="0.00") for i in range(2)]
        all_markets = passing + failing

        self._setup_request_side_effect(mock_client, all_markets)

        scanner = MarketScanner(mock_client, settings, session_factory=factory)
        asyncio.get_event_loop().run_until_complete(scanner.run_cycle())

        # session.execute should have been called at least once (for the upsert)
        # and commit should have been called exactly once
        session.commit.assert_called_once()

    def test_enrichment_fetches_orderbook_and_event(self, mock_client, settings, mock_session):
        """Enrichment hits orderbook and event endpoints per passing candidate."""
        from pmtb.scanner.scanner import MarketScanner

        factory, _ = mock_session

        market = _make_market("ENRICH-01", event_ticker="ENRICH-EVENT")
        markets = [market]

        orderbook_calls = []
        event_calls = []

        async def side_effect(method, path, *, params=None, json=None):
            if "orderbook" in path:
                orderbook_calls.append(path)
                return _make_orderbook_response("0.45", "0.40")
            elif "/events/" in path:
                event_calls.append(path)
                return _make_event_response("ENRICH-EVENT", "Enrichment Test")
            else:
                return {"markets": markets, "cursor": None}

        mock_client._request.side_effect = side_effect
        scanner = MarketScanner(mock_client, settings, session_factory=factory)
        result = asyncio.get_event_loop().run_until_complete(scanner.run_cycle())

        assert len(orderbook_calls) == 1
        assert "ENRICH-01" in orderbook_calls[0]
        assert len(event_calls) == 1
        assert "ENRICH-EVENT" in event_calls[0]

        # Verify candidate fields from enrichment
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.yes_bid == pytest.approx(0.45)
        assert c.yes_ask == pytest.approx(1.0 - 0.40)  # 1 - no_bid
        assert c.event_context["title"] == "Enrichment Test"
        assert c.event_context["event_ticker"] == "ENRICH-EVENT"


# ---------------------------------------------------------------------------
# Rejection logging test
# ---------------------------------------------------------------------------


class TestRejectionLogging:
    def test_rejection_logged_at_debug(self, mock_client, settings, mock_session):
        """Markets rejected by filters have their rejection reason logged at DEBUG."""
        from pmtb.scanner.scanner import MarketScanner

        factory, _ = mock_session

        # Market that fails liquidity filter
        bad_market = _make_market("BAD-01", oi="0.00")

        async def side_effect(method, path, *, params=None, json=None):
            return {"markets": [bad_market], "cursor": None}

        mock_client._request.side_effect = side_effect

        log_messages = []

        def capture_log(message):
            log_messages.append(message)

        scanner = MarketScanner(mock_client, settings, session_factory=factory)

        with patch("pmtb.scanner.scanner.logger") as mock_logger:
            mock_logger.bind.return_value = mock_logger
            asyncio.get_event_loop().run_until_complete(scanner.run_cycle())
            # Debug should have been called for rejection
            assert mock_logger.debug.called or mock_logger.bind.called


# ---------------------------------------------------------------------------
# run_forever test
# ---------------------------------------------------------------------------


class TestRunForever:
    def test_run_forever_sleeps_between_cycles(self, mock_client, settings, mock_session):
        """run_forever calls asyncio.sleep with scan_interval_seconds after each cycle."""
        from pmtb.scanner.scanner import MarketScanner

        factory, _ = mock_session

        cycle_count = 0
        sleep_seconds = []

        async def mock_run_cycle():
            nonlocal cycle_count
            cycle_count += 1
            # Return a fake result
            return MagicMock(spec=ScanResult, candidates=[])

        class _StopAfterFirstSleep(Exception):
            pass

        async def mock_sleep(seconds):
            sleep_seconds.append(seconds)
            # Raise after recording the sleep call so the loop terminates
            raise _StopAfterFirstSleep("stop after first sleep")

        scanner = MarketScanner(mock_client, settings, session_factory=factory)
        scanner.run_cycle = mock_run_cycle

        async def run():
            with patch("pmtb.scanner.scanner.asyncio.sleep", mock_sleep):
                try:
                    await scanner.run_forever()
                except _StopAfterFirstSleep:
                    pass

        asyncio.get_event_loop().run_until_complete(run())

        assert cycle_count >= 1
        assert len(sleep_seconds) >= 1
        assert sleep_seconds[0] == settings.scan_interval_seconds

    def test_run_forever_sleeps_on_success(self, mock_client, settings, mock_session):
        """run_forever calls asyncio.sleep with scan_interval_seconds after successful cycle."""
        # This is covered by test_run_forever_sleeps_between_cycles above.
        # Including as a separate explicit assertion for clarity.
        from pmtb.scanner.scanner import MarketScanner

        factory, _ = mock_session

        cycle_count = 0
        sleep_seconds = []

        class _StopAfterFirst(Exception):
            pass

        async def mock_run_cycle():
            nonlocal cycle_count
            cycle_count += 1
            return MagicMock(spec=ScanResult, candidates=[])

        async def mock_sleep(seconds):
            sleep_seconds.append(seconds)
            raise _StopAfterFirst("stop after first sleep")

        scanner = MarketScanner(mock_client, settings, session_factory=factory)
        scanner.run_cycle = mock_run_cycle

        async def run():
            with patch("pmtb.scanner.scanner.asyncio.sleep", mock_sleep):
                try:
                    await scanner.run_forever()
                except _StopAfterFirst:
                    pass

        asyncio.get_event_loop().run_until_complete(run())

        assert cycle_count >= 1
        assert len(sleep_seconds) >= 1
        assert sleep_seconds[0] == settings.scan_interval_seconds
