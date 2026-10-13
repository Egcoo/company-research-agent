"""Enforce worker deadlines and retain artifacts when a process tree stops."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from research_agent.runtime import _write_json


def _stop_process_tree(process):
    """Stop the owned worker and descendants without waiting for SDK cleanup."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=0.6,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        process.kill()


def supervise_process(command, directory, deadline_seconds):
    """Enforce an absolute wall-clock limit on the complete worker process tree."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    cleanup = min(2.0, deadline_seconds / 4)
    deadline = started + max(0.05, deadline_seconds - cleanup)
    with (directory / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=log,
            cwd=Path(__file__).resolve().parents[2],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        expired = False
        try:
            while process.poll() is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    expired = True
                    _stop_process_tree(process)
                    break
                time.sleep(min(0.05, left))
        finally:
            if process.poll() is None:
                _stop_process_tree(process)
    result_path = directory / "result.json"
    if not expired and result_path.exists():
        return json.loads(result_path.read_text(encoding="utf-8"))
    previous = {}
    if result_path.exists():
        previous = json.loads(result_path.read_text(encoding="utf-8"))
    result = {
        **previous,
        "execution_path": "mvp",
        "run_id": directory.name,
        "status": "partial" if (directory / "report.draft.md").exists() else "failed",
        "report_written": (directory / "2026-10-13-GOOG.md").exists(),
        "quality_accepted": None,
        "error": {
            "code": "hard_deadline_exceeded" if expired else "worker_failed",
            "message": "The worker stopped; saved sources, notes and drafts were retained.",
        },
        "latency_seconds": round(time.monotonic() - started, 3),
        "output_dir": str(directory),
    }
    _write_json(result_path, result)
    return result
