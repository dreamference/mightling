"""Night Shift's repeating tasks (DESKTOP_PARITY §9.5, the owner's decision 7).

Templates, when they are due, and queueing through `ling night add` in the repository, never a task
file written here.
"""

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dreamference.night_shift import NightShiftRecurring


def test_a_template_is_written_whole_and_checked(tmp_path: Path) -> None:
    night = tmp_path / "night"
    repo = tmp_path / "repo"
    repo.mkdir()
    daily = NightShiftRecurring.add(
        night, repo, "  Update the changelog  ", "daily", test="pytest -q"
    )
    weekly = NightShiftRecurring.add(night, repo, "Bump the dependencies", "weekly", "fri")
    assert (
        daily["task"] == "Update the changelog"
        and daily["test"] == "pytest -q"
        and daily["weekday"] is None
    )
    assert weekly["weekday"] == "fri" and weekly["test"] is None
    on_disk = json.loads((night / "recurring" / f"{daily['id']}.json").read_text())
    assert on_disk == daily
    assert [entry["id"] for entry in NightShiftRecurring.templates(night)] == [
        daily["id"],
        weekly["id"],
    ]
    for bad in [
        ("", "daily", None),
        ("x", "hourly", None),
        ("x", "weekly", None),
        ("x", "weekly", "someday"),
    ]:
        with pytest.raises(ValueError):
            NightShiftRecurring.add(night, repo, bad[0], bad[1], bad[2])
    with pytest.raises(ValueError):
        NightShiftRecurring.add(night, tmp_path / "missing", "x", "daily")
    assert NightShiftRecurring.drop(night, weekly["id"]) is True
    assert NightShiftRecurring.drop(night, weekly["id"]) is False
    assert NightShiftRecurring.drop(night, "../../etc") is False


def test_a_template_is_due_once_a_day_on_its_day() -> None:
    friday = date(2026, 10, 23)
    assert NightShiftRecurring.due({"cadence": "daily", "last_queued": None}, friday)
    assert not NightShiftRecurring.due({"cadence": "daily", "last_queued": "2026-10-23"}, friday)
    assert NightShiftRecurring.due({"cadence": "daily", "last_queued": "2026-10-22"}, friday)
    assert NightShiftRecurring.due(
        {"cadence": "weekly", "weekday": "fri", "last_queued": None}, friday
    )
    assert not NightShiftRecurring.due(
        {"cadence": "weekly", "weekday": "mon", "last_queued": None}, friday
    )


def test_due_templates_are_queued_through_ling_night_add_in_their_repository(
    tmp_path: Path,
) -> None:
    night = tmp_path / "night"
    repo = tmp_path / "repo"
    repo.mkdir()
    gone = tmp_path / "gone"
    gone.mkdir()
    daily = NightShiftRecurring.add(night, repo, "Update the changelog", "daily", test="pytest -q")
    lost = NightShiftRecurring.add(night, gone, "Tidy up", "daily")
    gone.rmdir()
    NightShiftRecurring.add(night, repo, "Only on Mondays", "weekly", "mon")
    calls: list[Any] = []

    def run(args: list[str], **options: Any) -> "subprocess.CompletedProcess[str]":
        calls.append((args, options["cwd"]))
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="Queued 20261023-2200 on branch night/20261023-2200, from 1a2b3c4.",
            stderr="",
        )

    said = NightShiftRecurring.queue_due(night, "/opt/ling", date(2026, 10, 23), run=run)
    assert calls == [
        (["/opt/ling", "night", "add", "--test", "pytest -q", "Update the changelog"], repo)
    ]
    assert any("queued for tonight" in line for line in said)
    assert any("not queued" in line and "gone" in line for line in said)
    by_id = {entry["id"]: entry for entry in NightShiftRecurring.templates(night)}
    assert by_id[daily["id"]]["last_queued"] == "2026-10-23"
    assert by_id[lost["id"]]["last_error"]
    # Once a night: a second run the same night queues nothing again.
    calls.clear()
    NightShiftRecurring.queue_due(night, "/opt/ling", date(2026, 10, 23), run=run)
    assert calls == []


def test_a_refused_add_is_kept_as_the_reason_and_tried_again(tmp_path: Path) -> None:
    night = tmp_path / "night"
    repo = tmp_path / "repo"
    repo.mkdir()
    template = NightShiftRecurring.add(night, repo, "Update the changelog", "daily")

    def refuse(args: list[str], **options: Any) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="not a git repository")

    said = NightShiftRecurring.queue_due(night, "/opt/ling", date(2026, 10, 23), run=refuse)
    assert said == [f"Repeating task {template['id']} not queued: not a git repository"]
    kept = NightShiftRecurring.templates(night)[0]
    assert kept["last_queued"] is None and kept["last_error"] == "not a git repository"


def test_ling_admin_night_every_adds_lists_and_drops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as added:
        DreamferenceCLIController.run_cli(
            [
                "night",
                "every",
                "add",
                "--weekly",
                "fri",
                "--test",
                "pytest -q",
                "--",
                "Bump",
                "the",
                "deps",
            ]
        )
    assert added.value.code == 0
    assert "every fri" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        DreamferenceCLIController.run_cli(["night", "every", "list"])
    [line] = capsys.readouterr().out.strip().splitlines()
    template = json.loads(line)
    assert (
        template["task"] == "Bump the deps"
        and template["test"] == "pytest -q"
        and template["repo"] == str(repo)
    )
    with pytest.raises(SystemExit) as dropped:
        DreamferenceCLIController.run_cli(["night", "every", "drop", template["id"]])
    assert dropped.value.code == 0
    with pytest.raises(SystemExit) as again:
        DreamferenceCLIController.run_cli(["night", "every", "drop", template["id"]])
    assert again.value.code == 1


def test_a_refusal_printed_by_ling_night_add_is_not_taken_for_queued(tmp_path: Path) -> None:
    night = tmp_path / "night"
    repo = tmp_path / "repo"
    repo.mkdir()
    NightShiftRecurring.add(night, repo, "Update the changelog", "daily")

    def printed(args: list[str], **options: Any) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="Night Shift needs a git repository, and this directory is not in one.",
            stderr="",
        )

    said = NightShiftRecurring.queue_due(night, "/opt/ling", date(2026, 10, 23), run=printed)
    assert "not queued" in said[0]
    assert NightShiftRecurring.templates(night)[0]["last_queued"] is None
