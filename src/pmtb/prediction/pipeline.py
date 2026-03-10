"""
ProbabilityPipeline — orchestrates the full prediction flow.

Handles three modes:
  - Cold start (xgb.is_ready=False, claude.is_available=True):
      Claude is the sole estimator. XGBoost runs shadow_predict for future training data.
      PredictionResult.used_llm=True, is_shadow=False.

  - Hybrid (xgb.is_ready=True):
      XGBoost provides base estimate. Claude is gated to the 0.4-0.6 uncertainty band.
      If XGBoost p is outside [confidence_low, confidence_high], Claude is NOT called
      and combine_estimates receives p_claude=None (pass-through).
      If XGBoost p is inside the band AND Claude is available, both are combined via
      combine_estimates using the configured method.

  - Shadow-only (xgb.is_ready=False, claude.is_available=False):
      Neither estimator is ready. p_model is set to 0.5 (uninformative prior).
      PredictionResult.is_shadow=True. Logged but not traded.

Design decisions:
  - Shadow-only p_model=0.5: float("nan") would fail PredictionResult's ge=0.0
    Pydantic constraint. 0.5 is the uninformative prior — clearly marked by is_shadow=True.
  - market_id FK resolved per-prediction by querying the markets table for ticker.
    If market not found, logs warning and skips persistence (pipeline does not crash).
  - predict_all matches markets to bundles by ticker. Markets with no matching bundle
    are logged and skipped.
  - Prometheus PREDICTION_LATENCY histogram and PREDICTION_COUNT counter with mode label
    track production cost and performance.
"""
from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from loguru import logger
from prometheus_client import Counter, Histogram
from sqlalchemy import select, text

from pmtb.db.models import Market, ModelOutput
from pmtb.prediction.combiner import combine_estimates
from pmtb.prediction.confidence import compute_confidence_interval
from pmtb.prediction.features import build_feature_vector
from pmtb.prediction.models import PredictionResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from pmtb.config import Settings
    from pmtb.prediction.llm_predictor import ClaudePredictor
    from pmtb.prediction.xgboost_model import XGBoostPredictor
    from pmtb.research.models import SignalBundle
    from pmtb.scanner.models import MarketCandidate


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

