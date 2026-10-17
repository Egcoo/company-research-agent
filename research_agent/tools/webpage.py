"""Fetch bounded public documents and extract traceable reading snippets."""

import ipaddress
import json
import logging
import re
import socket
import time
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import Field

from research_agent.config import ResearchBudget, ResearchError, Settings
from research_agent.tools.contracts import Contract, TextSnippet

logger = logging.getLogger(__name__)


class FetchedPage(Contract):
    """Keep the final public URL beside its bounded response body."""

    url: str
    text: str
    content_type: str
    body: bytes = Field(default=b"", exclude=True, repr=False)


class ExtractedContent(Contract):
    """Return readable excerpts without pretending to provide filing RAG."""

    title: str
    snippets: list[TextSnippet] = Field(max_length=30)
    truncated: bool
    full_text: str = Field(default="", exclude=True)


def _resolve_host(host: str) -> list[str]:
    """Resolve all addresses so mixed public/private DNS answers are rejected."""
    return list({record[4][0] for record in socket.getaddrinfo(host, None)})


class HttpFetcher:
    """Enforce public HTTP destinations, response limits and finite retries."""

    def __init__(
        self,
        settings: Settings,
        budget: ResearchBudget,
        client: httpx.Client | None = None,
        resolve_host: Callable[[str], Iterable[str]] = _resolve_host,
    ):
        """Accept a client and DNS resolver for entirely offline tests."""
        self.settings = settings
        self.budget = budget
        self.client = client or httpx.Client(
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self.resolve_host = resolve_host
        self._owns_client = client is None

    def close(self) -> None:
        """Release a client created by this fetcher."""
        if self._owns_client:
            self.client.close()

    def _destination(self, url: str) -> tuple[httpx.URL, str, str]:
        """Pin connections to a validated public IP while preserving TLS SNI."""
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in {None, 80, 443}
                or len(url) > 4096
            ):
                raise ValueError("Unsupported URL")
            host = parsed.hostname
            try:
                addresses = [str(ipaddress.ip_address(host))]
            except ValueError:
                addresses = list(self.resolve_host(host))
            if not addresses or any(
                not ipaddress.ip_address(address).is_global for address in addresses
            ):
                raise ValueError("Non-public destination")
            destination = httpx.URL(url)
            authority = destination.netloc.decode("ascii")
            return destination.copy_with(host=addresses[0]), authority, host
        except (ValueError, httpx.InvalidURL, OSError):
            raise ResearchError(
                "unsafe_url", "A public HTTP(S) destination is required."
            ) from None

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        content_types: tuple[str, ...] = ("text/html", "application/xhtml+xml"),
        before_request: Callable[[str], None] | None = None,
    ) -> FetchedPage:
        """Fetch bounded HTML with a timeout, safe redirects and finite retries."""
        current_url = url
        request_headers = httpx.Headers(headers or {})
        for redirect_count in range(6):
            destination, authority, host = self._destination(current_url)
            request_headers["Host"] = authority
            for attempt in range(self.settings.max_retries + 1):
                request_headers["Cookie"] = ""
                request_headers["Accept-Encoding"] = "identity"
                self.budget.consume("request")
                if before_request:
                    before_request(current_url)
                self.budget.check()
                try:
                    with self.client.stream(
                        "GET",
                        destination,
                        headers=request_headers,
                        timeout=self.settings.timeout_seconds,
                        follow_redirects=False,
                        extensions={"sni_hostname": host},
                    ) as response:
                        status = response.status_code
                        logger.info(
                            "document_request status=%s attempt=%s", status, attempt + 1
                        )
                        if status == 429 or 500 <= status <= 599:
                            if attempt < self.settings.max_retries:
                                time.sleep(0.1 * (attempt + 1))
                                continue
                        if status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location or redirect_count == 5:
                                raise ResearchError(
                                    "redirect_error",
                                    "Document redirect limit or target is invalid.",
                                )
                            try:
                                target = urljoin(current_url, location)
                                changed_host = (
                                    urlsplit(target).netloc
                                    != urlsplit(current_url).netloc
                                )
                            except ValueError:
                                raise ResearchError(
                                    "redirect_error",
                                    "The document redirect target is invalid.",
                                ) from None
                            if changed_host:
                                for name in ("authorization", "cookie", "host"):
                                    request_headers.pop(name, None)
                            current_url = target
                            break
                        if status < 200 or status >= 300:
                            raise ResearchError(
                                f"http_{status}",
                                f"Document request failed with HTTP {status}.",
                            )
                        content_encoding = (
                            response.headers.get("content-encoding", "identity")
                            .strip()
                            .lower()
                        )
                        if content_encoding not in {"", "identity"}:
                            raise ResearchError(
                                "unsupported_content_encoding",
                                "The document must use identity content encoding to enforce the response limit.",
                            )
                        content_type = (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if content_type not in content_types:
                            raise ResearchError(
                                "unsupported_content_type",
                                "The document is not a supported content type.",
                            )
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            self.budget.check()
                            if (
                                len(body) + len(chunk)
                                > self.settings.max_response_bytes
                            ):
                                raise ResearchError(
                                    "response_too_large",
                                    "The document exceeds the configured response limit.",
                                )
                            body.extend(chunk)
                        try:
                            text = bytes(body).decode(
                                response.encoding or "utf-8", errors="replace"
                            )
                        except LookupError:
                            raise ResearchError(
                                "parse_error",
                                "The document declares an unsupported encoding.",
                            ) from None
                        return FetchedPage(
                            url=current_url,
                            text=text,
                            content_type=content_type,
                            body=bytes(body),
                        )
                except httpx.TransportError:
                    logger.warning("document_transport_error attempt=%s", attempt + 1)
                    if attempt == self.settings.max_retries:
                        raise ResearchError(
                            "transport_error",
                            "Document request failed after bounded retries.",
                        ) from None
                    time.sleep(0.1 * (attempt + 1))
        raise ResearchError("redirect_error", "Document redirect limit exceeded.")

    def get_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        before_request: Callable[[str], None] | None = None,
    ) -> dict:
        """Fetch a bounded JSON object and report malformed responses safely."""
        page = self.get(url, headers, ("application/json", "text/json"), before_request)
        try:
            payload = json.loads(page.text)
        except (ValueError, RecursionError):
            raise ResearchError(
                "parse_error", "The document contains invalid JSON."
            ) from None
        if not isinstance(payload, dict):
            raise ResearchError(
                "parse_error", "The JSON response must contain an object."
            )
        return payload


