"""The nightly release dry run (release-dry-run.yml) and the build workflow it shares with release.yml.

The workflows are read, never run: their shape is asserted. What matters is that the dry run runs
the release's own build and test jobs (one file, release-build.yml, called by both, so the two cannot
drift) and that nothing in it can tag, sign with the release key or publish.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

ROOT: Final = Path(__file__).resolve().parent.parent
WORKFLOWS: Final = ROOT / ".github" / "workflows"
BUILD_JOBS: Final = {
    "test",
    "python-dist",
    "desktop",
    "ling",
    "windows",
    "ling-clients",
    "desktop-mac",
}
PUBLISHING: Final = (
    "gh release",
    "git tag",
    "git push",
    "RELEASE_SIGNING_KEY",
    "ssh-keygen",
    "attest-build-provenance",
)


def workflow(name: str) -> dict[Any, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    assert isinstance(data, dict)
    return data


def trigger(data: dict[Any, Any]) -> dict[str, Any]:
    # PyYAML reads the bare key `on` as True.
    value = data.get("on", data.get(True))
    assert isinstance(value, dict)
    return value


def granted(data: dict[Any, Any]) -> list[dict[str, str]]:
    """Every permissions block in a workflow: the top level's and each job's."""
    blocks = [data.get("permissions", {})]
    blocks += [job.get("permissions", {}) for job in data["jobs"].values()]
    return blocks


def test_the_release_keeps_only_its_own_jobs_and_calls_the_shared_build() -> None:
    jobs = workflow("release.yml")["jobs"]
    assert set(jobs) == {"validate", "build", "release"}
    assert jobs["build"]["uses"] == "./.github/workflows/release-build.yml"
    assert jobs["release"]["needs"] == ["validate", "build"]
    assert "needs.build.result == 'success'" in jobs["release"]["if"]
    # Every input the release dispatch takes for its builds reaches the shared workflow.
    inputs = trigger(workflow("release.yml"))["workflow_dispatch"]["inputs"]
    passed = jobs["build"]["with"]
    for name in set(inputs) - {"prerelease", "draft", "generated_notes"}:
        assert passed[name] == f"${{{{ inputs.{name} }}}}", name


def test_only_the_release_job_can_write_and_only_a_dispatch_starts_it() -> None:
    # Security review 2026-10, finding 2: a moved tag must not run with a token that publishes.
    release = workflow("release.yml")
    assert set(trigger(release)) == {"workflow_dispatch"}
    assert release["permissions"] == {"contents": "read"}
    for name, job in release["jobs"].items():
        writes = {key for key, value in job.get("permissions", {}).items() if value == "write"}
        assert not writes or name == "release", name
    assert release["jobs"]["release"]["permissions"]["contents"] == "write"
    # An input reaches shell code only as a validated boolean; any other goes through env.
    inputs = trigger(release)["workflow_dispatch"]["inputs"]
    for job in release["jobs"].values():
        for step in job.get("steps", []):
            for name in re.findall(r"\$\{\{\s*inputs\.(\w+)\s*\}\}", step.get("run", "")):
                assert inputs[name]["type"] == "boolean", (step.get("name"), name)


def test_the_shared_build_holds_every_build_and_publishes_nothing() -> None:
    build = workflow("release-build.yml")
    assert set(trigger(build)) == {"workflow_call"}
    assert set(build["jobs"]) == BUILD_JOBS
    for block in granted(build):
        assert set(block.values()) <= {"read"}, block
    text = (WORKFLOWS / "release-build.yml").read_text()
    for word in PUBLISHING:
        assert word not in text, word
    # The release's defaults are the shared workflow's, so a dry run that passes none builds them.
    release_inputs = trigger(workflow("release.yml"))["workflow_dispatch"]["inputs"]
    build_inputs = trigger(build)["workflow_call"]["inputs"]
    for name, spec in build_inputs.items():
        if name in release_inputs:
            assert spec.get("default") == release_inputs[name].get("default"), name
    assert build_inputs["sign"]["default"] is False


def test_the_dry_run_runs_by_hand_and_the_timer_and_calls_the_same_build_and_calls_the_same_build() -> (
    None
):
    dry = workflow("release-dry-run.yml")
    on = trigger(dry)
    # gx10's mightling-release-dry-run.timer is the only scheduler (a second one ran it twice a day).
    assert "schedule" not in on
    assert "workflow_dispatch" in on
    jobs = dry["jobs"]
    build = jobs["build"]
    assert build["uses"] == "./.github/workflows/release-build.yml"
    assert build["needs"] == "gate"
    # Every platform: the clients and the Mac dmgs, which a release dispatch leaves to an input.
    assert build["with"]["build_clients"] is True
    # Never the release environment, so never the Apple identity either.
    assert build["with"]["sign"] is False
    assert not {"run_tests", "build_windows", "build_desktop", "build_mightling"} & set(
        build["with"]
    )


def test_the_dry_run_cannot_tag_sign_or_publish() -> None:
    dry = workflow("release-dry-run.yml")
    text = (WORKFLOWS / "release-dry-run.yml").read_text()
    for word in PUBLISHING:
        assert word not in text, word
    assert "environment:" not in text
    assert not any("environment" in job for job in dry["jobs"].values())
    assert "secrets." not in text
    assert dry["permissions"] == {"contents": "read"}
    # Only the report may write, and only issues.
    for name, job in dry["jobs"].items():
        writes = {key for key, value in job.get("permissions", {}).items() if value == "write"}
        assert writes == ({"issues"} if name == "report" else set()), name


def test_a_scheduled_run_is_skipped_when_main_has_not_moved() -> None:
    gate = workflow("release-dry-run.yml")["jobs"]["gate"]
    check = next(s for s in gate["steps"] if s.get("id") == "check")
    run = str(check["run"])
    assert '"$EVENT" == "schedule"' in run
    assert "release-dry-run.yml/runs?branch=main&status=success&per_page=1" in run
    assert '"$last" == "$GITHUB_SHA"' in run
    assert "run=false" in run


def test_the_report_opens_updates_and_closes_one_issue() -> None:
    report = workflow("release-dry-run.yml")["jobs"]["report"]
    assert report["needs"] == ["gate", "build"]
    assert report["if"].startswith("${{ !cancelled()")
    assert report["env"]["TITLE"] == "Release dry run failing"
    run = str(report["steps"][0]["run"])
    assert 'select(.title == \\"$TITLE\\")' in run
    assert "--paginate" in run and "/jobs?per_page=100" in run
    assert 'gh issue create --title "$TITLE"' in run
    assert 'gh issue edit "$number"' in run
    assert 'gh issue close "$number"' in run
    assert '"$GITHUB_REF" == "refs/heads/main"' in run


def test_electron_is_fetched_once_before_any_desktop_test() -> None:
    # Electron's package fetches its binary on first `require`; vitest's workers raced to do it.
    for name, job in (
        ("release-build.yml", "desktop"),
        ("windows.yml", "desktop"),
        ("mac-preview.yml", "build"),
    ):
        steps = workflow(name)["jobs"][job]["steps"]
        electron = [s for s in steps if s.get("working-directory") == "desktop/electron"]
        lines = "\n".join(str(s["run"]) for s in electron).splitlines()
        script = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
        fetch = script.index("node node_modules/electron/install.js")
        assert script.index("npm ci") < fetch < script.index("npm test"), name
        assert all(m.start() > fetch for m in re.finditer(r"npm test|vitest", script)), name
