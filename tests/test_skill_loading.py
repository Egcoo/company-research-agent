"""Verify target-specific rule loading and packaged snapshot integrity."""

import hashlib
import json
from importlib import resources

import pytest

from research_agent.config import ConfigurationError
from research_agent.skill_loader import load_skill


def test_stock_uses_only_stock_files_and_its_nine_section_contract():
    bundle = load_skill("stock")
    assert bundle.files == [
        "SKILL.md",
        "references/source-verification.md",
        "references/stock-research.md",
        "references/memo-template.md",
    ]
    assert len(bundle.report_headings) == 9
    assert bundle.report_headings[1] == "公司到底是怎么赚钱的"
    assert "## Fund Output Contract" not in bundle.methodology
    assert "基金到底在投什么" not in bundle.methodology


def test_active_fund_excludes_stock_and_passive_routes():
    bundle = load_skill("fund", "active")
    assert bundle.files == [
        "SKILL.md",
        "references/source-verification.md",
        "references/china-otc-fund-research.md",
        "references/fund-memo-template.md",
    ]
    assert len(bundle.report_headings) == 9
    assert bundle.report_headings[1] == "基金到底在投什么"
    assert "## Stock Output Contract" not in bundle.methodology
    assert "公司到底是怎么赚钱的" not in bundle.methodology
    assert "### Active Funds" in bundle.methodology
    assert "### Enhanced Index Funds" not in bundle.methodology


@pytest.mark.parametrize("fund_type", ["passive_index", "etf_feeder"])
def test_passive_and_feeder_load_additional_index_rules(fund_type):
    bundle = load_skill("fund", fund_type)
    assert "references/passive-index-fund-research.md" in bundle.files
    assert "## Vehicle Quality" in bundle.methodology
    assert "### Active Funds" not in bundle.methodology
    assert "### Enhanced Index Funds" not in bundle.methodology
    assert bundle.report_headings == load_skill("fund", "active").report_headings


@pytest.mark.parametrize(
    "task_type,expected,excluded",
    [
        (
            "business",
            "## 2. Understand How The Company Makes Money",
            "## 6. Value The Stock",
        ),
        ("industry", "## 3. Analyze The Industry", "## 5. Test Financial Quality"),
        ("financial", "## 5. Test Financial Quality", "## 3. Analyze The Industry"),
        ("risk", "## 8. Define Catalysts", "## 6. Value The Stock"),
        ("valuation", "## 6. Value The Stock", "## 2. Understand How The Company"),
    ],
)
def test_stock_tasks_receive_only_their_methods_and_no_report_template(
    task_type, expected, excluded
):
    bundle = load_skill("stock", task_type=task_type)
    assert expected in bundle.methodology
    assert excluded not in bundle.methodology
    assert "references/memo-template.md" not in bundle.files
    assert "## Stock Output Contract" not in bundle.methodology
    assert "Final Quality Gates" not in bundle.methodology
    assert bundle.report_headings == []


@pytest.mark.parametrize(
    "task_type,expected,excluded",
    [
        ("fund_exposure", "## Index Exposure", "## Vehicle Quality"),
        ("fund_quality", "## Vehicle Quality", "## Timing And Conditions"),
        ("fund_risk", "## Timing And Conditions", "## Share Classes"),
    ],
)
def test_fund_tasks_receive_only_selected_passive_methods(
    task_type, expected, excluded
):
    bundle = load_skill("fund", "passive_index", task_type=task_type)
    assert expected in bundle.methodology
    assert excluded not in bundle.methodology
    assert "references/fund-memo-template.md" not in bundle.files
    assert "## Fund Output Contract" not in bundle.methodology
    assert bundle.report_headings == []


@pytest.mark.parametrize(
    "kind,fund_type", [("stock", None), ("fund", "active"), ("fund", "etf_feeder")]
)
def test_focused_report_does_not_impose_full_investment_memo(kind, fund_type):
    bundle = load_skill(kind, fund_type, focused=True)
    assert bundle.report_headings == []
    assert not any("memo-template" in name for name in bundle.files)
    assert "Output Contract" not in bundle.methodology
    assert "## Nine-Part Report Logic" not in bundle.methodology
    assert "## Completion Check" not in bundle.methodology
    assert "## 6. Value The Stock" not in bundle.methodology


@pytest.mark.parametrize(
    "options",
    [
        {"kind": "../SKILL.md"},
        {"kind": "fund", "fund_type": "../SKILL.md"},
        {"kind": "stock", "fund_type": "active"},
        {"kind": "stock", "task_type": "fund_exposure"},
        {"kind": "fund", "task_type": "financial"},
        {"task_type": "../../config.py"},
    ],
)
def test_invalid_routes_do_not_open_arbitrary_files(options):
    with pytest.raises(ConfigurationError):
        load_skill(**options)


def test_snapshot_manifest_and_bundle_hashes_match_packaged_bytes():
    root = resources.files("research_agent").joinpath("skills", "investment-master")
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_url"] == "https://github.com/Egcoo/investment-master"
    assert manifest["commit"] == "e6a7bf032c05036b2d245157b9e787c3532c0897"
    for name, expected in manifest["hashes"].items():
        data = root.joinpath(*name.split("/")).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
    bundle = load_skill()
    assert set(bundle.hashes) == set(bundle.files)
    assert manifest["commit"] in bundle.source_url
    assert bundle.hashes == {name: manifest["hashes"][name] for name in bundle.files}


def test_separate_loads_do_not_share_mutable_manifests():
    first = load_skill()
    first.files.clear()
    first.hashes.clear()
    assert len(load_skill().files) == 4
    assert len(load_skill().hashes) == 4


def test_tampered_packaged_rule_is_rejected(tmp_path, monkeypatch):
    root = resources.files("research_agent").joinpath("skills", "investment-master")
    altered = tmp_path / "skills" / "investment-master"
    altered.mkdir(parents=True)
    import shutil

    shutil.copytree(str(root), altered, dirs_exist_ok=True)
    altered.joinpath("manifest.json").write_bytes(
        root.joinpath("manifest.json").read_bytes()
    )
    altered.joinpath("SKILL.md").write_bytes(
        root.joinpath("SKILL.md").read_bytes() + b"\nchanged"
    )
    monkeypatch.setattr(
        "research_agent.skill_loader.resources.files", lambda package: tmp_path
    )
    with pytest.raises(ConfigurationError, match="SHA256"):
        load_skill()
