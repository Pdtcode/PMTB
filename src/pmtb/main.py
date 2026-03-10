"""
Application entry point for PMTB.

Wires all components together:
- Settings (pydantic-settings) — load and validate config
- Logging (loguru) — configure before any log statements
- Database — create async engine and session factory
- KalshiClient — authenticated REST client
- Executor — paper or live order executor via factory
- Reconciler — resolve position discrepancies on startup
- Metrics — Prometheus HTTP server

Starts in paper mode by default (safe default from Settings).
Phase 2 will add the actual market scan loop.

Usage:
    uv run python -m pmtb.main
    # or via entry point:
    uv run pmtb
"""
from __future__ import annotations

import asyncio
import signal

from loguru import logger


async def main() -> None:
    """
    PMTB application startup and lifecycle management.

    Loads config, wires all subsystems, runs reconciliation, and then
    waits for shutdown signal (SIGINT/SIGTERM or KeyboardInterrupt).
    """
    # Import here to keep startup imports explicit and ordered
    from pmtb.config import Settings
    from pmtb.logging_ import configure_logging
    from pmtb.db.engine import create_engine_from_settings, create_session_factory
    from pmtb.kalshi.client import KalshiClient
    from pmtb.executor import create_executor
    from pmtb.reconciler import reconcile_positions
    from pmtb.metrics import start_metrics_server

    # --- Load and validate configuration ---
    # Fails fast on missing required fields (DATABASE_URL, KALSHI_API_KEY_ID, etc.)
    settings = Settings()

    # --- Configure logging before any log statements ---
    configure_logging(settings)

    # Mask sensitive fields in startup log
    masked_db_url = settings.database_url.split("@")[-1] if "@" in settings.database_url else "***"
    logger.info(
        "PMTB starting up",
        version="0.1.0",
        trading_mode=settings.trading_mode,
        database_host=masked_db_url,
        log_level=settings.log_level,
    )

    # --- Create database engine and session factory ---
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    # --- Create Kalshi client ---
    kalshi_client = KalshiClient(settings)

    # --- Create order executor (paper or live based on trading_mode) ---
    executor = create_executor(settings, kalshi_client)
    logger.info(
        "Executor created",
        executor_type=type(executor).__name__,
        trading_mode=settings.trading_mode,
    )

    # --- Run position reconciliation ---
    # Does not fail startup on reconciliation errors — logs warning and continues.
    try:
        recon_result = await reconcile_positions(kalshi_client, session_factory)
        logger.info(
            "Startup reconciliation complete",
            orphaned_orders=recon_result.orphaned_orders,
            new_orders=recon_result.new_orders,
            updated_orders=recon_result.updated_orders,
            new_positions=recon_result.new_positions,
            closed_positions=recon_result.closed_positions,
        )
    except Exception as exc:
        logger.warning(
            "Position reconciliation failed — continuing without reconciliation",
            error=str(exc),
        )

    # --- Start Prometheus metrics server ---
    metrics_port = 9090
    start_metrics_server(port=metrics_port)
    logger.info("Metrics server started", port=metrics_port)

    # --- Application started ---
    logger.info(
        "PMTB started",
        trading_mode=settings.trading_mode,
        note="Phase 2 will add market scan loop",
    )

    # --- Wait for shutdown ---
    # Phase 2 will replace this placeholder with the actual scan loop.
    stop_event = asyncio.Event()

    def _signal_handler(sig, frame) -> None:
        logger.info("Shutdown signal received", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("PMTB running — press Ctrl+C or send SIGTERM to stop")

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass

    logger.info("PMTB shutting down cleanly")
    await engine.dispose()
    logger.info("PMTB stopped")


if __name__ == "__main__":
    asyncio.run(main())
