"""Bind source acquisition and short references to one research task."""

import re
import threading
from datetime import date
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from langchain_core.tools import StructuredTool

from research_agent.config import ConfigurationError, ResearchError
from research_agent.schemas import ResearchOutput
from research_agent.tools.contracts import CompanyIdentity
from research_agent.tools.sec import (
    ARCHIVES_ROOT,
    SUBMISSIONS_ROOT,
    SecClient,
    _IndexParser,
    _validate_url,
)
from research_agent.tools.source_dates import source_dates
from research_agent.tools.webpage import extract_web_content

_INITIALIZATION_LOCK = threading.Lock()
_FORMS = {"10-Q", "10-K", "8-K", "20-F", "6-K"}
CAPABILITY_METHODS = {
    "web.search": "search_sources",
    "web.read": "read_source",
    "math.calculate": "calculate",
    "sec.resolve": "resolve_sec_target",
    "sec.disclosures": "discover_sec_sources",
    "sec.attachments": "discover_sec_attachments",
}


def confirm_target_identity(target, settings, fetcher):
    """Apply provider identity checks outside the workflow scheduler."""
    if target.kind != "stock" or (target.market or "").upper() not in {
        "US",
        "USA",
        "NASDAQ",
        "NYSE",
    }:
        return target
    company = SecClient(settings, fetcher).resolve_company(target.symbol or target.name)
    return target.model_copy(
        update={
            "name": company.name,
            "symbol": company.ticker,
            "identity_status": "verified",
            "identity_source": "https://www.sec.gov/files/company_tickers.json",
            "adapter_metadata": {"cik": company.cik},
        }
    )


