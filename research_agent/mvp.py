"""Run one stock researcher and write Markdown without a structured research ledger."""

import argparse
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from anthropic import APIConnectionError
from langchain.chat_models import init_chat_model
from langsmith import tracing_context

from research_agent.agent import _text
from research_agent.agent.research import run_research
from research_agent.agent.synthesis import synthesize_report
from research_agent.config import (
    ConfigurationError,
    ResearchBudget,
    ResearchError,
    Settings,
)
from research_agent.model_errors import _model_connection_error
from research_agent.runtime import _write_json
from research_agent.runtime.reporting import (
    _citation_targets as _citation_targets,
)
from research_agent.runtime.reporting import (
    _source_list as _source_list,
)
from research_agent.runtime.reporting import (
    canonical_url as canonical_url,
)
from research_agent.runtime.reporting import (
    check_report as check_report,
)
from research_agent.runtime.reporting import (
    normalize_markdown as normalize_markdown,
)
from research_agent.runtime.supervisor import (
    _stop_process_tree as _stop_process_tree,
)
from research_agent.runtime.supervisor import (
    supervise_process as supervise_process,
)
from research_agent.skill_loader import load_skill
from research_agent.tools.mvp import MvpTools
from research_agent.tools.web import WebSearch
from research_agent.tools.webpage import HttpFetcher


def _now():
    return datetime.now(UTC).isoformat()


