"""
Query constructor for the research signal pipeline.

QueryCache   — TTL-based in-process cache keyed by market ticker.
QueryConstructor — Generates search queries from MarketCandidate fields using:
  1. Template matching (no API cost) for common market title patterns
  2. Claude fallback for unusual titles (if API key available)
  3. Keyword extraction fallback (no API key / template and Claude both fail)

Cache is checked first on every build_query() call. Queries are cached after
generation to avoid redundant API calls within the TTL window.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pmtb.scanner.models import MarketCandidate

# ---------------------------------------------------------------------------
# Stopwords for keyword extraction fallback
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "will", "would",
        "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "to", "of", "in", "on", "at", "by", "for", "with", "about", "than",
        "or", "and", "but", "if", "then", "that", "this", "it", "its",
        "not", "no", "nor", "so", "yet", "both", "either", "from", "into",
        "through", "during", "before", "after", "above", "below", "between",
        "out", "up", "down", "over", "under", "again", "further", "once",
        "s", "t", "re", "ll", "ve", "m", "d",
    }
)


# ---------------------------------------------------------------------------
# QueryCache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    query: str
    expires_at: datetime


class QueryCache:
    """
    In-process TTL cache for generated search queries.

    Keys are market tickers. Values are query strings that expire after `ttl_seconds`.
    Uses UTC timestamps for expiry — no external dependencies.
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, _CacheEntry] = {}

    def get(self, ticker: str) -> str | None:
        """Return cached query if within TTL, else None."""
        entry = self._store.get(ticker)
        if entry is None:
            return None
        if datetime.now(tz=timezone.utc) >= entry.expires_at:
            del self._store[ticker]
            return None
        return entry.query

    def set(self, ticker: str, query: str) -> None:
        """Store a query with TTL expiry."""
        expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=self._ttl)
        self._store[ticker] = _CacheEntry(query=query, expires_at=expires_at)


# ---------------------------------------------------------------------------
# QueryConstructor
# ---------------------------------------------------------------------------


class QueryConstructor:
    """
    Generate search queries from MarketCandidate metadata.

    Template patterns cover the most common Kalshi market title structures.
    Unusual titles fall back to Claude or simple keyword extraction.
    Results are cached per ticker to avoid redundant generation.

    Parameters
    ----------
    cache_ttl : int
        TTL in seconds for the query cache (default 3600 = 1 hour).
    anthropic_api_key : str | None
        Anthropic API key. If None, Claude fallback is disabled.
    model : str
        Claude model for query generation fallback.
    """

    def __init__(
        self,
        cache_ttl: int = 3600,
        anthropic_api_key: str | None = None,
        model: str = "claude-3-5-haiku-latest",
    ) -> None:
        self._cache = QueryCache(ttl_seconds=cache_ttl)
        self._model = model
        self._client = None

        if anthropic_api_key is not None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=anthropic_api_key)

    async def build_query(self, candidate: "MarketCandidate") -> str:
        """
        Generate a search query for the given MarketCandidate.

        Tries in order:
          1. Return cached result (if within TTL)
          2. Template extraction (common Kalshi title patterns)
          3. Claude API (if key available)
          4. Keyword extraction (always works)

        Result is stored in cache before returning.
        """
        cached = self._cache.get(candidate.ticker)
        if cached is not None:
            logger.debug("Query cache hit", ticker=candidate.ticker)
            return cached

        query = self._template_query(candidate)

        if query is None:
            logger.debug(
                "Template failed — falling back",
                ticker=candidate.ticker,
                has_claude=self._client is not None,
            )
            if self._client is not None:
                query = await self._claude_query(candidate)
            else:
                query = self._keyword_query(candidate)

        self._cache.set(candidate.ticker, query)
        logger.debug("Query generated and cached", ticker=candidate.ticker, query=query)
        return query

    # ------------------------------------------------------------------
    # Template matching
    # ------------------------------------------------------------------

    def _template_query(self, candidate: "MarketCandidate") -> str | None:
        """
        Extract a search query using pattern matching on common title structures.

        Returns a query string if a pattern matches with >2 meaningful words,
        else returns None to signal template failure.
        """
        title = candidate.title

        # Pattern: "Will X (happen|be|...)?"
        will_match = re.match(
            r"Will\s+(.+?)(?:\s+(?:happen|occur|pass|win|be|get|reach|exceed|fall|rise|drop|increase|decrease))?[?]?$",
            title,
            re.IGNORECASE,
        )
        if will_match:
            extracted = will_match.group(1).strip().rstrip("?")
            if self._is_meaningful(extracted):
                # For election/vote titles, append "polls results"
                if re.search(r"\b(election|vote|primary|referendum)\b", extracted, re.IGNORECASE):
                    return f"{extracted} election polls results"
                # For price/above/below titles
                if re.search(r"\b(price|above|below|\$|bitcoin|btc|eth)\b", extracted, re.IGNORECASE):
                    # Extract asset name
                    asset_match = re.search(r"(bitcoin|btc|ethereum|eth|gold|oil|silver|nasdaq|s&p)", extracted, re.IGNORECASE)
                    asset = asset_match.group(1) if asset_match else extracted
                    return f"{asset} price forecast"
                return extracted

        # Pattern: "Will ... price be above/below ..."
        price_match = re.match(
            r"Will\s+(.+?)\s+(?:price\s+)?(?:be\s+)?(?:above|below|reach|exceed|hit)\s+",
            title,
            re.IGNORECASE,
        )
        if price_match:
            asset = price_match.group(1).strip()
            if self._is_meaningful(asset):
                return f"{asset} price forecast"

        # Pattern: "X election/vote" anywhere in title
        election_match = re.search(
            r"(\w+(?:\s+\w+){0,3})\s+(?:presidential\s+)?(?:election|vote|primary|referendum)",
            title,
            re.IGNORECASE,
        )
        if election_match:
            subject = election_match.group(1).strip()
            if self._is_meaningful(subject):
                return f"{subject} election polls results"

        # Generic: strip stopwords from title
        keywords = self._extract_keywords(title)
        if len(keywords) > 2:
            return " ".join(keywords)

        return None  # Template failed

    def _is_meaningful(self, text: str) -> bool:
        """Return True if text has more than 2 meaningful (non-stopword) words."""
        words = [w.lower().strip("?.,!") for w in text.split()]
        meaningful = [w for w in words if w and w not in _STOPWORDS]
        return len(meaningful) > 2

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords by filtering stopwords."""
        words = re.findall(r"[a-zA-Z0-9$%]+", text)
        return [w for w in words if w.lower() not in _STOPWORDS and len(w) > 1]

    def _keyword_query(self, candidate: "MarketCandidate") -> str:
        """
        Simple keyword extraction from title + category.

        Used when template fails and no Claude client is available.
        Always produces a non-empty result.
        """
        keywords = self._extract_keywords(candidate.title)
        if not keywords:
            # Last resort: use category + ticker
            return f"{candidate.category} {candidate.ticker}"
        return " ".join(keywords[:8])  # cap at 8 terms

    # ------------------------------------------------------------------
    # Claude fallback
    # ------------------------------------------------------------------

    async def _claude_query(self, candidate: "MarketCandidate") -> str:
        """Call Claude to generate a search query for an unusual market title."""
        prompt = (
            "Generate a concise web search query (5-10 words) for researching the outcome "
            "of the following prediction market. Return ONLY the search query, no explanation.\n\n"
            f"Title: {candidate.title}\n"
            f"Category: {candidate.category}"
        )

        message = await self._client.messages.create(
            model=self._model,
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )

        return message.content[0].text.strip()
