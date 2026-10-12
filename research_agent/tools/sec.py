"""Resolve SEC identities and retrieve documents within safe accession boundaries."""

import logging
import re
import threading
import time
from urllib.parse import urlsplit

from pydantic import ValidationError

from research_agent.config import ResearchError, Settings
from research_agent.tools.contracts import CompanyIdentity
from research_agent.tools.webpage import FetchedPage, HttpFetcher

logger = logging.getLogger(__name__)
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_ROOT = "https://data.sec.gov/submissions/"
ARCHIVES_ROOT = "https://www.sec.gov/Archives/edgar/data/"


def _normalize_name(value: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", value.casefold()).split()
    while words and words[-1] in {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "ltd",
        "plc",
    }:
        words.pop()
    return " ".join(words)


def _validate_url(url: str, expected: str, *, directory: bool = False) -> None:
    try:
        parsed = urlsplit(url)
        reference = urlsplit(expected)
        valid_port = parsed.port in {None, 443}
    except ValueError:
        raise ResearchError(
            "unsafe_sec_url", "SEC document URL is malformed."
        ) from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != reference.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not valid_port
        or parsed.query
        or parsed.fragment
    ):
        raise ResearchError(
            "unsafe_sec_url", "SEC access must remain on the expected HTTPS endpoint."
        )
    if directory:
        filename = parsed.path.removeprefix(reference.path)
        matches = parsed.path.startswith(reference.path) and re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", filename
        )
    else:
        matches = parsed.path == reference.path
    if not matches:
        raise ResearchError(
            "unsafe_sec_url",
            "SEC document must belong to the expected accession directory.",
        )


class SecClient:
    """Share one SEC request limiter across a research run's workers."""

    def __init__(self, settings: Settings, fetcher: HttpFetcher):
        """Use injected HTTP transport and defer all network access."""
        self.settings = settings
        self.fetcher = fetcher
        self._request_lock = threading.Lock()
        self._last_request_at: float | None = None
        self._companies: list[CompanyIdentity] | None = None

    def _before_request(
        self, url: str, expected: str, *, directory: bool = False
    ) -> None:
        _validate_url(url, expected, directory=directory)
        self.settings.require_sec_user_agent()
        with self._request_lock:
            if self._last_request_at is not None:
                delay = self.settings.sec_interval_seconds - (
                    time.monotonic() - self._last_request_at
                )
                if delay > 0:
                    time.sleep(delay)
            self._last_request_at = time.monotonic()

    def _get_json(self, url: str) -> dict:
        return self.fetcher.get_json(
            url,
            headers={"User-Agent": self.settings.require_sec_user_agent()},
            before_request=lambda current_url: self._before_request(current_url, url),
        )

    def _get_document(self, url: str, expected: str) -> FetchedPage:
        _validate_url(url, expected, directory=True)
        page = self.fetcher.get(
            url,
            headers={"User-Agent": self.settings.require_sec_user_agent()},
            before_request=lambda current_url: self._before_request(
                current_url, expected, directory=True
            ),
        )
        _validate_url(page.url, expected, directory=True)
        return page

    def resolve_company(self, name_or_ticker: str) -> CompanyIdentity:
        """Resolve a ticker or unambiguous normalized company-name prefix."""
        query = name_or_ticker.strip()
        if not query or len(query) > 200:
            raise ResearchError(
                "invalid_company",
                "Provide a company name or ticker of at most 200 characters.",
            )
        if self._companies is None:
            payload = self._get_json(TICKERS_URL)
            try:
                companies = []
                for record in payload.values():
                    cik = str(record["cik_str"])
                    if not cik.isdigit() or not 0 < int(cik) < 10**10:
                        raise ValueError("Invalid CIK")
                    companies.append(
                        CompanyIdentity(
                            name=record["title"],
                            ticker=record["ticker"],
                            cik=cik.zfill(10),
                        )
                    )
                if not companies:
                    raise ValueError("Empty ticker registry")
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                raise ResearchError(
                    "sec_parse_error",
                    "SEC company ticker data has an unexpected structure.",
                ) from error
            self._companies = companies
        matches = [
            company for company in self._companies if company.ticker == query.upper()
        ]
        normalized = _normalize_name(query)
        if not matches and normalized:
            matches = [
                company
                for company in self._companies
                if _normalize_name(company.name) == normalized
            ]
        if not matches and normalized:
            matches = [
                company
                for company in self._companies
                if _normalize_name(company.name).startswith(normalized + " ")
            ]
        if not matches:
            raise ResearchError(
                "company_not_found",
                "No SEC-listed company matches the supplied name or ticker.",
            )
        if len(matches) != 1:
            raise ResearchError(
                "ambiguous_company",
                "The company name is ambiguous; provide its exact ticker.",
            )
        logger.info(
            "sec_company_resolved",
            extra={"ticker": matches[0].ticker, "cik": matches[0].cik},
        )
        return matches[0]
