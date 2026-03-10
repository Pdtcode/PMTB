"""
MarketScanner — core market scanning loop for PMTB.

Orchestrates the full pipeline per cycle:
    1. Fetch all active markets via cursor-based pagination (_request directly)
    2. Upsert all discovered markets to the DB (regardless of filter outcome)
    3. Apply the five-filter chain (liquidity, volume, spread, TTR, volatility)
    4. Enrich passing candidates with orderbook + event context
    5. Return a typed ScanResult sorted by edge potential

run_cycle() executes one complete scan.
run_forever() wraps run_cycle() in an infinite async loop with configurable sleep.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pmtb.db.models import Market
from pmtb.db.session import get_session
from pmtb.scanner.filters import (
    VolatilityTracker,
    filter_liquidity,
    filter_spread,
    filter_ttr,
    filter_volatility,
    filter_volume,
    parse_close_time,
)
from pmtb.scanner.models import MarketCandidate, ScanResult

# Kalshi pagination endpoint
_MARKETS_PATH = "/trade-api/v2/markets"
_ORDERBOOK_PATH = "/trade-api/v2/markets/{ticker}/orderbook"
_EVENT_PATH = "/trade-api/v2/events/{event_ticker}"


class MarketScanner:
    """
    Core market scanner that produces MarketCandidate objects for the downstream
    signal evaluation and trading pipeline.

    Constructor:
        client:          KalshiClient instance for REST API access.
        settings:        Settings instance (scanner_* threshold fields expected).
        session_factory: Optional SQLAlchemy async session factory. If None,
                         the module-level factory from db.session is used.
    """

    def __init__(self, client, settings, session_factory=None) -> None:
        self._client = client
        self._settings = settings
        self._session_factory = session_factory
        self._volatility_tracker = VolatilityTracker()

    # ------------------------------------------------------------------
    # Private: pagination
    # ------------------------------------------------------------------

    async def _fetch_all_markets(self) -> list[dict]:
        """
        Fetch ALL active Kalshi markets via cursor-based pagination.

        Uses _request() directly (not get_markets()) so we have access to the
        cursor field — get_markets() strips it from the response.

        Returns:
            Flat list of all market dicts across all pages.
        """
        all_markets: list[dict] = []
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {"status": "active", "limit": 1000}
            if cursor:
                params["cursor"] = cursor

            data = await self._client._request("GET", _MARKETS_PATH, params=params)
            page_markets = data.get("markets", [])
            all_markets.extend(page_markets)

            cursor = data.get("cursor") or None
            if not cursor:
                break

        logger.info(
            "Fetched all markets from Kalshi",
            total=len(all_markets),
        )
        return all_markets

    # ------------------------------------------------------------------
    # Private: DB upsert
    # ------------------------------------------------------------------

    async def _upsert_markets(self, markets: list[dict]) -> None:
        """
        Upsert ALL fetched markets into the DB markets table.

        Uses PostgreSQL INSERT ... ON CONFLICT DO UPDATE to handle existing rows.
        Commits explicitly — AsyncSession does NOT auto-commit.

        Args:
            markets: Raw Kalshi market dicts.
        """
        if not markets:
            return

        now = datetime.now(timezone.utc)
        rows = []
        for m in markets:
            raw_ct = m.get("close_time", "")
            try:
                close_time = parse_close_time(raw_ct) if raw_ct else now
            except (ValueError, TypeError):
                close_time = now

            rows.append(
                {
                    "ticker": m.get("ticker", ""),
                    "title": m.get("title", ""),
                    "category": m.get("category", ""),
                    "status": m.get("status", "active"),
                    "close_time": close_time,
                    "updated_at": now,
                }
            )

        stmt = pg_insert(Market).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker"],
            set_={
                "title": stmt.excluded.title,
                "category": stmt.excluded.category,
                "status": stmt.excluded.status,
                "close_time": stmt.excluded.close_time,
                "updated_at": stmt.excluded.updated_at,
            },
        )

        async with get_session(self._session_factory) as session:
            await session.execute(stmt)
            await session.commit()

    # ------------------------------------------------------------------
    # Private: filter chain
    # ------------------------------------------------------------------

    def _apply_filters(
        self, markets: list[dict]
    ) -> tuple[list[dict], dict[str, int]]:
        """
        Apply the five-filter chain sequentially.

        Per-market rejection reasons are logged at DEBUG level.

        Returns:
            (passing_markets, rejection_counts_dict)
        """
        s = self._settings

        # Track which markets pass each stage to infer per-market rejections
        def log_rejections(before: list[dict], after: list[dict], reason: str) -> None:
            passing_tickers = {m["ticker"] for m in after}
            for m in before:
                if m["ticker"] not in passing_tickers:
                    logger.bind(ticker=m["ticker"]).debug(
                        "Market rejected by filter", reason=reason
                    )

        after_liquidity, rej_liq = filter_liquidity(markets, s.scanner_min_open_interest)
        log_rejections(markets, after_liquidity, "liquidity")

        after_volume, rej_vol = filter_volume(after_liquidity, s.scanner_min_volume_24h)
        log_rejections(after_liquidity, after_volume, "volume")

        after_spread, rej_spread = filter_spread(after_volume, s.scanner_max_spread)
        log_rejections(after_volume, after_spread, "spread")

        after_ttr, rej_ttr = filter_ttr(
            after_spread, s.scanner_min_ttr_hours, s.scanner_max_ttr_days
        )
        log_rejections(after_spread, after_ttr, "ttr")

        after_volatility, rej_vol_filter = filter_volatility(
            after_ttr,
            s.scanner_min_volatility,
            self._volatility_tracker,
            s.scanner_volatility_warmup,
        )
        log_rejections(after_ttr, after_volatility, "volatility")

        return after_volatility, {
            "rejected_liquidity": rej_liq,
            "rejected_volume": rej_vol,
            "rejected_spread": rej_spread,
            "rejected_ttr": rej_ttr,
            "rejected_volatility": rej_vol_filter,
        }

    # ------------------------------------------------------------------
    # Private: enrichment
    # ------------------------------------------------------------------

    async def _enrich(self, markets: list[dict]) -> list[MarketCandidate]:
        """
        Enrich each passing market with orderbook snapshot and event context.

        Concurrency is limited by scanner_enrichment_concurrency semaphore.
        Empty orderbooks are skipped gracefully.

        Returns:
            List of MarketCandidate objects sorted by |implied_probability - 0.5|
            ascending (closest to 50% edge first).
        """
        semaphore = asyncio.Semaphore(self._settings.scanner_enrichment_concurrency)

        async def _enrich_one(market: dict) -> MarketCandidate | None:
            ticker = market["ticker"]
            event_ticker = market.get("event_ticker", "")

            async with semaphore:
                ob_data, event_data = await asyncio.gather(
                    self._client._request(
                        "GET",
                        _ORDERBOOK_PATH.format(ticker=ticker),
                        params={"depth": 3},
                    ),
                    self._client._request(
                        "GET",
                        _EVENT_PATH.format(event_ticker=event_ticker),
                    ),
                )

            # Parse orderbook
            ob = ob_data.get("orderbook_fp", {})
            yes_dollars = ob.get("yes_dollars", [])
            no_dollars = ob.get("no_dollars", [])

            if not yes_dollars and not no_dollars:
                # Empty orderbook — skip gracefully
                logger.bind(ticker=ticker).debug("Skipping candidate: empty orderbook")
                return None

            yes_bid = float(yes_dollars[0][0]) if yes_dollars else 0.0
            yes_ask = (1.0 - float(no_dollars[0][0])) if no_dollars else 1.0

            implied_probability = (yes_bid + yes_ask) / 2.0
            spread = yes_ask - yes_bid

            volume_24h = float(market.get("volume_24h_fp", "0"))

            # Volatility: record current yes_bid and get score
            volatility_score = self._volatility_tracker.record_and_get(
                ticker,
                yes_bid,
                warmup=self._settings.scanner_volatility_warmup,
            )

            # Event context
            event = event_data.get("event", {})
            event_context = {
                "title": event.get("title", ""),
                "event_ticker": event.get("event_ticker", event_ticker),
            }

            raw_ct = market.get("close_time", "")
            try:
                close_time = parse_close_time(raw_ct) if raw_ct else datetime.now(timezone.utc)
            except (ValueError, TypeError):
                close_time = datetime.now(timezone.utc)

            return MarketCandidate(
                ticker=ticker,
                title=market.get("title", ""),
                category=market.get("category", ""),
                event_context=event_context,
                close_time=close_time,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                implied_probability=implied_probability,
                spread=spread,
                volume_24h=volume_24h,
                volatility_score=volatility_score,
            )

        tasks = [_enrich_one(m) for m in markets]
        results = await asyncio.gather(*tasks)
        candidates = [c for c in results if c is not None]

        # Sort by distance from 50% implied probability (ascending = best edge first)
        candidates.sort(key=lambda c: abs(c.implied_probability - 0.5))
        return candidates

    # ------------------------------------------------------------------
    # Public: run_cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> ScanResult:
        """
        Execute a complete scan cycle.

        Steps:
            1. Fetch all active markets (paginated)
            2. Upsert all markets to DB
            3. Apply five-filter chain
            4. Enrich passing candidates
            5. Return ScanResult with metadata

        Returns:
            ScanResult with candidates list and per-filter rejection counts.
        """
        cycle_id = str(uuid.uuid4())
        start_time = datetime.now(timezone.utc)

        logger.info("Starting scan cycle", cycle_id=cycle_id)

        markets = await self._fetch_all_markets()
        total_markets = len(markets)

        await self._upsert_markets(markets)

        passing, rejection_counts = self._apply_filters(markets)

        candidates = await self._enrich(passing)

        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        result = ScanResult(
            candidates=candidates,
            total_markets=total_markets,
            rejected_liquidity=rejection_counts["rejected_liquidity"],
            rejected_volume=rejection_counts["rejected_volume"],
            rejected_spread=rejection_counts["rejected_spread"],
            rejected_ttr=rejection_counts["rejected_ttr"],
            rejected_volatility=rejection_counts["rejected_volatility"],
            scan_duration_seconds=duration,
            cycle_id=cycle_id,
        )

        logger.info(
            "Scan cycle complete",
            cycle_id=cycle_id,
            total_markets=total_markets,
            candidates=len(candidates),
            rejected_liquidity=rejection_counts["rejected_liquidity"],
            rejected_volume=rejection_counts["rejected_volume"],
            rejected_spread=rejection_counts["rejected_spread"],
            rejected_ttr=rejection_counts["rejected_ttr"],
            rejected_volatility=rejection_counts["rejected_volatility"],
            duration_seconds=round(duration, 2),
        )

        return result

    # ------------------------------------------------------------------
    # Public: run_forever
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """
        Infinite scan loop: run_cycle() → sleep → repeat.

        Errors in run_cycle() are caught and logged — the loop continues.
        Sleep duration is controlled by settings.scan_interval_seconds.
        """
        while True:
            try:
                result = await self.run_cycle()
                logger.info(
                    "Cycle done, sleeping",
                    candidates=len(result.candidates),
                    sleep_seconds=self._settings.scan_interval_seconds,
                )
            except Exception as exc:
                logger.exception(
                    "Error in scan cycle — continuing",
                    error=str(exc),
                )

            await asyncio.sleep(self._settings.scan_interval_seconds)
