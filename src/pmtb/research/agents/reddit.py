"""
RedditAgent — fetches posts from category-mapped subreddits and Reddit search.

Strategy:
  1. For each subreddit mapped to candidate.category, fetch hot posts
  2. Also search r/all for the query string
  3. Classify each post title through the SentimentClassifier
  4. Return SourceResult with all classifications

When client_id is None, returns an empty SourceResult immediately (no error).
"""
from __future__ import annotations

import asyncio

import asyncpraw
from loguru import logger

from pmtb.research.models import SourceResult
from pmtb.research.sentiment import SentimentClassifier
from pmtb.scanner.models import MarketCandidate

# Category → subreddit mapping
_CATEGORY_SUBREDDITS: dict[str, list[str]] = {
    "politics": ["politics", "PoliticalDiscussion"],
    "economics": ["economics", "finance"],
    "finance": ["wallstreetbets", "stocks", "investing"],
    "sports": ["sports", "sportsbook"],
    "weather": ["weather"],
    "science": ["science"],
    "general": ["news", "worldnews"],
}
_DEFAULT_CATEGORY = "general"


class RedditAgent:
    """
    Fetches posts from Reddit and classifies sentiment.

    Implements the ResearchAgent Protocol.
    """

    source_name = "reddit"

    def __init__(
        self,
        classifier: SentimentClassifier,
        client_id: str | None,
        client_secret: str | None,
        user_agent: str,
        results_limit: int = 10,
    ) -> None:
        self._classifier = classifier
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._results_limit = results_limit

    async def fetch(self, candidate: MarketCandidate, query: str) -> SourceResult:
        """
        Fetch Reddit posts for the candidate and classify sentiment.

        Returns empty SourceResult if credentials are missing.
        """
        log = logger.bind(source="reddit", ticker=candidate.ticker)

        if not self._client_id:
            log.debug("No Reddit credentials — returning empty SourceResult")
            return SourceResult(source="reddit", signals=[])

        subreddits = _CATEGORY_SUBREDDITS.get(candidate.category, _CATEGORY_SUBREDDITS[_DEFAULT_CATEGORY])
        log.debug("Fetching Reddit posts", subreddits=subreddits, query=query)

        post_titles: list[str] = []
        try:
            async with asyncpraw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                user_agent=self._user_agent,
            ) as reddit:
                # Fetch hot posts from each mapped subreddit
                for sub_name in subreddits:
                    sub = await reddit.subreddit(sub_name)
                    async for post in sub.hot(limit=self._results_limit):
                        post_titles.append(post.title)

                # Also search r/all for the query
                all_sub = await reddit.subreddit("all")
                async for post in all_sub.search(query, limit=self._results_limit):
                    post_titles.append(post.title)

        except Exception as exc:
            log.warning("Reddit fetch error — returning empty SourceResult", error=str(exc))
            return SourceResult(source="reddit", signals=[])

        # Classify all titles concurrently
        signals = []
        for title in post_titles:
            try:
                signal = await self._classifier.classify(title)
                signals.append(signal)
            except Exception as exc:
                log.warning("Classification error for post title", title=title[:60], error=str(exc))

        log.info("Reddit fetch complete", post_count=len(post_titles), signal_count=len(signals))

        return SourceResult(
            source="reddit",
            signals=signals,
            raw_data={
                "subreddits_searched": subreddits + ["all"],
                "post_titles": post_titles[: self._results_limit],
                "total_posts": len(post_titles),
            },
        )
