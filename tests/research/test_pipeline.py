"""
Tests for ResearchPipeline orchestrator.

Tests verify:
- Parallel execution (all 4 agents fire concurrently via asyncio.gather)
- Graceful degradation (exception in one agent preserves others)
- Timeout handling (slow agent times out and produces None in SignalBundle)
- Failed source is None, NOT neutral sentiment
- Signal DB persistence (mock session)
- SignalBundle assembly per market per source_name
"""
from __future__ import annotations

import asyncio
import time
import uuid
from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmtb.research.pipeline import ResearchPipeline
from pmtb.research.models import (
    SignalBundle,
    SignalClassification,
    SourceResult,
    SourceSummary,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def make_candidate(ticker: str = "TEST-1", title: str = "Will X happen?", category: str = "politics"):
    """Create a minimal MarketCandidate-like object."""
    import datetime as dt
    from pmtb.scanner.models import MarketCandidate
    return MarketCandidate(
        ticker=ticker,
        title=title,
        category=category,
        event_context={},
        close_time=dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc),
        yes_bid=0.48,
        yes_ask=0.52,
        implied_probability=0.50,
        spread=0.04,
        volume_24h=1000.0,
        volatility_score=0.1,
    )


def make_agent(source_name: str, result: SourceResult | None = None, delay: float = 0.0, raises=None):
    """Create a mock ResearchAgent."""
    agent = MagicMock()
    agent.source_name = source_name

    async def _fetch(candidate, query):
        if delay:
            await asyncio.sleep(delay)
        if raises is not None:
            raise raises
        return result or SourceResult(
            source=source_name,
            signals=[SignalClassification(sentiment="bullish", confidence=0.8)],
        )

    agent.fetch = _fetch
    return agent


def make_query_constructor(query: str = "test query"):
    qc = MagicMock()
    qc.build_query = AsyncMock(return_value=query)
    return qc


def make_session_factory():
    """Return a mock async session factory that supports add/commit."""
    session = AsyncMock()
    session.add = MagicMock()  # add is synchronous
    session.commit = AsyncMock()

    # Session factory returns an async context manager
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory() -> AsyncGenerator:
        yield session

    return _factory, session


# ---------------------------------------------------------------------------
# test_parallel_execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_execution():
    """All 4 agents run concurrently — total time must be < 0.3s for 4 x 0.1s agents."""
    agents = [make_agent(name, delay=0.1) for name in ["reddit", "rss", "trends", "twitter"]]
    qc = make_query_constructor()
    factory, _ = make_session_factory()

    pipeline = ResearchPipeline(
        agents=agents,
        query_constructor=qc,
        session_factory=factory,
        timeout=5.0,
    )

    candidate = make_candidate()

    # Patch _resolve_market_id to return None (skip DB persistence)
    pipeline._resolve_market_id = AsyncMock(return_value=None)

    start = time.monotonic()
    bundles = await pipeline.run([candidate], cycle_id="cycle-001")
    elapsed = time.monotonic() - start

    # If sequential: 4 * 0.1 = 0.4s minimum. Parallel should be < 0.25s
    assert elapsed < 0.3, f"Pipeline took {elapsed:.3f}s — expected parallel < 0.3s"
    assert len(bundles) == 1


# ---------------------------------------------------------------------------
# test_graceful_degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_degradation():
    """One agent raises RuntimeError; other 3 sources are still populated."""
    agents = [
        make_agent("reddit"),
        make_agent("rss"),
        make_agent("trends", raises=RuntimeError("API down")),
        make_agent("twitter"),
    ]
    qc = make_query_constructor()
    factory, _ = make_session_factory()

    pipeline = ResearchPipeline(
        agents=agents,
        query_constructor=qc,
        session_factory=factory,
        timeout=5.0,
    )
    pipeline._resolve_market_id = AsyncMock(return_value=None)

    bundles = await pipeline.run([make_candidate()], cycle_id="cycle-002")
    assert len(bundles) == 1
    bundle = bundles[0]

    # 3 sources should have SourceSummary, 1 should be None
    assert bundle.reddit is not None
    assert bundle.rss is not None
    assert bundle.trends is None  # raised
    assert bundle.twitter is not None


