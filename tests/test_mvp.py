"""Exercise plain-text research and Markdown delivery without the old ledger."""

import json
import sys
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from fakes import ToolCallingFakeModel, call_tool
from langchain_core.messages import AIMessage, AIMessageChunk

from research_agent import runner
from research_agent.config import ResearchBudget, Settings
from research_agent.mvp import MvpRun
from research_agent.runtime.reporting import check_report, normalize_markdown
from research_agent.skill_loader import load_skill
from research_agent.tools.webpage import HttpFetcher

URL = "https://example.test/annual"


def report(url=URL):
    return "# Synthetic stock research\n\n" + "\n\n".join(
        f"## {index}. {heading}\n\n"
        f"2025A Revenue was USD 100 million; 2026E is an explicit assumption. "
        f"Cash generation supports a conditional interpretation. [Annual report]({url})"
        for index, heading in enumerate(load_skill(kind="stock").report_headings, 1)
    )


@pytest.fixture
def mvp_case(tmp_path, monkeypatch):
    owned = []

    def build(responses):
        settings = Settings()
        budget = ResearchBudget(settings)
        requests = []

        def respond(request):
            requests.append(
                "https://" + request.headers["Host"] + request.url.raw_path.decode()
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<title>Annual report</title><p>2025 revenue was USD 100 million.</p>",
            )

        client = httpx.Client(transport=httpx.MockTransport(respond))
        fetcher = HttpFetcher(
            settings, budget, client=client, resolve_host=lambda host: ["93.184.216.34"]
        )
        search = SimpleNamespace(budget=budget, close=lambda: None)
        model = ToolCallingFakeModel(responses=responses)
        run = MvpRun(
            settings,
            "Full synthetic stock research",
            "SYN",
            date(2026, 9, 30),
            tmp_path / str(len(owned)),
            model=model,
            fetcher=fetcher,
            search=search,
        )

        def discover_sec_sources(limit: int = 6):
            """Discover synthetic filings without a provider call."""
            return {"filings": [], "company": {"ticker": "SYN"}}

        monkeypatch.setattr(
            run.source_tools, "discover_sec_sources", discover_sec_sources
        )
        owned.append(client)
        return run, model, requests

    yield build
    for client in owned:
        client.close()


def read_and_note():
    return [
        call_tool("read_source", {"url": URL}),
        call_tool(
            "save_notes",
            {
                "markdown": f"2025A: USD 100 million revenue. [Source]({URL}). 2026E growth is an assumption; data gaps remain explicit."
            },
        ),
        AIMessage(
            content="Research notes: cash generation and assumptions must be compared with the dated price."
        ),
    ]


def test_plain_plan_continues_to_sources_and_markdown_without_old_ledger(mvp_case):
    run, model, requests = mvp_case(
        [
            AIMessage(
                content="Plan: read annual financials, then compare cash flow and valuation."
            ),
            *read_and_note(),
            AIMessage(content=report()),
        ]
    )
    result = run.run()
    assert result["status"] == "generated" and result["quality_accepted"] is None
    assert result["report_written"] and result["checks"]["errors"] == []
    assert model.i == 5 and requests == [URL]
    assert "Plan:" in (run.directory / "notes.md").read_text(encoding="utf-8")
    source = json.loads((run.directory / "sources.jsonl").read_text(encoding="utf-8"))
    assert "USD 100 million" in source["text"]
    output = (run.directory / "2026-10-13-GOOG.md").read_text(encoding="utf-8")
    assert output.count("\n## ") == 9 and "本次实际读取的来源" in output
    assert not (run.directory / "sources").exists()
    assert not (run.directory / "evidence.json").exists()
    assert not {
        "research_agent.evidence",
        "research_agent.schemas",
        "research_agent.workflow",
    } & set(sys.modules)


