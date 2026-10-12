"""Exercise HTTP and Tavily boundaries entirely with local fake responses."""

from datetime import date

import httpx
import pytest
from tavily.errors import ForbiddenError, UsageLimitExceededError

from research_agent.config import (
    ConfigurationError,
    ResearchBudget,
    ResearchError,
    Settings,
)
from research_agent.tools import WebSearch
from research_agent.tools.webpage import FetchedPage, HttpFetcher, extract_web_content


def make_fetcher(handler, **settings_values):
    settings = Settings(**settings_values)
    budget = ResearchBudget(settings)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpFetcher(
        settings, budget, client, resolve_host=lambda host: ["93.184.216.34"]
    )


def test_full_fetched_text_survives_a_short_reading_window():
    page = FetchedPage(
        url="https://example.test/long",
        content_type="text/html",
        text="<p>" + "Intro " * 2000 + "</p><p>Revenue tail &amp; unit context</p>",
    )
    extracted = extract_web_content(page, 1000)
    assert extracted.truncated
    assert "Revenue tail & unit context" in extracted.full_text
    assert "Revenue tail" not in extracted.snippets[0].content
    assert "full_text" not in extracted.model_dump()


def test_http_retries_redirects_final_url_and_attempt_hook(monkeypatch):
    monkeypatch.setattr("research_agent.tools.webpage.time.sleep", lambda seconds: None)
    responses = [
        httpx.Response(503),
        httpx.Response(302, headers={"location": "/earnings"}),
        httpx.Response(
            200, text="<p>Revenue</p>", headers={"content-type": "text/html"}
        ),
    ]
    requests = []

    def handler(request):
        requests.append(request)
        return responses.pop(0)

    fetcher = make_fetcher(handler)
    observed_urls = []
    page = fetcher.get("https://example.com/ir", before_request=observed_urls.append)
    assert page.url == "https://example.com/earnings"
    assert fetcher.budget.counts["request"] == 3
    assert observed_urls == [
        "https://example.com/ir",
        "https://example.com/ir",
        "https://example.com/earnings",
    ]
    assert all(request.url.host == "93.184.216.34" for request in requests)
    assert all(request.headers["host"] == "example.com" for request in requests)
    assert all(
        request.extensions["sni_hostname"] == "example.com" for request in requests
    )
    assert requests[0].extensions["timeout"]["read"] == 20


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/private",
        "http://169.254.169.254/meta",
        "http://[::1]/",
        "file:///local",
        "https://name:password@example.com/",
    ],
)
def test_rejects_unsafe_urls_before_request(target):
    def handler(request):
        pytest.fail("Unsafe destination reached transport")

    fetcher = make_fetcher(handler)
    with pytest.raises(ResearchError) as caught:
        fetcher.get(target)
    assert caught.value.code == "unsafe_url"
    assert fetcher.budget.counts["request"] == 0


def test_rejects_mixed_public_private_dns_answer():
    fetcher = make_fetcher(lambda request: pytest.fail("Private DNS reached transport"))
    fetcher.resolve_host = lambda host: ["93.184.216.34", "10.0.0.1"]
    with pytest.raises(ResearchError, match="public HTTP"):
        fetcher.get("https://example.com/")


def test_redirect_cannot_reach_private_network():
    fetcher = make_fetcher(
        lambda request: httpx.Response(
            302, headers={"location": "http://127.0.0.1/secret"}
        )
    )
    with pytest.raises(ResearchError) as caught:
        fetcher.get("https://example.com/")
    assert caught.value.code == "unsafe_url"
    assert fetcher.budget.counts["request"] == 1


@pytest.mark.parametrize("status", [403, 404])
def test_http_permanent_errors_are_not_retried(status):
    fetcher = make_fetcher(lambda request: httpx.Response(status))
    with pytest.raises(ResearchError) as caught:
        fetcher.get("https://example.com/")
    assert caught.value.code == f"http_{status}"
    assert fetcher.budget.counts["request"] == 1


def test_transport_error_is_bounded_and_does_not_expose_url(monkeypatch, caplog):
    monkeypatch.setattr("research_agent.tools.webpage.time.sleep", lambda seconds: None)

    def handler(request):
        raise httpx.ConnectError("secret-token", request=request)

    fetcher = make_fetcher(handler)
    with pytest.raises(ResearchError) as caught:
        fetcher.get("https://example.com/?key=secret-token")
    assert caught.value.code == "transport_error"
    assert fetcher.budget.counts["request"] == 2
    assert "secret-token" not in str(caught.value) + caplog.text


