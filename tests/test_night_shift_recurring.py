"""Night Shift's repeating tasks (DESKTOP_PARITY §9.5, the owner's decision 7).

Templates, when they are due, and queueing through `ling night add` in the repository, never a task
file written here.
"""

import calendar
import json
import subprocess
from datetime import date, timedelta
from itertools import pairwise
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


def test_the_new_cadences_are_checked_and_kept_with_their_parameter(tmp_path: Path) -> None:
    night = tmp_path / "night"
    repo = tmp_path / "repo"
    repo.mkdir()
    days = NightShiftRecurring.add(night, repo, "Audit", "days", " Mon,wed,fri,mon ")
    every = NightShiftRecurring.add(night, repo, "Sync", "every", "3")
    monthly = NightShiftRecurring.add(night, repo, "Rotate", "monthly", 31)
    assert (days["days"], days["every"], days["day"]) == (["mon", "wed", "fri"], None, None)
    assert (every["every"], every["days"], every["weekday"]) == (3, None, None)
    assert (monthly["day"], monthly["every"]) == (31, None)
    assert NightShiftRecurring.add(night, repo, "Listed", "days", ["sat", "sun"])["days"] == [
        "sat",
        "sun",
    ]
    for cadence, bad in [
        ("days", None),
        ("days", ""),
        ("days", []),
        ("days", "mon,holiday"),
        ("days", 3),
        ("every", 0),
        ("every", 366),
        ("every", -1),
        ("every", "3.5"),
        ("every", True),
        ("every", None),
        ("every", [3]),
        ("monthly", 0),
        ("monthly", 32),
        ("monthly", "x"),
        ("monthly", None),
        ("weekly", ["fri"]),
    ]:
        with pytest.raises(ValueError):
            NightShiftRecurring.add(night, repo, "Bad", cadence, bad)
    assert len(NightShiftRecurring.templates(night)) == 4


def nights(first: date, last: date) -> list[date]:
    """Every date from ``first`` to ``last``, both included."""
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def run_nightly(template: dict[str, Any], first: date, last: date) -> list[date]:
    """The nights a template is queued, as the runner marks it each night it is due."""
    queued = []
    for night in nights(first, last):
        if NightShiftRecurring.due(template, night):
            template["last_queued"] = night.isoformat()
            queued.append(night)
    return queued


def test_a_monthly_template_runs_once_a_month_on_its_day_or_the_months_last() -> None:
    # Two years and more of nights, over year ends and February 2028, a leap year.
    for day in range(1, 32):
        template = {"cadence": "monthly", "day": day, "last_queued": None}
        queued = run_nightly(template, date(2026, 1, 1), date(2028, 3, 31))
        expected = [
            date(year, month, min(day, calendar.monthrange(year, month)[1]))
            for year in (2026, 2027, 2028)
            for month in range(1, 13)
            if (year, month) <= (2028, 3)
        ]
        assert queued == expected, day
    template = {"cadence": "monthly", "day": 31, "last_queued": None}
    assert run_nightly(template, date(2027, 12, 1), date(2028, 3, 31)) == [
        date(2027, 12, 31),
        date(2028, 1, 31),
        date(2028, 2, 29),
        date(2028, 3, 31),
    ]
    assert run_nightly(
        {"cadence": "monthly", "day": 30, "last_queued": None}, date(2027, 2, 1), date(2027, 3, 31)
    ) == [
        date(2027, 2, 28),
        date(2027, 3, 30),
    ]


def test_an_every_n_template_runs_the_night_it_is_set_then_every_nth_night() -> None:
    for every in (1, 2, 3, 7, 30, 365):
        template = {"cadence": "every", "every": every, "last_queued": None}
        queued = run_nightly(template, date(2026, 12, 20), date(2028, 1, 15))
        assert queued[0] == date(2026, 12, 20), every
        assert all((later - earlier).days == every for earlier, later in pairwise(queued)), every
        assert queued[-1] + timedelta(days=every) > date(2028, 1, 15)
    # Over the year's end, three nights apart.
    template = {"cadence": "every", "every": 3, "last_queued": None}
    assert run_nightly(template, date(2026, 12, 28), date(2027, 1, 6)) == [
        date(2026, 12, 28),
        date(2026, 12, 31),
        date(2027, 1, 3),
        date(2027, 1, 6),
    ]
    # A night it was not queued (refused, or no run) counts on from the night it was.
    late = {"cadence": "every", "every": 3, "last_queued": "2026-12-28"}
    assert not NightShiftRecurring.due(late, date(2026, 12, 30))
    assert NightShiftRecurring.due(late, date(2026, 12, 31))
    assert NightShiftRecurring.due(late, date(2027, 1, 2))
    for broken in [0, 366, "3", None, True]:
        assert not NightShiftRecurring.due(
            {"cadence": "every", "every": broken, "last_queued": None}, date(2026, 12, 28)
        )


