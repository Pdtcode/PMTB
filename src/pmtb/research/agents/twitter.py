"""
TwitterAgent — stub implementation of the ResearchAgent Protocol.

Twitter/X API cost is not yet justified for Phase 3.
This stub implements the full interface and returns an empty SourceResult without error.
Swap in real implementation when the cost is justified.

Decision: [Research flag from Phase 3 planning] Twitter/X API tier cost may require
launching Phase 3 with Reddit + RSS only. This stub preserves the interface contract.
"""
from __future__ import annotations

from loguru import logger

from pmtb.research.models import SourceResult
from pmtb.scanner.models import MarketCandidate

_STUB_REASON = (
    "Twitter/X API deferred — swap in real implementation when cost justified"
)


class TwitterAgent:
    """
    Stub Twitter/X research agent.

    Returns empty SourceResult for every fetch call.
    Implements the full ResearchAgent Protocol as a drop-in replacement.
    """

    source_name = "twitter"

    def __init__(self) -> None:
        logger.bind(source="twitter").info(
            "TwitterAgent initialized as stub — real implementation deferred"
        )

    async def fetch(self, candidate: MarketCandidate, query: str) -> SourceResult:
        """Return empty SourceResult — Twitter/X API not yet implemented."""
        logger.bind(source="twitter", ticker=candidate.ticker).debug(
            "TwitterAgent stub called — returning empty SourceResult"
        )
        return SourceResult(
            source="twitter",
            signals=[],
            raw_data={"stub": True, "reason": _STUB_REASON},
        )
