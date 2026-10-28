"""`scripts/release_on_green.py`: when main is released without a hand step. Every GitHub and git call is faked."""

import importlib.util
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
HEAD = "a" * 40
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def mod() -> Any:
    spec = importlib.util.spec_from_file_location(
        "release_on_green", ROOT / "scripts" / "release_on_green.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRepo:
    """Canned answers for the gh and git calls, keyed by what the script asks."""

    def __init__(self) -> None:
        self.release_runs: list[dict] = []
        self.dry_runs: list[dict] = [{"conclusion": "success", "status": "completed"}]
        self.sha_runs: list[dict] = []
        self.tags = ["v1.7.1", "v1.7.2", "v1.8.0-rc.1"]
        self.changed = ["dreamference/hardware/model_matrix_registry.py", "docs/x.md"]
        self.notes_on_main = ""
        self.calls: list[list[str]] = []
        self.head_after = HEAD

    def __call__(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        cmd = " ".join(argv)
        if argv[0] == "gh":
            return self.gh(argv[2])
        answers = {
            "git fetch": "",
            "git tag": "\n".join(self.tags),
            "git diff": "\n".join(self.changed),
            "git ls-tree": self.notes_on_main,
        }
        for prefix, answer in answers.items():
            if cmd.startswith(prefix):
                return answer
        raise AssertionError(cmd)

    def gh(self, path: str) -> str:
        if "commits/main" in path:
            seen = sum("commits/main" in " ".join(c) for c in self.calls)
            return json.dumps({"sha": HEAD if seen == 1 else self.head_after})
        runs = {
            "workflows/release.yml/runs": self.release_runs,
            "workflows/release-dry-run.yml/runs": self.dry_runs,
            "actions/runs?head_sha": self.sha_runs,
        }
        for key, value in runs.items():
            if key in path:
                return json.dumps({"workflow_runs": value})
        raise AssertionError(path)

    def decide(self, mod: Any, switch: str | None = "true") -> tuple[list[str] | None, str]:
        return mod.ReleaseOnGreen("o/r", self, NOW).decide(switch)


def run_entry(
    path: str, name: str, status: str = "completed", conclusion: str | None = "success"
) -> dict:
    return {"path": path, "name": name, "status": status, "conclusion": conclusion}


def test_green_main_with_user_changes_starts_the_next_patch_with_generated_notes(mod: Any) -> None:
    command, reason = FakeRepo().decide(mod)
    assert command is not None, reason
    assert command[:5] == ["gh", "workflow", "run", "release.yml", "--repo"]
    assert "version=1.7.3" in command
    assert "generated_notes=true" in command
    assert "draft=false" in command and "prerelease=false" in command
    assert command[command.index("--ref") + 1] == "main"


def test_a_notes_spec_on_main_is_used(mod: Any) -> None:
    repo = FakeRepo()
    repo.notes_on_main = "specs/DREAMFERENCE_RELEASE_1.7.3.md"
    command, _ = repo.decide(mod)
    assert command and "generated_notes=false" in command


@pytest.mark.parametrize("switch", [None, "", "false", "False", "0", "yes"])
def test_only_true_switches_it_on(mod: Any, switch: str | None) -> None:
    repo = FakeRepo()
    command, reason = repo.decide(mod, switch)
    assert command is None and "RELEASE_ON_GREEN" in reason
    assert repo.calls == [], "off must not even read GitHub"


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting"])
def test_never_two_releases_at_once(mod: Any, status: str) -> None:
    repo = FakeRepo()
    repo.release_runs = [
        {"id": 7, "status": status, "conclusion": None, "created_at": "2026-10-29T08:00:00Z"}
    ]
    command, reason = repo.decide(mod)
    assert command is None and "running" in reason


def test_at_most_one_start_per_three_hours(mod: Any) -> None:
    repo = FakeRepo()
    repo.release_runs = [
        {
            "id": 7,
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-10-29T09:30:00Z",
        }
    ]
    assert repo.decide(mod)[0] is None
    repo.release_runs[0]["created_at"] = "2026-10-29T08:59:00Z"
    assert repo.decide(mod)[0] is not None


@pytest.mark.parametrize(
    "entry",
    [
        run_entry(".github/workflows/windows.yml", "Windows", conclusion="failure"),
        run_entry(
            ".github/workflows/windows.yml", "Windows", status="in_progress", conclusion=None
        ),
        run_entry(".github/workflows/windows.yml", "Windows", conclusion="cancelled"),
    ],
)
def test_a_failed_or_pending_run_on_head_is_not_green(mod: Any, entry: dict) -> None:
    repo = FakeRepo()
    repo.sha_runs = [entry]
    assert repo.decide(mod)[0] is None


def test_skipped_runs_do_not_block_and_releases_are_ignored(mod: Any) -> None:
    repo = FakeRepo()
    repo.sha_runs = [
        run_entry(".github/workflows/docs.yml", "Product site", conclusion="skipped"),
        run_entry(".github/workflows/release.yml", "Release", conclusion="failure"),
    ]
    assert repo.decide(mod)[0] is not None


def test_a_head_without_a_successful_dry_run_is_not_green(mod: Any) -> None:
    repo = FakeRepo()
    repo.dry_runs = [{"conclusion": "failure", "status": "completed"}]
    command, reason = repo.decide(mod)
    assert command is None and "dry run" in reason


@pytest.mark.parametrize(
    "changed",
    [
        [
            "docs/a.md",
            "specs/B.md",
            "tests/test_x.py",
            "website/i.html",
            "AGENTS.md",
            ".github/workflows/x.yml",
        ],
        [],
    ],
)
def test_docs_specs_and_tests_only_changes_do_not_release(mod: Any, changed: list[str]) -> None:
    repo = FakeRepo()
    repo.changed = changed
    command, reason = repo.decide(mod)
    assert command is None and "matters" in reason


def test_one_user_facing_path_among_docs_releases(mod: Any) -> None:
    repo = FakeRepo()
    repo.changed = ["docs/a.md", "ling-rs/src/lib.rs"]
    assert repo.decide(mod)[0] is not None


def test_prerelease_tags_do_not_set_the_base_and_a_taken_tag_stops(mod: Any) -> None:
    assert mod.newest_stable_tag(["v1.7.2", "v1.10.0", "v2.0.0-rc.1", "latest"]) == "v1.10.0"
    assert mod.next_patch("v1.10.0") == "1.10.1"
    repo = FakeRepo()
    repo.tags = ["v1.7.2", "v1.7.3-rc.1"]
    command, _ = repo.decide(mod)
    assert command and "version=1.7.3" in command


def test_main_moving_while_deciding_skips(mod: Any) -> None:
    repo = FakeRepo()
    repo.head_after = "b" * 40
    command, reason = repo.decide(mod)
    assert command is None and "moved" in reason


def test_main_dry_prints_and_never_runs_the_dispatch(
    mod: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    fake = FakeRepo()
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("RELEASE_ON_GREEN", "true")
    monkeypatch.setattr(mod, "run", fake)
    monkeypatch.setattr(
        mod.ReleaseOnGreen,
        "__init__",
        lambda self, repo, runner=fake, now=NOW: (
            setattr(self, "repo", repo),
            setattr(self, "run", runner),
            setattr(self, "now", now),
            None,
        )[-1],
    )
    assert mod.main(["--dry"]) == 0
    assert "release: release 1.7.3" in capsys.readouterr().out
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in fake.calls)


def test_the_workflow_only_dispatches_and_is_wired_to_the_script() -> None:
    text = (ROOT / ".github/workflows/release-on-green.yml").read_text()
    data = yaml.safe_load(text)
    on = data.get("on", data.get(True))
    assert set(on) == {"workflow_run", "workflow_dispatch"}
    assert on["workflow_run"]["workflows"] == ["Release dry run", "Release"]
    assert on["workflow_run"]["branches"] == ["main"]
    assert data["concurrency"]["cancel-in-progress"] is False
    assert data["jobs"]["decide"]["permissions"] == {"contents": "read", "actions": "write"}
    assert "vars.RELEASE_ON_GREEN" in text
    for word in (
        "gh release",
        "git tag",
        "git push",
        "RELEASE_SIGNING_KEY",
        "environment:",
        "secrets.",
    ):
        assert word not in text, word
    assert "pull_request" not in text.replace("Never pull_request or pull_request_target.", "")


def test_the_release_workflow_takes_the_generated_notes_input() -> None:
    data = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    inputs = data.get("on", data.get(True))["workflow_dispatch"]["inputs"]
    assert inputs["generated_notes"]["default"] is False