def test_a_days_template_runs_on_its_weekdays_only() -> None:
    template = {"cadence": "days", "days": ["mon", "wed", "fri"], "last_queued": None}
    queued = run_nightly(template, date(2026, 12, 1), date(2027, 2, 28))
    assert queued and {night.weekday() for night in queued} == {0, 2, 4}
    assert len(queued) == sum(
        1 for night in nights(date(2026, 12, 1), date(2027, 2, 28)) if night.weekday() in (0, 2, 4)
    )
    assert not NightShiftRecurring.due(
        {"cadence": "days", "days": "mon", "last_queued": None}, date(2026, 10, 19)
    )
    assert not NightShiftRecurring.due(
        {"cadence": "hourly", "last_queued": None}, date(2026, 10, 19)
    )


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["night", "every", "--days", "mon"], ["night", "every", "add", "--days", "mon"]),
        (["night", "every", "--every=3", "x"], ["night", "every", "add", "--every=3", "x"]),
        (
            ["--agent", "codex", "night", "every", "--daily", "x"],
            ["--agent", "codex", "night", "every", "add", "--daily", "x"],
        ),
        (
            ["--config=x.toml", "night", "every", "--daily", "x"],
            ["--config=x.toml", "night", "every", "add", "--daily", "x"],
        ),
        (["night", "every", "add", "--daily", "x"], None),
        (["night", "every", "list"], None),
        (["night", "every", "drop", "r000000000001"], None),
        (["night", "every", "--help"], None),
        (["night", "every"], None),
        (["night", "add", "night", "every", "--daily"], None),
        (["run", "night every --daily"], None),
    ],
)
def test_the_shorthand_adds_add_only_right_after_night_every(
    argv: list[str], expected: list[str] | None
) -> None:
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    assert DreamferenceCLIController.night_every_shorthand(list(argv)) == (expected or argv)


def test_ling_admin_night_every_takes_the_new_cadences_one_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(repo)

    def run(*argv: str) -> int:
        with pytest.raises(SystemExit) as code:
            DreamferenceCLIController.run_cli(list(argv))
        return int(code.value.code or 0)

    assert run("night", "every", "--days", "mon,wed,fri", "--", "Days", "task") == 0
    assert "on mon,wed,fri" in capsys.readouterr().out
    assert run("night", "every", "add", "--every", "3", "--", "Every", "task") == 0
    assert "every 3 nights" in capsys.readouterr().out
    assert run("night", "every", "--monthly", "31", "Monthly") == 0
    assert "on day 31 of each month" in capsys.readouterr().out
    assert run("night", "every", "--every", "0", "Never") == 1
    assert "❌" in capsys.readouterr().out
    for twice in (
        ["--days", "mon", "--every", "3"],
        ["--daily", "--monthly", "15"],
        ["--weekly", "fri", "--days", "sat"],
    ):
        assert run("night", "every", "add", *twice, "--", "Bad") == 2
    assert run("night", "every", "add", "--every", "three", "Bad") == 2
    assert run("night", "every", "--every", "3", "--bogus", "Bad") == 2
    assert run("night", "every", "list") == 0
    listed = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    assert [entry["cadence"] for entry in listed] == ["days", "every", "monthly"]
    assert run("night", "every", "drop", listed[0]["id"]) == 0


def test_a_template_dropped_while_its_task_is_being_queued_stays_dropped(tmp_path: Path) -> None:
    night = tmp_path / "night"
    repo = tmp_path / "repo"
    repo.mkdir()
    template = NightShiftRecurring.add(night, repo, "Update the changelog", "daily")

    def dropped_meanwhile(args: list[str], **options: Any) -> "subprocess.CompletedProcess[str]":
        # `ling night repeat drop` runs while `ling night add` is still at work.
        assert NightShiftRecurring.drop(night, template["id"])
        return subprocess.CompletedProcess(args, 0, stdout="Queued 20261023-2200 on branch x.", stderr="")

    said = NightShiftRecurring.queue_due(night, "/opt/ling", date(2026, 10, 23), run=dropped_meanwhile)
    assert any("queued for tonight" in line for line in said)
    assert NightShiftRecurring.templates(night) == []
    assert not list(NightShiftRecurring.folder(night).glob("*.tmp"))
