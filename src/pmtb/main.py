"""
Application entry point for PMTB.

Wires all Phase 1-6 components together:
- Settings (pydantic-settings) — load and validate config
- Logging (loguru) — configure before any log statements
- Database — create async engine and session factory
- KalshiClient — authenticated REST client
- KalshiWSClient — WebSocket client for real-time fills
- Executor — paper or live order executor via factory
- Reconciler — resolve position discrepancies on startup
- Metrics — Prometheus HTTP server
- Scanner — market candidate discovery (Phase 2)
- Research — signal pipeline (Phase 3)
- Predictor — probability model (Phase 4)
- DecisionPipeline — edge/size/risk gates (Phase 5)
- OrderRepository — CRUD layer for orders (Phase 6-01)
- FillTracker — order fill lifecycle (Phase 6-02)
- PipelineOrchestrator — end-to-end pipeline loop (Phase 6-03)
- Watchdog — drawdown circuit breaker (Phase 5-03)

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

    Loads config, wires all subsystems, runs reconciliation, starts the
    PipelineOrchestrator, and shuts down cleanly on SIGINT/SIGTERM.
    """
    # Import here to keep startup imports explicit and ordered
    from pmtb.config import Settings
    from pmtb.logging_ import configure_logging
    from pmtb.db.engine import create_engine_from_settings, create_session_factory
    from pmtb.kalshi.client import KalshiClient
    from pmtb.kalshi.ws_client import KalshiWSClient
    from pmtb.executor import create_executor
    from pmtb.reconciler import reconcile_positions
    from pmtb.metrics import start_metrics_server

    # --- Phase 6-03 pipeline components ---
    from pmtb.orchestrator import PipelineOrchestrator
    from pmtb.fill_tracker import FillTracker
    from pmtb.order_repo import OrderRepository
    from pmtb.decision.pipeline import DecisionPipeline
    from pmtb.decision.watchdog import launch_watchdog
    from pmtb.scanner.scanner import MarketScanner
    from pmtb.research.pipeline import ResearchPipeline
    from pmtb.research.agents.reddit import RedditAgent
    from pmtb.research.agents.rss import RSSAgent
    from pmtb.research.agents.trends import TrendsAgent
    from pmtb.research.agents.twitter import TwitterAgent
    from pmtb.research.query import QueryConstructor
    from pmtb.research.sentiment import SentimentClassifier
    from pmtb.prediction.pipeline import ProbabilityPipeline
    from pmtb.prediction.xgboost_model import XGBoostPredictor
    from pmtb.prediction.llm_predictor import ClaudePredictor
    from pathlib import Path

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

    # --- Create Kalshi clients ---
    kalshi_client = KalshiClient(settings)
    ws_client = KalshiWSClient(settings)

    # --- Create order executor (paper or live based on trading_mode) ---
    executor = create_executor(settings, kalshi_client, session_factory=session_factory)
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

    # --- Build pipeline components ---

    # Scanner (Phase 2)
    scanner = MarketScanner(
        client=kalshi_client,
        settings=settings,
        session_factory=session_factory,
    )

    # Research pipeline (Phase 3) — construct agents from settings
    classifier = SentimentClassifier(
        escalation_threshold=settings.vader_escalation_threshold,
        anthropic_api_key=settings.anthropic_api_key,
    )
    reddit_agent = RedditAgent(
        classifier=classifier,
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
        results_limit=settings.research_results_per_source,
    )
    rss_agent = RSSAgent(
        classifier=classifier,
        feeds_by_category=settings.rss_feeds,
        results_limit=settings.research_results_per_source,
    )
    trends_agent = TrendsAgent(classifier=classifier)
    twitter_agent = TwitterAgent()
    query_constructor = QueryConstructor(
        cache_ttl=settings.query_cache_ttl_seconds,
        anthropic_api_key=settings.anthropic_api_key,
    )
    research = ResearchPipeline(
        agents=[reddit_agent, rss_agent, trends_agent, twitter_agent],
        query_constructor=query_constructor,
        session_factory=session_factory,
        timeout=settings.research_agent_timeout,
    )

    # Prediction pipeline (Phase 4)
    xgb_predictor = XGBoostPredictor(
        model_path=Path(settings.prediction_model_path),
        min_training_samples=settings.prediction_min_training_samples,
        calibration_method=settings.prediction_calibration_method,
    )
    claude_predictor = ClaudePredictor(
        anthropic_api_key=settings.anthropic_api_key,
        model=settings.prediction_claude_model,
    )
    predictor = ProbabilityPipeline(
        xgb_predictor=xgb_predictor,
        claude_predictor=claude_predictor,
        session_factory=session_factory,
        settings=settings,
    )

    # Decision pipeline (Phase 5)
    decision_pipeline = DecisionPipeline.from_settings(
        settings=settings,
        session_factory=session_factory,
        portfolio_value=settings.portfolio_value,
    )

    # Phase 6-01: Order repository
    order_repo = OrderRepository(session_factory)

    # Phase 6-02: Fill tracker
    fill_tracker = FillTracker(
        ws_client=ws_client,
        kalshi_client=kalshi_client,
        order_repo=order_repo,
        settings=settings,
    )

    # --- Launch watchdog (Phase 5-03) before asyncio orchestrator ---
    # daemon=False — must survive main process crash
    watchdog_proc = launch_watchdog(settings)
    logger.info("Watchdog launched", pid=watchdog_proc.pid)

    # --- Create orchestrator (Phase 6-03) ---
    orchestrator = PipelineOrchestrator(
        scanner=scanner,
        research=research,
        predictor=predictor,
        decision_pipeline=decision_pipeline,
        executor=executor,
        fill_tracker=fill_tracker,
        order_repo=order_repo,
        settings=settings,
        session_factory=session_factory,
    )

    # --- Application started ---
    logger.info(
        "PMTB started",
        trading_mode=settings.trading_mode,
        scan_interval_seconds=settings.scan_interval_seconds,
    )

    # --- Wait for shutdown ---
    stop_event = asyncio.Event()

    def _signal_handler(sig, frame) -> None:
        logger.info("Shutdown signal received", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("PMTB running — press Ctrl+C or send SIGTERM to stop")

    # --- Run pipeline orchestrator ---
    try:
        await orchestrator.run(stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("PMTB shutting down cleanly")
        if watchdog_proc.is_alive():
            watchdog_proc.terminate()
            watchdog_proc.join(timeout=5)
        await engine.dispose()
        logger.info("PMTB stopped")


if __name__ == "__main__":
    asyncio.run(main())