def discover_recent_sec_sources(sec, company, as_of: date, limit: int = 6) -> dict:
    """Select bounded recent disclosures, including foreign-issuer forms."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ResearchError(
            "invalid_limit", "SEC filing limit must be between 1 and 10."
        )
    payload = sec._get_json(f"{SUBMISSIONS_ROOT}CIK{company.cik}.json")
    try:
        if int(payload["cik"]) != int(company.cik):
            raise ValueError("CIK mismatch")
        tickers = payload.get("tickers")
        if tickers is not None and getattr(company, "ticker", None):
            if not isinstance(tickers, list) or company.ticker not in tickers:
                raise ResearchError(
                    "target_mismatch", "SEC submissions do not match the bound ticker."
                )
        recent = payload["filings"]["recent"]
        keys = (
            "accessionNumber",
            "filingDate",
            "reportDate",
            "form",
            "primaryDocument",
        )
        columns = [recent[key] for key in keys]
        if (
            any(not isinstance(column, list) for column in columns)
            or len({len(column) for column in columns}) != 1
        ):
            raise ValueError("Unaligned columns")
        items = recent.get("items", [""] * len(columns[0]))
        if (
            not isinstance(items, list)
            or len(items) != len(columns[0])
            or any(not isinstance(item, str) for item in items)
        ):
            raise ValueError("Unaligned items")
        filings = []
        for row, item in zip(zip(*columns, strict=True), items, strict=True):
            accession, filed, period, form, document = row
            if form not in _FORMS:
                continue
            if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                raise ValueError("Invalid accession")
            filing_date = date.fromisoformat(filed)
            report_date = date.fromisoformat(period) if period else None
            if filing_date > as_of or (report_date is not None and report_date > as_of):
                continue
            directory = (
                f"{ARCHIVES_ROOT}{int(company.cik)}/{accession.replace('-', '')}/"
            )
            url = directory + document
            _validate_url(url, directory, directory=True)
            filings.append(
                {
                    "form": form,
                    "accession_number": accession,
                    "filing_date": filed,
                    "report_date": period or None,
                    "url": url,
                    "directory": directory,
                    "items": item,
                }
            )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ResearchError(
            "sec_parse_error", "SEC recent disclosures have an invalid structure."
        ) from None
    filings.sort(key=lambda row: row["filing_date"], reverse=True)
    periodic = sorted(
        (row for row in filings if row["form"] in {"10-Q", "10-K", "20-F"}),
        key=lambda row: (row["report_date"] or "", row["filing_date"]),
        reverse=True,
    )
    selected = periodic[:1]
    earnings = [
        row
        for row in filings
        if row["form"] == "6-K"
        or (row["form"] == "8-K" and "2.02" in re.split(r"[,;\s]+", row["items"]))
    ]
    selected.extend(earnings[:1])
    annual = next((row for row in periodic if row["form"] in {"10-K", "20-F"}), None)
    if annual is not None and annual not in selected:
        selected.append(annual)
    selected.extend(row for row in earnings[1:] if row not in selected)
    gaps = [
        "Coverage is recent-only; archived disclosures and amended-report reconciliation are not supported."
    ]
    if not periodic:
        gaps.append("No recent periodic disclosure was available by the cutoff.")
    if len(selected) > limit:
        gaps.append("The filing limit excluded supporting disclosures.")
    if any(row["form"] == "6-K" for row in selected):
        gaps.append(
            "A 6-K may describe an event rather than earnings; its reporting period requires source review."
        )
    return {"filings": selected[:limit], "coverage": "recent_only", "gaps": gaps}


class SourceTools:
    """Expose task-bound tools without asking models to repeat database IDs."""

    def __init__(
        self,
        settings,
        budget,
        fetcher,
        search,
        ledger,
        target,
        task_id,
        event_callback=None,
    ):
        """Share run resources while keeping selection references task-local."""
        self.settings, self.budget = settings, budget
        self.fetcher, self.search, self.ledger = fetcher, search, ledger
        self.target, self.task_id = target, task_id
        if ledger.target_key != target.key:
            raise ResearchError(
                "target_mismatch",
                "Source tools and the ledger must use the same research target.",
            )
        self.event_callback = event_callback
        self._source_refs, self._evidence_refs, self._finding_refs = {}, {}, {}
        self._read_refs = {}
        self._read_locations = {}
        self._dependency_refs = set()
        self._discovered, self._filings = {}, {}
        self._sec_result = None
        with _INITIALIZATION_LOCK:
            if not hasattr(ledger, "_source_tool_cache"):
                ledger._source_tool_cache = {}
                ledger._source_tool_locks = {}
                ledger._source_tool_sec = SecClient(settings, fetcher)
            self._cache = ledger._source_tool_cache
            self.sec = ledger._source_tool_sec

    def _event(self, tool: str, code: str = "ok") -> None:
        if self.event_callback:
            self.event_callback(
                {
                    "type": "runtime_material"
                    if tool.startswith("record_")
                    else "tool_result",
                    "task_id": self.task_id,
                    "tool": tool,
                    "code": code,
                }
            )

    def dependency_material(self, task_ids: list[str]) -> list[dict]:
        """Expose completed predecessor evidence through this task's short handles."""
        with self.ledger._lock:
            evidence = [
                item
                for item in self.ledger.evidence.values()
                if item.task_id in task_ids
            ]
            materials = [
                {
                    "evidence_ref": self._reference(
                        self._evidence_refs, item.evidence_id
                    ),
                    "topic": item.topic,
                    "statement": item.statement,
                    "status": item.status,
                    "definition": item.definition,
                    "value": str(item.value) if item.value is not None else None,
                    "unit": item.unit,
                    "currency": item.currency,
                    "reporting_period": item.reporting_period,
                    "effective_date": str(item.effective_date)
                    if item.effective_date
                    else None,
                }
                for item in evidence
            ]
            self._dependency_refs = {item["evidence_ref"] for item in materials}
            return materials

    def dependency_metrics(self, task_ids: list[str]) -> list[dict]:
        """Keep predecessor observations linked to the supplied evidence handles."""
        with self.ledger._lock:
            return [
                item.model_dump(mode="json")
                | {
                    "evidence_ref": self._reference(
                        self._evidence_refs, item.evidence_id
                    )
                }
                for item in self.ledger.metrics.values()
                if item.task_id in task_ids
            ]

    @staticmethod
    def _reference(references: dict, identifier: str) -> int:
        for reference, known in references.items():
            if known == identifier:
                return reference
        reference = len(references) + 1
        references[reference] = identifier
        return reference

    @staticmethod
    def _lookup(references: dict, reference: int, kind: str) -> str:
        if isinstance(reference, bool) or reference not in references:
            raise ResearchError(
                f"unknown_{kind}",
                f"Choose a {kind} reference returned by this task's tools.",
            )
        return references[reference]

    def search_sources(self, query: str, domains: list[str] | None = None) -> dict:
        """Discover source URLs; search summaries are not evidence."""
        try:
            key = f"search:{query}:{tuple(sorted(domains or []))}"
            results = self._cached(
                key, lambda: self.search.search(query, domains=domains)
            )
        except ConfigurationError:
            raise ResearchError(
                "search_configuration_missing",
                "Web search is not configured; directly supplied public URLs can still be read.",
            ) from None
        output = []
        for hit in results.results:
            published_at = hit.published_at
            if published_at is not None and published_at > self.ledger.as_of:
                continue
            self._discovered[hit.url] = {
                "title": hit.title,
                "published_at": published_at,
                **self._discovered.get(hit.url, {}),
            }
            output.append(
                hit.model_dump(mode="json")
                | {"read_ref": self._reference(self._read_refs, hit.url)}
            )
        self._event("search_sources")
        return {
            "results": output,
            "coverage": "discovery_only",
            "summaries_are_evidence": False,
        }

    def resolve_sec_target(self) -> dict:
        """Resolve the bound stock in SEC metadata without changing its identity."""
        if self.target.kind != "stock":
            raise ResearchError(
                "sec_not_applicable",
                "SEC company disclosure tools do not cover this target type.",
            )
        try:
            self.settings.require_sec_user_agent()
        except ConfigurationError:
            raise ResearchError(
                "sec_configuration_missing",
                "SEC_USER_AGENT is required only for SEC access.",
            ) from None
        if self._sec_result is None:
            cik = self.target.adapter_metadata.get("cik")
            if cik and self.target.symbol:
                if not re.fullmatch(r"\d{1,10}", str(cik)) or int(cik) == 0:
                    raise ResearchError(
                        "invalid_cik",
                        "The SEC adapter CIK must contain one to ten digits.",
                    )
                self._sec_result = CompanyIdentity(
                    name=self.target.name,
                    ticker=self.target.symbol.upper(),
                    cik=str(cik).zfill(10),
                )
            else:
                name = self.target.symbol or self.target.name
                self._sec_result = self._cached(
                    f"sec_identity:{name}", lambda: self.sec.resolve_company(name)
                )
        if self.target.symbol and self._sec_result.ticker != self.target.symbol.upper():
            raise ResearchError(
                "target_mismatch", "The SEC ticker does not match the bound target."
            )
        self._event("resolve_sec_target")
        return self._sec_result.model_dump(mode="json")

    def discover_sec_sources(self, limit: int = 6) -> dict:
        """Discover recent domestic and foreign issuer disclosure URLs."""
        self.resolve_sec_target()
        key = f"sec_discovery:{self._sec_result.cik}:{self.ledger.as_of}:{limit}"
        result = self._cached(
            key,
            lambda: discover_recent_sec_sources(
                self.sec, self._sec_result, self.ledger.as_of, limit
            ),
        )
        output = []
        for filing in result["filings"]:
            reference = len(self._filings) + 1
            existing = next(
                (
                    ref
                    for ref, row in self._filings.items()
                    if row["url"] == filing["url"]
                ),
                None,
            )
            reference = existing or reference
            self._filings[reference] = filing
            self._discovered[filing["url"]] = {
                "published_at": date.fromisoformat(filing["filing_date"]),
                "directory": filing["directory"],
                "source_type": "sec",
            }
            output.append(
                {
                    key: value
                    for key, value in filing.items()
                    if key not in {"directory", "items"}
                }
                | {
                    "filing_ref": reference,
                    "read_ref": self._reference(self._read_refs, filing["url"]),
                    "attachments_supported": filing["form"] in {"8-K", "6-K"},
                }
            )
        self._event("discover_sec_sources")
        return {
            "filings": output,
            "coverage": result["coverage"],
            "gaps": result["gaps"],
        }

    def discover_sec_attachments(self, filing_ref: int) -> dict:
        """Find up to two EX-99 HTML exhibits in a discovered earnings accession."""
        if isinstance(filing_ref, bool) or filing_ref not in self._filings:
            raise ResearchError(
                "unknown_filing", "Choose a filing reference from SEC discovery."
            )
        filing = self._filings[filing_ref]
        if filing["form"] not in {"8-K", "6-K"}:
            raise ResearchError(
                "invalid_filing",
                "Earnings exhibits require an 8-K or 6-K; read periodic filings directly with read_source.",
            )
        directory = filing["directory"]
        index_url = f"{directory}{filing['accession_number']}-index.html"
        page = self._cached(
            index_url, lambda: self.sec._get_document(index_url, directory)
        )
        parser = _IndexParser()
        parser.feed(page.text)
        parser.close()
        if not any(
            any(cell.casefold() == "type" for cell in cells) for cells, _ in parser.rows
        ):
            raise ResearchError(
                "sec_parse_error", "SEC filing index has no document-type table."
            )
        urls = []
        for cells, links in parser.rows:
            if not any(cell.upper() in {"EX-99.1", "EX-99.2"} for cell in cells):
                continue
            for link in links:
                try:
                    url = urljoin(index_url, link)
                except ValueError:
                    raise ResearchError(
                        "sec_parse_error",
                        "SEC filing index contains a malformed attachment URL.",
                    ) from None
                _validate_url(url, directory, directory=True)
                if (
                    urlsplit(url).path.lower().endswith((".htm", ".html"))
                    and url not in urls
                ):
                    urls.append(url)
                if len(urls) == 2:
                    break
            if len(urls) == 2:
                break
        for url in urls:
            self._discovered[url] = {
                "published_at": date.fromisoformat(filing["filing_date"]),
                "directory": directory,
                "source_type": "sec",
            }
        self._event("discover_sec_attachments")
        return {
            "urls": urls,
            "sources": [
                {"url": url, "read_ref": self._reference(self._read_refs, url)}
                for url in urls
            ],
            "coverage": "html_ex99_only",
            "gaps": [] if urls else ["No supported EX-99 HTML exhibit was found."],
        }

    def _cached(self, key, fetch):
        with _INITIALIZATION_LOCK:
            lock = self.ledger._source_tool_locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self._cache:
                try:
                    self._cache[key] = fetch()
                except ResearchError as error:
                    self._cache[key] = error
            result = self._cache[key]
            if isinstance(result, ResearchError):
                raise ResearchError(result.code, str(result), details=result.details)
            return result

    @staticmethod
    def _reject_sec_redirect(url: str) -> None:
        host = urlsplit(url).hostname or ""
        if host == "sec.gov" or host.endswith(".sec.gov"):
            raise ResearchError(
                "sec_tool_required",
                "Discover SEC URLs with the SEC tools before reading them.",
            )

    def read_source(
        self,
        url: str | None = None,
        focus: str = "",
        read_ref: int | None = None,
    ) -> dict:
        """Read a discovered read_ref or public URL; a missing literal focus returns the first window."""
        if read_ref is not None:
            bound_url = self._lookup(self._read_refs, read_ref, "read")
            if url is not None and url != bound_url:
                raise ResearchError(
                    "source_reference_mismatch",
                    "The URL must match the selected read_ref; omit URL when using read_ref.",
                )
            url = bound_url
        if not isinstance(url, str) or not url:
            raise ResearchError(
                "invalid_input", "Supply a discovered read_ref or a public source URL."
            )
        if not isinstance(focus, str) or len(focus) > 200:
            raise ResearchError(
                "invalid_input", "Reading focus must contain at most 200 characters."
            )
        try:
            parsed = urlsplit(url)
        except ValueError:
            raise ResearchError(
                "unsafe_url", "A public HTTP(S) destination is required."
            ) from None
        if parsed.path.lower().endswith(".pdf"):
            raise ResearchError(
                "pdf_not_supported",
                "PDF/OCR is not supported; seek an HTML disclosure or report a coverage gap.",
            )
        metadata = self._discovered.get(url, {})
        host = parsed.hostname or ""
        if host == "sec.gov" or host.endswith(".sec.gov"):
            if metadata.get("source_type") != "sec":
                raise ResearchError(
                    "sec_tool_required",
                    "Discover this SEC URL with the SEC tools before reading it.",
                )
            page = self._cached(
                url, lambda: self.sec._get_document(url, metadata["directory"])
            )
        else:
            page = self._cached(
                url,
                lambda: self.fetcher.get(url, before_request=self._reject_sec_redirect),
            )
        extracted = extract_web_content(page, self.settings.max_content_chars)
        published_at = metadata.get("published_at") if page.url == url else None
        published_metadata = set()
        for tag in BeautifulSoup(page.text, "html.parser").find_all("meta"):
            label = str(
                tag.get("property") or tag.get("itemprop") or tag.get("name") or ""
            ).casefold()
            if label not in {"article:published_time", "datepublished"}:
                continue
            try:
                published_metadata.add(
                    date.fromisoformat(str(tag.get("content", ""))[:10])
                )
            except ValueError:
                continue
        if any(value > self.ledger.as_of for value in published_metadata):
            raise ResearchError(
                "future_source", "The source declares publication after the cutoff."
            )
        if published_at is None and len(published_metadata) == 1:
            published_at = next(iter(published_metadata))
        date_records = source_dates(
            SimpleNamespace(
                source_type=metadata.get("source_type", "web"),
                published_at=published_at,
                report_date=None,
                snippets=[
                    SimpleNamespace(content=extracted.full_text, snippet_id="body")
                ],
            ),
            self.ledger.as_of,
        )
        if any(
            record["after_cutoff"]
            or any(
                date.fromisoformat(candidate["date"]) > self.ledger.as_of
                for candidate in record["candidates"]
            )
            for kind, record in date_records.items()
            if kind in {"published_at", "filing_date"}
        ):
            raise ResearchError(
                "future_source",
                "The fetched source has an explicit publication or filing date after the cutoff.",
            )
        known_publication = date_records["published_at"]["date"]
        if published_at is None and known_publication:
            published_at = date.fromisoformat(known_publication)
        source = self.ledger.add_source(
            url=page.url,
            title=extracted.title,
            text=extracted.full_text,
            source_type=metadata.get("source_type", "web"),
            published_at=published_at,
        )
        source_ref = self._reference(self._source_refs, source.source_id)
        start = 0
        focus_matched = None
        if focus:
            match = re.search(re.escape(focus), source.text, re.IGNORECASE)
            focus_matched = match is not None
            if match is not None:
                start = match.start() // 6000
        count = max(1, (self.settings.max_content_chars + 5999) // 6000)
        locations = [
            {"location": location, "text": text}
            for location, text in source.locations.items()
            if start <= location < start + count
        ]
        self._read_locations.setdefault(source.source_id, set()).update(
            item["location"] for item in locations
        )
        self._event("read_source")
        result = {
            "source_ref": source_ref,
            "read_ref": self._reference(self._read_refs, url),
            "url": str(source.url),
            "title": source.title,
            "published_at": source.published_at.isoformat()
            if source.published_at
            else None,
            "locations": locations,
            "truncated": len(locations) < len(source.locations),
            "coverage": "html_snapshot",
            "identity_verified": False,
        }
        if focus:
            result["focus_matched"] = focus_matched
            if not focus_matched:
                result["reading_warning"] = (
                    "The literal focus was not found; these locations contain the document's first window, "
                    "not a passage matching the requested focus."
                )
        return result

    def reading_context(self) -> list[dict]:
        """Return at most 6,000 characters from this task's already visible source windows."""
        with self.ledger._lock:
            sources = [
                (source_id, sorted(locations))
                for source_id, locations in self._read_locations.items()
                if locations and source_id in self.ledger.sources
            ]
            if not sources:
                return []
            width = min(1500, max(1, 6000 // len(sources)))
            remaining = 6000
            output = {
                source_id: {
                    "source_ref": self._reference(self._source_refs, source_id),
                    "locations": [],
                }
                for source_id, _ in sources
            }
            for index in range(max(len(locations) for _, locations in sources)):
                for source_id, locations in sources:
                    if index >= len(locations) or remaining <= 0:
                        continue
                    location = locations[index]
                    text = self.ledger.sources[source_id].locations[location][
                        : min(width, remaining)
                    ]
                    output[source_id]["locations"].append(
                        {"location": location, "text": text}
                    )
                    remaining -= len(text)
            return [item for item in output.values() if item["locations"]]

    def record_source_evidence(
        self,
        source_ref: int,
        location: int,
        topic: str,
        statement: str,
        status: Literal["verified", "source_opinion", "unresolved"],
        definition: str,
        reporting_period: str | None = None,
        effective_date: date | None = None,
        value: str | None = None,
        unit: str | None = None,
        currency: str | None = None,
    ) -> dict:
        """Register a source statement; original text and task identity are bound by the runtime."""
        source_id = self._lookup(self._source_refs, source_ref, "source")
        evidence = self.ledger.record(
            self.task_id,
            source_id,
            location,
            topic,
            statement,
            status,
            definition,
            reporting_period=reporting_period,
            effective_date=effective_date,
            value=value,
            unit=unit,
            currency=currency,
        )
        self._event("record_source_evidence")
        return {
            "evidence_ref": self._reference(self._evidence_refs, evidence.evidence_id),
            "status": evidence.status,
            "statement": evidence.statement,
        }

    def record_metric(
        self,
        evidence_ref: int,
        metric: str,
        value: str | None = None,
        unit: str | None = None,
        definition: str | None = None,
        reporting_period: str | None = None,
        effective_date: date | None = None,
        currency: str | None = None,
    ) -> dict:
        """Store a task-selected metric, inheriting omitted fields from its evidence."""
        evidence_id = self._lookup(self._evidence_refs, evidence_ref, "evidence")
        observation = self.ledger.add_metric(
            self.task_id,
            evidence_id,
            metric,
            value,
            unit,
            definition,
            reporting_period=reporting_period,
            effective_date=effective_date,
            currency=currency,
        )
        self._event("record_metric")
        return {
            "metric": observation.metric,
            "value": str(observation.value),
            "unit": observation.unit,
            "status": observation.status,
        }

    def record_finding(
        self,
        topic: str,
        evidence_refs: list[int],
        inference: str,
        limitations: list[str],
    ) -> dict:
        """Record an interpretation separately from the source statements it depends on."""
        evidence_ids = [
            self._lookup(self._evidence_refs, reference, "evidence")
            for reference in evidence_refs
        ]
        finding = self.ledger.add_finding(
            self.task_id, topic, evidence_ids, inference, limitations
        )
        self._event("record_finding")
        return {
            "finding_ref": self._reference(self._finding_refs, finding.finding_id),
            "inference": finding.inference,
        }

    def calculate(
        self,
        operation: Literal["sum", "difference", "product", "ratio"],
        values: list[str],
        currency: str | None = None,
    ) -> dict:
        """Calculate Decimal inputs reproducibly and save the operation with its result."""
        operations = {
            "sum": "add",
            "difference": "subtract",
            "product": "multiply",
            "ratio": "divide",
        }
        if operation not in operations:
            raise ResearchError(
                "invalid_calculation", "Choose a supported calculation operation."
            )
        calculation = self.ledger.add_calculation(
            self.task_id, operations[operation], values, currency=currency
        )
        self._event("calculate")
        return {
            "operation": operation,
            "values": [str(value) for value in calculation.values],
            "result": str(calculation.result),
            "currency": calculation.currency,
        }

    def validate_output(self, output: ResearchOutput) -> list[tuple[int, int]]:
        """Check task-visible provenance without registering or acquiring material."""
        output = ResearchOutput.model_validate(
            output.model_dump() if isinstance(output, ResearchOutput) else output
        )
        with self.ledger._lock:
            bound = []
            for evidence_index, draft in enumerate(output.evidence):
                source_ref = draft.source_ref
                if source_ref is None and self._dependency_refs:
                    raise ResearchError(
                        "ambiguous_source",
                        "Predecessor evidence and new sources are both available; select an explicit source_ref for new evidence, or reuse dependency_evidence_refs in findings.",
                        details={
                            "evidence_index": evidence_index,
                            "allowed_source_refs": sorted(self._source_refs),
                        },
                    )
                if source_ref is None and len(self._source_refs) == 1:
                    source_ref = next(iter(self._source_refs))
                if source_ref not in self._source_refs:
                    raise ResearchError(
                        "unknown_source",
                        "Choose a source reference (source_ref) from this task's successfully read sources, not read_ref.",
                        details={
                            "evidence_index": evidence_index,
                            "allowed_source_refs": sorted(self._source_refs),
                        },
                    )
                source_id = self._lookup(self._source_refs, source_ref, "source")
                visible = self._read_locations.get(source_id, set())
                location = draft.location
                if location is None and len(visible) == 1:
                    location = next(iter(visible))
                if location not in visible:
                    raise ResearchError(
                        "unknown_source",
                        "Output references a location that was not fetched.",
                        details={
                            "evidence_index": evidence_index,
                            "allowed_locations": sorted(visible),
                        },
                    )
                bound.append((source_ref, location))
                if draft.effective_date and draft.effective_date > self.ledger.as_of:
                    raise ResearchError(
                        "future_observation", "Output observation is after the cutoff."
                    )
                if draft.metric and draft.source_status != "unresolved":
                    source_text = self.ledger.sources[source_id].text
                    # This is a numeric observation check, not copied-quote matching.
                    source_text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", source_text)
                    values = set()
                    for token in re.findall(
                        r"(?<![\w.])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?![\w.])",
                        source_text,
                    ):
                        try:
                            values.add(Decimal(token.replace(",", "")))
                        except InvalidOperation:
                            continue
                    supported = abs(Decimal(str(draft.value))) in {
                        abs(value) for value in values
                    }
                    if not supported:
                        supported = any(
                            calculation.task_id == self.task_id
                            and calculation.result == Decimal(str(draft.value))
                            and calculation.currency == draft.currency
                            and all(
                                abs(value) in {abs(item) for item in values}
                                for value in calculation.values
                            )
                            for calculation in self.ledger.calculations.values()
                        )
                    if not supported and any(
                        label in (draft.unit or "").casefold()
                        for label in (
                            "thousand",
                            "million",
                            "billion",
                            "千",
                            "万",
                            "亿",
                        )
                    ):
                        supported = any(
                            abs(Decimal(str(draft.value))) == abs(value * factor)
                            for value in values
                            for factor in (
                                Decimal("0.000000001"),
                                Decimal("0.00000001"),
                                Decimal("0.000001"),
                                Decimal("0.0001"),
                                Decimal("0.001"),
                                Decimal("0.01"),
                                Decimal("100"),
                                Decimal("1000"),
                                Decimal("10000"),
                                Decimal("1000000"),
                                Decimal("100000000"),
                                Decimal("1000000000"),
                            )
                        )
                    if not supported:
                        raise ResearchError(
                            "metric_source_mismatch",
                            "The numeric observation is absent from the selected source. Reuse predecessor evidence, record arithmetic as a calculation/inference, or preserve the observation as unresolved.",
                            details={"evidence_index": evidence_index},
                        )
            for finding in output.findings:
                if any(
                    index >= len(output.evidence) for index in finding.evidence_indices
                ):
                    raise ResearchError(
                        "unknown_evidence",
                        "Finding references evidence outside this output.",
                    )
                if set(finding.dependency_evidence_refs) - self._dependency_refs:
                    raise ResearchError(
                        "unknown_evidence",
                        "Finding references a predecessor handle not supplied to this task.",
                    )
                if (
                    not finding.evidence_indices
                    and not finding.dependency_evidence_refs
                    and not finding.limitations
                ):
                    raise ResearchError(
                        "unsupported_finding",
                        "An unsourced finding must disclose its limitation.",
                    )
            return bound

    def bind_output(self, output: ResearchOutput) -> None:
        """Register one validated output without exposing ledger writes as tools."""
        output = ResearchOutput.model_validate(
            output.model_dump() if isinstance(output, ResearchOutput) else output
        )
        with self.ledger._lock:
            bound = self.validate_output(output)
            references = []
            for draft, (source_ref, location) in zip(
                output.evidence, bound, strict=True
            ):
                data = draft.model_dump(
                    exclude={"source_status", "metric", "source_ref", "location"}
                )
                data.update(source_ref=source_ref, location=location)
                registered = self.record_source_evidence(
                    **data, status=draft.source_status
                )
                references.append(registered["evidence_ref"])
                if draft.metric:
                    self.record_metric(registered["evidence_ref"], draft.metric)
            for finding in output.findings:
                self.record_finding(
                    finding.topic,
                    [references[index] for index in finding.evidence_indices]
                    + finding.dependency_evidence_refs,
                    finding.inference,
                    finding.limitations,
                )
            if self.event_callback:
                self.event_callback(
                    {
                        "type": "runtime_registration",
                        "task_id": self.task_id,
                        "evidence_count": len(output.evidence),
                        "metric_count": sum(
                            bool(item.metric) for item in output.evidence
                        ),
                        "finding_count": len(output.findings),
                        "implicit_source_fields": sum(
                            (item.source_ref is None) + (item.location is None)
                            for item in output.evidence
                        ),
                    }
                )

    def capability_status(self, capability_ids) -> list[dict]:
        """Describe bindings and configuration without network probes or credentials."""
        result = []
        for identifier in capability_ids:
            if identifier not in CAPABILITY_METHODS:
                raise ConfigurationError("Unknown execution capability.")
            applicable = not identifier.startswith("sec.") or (
                self.target.kind == "stock"
                and (
                    (self.target.market or "").upper()
                    in {"US", "USA", "NASDAQ", "NYSE"}
                    or bool(self.target.adapter_metadata.get("cik"))
                )
            )
            configured = True
            try:
                if identifier == "web.search":
                    self.settings.require("tavily")
                elif identifier.startswith("sec."):
                    self.settings.require_sec_user_agent()
            except ConfigurationError:
                configured = False
            result.append(
                {
                    "id": identifier,
                    "tool": CAPABILITY_METHODS[identifier],
                    "applicable": applicable,
                    "configured": configured,
                }
            )
        return result

    def tools(self, capability_ids=None) -> list:
        """Return model-callable tools whose metadata is supplied by this instance."""
        capability_ids = (
            list(CAPABILITY_METHODS) if capability_ids is None else capability_ids
        )
        methods = [
            getattr(self, item["tool"])
            for item in self.capability_status(capability_ids)
            if item["applicable"] and item["configured"]
        ]
        return [StructuredTool.from_function(method) for method in methods]
