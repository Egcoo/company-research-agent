"""Discover bounded web results without treating search snippets as evidence."""

import logging
import time
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import urlsplit

import requests
from pydantic import Field, ValidationError
from tavily import TavilyClient
from tavily.errors import TimeoutError as TavilyTimeoutError
from tavily.errors import UsageLimitExceededError

from research_agent.config import ResearchBudget, ResearchError, Settings
from research_agent.tools.contracts import Contract

logger = logging.getLogger(__name__)


class SearchHit(Contract):
    """Describe a discovered URL that still needs fetching before citation."""

    title: str = Field(max_length=300)
    url: str = Field(max_length=4096)
    snippet: str = Field(max_length=2000)
    published_at: date | None = None


class SearchResults(Contract):
    """Distinguish empty discovery from an API or parsing failure."""

    results: list[SearchHit] = Field(max_length=10)
    status: Literal["ok", "empty"]
    truncated: bool = False


def _publication_date(value: object) -> date | None:
    """Accept provider ISO and RFC publication dates without inventing one."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        try:
            return parsedate_to_datetime(value).date()
        except (ValueError, TypeError, OverflowError):
            return None


class WebSearch:
    """Use explicit Tavily credentials, request limits and bounded responses."""

    def __init__(self, settings: Settings, budget: ResearchBudget, client=None):
        """Accept a fake SDK client without constructing clients at import."""
        self.settings = settings
        self.budget = budget
        self.client = client
        self._owns_client = client is None

    def close(self) -> None:
        """Release an SDK session created for this research run."""
        if self._owns_client and self.client is not None:
            self.client.close()

    def search(
        self,
        query: str,
        topic: Literal["general", "news", "finance"] = "general",
        domains: list[str] | None = None,
    ) -> SearchResults:
        """Discover URLs only; fetch selected pages separately before citing."""
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1000:
            raise ResearchError(
                "invalid_input", "Search query must contain 1 to 1000 characters."
            )
        if topic not in {"general", "news", "finance"}:
            raise ResearchError("invalid_input", "Search topic is unsupported.")
        if domains and (
            len(domains) > 5
            or any(
                not domain
                or len(domain) > 253
                or any(character in domain for character in "/:@ ")
                for domain in domains
            )
        ):
            raise ResearchError(
                "invalid_input", "Use at most five plain domain filters."
            )
        api_key = self.settings.require("tavily")
        if self.client is None:
            self.client = TavilyClient(api_key=api_key)
        payload = None
        for attempt in range(self.settings.max_retries + 1):
            self.budget.consume("request")
            try:
                payload = self.client.search(
                    query=query.strip(),
                    topic=topic,
                    include_domains=domains,
                    max_results=self.settings.max_search_results,
                    timeout=self.settings.timeout_seconds,
                    include_answer=False,
                    include_raw_content=False,
                    auto_parameters=False,
                    search_depth="basic",
                )
                self.budget.check()
                break
            except Exception as error:
                if isinstance(error, ResearchError):
                    raise
                status = getattr(getattr(error, "response", None), "status_code", None)
                retryable = (
                    isinstance(
                        error,
                        (
                            TavilyTimeoutError,
                            UsageLimitExceededError,
                            requests.ConnectionError,
                            requests.Timeout,
                        ),
                    )
                    or status == 429
                    or (isinstance(status, int) and status >= 500)
                )
                logger.warning(
                    "search_error attempt=%s retryable=%s", attempt + 1, retryable
                )
                if not retryable or attempt == self.settings.max_retries:
                    raise ResearchError(
                        "search_error",
                        "Web search failed; check provider configuration and availability.",
                    ) from None
                time.sleep(0.1 * (attempt + 1))
        if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list
        ):
            raise ResearchError(
                "parse_error", "Web search returned an invalid results object."
            )
        results = []
        truncated = len(payload["results"]) > self.settings.max_search_results
        try:
            for item in payload["results"][: self.settings.max_search_results]:
                if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                    raise ValueError("Invalid result entry")
                url = item["url"]
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username is not None
                ):
                    raise ValueError("Invalid result URL")
                title = item.get("title") or "Untitled search result"
                snippet = item.get("content") or ""
                if not isinstance(title, str) or not isinstance(snippet, str):
                    raise ValueError("Invalid result text")
                truncated = truncated or len(title) > 300 or len(snippet) > 2000
                results.append(
                    SearchHit(
                        title=title[:300],
                        url=url,
                        snippet=snippet[:2000],
                        published_at=_publication_date(item.get("published_date")),
                    )
                )
        except (KeyError, TypeError, ValueError, ValidationError):
            raise ResearchError(
                "parse_error", "Web search returned an invalid result entry."
            ) from None
        logger.info(
            "web_search_complete results=%s truncated=%s", len(results), truncated
        )
        return SearchResults(
            results=results, status="ok" if results else "empty", truncated=truncated
        )
