"""Expose source capabilities independently of research responsibilities."""

from research_agent.tools.web import (
    SearchHit,
    SearchResults,
    WebSearch,
    tavily_search,
    think_tool,
)

__all__ = ["SearchHit", "SearchResults", "WebSearch", "tavily_search", "think_tool"]
