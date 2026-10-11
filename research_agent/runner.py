"""Run and export the shared Skill-driven workflow through the command line."""

import argparse
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

from anthropic import APIConnectionError
from langsmith import tracing_context

from research_agent.config import ConfigurationError, ResearchError, Settings
from research_agent.report import export_run
from research_agent.skill_loader import load_adapter
from research_agent.workflow import create_research_agent


def run_research(run, *, model=None, task_model=None, output_dir=None):
    """Preserve acquired work and return safe errors without unapproved retries."""
    started = time.monotonic()
    result = {
        "run_id": run.run_id,
        "pipeline": "skill",
        "skill_id": run.adapter.skill_id,
        "adapter_hash": run.adapter.fingerprint,
        "execution_mode": "injected_models" if model is not None else "live",
        "execution_limits": {
            "max_model_calls": run.settings.max_model_calls,
            "max_requests": run.settings.max_requests,
            "deadline_seconds": run.settings.deadline_seconds,
            "max_output_tokens": run.settings.max_output_tokens,
            "structured_thinking": run.settings.structured_thinking,
        },
    }
    try:
        with tracing_context(enabled=run.settings.tracing):
            graph = create_research_agent(run, model=model, task_model=task_model)
            graph.invoke({"messages": [{"role": "user", "content": run.query}]})
        result.update(status=run.status)
    except (ConfigurationError, ResearchError) as error:
        result.update(
            status="failed",
            error={
                "code": getattr(error, "code", "missing_configuration"),
                "message": str(error),
            },
        )
    except APIConnectionError as error:
        from research_agent.model_errors import _model_connection_error

        result.update(status="failed", error=_model_connection_error(error))
    except Exception as error:
        result.update(
            status="failed",
            error={
                "code": "workflow_error",
                "message": f"Research failed ({type(error).__name__}); acquired material was preserved.",
            },
        )
    finally:
        run.close()
    run.status = result["status"]
    ledger = run.ledger
    result.update(
        latency_seconds=round(time.monotonic() - started, 3),
        calls=dict(run.budget.counts),
        token_usage=run.token_usage or None,
        cost=None,
        model_count_scope="application_model_calls",
        task_statuses={key: value.status for key, value in run.results.items()},
        evidence_count=len(ledger.evidence) if ledger else 0,
        finding_count=len(ledger.findings) if ledger else 0,
        metric_count=len(ledger.metrics) if ledger else 0,
        deterministic_burden={
            "ledger_write_tool_calls": sum(
                call["tool"]
                in {"record_source_evidence", "record_metric", "record_finding"}
                for call in run.model_field_calls
            ),
            "structured_research_responses": sum(
                call["tool"] == "ResearchOutput" for call in run.model_field_calls
            ),
            "implicitly_bound_source_fields": sum(
                event.get("implicit_source_fields", 0)
                for event in run.events
                if event["type"] == "runtime_registration"
            ),
            "metadata_repetition_fields": sorted(
                {
                    field
                    for call in run.model_field_calls
                    for field in call.get("metadata_fields", [])
                }
            ),
            "metadata_field_occurrences": sum(
                len(call.get("metadata_fields", [])) for call in run.model_field_calls
            ),
            "quote_exact_match_checks": 0,
            "natural_language_exact_match_checks": 0,
            "material_reference_selections": sum(
                item["material_reference_selections"] for item in run.model_field_calls
            ),
            "tool_field_calls": len(run.model_field_calls),
            "runtime_bound_fields": [
                "run_id",
                "task_id",
                "target",
                "source_id",
                "evidence_id",
                "finding_id",
                "agent_identity",
                "skill_paths",
                "timestamps",
                "status",
            ],
        },
        validation_scope="registered_provenance_and_deterministic_constraints; analysis_semantics_not_automatically_verified",
        target_route=run.target.kind if run.target else None,
    )
    if output_dir is not None:
        destination = export_run(run, result, output_dir)
        result["output_dir"] = str(destination)
    return result


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; reading an environment file requires an explicit option."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        adapter = load_adapter()
    except ConfigurationError as error:
        sys.stderr.write(str(error) + "\n")
        return 1
    parser = argparse.ArgumentParser(
        description="DeepEquity — Agentic Company Research System"
    )
    parser.add_argument("query", help="Stock or China OTC fund research question")
    parser.add_argument(
        "--ticker",
        help="Optional explicit ticker to prevent ambiguous company selection",
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output-dir", type=Path, default=Path("data/runs"))
    parser.add_argument("--target-type", choices=["stock", "fund"])
    parser.add_argument(
        "--fund-type",
        choices=adapter.routes["fund"].subtype_sections,
    )
    parser.add_argument("--share-class")
    parser.add_argument("--market")
    parser.add_argument("--scope", choices=["full", "focused"])
    parser.add_argument("--execution-path", choices=["skill", "mvp"], default="skill")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicitly load a local environment file without overwriting process variables",
    )
    arguments = parser.parse_args(argv)
    if arguments.execution_path == "mvp" and (
        not arguments.ticker
        or arguments.target_type not in {None, "stock"}
        or arguments.scope not in {None, "full"}
        or arguments.market not in {None, "US"}
        or arguments.fund_type
        or arguments.share_class
    ):
        parser.error(
            "MVP requires an explicit US stock ticker and full scope; funds and focused questions use the existing skill path."
        )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if arguments.env_file:
        from dotenv import load_dotenv

        if not arguments.env_file.is_file():
            parser.error("The requested environment file does not exist.")
        load_dotenv(arguments.env_file, override=False)
    try:
        settings = Settings.from_env()
        if arguments.execution_path == "mvp":
            from research_agent.mvp import run_mvp

            result = run_mvp(
                settings,
                query=arguments.query,
                ticker=arguments.ticker,
                as_of=arguments.as_of,
                output_dir=arguments.output_dir,
            )
        else:
            from research_agent.workflow import ResearchRun

            session = ResearchRun(
                settings,
                query=arguments.query,
                ticker=arguments.ticker,
                as_of=arguments.as_of,
                target_type=arguments.target_type,
                fund_type=arguments.fund_type,
                share_class=arguments.share_class,
                market=arguments.market,
                scope=arguments.scope,
            )
            result = run_research(session, output_dir=arguments.output_dir)
    except ConfigurationError as error:
        sys.stderr.write(str(error) + "\n")
        return 1
    except (ValueError, OSError) as error:
        sys.stderr.write(
            f"Local configuration or output failed ({type(error).__name__}).\n"
        )
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
