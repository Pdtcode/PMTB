"""
Configuration management for PMTB.

Settings class uses pydantic-settings v2 with layered sources:
    defaults -> YAML file -> .env file -> environment variables

Validated at startup — fails fast with clear errors on missing required fields.
"""

from __future__ import annotations

from typing import Any, Tuple, Type

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class Settings(BaseSettings):
    """
    Application settings loaded from .env + config.yaml + environment variables.

    Precedence (highest to lowest):
        1. Environment variables
        2. YAML config file (config.yaml)
        3. .env file
        4. Defaults defined here
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        yaml_file="config.yaml",
    )

    # --- Trading mode ---
    trading_mode: str = Field(
        default="paper",
        pattern=r"^(paper|live)$",
        description="Trading mode: 'paper' (simulated) or 'live' (real orders)",
    )

    # --- Required secrets (no defaults — must be set) ---
    database_url: str = Field(
        description="PostgreSQL connection URL (postgresql+asyncpg://...)"
    )
    kalshi_api_key_id: str = Field(
        description="Kalshi API key ID for request signing"
    )
    kalshi_private_key_path: str = Field(
        description="Path to RSA private key PEM file for Kalshi request signing"
    )

    # --- Trading model parameters (YAML defaults) ---
    edge_threshold: float = Field(
        default=0.04,
        description="Minimum edge (model probability - market probability) to consider a trade",
    )
    kelly_alpha: float = Field(
        default=0.25,
        description="Fractional Kelly multiplier (0.25 = quarter Kelly)",
    )
    max_drawdown: float = Field(
        default=0.08,
        description="Maximum portfolio drawdown before halting (hard stop)",
    )

    # --- Scanner settings ---
    scan_interval_seconds: int = Field(
        default=300,
        description="Seconds between market scan cycles",
    )
    rate_limit_per_second: int = Field(
        default=10,
        description="API calls per second limit",
    )

    # --- Scanner filter thresholds ---
    scanner_min_open_interest: float = Field(
        default=100.0,
        description="Minimum open_interest_fp (fixed-point) to pass liquidity filter",
    )
    scanner_min_volume_24h: float = Field(
        default=50.0,
        description="Minimum volume_24h_fp (fixed-point) to pass volume filter",
    )
    scanner_max_spread: float = Field(
        default=0.15,
        description="Maximum yes_ask - yes_bid spread to pass spread filter",
    )
    scanner_min_ttr_hours: float = Field(
        default=1.0,
        description="Minimum hours until close_time to pass TTR filter",
    )
    scanner_max_ttr_days: float = Field(
        default=30.0,
        description="Maximum days until close_time to pass TTR filter",
    )
    scanner_min_volatility: float = Field(
        default=0.005,
        description="Minimum price stdev to pass volatility filter (after warmup)",
    )
    scanner_volatility_warmup: int = Field(
        default=6,
        description="Number of price snapshots required before volatility is computed",
    )
    scanner_enrichment_concurrency: int = Field(
        default=5,
        description="Max concurrent API calls during market enrichment",
    )

    # --- Research settings ---
    research_agent_timeout: float = Field(
        default=30.0,
        description="Timeout in seconds per research agent",
    )
    research_concurrency: int = Field(
        default=4,
        description="Max concurrent research agent calls per market",
    )
    vader_escalation_threshold: float = Field(
        default=0.3,
        description="VADER compound score abs threshold below which text is escalated to Claude",
    )
    query_cache_ttl_seconds: int = Field(
        default=3600,
        description="TTL for cached search queries per ticker",
    )
    research_results_per_source: int = Field(
        default=10,
        description="Number of results to fetch per source per market",
    )
    reddit_client_id: str | None = Field(
        default=None,
        description="Reddit API OAuth client ID",
    )
    reddit_client_secret: str | None = Field(
        default=None,
        description="Reddit API OAuth client secret",
    )
    reddit_user_agent: str = Field(
        default="pmtb-research/1.0",
        description="Reddit API user agent string",
    )
    anthropic_api_key: str | None = Field(
        default=None,
        description="Anthropic API key for Claude sentiment escalation — None = VADER-only mode",
    )
    rss_feeds: dict[str, list[str]] = Field(
        default_factory=dict,
        description="RSS feed URLs by market category",
    )

    # --- Logging ---
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG, INFO, WARNING, ERROR",
    )

    # --- Kalshi API endpoints ---
    kalshi_base_url: str = Field(
        default="https://api.elections.kalshi.com",
        description="Kalshi production REST API base URL",
    )
    kalshi_ws_url: str = Field(
        default="wss://api.elections.kalshi.com/trade-api/ws/v2",
        description="Kalshi production WebSocket URL",
    )
    kalshi_demo_base_url: str = Field(
        default="https://demo-api.kalshi.co",
        description="Kalshi demo REST API base URL",
    )
    kalshi_demo_ws_url: str = Field(
        default="wss://demo-api.kalshi.co/trade-api/ws/v2",
        description="Kalshi demo WebSocket URL",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """
        Custom source order: env vars > YAML > .env > init defaults.
        This ensures environment variables always win (important for Docker/CI).
        """
        return (
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            dotenv_settings,
            init_settings,
        )
