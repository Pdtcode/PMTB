"""
Research agents package.

Four agents implement the ResearchAgent Protocol:
  - RedditAgent:  fetches posts from category-mapped subreddits + search
  - RSSAgent:     fetches and parses RSS feeds for the candidate's category
  - TrendsAgent:  derives momentum from Google Trends interest-over-time
  - TwitterAgent: stub that returns empty SourceResult (Twitter/X API deferred)
"""
from __future__ import annotations

from pmtb.research.agents.reddit import RedditAgent
from pmtb.research.agents.rss import RSSAgent
from pmtb.research.agents.trends import TrendsAgent
from pmtb.research.agents.twitter import TwitterAgent

__all__ = [
    "RedditAgent",
    "RSSAgent",
    "TrendsAgent",
    "TwitterAgent",
]
