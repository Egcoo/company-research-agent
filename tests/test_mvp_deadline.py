"""Exercise the MVP deadline against real, entirely local process trees."""

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path


def _script(directory: Path, body: str) -> list[str]:
    path = directory / "local_worker.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return [sys.executable, "-u", str(path), str(directory)]


def _cleanup_recorded_processes(directory: Path) -> None:
    """Contain a failed regression without leaving its synthetic workers alive."""
    for path in sorted(directory.glob("worker-*.pid"), reverse=True):
        pid = int(path.read_text(encoding="utf-8"))
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_supervisor_returns_completed_result_without_rewriting_it(tmp_path):
    from research_agent.runtime.supervisor import supervise_process

    expected = {
        "status": "partial",
        "execution_path": "mvp",
        "run_id": "local-completed-worker",
        "gaps": ["Current stock price was not available."],
    }
    serialized = json.dumps(expected, ensure_ascii=False, indent=3) + "\n"
    (tmp_path / "prepared-result.json").write_text(serialized, encoding="utf-8")
    command = _script(
        tmp_path,
        r"""
        import pathlib
        import sys

        directory = pathlib.Path(sys.argv[1])
        result = (directory / "prepared-result.json").read_bytes()
        (directory / "result.json").write_bytes(result)
        (directory / "notes.md").write_text("Saved research notes.\n", encoding="utf-8")
        """,
    )

    result = supervise_process(command, tmp_path, deadline_seconds=2.0)

    assert result == expected
    assert (tmp_path / "result.json").read_text(encoding="utf-8") == serialized
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == (
        "Saved research notes.\n"
    )


def test_supervisor_terminates_hung_worker_without_draft_as_failed(tmp_path):
    from research_agent.runtime.supervisor import supervise_process

    command = _script(
        tmp_path,
        """
        import os
        import pathlib
        import sys
        import time

        directory = pathlib.Path(sys.argv[1])
        (directory / "worker-0.pid").write_text(str(os.getpid()), encoding="utf-8")
        (directory / "notes.md").write_text("Research before a hung SDK call.", encoding="utf-8")
        time.sleep(60)
        (directory / "finished.txt").write_text("must never finish", encoding="utf-8")
        """,
    )

    try:
        started = time.monotonic()
        result = supervise_process(command, tmp_path, deadline_seconds=2.0)
        elapsed = time.monotonic() - started

        assert elapsed < 3.0, "The deadline must not wait for worker/SDK cleanup."
        assert result["status"] == "failed"
        assert result["execution_path"] == "mvp"
        assert result["error"]["code"] == "hard_deadline_exceeded"
        assert (
            json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == result
        )
        assert (tmp_path / "notes.md").read_text(encoding="utf-8") == (
            "Research before a hung SDK call."
        )
        assert not (tmp_path / "finished.txt").exists()
    finally:
        _cleanup_recorded_processes(tmp_path)


def test_supervisor_kills_descendants_and_retains_saved_research_and_draft(tmp_path):
    from research_agent.runtime.supervisor import supervise_process

    saved = {
        "notes.md": "# Research notes\nObserved revenue, explicit valuation gap.\n",
        "sources.jsonl": '{"url":"https://example.invalid/read","text":"Read text."}\n',
        "report.draft.md": "# Apple research\n## 1. Conclusion\nDraft content survives.\n",
    }
    (tmp_path / "prepared-artifacts.json").write_text(
        json.dumps(saved), encoding="utf-8"
    )
    command = _script(
        tmp_path,
        """
        import json
        import os
        import pathlib
        import subprocess
        import sys
        import time

        directory = pathlib.Path(sys.argv[1])
        generation = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        (directory / f"worker-{generation}.pid").write_text(str(os.getpid()), encoding="utf-8")
        if generation == 0:
            artifacts = json.loads((directory / "prepared-artifacts.json").read_text(encoding="utf-8"))
            for filename, content in artifacts.items():
                (directory / filename).write_text(content, encoding="utf-8")
        if generation < 2:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen(
                [sys.executable, "-u", __file__, str(directory), str(generation + 1)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        heartbeat = directory / f"heartbeat-{generation}.txt"
        while True:
            heartbeat.write_text(str(time.monotonic_ns()), encoding="utf-8")
            time.sleep(0.03)
        """,
    )

    try:
        started = time.monotonic()
        result = supervise_process(command, tmp_path, deadline_seconds=2.0)
        elapsed = time.monotonic() - started

        assert elapsed < 3.0, (
            "The process tree must terminate within a bounded deadline."
        )
        assert result["status"] == "partial"
        assert result["execution_path"] == "mvp"
        assert result["error"]["code"] == "hard_deadline_exceeded"
        assert (
            json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == result
        )
        for filename, content in saved.items():
            assert (tmp_path / filename).read_text(encoding="utf-8") == content

        heartbeats = [
            tmp_path / f"heartbeat-{generation}.txt" for generation in range(3)
        ]
        before = [path.read_bytes() for path in heartbeats]
        time.sleep(0.3)
        assert [path.read_bytes() for path in heartbeats] == before, (
            "An orphaned child or grandchild kept running after the supervisor returned."
        )
    finally:
        _cleanup_recorded_processes(tmp_path)
