"""
ResearchPipeline — top-level orchestrator for the research signal pipeline.

Responsibilities:
- Dispatch all 4 research agents concurrently via asyncio.gather
- Wrap each agent call with asyncio.timeout for bounded execution
- Persist individual Signal rows to PostgreSQL for each successful classification
- Assemble per-market SignalBundles for downstream XGBoost consumption
- Emit Prometheus metrics for signals collected, failures, and cycle duration

Design decisions:
- Failed or timed-out agents produce None in SignalBundle (not neutral sentiment)
- Absence of data is not the same as neutral — NaN propagates to XGBoost correctly
- DB persistence is skipped if market_id cannot be resolved (market not in DB yet)
- All agents fire concurrently per candidate (not sequentially)
"""
from __future__ import annotations

import asyncio
import statistics
import uuid
from collections import Counter
from datetime import datetime, UTC
from decimal import Decimal

from loguru import logger
from prometheus_client import Counter as PCounter, Histogram

from pmtb.db.models import Signal
from pmtb.db.session import get_session
from pmtb.research.agent import ResearchAgent
from pmtb.research.models import (
    SignalBundle,
    SignalClassification,
    SourceResult,
    SourceSummary,
)
from pmtb.research.query import QueryConstructor

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

RESEARCH_SIGNALS_COLLECTED = PCounter(
    "research_signals_collected_total",
    "Number of individual Signal classifications collected",
    ["source"],
)

RESEARCH_AGENT_FAILURES = PCounter(
    "research_agent_failures_total",
    "Number of agent failures by source and reason",
    ["source", "reason"],
)

RESEARCH_CYCLE_DURATION = Histogram(
    "research_cycle_duration_seconds",
    "Time to run the full research pipeline for a batch of candidates",
)


# ---------------------------------------------------------------------------
# ResearchPipeline
# ---------------------------------------------------------------------------


