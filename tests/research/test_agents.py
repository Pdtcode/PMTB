"""
Tests for research agents: Reddit, RSS, Trends, and Twitter stub.

All agents must:
  - Implement ResearchAgent Protocol (isinstance check)
  - Return SourceResult (never raise) on both success and failure paths
  - Handle missing credentials / empty data gracefully
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from pmtb.research.agent import ResearchAgent
from pmtb.research.models import SourceResult
from pmtb.research.sentiment import SentimentClassifier
from pmtb.scanner.models import MarketCandidate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def candidate() -> MarketCandidate:
    return MarketCandidate(
        ticker="TEST-XYZ",
        title="Will something happen?",
        category="politics",
        event_context={},
        close_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        yes_bid=0.45,
        yes_ask=0.50,
        implied_probability=0.475,
        spread=0.05,
        volume_24h=10000.0,
    )


@pytest.fixture
def classifier() -> SentimentClassifier:
    """VADER-only classifier, no Anthropic API key."""
    return SentimentClassifier()


# ---------------------------------------------------------------------------
# RedditAgent tests
# ---------------------------------------------------------------------------


class TestRedditAgent:
    def test_isinstance_research_agent(self, classifier: SentimentClassifier) -> None:
        from pmtb.research.agents.reddit import RedditAgent

        agent = RedditAgent(
            classifier=classifier,
            client_id="cid",
            client_secret="csec",
            user_agent="pmtb/1.0",
        )
        assert isinstance(agent, ResearchAgent)

    def test_source_name(self, classifier: SentimentClassifier) -> None:
        from pmtb.research.agents.reddit import RedditAgent

        agent = RedditAgent(
            classifier=classifier,
            client_id="cid",
            client_secret="csec",
            user_agent="pmtb/1.0",
        )
        assert agent.source_name == "reddit"

    def test_fetch_no_credentials_returns_empty(
        self, candidate: MarketCandidate, classifier: SentimentClassifier
    ) -> None:
        from pmtb.research.agents.reddit import RedditAgent

        agent = RedditAgent(
            classifier=classifier,
            client_id=None,
            client_secret=None,
            user_agent="pmtb/1.0",
        )
        result = asyncio.get_event_loop().run_until_complete(
            agent.fetch(candidate, "test query")
        )
        assert isinstance(result, SourceResult)
        assert result.source == "reddit"
        assert result.signals == []

    def test_fetch_with_credentials_returns_source_result(
        self, candidate: MarketCandidate, classifier: SentimentClassifier
    ) -> None:
        from pmtb.research.agents.reddit import RedditAgent

        agent = RedditAgent(
            classifier=classifier,
            client_id="cid",
            client_secret="csec",
            user_agent="pmtb/1.0",
            results_limit=2,
        )

        # Mock asyncpraw.Reddit context manager
        mock_post = MagicMock()
        mock_post.title = "Stock market surges to all-time high"

        mock_subreddit = AsyncMock()
        mock_subreddit.hot = AsyncMock()

        async def mock_hot(limit):
            for _ in range(2):
                yield mock_post

        mock_subreddit.hot = mock_hot

        mock_all_subreddit = AsyncMock()

        async def mock_search(query, limit):
            yield mock_post

        mock_all_subreddit.search = mock_search

        mock_reddit_instance = AsyncMock()
        mock_reddit_instance.subreddit = AsyncMock(
            side_effect=lambda name: mock_all_subreddit if name == "all" else mock_subreddit
        )
        mock_reddit_instance.__aenter__ = AsyncMock(return_value=mock_reddit_instance)
        mock_reddit_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("pmtb.research.agents.reddit.asyncpraw.Reddit", return_value=mock_reddit_instance):
            result = asyncio.get_event_loop().run_until_complete(
                agent.fetch(candidate, "test query")
            )

        assert isinstance(result, SourceResult)
        assert result.source == "reddit"
        assert isinstance(result.signals, list)


# ---------------------------------------------------------------------------
# RSSAgent tests
# ---------------------------------------------------------------------------


class TestRSSAgent:
    def test_isinstance_research_agent(self, classifier: SentimentClassifier) -> None:
        from pmtb.research.agents.rss import RSSAgent

        agent = RSSAgent(classifier=classifier, feeds_by_category={})
        assert isinstance(agent, ResearchAgent)

    def test_source_name(self, classifier: SentimentClassifier) -> None:
        from pmtb.research.agents.rss import RSSAgent

        agent = RSSAgent(classifier=classifier, feeds_by_category={})
        assert agent.source_name == "rss"

    def test_fetch_no_feeds_returns_empty(
        self, candidate: MarketCandidate, classifier: SentimentClassifier
    ) -> None:
        from pmtb.research.agents.rss import RSSAgent

        agent = RSSAgent(classifier=classifier, feeds_by_category={})
        result = asyncio.get_event_loop().run_until_complete(
            agent.fetch(candidate, "test query")
        )
        assert isinstance(result, SourceResult)
        assert result.source == "rss"
        assert result.signals == []

    def test_fetch_with_feeds_returns_source_result(
        self, candidate: MarketCandidate, classifier: SentimentClassifier
    ) -> None:
        from pmtb.research.agents.rss import RSSAgent

        feeds = {"politics": ["https://example.com/feed.rss"]}
        agent = RSSAgent(classifier=classifier, feeds_by_category=feeds, results_limit=5)

        mock_response = MagicMock()
        mock_response.text = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Test Feed</title>
<item><title>Politics news today raises concerns</title><description>Some news about politics</description></item>
</channel></rss>"""
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch("pmtb.research.agents.rss.httpx.AsyncClient", return_value=mock_http_client):
            result = asyncio.get_event_loop().run_until_complete(
                agent.fetch(candidate, "politics")
            )

        assert isinstance(result, SourceResult)
        assert result.source == "rss"
        assert isinstance(result.signals, list)


