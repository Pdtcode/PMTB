"""
Loguru-based structured logging configuration for PMTB.

Usage:
    from pmtb.logging_ import configure_logging, logger
    from pmtb.config import Settings

    settings = Settings()
    configure_logging(settings)

    # Bind correlation IDs for tracing
    cycle_logger = logger.bind(cycle_id="cycle-abc123")
    cycle_logger.info("Scan started", markets_checked=42)
"""

from __future__ import annotations

import sys

from loguru import logger

# Re-export logger for use throughout the codebase
# Import: `from pmtb.logging_ import logger`
__all__ = ["configure_logging", "logger"]


def configure_logging(settings) -> None:
    """
    Configure loguru sinks for PMTB.

    Adds two sinks:
        1. JSON stdout — for Docker/cloud log ingestion (structured, machine-readable)
        2. Rotating file — for local dev debugging (human-readable format)

    Args:
        settings: Settings instance providing log_level.
    """
    # Remove all default handlers (loguru adds a stderr handler by default)
    logger.remove()

    # Sink 1: JSON to stdout for Docker/cloud ingestion
    # serialize=True outputs each log record as a JSON object on a single line
    logger.add(
        sys.stdout,
        serialize=True,
        level=settings.log_level,
        enqueue=True,  # Thread-safe async logging
    )

    # Sink 2: Human-readable rotating file for local dev debugging
    # Rotates at 100 MB, keeps 7 days of history
    logger.add(
        "logs/pmtb_{time:YYYY-MM-DD}.log",
        rotation="100 MB",
        retention="7 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} | {message}",
        enqueue=True,  # Thread-safe async logging
    )
