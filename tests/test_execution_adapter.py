"""Verify pinned Skill bindings without the retired research workflow."""

import json
import shutil
from importlib import resources

import pytest

from research_agent.config import ConfigurationError
from research_agent.skill_loader import FIXED_SKILL_ID, load_adapter, load_skill


@pytest.fixture
def adapter_copy(tmp_path, monkeypatch):
    root = resources.files("research_agent").joinpath("skills", FIXED_SKILL_ID)
    copied = tmp_path / "skills" / FIXED_SKILL_ID
    shutil.copytree(str(root), copied)
    monkeypatch.setattr(
        "research_agent.skill_loader.resources.files", lambda package: tmp_path
    )
    return copied


def edit_adapter(root, change):
    path = root / "runtime_policy.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(skill_id="competitor-analysis"),
        lambda data: data.update(
            research_policy="Prefer a particular valuation method"
        ),
        lambda data: data.update(snapshot_commit="wrong-version"),
        lambda data: data["routes"]["stock"]["capability_ids"].append(
            "ledger.record_evidence"
        ),
        lambda data: data["routes"]["stock"].update(method_file="../config.py"),
        lambda data: data["routes"]["stock"]["task_sections"].update(
            business=["Missing Heading"]
        ),
        lambda data: data["routes"]["stock"]["report_attachments"].update(
            metrics="Unregistered Heading"
        ),
        lambda data: data["routes"]["stock"]["coverage_bindings"].pop(3),
        lambda data: data["routes"]["stock"]["coverage_bindings"][0].update(
            method_sections=["8. Define Catalysts, Risks, Invalidation, And Execution"]
        ),
    ],
)
def test_invalid_execution_bindings_fail_before_model_work(adapter_copy, change):
    edit_adapter(adapter_copy, change)
    with pytest.raises(ConfigurationError):
        load_adapter()


def test_adapter_and_rules_are_frozen_for_a_run(adapter_copy):
    adapter = load_adapter()
    before = load_skill(adapter=adapter).task_catalog

    def remove_risk(data):
        route = data["routes"]["stock"]
        route["task_sections"].pop("risk")
        route["coverage_bindings"] = [
            item for item in route["coverage_bindings"] if item["task_type"] != "risk"
        ]

    edit_adapter(adapter_copy, remove_risk)
    assert load_skill(adapter=adapter).task_catalog == before
    assert "risk" not in load_skill().task_catalog
    path = adapter_copy / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["commit"] = "changed"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="changed"):
        load_skill(adapter=adapter)
