"""
ResearchAgent Protocol — the shared interface that all research agents implement.

Pattern: @runtime_checkable Protocol (mirrors OrderExecutorProtocol in src/pmtb/executor.py).
Using Protocol instead of ABC means:
  - isinstance() checks work at runtime without requiring inheritance
  - Stub and real agents are structurally interchangeable
  - No import coupling between the protocol definition and implementations

All four agents (Reddit, RSS, Trends, Twitter-stub) must implement:
  - source_name: str class attribute identifying the source
  - async fetch(candidate, query) -> SourceResult
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pmtb.scanner.models import MarketCandidate
from pmtb.research.models import SourceResult


@runtime_checkable
class ResearchAgent(Protocol):
    """
    Protocol for all research agents in the signal pipeline.

    Agents are responsible for:
    1. Fetching raw content from their respective source API
    2. Running NLP sentiment classification on each piece of content
    3. Returning a SourceResult with individual SignalClassification objects

    The pipeline orchestrator (ResearchPipeline) wraps each agent's fetch() call with
    asyncio.timeout() and error handling — agents do NOT need to handle timeouts themselves.

    source_name must match Signal.source values in the DB:
        "reddit" | "rss" | "trends" | "twitter"
    """

    source_name: str

    async def fetch(self, candidate: MarketCandidate, query: str) -> SourceResult:
        """
        Fetch and classify signals for a market candidate.

        Args:
            candidate: The market being researched (use candidate.category for source routing)
            query:     Pre-constructed search query string (from QueryConstructor)

        Returns:
            SourceResult with zero or more SignalClassification objects.
            Empty signals list is valid — it means the source found no relevant content.

        Raises:
            Any exception is allowed — the pipeline's _run_agent_safe() wrapper handles it.
        """
        ...
