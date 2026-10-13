"""Exercise agent stages through explicit inputs without a complete MVP run."""

import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import ToolCallingFakeModel, call_tool
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from research_agent.agent.research import run_research
from research_agent.agent.synthesis import synthesize_report
from research_agent.config import ResearchBudget, ResearchError, Settings
from research_agent.skill_loader import load_skill

ROOT = Path(__file__).resolve().parents[1]
URL = "https://example.test/apple-annual"
AS_OF = date(2026, 10, 9)


def source(text="FY2025 revenue was USD 100 million."):
    return {
        "title": "Synthetic Apple annual report",
        "url": URL,
        "published_at": "2026-01-28",
        "retrieved_at": "2026-10-13T00:00:00+00:00",
        "text": text,
        "truncated": False,
    }


def options_callback(budget, stages):
    def options(stage):
        stages.append(stage)
        budget.consume("model", reserve=2 if stage == "research" else 0)
        return {}

    return options


def test_agent_modules_import_without_run_coordinator_or_provider_initialization():
    script = """
import json, socket, sys
def forbidden(*args, **kwargs):
    raise AssertionError("Agent imports must not access the network.")
socket.create_connection = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
import research_agent.agent.research
import research_agent.agent.synthesis
assert not {"research_agent.mvp", "research_agent.runner",
            "research_agent.tools.mvp", "research_agent.runtime.supervisor"} & set(sys.modules)
assert not any(name == prefix or name.startswith(prefix + ".")
               for name in sys.modules
               for prefix in ("anthropic", "langchain_anthropic"))
print(json.dumps({"status": "ok"}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == {"status": "ok"}


@pytest.mark.parametrize("failure", ["unavailable", "invalid_input"])
def test_research_closes_acquisition_and_returns_tool_gaps_to_the_same_model(failure):
    settings = Settings()
    budget = ResearchBudget(settings)
    notes, events, stages, responses = [], [], [], []

    @tool
    def save_notes(markdown: str) -> dict:
        """Save synthetic notes or raise a local input failure."""
        if failure == "invalid_input":
            raise ValueError("private tool details")
        notes.append(markdown)
        return {"saved": True}

    def finish(messages):
        replies = [message for message in messages if isinstance(message, ToolMessage)]
        assert len(replies) == 1
        gap = json.loads(replies[0].content)
        assert gap["gap"] is True
        assert gap["error"] == (
            "tool_unavailable" if failure == "unavailable" else "tool_input_invalid"
        )
        assert "private tool details" not in replies[0].content
        assert messages[-1].content.startswith("Research acquisition is closed.")
        return AIMessage(
            content="Saved research remains useful; the input gap is explicit."
        )

    model = ToolCallingFakeModel(
        responses=[
            call_tool(
                "read_source" if failure == "unavailable" else "save_notes",
                {"url": URL} if failure == "unavailable" else {"markdown": "Notes"},
            ),
            finish,
        ]
    )
    tools = SimpleNamespace(
        discover_sec_sources=lambda: {"filings": []},
        tools=lambda: [save_notes],
        sources=[source()],
    )
    run_research(
        model=model,
        methodology="Synthetic research rules",
        query="Research Apple",
        ticker="AAPL",
        as_of=AS_OF,
        source_tools=tools,
        budget=budget,
        research_until=time.monotonic() + 30,
        model_options=options_callback(budget, stages),
        record_response=lambda response, stage: responses.append(stage),
        save_note=lambda text: notes.append(text) if text.strip() else None,
        emit_event=events.append,
    )
    assert stages == responses == ["research", "research"]
    assert budget.counts["model"] == 2 and budget.counts["request"] == 0
    assert notes == ["Saved research remains useful; the input gap is explicit."]
    assert [item["function"]["name"] for item in model.bound_tools] == ["save_notes"]
    assert bool(events) is (failure == "invalid_input")


def synthesis_arguments(directory, model):
    bundle = load_skill(kind="stock", focused=False)
    budget = ResearchBudget(Settings())
    stages, responses, events = [], [], []
    return {
        "model": model,
        "methodology": bundle.methodology,
        "query": "Research Apple",
        "ticker": "AAPL",
        "as_of": AS_OF,
        "headings": bundle.report_headings,
        "sources": [source()],
        "allowed_urls": {URL},
        "research_errors": [{"stage": "research", "code": "synthetic_timeout"}],
        "budget": budget,
        "directory": directory,
        "model_options": options_callback(budget, stages),
        "record_response": lambda response, stage: responses.append(stage),
        "emit_event": events.append,
    }, (stages, responses, events)


def test_synthesis_uses_saved_notes_and_read_windows_without_tool_or_run_objects(
    tmp_path,
):
    notes = "Financial notes survive a research interruption."
    (tmp_path / "notes.md").write_text(notes, encoding="utf-8")

    def write_report(messages):
        packet = json.loads(messages[1].content)
        assert packet["ticker"] == "AAPL" and packet["as_of"] == str(AS_OF)
        assert packet["notes"] == notes
        assert packet["research_errors"][0]["code"] == "synthetic_timeout"
        assert [item["text"] for item in packet["read_sources"]] == [
            source()["text"],
            "FY2025 operating cash flow was USD 20 million.",
        ]
        return AIMessage(
            content="\n\n".join(
                f"## {index}. {heading}\n\n"
                f"FY2025 figures are in USD million; unavailable facts remain gaps. [Annual]({URL})"
                for index, heading in enumerate(packet["headings"], 1)
            )
        )

    model = ToolCallingFakeModel(responses=[write_report])
    arguments, (stages, responses, events) = synthesis_arguments(tmp_path, model)
    arguments["sources"] += [
        source(),
        source("FY2025 operating cash flow was USD 20 million."),
    ]
    checks = synthesize_report(**arguments)

    assert checks == {"errors": [], "warnings": [], "unread_urls": []}
    assert stages == responses == ["synthesis"]
    assert arguments["budget"].counts["model"] == 1
    report = (tmp_path / "2026-10-13-GOOG.md").read_text(encoding="utf-8")
    assert report.count("\n## ") == 8
    assert report.count("- [Synthetic Apple annual report]") == 1
    assert events[0]["source_windows"] == 2
    assert events[-1]["type"] == "report_check"
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == notes


def test_synthesis_without_read_sources_fails_before_spending_a_model_call(tmp_path):
    model = ToolCallingFakeModel(responses=[])
    arguments, (stages, responses, events) = synthesis_arguments(tmp_path, model)
    arguments["sources"] = []

    with pytest.raises(ResearchError) as error:
        synthesize_report(**arguments)

    assert error.value.code == "sources_unavailable"
    assert stages == responses == events == []
    assert arguments["budget"].counts["model"] == 0
    assert not list(tmp_path.iterdir())
