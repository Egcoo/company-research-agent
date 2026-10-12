"""Acquire sources and retain plain research notes for the stock-report MVP."""

import json
import re
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
from langchain_core.tools import StructuredTool

from research_agent.config import ConfigurationError, ResearchError
from research_agent.tools.research import discover_recent_sec_sources
from research_agent.tools.sec import SecClient, _validate_url
from research_agent.tools.source_dates import source_dates
from research_agent.tools.webpage import extract_web_content

_ERROR_MESSAGES = {
    "acquisition_closed": "Research acquisition has ended; save your notes and use the sources already read.",
    "budget_exceeded": "The run's time or call limit has been reached.",
    "configuration_missing": "This provider is not configured; use available sources and record the gap.",
    "empty_content": "The page has no readable body; it is not an available citation.",
    "future_source": "The source declares publication after the research cutoff; do not use it.",
    "invalid_calculation": "Use 2 to 20 finite numeric inputs and avoid division by zero.",
    "invalid_input": "Check the tool arguments and retry with a valid URL, query, or nonempty note.",
    "invalid_limit": "Use a SEC discovery limit between 1 and 10.",
    "pdf_not_supported": "PDF extraction is unavailable; seek an HTML alternative or record a gap.",
    "sec_tool_required": "Discover SEC filings for the bound ticker before reading their URLs.",
    "storage_error": "The result could not be saved to the run directory.",
    "target_mismatch": "SEC metadata does not match the bound company ticker.",
    "unsafe_sec_url": "The SEC URL must stay in the discovered company's accession directory.",
    "unsafe_url": "Use a public HTTP(S) URL without embedded credentials.",
    "unsupported_content_type": "This source format is unsupported; seek readable HTML or record a gap.",
}


def _arithmetic(
    operation: Literal["add", "subtract", "multiply", "divide"],
    values: list[Decimal | str | int],
) -> tuple[list[Decimal], Decimal]:
    """Evaluate a bounded operation with finite Decimal operands."""
    try:
        operands = [Decimal(str(value)) for value in values]
        if not 2 <= len(operands) <= 20 or any(
            not value.is_finite() for value in operands
        ):
            raise ValueError("Invalid operands")
        result = operands[0]
        for value in operands[1:]:
            if operation == "add":
                result += value
            elif operation == "subtract":
                result -= value
            elif operation == "multiply":
                result *= value
            elif operation == "divide":
                result /= value
            else:
                raise ValueError("Unsupported operation")
        return operands, result
    except (InvalidOperation, ArithmeticError, ValueError):
        raise ResearchError(
            "invalid_calculation",
            "Calculation requires finite inputs and a supported operation without division by zero.",
        ) from None


