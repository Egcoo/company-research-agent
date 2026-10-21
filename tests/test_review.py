"""Exercise the saved-run reviewer without real models, tools or network."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from research_agent import review
from research_agent.config import ResearchError, Settings


class ReviewModel:
    def __init__(self, response=None, error=None):
        self.response = (
            response
            if response is not None
            else AIMessage(
                content="## 审阅结论\n需要修改。\n\n## 主要问题\n[依据不足] 第八章阈值缺少 S1 原文支持，建议删除或补充推导。\n\n## 审阅范围与保留意见\n仅依据提供材料；不是人工质量验收。",
                usage_metadata={
                    "input_tokens": 20,
                    "output_tokens": 30,
                    "total_tokens": 50,
                },
            )
        )
        self.error = error
        self.calls = []

    def invoke(self, messages, **options):
        assert not self.calls, "Only one reviewer call is allowed."
        self.calls.append((messages, options))
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def saved_run(tmp_path):
    def create(kind="fund", filename="report.md"):
        directory = tmp_path / kind
        directory.mkdir()
        inputs = {
            "query": "研究所选份额，未知资料保留缺口",
            "target_type": kind,
            "ticker": "021528" if kind == "fund" else "NVDA",
            "as_of": "2026-10-16",
            "settings": {
                "api_key": "synthetic-private-setting-must-not-reach-reviewer"
            },
        }
        if kind == "fund":
            inputs.update(fund_type="mixed", share_class="C")
        (directory / "inputs.json").write_text(
            json.dumps(inputs, ensure_ascii=False), encoding="utf-8"
        )
        (directory / filename).write_text(
            "# 原始研究报告\n第八章：建议依据尚不充分。", encoding="utf-8"
        )
        (directory / "result.json").write_text(
            json.dumps({"status": "generated", "quality_accepted": None}),
            encoding="utf-8",
        )
        source = {
            "url": "https://example.test/report",
            "title": "原始披露",
            "text": "C类可比历史不足三年。",
            "published_at": "2026-06-30",
            "truncated": True,
        }
        (directory / "sources.jsonl").write_text(
            "\n".join([json.dumps(source, ensure_ascii=False)] * 2), encoding="utf-8"
        )
        (directory / "notes.md").write_text(
            "模型工作笔记，不是独立证据。", encoding="utf-8"
        )
        (directory / "worker.log").write_text(
            "worker-private-details", encoding="utf-8"
        )
        (directory / "acceptance_review.md").write_text(
            "previous-human-review-must-not-be-used", encoding="utf-8"
        )
        if kind == "fund":
            (directory / "fund_data.json").write_text(
                json.dumps(
                    {"identity_verified": True, "gaps": ["duration unavailable"]}
                ),
                encoding="utf-8",
            )
            (directory / "nav-021528.json").write_text(
                json.dumps({"code": "021528", "raw_rows": [{"y": 1}, {"y": 1.1}]}),
                encoding="utf-8",
            )
            (directory / "nav-999999.json").write_text(
                json.dumps({"text": "comparison-raw-series-not-in-packet"}),
                encoding="utf-8",
            )
        return directory

    return create


def test_fund_review_uses_one_plain_model_call_and_preserves_originals(saved_run):
    directory = saved_run()
    before = {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }
    model = ReviewModel()
    result = review.run_review(Settings(), directory, model=model)

    assert result["status"] == "completed"
    assert result["model_calls"] == 1 and result["tool_calls"] == 0
    assert result["quality_accepted"] is None
    assert result["token_usage"]["total_tokens"] == 50
    output = Path(result["output_dir"])
    assert output.parent == directory / "reviews"
    assert json.loads((output / "result.json").read_text(encoding="utf-8")) == result
    assert "人工质量验收" in (output / "review.md").read_text(encoding="utf-8")
    for name, body in before.items():
        assert (directory / name).read_bytes() == body

    messages, options = model.calls[0]
    packet = json.loads(messages[1].content)
    assert "基金报告判卷标准" in messages[0].content
    assert packet["inputs"]["share_class"] == "C"
    assert packet["retained_nav"]["code"] == "021528"
    assert packet["coverage"]["source_windows_saved"] == 2
    assert len(packet["sources"]) == 1 and packet["sources"][0]["source_id"] == "S1"
    assert packet["coverage"]["sources_already_truncated"] == 1
    assert "synthetic-private-setting" not in messages[1].content
    assert "worker-private-details" not in messages[1].content
    assert "previous-human-review" not in messages[1].content
    assert "comparison-raw-series" not in messages[1].content
    assert "settings" not in packet["inputs"]
    assert options == {
        "thinking": {"type": "disabled"},
        "timeout": 120,
        "max_tokens": 2048,
    }


def test_stock_legacy_filename_uses_stock_rules_and_repeated_reviews_do_not_overwrite(
    saved_run,
):
    directory = saved_run("stock", "2026-10-13-GOOG.md")
    first_model = ReviewModel()
    first = review.run_review(Settings(), directory, model=first_model)
    second = review.run_review(Settings(), directory, model=ReviewModel())
    packet = json.loads(first_model.calls[0][0][1].content)
    assert packet["inputs"]["ticker"] == "NVDA"
    assert "股票报告判卷标准" in first_model.calls[0][0][0].content
    assert packet["fund_data"] is None and packet["retained_nav"] is None
    assert first["output_dir"] != second["output_dir"]
    assert Path(first["review_file"]).exists() and Path(second["review_file"]).exists()


def test_missing_optional_materials_are_visible_to_model_and_user(saved_run):
    directory = saved_run()
    for name in (
        "sources.jsonl",
        "notes.md",
        "fund_data.json",
        "nav-021528.json",
        "result.json",
    ):
        (directory / name).unlink()
    model = ReviewModel()
    result = review.run_review(Settings(), directory, model=model)
    assert result["status"] == "completed"
    assert len(result["coverage"]["warnings"]) == 3
    assert "没有可用的原文窗口" in Path(result["review_file"]).read_text(
        encoding="utf-8"
    )
    assert not json.loads(model.calls[0][0][1].content)["sources"]


@pytest.mark.parametrize(
    "problem",
    [
        "missing_report",
        "empty_report",
        "bad_inputs",
        "bad_sources",
        "unknown_type",
        "too_large",
        "ambiguous",
    ],
)
def test_invalid_inputs_stop_before_spending_model_calls_or_writing_reviews(
    saved_run, problem
):
    directory = saved_run()
    kwargs = {}
    if problem == "missing_report":
        (directory / "report.md").unlink()
    elif problem == "empty_report":
        (directory / "report.md").write_text(" ", encoding="utf-8")
    elif problem == "bad_inputs":
        (directory / "inputs.json").write_text("[", encoding="utf-8")
    elif problem == "bad_sources":
        (directory / "sources.jsonl").write_text("not-json", encoding="utf-8")
    elif problem == "unknown_type":
        (directory / "inputs.json").write_text(
            '{"target_type":"other"}', encoding="utf-8"
        )
    elif problem == "too_large":
        kwargs["max_input_chars"] = 1000
    elif problem == "ambiguous":
        (directory / "2026-10-20-021528.md").write_text(
            "another final report", encoding="utf-8"
        )
    model = ReviewModel()
    with pytest.raises(ResearchError):
        review.run_review(Settings(), directory, model=model, **kwargs)
    assert not model.calls and not (directory / "reviews").exists()


@pytest.mark.parametrize(
    "filename",
    [
        "../report.md",
        "notes.md",
        "report.draft.md",
        "report.repair.md",
        "acceptance_review.md",
        ".env",
    ],
)
def test_explicit_report_cannot_select_notes_prior_reviews_or_paths_outside_run(
    saved_run, filename
):
    directory = saved_run()
    with pytest.raises(ResearchError):
        review.prepare_review(directory, report_filename=filename)


def test_explicit_report_resolves_ambiguous_final_reports(saved_run):
    directory = saved_run()
    (directory / "2026-10-20-021528.md").write_text("another final", encoding="utf-8")
    prepared = review.prepare_review(directory, report_filename="report.md")
    assert prepared["report_filename"] == "report.md"


def test_linked_material_cannot_read_outside_the_selected_run(saved_run, tmp_path):
    directory = saved_run()
    outside = tmp_path / "outside.json"
    outside.write_text('{"private":"outside-material"}', encoding="utf-8")
    linked = directory / "fund_data.json"
    linked.unlink()
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this Windows host.")
    with pytest.raises(ResearchError, match="escapes directory"):
        review.prepare_review(directory)


def test_cli_input_error_is_clear_and_does_not_write_a_review(tmp_path, capfd):
    assert review.main([str(tmp_path / "missing-run")]) == 1
    assert "does not exist" in capfd.readouterr().err
    assert not (tmp_path / "reviews").exists()


@pytest.mark.parametrize(
    "response,error_code",
    [
        (AIMessage(content=""), "review_empty"),
        (
            AIMessage(
                content="",
                tool_calls=[{"name": "read_source", "args": {}, "id": "unexpected"}],
            ),
            "review_unexpected_tool_call",
        ),
    ],
)
def test_empty_or_tool_request_response_never_retries_or_executes_tools(
    saved_run, response, error_code
):
    result = review.run_review(
        Settings(), saved_run(), model=ReviewModel(response=response)
    )
    assert result["status"] == "failed" and result["model_calls"] == 1
    assert result["errors"][0]["code"] == error_code
    assert result["tool_calls"] == 0
    assert not (Path(result["output_dir"]) / "review.md").exists()


def test_model_failure_is_recorded_without_exporting_provider_details(saved_run):
    result = review.run_review(
        Settings(),
        saved_run(),
        model=ReviewModel(error=RuntimeError("private-provider-request-and-key")),
    )
    assert result["status"] == "failed" and result["model_calls"] == 1
    stored = (Path(result["output_dir"]) / "result.json").read_text(encoding="utf-8")
    assert "private-provider" not in stored and "RuntimeError" in stored


def test_missing_credentials_preserves_failed_review_without_model_call(saved_run):
    result = review.run_review(Settings(), saved_run())
    assert result["status"] == "failed" and result["model_calls"] == 0
    assert result["errors"][0]["code"] == "missing_configuration"
    assert Path(result["output_dir"], "result.json").exists()


def test_truncated_response_is_only_saved_as_a_draft(saved_run):
    response = AIMessage(
        content="## 审阅结论\n未完成", response_metadata={"stop_reason": "max_tokens"}
    )
    result = review.run_review(
        Settings(), saved_run(), model=ReviewModel(response=response)
    )
    assert result["status"] == "partial" and result["model_calls"] == 1
    assert Path(result["review_file"]).name == "review.draft.md"
    assert not Path(result["output_dir"], "review.md").exists()


def test_model_creation_uses_existing_adapter_with_no_retries(saved_run, monkeypatch):
    created = []
    model = ReviewModel()
    monkeypatch.setattr(
        review,
        "init_chat_model",
        lambda *args, **kwargs: created.append((args, kwargs)) or model,
    )
    result = review.run_review(
        Settings(anthropic_api_key="synthetic-test-only"), saved_run()
    )
    assert result["status"] == "completed" and len(created) == 1
    assert created[0][1]["max_retries"] == 0
    assert created[0][1]["max_tokens"] == 2048
    assert "synthetic-test-only" not in json.dumps(result)


def test_output_write_failure_cannot_be_reported_as_completed(saved_run, monkeypatch):
    directory = saved_run()
    original = Path.write_text

    def fail_review(path, *args, **kwargs):
        if path.name == "review.md":
            raise OSError("private-storage-error")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_review)
    result = review.run_review(Settings(), directory, model=ReviewModel())
    assert result["status"] == "failed" and "review_file" not in result
    assert Path(result["output_dir"], "result.json").exists()


def test_cli_uses_only_explicit_env_file_and_forwards_report_options(
    saved_run, capfd, monkeypatch, tmp_path
):
    import dotenv

    directory = saved_run()
    reads, calls = [], []
    monkeypatch.setattr(
        dotenv, "load_dotenv", lambda *args, **kwargs: reads.append((args, kwargs))
    )
    monkeypatch.setattr(
        review,
        "run_review",
        lambda settings, run_dir, **kwargs: (
            calls.append((settings, run_dir, kwargs)) or {"status": "completed"}
        ),
    )
    assert review.main([str(directory)]) == 0
    assert not reads
    assert json.loads(capfd.readouterr().out)["status"] == "completed"
    env = tmp_path / "synthetic.env"
    env.write_text("DEEP_EQUITY_MODEL=anthropic:synthetic", encoding="utf-8")
    assert (
        review.main(
            [
                str(directory),
                "--env-file",
                str(env),
                "--model",
                "anthropic:review-only",
                "--report",
                "report.md",
                "--max-input-chars",
                "120000",
            ]
        )
        == 0
    )
    assert reads == [((env,), {"override": False})]
    assert calls[-1][0].model == "anthropic:review-only"
    assert calls[-1][2] == {"report_filename": "report.md", "max_input_chars": 120000}
    assert json.loads(capfd.readouterr().out)["status"] == "completed"


def test_module_help_does_not_need_credentials_or_start_research():
    completed = subprocess.run(
        [sys.executable, "-m", "research_agent.review", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0
    assert "run_dir" in completed.stdout and "--report" in completed.stdout
