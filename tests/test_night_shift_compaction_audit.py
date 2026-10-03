"""The nightly compaction audit (specs/DREAMFERENCE_PUFFIN_COMPACTION.md §10.3).

Rollouts are written here in the shape Codex records them (`session_meta`, `response_item`
calls and outputs, `compacted`, the ledger as a developer message); nothing reads the user's
sessions.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from dreamference.night_shift import (
    NightShiftCompactionAudit,
    NightShiftReport,
    NightShiftRunner,
    NightShiftSettings,
)

PATCH = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch\n"


def call(call_id, command, output, custom=False):
    """A tool call and its output, as two rollout records."""
    if custom:
        item = {"type": "custom_tool_call", "name": "apply_patch", "call_id": call_id, "input": command}
    else:
        item = {"type": "function_call", "name": "exec_command", "call_id": call_id,
                "arguments": json.dumps({"cmd": command})}
    out = {"type": "function_call_output", "call_id": call_id,
           "output": [{"type": "input_text", "text": output}]}
    return [{"type": "response_item", "payload": item}, {"type": "response_item", "payload": out}]


def compacted(summary):
    return [{"type": "compacted", "payload": {"message": summary, "replacement_history": []}}]


def ledger(text):
    content = [{"type": "input_text", "text": "Ledger built from the tool history by rule; …\n" + text}]
    return [{"type": "response_item", "payload": {"type": "message", "role": "developer", "content": content}}]


def write_rollout(sessions: Path, records, name="rollout-2026-10-03T01-00-00-01a10215-c233-7922-94e0-1f6a473522a8"):
    day = sessions / "2026" / "10" / "03"
    day.mkdir(parents=True, exist_ok=True)
    session = {"id": "01a10215-c233", "cwd": "/repo", "timestamp": "2026-10-03T01:00:00Z"}
    meta = {"type": "session_meta", "payload": session}
    path = day / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in [meta, *records]) + "\n")
    return path


def two_compactions():
    return [
        *call("1", PATCH, "Success. Process exited with code 0", custom=True),
        *call("2", "cd /repo && cat > /repo/src/util.py <<'EOF'\nx = 1\nEOF", "Process exited with code 0"),
        *call("3", "cat > /tmp/repro.py <<'EOF'\nprint(1)\nEOF", "Process exited with code 0"),
        *call("4", "cd /repo && pytest tests/test_app.py",
              "Process exited with code 1\nE   AssertionError: boom\n1 failed, 2 passed in 0.10s"),
        *compacted("## Handoff\nEdited src/app.py; tests still failing."),
        *ledger("Files changed (git status):\n- M src/util.py\nLast test result: 1 failed, 2 passed in 0.10s"),
        *call("5", "python -c 'open(\"src/conf.json\", \"w\").write(\"{}\")'", "Process exited with code 0"),
        *compacted("Summary that names src/conf.json."),
    ]


def test_each_compaction_is_judged_against_what_it_replaced(tmp_path):
    path = write_rollout(tmp_path / "sessions", two_compactions())

    first, second = NightShiftCompactionAudit.audit_rollout(path)

    assert (first["number"], first["total"], first["session"], first["cwd"]) == (1, 2, "01a10215", "/repo")
    # /tmp/repro.py is scratch and left out; util.py is under the working directory, made relative.
    assert first["kept"]["files changed"] == (1, 2)
    assert first["lost"]["files changed"] == ["src/util.py"]
    # The failed pytest is named by the file it ran; the summary does not name it.
    assert first["kept"]["failed commands"] == (0, 1)
    assert first["lost"]["last test result"] == ["1 failed, 2 passed in 0.10s"]
    # The ledger after the summary names util.py and the test result, not the test file.
    assert first["ledger_covered"] == 2
    # The second compaction replaced only the call after the first one: a file a script wrote.
    assert second["kept"] == {"files changed": (1, 1), "failed commands": (0, 0), "last test result": (0, 0)}
    assert second["ledger_covered"] == 0


def test_the_report_lines_total_and_name_only_compactions_that_lost_something(tmp_path):
    path = write_rollout(tmp_path / "sessions", two_compactions())
    findings = NightShiftCompactionAudit.audit_rollout(path)

    lines = NightShiftCompactionAudit.render(findings, datetime(2026, 10, 3, 1, 0).timestamp())

    assert lines[0].startswith("2 compaction(s) in 1 session(s) since 2026-10-03 01:00.")
    assert "files changed: 2 of 3; failed commands: 0 of 1; last test result: 0 of 1" in lines[0]
    assert len(lines) == 2
    assert "compaction 1 of 2" in lines[1] and "`src/util.py`" in lines[1]
    assert "the ledger named 2 of them" in lines[1]
    assert NightShiftCompactionAudit.render([], 0) == []


def test_a_command_that_failed_without_a_file_or_an_error_name_is_not_judged(tmp_path):
    records = [*call("1", "python - <<'PY'\nprint(1)\nPY", "Process exited with code 2"), *compacted("s")]
    (finding,) = NightShiftCompactionAudit.audit_rollout(write_rollout(tmp_path / "sessions", records))
    assert finding["kept"]["failed commands"] == (0, 0)


def test_a_summary_that_is_a_tool_call_is_reported(tmp_path):
    records = [*call("1", "ls", "Process exited with code 0"), *compacted('<tool_call>{"name": "x"}</tool_call>')]
    findings = NightShiftCompactionAudit.audit_rollout(write_rollout(tmp_path / "sessions", records))
    assert findings[0]["stray_tool_call"]
    assert "stray tool call" in NightShiftCompactionAudit.render(findings, 0)[1]


def test_paths_are_named_whole_by_their_last_parts_or_a_distinctive_basename():
    names = NightShiftCompactionAudit.names_path
    assert names("changed sympy/solvers/solveset.py", "/testbed/sympy/solvers/solveset.py")
    assert names("see solveset.py for the fix", "sympy/solvers/solveset.py")
    assert not names("see the __init__.py files", "sympy/core/__init__.py")
    assert not names("mysolveset.pyc", "sympy/solvers/solveset.py")


def test_runs_cover_the_time_since_the_previous_run(tmp_path):
    sessions, night = tmp_path / "sessions", tmp_path / "night"
    path = write_rollout(sessions, two_compactions())
    now = time.time()
    old = now - 2 * 24 * 3600
    os.utime(path, (old, old))
    # The first run looks back a day, so a two-day-old rollout is not audited.
    assert NightShiftCompactionAudit.run(sessions, night, now=now) == []
    os.utime(path, (now + 10, now + 10))
    assert NightShiftCompactionAudit.run(sessions, night, now=now + 20)[0].startswith("2 compaction(s)")
    # The next run starts where that one stopped.
    assert NightShiftCompactionAudit.run(sessions, night, now=now + 30) == []


def test_an_empty_queue_still_reports_the_audit_and_switching_it_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    write_rollout(tmp_path / "home" / "sessions", two_compactions())
    night = tmp_path / "night"

    off = NightShiftSettings({"compaction_audit": False})
    assert NightShiftRunner.run(night_dir=night, puffin_bin="puffin", vllm_host="http://127.0.0.1:9", settings=off) == 0
    assert not (night / "reports").exists()

    assert NightShiftRunner.run(night_dir=night, puffin_bin="puffin", vllm_host="http://127.0.0.1:9",
                                settings=NightShiftSettings({})) == 0
    (report,) = (night / "reports").glob("*.md")
    text = report.read_text()
    assert "## Compactions" in text and "`src/util.py`" in text
    assert "## Review" not in text


def test_the_report_keeps_its_review_section_when_there_are_tasks():
    task = {"id": "t", "repo": "/r", "base": "a" * 40, "status": "done", "task": "x",
            "result": {"branch": "night/t"}}
    text = NightShiftReport.render(datetime(2026, 10, 3, 1, 0), [task], [], compactions=["1 compaction(s)."])
    assert text.index("## Review") < text.index("## Compactions")
