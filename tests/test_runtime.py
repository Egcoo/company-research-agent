"""Check runtime helpers without involving research or model providers."""

import json
import subprocess
import sys
from pathlib import Path

from research_agent import mvp
from research_agent.runtime import _write_json
from research_agent.runtime.reporting import (
    _source_list,
    check_report,
    normalize_markdown,
)
from research_agent.runtime.supervisor import supervise_process

ROOT = Path(__file__).resolve().parents[1]


def test_mvp_keeps_existing_helper_imports_as_runtime_aliases():
    assert mvp.check_report is check_report
    assert mvp.normalize_markdown is normalize_markdown
    assert mvp.supervise_process is supervise_process


def test_runtime_modules_import_without_model_or_execution_dependencies():
    script = """
import json, socket, sys
def forbidden(*args, **kwargs):
    raise AssertionError("Runtime imports must not access the network.")
socket.create_connection = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
import research_agent.runtime.reporting
import research_agent.runtime.supervisor
assert "research_agent.mvp" not in sys.modules
assert not any(name == prefix or name.startswith(prefix + ".")
               for name in sys.modules
               for prefix in ("anthropic", "langchain", "langsmith"))
print(json.dumps({"status": "ok"}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {"status": "ok"}


def test_atomic_json_preserves_unicode_format_and_replaces_previous_result(tmp_path):
    path = tmp_path / "result.json"
    path.write_text("Previous incomplete result", encoding="utf-8")
    result = {"status": "partial", "gap": "缺少股价资料"}

    _write_json(path, result)

    assert path.read_text(encoding="utf-8") == (
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    assert not path.with_suffix(".json.tmp").exists()


def test_source_list_retains_order_deduplication_and_original_format():
    sources = [
        {
            "url": "https://example.test/annual",
            "title": "年报[2025]\n附注",
            "published_at": "2026-01-28",
            "retrieved_at": "2026-10-09T12:00:00+00:00",
        },
        {
            "url": "https://example.test/annual",
            "title": "Duplicate reading",
            "published_at": None,
            "retrieved_at": "2026-10-09T12:01:00+00:00",
        },
        {
            "url": "https://example.test/quarter",
            "title": "季报",
            "published_at": None,
            "retrieved_at": "2026-10-09T12:02:00+00:00",
        },
    ]

    output = _source_list(sources)

    assert output == (
        "### 本次实际读取的来源\n\n"
        "- [年报\\[2025\\] 附注](<https://example.test/annual>)；发布日期：2026-01-28；读取日期：2026-10-09。\n"
        "- [季报](<https://example.test/quarter>)；发布日期：未确认；读取日期：2026-10-09。\n"
    )


def test_source_list_normalizes_utc_and_local_read_times_to_the_same_china_date():
    sources = [
        {
            "url": "https://example.test/a",
            "title": "A",
            "published_at": None,
            "retrieved_at": "2026-10-15T16:20:00+00:00",
        },
        {
            "url": "https://example.test/b",
            "title": "B",
            "published_at": None,
            "retrieved_at": "2026-10-16T00:20:00+08:00",
        },
    ]
    text = _source_list(sources)
    assert text.count("读取日期：2026-10-16") == 2
    sources[0]["retrieved_at"] = "invalid"
    assert "读取日期：未确认" in _source_list(sources)