class ResearchPipeline:
    """
    Orchestrates parallel research across all configured agents for a batch
    of market candidates in a single scan cycle.

    Usage:
        pipeline = ResearchPipeline(
            agents=[reddit_agent, rss_agent, trends_agent, twitter_agent],
            query_constructor=QueryConstructor(),
            session_factory=session_factory,
            timeout=30.0,
        )
        bundles = await pipeline.run(candidates, cycle_id="cycle-2026-001")
    """

    def __init__(
        self,
        agents: list[ResearchAgent],
        query_constructor: QueryConstructor,
        session_factory,
        timeout: float = 30.0,
    ) -> None:
        self._agents = agents
        self._query_constructor = query_constructor
        self._session_factory = session_factory
        self._timeout = timeout
        # Build source_name -> agent lookup for SignalBundle assembly
        self._agent_map: dict[str, ResearchAgent] = {a.source_name: a for a in agents}

    # ------------------------------------------------------------------
    # Internal: agent execution
    # ------------------------------------------------------------------

    async def _run_agent_safe(
        self,
        agent: ResearchAgent,
        candidate,
        query: str,
    ) -> SourceResult | None:
        """
        Run a single agent with timeout and exception isolation.

        Returns SourceResult on success, None on timeout or any exception.
        The caller (run()) maps None to a None SourceSummary in the SignalBundle.
        """
        try:
            async with asyncio.timeout(self._timeout):
                result = await agent.fetch(candidate, query)
                return result
        except TimeoutError:
            logger.warning(
                "Agent timed out",
                source=agent.source_name,
                ticker=candidate.ticker,
                timeout=self._timeout,
            )
            RESEARCH_AGENT_FAILURES.labels(source=agent.source_name, reason="timeout").inc()
            return None
        except Exception:
            logger.exception(
                "Agent raised exception",
                source=agent.source_name,
                ticker=candidate.ticker,
            )
            RESEARCH_AGENT_FAILURES.labels(source=agent.source_name, reason="error").inc()
            return None

    # ------------------------------------------------------------------
    # Internal: aggregation
    # ------------------------------------------------------------------

    def _aggregate_source(self, result: SourceResult | None) -> SourceSummary | None:
        """
        Aggregate a SourceResult into a SourceSummary.

        - None result  -> None (failed/timed-out source)
        - Empty signals -> SourceSummary(sentiment=None, confidence=None, signal_count=0)
        - Non-empty    -> majority sentiment + mean confidence
        """
        if result is None:
            return None

        signals = result.signals
        if not signals:
            return SourceSummary(sentiment=None, confidence=None, signal_count=0)

        # Majority vote for sentiment
        counts = Counter(s.sentiment for s in signals)
        majority_sentiment = counts.most_common(1)[0][0]

        # Mean confidence
        mean_confidence = statistics.mean(s.confidence for s in signals)

        return SourceSummary(
            sentiment=majority_sentiment,
            confidence=mean_confidence,
            signal_count=len(signals),
        )

    # ------------------------------------------------------------------
    # Internal: DB operations
    # ------------------------------------------------------------------

    async def _resolve_market_id(self, ticker: str) -> uuid.UUID | None:
        """
        Look up the market UUID by ticker.

        Returns None (with a warning) if the market is not in the DB yet.
        Persistence is skipped when None is returned — signals are still aggregated.
        """
        from sqlalchemy import text

        try:
            async with get_session(self._session_factory) as session:
                result = await session.execute(
                    text("SELECT id FROM markets WHERE ticker = :ticker"),
                    {"ticker": ticker},
                )
                row = result.fetchone()
                if row is None:
                    logger.warning("Market not found in DB — skipping signal persistence", ticker=ticker)
                    return None
                return uuid.UUID(str(row[0]))
        except Exception:
            logger.exception("Failed to resolve market_id", ticker=ticker)
            return None

    async def _persist_signals(
        self,
        market_id: uuid.UUID,
        result: SourceResult,
        cycle_id: str,
    ) -> None:
        """
        Write one Signal DB row per SignalClassification in the SourceResult.

        Commits after all signals for this source are added. If the session
        raises, the exception propagates but is caught by the caller.
        """
        async with get_session(self._session_factory) as session:
            for classification in result.signals:
                # Include reasoning in raw_data if present
                raw = dict(result.raw_data) if result.raw_data else {}
                if classification.reasoning:
                    raw["reasoning"] = classification.reasoning

                signal = Signal(
                    id=uuid.uuid4(),
                    market_id=market_id,
                    source=result.source,
                    sentiment=classification.sentiment,
                    confidence=Decimal(str(classification.confidence)),
                    raw_data=raw if raw else None,
                    cycle_id=cycle_id,
                    created_at=datetime.now(UTC),
                )
                session.add(signal)

            await session.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, candidates: list, cycle_id: str) -> list[SignalBundle]:
        """
        Run the research pipeline for a batch of MarketCandidates.

        For each candidate:
        1. Builds a search query via QueryConstructor
        2. Resolves market_id (for DB persistence)
        3. Fires all agents concurrently via asyncio.gather
        4. Persists signals for successful agents (if market_id resolved)
        5. Aggregates each SourceResult into SourceSummary
        6. Assembles SignalBundle

        Returns list[SignalBundle] matching input candidates order.
        """
        bundles: list[SignalBundle] = []
        total_signals = 0
        total_failures = 0

        with RESEARCH_CYCLE_DURATION.time():
            for candidate in candidates:
                # Step 1: Build search query
                query = await self._query_constructor.build_query(candidate)

                # Step 2: Resolve market_id (may be None — skips persistence)
                market_id = await self._resolve_market_id(candidate.ticker)

                # Step 3: Fire all agents concurrently
                results: list[SourceResult | None] = await asyncio.gather(
                    *[self._run_agent_safe(agent, candidate, query) for agent in self._agents]
                )

                # Step 4: Persist signals for successful agents
                for result in results:
                    if result is not None:
                        if market_id is not None:
                            try:
                                await self._persist_signals(market_id, result, cycle_id)
                            except Exception:
                                logger.exception(
                                    "Failed to persist signals",
                                    source=result.source,
                                    ticker=candidate.ticker,
                                )
                        # Prometheus counter for collected signals
                        RESEARCH_SIGNALS_COLLECTED.labels(source=result.source).inc(
                            len(result.signals)
                        )
                        total_signals += len(result.signals)
                    else:
                        total_failures += 1

                # Step 5: Aggregate each result into SourceSummary
                # Map agent index -> source_name for assembly
                source_summaries: dict[str, SourceSummary | None] = {}
                for agent, result in zip(self._agents, results):
                    source_summaries[agent.source_name] = self._aggregate_source(result)

                # Step 6: Assemble SignalBundle
                bundle = SignalBundle(
                    ticker=candidate.ticker,
                    cycle_id=cycle_id,
                    reddit=source_summaries.get("reddit"),
                    rss=source_summaries.get("rss"),
                    trends=source_summaries.get("trends"),
                    twitter=source_summaries.get("twitter"),
                )
                bundles.append(bundle)

        logger.info(
            "Research cycle complete",
            cycle_id=cycle_id,
            candidates=len(candidates),
            total_signals=total_signals,
            total_failures=total_failures,
        )

        return bundles
