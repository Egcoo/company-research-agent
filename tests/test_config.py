"""Verify credential boundaries and shared research limits without external calls."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from research_agent.config import (
    ConfigurationError,
    ResearchBudget,
    ResearchError,
    Settings,
)


def test_output_token_cap_has_a_bounded_environment_override():
    assert Settings.from_env({}).max_output_tokens == 8192
    assert (
        Settings.from_env({"DEEP_EQUITY_MAX_OUTPUT_TOKENS": "4096"}).max_output_tokens
        == 4096
    )
    for invalid in ("0", "65536", "not-a-number"):
        with pytest.raises(ValidationError):
            Settings.from_env({"DEEP_EQUITY_MAX_OUTPUT_TOKENS": invalid})


def test_structured_thinking_is_opt_in_and_rejects_unsupported_values():
    assert Settings.from_env({}).structured_thinking == "provider_default"
    assert (
        Settings.from_env(
            {"DEEP_EQUITY_STRUCTURED_THINKING": "disabled"}
        ).structured_thinking
        == "disabled"
    )
    with pytest.raises(ValidationError):
        Settings.from_env({"DEEP_EQUITY_STRUCTURED_THINKING": "unknown"})


def test_model_timeout_is_independent_of_web_timeout_and_configurable():
    settings = Settings.from_env({})
    assert settings.timeout_seconds == 20
    assert settings.model_timeout_seconds == 120
    custom = Settings.from_env({"DEEP_EQUITY_MODEL_TIMEOUT_SECONDS": "180"})
    assert custom.model_timeout_seconds == 180 and custom.timeout_seconds == 20
    for invalid in ("0", "-1", "601", "not-a-number"):
        with pytest.raises(ValidationError):
            Settings.from_env({"DEEP_EQUITY_MODEL_TIMEOUT_SECONDS": invalid})


def test_anthropic_request_payload_uses_the_per_call_output_cap(tmp_path):
    from datetime import date
    from types import SimpleNamespace

    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage

    from research_agent.tools.mvp import MvpTools

    settings = Settings()
    tools = MvpTools(
        settings,
        ResearchBudget(settings),
        SimpleNamespace(),
        SimpleNamespace(),
        tmp_path,
        date(2026, 9, 30),
        "SYN",
    )
    model = ChatAnthropic(
        model="synthetic-offline-model", api_key="test-only", max_tokens=8192
    )
    bound = model.bind_tools(
        tools.tools(), max_tokens=16384, thinking={"type": "disabled"}
    )
    payload = model._get_request_payload(
        [HumanMessage(content="Offline payload check")], **bound.kwargs
    )
    assert model.max_tokens == 8192
    assert payload["max_tokens"] == 16384
    assert payload["thinking"] == {"type": "disabled"}
    assert {tool["name"] for tool in payload["tools"]} == {
        "search_sources",
        "discover_sec_sources",
        "read_source",
        "calculate",
        "save_notes",
    }


@pytest.mark.parametrize(
    ("provider", "variable"),
    [("anthropic", "ANTHROPIC_API_KEY"), ("tavily", "TAVILY_API_KEY")],
)
@pytest.mark.parametrize(
    "value", ["", " ", "your_api_key_here", "lsv2_pt_your_api_key_here"]
)
def test_missing_or_placeholder_credentials_name_required_variable(
    provider, variable, value
):
    settings = Settings.from_env({variable: value})

    with pytest.raises(ConfigurationError, match=variable):
        settings.require(provider)


def test_empty_environment_can_load_without_credentials():
    settings = Settings.from_env({})

    assert settings.tracing is False
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        settings.require("anthropic")
    with pytest.raises(ConfigurationError, match="TAVILY_API_KEY"):
        settings.require("tavily")


def test_configured_credentials_are_usable_but_excluded_from_diagnostics():
    anthropic_key = "unit-test-anthropic-key"
    tavily_key = "unit-test-tavily-key"
    settings = Settings.from_env(
        {"ANTHROPIC_API_KEY": f" {anthropic_key} ", "TAVILY_API_KEY": tavily_key}
    )

    assert settings.require("anthropic") == anthropic_key
    assert settings.require("tavily") == tavily_key
    diagnostics = (
        repr(settings),
        str(settings.model_dump()),
        settings.model_dump_json(),
    )
    assert all(
        anthropic_key not in value and tavily_key not in value for value in diagnostics
    )


@pytest.mark.parametrize("user_agent", ["", "DeepEquity", "analyst@example.test"])
def test_sec_requires_both_application_name_and_contact(user_agent):
    settings = Settings.from_env({"SEC_USER_AGENT": user_agent})

    with pytest.raises(ConfigurationError, match="SEC_USER_AGENT"):
        settings.require_sec_user_agent()


def test_sec_accepts_declared_application_and_contact():
    user_agent = "DeepEquity analyst@example.test"

    assert (
        Settings.from_env({"SEC_USER_AGENT": user_agent}).require_sec_user_agent()
        == user_agent
    )


@pytest.mark.parametrize("kind", ["request", "tool", "model"])
def test_parallel_workers_cannot_exceed_shared_call_limit(kind):
    budget = ResearchBudget(
        Settings(max_requests=5, max_tool_calls=5, max_model_calls=5)
    )

    def attempt_call(_attempt):
        try:
            budget.consume(kind)
        except ResearchError as error:
            return error.code
        return "allowed"

    with ThreadPoolExecutor(max_workers=8) as workers:
        outcomes = list(workers.map(attempt_call, range(24)))

    assert outcomes.count("allowed") == 5
    assert outcomes.count("budget_exceeded") == 19
    assert budget.remaining()[kind] == 0
    assert budget.counts[kind] == outcomes.count("allowed")


def test_remaining_is_a_nonconsuming_snapshot_with_nonnegative_time(monkeypatch):
    current_time = [1000.0]
    monkeypatch.setattr("research_agent.config.time.monotonic", lambda: current_time[0])
    budget = ResearchBudget(
        Settings(
            max_requests=7, max_tool_calls=8, max_model_calls=9, deadline_seconds=10
        )
    )
    budget.consume("request")
    budget.consume("model")
    current_time[0] = 1003.0

    snapshot = budget.remaining()

    assert snapshot == {"request": 6, "tool": 8, "model": 8, "seconds": 7.0}
    assert budget.remaining() == snapshot
    assert budget.counts == {"request": 1, "tool": 0, "model": 1}
    budget.consume("tool")
    assert snapshot["tool"] == 8
    assert budget.remaining()["tool"] == 7
    current_time[0] = 1011.0
    assert budget.remaining()["seconds"] == 0.0
    assert budget.counts == {"request": 1, "tool": 1, "model": 1}


def test_deadline_rejects_further_work_at_exact_boundary(monkeypatch):
    current_time = [1000.0]
    monkeypatch.setattr("research_agent.config.time.monotonic", lambda: current_time[0])
    budget = ResearchBudget(Settings(deadline_seconds=10))

    current_time[0] = 1009.9
    budget.consume("request")
    current_time[0] = 1010.0

    with pytest.raises(ResearchError, match="deadline") as failure:
        budget.check()
    assert failure.value.code == "budget_exceeded"
    with pytest.raises(ResearchError, match="deadline"):
        budget.consume("tool")


def test_parallel_workers_reserve_model_steps_without_raising_total_limit():
    budget = ResearchBudget(Settings(max_model_calls=10))

    def worker(_attempt):
        try:
            budget.consume("model", reserve=2)
            return "allowed"
        except ResearchError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as workers:
        outcomes = list(workers.map(worker, range(24)))
    assert outcomes.count("allowed") == 8
    assert outcomes.count("synthesis_reserved") == 16
    assert budget.remaining()["model"] == 2
    budget.consume("model")
    budget.consume("model")
    with pytest.raises(ResearchError) as failure:
        budget.consume("model")
    assert failure.value.code == "budget_exceeded"
    assert budget.counts["model"] == 10
