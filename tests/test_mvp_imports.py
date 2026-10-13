"""Verify the installed MVP entry and dependency closure in a fresh interpreter."""

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIRED = {
    "research_agent.workflow",
    "research_agent.schemas",
    "research_agent.validation",
    "research_agent.report",
    "research_agent.evidence",
    "research_agent.replay_report",
    "research_agent.agents",
    "research_agent.tools.market_data",
}


def test_every_remaining_package_module_imports_without_legacy_or_network():
    script = """
import importlib, json, pkgutil, socket, sys
import research_agent
def reject_connection(*args, **kwargs):
    raise AssertionError("Import must not access the network.")
socket.create_connection = reject_connection
socket.socket.connect = reject_connection
socket.socket.connect_ex = reject_connection
for item in pkgutil.walk_packages(research_agent.__path__, "research_agent."):
    importlib.import_module(item.name)
print(json.dumps(sorted(name for name in sys.modules if name.startswith("research_agent"))))
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
    modules = set(json.loads(result.stdout))
    assert {
        "research_agent.runner",
        "research_agent.mvp",
        "research_agent.agent.research",
        "research_agent.agent.synthesis",
        "research_agent.tools.mvp",
    } <= modules
    assert not RETIRED & modules


def test_retained_python_has_no_imports_of_retired_modules():
    files = [
        *ROOT.joinpath("research_agent").rglob("*.py"),
        *ROOT.joinpath("tests").glob("*.py"),
    ]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            assert not any(
                name == retired or name.startswith(retired + ".")
                for name in names
                for retired in RETIRED
            ), path


def test_project_metadata_and_active_files_expose_only_mvp():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"] == {"deepequity": "research_agent.runner:main"}
    assert (
        not {"studio", "notebook"}
        & project["project"].get("optional-dependencies", {}).keys()
    )
    for name in (
        "agent.py",
        "langgraph.json",
        "research_agent.ipynb",
        "examples/v1_saved_run_report.py",
        "examples/v1_saved_material_comparison.py",
    ):
        assert not (ROOT / name).exists()
    for name in RETIRED:
        path = ROOT.joinpath(*name.split("."))
        assert (
            not path.with_suffix(".py").exists() and not (path / "__init__.py").exists()
        )


def test_public_module_cli_help_starts_without_credentials():
    result = subprocess.run(
        [sys.executable, "-m", "research_agent.runner", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    assert "--execution-path {mvp}" in result.stdout
    assert "--ticker" in result.stdout
