"""Hidden acceptance tests for the Night Shift task (specs/DREAMFERENCE_MIGHTLING_COMPACTION.md §11.2's Phase 0 task).

Used by scripts/turn_end_compaction_bench.py: copied into the scratch repository (built at Phase 0's
base, before the rename, hence the old names) after the session ends, run, and removed. Not named
`test_*` so that this repository's own suite never collects it.
"""
import json
from datetime import datetime

import pytest

from dreamference.night_shift import NightShiftQueue, NightShiftReport, NightShiftRunner, NightShiftSettings
from test_night_shift import fake_host, git, queue, setup  # noqa: F401 - fixtures


def test_hidden_dry_run_starts_nothing_and_changes_nothing(setup, fake_host, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "change")
    ids = ["20261001-0100-a00", "20261001-0100-a10"]
    for task_id in ids:
        queue(setup["night"], setup["repo"], task_text="Task", test="test -f hello.txt", task_id=task_id)
    files = sorted((setup["night"] / "tasks").glob("*.json"))
    before = [path.read_bytes() for path in files]
    code = NightShiftRunner.run(minutes=5, idle_minutes=0, night_dir=setup["night"],
                                puffin_bin=setup["puffin"], vllm_host="http://x",
                                settings=NightShiftSettings({"index": False}), dry_run=True)
    out = capsys.readouterr().out
    assert code == 0
    assert [path.read_bytes() for path in files] == before
    assert all(task_id in out for task_id in ids)
    assert "test -f hello.txt" in out and str(setup["repo"]) in out
    assert not setup["calls"].exists()
    assert git(setup["repo"], "branch", "--list", "night/*").stdout.strip() == ""
    assert not (setup["night"] / "reports").exists() or not list((setup["night"] / "reports").glob("*.md"))


def test_hidden_max_tasks_caps_one_night(setup, fake_host, monkeypatch):
    assert NightShiftSettings({}).max_tasks == 0
    assert NightShiftSettings({"max_tasks": 2}).max_tasks == 2
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "change")
    for index in range(3):
        queue(setup["night"], setup["repo"], task_text=f"Task {index}", test="test -f hello.txt",
              task_id=f"20261001-0100-a{index}0")
    code = NightShiftRunner.run(minutes=5, idle_minutes=0, night_dir=setup["night"],
                                puffin_bin=setup["puffin"], vllm_host="http://x",
                                settings=NightShiftSettings({"max_tasks": 1, "index": False}))
    assert code == 0
    statuses = sorted(task["status"] for task in NightShiftQueue.tasks(setup["night"]))
    assert statuses == ["done", "queued", "queued"]


def test_hidden_report_totals_line():
    tasks = [
        {"id": "a", "repo": "/r", "base": "b" * 40, "task": "One", "status": "done",
         "result": {"branch": "night/a", "diff_stat": "", "test_command": "pytest", "test_source": "pytest",
                    "test_result": "passed", "attempts": 1, "nudges": 0, "wall_s": 5}},
        {"id": "b", "repo": "/r", "base": "b" * 40, "task": "Two", "status": "stalled",
         "result": {"last_message": "I'll now do it.", "attempts": 1, "nudges": 2, "wall_s": 5}},
        {"id": "c", "repo": "/r", "base": "b" * 40, "task": "Three", "status": "done",
         "result": {"branch": "night/c", "diff_stat": "", "test_command": "pytest", "test_source": "pytest",
                    "test_result": "passed", "attempts": 1, "nudges": 0, "wall_s": 5}},
    ]
    text = NightShiftReport.render(datetime(2026, 10, 1, 1, 0), tasks, [])
    totals = [line for line in text.splitlines() if line.startswith("Totals:")]
    assert totals == ["Totals: 2 done, 1 stalled"]


def test_hidden_cli_passes_dry_run(monkeypatch):
    from dreamference.cli import dreamference_cli_controller as cli
    seen = {}
    monkeypatch.setattr(NightShiftRunner, "run", lambda **kwargs: seen.update(kwargs) or 0)
    with pytest.raises(SystemExit) as info:
        cli.main(["night", "run", "--dry-run"])
    assert info.value.code == 0 and seen.get("dry_run") is True
    seen.clear()
    with pytest.raises(SystemExit):
        cli.main(["night", "run"])
    assert not seen.get("dry_run")


def test_hidden_json_report(setup, fake_host, monkeypatch):
    tasks = [
        {"id": "a", "repo": "/r", "base": "b" * 40, "task": "One", "status": "done", "branch": "night/a",
         "result": {"branch": "night/a", "diff_stat": "", "test_command": "pytest", "test_source": "pytest",
                    "test_result": "passed", "attempts": 1, "nudges": 0, "wall_s": 5}},
        {"id": "b", "repo": "/r", "base": "b" * 40, "task": "Two", "status": "stalled", "branch": "night/b",
         "result": {"last_message": "I'll now do it.", "attempts": 1, "nudges": 2, "wall_s": 5}},
    ]
    document = json.loads(NightShiftReport.to_json(datetime(2026, 10, 1, 1, 0), tasks, ["note one"]))
    assert document["date"] == "2026-10-01"
    assert document["totals"] == {"done": 1, "stalled": 1}
    assert document["notes"] == ["note one"]
    first, second = document["tasks"]
    assert (first["id"], first["repo"], first["status"]) == ("a", "/r", "done")
    assert first["branch"] == "night/a" and first["test_result"] == "passed"
    assert (second["id"], second["status"]) == ("b", "stalled")
    # A night run writes it beside the Markdown report.
    monkeypatch.setenv("FAKE_PUFFIN_MODE", "change")
    for index in range(2):
        queue(setup["night"], setup["repo"], task_text=f"Task {index}", test="test -f hello.txt",
              task_id=f"20261001-0100-a{index}0")
    assert NightShiftRunner.run(minutes=5, idle_minutes=0, night_dir=setup["night"], puffin_bin=setup["puffin"],
                                vllm_host="http://x", settings=NightShiftSettings({"index": False})) == 0
    markdown = sorted((setup["night"] / "reports").glob("*.md"))
    written = sorted((setup["night"] / "reports").glob("*.json"))
    assert len(markdown) == 1 and [path.stem for path in written] == [markdown[0].stem]
    assert json.loads(written[0].read_text())["totals"] == {"done": 2}