# ---------------------------------------------------------------------------
# test_timeout_handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_handling():
    """Agent that sleeps longer than timeout becomes None in SignalBundle."""
    agents = [
        make_agent("reddit"),
        make_agent("rss"),
        make_agent("trends"),
        make_agent("twitter", delay=100.0),  # will timeout
    ]
    qc = make_query_constructor()
    factory, _ = make_session_factory()

    pipeline = ResearchPipeline(
        agents=agents,
        query_constructor=qc,
        session_factory=factory,
        timeout=0.05,  # 50ms timeout
    )
    pipeline._resolve_market_id = AsyncMock(return_value=None)

    bundles = await pipeline.run([make_candidate()], cycle_id="cycle-003")
    bundle = bundles[0]

    assert bundle.twitter is None  # timed out


# ---------------------------------------------------------------------------
# test_failed_source_is_none_not_neutral
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_source_is_none_not_neutral():
    """A failed source produces None SourceSummary, not SourceSummary(sentiment='neutral')."""
    agents = [
        make_agent("reddit", raises=RuntimeError("network error")),
        make_agent("rss"),
        make_agent("trends"),
        make_agent("twitter"),
    ]
    qc = make_query_constructor()
    factory, _ = make_session_factory()

    pipeline = ResearchPipeline(
        agents=agents,
        query_constructor=qc,
        session_factory=factory,
        timeout=5.0,
    )
    pipeline._resolve_market_id = AsyncMock(return_value=None)

    bundles = await pipeline.run([make_candidate()], cycle_id="cycle-004")
    bundle = bundles[0]

    assert bundle.reddit is None, "Failed source must be None, not a neutral SourceSummary"


# ---------------------------------------------------------------------------
# test_signal_persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_persistence():
    """Signal rows are added to DB session for each successful agent classification."""
    market_id = uuid.uuid4()

    result = SourceResult(
        source="reddit",
        signals=[
            SignalClassification(sentiment="bullish", confidence=0.9),
            SignalClassification(sentiment="bearish", confidence=0.6),
        ],
    )
    agents = [
        make_agent("reddit", result=result),
        make_agent("rss"),
        make_agent("trends"),
        make_agent("twitter"),
    ]
    qc = make_query_constructor()
    factory, session = make_session_factory()

    pipeline = ResearchPipeline(
        agents=agents,
        query_constructor=qc,
        session_factory=factory,
        timeout=5.0,
    )
    # Return a real market_id so persistence runs
    pipeline._resolve_market_id = AsyncMock(return_value=market_id)

    await pipeline.run([make_candidate()], cycle_id="cycle-005")

    # session.add should have been called once per signal across all agents
    # reddit: 2, rss: 1, trends: 1, twitter: 1 (at minimum reddit signals)
    assert session.add.call_count >= 2  # at least the 2 reddit signals

    # Verify Signal objects were added (check args of first few calls)
    from pmtb.db.models import Signal
    added_objects = [call.args[0] for call in session.add.call_args_list]
    signal_objs = [obj for obj in added_objects if isinstance(obj, Signal)]
    assert len(signal_objs) >= 2

    # Check fields on the reddit signals
    reddit_signals = [s for s in signal_objs if s.source == "reddit"]
    assert len(reddit_signals) == 2
    sentiments = {s.sentiment for s in reddit_signals}
    assert sentiments == {"bullish", "bearish"}
    assert all(s.market_id == market_id for s in reddit_signals)
    assert all(s.cycle_id == "cycle-005" for s in reddit_signals)

    # session.commit should have been called at least once
    assert session.commit.call_count >= 1