class MvpRun:
    """Keep read sources and ordinary notes across all later failures."""

    def __init__(
        self,
        settings,
        query,
        ticker,
        as_of,
        directory,
        *,
        model=None,
        fetcher=None,
        search=None,
    ):
        """Accept injected transports and models without reading credential files."""
        if not ticker or as_of > date.today():
            raise ConfigurationError(
                "MVP requires an explicit ticker and a cutoff that is not in the future."
            )
        self.settings, self.query, self.ticker, self.as_of = (
            settings,
            query,
            ticker,
            as_of,
        )
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.bundle = load_skill(kind="stock", focused=False)
        self.budget = (
            fetcher.budget if fetcher is not None else ResearchBudget(settings)
        )
        self.fetcher = fetcher or HttpFetcher(settings, self.budget)
        self.search = search or WebSearch(settings, self.budget)
        if self.search.budget is not self.budget:
            raise ValueError("MVP transports must share their budget.")
        self.model = model
        self.usage = {}
        self.failures = []
        self.research_until = self.budget.started_at + max(
            0, settings.deadline_seconds - min(120, settings.deadline_seconds / 2) - 5
        )
        self.source_tools = MvpTools(
            settings,
            self.budget,
            self.fetcher,
            self.search,
            self.directory,
            as_of,
            ticker,
            event_callback=self.event,
            acquisition_allowed=lambda: (
                time.monotonic()
                < self.research_until - min(20, settings.timeout_seconds)
            ),
        )

    def event(self, event):
        """Append safe diagnostics and the current call counts immediately."""
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {**event, "timestamp": _now(), "calls": dict(self.budget.counts)},
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )
            stream.flush()

    def _note(self, text):
        if text.strip():
            with (self.directory / "notes.md").open("a", encoding="utf-8") as stream:
                stream.write(text.strip() + "\n\n")
                stream.flush()

    def _failure(self, stage, error):
        if isinstance(error, APIConnectionError):
            details = _model_connection_error(error)
        elif isinstance(error, (ResearchError, ConfigurationError)):
            details = {
                "code": getattr(error, "code", "missing_configuration"),
                "message": str(error),
            }
        else:
            details = {
                "code": "local_error",
                "message": f"MVP stage failed ({type(error).__name__}).",
            }
        self.failures.append({"stage": stage, **details})
        self.event({"type": "stage_error", "stage": stage, **details})

    def _options(self, stage):
        remaining = self.budget.remaining()["seconds"]
        if stage == "research":
            remaining = min(remaining, self.research_until - time.monotonic())
        if remaining <= 1:
            raise ResearchError(
                "stage_deadline", "Time reserved for report delivery has been reached."
            )
        self.budget.consume(
            "model",
            reserve=2
            if stage == "research" and self.settings.max_model_calls > 2
            else 0,
        )
        self.event({"type": "model_start", "stage": stage})
        return {
            "timeout": min(self.settings.model_timeout_seconds, remaining - 1),
            "max_tokens": self.settings.max_output_tokens,
            **(
                {"thinking": {"type": "disabled"}}
                if stage == "synthesis"
                and self.settings.structured_thinking == "disabled"
                else {}
            ),
        }

    def _response(self, response, stage):
        usage = getattr(response, "usage_metadata", None) or {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if isinstance(usage.get(key), int):
                self.usage[key] = self.usage.get(key, 0) + usage[key]
        self.event(
            {
                "type": "model_end",
                "stage": stage,
                "characters": len(_text(response)),
                "tool_calls": len(getattr(response, "tool_calls", [])),
                "usage": {key: usage[key] for key in self.usage if key in usage},
            }
        )

    def research(self):
        """Delegate research using the shared model, tools, budget and callbacks."""
        return run_research(
            model=self.model,
            methodology=self.bundle.methodology,
            query=self.query,
            ticker=self.ticker,
            as_of=self.as_of,
            source_tools=self.source_tools,
            budget=self.budget,
            research_until=self.research_until,
            model_options=self._options,
            record_response=self._response,
            save_note=self._note,
            emit_event=self.event,
        )

    def synthesize(self):
        """Delegate delivery with reading windows and explicit runtime callbacks."""
        return synthesize_report(
            model=self.model,
            methodology=self.bundle.methodology,
            query=self.query,
            ticker=self.ticker,
            as_of=self.as_of,
            headings=self.bundle.report_headings,
            sources=self.source_tools.sources,
            allowed_urls=self.source_tools.allowed_urls,
            research_errors=self.failures,
            budget=self.budget,
            directory=self.directory,
            model_options=self._options,
            record_response=self._response,
            emit_event=self.event,
        )

    def run(self):
        """Keep useful artifacts even when research or final delivery fails."""
        started = time.monotonic()
        result = {
            "execution_path": "mvp",
            "run_id": self.directory.name,
            "quality_accepted": None,
            "status": "failed",
            "report_written": False,
            "output_dir": str(self.directory),
        }
        try:
            if self.model is None:
                if not self.settings.model.startswith("anthropic:"):
                    raise ConfigurationError(
                        "MVP currently uses the existing Anthropic-compatible model adapter."
                    )
                self.model = init_chat_model(
                    self.settings.model,
                    api_key=self.settings.require("anthropic"),
                    temperature=0,
                    max_retries=0,
                    timeout=self.settings.model_timeout_seconds,
                    max_tokens=self.settings.max_output_tokens,
                )
            with tracing_context(enabled=self.settings.tracing):
                try:
                    self.research()
                except Exception as error:
                    self._failure("research", error)
                checks = self.synthesize()
            result.update(
                status="generated" if not checks["errors"] else "partial",
                checks=checks,
                report_written=(self.directory / "2026-10-13-GOOG.md").exists(),
            )
        except Exception as error:
            self._failure("delivery", error)
            if any(
                (self.directory / name).exists()
                for name in ("notes.md", "sources.jsonl", "report.draft.md")
            ):
                result["status"] = "partial"
        finally:
            result.update(
                calls=dict(self.budget.counts),
                token_usage=self.usage,
                errors=self.failures,
                latency_seconds=round(time.monotonic() - started, 3),
                source_reads=len(self.source_tools.sources),
            )
            _write_json(self.directory / "result.json", result)
            self.fetcher.close()
            self.search.close()
        return result


def run_mvp(settings, *, query, ticker, as_of, output_dir):
    """Create a fresh supervised stock run with credential-free persisted inputs."""
    if not ticker or as_of > date.today():
        raise ConfigurationError(
            "MVP requires an explicit ticker and a cutoff that is not in the future."
        )
    bundle = load_skill(kind="stock", focused=False)
    directory = Path(output_dir).resolve() / "mvp" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    public_settings = {
        key: getattr(settings, key)
        for key in (
            "model",
            "max_requests",
            "max_tool_calls",
            "max_model_calls",
            "max_output_tokens",
            "deadline_seconds",
            "timeout_seconds",
            "model_timeout_seconds",
            "max_content_chars",
            "structured_thinking",
            "tracing",
        )
    }
    _write_json(
        directory / "inputs.json",
        {
            "query": query,
            "ticker": ticker,
            "as_of": str(as_of),
            "scope": "full",
            "target_type": "stock",
            "settings": public_settings,
            "skill_files": bundle.files,
            "skill_hashes": bundle.hashes,
        },
    )
    return supervise_process(
        [sys.executable, "-m", "research_agent.mvp", "--worker", str(directory)],
        directory,
        settings.deadline_seconds,
    )


def main():
    """Execute the internal worker; the public CLI owns process supervision."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    args = parser.parse_args()
    inputs = json.loads((args.worker / "inputs.json").read_text(encoding="utf-8"))
    settings = Settings.from_env().model_copy(update=inputs["settings"])
    session = MvpRun(
        settings,
        inputs["query"],
        inputs["ticker"],
        date.fromisoformat(inputs["as_of"]),
        args.worker,
    )
    session.run()


if __name__ == "__main__":
    main()
