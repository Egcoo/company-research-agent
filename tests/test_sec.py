"""Verify SEC identity, transport limits and document boundaries offline."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from research_agent.config import (
    ConfigurationError,
    ResearchBudget,
    ResearchError,
    Settings,
)
from research_agent.tools.sec import SecClient
from research_agent.tools.webpage import HttpFetcher

TICKERS = {
    "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


@pytest.fixture
def mock_sec(monkeypatch):
    """Provide mock HTTP clients with a deterministic SEC rate limiter clock."""
    clock = [100.0]
    sleeps = []
    clients = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(
        "research_agent.tools.sec.time",
        SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep),
    )

    def create(
        routes, *, retries=0, sec_user_agent="DeepEquity test contact@example.test"
    ):
        requests = []

        def handle(request):
            requests.append(request)
            result = routes[request.url.path]
            if isinstance(result, list):
                result = result.pop(0)
            if isinstance(result, httpx.Response):
                return result
            if isinstance(result, str):
                return httpx.Response(
                    200, text=result, headers={"Content-Type": "text/html"}
                )
            return httpx.Response(200, json=result)

        settings = Settings(
            sec_user_agent=sec_user_agent,
            max_retries=retries,
            sec_interval_seconds=0.11,
        )
        budget = ResearchBudget(settings)
        client = httpx.Client(transport=httpx.MockTransport(handle))
        clients.append(client)
        fetcher = HttpFetcher(
            settings,
            budget,
            client=client,
            resolve_host=lambda hostname: ["93.184.216.34"],
        )
        return SecClient(settings, fetcher), budget, requests, sleeps

    yield create
    for client in clients:
        client.close()


@pytest.mark.parametrize(
    ("query", "ticker", "cik"),
    [
        ("NVIDIA", "NVDA", "0001045810"),
        ("Apple", "AAPL", "0000320193"),
        ("Microsoft", "MSFT", "0000789019"),
        ("nvda", "NVDA", "0001045810"),
        ("APPLE, INC.", "AAPL", "0000320193"),
    ],
)
def test_resolve_company_normalizes_names_and_pads_cik(mock_sec, query, ticker, cik):
    client, _, requests, _ = mock_sec({"/files/company_tickers.json": TICKERS})
    company = client.resolve_company(query)
    assert (company.ticker, company.cik) == (ticker, cik)
    assert requests[0].headers["User-Agent"] == "DeepEquity test contact@example.test"
    assert client.resolve_company(ticker) == company
    assert len(requests) == 1


def test_resolve_unique_prefix_and_reject_ambiguous_company(mock_sec):
    tickers = {
        **TICKERS,
        "3": {"cik_str": 1, "ticker": "ABC", "title": "Example Energy Resources Corp"},
        "4": {"cik_str": 2, "ticker": "DEF", "title": "Example Mining Corp"},
    }
    client, _, _, _ = mock_sec({"/files/company_tickers.json": tickers})
    assert client.resolve_company("Example Energy").ticker == "ABC"
    with pytest.raises(ResearchError, match="ambiguous") as error:
        client.resolve_company("Example")
    assert error.value.code == "ambiguous_company"
    with pytest.raises(ResearchError) as missing:
        client.resolve_company("Unknown Company")
    assert missing.value.code == "company_not_found"


@pytest.mark.parametrize(
    "payload", [{}, {"0": {"cik_str": "wrong", "title": "Name", "ticker": "ABC"}}]
)
def test_malformed_company_payload_is_not_no_results(mock_sec, payload):
    client, _, _, _ = mock_sec({"/files/company_tickers.json": payload})
    with pytest.raises(ResearchError) as error:
        client.resolve_company("AAPL")
    assert error.value.code == "sec_parse_error"


@pytest.mark.parametrize("status", [403, 429])
def test_sec_http_errors_are_not_empty_results(mock_sec, status):
    client, budget, _, _ = mock_sec(
        {"/files/company_tickers.json": httpx.Response(status)}
    )
    with pytest.raises(ResearchError):
        client.resolve_company("AAPL")
    assert budget.counts["request"] == 1


def test_retry_attempts_are_budgeted_and_sec_throttled(mock_sec):
    responses = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json=TICKERS),
    ]
    client, budget, requests, sleeps = mock_sec(
        {"/files/company_tickers.json": responses}, retries=1
    )
    assert client.resolve_company("AAPL").ticker == "AAPL"
    assert budget.counts["request"] == 2
    assert len(requests) == 2
    assert sleeps == pytest.approx([0.11])


def test_concurrent_workers_share_one_sec_limiter(mock_sec):
    client, _, _, sleeps = mock_sec({})
    url = "https://www.sec.gov/files/company_tickers.json"
    with ThreadPoolExecutor(max_workers=3) as workers:
        list(workers.map(lambda number: client._before_request(url, url), range(3)))
    assert sleeps == pytest.approx([0.11, 0.11])


def test_missing_sec_contact_prevents_http(mock_sec):
    client, budget, requests, _ = mock_sec({}, sec_user_agent="")
    with pytest.raises(ConfigurationError, match="SEC_USER_AGENT"):
        client.resolve_company("AAPL")
    assert budget.counts["request"] == 0
    assert requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/earnings.htm",
        "http://www.sec.gov/Archives/edgar/data/320193/000032019326000001/earnings.htm",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000002/earnings.htm",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/%2e%2e/earnings.htm",
        "https://www.sec.gov@evil.example/earnings.htm",
        "https://[invalid/earnings.htm",
    ],
)
def test_document_rejects_external_or_other_accession_urls(mock_sec, url):
    client, budget, requests, _ = mock_sec({})
    directory = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/"
    with pytest.raises(ResearchError) as failure:
        client._get_document(url, directory)
    assert failure.value.code == "unsafe_sec_url"
    assert budget.counts["request"] == 0
    assert requests == []


def test_download_document_rejects_cross_accession_redirect(mock_sec):
    directory = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/"
    path = "/Archives/edgar/data/320193/000032019326000001/earnings.htm"
    url = directory + "earnings.htm"
    client, _, _, _ = mock_sec({path: "<p>Primary document text</p>"})
    assert "Primary document text" in client._get_document(url, directory).text
    redirect = httpx.Response(
        302,
        headers={
            "Location": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000002/other.htm"
        },
    )
    client, _, requests, _ = mock_sec({path: redirect})
    with pytest.raises(ResearchError) as failure:
        client._get_document(url, directory)
    assert failure.value.code == "unsafe_sec_url"
    assert len(requests) == 1
