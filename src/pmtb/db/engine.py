"""
SQLAlchemy async engine and session factory.

Usage:
    from pmtb.db.engine import create_engine_from_settings, create_session_factory
    from pmtb.config import Settings

    settings = Settings()
    engine = create_engine_from_settings(settings)
    AsyncSessionLocal = create_session_factory(engine)
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pmtb.config import Settings


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    """
    Create an async SQLAlchemy engine from application settings.

    Pool configuration:
        pool_size=5      — baseline connections kept open
        max_overflow=10  — additional connections allowed under load
        pool_pre_ping=True  — test connection health before use
        pool_recycle=300 — recycle connections every 5 minutes
    """
    return create_async_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=False,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """
    Create an async session factory bound to the given engine.

    expire_on_commit=False — loaded objects remain usable after commit
    (important for async contexts where lazy-load would fail post-commit).
    """
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