def test_retry_stops_at_shared_request_budget(monkeypatch):
    monkeypatch.setattr("research_agent.tools.webpage.time.sleep", lambda seconds: None)
    fetcher = make_fetcher(lambda request: httpx.Response(429), max_requests=1)
    with pytest.raises(ResearchError) as caught:
        fetcher.get("https://example.com/")
    assert caught.value.code == "budget_exceeded"
    assert fetcher.budget.counts["request"] == 1


def test_response_and_content_type_limits():
    fetcher = make_fetcher(
        lambda request: httpx.Response(
            200, text="a" * 1025, headers={"content-type": "text/html"}
        ),
        max_response_bytes=1024,
    )
    with pytest.raises(ResearchError) as caught:
        fetcher.get("https://example.com/")
    assert caught.value.code == "response_too_large"
    fetcher = make_fetcher(
        lambda request: httpx.Response(
            200, content=b"binary", headers={"content-type": "application/pdf"}
        )
    )
    with pytest.raises(ResearchError) as caught:
        fetcher.get("https://example.com/report.pdf")
    assert caught.value.code == "unsupported_content_type"


@pytest.mark.parametrize("text", ["not json", "[]"])
def test_json_rejects_invalid_objects(text):
    fetcher = make_fetcher(
        lambda request: httpx.Response(
            200, text=text, headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(ResearchError) as caught:
        fetcher.get_json("https://example.com/data.json")
    assert caught.value.code == "parse_error"


def test_cross_host_redirect_strips_sensitive_headers():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                302, headers={"location": "https://other.example.com/news"}
            )
        return httpx.Response(
            200, text="<p>Result</p>", headers={"content-type": "text/html"}
        )

    fetcher = make_fetcher(handler)
    fetcher.get(
        "https://example.com/",
        headers={"Authorization": "private", "Cookie": "private"},
    )
    assert "authorization" not in requests[1].headers
    assert not requests[1].headers.get("cookie")
    assert requests[1].headers["host"] == "other.example.com"


def test_pinned_shared_ip_never_leaks_cookies_across_origins_or_retries(monkeypatch):
    monkeypatch.setattr("research_agent.tools.webpage.time.sleep", lambda seconds: None)
    observed = []
    responses = [
        httpx.Response(503, headers={"set-cookie": "retry_cookie=synthetic; Path=/"}),
        httpx.Response(
            200,
            text="<p>Source A</p>",
            headers={
                "content-type": "text/html",
                "set-cookie": "source_cookie=synthetic; Path=/",
            },
        ),
        httpx.Response(
            302,
            headers={
                "location": "https://source-c.example/page",
                "set-cookie": "redirect_cookie=synthetic; Path=/",
            },
        ),
        httpx.Response(
            200, text="<p>Source C</p>", headers={"content-type": "text/html"}
        ),
    ]

    def handler(request):
        observed.append(request)
        return responses.pop(0)

    fetcher = make_fetcher(handler)
    fetcher.client.headers["Cookie"] = "client_cookie=synthetic"
    fetcher.get(
        "https://source-a.example/page",
        headers={"cOoKiE": "caller_cookie=synthetic", "aCcEpT-EnCoDiNg": "gzip"},
    )
    assert list(fetcher.client.cookies.jar)
    fetcher.get("https://source-b.example/page")
    assert [request.headers["host"] for request in observed] == [
        "source-a.example",
        "source-a.example",
        "source-b.example",
        "source-c.example",
    ]
    assert all(request.url.host == "93.184.216.34" for request in observed)
    assert all(not request.headers.get("cookie") for request in observed)
    assert all(request.headers["accept-encoding"] == "identity" for request in observed)


@pytest.mark.parametrize("content_encoding", ["gzip", "GZip", "br", "identity, gzip"])
def test_compressed_body_is_rejected_before_reading_or_decoding(content_encoding):
    class UnreadBody(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Compressed content must be rejected before body iteration")

    fetcher = make_fetcher(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": content_encoding},
            stream=UnreadBody(),
        )
    )
    with pytest.raises(ResearchError) as caught:
        fetcher.get("https://example.com/oversized-compressed-document")
    assert caught.value.code == "unsupported_content_encoding"
    assert fetcher.budget.counts["request"] == 1


def test_extraction_retains_financial_tables_and_traceable_snippets():
    page = FetchedPage(
        url="https://example.com/earnings",
        content_type="text/html",
        text="<html><head><title>Quarter results</title></head><body><nav>Skip me</nav><h1>USD millions</h1><table><tr><th>Period</th><th>Revenue</th></tr><tr><td>Q1</td><td>1,200</td></tr></table><script>evil()</script><p>Details "
        + "x" * 8000
        + "</p></body></html>",
    )
    result = extract_web_content(page, max_chars=6500)
    text = "".join(snippet.content for snippet in result.snippets)
    assert result.title == "Quarter results"
    assert (
        "USD millions" in text and "Period | Revenue" in text and "Q1 | 1,200" in text
    )
    assert "evil" not in text and "Skip me" not in text
    assert len(text) == 6500 and result.truncated
    assert [snippet.snippet_id for snippet in result.snippets] == ["p1", "p2@6500"]
    assert result.snippets[1].locator == "extracted_text_chars:6000:6500"


def test_extraction_empty_is_not_success():
    page = FetchedPage(
        url="https://example.com/",
        content_type="text/html",
        text="<title>Title</title><script>nothing</script>",
    )
    with pytest.raises(ResearchError) as caught:
        extract_web_content(page, 500)
    assert caught.value.code == "empty_content"


def test_extraction_omits_hidden_xbrl_before_applying_reading_limit():
    metadata_tags = [
        "ix:header",
        "ix:hidden",
        "xbrli:context",
        "xbrli:unit",
        "context",
        "unit",
    ]
    hidden_metadata = "".join(
        f"<{tag}>" + "hidden-metadata " * 100 + f"</{tag}>" for tag in metadata_tags
    )
    page = FetchedPage(
        url="https://example.com/filing",
        content_type="text/html",
        text=(
            "<html><body>"
            + hidden_metadata
            + "<h1>Three Months Ended July 26, 2026; USD millions</h1>"
            + "<table><tr><th>Period</th><th>Revenue</th></tr>"
            + '<tr><td>Q2</td><td><ix:nonFraction contextRef="quarter" '
            + 'unitRef="USD">1,200</ix:nonFraction></td></tr></table>'
            + "</body></html>"
        ),
    )
    result = extract_web_content(page, max_chars=500)
    text = "".join(snippet.content for snippet in result.snippets)
    assert "hidden-metadata" not in text
    assert "Three Months Ended July 26, 2026; USD millions" in text
    assert "Period | Revenue" in text and "Q2 | 1,200" in text
    assert not result.truncated
    assert result.snippets[0].locator == f"extracted_text_chars:0:{len(text)}"


@pytest.mark.parametrize(
    "style",
    [
        "display:none",
        "color: red; DISPLAY : NoNe !important;",
        "visibility: hidden",
        "  VISIBILITY : HIDDEN ! IMPORTANT ; color: red",
    ],
)
def test_extraction_omits_explicitly_hidden_inline_styles(style):
    page = FetchedPage(
        url="https://example.com/earnings",
        content_type="text/html",
        text=(
            f'<div style="{style}"><span style="display: none">nested-hidden</span>'
            + "hidden-content " * 100
            + '</div><p style="display: block; visibility: visible">Revenue 1,200</p>'
        ),
    )
    result = extract_web_content(page, max_chars=500)
    assert result.snippets[0].content == "Revenue 1,200"
    assert not result.truncated


def test_focus_reads_later_text_with_absolute_snippet_ids_and_offsets():
    normalized_text = "x" * 18500 + "Management Discussion" + "tail" * 4000
    page = FetchedPage(
        url="https://example.com/filing",
        content_type="text/html",
        text=f"<p>{normalized_text}</p>",
    )
    beginning = extract_web_content(page, 6500)
    assert "Management Discussion" not in "".join(
        part.content for part in beginning.snippets
    )
    focused = extract_web_content(page, 6500, focus="MANAGEMENT DISCUSSION")
    assert [part.snippet_id for part in focused.snippets] == ["p4", "p5@24500"]
    assert focused.snippets[0].locator == "extracted_text_chars:18000:24000"
    assert focused.snippets[1].locator == "extracted_text_chars:24000:24500"
    assert (
        "".join(part.content for part in focused.snippets)
        == normalized_text[18000:24500]
    )
    assert focused.truncated


def test_focus_offsets_do_not_shift_when_prior_text_casefold_expands():
    normalized_text = (
        "Straße " * 2800 + "Management & Analysis (MD&A)" + " " + "z" * 3000
    )
    page = FetchedPage(
        url="https://example.com/filing",
        content_type="text/html",
        text=f"<p>{normalized_text}</p>",
    )
    focused = extract_web_content(page, 6000, focus="management & analysis (md&a)")
    expected_start = normalized_text.index("Management") // 6000 * 6000
    assert (
        focused.snippets[0].content
        == normalized_text[expected_start : expected_start + 6000]
    )
    assert (
        focused.snippets[0].locator
        == f"extracted_text_chars:{expected_start}:{len(normalized_text)}"
    )
    assert focused.truncated


def test_later_read_does_not_reuse_truncated_snippet_identity():
    normalized_text = "x" * 6400 + "Target" + "z" * 14000
    page = FetchedPage(
        url="https://example.com/filing",
        content_type="text/html",
        text=f"<p>{normalized_text}</p>",
    )
    beginning = extract_web_content(page, 6500)
    focused = extract_web_content(page, 6000, focus="Target")
    repeated_prefix = extract_web_content(page, 500, focus="Target")
    assert beginning.snippets[-1].snippet_id == "p2@6500"
    assert focused.snippets[0].snippet_id == "p2"
    assert beginning.snippets[-1].content == normalized_text[6000:6500]
    assert focused.snippets[0].content == normalized_text[6000:12000]
    assert repeated_prefix.snippets[0] == beginning.snippets[-1]


@pytest.mark.parametrize("focus", ["a" * 201, None])
def test_focus_rejects_invalid_input(focus):
    page = FetchedPage(
        url="https://example.com/filing",
        content_type="text/html",
        text="<p>Management</p>",
    )
    with pytest.raises(ResearchError) as caught:
        extract_web_content(page, 6000, focus=focus)
    assert caught.value.code == "invalid_input"


def test_focus_reports_missing_literal_and_insufficient_aligned_window():
    page = FetchedPage(
        url="https://example.com/filing",
        content_type="text/html",
        text="<p>" + "x" * 5900 + "Management" + "</p>",
    )
    with pytest.raises(ResearchError) as caught:
        extract_web_content(page, 6000, focus="Revenue")
    assert caught.value.code == "text_not_found"
    with pytest.raises(ResearchError) as caught:
        extract_web_content(page, 500, focus="Management")
    assert caught.value.code == "invalid_input"


def test_focus_limits_excerpt_count_even_with_large_character_limit():
    normalized_text = "x" * 7000 + "Target" + "z" * 200000
    page = FetchedPage(
        url="https://example.com/filing",
        content_type="text/html",
        text=f"<p>{normalized_text}</p>",
    )
    focused = extract_web_content(page, 300000, focus="Target")
    assert len(focused.snippets) == 30
    assert focused.snippets[0].snippet_id == "p2"
    assert focused.snippets[-1].snippet_id == "p31"
    assert sum(len(part.content) for part in focused.snippets) == 180000
    assert all(len(part.content) <= 6000 for part in focused.snippets)
    assert focused.truncated


class FakeSearchClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_search(client, **overrides):
    settings = Settings(tavily_api_key="test-only-not-real", **overrides)
    return WebSearch(settings, ResearchBudget(settings), client)


def test_missing_key_never_enters_keyless_search():
    settings = Settings()
    client = FakeSearchClient([])
    search = WebSearch(settings, ResearchBudget(settings), client)
    with pytest.raises(ConfigurationError, match="TAVILY_API_KEY"):
        search.search("company earnings")
    assert client.calls == []
    assert search.budget.counts["request"] == 0


def test_search_empty_is_successful_discovery():
    client = FakeSearchClient([{"results": []}])
    search = make_search(client)
    result = search.search("company earnings", topic="news", domains=["example.com"])
    assert result.status == "empty" and not result.results
    assert client.calls[0]["timeout"] == 20
    assert client.calls[0]["include_raw_content"] is False
    assert client.calls[0]["auto_parameters"] is False


def test_search_retry_counts_attempts_and_bounds_results(monkeypatch):
    monkeypatch.setattr("research_agent.tools.web.time.sleep", lambda seconds: None)
    item = {
        "url": "https://example.com/earnings",
        "title": "title",
        "content": "x" * 3000,
        "published_date": "2026-10-01",
    }
    client = FakeSearchClient(
        [UsageLimitExceededError("secret-token"), {"results": [item, item]}]
    )
    search = make_search(client, max_search_results=1)
    result = search.search("earnings")
    assert result.status == "ok" and result.truncated
    assert len(result.results) == 1 and len(result.results[0].snippet) == 2000
    assert result.results[0].published_at == date(2026, 10, 1)
    assert search.budget.counts["request"] == 2


def test_search_permanent_failure_safe_and_not_retried(caplog):
    client = FakeSearchClient([ForbiddenError("secret-token")])
    search = make_search(client)
    with pytest.raises(ResearchError) as caught:
        search.search("earnings")
    assert caught.value.code == "search_error"
    assert search.budget.counts["request"] == 1
    assert "secret-token" not in str(caught.value) + caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": None},
        {"results": [{"url": "file:///private"}]},
        {"results": [{"url": {}}]},
        {"results": [{"url": "https://example.com", "content": 123}]},
    ],
)
def test_search_malformed_response_is_not_empty(payload):
    search = make_search(FakeSearchClient([payload]))
    with pytest.raises(ResearchError) as caught:
        search.search("earnings")
    assert caught.value.code == "parse_error"
