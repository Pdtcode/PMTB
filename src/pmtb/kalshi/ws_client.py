"""
KalshiWSClient — WebSocket client with auto-reconnect.

Connects to Kalshi WSS endpoint with RSA-PSS signed headers, subscribes to
specified channels, routes incoming messages to a callback, and automatically
reconnects after any connection failure with a fixed 5-second delay.

Fixed 5-second retry (not exponential) — per architecture decision to keep
reconnect behavior simple and predictable for real-time data feeds.

Demo vs production URL is selected from Settings.trading_mode:
    "paper" -> kalshi_demo_ws_url
    "live"  -> kalshi_ws_url
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, List

import websockets
import websockets.exceptions
from loguru import logger

from pmtb.kalshi.auth import build_kalshi_headers, load_private_key

_WS_API_PATH = "/trade-api/ws/v2"
_RECONNECT_DELAY = 5  # seconds — fixed, not exponential


class KalshiWSClient:
    """
    Kalshi WebSocket client with automatic reconnection.

    Generates fresh RSA-PSS signed headers on every connection attempt.
    Subscribes to specified channels after each successful connect.
    Routes all incoming messages (JSON-parsed) to the on_message callback.
    Reconnects with a fixed 5-second delay on ConnectionClosed or OSError.

    Args:
        settings: Application Settings instance. Used for:
            - trading_mode (selects demo vs production WS URL)
            - kalshi_api_key_id
            - kalshi_private_key_path
            - kalshi_ws_url / kalshi_demo_ws_url
    """

    def __init__(self, settings) -> None:
        self._api_key_id: str = settings.kalshi_api_key_id
        self._private_key = load_private_key(settings.kalshi_private_key_path)
        self._trading_mode: str = settings.trading_mode
        self._ws_url_live: str = settings.kalshi_ws_url
        self._ws_url_demo: str = settings.kalshi_demo_ws_url

    @property
    def _ws_url(self) -> str:
        """Return the appropriate WebSocket URL based on trading mode."""
        if self._trading_mode == "live":
            return self._ws_url_live
        return self._ws_url_demo

    async def subscribe(
        self,
        ws,
        channels: List[str],
        market_tickers: List[str],
    ) -> None:
        """
        Send a subscription command to the WebSocket server.

        Args:
            ws:             Active WebSocket connection.
            channels:       List of channel names (e.g. ["orderbook_delta", "fill"]).
            market_tickers: List of market ticker strings to subscribe to.
        """
        payload = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": channels,
                "market_tickers": market_tickers,
            },
        }
        await ws.send(json.dumps(payload))
        logger.debug("Subscribed to channels", channels=channels, tickers=market_tickers)

    async def unsubscribe(
        self,
        ws,
        channels: List[str],
        market_tickers: List[str],
    ) -> None:
        """
        Send an unsubscribe command to the WebSocket server.

        Args:
            ws:             Active WebSocket connection.
            channels:       List of channel names to unsubscribe.
            market_tickers: List of market ticker strings to unsubscribe.
        """
        payload = {
            "id": 2,
            "cmd": "unsubscribe",
            "params": {
                "channels": channels,
                "market_tickers": market_tickers,
            },
        }
        await ws.send(json.dumps(payload))
        logger.debug("Unsubscribed from channels", channels=channels, tickers=market_tickers)

    async def run(
        self,
        on_message: Callable,
        channels: List[str] | None = None,
        market_tickers: List[str] | None = None,
    ) -> None:
        """
        Connect to Kalshi WebSocket and run message loop with auto-reconnect.

        Generates fresh signed headers on every connection attempt.
        Subscribes to channels after connect.
        Passes each received message (JSON-parsed dict) to on_message.
        On ConnectionClosed or OSError: logs the error, sleeps 5 seconds, reconnects.

        Args:
            on_message:      Async callable receiving parsed message dicts.
            channels:        Channels to subscribe to (default: ["orderbook_delta", "fill"]).
            market_tickers:  Market tickers to subscribe to (default: []).
        """
        if channels is None:
            channels = ["orderbook_delta", "fill"]
        if market_tickers is None:
            market_tickers = []

        while True:
            try:
                # Build fresh headers on every connection attempt
                headers = build_kalshi_headers(
                    method="GET",
                    path=_WS_API_PATH,
                    private_key=self._private_key,
                    api_key_id=self._api_key_id,
                )
                logger.info("Connecting to Kalshi WebSocket", url=self._ws_url)

                async with websockets.connect(
                    self._ws_url,
                    additional_headers=headers,
                ) as ws:
                    await self.subscribe(ws, channels, market_tickers)
                    logger.info("Kalshi WebSocket connected and subscribed")

                    async for raw_message in ws:
                        parsed = json.loads(raw_message)
                        await on_message(parsed)

            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                logger.warning(
                    "Kalshi WebSocket disconnected, reconnecting in 5s",
                    error=str(exc),
                )
                await asyncio.sleep(_RECONNECT_DELAY)


async def run_ws_client(
    settings,
    on_message: Callable,
    channels: List[str] | None = None,
    market_tickers: List[str] | None = None,
) -> None:
    """
    Convenience function: create KalshiWSClient and run it.

    Args:
        settings:        Application Settings instance.
        on_message:      Async callable receiving parsed message dicts.
        channels:        Channels to subscribe to.
        market_tickers:  Market tickers to subscribe to.
    """
    client = KalshiWSClient(settings)
    await client.run(on_message, channels=channels, market_tickers=market_tickers)
