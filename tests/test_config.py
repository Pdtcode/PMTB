"""
Tests for pmtb.config.Settings

Covers:
    - Test 1: Settings loads database_url from .env file
    - Test 2: Settings loads edge_threshold from config.yaml
    - Test 3: Environment variable overrides .env value
    - Test 4: TRADING_MODE defaults to "paper" and rejects invalid values
    - Test 5: Missing required field (database_url) raises ValidationError at startup
    - Test 6: CLI --paper flag concept: TRADING_MODE=paper is valid
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_settings import YamlConfigSettingsSource

from pmtb.config import Settings


def build_settings_class(env_file: str, yaml_file: str):
    """
    Create a Settings subclass that points to specific temp files.
    This avoids relying on init kwargs (not supported in pydantic-settings v2).
    """
    from pydantic_settings import SettingsConfigDict, PydanticBaseSettingsSource
    from typing import Tuple, Type

    class TestSettings(Settings):
        model_config = SettingsConfigDict(
            env_file=env_file,
            env_file_encoding="utf-8",
            yaml_file=yaml_file,
        )

        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: Type[Settings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> Tuple[PydanticBaseSettingsSource, ...]:
            return (
                env_settings,
                YamlConfigSettingsSource(settings_cls),
                dotenv_settings,
                init_settings,
            )

    return TestSettings


# --- Test 1: Settings loads database_url from .env file ---

def test_loads_database_url_from_dotenv(tmp_path, monkeypatch):
    """Settings reads database_url from .env when no env var is set."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://localhost:5432/pmtb\n"
        "KALSHI_API_KEY_ID=test-key\n"
        "KALSHI_PRIVATE_KEY_PATH=/tmp/key.pem\n"
    )
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("")

    TestSettings = build_settings_class(str(env_file), str(yaml_file))
    s = TestSettings()
    assert s.database_url == "postgresql+asyncpg://localhost:5432/pmtb"


# --- Test 2: Settings loads edge_threshold from config.yaml ---

def test_loads_edge_threshold_from_yaml(tmp_path, monkeypatch):
    """Settings reads edge_threshold from config.yaml."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("EDGE_THRESHOLD", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://localhost:5432/pmtb\n"
        "KALSHI_API_KEY_ID=test-key\n"
        "KALSHI_PRIVATE_KEY_PATH=/tmp/key.pem\n"
    )
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("edge_threshold: 0.07\n")

    TestSettings = build_settings_class(str(env_file), str(yaml_file))
    s = TestSettings()
    assert s.edge_threshold == pytest.approx(0.07)


# --- Test 3: Environment variable overrides .env value ---

def test_env_var_overrides_dotenv(tmp_path, monkeypatch):
    """Environment variable DATABASE_URL takes precedence over .env file value."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://envhost:5432/env_db")
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://dotenv-host:5432/dotenv_db\n"
        "KALSHI_API_KEY_ID=test-key\n"
        "KALSHI_PRIVATE_KEY_PATH=/tmp/key.pem\n"
    )
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("")

    TestSettings = build_settings_class(str(env_file), str(yaml_file))
    s = TestSettings()
    assert s.database_url == "postgresql+asyncpg://envhost:5432/env_db"


# --- Test 4: TRADING_MODE defaults to "paper" and rejects invalid values ---

def test_trading_mode_defaults_to_paper(tmp_path, monkeypatch):
    """TRADING_MODE defaults to 'paper' when not specified."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("TRADING_MODE", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://localhost:5432/pmtb\n"
        "KALSHI_API_KEY_ID=test-key\n"
        "KALSHI_PRIVATE_KEY_PATH=/tmp/key.pem\n"
    )
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("")

    TestSettings = build_settings_class(str(env_file), str(yaml_file))
    s = TestSettings()
    assert s.trading_mode == "paper"


def test_trading_mode_rejects_invalid(tmp_path, monkeypatch):
    """TRADING_MODE='invalid' raises ValidationError."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("TRADING_MODE", "invalid")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://localhost:5432/pmtb\n"
        "KALSHI_API_KEY_ID=test-key\n"
        "KALSHI_PRIVATE_KEY_PATH=/tmp/key.pem\n"
    )
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("")

    TestSettings = build_settings_class(str(env_file), str(yaml_file))
    with pytest.raises(ValidationError, match="trading_mode"):
        TestSettings()


# --- Test 5: Missing required field (database_url) raises ValidationError ---

def test_missing_database_url_raises(tmp_path, monkeypatch):
    """Missing DATABASE_URL raises ValidationError at startup."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)

    env_file = tmp_path / ".env"
    # Intentionally omit DATABASE_URL
    env_file.write_text(
        "KALSHI_API_KEY_ID=test-key\n"
        "KALSHI_PRIVATE_KEY_PATH=/tmp/key.pem\n"
    )
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("")

    TestSettings = build_settings_class(str(env_file), str(yaml_file))
    with pytest.raises(ValidationError):
        TestSettings()


# --- Test 6: CLI --paper flag concept: TRADING_MODE=paper is valid ---

def test_trading_mode_paper_is_valid(tmp_path, monkeypatch):
    """TRADING_MODE='paper' is accepted as a valid value."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("TRADING_MODE", "paper")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://localhost:5432/pmtb\n"
        "KALSHI_API_KEY_ID=test-key\n"
        "KALSHI_PRIVATE_KEY_PATH=/tmp/key.pem\n"
    )
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("")

    TestSettings = build_settings_class(str(env_file), str(yaml_file))
    s = TestSettings()
    assert s.trading_mode == "paper"


def test_trading_mode_live_is_valid(tmp_path, monkeypatch):
    """TRADING_MODE='live' is also accepted."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("TRADING_MODE", "live")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://localhost:5432/pmtb\n"
        "KALSHI_API_KEY_ID=test-key\n"
        "KALSHI_PRIVATE_KEY_PATH=/tmp/key.pem\n"
    )
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("")

    TestSettings = build_settings_class(str(env_file), str(yaml_file))
    s = TestSettings()
    assert s.trading_mode == "live"
