"""
Tests for KalshiClient — authenticated REST client using httpx with RSA-PSS headers.

All tests use mock httpx responses — no real API calls.
"""
from __future__ import annotations

import json
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from pmtb.kalshi.client import KalshiClient


@pytest.fixture
def rsa_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def private_key_path(rsa_private_key, tmp_path):
    pem = rsa_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file = tmp_path / "test_key.pem"
    key_file.write_bytes(pem)
    return str(key_file)


@pytest.fixture
def paper_settings(private_key_path):
    """Settings-like object for paper trading mode."""
    s = MagicMock()
    s.trading_mode = "paper"
    s.kalshi_api_key_id = "test-api-key-id"
    s.kalshi_private_key_path = private_key_path
    s.kalshi_base_url = "https://api.elections.kalshi.com"
    s.kalshi_demo_base_url = "https://demo-api.kalshi.co"
    return s


@pytest.fixture
def live_settings(private_key_path):
    """Settings-like object for live trading mode."""
    s = MagicMock()
    s.trading_mode = "live"
    s.kalshi_api_key_id = "test-api-key-id"
    s.kalshi_private_key_path = private_key_path
    s.kalshi_base_url = "https://api.elections.kalshi.com"
    s.kalshi_demo_base_url = "https://demo-api.kalshi.co"
    return s


def make_response(data, status_code=200):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


class TestKalshiClientConstruction:
    def test_constructs_with_settings_and_loads_key(self, paper_settings):
        """Test 1: KalshiClient constructs with Settings and loads private key."""
        client = KalshiClient(paper_settings)
        assert client is not None
        assert client._api_key_id == "test-api-key-id"
        assert client._private_key is not None

    def test_uses_demo_url_for_paper_mode(self, paper_settings):
        """Test 8: Client uses demo URL when trading_mode is 'paper'."""
        client = KalshiClient(paper_settings)
        assert "demo" in client._base_url

    def test_uses_production_url_for_live_mode(self, live_settings):
        """Test 9: Client uses production URL when trading_mode is 'live'."""
        client = KalshiClient(live_settings)
        assert "demo" not in client._base_url
        assert "api.elections.kalshi.com" in client._base_url


class TestKalshiClientMethods:
    @pytest.mark.asyncio
    async def test_get_markets_returns_list(self, paper_settings):
        """Test 2: get_markets calls endpoint and returns list of market dicts."""
        client = KalshiClient(paper_settings)
        mock_resp = make_response({"markets": [{"ticker": "AAPL-2024"}, {"ticker": "BTC-USD"}]})

        with patch.object(client._http, "request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_resp
            result = await client.get_markets()

        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_balance_returns_dict(self, paper_settings):
        """Test 3: get_balance calls endpoint and returns balance dict."""
        client = KalshiClient(paper_settings)
        mock_resp = make_response({"balance": 10000})

        with patch.object(client._http, "request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_resp
            result = await client.get_balance()

        assert isinstance(result, dict)
        assert "balance" in result

    @pytest.mark.asyncio
    async def test_get_positions_returns_list(self, paper_settings):
        """Test 4: get_positions calls portfolio positions endpoint."""
        client = KalshiClient(paper_settings)
        mock_resp = make_response({"market_positions": [{"market_id": "abc"}]})

        with patch.object(client._http, "request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_resp
            result = await client.get_positions()

        assert isinstance(result, list)
        mock_req.assert_called_once()
        call_args = mock_req.call_args
        assert "positions" in call_args[1]["url"] or "positions" in str(call_args)

    @pytest.mark.asyncio
    async def test_get_orders_returns_list(self, paper_settings):
        """Test 5: get_orders calls portfolio orders endpoint."""
        client = KalshiClient(paper_settings)
        mock_resp = make_response({"orders": [{"order_id": "ord-1"}]})

        with patch.object(client._http, "request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_resp
            result = await client.get_orders()

        assert isinstance(result, list)
        call_args = mock_req.call_args
        assert "orders" in call_args[1]["url"] or "orders" in str(call_args)

    @pytest.mark.asyncio
    async def test_place_order_sends_correct_params(self, paper_settings):
        """Test 6: place_order sends order request with correct parameters."""
        client = KalshiClient(paper_settings)
        mock_resp = make_response({"order": {"order_id": "ord-123", "status": "resting"}})

        with patch.object(client._http, "request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_resp
            result = await client.place_order(
                market_ticker="AAPL-Q1", side="yes", quantity=10, price=55
            )

        assert "order" in result or isinstance(result, dict)
        call_args = mock_req.call_args
        # Verify POST method used
        assert call_args[0][0] == "POST" or call_args[1].get("method") == "POST" or "POST" in str(call_args)

    @pytest.mark.asyncio
    async def test_cancel_order_sends_delete_request(self, paper_settings):
        """Test 7: cancel_order sends DELETE request for given order_id."""
        client = KalshiClient(paper_settings)
        mock_resp = make_response({"order": {"order_id": "ord-123", "status": "canceled"}})

        with patch.object(client._http, "request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_resp
            result = await client.cancel_order("ord-123")

        assert isinstance(result, dict)
        call_args = mock_req.call_args
        assert "ord-123" in str(call_args)
        assert "DELETE" in str(call_args) or call_args[0][0] == "DELETE"