def extract_web_content(
    page: FetchedPage, max_chars: int, *, focus: str = ""
) -> ExtractedContent:
    """Read bounded excerpts from the start or first literal focus match."""
    if max_chars < 1:
        raise ResearchError(
            "invalid_input", "The extraction character limit must be positive."
        )
    if not isinstance(focus, str) or len(focus) > 200:
        raise ResearchError(
            "invalid_input", "The reading focus must contain at most 200 characters."
        )
    if page.content_type not in {"text/html", "application/xhtml+xml"}:
        raise ResearchError(
            "unsupported_content_type", "Only HTML documents can be extracted."
        )
    soup = BeautifulSoup(page.text, "html.parser")
    title = (
        soup.title.get_text(" ", strip=True)[:300]
        if soup.title
        else "Untitled document"
    )
    for element in soup.select(
        "head, title, script, style, nav, header, footer, noscript, svg, form, iframe, [hidden], "
        "ix\\:header, ix\\:hidden, xbrli\\:context, xbrli\\:unit, context, unit"
    ):
        element.decompose()
    for element in soup.find_all(
        style=re.compile(
            r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)"
            r"\s*(?:!\s*important\s*)?(?:;|$)",
            re.IGNORECASE,
        )
    ):
        element.decompose()
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if cells and row.parent is not None:
            row.replace_with(
                "\n"
                + " | ".join(cell.get_text(" ", strip=True) for cell in cells)
                + "\n"
            )
    for element in soup.find_all(
        ["p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "br", "table"]
    ):
        element.insert_before("\n")
        element.insert_after("\n")
    text = "\n".join(
        normalized
        for line in (soup.body or soup).get_text(" ", strip=False).splitlines()
        if (normalized := " ".join(line.split()))
    )
    if not text:
        raise ResearchError("empty_content", "The document has no readable content.")
    maximum = min(max_chars, 30 * 6000)
    start = 0
    if focus:
        match = re.search(re.escape(focus), text, re.IGNORECASE)
        if match is None:
            raise ResearchError(
                "text_not_found", "The reading focus was not found in the document."
            )
        start = match.start() // 6000 * 6000
        if match.end() > start + maximum:
            raise ResearchError(
                "invalid_input",
                "The reading limit is too small to include the focus in its aligned excerpt.",
            )
    end = min(start + maximum, len(text))
    snippets = [
        TextSnippet(
            snippet_id=f"p{offset // 6000 + 1}"
            + (f"@{end}" if offset + 6000 > end and end < len(text) else ""),
            locator=f"extracted_text_chars:{offset}:{min(offset + 6000, end)}",
            content=text[offset : min(offset + 6000, end)],
        )
        for offset in range(start, end, 6000)
    ]
    return ExtractedContent(
        title=title,
        snippets=snippets,
        truncated=start > 0 or end < len(text),
        full_text=text,
    )