def test_unread_citation_is_repaired_once_without_losing_original_draft(mvp_case):
    run, model, _ = mvp_case(
        [
            *read_and_note(),
            AIMessage(content=report("https://example.test/not-read")),
            AIMessage(content=report()),
        ]
    )
    result = run.run()
    assert result["status"] == "generated" and model.i == 5
    assert "not-read" in (run.directory / "report.draft.md").read_text(encoding="utf-8")
    assert "not-read" not in (run.directory / "2026-10-13-GOOG.md").read_text(encoding="utf-8")
    assert (run.directory / "notes.md").exists()


def test_repeated_bad_report_keeps_materials_and_drafts(mvp_case):
    bad = AIMessage(content=report("https://example.test/not-read"))
    run, model, _ = mvp_case([*read_and_note(), bad, bad])
    result = run.run()
    assert result["status"] == "partial" and not result["report_written"]
    assert "unread_citation" in result["checks"]["errors"] and model.i == 5
    for name in ("sources.jsonl", "notes.md", "report.draft.md", "report.repair.md"):
        assert (run.directory / name).stat().st_size > 0


def test_report_stream_failure_keeps_the_received_markdown(mvp_case, monkeypatch):
    run, model, _ = mvp_case(read_and_note())

    def broken_stream(*args, **kwargs):
        yield AIMessageChunk(content="# Partial streamed report\n")
        raise RuntimeError("private-provider-error-must-not-be-exported")

    monkeypatch.setattr(type(model), "stream", broken_stream)
    result = run.run()
    assert result["status"] == "partial" and not result["report_written"]
    assert (run.directory / "report.draft.md").read_text(
        encoding="utf-8"
    ) == "# Partial streamed report\n"
    assert (run.directory / "sources.jsonl").exists()
    assert "private-provider-error" not in (run.directory / "result.json").read_text(
        encoding="utf-8"
    )


def test_basic_checks_reject_empty_sections_and_unread_urls_without_semantic_scoring():
    headings = load_skill(kind="stock").report_headings
    normalized = normalize_markdown(
        "```markdown\n" + report().replace("## 1.", "# 1、") + "\n```", headings
    )
    assert check_report(normalized, headings, {URL})["errors"] == []
    assert "unread_citation" in check_report(normalized, headings, set())["errors"]
    empty = "\n\n".join(f"## {i}. {heading}" for i, heading in enumerate(headings, 1))
    assert any(
        item.startswith("section_empty")
        for item in check_report(empty, headings, set())["errors"]
    )


@pytest.mark.parametrize(
    "extra",
    [
        "[unread]: //unread.example/report",
        '<a href="//unread.example/report">link</a>',
        "<a href=/relative>link</a>",
    ],
)
def test_nonstandard_unread_links_cannot_bypass_citation_check(extra):
    headings = load_skill(kind="stock").report_headings
    assert (
        "unread_citation"
        in check_report(report() + "\n" + extra, headings, {URL})["errors"]
    )


def test_read_url_with_parentheses_is_not_truncated():
    url = "https://example.test/report_(2025)"
    headings = load_skill(kind="stock").report_headings
    assert check_report(report(url), headings, {url})["errors"] == []


def test_adjacent_chinese_links_and_punctuation_are_not_part_of_urls():
    headings = load_skill(kind="stock").report_headings
    text = report() + f"\n[年报]({URL})、[季报]({URL})，2026-06-27）；[附注](<{URL}>)。"
    assert check_report(text, headings, {URL})["errors"] == []


def test_mvp_cli_accepts_explicit_path_and_rejects_funds(tmp_path, capfd, monkeypatch):
    calls = []

    def fake_run(settings, **arguments):
        calls.append(arguments)
        return {"status": "generated", "execution_path": "mvp"}

    monkeypatch.setattr("research_agent.mvp.run_mvp", fake_run)
    assert (
        runner.main(
            [
                "Synthetic full research",
                "--execution-path",
                "mvp",
                "--ticker",
                "SYN",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert json.loads(capfd.readouterr().out)["execution_path"] == "mvp"
    assert calls[0]["ticker"] == "SYN"
    with pytest.raises(SystemExit):
        runner.main(
            [
                "Fund question",
                "--execution-path",
                "mvp",
                "--ticker",
                "SYN",
                "--target-type",
                "fund",
            ]
        )
    assert len(calls) == 1
