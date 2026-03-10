"""
RSSAgent — fetches RSS/Atom feeds and classifies article sentiment.

Strategy:
  1. Look up feed URLs for candidate.category (fallback to "general")
  2. For each feed URL: fetch HTML with httpx, parse with feedparser.parse(text)
     (NEVER pass URL directly to feedparser — it uses urllib.request which blocks the event loop)
  3. Filter entries by query keyword relevance (substring match on title)
  4. Classify each relevant entry's title+summary through SentimentClassifier
  5. Return SourceResult

Uses tenacity retry for transient HTTP failures (same exponential backoff pattern
as kalshi_retry in the Kalshi client).
"""
from __future__ import annotations

import feedparser
import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from pmtb.research.models import SourceResult
from pmtb.research.sentiment import SentimentClassifier
from pmtb.scanner.models import MarketCandidate

_DEFAULT_CATEGORY = "general"
_HTTP_TIMEOUT = 15.0


def _make_fetch_with_retry():
    """Build a retrying HTTP fetch function."""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _fetch_url(client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url, timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
        return response.text

    return _fetch_url


_fetch_url_with_retry = _make_fetch_with_retry()


class RSSAgent:
    """
    Fetches RSS/Atom feeds and classifies article sentiment.

    Implements the ResearchAgent Protocol.
    """

    source_name = "rss"

    def __init__(
        self,
        classifier: SentimentClassifier,
        feeds_by_category: dict[str, list[str]],
        results_limit: int = 10,
    ) -> None:
        self._classifier = classifier
        self._feeds_by_category = feeds_by_category
        self._results_limit = results_limit

    async def fetch(self, candidate: MarketCandidate, query: str) -> SourceResult:
        """
        Fetch RSS feeds for the candidate category and classify article sentiment.

        Returns empty SourceResult if no feeds configured for category.
        """
        log = logger.bind(source="rss", ticker=candidate.ticker)

        feed_urls: list[str] = self._feeds_by_category.get(
            candidate.category,
            self._feeds_by_category.get(_DEFAULT_CATEGORY, []),
        )

        if not feed_urls:
            log.debug("No RSS feeds configured for category — returning empty", category=candidate.category)
            return SourceResult(source="rss", signals=[])

        log.debug("Fetching RSS feeds", feed_count=len(feed_urls), query=query)

        all_entries: list[dict] = []
        fetched_urls: list[str] = []

        async with httpx.AsyncClient(follow_redirects=True) as client:
            for url in feed_urls:
                try:
                    text = await _fetch_url_with_retry(client, url)
                    feed = feedparser.parse(text)
                    fetched_urls.append(url)

                    query_lower = query.lower()
                    for entry in feed.entries:
                        title = getattr(entry, "title", "") or ""
                        # Filter by keyword relevance (simple substring match)
                        if query_lower in title.lower():
                            summary = getattr(entry, "summary", "") or ""
                            all_entries.append({"title": title, "summary": summary})
                            if len(all_entries) >= self._results_limit:
                                break

                except Exception as exc:
                    log.warning("Failed to fetch RSS feed", url=url, error=str(exc))

        if not all_entries:
            log.debug("No matching RSS entries found", query=query)
            return SourceResult(
                source="rss",
                signals=[],
                raw_data={"feed_urls": fetched_urls, "entry_count": 0},
            )

        # Classify each entry's title + summary
        signals = []
        for entry in all_entries:
            text = f"{entry['title']} {entry['summary']}".strip()
            try:
                signal = await self._classifier.classify(text)
                signals.append(signal)
            except Exception as exc:
                log.warning("Classification error for RSS entry", title=entry["title"][:60], error=str(exc))

        log.info("RSS fetch complete", entry_count=len(all_entries), signal_count=len(signals))

        return SourceResult(
            source="rss",
            signals=signals,
            raw_data={"feed_urls": fetched_urls, "entry_count": len(all_entries)},
        )
