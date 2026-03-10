"""
Tests for KalshiWSClient — WebSocket client with auto-reconnect.

Uses mocked websockets.connect as async context manager.
Tests verify:
- Signed headers on connect
- Channel subscription after connect
- Message routing to on_message callback
- Auto-reconnect on ConnectionClosed (5-second sleep, NOT exponential)
- Auto-reconnect on OSError (5-second sleep)
- Subscription includes market_ticker
- WS URL selected by trading_mode (paper vs live)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import websockets.exceptions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(trading_mode: str = "paper") -> MagicMock:
    s = MagicMock()
    s.trading_mode = trading_mode
    s.kalshi_api_key_id = "test-key-id"
    s.kalshi_private_key_path = "/fake/key.pem"
    s.kalshi_ws_url = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    s.kalshi_demo_ws_url = "wss://demo-api.kalshi.co/trade-api/ws/v2"
    return s


def _make_ws_mock(messages: list | None = None) -> AsyncMock:
    """Return an AsyncMock that behaves like a websocket connection."""
    ws = AsyncMock()
    ws.send = AsyncMock()

    # Async iterator over messages, then StopAsyncIteration
    if messages is None:
        messages = []

    async def _aiter():
        for m in messages:
            yield m

    ws.__aiter__ = lambda self: _aiter()
    return ws


# ---------------------------------------------------------------------------
# Test 1: connects with signed headers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_uses_signed_headers():
    """KalshiWSClient passes signed headers to websockets.connect."""
    from pmtb.kalshi.ws_client import KalshiWSClient

    settings = _make_settings()
    ws_mock = _make_ws_mock(messages=[])

    with patch("pmtb.kalshi.ws_client.load_private_key", return_value=MagicMock()), \
         patch("pmtb.kalshi.ws_client.build_kalshi_headers", return_value={"KALSHI-ACCESS-KEY": "k"}) as mock_headers, \
         patch("pmtb.kalshi.ws_client.websockets.connect") as mock_connect:

        # Make context manager return ws_mock
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=ws_mock)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        client = KalshiWSClient(settings)

        messages_received = []
        async def on_message(msg): messages_received.append(msg)

        await client.run(on_message, channels=["orderbook_delta"], market_tickers=["SOME-TICKER"])

        # build_kalshi_headers must have been called
        mock_headers.assert_called_once()
        call_kwargs = mock_headers.call_args
        assert call_kwargs[1]["method"] == "GET" or call_kwargs[0][0] == "GET"

        # websockets.connect must have been called with additional_headers
        mock_connect.assert_called_once()
        _, kwargs = mock_connect.call_args
        assert "additional_headers" in kwargs
        assert kwargs["additional_headers"]["KALSHI-ACCESS-KEY"] == "k"


# ---------------------------------------------------------------------------
# Test 2: subscribes after connect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribes_after_connect():
    """After connecting, client sends subscription message."""
    from pmtb.kalshi.ws_client import KalshiWSClient

    settings = _make_settings()
    ws_mock = _make_ws_mock(messages=[])

    with patch("pmtb.kalshi.ws_client.load_private_key", return_value=MagicMock()), \
         patch("pmtb.kalshi.ws_client.build_kalshi_headers", return_value={}), \
         patch("pmtb.kalshi.ws_client.websockets.connect") as mock_connect:

        mock_connect.return_value.__aenter__ = AsyncMock(return_value=ws_mock)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        client = KalshiWSClient(settings)
        await client.run(AsyncMock(), channels=["orderbook_delta", "fill"], market_tickers=["TICKER-1"])

        ws_mock.send.assert_called_once()
        sent_payload = json.loads(ws_mock.send.call_args[0][0])
        assert sent_payload["cmd"] == "subscribe"
        assert "orderbook_delta" in sent_payload["params"]["channels"]
        assert "fill" in sent_payload["params"]["channels"]


# ---------------------------------------------------------------------------
# Test 3: messages routed to on_message callback as parsed dicts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_messages_routed_to_callback():
    """Incoming messages are JSON-parsed and passed to on_message."""
    from pmtb.kalshi.ws_client import KalshiWSClient

    settings = _make_settings()
    msg_data = {"type": "orderbook_delta", "seq": 1, "msg": {"ticker": "X-Y-Z"}}
    ws_mock = _make_ws_mock(messages=[json.dumps(msg_data)])

    with patch("pmtb.kalshi.ws_client.load_private_key", return_value=MagicMock()), \
         patch("pmtb.kalshi.ws_client.build_kalshi_headers", return_value={}), \
         patch("pmtb.kalshi.ws_client.websockets.connect") as mock_connect:

        mock_connect.return_value.__aenter__ = AsyncMock(return_value=ws_mock)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        client = KalshiWSClient(settings)

        received = []
        async def on_message(msg): received.append(msg)

        await client.run(on_message, channels=["orderbook_delta"], market_tickers=["X-Y-Z"])

        assert len(received) == 1
        assert received[0]["type"] == "orderbook_delta"
        assert received[0]["msg"]["ticker"] == "X-Y-Z"


# ---------------------------------------------------------------------------
# Test 4: reconnects on ConnectionClosed with 5-second sleep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconnects_on_connection_closed():
    """On ConnectionClosed, waits 5 seconds then reconnects."""
    from pmtb.kalshi.ws_client import KalshiWSClient

    settings = _make_settings()

    # First ws: raises ConnectionClosed in __aenter__
    # Second ws: succeeds with no messages
    fail_ws = AsyncMock()
    fail_ws.send = AsyncMock()

    async def fail_aiter():
        raise websockets.exceptions.ConnectionClosed(None, None)
        yield  # makes it a generator

    fail_ws.__aiter__ = lambda self: fail_aiter()

    success_ws = _make_ws_mock(messages=[])

    call_count = 0

    class ContextManagerSequence:
        def __init__(self, wses):
            self.wses = wses
            self.idx = 0

        def __call__(self, url, **kwargs):
            cm = MagicMock()
            ws = self.wses[min(self.idx, len(self.wses) - 1)]
            self.idx += 1
            cm.__aenter__ = AsyncMock(return_value=ws)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

    connect_seq = ContextManagerSequence([fail_ws, success_ws])

    with patch("pmtb.kalshi.ws_client.load_private_key", return_value=MagicMock()), \
         patch("pmtb.kalshi.ws_client.build_kalshi_headers", return_value={}), \
         patch("pmtb.kalshi.ws_client.websockets.connect", side_effect=connect_seq), \
         patch("pmtb.kalshi.ws_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        client = KalshiWSClient(settings)
        await client.run(AsyncMock(), channels=["orderbook_delta"], market_tickers=["T"])

        # sleep(5) must have been called exactly once after the first failure
        mock_sleep.assert_called_once_with(5)


# ---------------------------------------------------------------------------
# Test 5: reconnects on OSError with 5-second sleep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconnects_on_oserror():
    """On OSError, waits 5 seconds then reconnects."""
    from pmtb.kalshi.ws_client import KalshiWSClient

    settings = _make_settings()

    success_ws = _make_ws_mock(messages=[])

    call_count = [0]

    def connect_side_effect(url, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("connection refused")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=success_ws)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    with patch("pmtb.kalshi.ws_client.load_private_key", return_value=MagicMock()), \
         patch("pmtb.kalshi.ws_client.build_kalshi_headers", return_value={}), \
         patch("pmtb.kalshi.ws_client.websockets.connect", side_effect=connect_side_effect), \
         patch("pmtb.kalshi.ws_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        client = KalshiWSClient(settings)
        await client.run(AsyncMock(), channels=["orderbook_delta"], market_tickers=["T"])

        mock_sleep.assert_called_once_with(5)


# ---------------------------------------------------------------------------
# Test 6: subscription includes market_ticker parameter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscription_includes_market_tickers():
    """Subscription message includes the specified market_tickers."""
    from pmtb.kalshi.ws_client import KalshiWSClient

    settings = _make_settings()
    ws_mock = _make_ws_mock(messages=[])

    with patch("pmtb.kalshi.ws_client.load_private_key", return_value=MagicMock()), \
         patch("pmtb.kalshi.ws_client.build_kalshi_headers", return_value={}), \
         patch("pmtb.kalshi.ws_client.websockets.connect") as mock_connect:

        mock_connect.return_value.__aenter__ = AsyncMock(return_value=ws_mock)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        client = KalshiWSClient(settings)
        await client.run(AsyncMock(), channels=["fill"], market_tickers=["TICKER-A", "TICKER-B"])

        sent_payload = json.loads(ws_mock.send.call_args[0][0])
        assert "TICKER-A" in sent_payload["params"]["market_tickers"]
        assert "TICKER-B" in sent_payload["params"]["market_tickers"]


# ---------------------------------------------------------------------------
# Test 7: WS URL selected based on trading_mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ws_url_paper_mode():
    """In paper mode, uses demo WebSocket URL."""
    from pmtb.kalshi.ws_client import KalshiWSClient

    settings = _make_settings(trading_mode="paper")
    ws_mock = _make_ws_mock(messages=[])

    with patch("pmtb.kalshi.ws_client.load_private_key", return_value=MagicMock()), \
         patch("pmtb.kalshi.ws_client.build_kalshi_headers", return_value={}), \
         patch("pmtb.kalshi.ws_client.websockets.connect") as mock_connect:

        mock_connect.return_value.__aenter__ = AsyncMock(return_value=ws_mock)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        client = KalshiWSClient(settings)
        await client.run(AsyncMock(), channels=["orderbook_delta"], market_tickers=["T"])

        url_used = mock_connect.call_args[0][0]
        assert "demo" in url_used


@pytest.mark.asyncio
async def test_ws_url_live_mode():
    """In live mode, uses production WebSocket URL."""
    from pmtb.kalshi.ws_client import KalshiWSClient

    settings = _make_settings(trading_mode="live")
    ws_mock = _make_ws_mock(messages=[])

    with patch("pmtb.kalshi.ws_client.load_private_key", return_value=MagicMock()), \
         patch("pmtb.kalshi.ws_client.build_kalshi_headers", return_value={}), \
         patch("pmtb.kalshi.ws_client.websockets.connect") as mock_connect:

        mock_connect.return_value.__aenter__ = AsyncMock(return_value=ws_mock)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)

        client = KalshiWSClient(settings)
        await client.run(AsyncMock(), channels=["orderbook_delta"], market_tickers=["T"])

        url_used = mock_connect.call_args[0][0]
        assert "demo" not in url_used
        assert "api.elections.kalshi.com" in url_used