# ---------------------------------------------------------------------------
# TrendsAgent tests
# ---------------------------------------------------------------------------


class TestTrendsAgent:
    def test_isinstance_research_agent(self, classifier: SentimentClassifier) -> None:
        from pmtb.research.agents.trends import TrendsAgent

        agent = TrendsAgent(classifier=classifier)
        assert isinstance(agent, ResearchAgent)

    def test_source_name(self, classifier: SentimentClassifier) -> None:
        from pmtb.research.agents.trends import TrendsAgent

        agent = TrendsAgent(classifier=classifier)
        assert agent.source_name == "trends"

    def test_fetch_empty_dataframe_returns_empty(
        self, candidate: MarketCandidate, classifier: SentimentClassifier
    ) -> None:
        from pmtb.research.agents.trends import TrendsAgent

        agent = TrendsAgent(classifier=classifier)

        mock_trend_req = MagicMock()
        mock_trend_req.build_payload = MagicMock()
        mock_trend_req.interest_over_time = MagicMock(return_value=pd.DataFrame())
        mock_trend_req.related_queries = MagicMock(return_value={})

        with patch("pmtb.research.agents.trends.TrendReq", return_value=mock_trend_req):
            result = asyncio.get_event_loop().run_until_complete(
                agent.fetch(candidate, "test query")
            )

        assert isinstance(result, SourceResult)
        assert result.source == "trends"
        assert result.signals == []

    def test_fetch_with_data_returns_source_result(
        self, candidate: MarketCandidate, classifier: SentimentClassifier
    ) -> None:
        from pmtb.research.agents.trends import TrendsAgent

        agent = TrendsAgent(classifier=classifier)

        # Create a DataFrame with 14 days of trend data (ascending = bullish momentum)
        dates = pd.date_range("2026-01-01", periods=14, freq="D")
        data = pd.DataFrame({"test query": list(range(14))}, index=dates)

        mock_trend_req = MagicMock()
        mock_trend_req.build_payload = MagicMock()
        mock_trend_req.interest_over_time = MagicMock(return_value=data)
        mock_trend_req.related_queries = MagicMock(return_value={"test query": {"top": None, "rising": None}})

        with patch("pmtb.research.agents.trends.TrendReq", return_value=mock_trend_req):
            result = asyncio.get_event_loop().run_until_complete(
                agent.fetch(candidate, "test query")
            )

        assert isinstance(result, SourceResult)
        assert result.source == "trends"
        assert len(result.signals) == 1
        assert result.signals[0].sentiment in ("bullish", "bearish", "neutral")


# ---------------------------------------------------------------------------
# TwitterAgent tests
# ---------------------------------------------------------------------------


class TestTwitterAgent:
    def test_isinstance_research_agent(self) -> None:
        from pmtb.research.agents.twitter import TwitterAgent

        agent = TwitterAgent()
        assert isinstance(agent, ResearchAgent)

    def test_source_name(self) -> None:
        from pmtb.research.agents.twitter import TwitterAgent

        agent = TwitterAgent()
        assert agent.source_name == "twitter"

    def test_fetch_returns_empty_source_result(self, candidate: MarketCandidate) -> None:
        from pmtb.research.agents.twitter import TwitterAgent

        agent = TwitterAgent()
        result = asyncio.get_event_loop().run_until_complete(
            agent.fetch(candidate, "test query")
        )
        assert isinstance(result, SourceResult)
        assert result.source == "twitter"
        assert result.signals == []

    def test_fetch_does_not_raise(self, candidate: MarketCandidate) -> None:
        from pmtb.research.agents.twitter import TwitterAgent

        agent = TwitterAgent()
        # Should not raise any exception
        result = asyncio.get_event_loop().run_until_complete(
            agent.fetch(candidate, "test query")
        )
        assert result is not None

    def test_raw_data_has_stub_flag(self, candidate: MarketCandidate) -> None:
        from pmtb.research.agents.twitter import TwitterAgent

        agent = TwitterAgent()
        result = asyncio.get_event_loop().run_until_complete(
            agent.fetch(candidate, "test query")
        )
        assert result.raw_data is not None
        assert result.raw_data.get("stub") is True