PREDICTION_LATENCY = Histogram(
    "prediction_latency_seconds",
    "End-to-end prediction latency per market",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

PREDICTION_COUNT = Counter(
    "prediction_total",
    "Predictions by mode",
    labelnames=["mode"],
)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class ProbabilityPipeline:
    """
    Orchestrator for the full probability prediction flow.

    Parameters
    ----------
    xgb_predictor : XGBoostPredictor
        Calibrated XGBoost binary classifier. ``is_ready`` determines mode.
    claude_predictor : ClaudePredictor
        LLM-based probabilistic forecaster. Gated to uncertainty band.
    session_factory : async_sessionmaker
        SQLAlchemy async session factory for DB persistence.
    settings : Settings
        Application settings (band thresholds, combining method, CI half-width).
    """

    def __init__(
        self,
        xgb_predictor: "XGBoostPredictor",
        claude_predictor: "ClaudePredictor",
        session_factory: "async_sessionmaker",
        settings: "Settings",
    ) -> None:
        self._xgb = xgb_predictor
        self._claude = claude_predictor
        self._session_factory = session_factory
        self._settings = settings

    async def predict_one(
        self,
        market: "MarketCandidate",
        bundle: "SignalBundle",
    ) -> PredictionResult:
        """
        Produce a PredictionResult for a single market + signal bundle pair.

        Selects the prediction mode automatically based on estimator availability.
        Always persists a ModelOutput row to the DB (skipped only if market not found).

        Parameters
        ----------
        market : MarketCandidate
            Prediction market candidate to evaluate.
        bundle : SignalBundle
            Research signals bundle for the market.

        Returns
        -------
        PredictionResult
            Typed prediction output consumed by Phase 5 (execution engine).
        """
        start = time.perf_counter()
        log = logger.bind(ticker=market.ticker, cycle_id=bundle.cycle_id)

        X = build_feature_vector(bundle, market)

        if self._xgb.is_ready:
            # ----------------------------------------------------------------
            # HYBRID MODE — XGBoost primary, Claude gated to uncertainty band
            # ----------------------------------------------------------------
            p_xgb = self._xgb.predict(X)
            p_claude: float | None = None
            used_llm = False

            lo = self._settings.prediction_xgb_confidence_low
            hi = self._settings.prediction_xgb_confidence_high

            if lo <= p_xgb <= hi and self._claude.is_available:
                claude_result = await self._claude.predict(market, bundle)
                p_claude = claude_result["p_estimate"]
                used_llm = True
                log.debug(
                    "Hybrid mode: Claude called (p_xgb in band)",
                    p_xgb=p_xgb,
                    p_claude=p_claude,
                )
            else:
                log.debug(
                    "Hybrid mode: Claude NOT called (p_xgb outside band)",
                    p_xgb=p_xgb,
                    band_lo=lo,
                    band_hi=hi,
                )

            p_model = combine_estimates(
                p_xgb=p_xgb,
                p_claude=p_claude,
                method=self._settings.prediction_combine_method,
                weight_xgb=self._settings.prediction_xgb_weight,
                weight_claude=self._settings.prediction_claude_weight,
            )
            model_version = self._xgb.model_version
            is_shadow = False
            mode_label = "hybrid"

        elif self._claude.is_available:
            # ----------------------------------------------------------------
            # COLD START MODE — Claude is primary, XGBoost runs shadow
            # ----------------------------------------------------------------
            _ = self._xgb.shadow_predict(X)  # record for future training labels
            claude_result = await self._claude.predict(market, bundle)
            p_model = claude_result["p_estimate"]
            used_llm = True
            model_version = f"claude-only-{self._settings.prediction_claude_model}"
            is_shadow = False
            mode_label = "cold_start"
            log.debug("Cold start mode: Claude is sole estimator", p_model=p_model)

        else:
            # ----------------------------------------------------------------
            # SHADOW-ONLY — neither estimator available
            # ----------------------------------------------------------------
            _ = self._xgb.shadow_predict(X)
            # p_model=0.5: uninformative prior — float("nan") would fail PredictionResult
            # ge=0.0 Pydantic constraint. is_shadow=True marks this as not-tradeable.
            p_model = 0.5
            used_llm = False
            model_version = self._xgb.model_version
            is_shadow = True
            mode_label = "shadow_only"
            log.warning("Shadow-only mode: neither XGBoost nor Claude available")

        ci_low, ci_high = compute_confidence_interval(
            p_model, self._settings.prediction_ci_half_width
        )
        signal_weights = bundle.to_features()

        result = PredictionResult(
            ticker=market.ticker,
            cycle_id=bundle.cycle_id,
            p_model=p_model,
            confidence_low=ci_low,
            confidence_high=ci_high,
            signal_weights={k: v for k, v in signal_weights.items() if v == v},  # drop NaN
            model_version=model_version,
            used_llm=used_llm,
            is_shadow=is_shadow,
        )

        await self._persist(result, market)

        elapsed = time.perf_counter() - start
        PREDICTION_LATENCY.observe(elapsed)
        PREDICTION_COUNT.labels(mode=mode_label).inc()

        log.info(
            "Prediction complete",
            mode=mode_label,
            p_model=result.p_model,
            used_llm=result.used_llm,
            is_shadow=result.is_shadow,
            latency_s=round(elapsed, 3),
        )

        return result

    async def predict_all(
        self,
        markets: list["MarketCandidate"],
        bundles: list["SignalBundle"],
    ) -> list[PredictionResult]:
        """
        Produce PredictionResults for a batch of markets.

        Matches markets to bundles by ticker. Markets with no matching bundle are
        skipped with a warning. Individual prediction failures are caught, logged,
        and skipped — the pipeline continues with remaining markets.

        Parameters
        ----------
        markets : list[MarketCandidate]
        bundles : list[SignalBundle]

        Returns
        -------
        list[PredictionResult]
            Results for all markets that succeeded (may be fewer than input).
        """
        bundle_by_ticker: dict[str, "SignalBundle"] = {b.ticker: b for b in bundles}
        results: list[PredictionResult] = []

        for market in markets:
            bundle = bundle_by_ticker.get(market.ticker)
            if bundle is None:
                logger.warning(
                    "No bundle found for market — skipping",
                    ticker=market.ticker,
                )
                continue

            try:
                result = await self.predict_one(market, bundle)
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Prediction failed for market — continuing with remaining markets",
                    ticker=market.ticker,
                    error=str(exc),
                )

        return results

    async def _persist(
        self,
        result: PredictionResult,
        market: "MarketCandidate",
    ) -> None:
        """
        Write a ModelOutput row to the DB.

        Resolves the market_id FK by querying markets table for the ticker.
        Logs a warning and skips if the market is not found (does not crash the pipeline).

        Parameters
        ----------
        result : PredictionResult
        market : MarketCandidate
        """
        async with self._session_factory() as session:
            # Resolve market_id FK
            row = (
                await session.execute(
                    text("SELECT id FROM markets WHERE ticker = :ticker"),
                    {"ticker": market.ticker},
                )
            ).fetchone()

            if row is None:
                logger.warning(
                    "Market not found in DB — skipping ModelOutput persistence",
                    ticker=market.ticker,
                    cycle_id=result.cycle_id,
                )
                return

            market_id: uuid.UUID = row.id

            output = ModelOutput(
                market_id=market_id,
                p_model=result.p_model,
                p_market=market.implied_probability,
                confidence_low=result.confidence_low,
                confidence_high=result.confidence_high,
                signal_weights=result.signal_weights,
                model_version=result.model_version,
                used_llm=result.used_llm,
                cycle_id=result.cycle_id,
            )
            session.add(output)
            await session.commit()
