"""
Unit tests for PipelineOrchestrator.

Tests cover:
- _run_full_cycle calls all pipeline stages in sequence
- _run_full_cycle executes approved decisions and persists to DB
- _run_full_cycle skips cycle when scanner returns no candidates
- _run_full_cycle continues gracefully when scanner fails
- _run_full_cycle continues gracefully when research or prediction fails
- _execute_decision checks halt flag before placing order
- _execute_decision computes limit price as p_market + price_offset_cents
- _ws_reeval_loop re-runs decision pipeline on price event
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.orchestrator import PipelineOrchestrator


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_settings(
    scan_interval_seconds=900,
    stage_timeout_seconds=120.0,
    price_offset_cents=1,
    trading_mode="paper",
):
    s = MagicMock()
    s.scan_interval_seconds = scan_interval_seconds
    s.stage_timeout_seconds = stage_timeout_seconds
    s.price_offset_cents = price_offset_cents
    s.trading_mode = trading_mode
    return s


def _make_candidate(ticker="TEST-1", implied_probability=0.60):
    c = MagicMock()
    c.ticker = ticker
    c.implied_probability = implied_probability
    return c


def _make_scan_result(candidates):
    r = MagicMock()
    r.candidates = candidates
    return r


def _make_prediction(ticker="TEST-1", p_model=0.70):
    p = MagicMock()
    p.ticker = ticker
    p.p_model = p_model
    return p


def _make_decision(ticker="TEST-1", approved=True, p_market=0.60, quantity=5):
    d = MagicMock()
    d.ticker = ticker
    d.approved = approved
    d.side = "yes"
    d.quantity = quantity
    d.p_market = p_market
    d.edge = 0.10
    d.kelly_f = 0.05
    d.p_model = 0.70
    return d


def _make_orchestrator(
    scanner=None,
    research=None,
    predictor=None,
    decision=None,
    executor=None,
    fill_tracker=None,
    order_repo=None,
    settings=None,
    session_factory=None,
):
    scanner = scanner or AsyncMock()
    research = research or AsyncMock()
    predictor = predictor or AsyncMock()
    decision = decision or AsyncMock()
    executor = executor or AsyncMock()
    fill_tracker = fill_tracker or AsyncMock()
    order_repo = order_repo or AsyncMock()
    settings = settings or _make_settings()
    session_factory = session_factory or MagicMock()

    return PipelineOrchestrator(
        scanner=scanner,
        research=research,
        predictor=predictor,
        decision_pipeline=decision,
        executor=executor,
        fill_tracker=fill_tracker,
        order_repo=order_repo,
        settings=settings,
        session_factory=session_factory,
    )


# ---------------------------------------------------------------------------
# Task 1: _run_full_cycle — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_cycle_calls_all_stages():
    """_run_full_cycle calls scanner -> research -> predictor -> decision in sequence."""
    candidate = _make_candidate()
    scan_result = _make_scan_result([candidate])
    bundles = [MagicMock()]
    predictions = [_make_prediction()]
    approved = _make_decision(approved=True)

    scanner = AsyncMock()
    scanner.run_cycle = AsyncMock(return_value=scan_result)

    research = AsyncMock()
    research.run = AsyncMock(return_value=bundles)

    predictor = AsyncMock()
    predictor.predict_all = AsyncMock(return_value=predictions)

    decision = AsyncMock()
    decision.evaluate = AsyncMock(return_value=[approved])

    executor = AsyncMock()
    executor.place_order = AsyncMock(return_value={"order_id": "kalshi-123"})

    order_repo = AsyncMock()
    order_repo.create_order = AsyncMock()

    # Session factory that returns no trading halt
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    # TradingState returns None (not halted)
    mock_session.get = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=mock_session)

    orch = _make_orchestrator(
        scanner=scanner,
        research=research,
        predictor=predictor,
        decision=decision,
        executor=executor,
        order_repo=order_repo,
        session_factory=session_factory,
    )

    await orch._run_full_cycle()

    scanner.run_cycle.assert_called_once()
    research.run.assert_called_once()
    predictor.predict_all.assert_called_once()
    decision.evaluate.assert_called_once()


@pytest.mark.asyncio
async def test_full_cycle_executes_approved_decisions():
    """_run_full_cycle executes approved decisions and persists to DB."""
    candidate = _make_candidate(implied_probability=0.60)
    scan_result = _make_scan_result([candidate])
    bundles = [MagicMock()]
    predictions = [_make_prediction()]
    approved = _make_decision(approved=True, p_market=0.60)

    scanner = AsyncMock()
    scanner.run_cycle = AsyncMock(return_value=scan_result)
    research = AsyncMock()
    research.run = AsyncMock(return_value=bundles)
    predictor = AsyncMock()
    predictor.predict_all = AsyncMock(return_value=predictions)
    decision = AsyncMock()
    decision.evaluate = AsyncMock(return_value=[approved])

    executor = AsyncMock()
    executor.place_order = AsyncMock(return_value={"order_id": "kalshi-abc"})
    order_repo = AsyncMock()
    order_repo.create_order = AsyncMock()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = AsyncMock(return_value=None)  # not halted
    session_factory = MagicMock(return_value=mock_session)

    settings = _make_settings(price_offset_cents=1)
    orch = _make_orchestrator(
        scanner=scanner,
        research=research,
        predictor=predictor,
        decision=decision,
        executor=executor,
        order_repo=order_repo,
        settings=settings,
        session_factory=session_factory,
    )

    await orch._run_full_cycle()

    executor.place_order.assert_called_once()
    order_repo.create_order.assert_called_once()


@pytest.mark.asyncio
async def test_full_cycle_skips_when_no_candidates():
    """_run_full_cycle skips research/prediction when scanner returns no candidates."""
    scan_result = _make_scan_result([])

    scanner = AsyncMock()
    scanner.run_cycle = AsyncMock(return_value=scan_result)
    research = AsyncMock()
    predictor = AsyncMock()
    decision = AsyncMock()

    orch = _make_orchestrator(
        scanner=scanner,
        research=research,
        predictor=predictor,
        decision=decision,
    )

    await orch._run_full_cycle()

    research.run.assert_not_called()
    predictor.predict_all.assert_not_called()
    decision.evaluate.assert_not_called()


# ---------------------------------------------------------------------------
# Task 2: Stage failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_cycle_continues_when_scanner_fails():
    """_run_full_cycle logs error and returns early when scanner raises."""
    scanner = AsyncMock()
    scanner.run_cycle = AsyncMock(side_effect=Exception("Kalshi API down"))
    research = AsyncMock()
    predictor = AsyncMock()
    decision = AsyncMock()

    orch = _make_orchestrator(
        scanner=scanner,
        research=research,
        predictor=predictor,
        decision=decision,
    )

    # Should NOT raise — graceful handling
    await orch._run_full_cycle()

    research.run.assert_not_called()
    predictor.predict_all.assert_not_called()
    decision.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_full_cycle_continues_when_research_fails():
    """_run_full_cycle logs error and returns early when research raises."""
    candidate = _make_candidate()
    scan_result = _make_scan_result([candidate])

    scanner = AsyncMock()
    scanner.run_cycle = AsyncMock(return_value=scan_result)
    research = AsyncMock()
    research.run = AsyncMock(side_effect=Exception("Research pipeline error"))
    predictor = AsyncMock()
    decision = AsyncMock()

    orch = _make_orchestrator(
        scanner=scanner,
        research=research,
        predictor=predictor,
        decision=decision,
    )

    await orch._run_full_cycle()

    predictor.predict_all.assert_not_called()
    decision.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_full_cycle_continues_when_prediction_fails():
    """_run_full_cycle logs error and returns early when prediction raises."""
    candidate = _make_candidate()
    scan_result = _make_scan_result([candidate])
    bundles = [MagicMock()]

    scanner = AsyncMock()
    scanner.run_cycle = AsyncMock(return_value=scan_result)
    research = AsyncMock()
    research.run = AsyncMock(return_value=bundles)
    predictor = AsyncMock()
    predictor.predict_all = AsyncMock(side_effect=Exception("XGBoost crash"))
    decision = AsyncMock()

    orch = _make_orchestrator(
        scanner=scanner,
        research=research,
        predictor=predictor,
        decision=decision,
    )

    await orch._run_full_cycle()

    decision.evaluate.assert_not_called()


# ---------------------------------------------------------------------------
# Task 3: _execute_decision — halt flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_decision_skips_when_halted():
    """_execute_decision does NOT place an order when trading_halted is 'true'."""
    executor = AsyncMock()
    executor.place_order = AsyncMock(return_value={"order_id": "x"})

    mock_state = MagicMock()
    mock_state.value = "true"  # halted

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = AsyncMock(return_value=mock_state)
    session_factory = MagicMock(return_value=mock_session)

    orch = _make_orchestrator(
        executor=executor,
        session_factory=session_factory,
    )

    decision = _make_decision(approved=True, p_market=0.60)
    log = MagicMock()
    await orch._execute_decision(decision, log)

    executor.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_execute_decision_computes_limit_price():
    """_execute_decision computes price as int(p_market * 100) + price_offset_cents."""
    executor = AsyncMock()
    executor.place_order = AsyncMock(return_value={"order_id": "kalshi-xyz"})

    order_repo = AsyncMock()
    order_repo.create_order = AsyncMock()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = AsyncMock(return_value=None)  # not halted
    session_factory = MagicMock(return_value=mock_session)

    settings = _make_settings(price_offset_cents=2)
    orch = _make_orchestrator(
        executor=executor,
        order_repo=order_repo,
        settings=settings,
        session_factory=session_factory,
    )

    # p_market=0.55 → int(55) + 2 = 57
    decision = _make_decision(approved=True, p_market=0.55)
    log = MagicMock()
    await orch._execute_decision(decision, log)

    call_kwargs = executor.place_order.call_args
    assert call_kwargs.kwargs["price"] == 57


# ---------------------------------------------------------------------------
# Task 4: _ws_reeval_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_reeval_loop_calls_decision_on_event():
    """_ws_reeval_loop re-evaluates cached predictions when price event arrives."""
    predictions = [_make_prediction()]
    candidates = [_make_candidate()]
    decisions = [_make_decision(approved=False)]  # not approved — no executor call

    decision_pipeline = AsyncMock()
    decision_pipeline.evaluate = AsyncMock(return_value=decisions)

    orch = _make_orchestrator(decision=decision_pipeline)

    # Pre-populate the cache (normally set by _run_full_cycle)
    orch._last_predictions = predictions
    orch._last_candidates = candidates

    # Put one event in the queue then stop the loop
    orch._price_event_queue.put_nowait({"ticker": "TEST-1", "price": 62})

    stop_event = asyncio.Event()

    async def _run_and_stop():
        # Give the loop time to process the one event
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.gather(
        orch._ws_reeval_loop(stop_event),
        _run_and_stop(),
    )

    decision_pipeline.evaluate.assert_called_once_with(predictions, candidates)
