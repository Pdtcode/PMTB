"""
Shared test fixtures for the PMTB test suite.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def minimal_env(tmp_path, monkeypatch):
    """
    Provide minimal environment for Settings instantiation.
    Writes a .env file with required fields and points Settings to it.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://localhost:5432/pmtb_test\n"
        "KALSHI_API_KEY_ID=test-key-id\n"
        "KALSHI_PRIVATE_KEY_PATH=/tmp/test_key.pem\n"
    )
    return str(env_file)