# ---------------------------------------------------------------------------
# test_bundle_assembly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bundle_assembly():
    """SignalBundle has correct per-source SourceSummary mapped by source_name."""
    agents = [
        make_agent("reddit", result=SourceResult(
            source="reddit",
            signals=[SignalClassification(sentiment="bullish", confidence=0.9)],
        )),
        make_agent("rss", result=SourceResult(
            source="rss",
            signals=[SignalClassification(sentiment="bearish", confidence=0.7)],
        )),
        make_agent("trends", result=SourceResult(
            source="trends",
            signals=[SignalClassification(sentiment="neutral", confidence=0.5)],
        )),
        make_agent("twitter", result=SourceResult(
            source="twitter",
            signals=[],  # empty signals
        )),
    ]
    qc = make_query_constructor()
    factory, _ = make_session_factory()

    pipeline = ResearchPipeline(
        agents=agents,
        query_constructor=qc,
        session_factory=factory,
        timeout=5.0,
    )
    pipeline._resolve_market_id = AsyncMock(return_value=None)

    bundles = await pipeline.run([make_candidate(ticker="MKTX-1")], cycle_id="cycle-006")
    assert len(bundles) == 1
    bundle = bundles[0]

    assert bundle.ticker == "MKTX-1"
    assert bundle.cycle_id == "cycle-006"

    # Reddit: bullish
    assert bundle.reddit is not None
    assert bundle.reddit.sentiment == "bullish"
    assert bundle.reddit.signal_count == 1

    # RSS: bearish
    assert bundle.rss is not None
    assert bundle.rss.sentiment == "bearish"
    assert bundle.rss.signal_count == 1

    # Trends: neutral
    assert bundle.trends is not None
    assert bundle.trends.sentiment == "neutral"
    assert bundle.trends.signal_count == 1

    # Twitter: empty signals -> SourceSummary with signal_count=0 and None sentiment
    assert bundle.twitter is not None  # result returned (just empty signals)
    assert bundle.twitter.signal_count == 0
    assert bundle.twitter.sentiment is None


# ---------------------------------------------------------------------------
# test_aggregate_source_empty_signals
# ---------------------------------------------------------------------------


def test_aggregate_source_empty_signals():
    """Empty signals produce SourceSummary with None sentiment and confidence."""
    from unittest.mock import MagicMock
    factory, _ = make_session_factory()
    pipeline = ResearchPipeline(
        agents=[],
        query_constructor=MagicMock(),
        session_factory=factory,
    )
    result = SourceResult(source="rss", signals=[])
    summary = pipeline._aggregate_source(result)
    assert summary is not None
    assert summary.sentiment is None
    assert summary.confidence is None
    assert summary.signal_count == 0


def test_aggregate_source_none_returns_none():
    """None result produces None SourceSummary."""
    from unittest.mock import MagicMock
    factory, _ = make_session_factory()
    pipeline = ResearchPipeline(
        agents=[],
        query_constructor=MagicMock(),
        session_factory=factory,
    )
    assert pipeline._aggregate_source(None) is None


def test_aggregate_source_majority_sentiment():
    """Majority sentiment wins; confidence is averaged."""
    from unittest.mock import MagicMock
    factory, _ = make_session_factory()
    pipeline = ResearchPipeline(
        agents=[],
        query_constructor=MagicMock(),
        session_factory=factory,
    )
    result = SourceResult(
        source="reddit",
        signals=[
            SignalClassification(sentiment="bullish", confidence=0.9),
            SignalClassification(sentiment="bullish", confidence=0.7),
            SignalClassification(sentiment="bearish", confidence=0.5),
        ],
    )
    summary = pipeline._aggregate_source(result)
    assert summary is not None
    assert summary.sentiment == "bullish"
    assert abs(summary.confidence - (0.9 + 0.7 + 0.5) / 3) < 0.001
    assert summary.signal_count == 3
