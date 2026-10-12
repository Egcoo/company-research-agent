"""Verify URL provenance and incremental persistence using offline HTTP transport."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from research_agent.config import (
    ConfigurationError,
    ResearchBudget,
    ResearchError,
    Settings,
)
from research_agent.tools.mvp import MvpTools
from research_agent.tools.sec import TICKERS_URL
from research_agent.tools.web import WebSearch
from research_agent.tools.webpage import HttpFetcher

CUTOFF = date(2026, 10, 9)
PUBLIC_URL = "https://example.com/report"
CIK = "0000320193"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
DIRECTORY = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/"
FILING_URL = DIRECTORY + "report.htm"


@pytest.fixture
def mvp_case(tmp_path):
    clients = []

    def build(
        *, routes=None, settings=None, search_results=None, acquisition_allowed=None
    ):
        routes = routes or {
            PUBLIC_URL: "<title>Financial report</title><p>Published: 2026-08-01</p><p>Revenue USD 100 million.</p>"
        }
        settings = settings or Settings(
            max_retries=0,
            tavily_api_key="test-only",
            sec_user_agent="Test contact@example.com",
        )
        requests, searches, events = [], [], []

        def respond(request):
            url = "https://" + request.headers["Host"] + request.url.raw_path.decode()
            requests.append(
                {"url": url, "user_agent": request.headers.get("user-agent")}
            )
            response = routes.get(url, httpx.Response(404))
            if isinstance(response, httpx.Response):
                return response
            if isinstance(response, dict):
                return httpx.Response(200, json=response)
            return httpx.Response(
                200, text=response, headers={"Content-Type": "text/html"}
            )

        def search(**kwargs):
            searches.append(kwargs)
            return {
                "results": search_results
                or [
                    {
                        "title": "Report",
                        "url": PUBLIC_URL,
                        "content": "Search summary only.",
                    }
                ]
            }

        budget = ResearchBudget(settings)
        client = httpx.Client(transport=httpx.MockTransport(respond))
        clients.append(client)
        fetcher = HttpFetcher(
            settings, budget, client=client, resolve_host=lambda host: ["93.184.216.34"]
        )
        searcher = WebSearch(settings, budget, client=SimpleNamespace(search=search))
        tools = MvpTools(
            settings,
            budget,
            fetcher,
            searcher,
            tmp_path,
            CUTOFF,
            "AAPL",
            event_callback=events.append,
            acquisition_allowed=acquisition_allowed,
        )
        return SimpleNamespace(
            tools=tools,
            budget=budget,
            requests=requests,
            events=events,
            directory=tmp_path,
            searches=searches,
            routes=routes,
            settings=settings,
        )

    yield build
    for client in clients:
        client.close()


def sec_routes():
    return {
        TICKERS_URL: {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}
        },
        SUBMISSIONS_URL: {
            "cik": 320193,
            "tickers": ["AAPL"],
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000001"],
                    "filingDate": ["2026-08-01"],
                    "reportDate": ["2026-06-27"],
                    "form": ["10-Q"],
                    "primaryDocument": ["report.htm"],
                }
            },
        },
        FILING_URL: "<title>Apple 10-Q</title><p>Revenue USD 109417 million.</p>",
    }


def test_search_is_not_a_reading_and_only_saved_body_can_be_cited(mvp_case):
    case = mvp_case()
    found = case.tools.search_sources("Apple results")
    assert found["coverage"] == "discovery_only"
    assert found["results"] == [
        {
            "title": "Report",
            "url": PUBLIC_URL,
            "published_at": None,
            "coverage": "discovery_only",
        }
    ]
    assert "Search summary" not in json.dumps(found)
    assert case.budget.counts == {"request": 1, "tool": 1, "model": 0}
    assert not case.tools.allowed_urls and not case.tools.sources
    assert not (case.directory / "sources.jsonl").exists()
    reading = case.tools.read_source(PUBLIC_URL)
    saved = json.loads((case.directory / "sources.jsonl").read_text(encoding="utf-8"))
    assert reading == saved == case.tools.sources[0]
    assert "USD 100 million" in reading["text"]
    assert "Search summary" not in reading["text"]
    assert reading["published_at"] == "2026-08-01"
    assert case.tools.allowed_urls == {PUBLIC_URL}
    assert case.budget.counts == {"request": 2, "tool": 2, "model": 0}
    assert not (case.directory / "sources").exists()


def test_redirect_allows_only_requested_and_final_read_urls(mvp_case):
    intermediate, final = "https://example.com/moved", "https://example.org/final"
    case = mvp_case(
        routes={
            PUBLIC_URL: httpx.Response(302, headers={"location": intermediate}),
            intermediate: httpx.Response(302, headers={"location": final}),
            final: "<title>Final</title><p>Published: 2026-09-01</p><p>Actual reading.</p>",
        },
        search_results=[
            {"title": "Original", "url": PUBLIC_URL, "published_date": "2026-08-01"}
        ],
    )
    case.tools.search_sources("release")
    result = case.tools.read_source(PUBLIC_URL)
    assert result["published_at"] == "2026-09-01"
    assert result["url"] == final and result["requested_url"] == PUBLIC_URL
    assert case.tools.allowed_urls == {PUBLIC_URL, final}
    assert intermediate not in case.tools.allowed_urls


def test_read_windows_are_cached_and_missing_focus_keeps_actual_material(mvp_case):
    body = (
        "Beginning. "
        + "x " * 7000
        + "Revenue special section: 100 million USD. "
        + "y " * 7000
    )
    case = mvp_case(
        routes={PUBLIC_URL: f"<title>Long report</title><p>{body}</p>"},
        settings=Settings(max_content_chars=6000, max_retries=0),
    )
    first = case.tools.read_source(PUBLIC_URL, "phrase not in source")
    second = case.tools.read_source(PUBLIC_URL, "Revenue special section")
    assert (
        first["focus_matched"] is False and "first window" in first["reading_warning"]
    )
    assert (
        second["focus_matched"] is True and "Revenue special section" in second["text"]
    )
    assert (
        first["text"] != second["text"] and first["truncated"] and second["truncated"]
    )
    assert len(case.requests) == 1 and len(case.tools.sources) == 2
    records = [
        json.loads(line)
        for line in (case.directory / "sources.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records == [first, second]


def test_discovered_links_are_bounded_body_discovery_without_fetch_or_citation(
    mvp_case,
):
    links = "".join(f'<a href="/body-{index}">Body {index}</a>' for index in range(20))
    html = (
        '<nav><a href="/navigation">Navigation</a></nav><main><p>Read report.</p>'
        '<a href="javascript:alert(1)">Script</a><a href="mailto:person@example.com">Mail</a>'
        '<a href="https://user:secret@example.com/private">Credentialed URL</a>'
        '<a href="#internal">Internal heading</a>'
        '<a href="/body-0#heading">First body page</a>' + links + "</main>"
    )
    case = mvp_case(routes={PUBLIC_URL: html})
    reading = case.tools.read_source(PUBLIC_URL)
    found = reading["discovered_links"]
    assert len(found) == 12
    assert all(item["coverage"] == "discovery_only" for item in found)
    assert [item["url"] for item in found] == [
        f"https://example.com/body-{index}" for index in range(12)
    ]
    assert len(case.requests) == 1 and case.tools.allowed_urls == {PUBLIC_URL}
    assert not any(item["url"] in case.tools.allowed_urls for item in found)
    saved = json.loads((case.directory / "sources.jsonl").read_text(encoding="utf-8"))
    assert saved["discovered_links"] == found


def test_sec_exhibit_links_are_prioritized_and_need_their_own_read(mvp_case):
    routes = sec_routes()
    routes[FILING_URL] = (
        "<p>Filing with exhibits.</p>"
        + "".join(
            f'<a href="https://example.com/news-{index}">News {index}</a>'
            for index in range(15)
        )
        + '<a href="ex991.htm">Exhibit 99.1 Earnings release</a>'
    )
    exhibit = DIRECTORY + "ex991.htm"
    routes[exhibit] = "<p>Actual earnings release body.</p>"
    case = mvp_case(routes=routes)
    case.tools.discover_sec_sources()
    reading = case.tools.read_source(FILING_URL)
    assert reading["discovered_links"][0] == {
        "title": "Exhibit 99.1 Earnings release",
        "url": exhibit,
        "coverage": "discovery_only",
    }
    assert len(reading["discovered_links"]) == 12
    assert len(case.requests) == 3 and exhibit not in case.tools.allowed_urls
    assert case.tools.read_source(exhibit)["text"] == "Actual earnings release body."
    assert len(case.requests) == 4 and exhibit in case.tools.allowed_urls


@pytest.mark.parametrize(
    "html",
    [
        '<meta property="article:published_time" content="2026-10-10"><p>Body.</p>',
        "<p>Published: 2026-10-10</p><p>Body.</p>",
        "<p>Filing date: 2026-10-10</p><p>Body.</p>",
    ],
)
def test_known_future_publication_never_becomes_read_source(mvp_case, html):
    case = mvp_case(routes={PUBLIC_URL: html})
    result = case.tools.read_source(PUBLIC_URL)
    assert result["error"] == "future_source"
    assert not case.tools.sources and not case.tools.allowed_urls
    assert not (case.directory / "sources.jsonl").exists()


def test_future_search_metadata_is_filtered_and_direct_read_is_rejected(mvp_case):
    case = mvp_case(
        search_results=[
            {"title": "Future", "url": PUBLIC_URL, "published_date": "2026-10-10"}
        ]
    )
    assert case.tools.search_sources("future")["results"] == []
    assert case.tools.read_source(PUBLIC_URL)["error"] == "future_source"
    assert not case.requests


@pytest.mark.parametrize(
    "url,code",
    [
        ("https://example.com/report.pdf", "pdf_not_supported"),
        ("https://127.0.0.1/report", "unsafe_url"),
        ("file:///tmp/report", "unsafe_url"),
        ("https://user:secret@example.com/report", "unsafe_url"),
        (FILING_URL, "sec_tool_required"),
    ],
)
def test_unsupported_or_unsafe_read_never_enters_citation_list(mvp_case, url, code):
    case = mvp_case()
    result = case.tools.read_source(url)
    assert result["error"] == code
    assert not case.requests and not case.tools.allowed_urls


def test_failed_later_read_does_not_erase_sources_notes_or_calculations(mvp_case):
    case = mvp_case()
    reading = case.tools.read_source(PUBLIC_URL)
    note = (
        "## Financials\nRevenue is USD 100 million for FY2025. [Source]("
        + PUBLIC_URL
        + ")"
    )
    assert case.tools.save_notes(note)["saved"]
    assert case.tools.calculate("ratio", ["110", "100"])["result"] == "1.1"
    failure = case.tools.read_source("https://example.com/missing")
    assert failure["error"] == "http_404"
    assert case.tools.sources == [reading]
    assert case.tools.allowed_urls == {PUBLIC_URL}
    notes = (case.directory / "notes.md").read_text(encoding="utf-8")
    assert note in notes and "ratio(110, 100) = 1.1" in notes
    assert (
        json.loads((case.directory / "sources.jsonl").read_text(encoding="utf-8"))
        == reading
    )


def test_sec_discovery_binds_ticker_user_agent_and_accession(mvp_case):
    routes = sec_routes()
    exhibit = DIRECTORY + "ex991.htm"
    routes[exhibit] = "<p>Earnings release.</p>"
    case = mvp_case(routes=routes)
    found = case.tools.discover_sec_sources()
    assert found["company"] == {"name": "Apple Inc.", "ticker": "AAPL", "cik": CIK}
    assert found["filings"][0]["url"] == FILING_URL
    assert case.tools.company == found["company"]
    assert not case.tools.allowed_urls
    assert case.tools.read_source(FILING_URL)["published_at"] == "2026-08-01"
    assert case.tools.read_source(exhibit)["text"] == "Earnings release."
    assert all(
        row["user_agent"] == case.settings.sec_user_agent for row in case.requests
    )
    assert (
        case.tools.read_source(FILING_URL.replace("320193/", "1045810/"))["error"]
        == "sec_tool_required"
    )
    assert (
        case.tools.read_source(DIRECTORY + "../other/report.htm")["error"]
        == "unsafe_sec_url"
    )
    assert case.tools.allowed_urls == {FILING_URL, exhibit}


def test_sec_submissions_mismatched_ticker_cannot_authorize_filing(mvp_case):
    routes = sec_routes()
    routes[SUBMISSIONS_URL]["tickers"] = ["NVDA"]
    case = mvp_case(routes=routes)
    assert case.tools.discover_sec_sources()["error"] == "target_mismatch"
    assert case.tools.read_source(FILING_URL)["error"] == "sec_tool_required"
    assert not case.tools.allowed_urls


def test_public_redirect_cannot_bypass_sec_discovery(mvp_case):
    case = mvp_case(
        routes={PUBLIC_URL: httpx.Response(302, headers={"location": FILING_URL})}
    )
    assert case.tools.read_source(PUBLIC_URL)["error"] == "sec_tool_required"
    assert len(case.requests) == 1
    assert not case.tools.allowed_urls


def test_sec_redirect_cannot_escape_discovered_accession(mvp_case):
    routes = sec_routes()
    routes[FILING_URL] = httpx.Response(
        302, headers={"location": FILING_URL.replace("320193/", "1045810/")}
    )
    case = mvp_case(routes=routes)
    case.tools.discover_sec_sources()
    assert case.tools.read_source(FILING_URL)["error"] == "unsafe_sec_url"
    assert [item["url"] for item in case.requests] == [
        TICKERS_URL,
        SUBMISSIONS_URL,
        FILING_URL,
    ]
    assert not case.tools.sources and not case.tools.allowed_urls


def test_storage_failure_cannot_return_unpersisted_body_as_citable(mvp_case):
    case = mvp_case()
    (case.directory / "sources.jsonl").mkdir()
    result = case.tools.read_source(PUBLIC_URL)
    assert result["error"] == "storage_error" and "text" not in result
    assert not case.tools.sources and not case.tools.allowed_urls


def test_acquisition_cutoff_preserves_note_and_calculation_tools(mvp_case):
    case = mvp_case(acquisition_allowed=lambda: False)
    assert case.tools.search_sources("Apple")["error"] == "acquisition_closed"
    assert case.tools.discover_sec_sources()["error"] == "acquisition_closed"
    assert case.tools.read_source(PUBLIC_URL)["error"] == "acquisition_closed"
    assert case.tools.save_notes("Acquisition stopped; financial data remains a gap.")[
        "saved"
    ]
    assert case.tools.calculate("sum", ["1", "2"])["result"] == "3"
    assert not case.requests and not case.searches
    assert case.budget.counts["tool"] == 5


def test_total_deadline_is_checked_at_start_of_every_tool(mvp_case):
    case = mvp_case()
    case.budget.started_at -= case.settings.deadline_seconds + 1
    calls = [
        lambda: case.tools.search_sources("Apple"),
        lambda: case.tools.discover_sec_sources(),
        lambda: case.tools.read_source(PUBLIC_URL),
        lambda: case.tools.calculate("sum", ["1", "2"]),
        lambda: case.tools.save_notes("Late note"),
    ]
    assert all(call()["error"] == "budget_exceeded" for call in calls)
    assert not case.requests and not case.searches
    assert not (case.directory / "notes.md").exists()


def test_safe_tool_failures_do_not_echo_provider_exception_secrets(mvp_case):
    case = mvp_case()

    def broken_search(*args, **kwargs):
        raise ConfigurationError("provider-key=DO-NOT-EXPOSE")

    case.tools.search.search = broken_search
    output = case.tools.search_sources("Apple")
    assert output["error"] == "configuration_missing"
    assert "DO-NOT-EXPOSE" not in json.dumps(output)
    assert "DO-NOT-EXPOSE" not in json.dumps(case.events)

    def failed_read(*args, **kwargs):
        raise ResearchError("transport_error", "private request details")

    case.tools.fetcher.get = failed_read
    output = case.tools.read_source(PUBLIC_URL)
    assert output["error"] == "transport_error"
    assert "private request details" not in json.dumps(output)


def test_notes_append_safely_across_concurrent_calls(mvp_case):
    case = mvp_case()
    notes = [f"## Note {index}\nFact {index} and gap." for index in range(12)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(case.tools.save_notes, notes))
    assert all(result["saved"] for result in results)
    saved = (case.directory / "notes.md").read_text(encoding="utf-8")
    assert all(saved.count(note + "\n\n") == 1 for note in notes)


def test_model_tools_use_plain_arguments_and_safe_calculation_errors(mvp_case):
    case = mvp_case()
    tools = {tool.name: tool for tool in case.tools.tools()}
    assert set(tools) == {
        "search_sources",
        "discover_sec_sources",
        "read_source",
        "calculate",
        "save_notes",
    }
    assert set(tools["read_source"].args) == {"url", "focus"}
    assert tools["save_notes"].invoke(
        {"markdown": "## Plan\nRead SEC annual and quarterly filings."}
    )["saved"]
    assert (
        tools["calculate"].invoke({"operation": "ratio", "values": ["1", "0"]})["error"]
        == "invalid_calculation"
    )
    assert (
        tools["calculate"].invoke({"operation": "sum", "values": ["NaN", "1"]})["error"]
        == "invalid_calculation"
    )


def test_calculation_tool_accepts_json_numbers_and_numeric_strings(mvp_case):
    case = mvp_case()
    calculator = next(tool for tool in case.tools.tools() if tool.name == "calculate")
    result = calculator.invoke({"operation": "sum", "values": [100, 2.5, "0.25"]})
    assert result == {
        "operation": "sum",
        "values": ["100", "2.5", "0.25"],
        "result": "102.75",
    }
    assert "sum(100, 2.5, 0.25) = 102.75" in (case.directory / "notes.md").read_text(
        encoding="utf-8"
    )
    assert (
        calculator.invoke({"operation": "sum", "values": [float("inf"), 1]})["error"]
        == "invalid_calculation"
    )
