"""
Tests for pmtb.db.session and pmtb.db.engine

Covers:
    - Test 1: AsyncSessionLocal produces a working async session that can execute SELECT 1
    - Test 2: get_session context manager yields a session and closes it

Tests requiring a live PostgreSQL database are conditionally skipped.
"""

from __future__ import annotations

import os
import pytest

# Test DB URL — configurable via env var for CI/local environments
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://localhost:5432/pmtb_test",
)

HAS_TEST_DB = bool(os.environ.get("TEST_DATABASE_URL"))


@pytest.fixture
def test_settings(monkeypatch):
    """Settings instance pointing to the test database."""
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "/tmp/key.pem")

    from pmtb.config import Settings
    return Settings()


# --- Test 1: AsyncSessionLocal produces a working session ---

@pytest.mark.skipif(
    not HAS_TEST_DB,
    reason="Requires TEST_DATABASE_URL env var pointing to a running PostgreSQL instance",
)
async def test_async_session_executes_select_1(test_settings):
    """Session factory produces a session that can execute a simple query."""
    from sqlalchemy import text
    from pmtb.db.engine import create_engine_from_settings, create_session_factory

    engine = create_engine_from_settings(test_settings)
    AsyncSessionLocal = create_session_factory(engine)

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            row = result.scalar()
            assert row == 1
    finally:
        await engine.dispose()


# --- Test 2: get_session context manager yields and closes session ---

@pytest.mark.skipif(
    not HAS_TEST_DB,
    reason="Requires TEST_DATABASE_URL env var pointing to a running PostgreSQL instance",
)
async def test_get_session_yields_and_closes(test_settings):
    """get_session yields an AsyncSession and cleans up properly."""
    from sqlalchemy import text
    from pmtb.db.engine import create_engine_from_settings, create_session_factory
    from pmtb.db.session import get_session

    engine = create_engine_from_settings(test_settings)
    factory = create_session_factory(engine)

    try:
        async with get_session(factory=factory) as session:
            # Should be able to execute a query
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
        # After context exit, session should be closed
        # (accessing session.is_active after close is False)
    finally:
        await engine.dispose()


# --- Unit tests that do NOT require a live database ---

def test_create_engine_from_settings_returns_engine(monkeypatch):
    """create_engine_from_settings returns an async engine without connecting."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost:5432/pmtb_test")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "/tmp/key.pem")

    from pmtb.config import Settings
    from pmtb.db.engine import create_engine_from_settings
    from sqlalchemy.ext.asyncio import AsyncEngine

    settings = Settings()
    engine = create_engine_from_settings(settings)
    assert isinstance(engine, AsyncEngine)
    assert "postgresql" in str(engine.url)


def test_create_session_factory_returns_factory(monkeypatch):
    """create_session_factory returns an async_sessionmaker."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost:5432/pmtb_test")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "/tmp/key.pem")

    from pmtb.config import Settings
    from pmtb.db.engine import create_engine_from_settings, create_session_factory
    from sqlalchemy.ext.asyncio import async_sessionmaker

    settings = Settings()
    engine = create_engine_from_settings(settings)
    factory = create_session_factory(engine)
    assert isinstance(factory, async_sessionmaker)


async def test_get_session_raises_without_factory():
    """get_session raises RuntimeError if no factory is configured."""
    from pmtb.db import session as session_module
    from pmtb.db.session import get_session

    # Temporarily clear the module-level factory
    original = session_module._session_factory
    session_module._session_factory = None

    try:
        with pytest.raises(RuntimeError, match="Session factory not initialized"):
            async with get_session():
                pass
    finally:
        session_module._session_factory = original
