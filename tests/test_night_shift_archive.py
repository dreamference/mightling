"""Night Shift's archive (DESKTOP §16 B41): `ling-admin night archive [--weeks N]`.

Finished runs older than N weeks, their transcripts and the old morning reports move to
`night/archive/` in the same layout; live tasks, newer runs and the branches stay.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from dreamference.night_shift import NightShiftArchive, NightShiftQueue

NOW = datetime(2026, 10, 27, 9, 0).astimezone()


def _task(night: Path, task_id: str, status: str, at: datetime) -> Path:
    path = night / "tasks" / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "id": task_id,
        "repo": "/w/app",
        "task": f"task {task_id}",
        "status": status,
        "branch": f"night/{task_id}",
        "history": [{"at": at.isoformat(timespec="seconds"), "status": status}],
    }
    path.write_text(json.dumps(record))
    return path


def _file(path: Path, text: str, modified: datetime) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (modified.timestamp(), modified.timestamp()))
    return path


def test_old_finished_runs_their_logs_and_reports_move_and_the_rest_stays(tmp_path: Path) -> None:
    night = tmp_path / "night"
    old = NOW - timedelta(weeks=6)
    new = NOW - timedelta(days=3)
    _task(night, "20260910-0100-abc", "done", old)
    _task(night, "20260910-0200-def", "stalled", old)
    _task(night, "20261024-0100-123", "done", new)
    # Live tasks stay however old: queued, interrupted, running.
    _task(night, "20260901-0100-aaa", "queued", old)
    _task(night, "20260901-0200-bbb", "interrupted", old)
    _task(night, "20260901-0300-ccc", "running", old)
    logs = night / "logs"
    for name in (
        "20260910-0100-abc.jsonl",
        "20260910-0100-abc-a2.last.txt",
        "20260910-0100-abc.judge.jsonl",
    ):
        _file(logs / name, "x", NOW)  # moved with their task, however new the file
    _file(logs / "20261024-0100-123.jsonl", "x", old)  # its task is new: stays
    _file(logs / "20260901-0100-aaa.refined.txt", "x", old)  # its task is queued: stays
    _file(logs / "20260801-0100-fff.jsonl", "x", old)  # its task is gone (dropped): old, so moved
    _file(logs / "20261020-0100-eee.jsonl", "x", new)  # its task is gone, but new: stays
    _file(logs / "notes.txt", "x", old)  # not a run's log: stays
    reports = night / "reports"
    _file(reports / "2026-09-10.md", "# old", old)
    _file(reports / "2026-10-24.md", "# new", new)
    (night / "tasks" / "20260910-0100-abc.lock").touch()

    outcome = NightShiftArchive.archive(night, 4, now=NOW)

    assert outcome is not None
    assert sorted(outcome.tasks) == ["20260910-0100-abc", "20260910-0200-def"]
    assert (outcome.logs, outcome.reports, outcome.skipped) == (4, 1, [])
    archive = night / "archive"
    assert sorted(p.name for p in (archive / "tasks").iterdir()) == [
        "20260910-0100-abc.json",
        "20260910-0200-def.json",
    ]
    assert sorted(p.name for p in (archive / "logs").iterdir()) == [
        "20260801-0100-fff.jsonl",
        "20260910-0100-abc-a2.last.txt",
        "20260910-0100-abc.jsonl",
        "20260910-0100-abc.judge.jsonl",
    ]
    assert [p.name for p in (archive / "reports").iterdir()] == ["2026-09-10.md"]
    assert sorted(p.name for p in (night / "tasks").glob("*.json")) == [
        "20260901-0100-aaa.json",
        "20260901-0200-bbb.json",
        "20260901-0300-ccc.json",
        "20261024-0100-123.json",
    ]
    assert sorted(p.name for p in logs.iterdir()) == [
        "20260901-0100-aaa.refined.txt",
        "20261020-0100-eee.jsonl",
        "20261024-0100-123.jsonl",
        "notes.txt",
    ]
    assert not (night / "tasks" / "20260910-0100-abc.lock").exists()
    # The record moves whole: the window reads it from the archive as from the queue.
    moved = json.loads((archive / "tasks" / "20260910-0100-abc.json").read_text())
    assert moved["branch"] == "night/20260910-0100-abc"
    # A second archive finds nothing more to move.
    again = NightShiftArchive.archive(night, 4, now=NOW)
    assert again is not None and (again.tasks, again.logs, again.reports) == ([], 0, 0)


def test_the_weeks_set_the_cutoff_and_a_task_without_times_uses_its_file(tmp_path: Path) -> None:
    night = tmp_path / "night"
    _task(night, "20261010-0100-abc", "failed", NOW - timedelta(days=16))
    path = night / "tasks" / "20261001-0100-def.json"
    path.write_text(json.dumps({"id": "20261001-0100-def", "status": "cancelled", "history": []}))
    os.utime(path, ((NOW - timedelta(days=30)).timestamp(), (NOW - timedelta(days=30)).timestamp()))

    outcome = NightShiftArchive.archive(night, 3, now=NOW)
    assert outcome is not None and outcome.tasks == ["20261001-0100-def"]
    outcome = NightShiftArchive.archive(night, 2, now=NOW)
    assert outcome is not None and outcome.tasks == ["20261010-0100-abc"]
    with pytest.raises(ValueError, match="one week"):
        NightShiftArchive.archive(night, 0, now=NOW)


def test_a_file_already_in_the_archive_is_never_overwritten(tmp_path: Path) -> None:
    night = tmp_path / "night"
    old = NOW - timedelta(weeks=8)
    _task(night, "20260901-0100-abc", "done", old)
    _file(night / "archive" / "tasks" / "20260901-0100-abc.json", "kept", old)

    outcome = NightShiftArchive.archive(night, 4, now=NOW)

    assert outcome is not None and outcome.tasks == []
    assert outcome.skipped == [str(night / "tasks" / "20260901-0100-abc.json")]
    assert (night / "archive" / "tasks" / "20260901-0100-abc.json").read_text() == "kept"
    assert (night / "tasks" / "20260901-0100-abc.json").exists()


def test_nothing_moves_while_a_run_holds_the_queue(tmp_path: Path) -> None:
    night = tmp_path / "night"
    _task(night, "20260901-0100-abc", "done", NOW - timedelta(weeks=8))
    with NightShiftQueue.runner_lock(night) as held:
        assert held
        assert NightShiftArchive.archive(night, 4, now=NOW) is None
    assert (night / "tasks" / "20260901-0100-abc.json").exists()


def test_ling_admin_night_archive_moves_old_runs_and_rejects_unknown_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    home = tmp_path / "home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    night = home / "night"
    _task(night, "20260601-0100-abc", "done", datetime.now().astimezone() - timedelta(weeks=10))
    _task(night, "20260602-0100-def", "done", datetime.now().astimezone() - timedelta(weeks=2))

    with pytest.raises(SystemExit) as archived:
        DreamferenceCLIController.run_cli(["night", "archive"])
    assert archived.value.code == 0
    said = capsys.readouterr().out
    assert "Moved 1 finished runs older than 4 weeks" in said and "branches stay" in said
    assert (night / "archive" / "tasks" / "20260601-0100-abc.json").exists()

    with pytest.raises(SystemExit) as weekly:
        DreamferenceCLIController.run_cli(["night", "archive", "--weeks", "1"])
    assert weekly.value.code == 0
    assert (night / "archive" / "tasks" / "20260602-0100-def.json").exists()

    with pytest.raises(SystemExit) as zero:
        DreamferenceCLIController.run_cli(["night", "archive", "--weeks", "0"])
    assert zero.value.code == 1

    with pytest.raises(SystemExit) as unknown:
        DreamferenceCLIController.run_cli(["night", "archive", "--days", "3"])
    assert unknown.value.code == 2
