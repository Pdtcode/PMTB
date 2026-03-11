"""
LearningLoop — closes the feedback loop between trade outcomes and XGBoost retraining.

Responsibilities:
- Poll Kalshi GET /portfolio/settlements to detect resolved trade outcomes
- Resolve trades: update resolved_outcome, resolved_at, pnl in DB
- Compute recency-weighted retraining samples (exponential decay)
- Trigger retraining:
    1. On periodic schedule (configurable, default weekly via APScheduler)
    2. When rolling 30d Brier score degrades past threshold
- Model replacement gate: retrained model replaces live model ONLY if
  hold-out Brier score improves vs current model
- Schedule daily full recomputation of performance metrics via MetricsService
- Lifecycle: start()/stop()/run() for PipelineOrchestrator integration

Design decisions:
- AsyncIOScheduler with IntervalTrigger(max_instances=1) prevents concurrent retrain
- asyncio.wait_for on stop_event.wait() for interruptible polling sleep (Phase 06 pattern)
- Recency weights: exp(-ln(2)/half_life * age_days), normalized to sum=1
- temporal_train_test_split: sorted by resolved_at ascending, last 20% is hold-out
  (no random split — prevents lookahead bias)
- Void markets (market_result not in "yes"/"no") are skipped silently
- Retraining log includes: before/after Brier, sample count, model version, trigger
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import numpy as np
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from prometheus_client import Counter
from sqlalchemy import select

from pmtb.db.models import Market, ModelOutput, Trade

if TYPE_CHECKING:
    from pmtb.config import Settings
    from pmtb.performance.metrics import MetricsService
    from pmtb.prediction.xgboost_model import XGBoostPredictor


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

RETRAINING_EVENTS = Counter(
    "pmtb_retraining_events_total",
    "Total retraining events by trigger and result",
    ["trigger", "result"],
)

SETTLEMENTS_PROCESSED = Counter(
    "pmtb_settlements_processed_total",
    "Total trade settlements processed by the learning loop",
)


# ---------------------------------------------------------------------------
# LearningLoop
# ---------------------------------------------------------------------------


class LearningLoop:
    """
    Feedback loop: detects trade resolutions, feeds outcomes back into XGBoost retraining.

    Constructor:
        kalshi_client:   Authenticated Kalshi HTTP client (has ._request method)
        predictor:       XGBoostPredictor instance (shared with prediction pipeline)
        metrics_service: MetricsService for Brier checks and daily recomputation
        session_factory: SQLAlchemy async_sessionmaker
        settings:        Settings with retraining and polling config
    """

    def __init__(
        self,
        kalshi_client: Any,
        predictor: "XGBoostPredictor",
        metrics_service: "MetricsService",
        session_factory: Any,
        settings: "Settings",
    ) -> None:
        self._kalshi_client = kalshi_client
        self._predictor = predictor
        self._metrics_service = metrics_service
        self._session_factory = session_factory
        self._settings = settings
        self._scheduler: AsyncIOScheduler | None = None
        self._last_poll_time: datetime = datetime.now(UTC) - timedelta(days=1)

    # ------------------------------------------------------------------
    # Settlement polling
    # ------------------------------------------------------------------

    async def poll_settlements(self, since: datetime) -> list[dict]:
        """
        Fetch settled markets from Kalshi since a given timestamp.

        Uses cursor-based pagination to handle > 200 results.

        Args:
            since: Only return settlements after this UTC datetime.

        Returns:
            List of settlement dicts from Kalshi API.
        """
        settlements: list[dict] = []
        params: dict[str, Any] = {
            "min_ts": int(since.timestamp()),
            "limit": 200,
        }

        while True:
            try:
                response = await self._kalshi_client._request(
                    "GET",
                    "/trade-api/v2/portfolio/settlements",
                    params=params,
                )
                batch = response.get("settlements", [])
                settlements.extend(batch)

                cursor = response.get("cursor")
                if not cursor or len(batch) < 200:
                    break
                params["cursor"] = cursor

            except Exception:
                logger.exception("Settlement polling error")
                break

        logger.info("Settlements fetched", count=len(settlements), since=since.isoformat())
        return settlements

    # ------------------------------------------------------------------
    # Trade resolution
    # ------------------------------------------------------------------

    async def resolve_trades(self, settlements: list[dict]) -> int:
        """
        Match settlements to Trade rows and update resolved outcome + PnL.

        Void markets (market_result not in "yes"/"no") are skipped.
        Calls metrics_service.update_on_resolution() after each resolved trade.

        Args:
            settlements: List of settlement dicts from poll_settlements().

        Returns:
            Number of trades successfully resolved.
        """
        resolved_count = 0

        for settlement in settlements:
            market_result = settlement.get("market_result", "")
            if market_result not in ("yes", "no"):
                logger.debug(
                    "Skipping void/invalid settlement",
                    ticker=settlement.get("market_ticker"),
                    result=market_result,
                )
                continue

            ticker = settlement.get("market_ticker")
            if not ticker:
                continue

            revenue = settlement.get("revenue", 0)
            cost = settlement.get("cost", 0)
            pnl = float(revenue) / 100.0 - float(cost) / 100.0

            try:
                async with self._session_factory() as session:
                    # Find the market by ticker
                    mkt_stmt = select(Market).where(Market.ticker == ticker)
                    mkt_result = await session.execute(mkt_stmt)
                    market = mkt_result.scalar_one_or_none()

                    if market is None:
                        logger.debug("No market row found for settlement ticker", ticker=ticker)
                        continue

                    # Find the most recent unresolved trade for this market
                    trade_stmt = (
                        select(Trade)
                        .where(
                            Trade.market_id == market.id,
                            Trade.resolved_at.is_(None),
                        )
                        .order_by(Trade.created_at.desc())
                        .limit(1)
                    )
                    trade_result = await session.execute(trade_stmt)
                    trade = trade_result.scalar_one_or_none()

                    if trade is None:
                        logger.debug("No unresolved trade for ticker", ticker=ticker)
                        continue

                    # Update trade
                    trade.resolved_outcome = market_result
                    trade.resolved_at = datetime.now(UTC)
                    trade.pnl = Decimal(str(round(pnl, 6)))
                    await session.commit()

                resolved_count += 1
                SETTLEMENTS_PROCESSED.inc()
                logger.info(
                    "Trade resolved",
                    ticker=ticker,
                    outcome=market_result,
                    pnl=pnl,
                )

                await self._metrics_service.update_on_resolution()

            except Exception:
                logger.exception("Error resolving trade", ticker=ticker)

        return resolved_count

    # ------------------------------------------------------------------
    # Recency weighting
    # ------------------------------------------------------------------

    def compute_recency_weights(self, resolved_ats: list[datetime]) -> np.ndarray:
        """
        Compute exponential decay weights for resolved trade timestamps.

        weight_i = exp(-lambda * age_days_i)
        where lambda = ln(2) / half_life_days

        Normalized so weights sum to 1.

        Args:
            resolved_ats: List of UTC datetimes for each resolved trade.

        Returns:
            np.ndarray of shape (n,) with normalized weights, newest = highest.
        """
        if not resolved_ats:
            return np.array([], dtype=np.float64)

        now = datetime.now(UTC)
        half_life = getattr(self._settings, "retraining_half_life_days", 30.0)
        lam = math.log(2) / half_life

        ages = np.array(
            [(now - ts).total_seconds() / 86400.0 for ts in resolved_ats],
            dtype=np.float64,
        )
        weights = np.exp(-lam * ages)
        total = weights.sum()
        if total > 0:
            weights = weights / total
        return weights

    # ------------------------------------------------------------------
    # Temporal train/test split
    # ------------------------------------------------------------------

    def temporal_train_test_split(
        self,
        X: np.ndarray,
        y: np.ndarray,
        resolved_ats: list[datetime],
        test_fraction: float = 0.2,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Split data chronologically — last test_fraction by resolved_at is hold-out.

        Prevents lookahead bias: never random, always time-ordered.

        Args:
            X:              Feature matrix (n_samples, n_features)
            y:              Label vector (n_samples,)
            resolved_ats:   Corresponding resolved_at timestamps
            test_fraction:  Fraction to reserve as hold-out (default 0.2)

        Returns:
            (X_train, X_test, y_train, y_test, weights_train)
        """
        n = len(y)
        # Sort by resolved_at ascending
        order = sorted(range(n), key=lambda i: resolved_ats[i])
        split_idx = int(n * (1 - test_fraction))

        train_idx = [order[i] for i in range(split_idx)]
        test_idx = [order[i] for i in range(split_idx, n)]

        X_train = X[train_idx]
        X_test = X[test_idx]
        y_train = y[train_idx]
        y_test = y[test_idx]

        # Compute recency weights for training set
        train_ats = [resolved_ats[i] for i in train_idx]
        weights_train = self.compute_recency_weights(train_ats)

        return X_train, X_test, y_train, y_test, weights_train

    # ------------------------------------------------------------------
    # Training data builder
    # ------------------------------------------------------------------

    async def _build_training_data(
        self,
    ) -> tuple[np.ndarray, np.ndarray, list[datetime]] | None:
        """
        Query all resolved trades with their ModelOutput features.

        Builds:
            X:            Feature matrix from ModelOutput.signal_weights
            y:            Binary labels (1=yes outcome, 0=no outcome)
            resolved_ats: Corresponding resolved_at timestamps

        Returns None if fewer than min_training_samples resolved trades exist.
        """
        min_samples = getattr(self._settings, "prediction_min_training_samples", 100)

        async with self._session_factory() as session:
            stmt = (
                select(Trade)
                .where(
                    Trade.resolved_at.is_not(None),
                    Trade.resolved_outcome.is_not(None),
                )
                .order_by(Trade.resolved_at.asc())
            )
            result = await session.execute(stmt)
            trades = list(result.scalars().all())

            if len(trades) < min_samples:
                logger.info(
                    "Insufficient resolved trades for retraining",
                    count=len(trades),
                    required=min_samples,
                )
                return None

            X_rows: list[list[float]] = []
            y_vals: list[float] = []
            resolved_ats: list[datetime] = []

            for trade in trades:
                # Find most recent ModelOutput for this market
                mo_stmt = (
                    select(ModelOutput)
                    .where(
                        ModelOutput.market_id == trade.market_id,
                        ModelOutput.created_at <= trade.created_at,
                    )
                    .order_by(ModelOutput.created_at.desc())
                    .limit(1)
                )
                mo_result = await session.execute(mo_stmt)
                mo = mo_result.scalar_one_or_none()

                if mo is None or mo.signal_weights is None:
                    continue

                # Build feature vector from signal_weights dict
                # Sorted keys for deterministic ordering
                features = [float(mo.signal_weights.get(k, float("nan")))
                            for k in sorted(mo.signal_weights.keys())]
                if not features:
                    continue

                X_rows.append(features)
                y_vals.append(1.0 if trade.resolved_outcome == "yes" else 0.0)
                resolved_ats.append(trade.resolved_at)

            if len(X_rows) < min_samples:
                logger.info(
                    "Insufficient feature-complete trades for retraining",
                    count=len(X_rows),
                    required=min_samples,
                )
                return None

            # Pad rows to same length (in case signal sets differ)
            max_len = max(len(r) for r in X_rows)
            X = np.array(
                [r + [float("nan")] * (max_len - len(r)) for r in X_rows],
                dtype=np.float64,
            )
            y = np.array(y_vals, dtype=np.float64)

            return X, y, resolved_ats

    # ------------------------------------------------------------------
    # Baseline Brier getter
    # ------------------------------------------------------------------

    async def _get_baseline_brier(self) -> float | None:
        """
        Get the most recent rolling 30d Brier score from the PerformanceMetric table.

        Returns None if no rolling Brier exists.
        """
        from pmtb.db.models import PerformanceMetric
        from sqlalchemy import desc

        async with self._session_factory() as session:
            stmt = (
                select(PerformanceMetric)
                .where(
                    PerformanceMetric.metric_name == "brier_score",
                    PerformanceMetric.period == "30d",
                )
                .order_by(desc(PerformanceMetric.computed_at))
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return float(row.metric_value)

    # ------------------------------------------------------------------
    # Retraining logic
    # ------------------------------------------------------------------

    async def maybe_retrain(self, trigger: str = "periodic") -> bool:
        """
        Attempt to retrain XGBoost with recency-weighted resolved trades.

        Steps:
            1. Check rolling Brier (if trigger="brier_degradation": skip if not degraded)
            2. Build training data — return False if insufficient
            3. Temporal train/test split (no lookahead)
            4. Evaluate current model on hold-out -> old_brier
            5. Train new model with recency weights on train set
            6. Evaluate new model on hold-out -> new_brier
            7. If new_brier < old_brier: save new model, log success
            8. Else: keep current model, log failure

        Args:
            trigger: "periodic" or "brier_degradation" — logged with event.

        Returns:
            True if new model was saved, False otherwise.
        """
        from sklearn.metrics import brier_score_loss

        # Step 1: Check Brier degradation trigger
        if trigger == "brier_degradation":
            snapshot = await self._metrics_service.compute_all("30d")
            rolling_brier = snapshot.brier_score if snapshot else None
            baseline = await self._get_baseline_brier()

            if rolling_brier is None or baseline is None:
                logger.info("Brier degradation check: insufficient data, skipping retrain")
                return False

            threshold = getattr(self._settings, "brier_degradation_threshold", 0.05)
            if rolling_brier <= baseline + threshold:
                logger.info(
                    "Brier degradation check: not degraded, skipping retrain",
                    rolling_brier=rolling_brier,
                    baseline=baseline,
                    threshold=threshold,
                )
                return False

        # Step 2: Build training data
        data = await self._build_training_data()
        if data is None:
            RETRAINING_EVENTS.labels(trigger=trigger, result="skipped_insufficient").inc()
            return False

        X, y, resolved_ats = data
        sample_count = len(y)

        # Step 3: Temporal split
        X_train, X_test, y_train, y_test, weights_train = self.temporal_train_test_split(
            X, y, resolved_ats
        )

        if len(X_test) == 0 or len(X_train) == 0:
            logger.warning("Temporal split produced empty partition, skipping retrain")
            RETRAINING_EVENTS.labels(trigger=trigger, result="skipped_empty_split").inc()
            return False

        # Step 4: Evaluate current model on hold-out (old_brier)
        old_brier: float | None = None
        old_version = self._predictor.model_version if self._predictor.is_ready else "none"

        if self._predictor.is_ready:
            try:
                old_preds = np.array([self._predictor.predict(X_test[i:i+1]) for i in range(len(X_test))])
                old_brier = float(brier_score_loss(y_test, old_preds))
            except Exception:
                logger.exception("Failed to evaluate old model on hold-out, will use 1.0 as baseline")
                old_brier = 1.0
        else:
            # No existing model — any new model is an improvement
            old_brier = 1.0

        # Step 5: Train new model with recency weights
        import tempfile
        from pathlib import Path

        # Train on a temporary copy to avoid modifying the live predictor mid-evaluation
        model_path_str = getattr(self._settings, "prediction_model_path", "models/xgb_calibrated.joblib")
        min_samples = getattr(self._settings, "prediction_min_training_samples", 100)
        calibration_method = getattr(self._settings, "prediction_calibration_method", "sigmoid")

        from pmtb.prediction.xgboost_model import XGBoostPredictor

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "candidate.joblib"
            candidate = XGBoostPredictor(
                model_path=tmp_path,
                min_training_samples=min_samples,
                calibration_method=calibration_method,
            )

            try:
                train_metrics = candidate.train(X_train, y_train, sample_weight=weights_train)
            except ValueError as e:
                logger.warning("Candidate model training failed", error=str(e))
                RETRAINING_EVENTS.labels(trigger=trigger, result="failed_training").inc()
                return False

            # Step 6: Evaluate new model on hold-out
            new_preds = np.array([candidate.predict(X_test[i:i+1]) for i in range(len(X_test))])
            new_brier = float(brier_score_loss(y_test, new_preds))

            # Step 7 / 8: Gate — save only if improved
            if new_brier < old_brier:
                # Replace live predictor state
                self._predictor._model = candidate._model
                self._predictor._is_trained = True
                self._predictor._train_timestamp = candidate._train_timestamp
                self._predictor.save()
                new_version = self._predictor.model_version

                RETRAINING_EVENTS.labels(trigger=trigger, result="accepted").inc()
                logger.info(
                    "Retraining accepted — model updated",
                    trigger=trigger,
                    old_brier=old_brier,
                    new_brier=new_brier,
                    improvement=old_brier - new_brier,
                    sample_count=sample_count,
                    old_version=old_version,
                    new_version=new_version,
                )
                return True
            else:
                RETRAINING_EVENTS.labels(trigger=trigger, result="rejected_worse").inc()
                logger.warning(
                    "Retraining rejected — new model is not better",
                    trigger=trigger,
                    old_brier=old_brier,
                    new_brier=new_brier,
                    sample_count=sample_count,
                    old_version=old_version,
                )
                return False

    # ------------------------------------------------------------------
    # Settlement poll loop
    # ------------------------------------------------------------------

    async def _settlement_poll_loop(self, stop_event: asyncio.Event) -> None:
        """
        Continuously poll Kalshi for new settlements.

        Polls every settlement_poll_interval_seconds. Uses asyncio.wait_for on
        stop_event.wait() for clean shutdown (Phase 06 pattern).
        """
        poll_interval = getattr(
            self._settings, "settlement_poll_interval_seconds", 60
        )

        while not stop_event.is_set():
            try:
                settlements = await self.poll_settlements(since=self._last_poll_time)
                self._last_poll_time = datetime.now(UTC)

                if settlements:
                    resolved = await self.resolve_trades(settlements)
                    if resolved > 0:
                        # Check Brier degradation after new resolutions
                        asyncio.get_event_loop().create_task(
                            self.maybe_retrain(trigger="brier_degradation")
                        )

            except Exception:
                logger.exception("Settlement poll loop error")

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=poll_interval,
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass  # Normal: poll interval elapsed

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Start the APScheduler with two jobs:
          1. maybe_retrain on retraining_schedule_hours interval (max_instances=1)
          2. metrics_service.recompute_all_windows on 24h interval (max_instances=1)
        """
        self._scheduler = AsyncIOScheduler()

        # Job 1: Periodic retraining
        retrain_hours = getattr(self._settings, "retraining_schedule_hours", 168)
        self._scheduler.add_job(
            self.maybe_retrain,
            trigger=IntervalTrigger(hours=retrain_hours),
            kwargs={"trigger": "periodic"},
            max_instances=1,
            replace_existing=True,
            id="periodic_retrain",
        )

        # Job 2: Daily full metric recomputation
        self._scheduler.add_job(
            self._metrics_service.recompute_all_windows,
            trigger=IntervalTrigger(hours=24),
            max_instances=1,
            replace_existing=True,
            id="daily_recompute",
        )

        self._scheduler.start()
        logger.info(
            "LearningLoop scheduler started",
            retrain_interval_hours=retrain_hours,
            daily_recompute=True,
        )

    def stop(self) -> None:
        """Shut down the APScheduler without waiting for running jobs."""
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("LearningLoop scheduler stopped")

    async def run(self, stop_event: asyncio.Event) -> None:
        """
        Full lifecycle entry point:
          1. start() — initialize scheduler
          2. await _settlement_poll_loop() — run until stop_event
          3. stop() — shut down scheduler (in finally block)
        """
        self.start()
        try:
            await self._settlement_poll_loop(stop_event)
        finally:
            self.stop()
