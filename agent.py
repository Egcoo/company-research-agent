"""Expose a run-scoped DeepEquity graph factory for the existing LangGraph entry."""

from contextlib import contextmanager

from langsmith import tracing_context

from research_agent.config import Settings
from research_agent.workflow import (
    ResearchRun as ResearchSession,
)
from research_agent.workflow import create_research_agent


@contextmanager
def agent():
    """Build and release a fresh research session for each Studio execution."""
    settings = Settings.from_env()
    session = ResearchSession(settings)
    try:
        with tracing_context(enabled=session.settings.tracing):
            yield create_research_agent(session)
    finally:
        session.close()
