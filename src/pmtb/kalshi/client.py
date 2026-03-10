"""
KalshiClient — authenticated REST client for Kalshi API.

Uses httpx.AsyncClient with manual RSA-PSS header signing per request.
(kalshi-python-async SDK not used directly — it requires urllib3 which is
incompatible with the project's httpx-based async approach.)

Every request generates fresh auth headers via build_kalshi_headers.
Headers are NEVER cached — Kalshi's timestamp validation rejects stale headers.

All methods are decorated with @kalshi_retry for automatic retries on
429/5xx responses with exponential backoff + jitter.

Demo vs production URL is selected from Settings.trading_mode:
    "paper" -> kalshi_demo_base_url
    "live"  -> kalshi_base_url
"""
from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from pmtb.kalshi.auth import build_kalshi_headers, load_private_key
from pmtb.kalshi.errors import KalshiClientError, classify_error, kalshi_retry
from pmtb.metrics import API_CALLS

# Kalshi REST API v2 base path
_API_PATH_PREFIX = "/trade-api/v2"


class KalshiClient:
    """
    Authenticated Kalshi REST client.

    Args:
        settings: Application Settings instance. Used for:
            - trading_mode (selects demo vs production URL)
            - kalshi_api_key_id
            - kalshi_private_key_path
            - kalshi_base_url / kalshi_demo_base_url

    Example:
        client = KalshiClient(settings)
        markets = await client.get_markets(status="open")
        balance = await client.get_balance()
    """

    def __init__(self, settings) -> None:
        self._api_key_id: str = settings.kalshi_api_key_id
        self._private_key = load_private_key(settings.kalshi_private_key_path)

        # Select base URL based on trading mode
        if settings.trading_mode == "live":
            self._base_url: str = settings.kalshi_base_url
        else:
            self._base_url = settings.kalshi_demo_base_url

        self._http = httpx.AsyncClient(base_url=self._base_url)

    def _headers(self, method: str, path: str) -> dict:
        """
        Build fresh RSA-PSS auth headers for a request.

        Called on every request — never cached.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            path:   Request path including any query string.

        Returns:
            Dict of auth headers.
        """
        return build_kalshi_headers(
            method=method,
            path=path,
            private_key=self._private_key,
            api_key_id=self._api_key_id,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> Any:
        """
        Execute an authenticated REST request.

        Builds fresh headers, sends request, classifies errors,
        tracks Prometheus metrics, and returns parsed JSON.

        Args:
            method: HTTP method.
            path:   API path (e.g. "/trade-api/v2/markets").
            params: URL query parameters.
            json:   Request body as dict (for POST/PUT).

        Returns:
            Parsed JSON response body.

        Raises:
            KalshiRateLimitError: on 429 responses (retriable).
            KalshiServerError:    on 5xx responses (retriable).
            KalshiClientError:    on 4xx non-429 responses (not retriable).
        """
        headers = self._headers(method, path)
        bound_logger = logger.bind(endpoint=path, method=method)

        resp = await self._http.request(
            method,
            url=path,
            headers=headers,
            params=params,
            json=json,
        )

        status = resp.status_code
        status_label = str(status)

        API_CALLS.labels(endpoint=path, status=status_label).inc()

        if status >= 400:
            bound_logger.warning("Kalshi API error", status_code=status)
            raise classify_error(status, resp.text)

        bound_logger.debug("Kalshi API success", status_code=status)
        return resp.json()

    @kalshi_retry
    async def get_markets(self, **kwargs) -> list:
        """
        Fetch available markets.

        Args:
            **kwargs: Optional filters forwarded as query params
                      (e.g. status="open", limit=100).

        Returns:
            List of market dicts.
        """
        data = await self._request("GET", f"{_API_PATH_PREFIX}/markets", params=kwargs or None)
        return data.get("markets", data) if isinstance(data, dict) else data

    @kalshi_retry
    async def get_market(self, ticker: str) -> dict:
        """
        Fetch a single market by ticker.

        Args:
            ticker: Market ticker symbol.

        Returns:
            Market dict.
        """
        data = await self._request("GET", f"{_API_PATH_PREFIX}/markets/{ticker}")
        return data.get("market", data) if isinstance(data, dict) else data

    @kalshi_retry
    async def get_balance(self) -> dict:
        """
        Fetch current portfolio balance.

        Returns:
            Balance dict (includes 'balance' field in cents).
        """
        return await self._request("GET", f"{_API_PATH_PREFIX}/portfolio/balance")

    @kalshi_retry
    async def get_positions(self) -> list:
        """
        Fetch current open positions.

        Returns:
            List of position dicts.
        """
        data = await self._request("GET", f"{_API_PATH_PREFIX}/portfolio/positions")
        return data.get("market_positions", data) if isinstance(data, dict) else data

    @kalshi_retry
    async def get_orders(self, status: str | None = None) -> list:
        """
        Fetch orders, optionally filtered by status.

        Args:
            status: Optional order status filter (e.g. "resting", "executed").

        Returns:
            List of order dicts.
        """
        params = {"status": status} if status else None
        data = await self._request("GET", f"{_API_PATH_PREFIX}/portfolio/orders", params=params)
        return data.get("orders", data) if isinstance(data, dict) else data

    @kalshi_retry
    async def place_order(
        self,
        market_ticker: str,
        side: str,
        quantity: int,
        price: int,
        order_type: str = "limit",
    ) -> dict:
        """
        Place a new order.

        Args:
            market_ticker: Market ticker symbol.
            side:          "yes" or "no".
            quantity:      Number of contracts.
            price:         Limit price in cents (0-99).
            order_type:    "limit" (default) or "market".

        Returns:
            Created order dict.
        """
        body = {
            "ticker": market_ticker,
            "side": side,
            "count": quantity,
            "yes_price": price if side == "yes" else (100 - price),
            "no_price": price if side == "no" else (100 - price),
            "type": order_type,
            "action": "buy",
        }
        return await self._request("POST", f"{_API_PATH_PREFIX}/portfolio/orders", json=body)

    @kalshi_retry
    async def cancel_order(self, order_id: str) -> dict:
        """
        Cancel an existing order.

        Args:
            order_id: The order UUID to cancel.

        Returns:
            Canceled order dict.
        """
        return await self._request(
            "DELETE", f"{_API_PATH_PREFIX}/portfolio/orders/{order_id}"
        )