class MvpTools:
    """Expose URL-based reading and Markdown notes without evidence registration."""

    def __init__(
        self,
        settings,
        budget,
        fetcher,
        search,
        directory,
        as_of: date,
        ticker: str,
        event_callback=None,
        acquisition_allowed: Callable[[], bool] | None = None,
    ):
        """Share bounded provider clients and save each successful reading immediately."""
        self.settings, self.budget = settings, budget
        self.fetcher, self.search = fetcher, search
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.as_of, self.ticker = as_of, ticker.upper().strip()
        self.event_callback = event_callback
        self.acquisition_allowed = acquisition_allowed or (lambda: True)
        self.sources: list[dict] = []
        self.allowed_urls: set[str] = set()
        self.company: dict | None = None
        self.sec = SecClient(settings, fetcher)
        self._identity = None
        self._discovered: dict[str, dict] = {}
        self._pages: dict = {}
        self._lock = threading.RLock()

    def _event(self, tool: str, code: str = "ok") -> None:
        if self.event_callback:
            self.event_callback({"type": "tool_result", "tool": tool, "code": code})

    def _invoke(
        self, name: str, operation: Callable[[], dict], *, acquire=False
    ) -> dict:
        try:
            self.budget.consume("tool")
            if acquire and not self.acquisition_allowed():
                raise ResearchError("acquisition_closed", "Acquisition has ended.")
            result = operation()
        except (ResearchError, ConfigurationError, OSError) as error:
            code = (
                error.code
                if isinstance(error, ResearchError)
                else "configuration_missing"
                if isinstance(error, ConfigurationError)
                else "storage_error"
            )
            # Provider exception text may contain credentials or request details.
            code = code if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "tool_error"
            self._event(name, code)
            return {
                "error": code,
                "message": _ERROR_MESSAGES.get(
                    code,
                    "The source operation failed; preserve existing notes and record this gap.",
                ),
                "gap": True,
            }
        self._event(name)
        return result

    def search_sources(self, query: str, domains: list[str] | None = None) -> dict:
        """Find source URLs; search summaries are discovery hints and cannot be cited."""

        def operation():
            found = self.search.search(query, domains=domains)
            results = []
            with self._lock:
                for hit in found.results:
                    metadata = self._discovered.setdefault(hit.url, {})
                    if metadata.get("published_at") is None:
                        metadata["published_at"] = hit.published_at
                    if hit.published_at is None or hit.published_at <= self.as_of:
                        results.append(
                            hit.model_dump(
                                mode="json", include={"title", "url", "published_at"}
                            )
                            | {"coverage": "discovery_only"}
                        )
            return {
                "results": results,
                "coverage": "discovery_only",
                "summaries_are_evidence": False,
            }

        return self._invoke("search_sources", operation, acquire=True)

    def discover_sec_sources(self, limit: int = 6) -> dict:
        """Find recent SEC annual, quarterly and earnings filings for this run's ticker."""

        def operation():
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= 10
            ):
                raise ResearchError("invalid_limit", "Invalid SEC filing limit.")
            self.settings.require_sec_user_agent()
            with self._lock:
                if self._identity is None:
                    identity = self.sec.resolve_company(self.ticker)
                    if identity.ticker != self.ticker:
                        raise ResearchError(
                            "target_mismatch", "The SEC ticker differs."
                        )
                    self._identity = identity
                    self.company = identity.model_dump(mode="json")
                result = discover_recent_sec_sources(
                    self.sec, self._identity, self.as_of, limit
                )
                for filing in result["filings"]:
                    self._discovered[filing["url"]] = {
                        "published_at": date.fromisoformat(filing["filing_date"]),
                        "directory": filing["directory"],
                        "source_type": "sec",
                    }
            return {"company": self.company, **result}

        return self._invoke("discover_sec_sources", operation, acquire=True)

    @staticmethod
    def _reject_sec_redirect(url: str) -> None:
        host = urlsplit(url).hostname or ""
        if host == "sec.gov" or host.endswith(".sec.gov"):
            raise ResearchError("sec_tool_required", "Use discovered SEC URLs.")

    def _metadata(self, url: str) -> dict:
        metadata = dict(self._discovered.get(url, {}))
        host = urlsplit(url).hostname or ""
        if host == "sec.gov" or host.endswith(".sec.gov"):
            if not metadata.get("directory"):
                # A filing may link its HTML exhibit; preserve the discovered accession boundary.
                for known in self._discovered.values():
                    if known.get("directory") and url.startswith(known["directory"]):
                        _validate_url(url, known["directory"], directory=True)
                        metadata = dict(known)
                        break
            if not metadata.get("directory"):
                raise ResearchError("sec_tool_required", "Use discovered SEC URLs.")
        return metadata

    def _publication_date(self, page, content, metadata: dict) -> date | None:
        published_at = metadata.get("published_at")
        declared = set()
        for tag in BeautifulSoup(page.text, "html.parser").find_all("meta"):
            label = str(
                tag.get("property") or tag.get("itemprop") or tag.get("name") or ""
            ).casefold()
            if label in {"article:published_time", "datepublished"}:
                try:
                    declared.add(date.fromisoformat(str(tag.get("content", ""))[:10]))
                except ValueError:
                    continue
        if any(value > self.as_of for value in declared):
            raise ResearchError("future_source", "Publication date is after cutoff.")
        if published_at is None and len(declared) == 1:
            published_at = next(iter(declared))
        dated = source_dates(
            SimpleNamespace(
                source_type=metadata.get("source_type", "web"),
                published_at=published_at,
                report_date=None,
                snippets=[
                    SimpleNamespace(content=content.full_text, snippet_id="body")
                ],
            ),
            self.as_of,
        )
        for kind in ("published_at", "filing_date"):
            item = dated[kind]
            if item["after_cutoff"] or any(
                date.fromisoformat(candidate["date"]) > self.as_of
                for candidate in item["candidates"]
            ):
                raise ResearchError(
                    "future_source", "Publication date is after cutoff."
                )
        if published_at is None and dated["published_at"]["date"]:
            published_at = date.fromisoformat(dated["published_at"]["date"])
        return published_at

    @staticmethod
    def _discovered_links(page, metadata: dict) -> list[dict]:
        soup = BeautifulSoup(page.text, "html.parser")
        for element in soup.select(
            "head, script, style, nav, header, footer, noscript, form, iframe, [hidden]"
        ):
            element.decompose()
        candidates = {}
        for anchor in (soup.body or soup).find_all("a", href=True):
            href = str(anchor.get("href", "")).strip()
            if not href or href.startswith("#"):
                continue
            try:
                url = urldefrag(urljoin(page.url, href))[0]
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.port not in {None, 80, 443}
                    or len(url) > 4096
                    or url == urldefrag(page.url)[0]
                ):
                    continue
            except ValueError:
                continue
            title = (
                anchor.get_text(" ", strip=True)[:180] or parsed.path.rsplit("/", 1)[-1]
            )
            same_accession = bool(
                metadata.get("directory") and url.startswith(metadata["directory"])
            )
            exhibit = same_accession and re.search(
                r"ex(?:hibit)?[-_ .]?99|earnings|financial results",
                title + " " + parsed.path,
                re.IGNORECASE,
            )
            priority = 0 if exhibit else 1 if same_accession else 2
            record = {"title": title, "url": url, "coverage": "discovery_only"}
            if url not in candidates or priority < candidates[url][0]:
                candidates[url] = (priority, record)
        return [
            record
            for _, record in sorted(candidates.values(), key=lambda item: item[0])[:12]
        ]

    def read_source(self, url: str, focus: str = "") -> dict:
        """Read public HTML by URL, optionally near a literal phrase; save the returned text."""

        def operation():
            if (
                not isinstance(url, str)
                or not url
                or not isinstance(focus, str)
                or len(focus) > 200
            ):
                raise ResearchError("invalid_input", "Invalid reading arguments.")
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                    raise ValueError("Invalid URL")
            except ValueError:
                raise ResearchError("unsafe_url", "Use a public HTTP URL.") from None
            if parsed.path.lower().endswith(".pdf"):
                raise ResearchError("pdf_not_supported", "Read HTML instead.")
            with self._lock:
                metadata = self._metadata(url)
                if (
                    metadata.get("published_at")
                    and metadata["published_at"] > self.as_of
                ):
                    raise ResearchError(
                        "future_source", "Publication date is after cutoff."
                    )
                if url not in self._pages:
                    page = (
                        self.sec._get_document(url, metadata["directory"])
                        if metadata.get("directory")
                        else self.fetcher.get(
                            url, before_request=self._reject_sec_redirect
                        )
                    )
                    self._pages[url] = page
                page = self._pages[url]
                # A redirect destination must establish its own publication metadata.
                date_metadata = (
                    metadata
                    if page.url == url
                    else {"source_type": metadata.get("source_type", "web")}
                )
                warning = None
                try:
                    content = extract_web_content(
                        page, self.settings.max_content_chars, focus=focus
                    )
                except ResearchError as error:
                    if error.code != "text_not_found":
                        raise
                    content = extract_web_content(page, self.settings.max_content_chars)
                    warning = "The literal focus was not found; the returned text is the document's first window."
                published_at = self._publication_date(page, content, date_metadata)
                result = {
                    "requested_url": url,
                    "url": page.url,
                    "title": content.title,
                    "published_at": published_at.isoformat() if published_at else None,
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "text": "\n".join(snippet.content for snippet in content.snippets),
                    "truncated": content.truncated,
                    "discovered_links": self._discovered_links(page, metadata),
                }
                if focus:
                    result["focus_matched"] = warning is None
                if warning:
                    result["reading_warning"] = warning
                with (self.directory / "sources.jsonl").open(
                    "a", encoding="utf-8"
                ) as handle:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                self.sources.append(result)
                self.allowed_urls.update((url, page.url))
                return result

        return self._invoke("read_source", operation, acquire=True)

    def _append_note(self, markdown: str) -> None:
        with (
            self._lock,
            (self.directory / "notes.md").open("a", encoding="utf-8") as handle,
        ):
            handle.write(markdown.rstrip() + "\n\n")

    def calculate(
        self,
        operation: Literal["sum", "difference", "product", "ratio"],
        values: list[str | int | float],
    ) -> dict:
        """Compute finite decimal values and save inputs and result in the research notes."""

        def calculate_values():
            operations = {
                "sum": "add",
                "difference": "subtract",
                "product": "multiply",
                "ratio": "divide",
            }
            if operation not in operations:
                raise ResearchError("invalid_calculation", "Unsupported arithmetic.")
            operands, answer = _arithmetic(operations[operation], values)
            result = {
                "operation": operation,
                "values": [str(value) for value in operands],
                "result": str(answer),
            }
            self._append_note(
                f"计算：{operation}({', '.join(result['values'])}) = {result['result']}"
            )
            return result

        return self._invoke("calculate", calculate_values)

    def save_notes(self, markdown: str) -> dict:
        """Append Markdown facts, URLs, periods, analysis, assumptions and gaps immediately."""

        def operation():
            if (
                not isinstance(markdown, str)
                or not markdown.strip()
                or len(markdown) > 200_000
            ):
                raise ResearchError(
                    "invalid_input", "A bounded nonempty note is required."
                )
            self._append_note(markdown)
            return {"saved": True, "characters": len(markdown)}

        return self._invoke("save_notes", operation)

    def tools(self) -> list:
        """Return the five model-callable acquisition, arithmetic and note tools."""
        return [
            StructuredTool.from_function(method)
            for method in (
                self.search_sources,
                self.discover_sec_sources,
                self.read_source,
                self.calculate,
                self.save_notes,
            )
        ]
