"""
Tests for ProbabilityPipeline orchestration.

Covers:
- Cold start mode: Claude is primary, XGBoost shadow runs in background
- Hybrid mode (inside band): XGBoost primary, Claude gated in 0.4-0.6 band
- Hybrid mode (outside band): XGBoost primary, Claude NOT called
- Shadow-only mode: neither estimator available, graceful degradation
- predict_all continues past individual failures
- DB persistence: ModelOutput fields match PredictionResult values
- LLM gating: Claude never called when XGBoost p > 0.6 or p < 0.4
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.prediction.pipeline import ProbabilityPipeline
from pmtb.prediction.models import PredictionResult
from pmtb.research.models import SignalBundle
from pmtb.scanner.models import MarketCandidate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_market(ticker: str = "TEST-MARKET") -> MarketCandidate:
    """Create a minimal MarketCandidate for testing."""
    return MarketCandidate(
        ticker=ticker,
        title=f"Test market {ticker}",
        category="test",
        event_context={},
        close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
        implied_probability=0.5,
        yes_bid=0.45,
        yes_ask=0.55,
        spread=0.10,
        volume_24h=500.0,
        volatility_score=None,
    )


def _make_bundle(ticker: str = "TEST-MARKET") -> SignalBundle:
    """Create a minimal SignalBundle for testing."""
    return SignalBundle(
        ticker=ticker,
        cycle_id="cycle-001",
        reddit=None,
        rss=None,
        trends=None,
        twitter=None,
    )


def _make_settings():
    """Create a minimal Settings-like mock."""
    s = MagicMock()
    s.prediction_xgb_confidence_low = 0.4
    s.prediction_xgb_confidence_high = 0.6
    s.prediction_combine_method = "log_odds"
    s.prediction_xgb_weight = 0.6
    s.prediction_claude_weight = 0.4
    s.prediction_ci_half_width = 0.1
    s.prediction_claude_model = "claude-sonnet-4-20250514"
    return s


def _make_xgb(is_ready: bool = True, predict_val: float = 0.5):
    """Create a mock XGBoostPredictor."""
    xgb = MagicMock()
    xgb.is_ready = is_ready
    xgb.model_version = "xgb-v1-sigmoid-20260101T000000" if is_ready else "shadow-xgb-v0"
    xgb.predict.return_value = predict_val
    xgb.shadow_predict.return_value = float("nan")
    return xgb


def _make_claude(is_available: bool = True, p_estimate: float = 0.65):
    """Create a mock ClaudePredictor."""
    claude = MagicMock()
    claude.is_available = is_available
    claude.predict = AsyncMock(
        return_value={
            "p_estimate": p_estimate,
            "confidence": 0.8,
            "reasoning": "Test reasoning",
            "key_factors": ["factor1"],
        }
    )
    return claude


def _make_session_factory(market_uuid: uuid.UUID | None = None):
    """Create a mock async_sessionmaker returning a mock async session."""
    if market_uuid is None:
        market_uuid = uuid.uuid4()

    # Mock the execute result for market ID lookup
    mock_row = MagicMock()
    mock_row.id = market_uuid

    mock_result = MagicMock()
    mock_result.fetchone.return_value = mock_row

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    # Support async context manager usage
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value = mock_session

    return mock_factory, mock_session, market_uuid


# ---------------------------------------------------------------------------
# Cold start mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_start_mode():
    """Cold start: XGBoost not ready, Claude available -> Claude is sole estimator."""
    xgb = _make_xgb(is_ready=False)
    claude = _make_claude(is_available=True, p_estimate=0.65)
    session_factory, _, _ = _make_session_factory()
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    market = _make_market()
    bundle = _make_bundle()

    result = await pipeline.predict_one(market, bundle)

    assert isinstance(result, PredictionResult)
    assert result.used_llm is True
    assert result.is_shadow is False
    assert result.ticker == "TEST-MARKET"
    assert result.cycle_id == "cycle-001"
    # p_model should come from Claude
    assert abs(result.p_model - 0.65) < 1e-6

    # XGBoost shadow_predict should have been called for future training data
    xgb.shadow_predict.assert_called_once()

    # Claude predict should have been called once
    claude.predict.assert_awaited_once_with(market, bundle)


@pytest.mark.asyncio
async def test_cold_start_mode_sets_model_version():
    """Cold start: model_version should include 'claude-only' prefix."""
    xgb = _make_xgb(is_ready=False)
    claude = _make_claude(is_available=True, p_estimate=0.7)
    session_factory, _, _ = _make_session_factory()
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    result = await pipeline.predict_one(_make_market(), _make_bundle())

    assert "claude-only" in result.model_version


# ---------------------------------------------------------------------------
# Hybrid mode (inside 0.4-0.6 band)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_mode_inside_band():
    """Hybrid: XGBoost returns 0.5 (in band) -> Claude called, combine_estimates called."""
    xgb = _make_xgb(is_ready=True, predict_val=0.5)
    claude = _make_claude(is_available=True, p_estimate=0.6)
    session_factory, _, _ = _make_session_factory()
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    result = await pipeline.predict_one(_make_market(), _make_bundle())

    assert result.used_llm is True
    assert result.is_shadow is False
    # Claude should have been called
    claude.predict.assert_awaited_once()
    # XGBoost predict should have been called (not shadow_predict)
    xgb.predict.assert_called_once()


@pytest.mark.asyncio
async def test_hybrid_mode_at_band_boundaries():
    """Hybrid: p_xgb exactly at 0.4 and 0.6 (inclusive) should call Claude."""
    for p_xgb in [0.4, 0.6]:
        xgb = _make_xgb(is_ready=True, predict_val=p_xgb)
        claude = _make_claude(is_available=True, p_estimate=0.55)
        session_factory, _, _ = _make_session_factory()
        settings = _make_settings()

        pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
        result = await pipeline.predict_one(_make_market(), _make_bundle())

        assert result.used_llm is True, f"Expected Claude call at p_xgb={p_xgb}"


# ---------------------------------------------------------------------------
# Hybrid mode (outside 0.4-0.6 band)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_mode_outside_band_high():
    """Hybrid: XGBoost returns 0.8 (outside band) -> Claude NOT called."""
    xgb = _make_xgb(is_ready=True, predict_val=0.8)
    claude = _make_claude(is_available=True)
    session_factory, _, _ = _make_session_factory()
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    result = await pipeline.predict_one(_make_market(), _make_bundle())

    assert result.used_llm is False
    assert result.is_shadow is False
    # Claude should NOT have been called
    claude.predict.assert_not_awaited()
    # p_model should come directly from XGBoost (combine_estimates pass-through)
    assert abs(result.p_model - 0.8) < 1e-6


@pytest.mark.asyncio
async def test_hybrid_mode_outside_band_low():
    """Hybrid: XGBoost returns 0.2 (outside band) -> Claude NOT called."""
    xgb = _make_xgb(is_ready=True, predict_val=0.2)
    claude = _make_claude(is_available=True)
    session_factory, _, _ = _make_session_factory()
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    result = await pipeline.predict_one(_make_market(), _make_bundle())

    assert result.used_llm is False
    claude.predict.assert_not_awaited()
    assert abs(result.p_model - 0.2) < 1e-6


@pytest.mark.asyncio
async def test_llm_gating_outside_confidence_band():
    """Explicit LLM gating test: Claude is NEVER called when p_xgb outside 0.4-0.6."""
    outside_band_values = [0.0, 0.1, 0.2, 0.39, 0.61, 0.7, 0.8, 0.9, 1.0]
    for p_xgb in outside_band_values:
        xgb = _make_xgb(is_ready=True, predict_val=p_xgb)
        claude = _make_claude(is_available=True)
        session_factory, _, _ = _make_session_factory()
        settings = _make_settings()

        pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
        result = await pipeline.predict_one(_make_market(), _make_bundle())

        assert not claude.predict.await_count, (
            f"Claude should NOT be called when p_xgb={p_xgb} (outside 0.4-0.6 band). "
            f"Got {claude.predict.await_count} calls."
        )
        assert result.used_llm is False


# ---------------------------------------------------------------------------
# Shadow-only mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_only_mode():
    """Shadow-only: XGBoost not ready AND Claude unavailable -> is_shadow=True."""
    xgb = _make_xgb(is_ready=False)
    claude = _make_claude(is_available=False)
    session_factory, _, _ = _make_session_factory()
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    result = await pipeline.predict_one(_make_market(), _make_bundle())

    assert result.is_shadow is True
    assert result.used_llm is False
    # p_model should be 0.5 (uninformative prior)
    assert result.p_model == 0.5


# ---------------------------------------------------------------------------
# predict_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predict_all_continues_on_failure():
    """predict_all continues when one prediction fails — first and third still succeed."""
    market_a = _make_market("MARKET-A")
    market_b = _make_market("MARKET-B")
    market_c = _make_market("MARKET-C")

    bundle_a = _make_bundle("MARKET-A")
    bundle_b = _make_bundle("MARKET-B")
    bundle_c = _make_bundle("MARKET-C")

    xgb = _make_xgb(is_ready=True, predict_val=0.8)  # outside band, no Claude
    claude = _make_claude(is_available=True)
    session_factory, _, _ = _make_session_factory()
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)

    # Patch predict_one to fail for MARKET-B only
    original_predict_one = pipeline.predict_one
    call_count = {"n": 0}

    async def predict_one_patched(market, bundle):
        call_count["n"] += 1
        if market.ticker == "MARKET-B":
            raise RuntimeError("Simulated failure for MARKET-B")
        return await original_predict_one(market, bundle)

    pipeline.predict_one = predict_one_patched

    results = await pipeline.predict_all(
        [market_a, market_b, market_c],
        [bundle_a, bundle_b, bundle_c],
    )

    # Should return 2 successful results (A and C), skipping B
    assert len(results) == 2
    tickers = {r.ticker for r in results}
    assert "MARKET-A" in tickers
    assert "MARKET-C" in tickers
    assert "MARKET-B" not in tickers


@pytest.mark.asyncio
async def test_predict_all_matches_markets_to_bundles_by_ticker():
    """predict_all correctly matches markets to bundles by ticker."""
    market_x = _make_market("MARKET-X")
    market_y = _make_market("MARKET-Y")

    # Deliberately supply bundles in reverse order
    bundle_y = _make_bundle("MARKET-Y")
    bundle_x = _make_bundle("MARKET-X")

    xgb = _make_xgb(is_ready=True, predict_val=0.8)
    claude = _make_claude(is_available=False)
    session_factory, _, _ = _make_session_factory()
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    results = await pipeline.predict_all(
        [market_x, market_y],
        [bundle_y, bundle_x],  # reversed order
    )

    assert len(results) == 2
    result_by_ticker = {r.ticker: r for r in results}
    assert result_by_ticker["MARKET-X"].cycle_id == "cycle-001"
    assert result_by_ticker["MARKET-Y"].cycle_id == "cycle-001"


@pytest.mark.asyncio
async def test_predict_all_skips_market_without_bundle():
    """predict_all skips markets that have no matching bundle (logs warning)."""
    market_a = _make_market("MARKET-A")
    market_b = _make_market("MARKET-B")  # no bundle for this one

    bundle_a = _make_bundle("MARKET-A")

    xgb = _make_xgb(is_ready=True, predict_val=0.8)
    claude = _make_claude(is_available=False)
    session_factory, _, _ = _make_session_factory()
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    results = await pipeline.predict_all([market_a, market_b], [bundle_a])

    assert len(results) == 1
    assert results[0].ticker == "MARKET-A"


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_writes_model_output():
    """Verify ModelOutput fields match PredictionResult values."""
    from pmtb.db.models import ModelOutput

    market_uuid = uuid.uuid4()
    xgb = _make_xgb(is_ready=True, predict_val=0.8)  # outside band — no Claude
    claude = _make_claude(is_available=False)
    session_factory, mock_session, _ = _make_session_factory(market_uuid)
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    market = _make_market()
    bundle = _make_bundle()

    result = await pipeline.predict_one(market, bundle)

    # session.add should have been called with a ModelOutput
    assert mock_session.add.called
    added_obj = mock_session.add.call_args[0][0]
    assert isinstance(added_obj, ModelOutput)

    # Verify FK and fields
    assert added_obj.market_id == market_uuid
    assert float(added_obj.p_model) == pytest.approx(result.p_model, abs=1e-6)
    assert float(added_obj.confidence_low) == pytest.approx(result.confidence_low, abs=1e-6)
    assert float(added_obj.confidence_high) == pytest.approx(result.confidence_high, abs=1e-6)
    assert added_obj.model_version == result.model_version
    assert added_obj.used_llm == result.used_llm
    assert added_obj.cycle_id == result.cycle_id

    # Commit should have been called
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_skips_if_market_not_found_in_db():
    """If market not found in DB, pipeline logs warning but does not crash."""
    # Return None from fetchone to simulate market not found
    mock_result = MagicMock()
    mock_result.fetchone.return_value = None

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    session_factory = MagicMock()
    session_factory.return_value = mock_session

    xgb = _make_xgb(is_ready=True, predict_val=0.8)
    claude = _make_claude(is_available=False)
    settings = _make_settings()

    pipeline = ProbabilityPipeline(xgb, claude, session_factory, settings)
    # Should not raise — should log warning and skip persistence
    result = await pipeline.predict_one(_make_market(), _make_bundle())

    assert isinstance(result, PredictionResult)
    # add should NOT have been called since market was not found
    mock_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# Prometheus metrics (basic smoke test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prometheus_metrics_registered():
    """Verify PREDICTION_LATENCY and PREDICTION_COUNT exist in pipeline module."""
    from pmtb.prediction import pipeline as pipeline_module

    assert hasattr(pipeline_module, "PREDICTION_LATENCY")
    assert hasattr(pipeline_module, "PREDICTION_COUNT")
