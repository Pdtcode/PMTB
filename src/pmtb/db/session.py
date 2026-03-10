"""
Session context manager for PMTB database access.

Provides get_session() as an async context manager that yields an AsyncSession
and ensures the session is properly closed after use.

Usage:
    from pmtb.db.session import get_session

    async with get_session() as session:
        result = await session.execute(select(Market))
        markets = result.scalars().all()
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

# Module-level session factory — set by application startup
# Import and call setup_session_factory() in main.py before using get_session()
_session_factory = None


def setup_session_factory(factory) -> None:
    """
    Register the session factory. Call this at application startup.

    Example:
        from pmtb.db.engine import create_engine_from_settings, create_session_factory
        setup_session_factory(create_session_factory(create_engine_from_settings(settings)))
    """
    global _session_factory
    _session_factory = factory


@asynccontextmanager
async def get_session(
    factory=None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that yields an AsyncSession.

    Always use as `async with get_session() as session:` — never call
    session.close() manually; the context manager handles cleanup.

    Args:
        factory: Optional session factory override (useful for testing).
                 If None, uses the module-level factory set by setup_session_factory().
    """
    session_factory = factory or _session_factory
    if session_factory is None:
        raise RuntimeError(
            "Session factory not initialized. "
            "Call setup_session_factory() before using get_session()."
        )

    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
