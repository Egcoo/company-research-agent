"""Check the default MVP CLI without paid calls or implicit credential reads."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from research_agent import runner
from research_agent.config import Settings


@pytest.mark.parametrize("explicit", [False, True])
def test_mvp_is_default_and_existing_explicit_command_still_works(
    tmp_path, capfd, monkeypatch, explicit
):
    calls = []

    def fake_run(settings, **arguments):
        calls.append(arguments)
        return {"status": "generated", "execution_path": "mvp"}

    monkeypatch.setattr("research_agent.mvp.run_mvp", fake_run)
    arguments = [
        "Full Apple research",
        "--ticker",
        "AAPL",
        "--target-type",
        "stock",
        "--market",
        "US",
        "--scope",
        "full",
        "--as-of",
        "2026-09-30",
        "--output-dir",
        str(tmp_path),
    ]
    if explicit:
        arguments += ["--execution-path", "mvp"]
    assert runner.main(arguments) == 0
    assert json.loads(capfd.readouterr().out)["execution_path"] == "mvp"
    assert calls == [
        {
            "query": "Full Apple research",
            "ticker": "AAPL",
            "as_of": date(2026, 9, 30),
            "output_dir": tmp_path,
        }
    ]


@pytest.mark.parametrize(
    "status,exit_code", [("generated", 0), ("partial", 0), ("failed", 1)]
)
def test_cli_preserves_mvp_result_and_exit_status(
    capfd, monkeypatch, status, exit_code
):
    result = {
        "status": status,
        "execution_path": "mvp",
        "report_written": status == "generated",
    }
    monkeypatch.setattr("research_agent.mvp.run_mvp", lambda *args, **kwargs: result)
    assert runner.main(["Apple research", "--ticker", "AAPL"]) == exit_code
    assert json.loads(capfd.readouterr().out) == result


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--ticker", "AAPL", "--execution-path", "skill"],
        ["--ticker", "AAPL", "--target-type", "fund"],
        ["--ticker", "AAPL", "--scope", "focused"],
        ["--ticker", "AAPL", "--market", "CN"],
        ["--ticker", "AAPL", "--fund-type", "active"],
        ["--ticker", "AAPL", "--share-class", "C"],
    ],
)
def test_unsupported_input_is_rejected_before_research(options, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid input must not start research.")

    monkeypatch.setattr("research_agent.mvp.run_mvp", forbidden)
    with pytest.raises(SystemExit) as error:
        runner.main(["Research question", *options])
    assert error.value.code == 2


def test_cli_rejects_future_cutoff_before_network(tmp_path, capfd):
    future = date.today() + timedelta(days=1)
    assert (
        runner.main(
            [
                "Apple research",
                "--ticker",
                "AAPL",
                "--as-of",
                future.isoformat(),
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    assert "future" in capfd.readouterr().err
    assert not list(tmp_path.iterdir())


def test_cli_missing_credentials_preserves_failed_mvp_run(tmp_path, capfd, monkeypatch):
    import dotenv

    def reject_env_read(*args, **kwargs):
        raise AssertionError("No environment file was explicitly requested.")

    monkeypatch.setattr(dotenv, "load_dotenv", reject_env_read)
    assert (
        runner.main(
            [
                "Apple research",
                "--ticker",
                "AAPL",
                "--as-of",
                "2026-09-30",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    output = json.loads(capfd.readouterr().out)
    assert output["status"] == "failed" and output["execution_path"] == "mvp"
    assert output["errors"][0]["code"] == "missing_configuration"
    assert "ANTHROPIC_API_KEY" in output["errors"][0]["message"]
    assert output["calls"] == {"request": 0, "tool": 0, "model": 0}
    directory = Path(output["output_dir"])
    assert not (directory / "2026-10-13-GOOG.md").exists()
    inputs = json.loads((directory / "inputs.json").read_text(encoding="utf-8"))
    assert inputs["ticker"] == "AAPL" and inputs["scope"] == "full"
    assert "anthropic_api_key" not in inputs["settings"]


def test_cli_loads_only_explicit_environment_file_without_overwriting(
    tmp_path, monkeypatch
):
    import dotenv

    path = tmp_path / "synthetic.env"
    path.write_text("DEEP_EQUITY_MODEL=anthropic:synthetic\n", encoding="utf-8")
    reads = []
    monkeypatch.setattr(
        dotenv, "load_dotenv", lambda *args, **kwargs: reads.append((args, kwargs))
    )
    monkeypatch.setattr(
        "research_agent.mvp.run_mvp",
        lambda *args, **kwargs: {"status": "generated", "execution_path": "mvp"},
    )
    assert (
        runner.main(["Apple research", "--ticker", "AAPL", "--env-file", str(path)])
        == 0
    )
    assert reads == [((path,), {"override": False})]


def test_environment_budget_bounds_are_validated():
    settings = Settings.from_env(
        {"DEEP_EQUITY_MAX_REQUESTS": "7", "DEEP_EQUITY_DEADLINE_SECONDS": "60"}
    )
    assert settings.max_requests == 7 and settings.deadline_seconds == 60
    with pytest.raises(ValueError):
        Settings.from_env({"DEEP_EQUITY_MAX_REQUESTS": "-1"})
