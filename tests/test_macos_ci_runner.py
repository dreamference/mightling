"""The self-hosted macOS runner: its job filter and the workflows that route to it.

The runner (scripts/macos/ci-runner/) builds the Apple silicon client when the MACOS_RUNNER
variable names it. The repository is public and a fork's pull request runs the workflow as the
fork wrote it, so the job-started hook must refuse every event but push, workflow_dispatch and
schedule, every other repository, ref and workflow, and the workflows must fall back to GitHub's
macos-15 when the variable is unset or `macos_runner` is `github`.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
RUNNER_DIR = ROOT / "scripts" / "macos" / "ci-runner"
WORKFLOWS = ROOT / ".github" / "workflows"
REPO = "dreamference/mightling"
LOCK = "/Users/Shared/ci-lock/mac.lock"
ROUTED = (
    "${{ (inputs.macos_runner != 'github' && !startsWith(github.event_name, 'pull_request') "
    "&& vars.MACOS_RUNNER) || '\"macos-15\"' }}"
)


def workflow(name: str) -> dict:
    """Load a workflow file.

    Args:
        name: The file name under .github/workflows.

    Returns:
        dict: The parsed workflow (PyYAML reads the `on` key as True).
    """
    return yaml.safe_load((WORKFLOWS / name).read_text())


def hook(tmp_path: Path, name: str) -> Path:
    """Copy a hook with its lock moved into the test's folder.

    Args:
        tmp_path: The test's folder.
        name: The hook's file name.

    Returns:
        Path: The copy to run.
    """
    copy = tmp_path / name
    copy.write_text((RUNNER_DIR / name).read_text().replace(LOCK, str(tmp_path / "mac.lock")))
    return copy


def start(
    tmp_path: Path,
    event: str,
    ref: str,
    workflow_file: str,
    repo: str = REPO,
    payload: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the job-started hook as the runner would, then the job-completed hook.

    Args:
        tmp_path: The test's folder.
        event: GITHUB_EVENT_NAME.
        ref: GITHUB_REF.
        workflow_file: The workflow's path inside the repository named by GITHUB_WORKFLOW_REF.
        repo: GITHUB_REPOSITORY, and the repository of the workflow reference.
        payload: The event payload.

    Returns:
        subprocess.CompletedProcess: The job-started hook's result.
    """
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload or {"ref": ref}))
    environment = {
        "PATH": os.environ["PATH"],
        "GITHUB_REPOSITORY": repo,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": ref,
        "GITHUB_WORKFLOW_REF": f"{repo}/.github/workflows/{workflow_file}@{ref}",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_RUN_ID": "1",
        "GITHUB_JOB": "build",
    }
    result = subprocess.run(
        ["bash", str(hook(tmp_path, "job-started.sh"))],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    subprocess.run(
        ["bash", str(hook(tmp_path, "job-completed.sh"))],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result


@pytest.mark.parametrize(
    ("event", "ref", "workflow_file"),
    [
        ("push", "refs/heads/clients/mac", "build-clients.yml"),
        ("schedule", "refs/heads/main", "release-dry-run.yml"),
        ("workflow_dispatch", "refs/heads/release/1.8.0", "release.yml"),
    ],
)
def test_the_hook_runs_trusted_jobs(
    tmp_path: Path, event: str, ref: str, workflow_file: str
) -> None:
    result = start(tmp_path, event, ref, workflow_file)
    assert result.returncode == 0, result.stderr
    assert "Mac lock taken" in result.stdout
    assert not (tmp_path / "mac.lock").exists(), "job-completed releases the lock"


@pytest.mark.parametrize(
    ("event", "ref", "workflow_file", "repo", "reason"),
    [
        ("pull_request", "refs/pull/1/merge", "build-clients.yml", REPO, "event pull_request"),
        (
            "pull_request_target",
            "refs/heads/main",
            "release.yml",
            REPO,
            "event pull_request_target",
        ),
        ("push", "refs/tags/v1.8.0", "release.yml", REPO, "ref refs/tags"),
        ("push", "refs/heads/main", "windows.yml", REPO, "workflow"),
        ("push", "refs/heads/main", "release.yml", "someone/mightling", "repository"),
    ],
)
def test_the_hook_refuses_everything_else(
    tmp_path: Path, event: str, ref: str, workflow_file: str, repo: str, reason: str
) -> None:
    result = start(tmp_path, event, ref, workflow_file, repo=repo)
    assert result.returncode != 0
    assert f"refused: {reason}" in result.stderr


def test_the_hook_refuses_a_pull_request_payload(tmp_path: Path) -> None:
    result = start(
        tmp_path, "push", "refs/heads/main", "release.yml", payload={"pull_request": {"number": 1}}
    )
    assert result.returncode != 0
    assert "refused: a pull request event" in result.stderr


def test_the_hook_waits_for_a_hand_build_and_takes_over_a_stale_ci_lock(tmp_path: Path) -> None:
    (tmp_path / "mac.lock").write_text("ci run 0 job build since earlier")
    assert start(tmp_path, "push", "refs/heads/main", "release.yml").returncode == 0
    text = (RUNNER_DIR / "job-started.sh").read_text()
    assert "waiting for the Mac's lock" in text
    assert "deadline=$(( $(date +%s) + 3 * 3600 ))" in text


def test_only_the_apple_silicon_client_is_routed_to_the_mac() -> None:
    build = workflow("build-clients.yml")
    job = build["jobs"]["build"]
    assert job["runs-on"] == "${{ fromJSON(matrix.runner) }}"
    runners = {entry["target"]: entry["runner"] for entry in job["strategy"]["matrix"]["include"]}
    assert runners == {
        "x86_64-unknown-linux-gnu": '"ubuntu-24.04"',
        "aarch64-apple-darwin": ROUTED,
        "x86_64-apple-darwin": '"macos-15-intel"',
    }
    for name in ("workflow_dispatch", "workflow_call"):
        assert build[True][name]["inputs"]["macos_runner"]["default"] == "mac"
    # The dmg job starts the app, which opens a window: GitHub's runners only.
    preview = workflow("mac-preview.yml")["jobs"]["build"]["strategy"]["matrix"]["include"]
    assert {entry["runner"] for entry in preview} == {"macos-15", "macos-15-intel"}


def test_the_release_workflows_pass_the_choice_through() -> None:
    assert (
        workflow("release-build.yml")["jobs"]["ling-clients"]["with"]["macos_runner"]
        == "${{ inputs.macos_runner }}"
    )
    assert (
        workflow("release.yml")["jobs"]["build"]["with"]["macos_runner"]
        == "${{ inputs.macos_runner }}"
    )
    assert (
        workflow("release-dry-run.yml")["jobs"]["build"]["with"]["macos_runner"]
        == "${{ inputs.macos_runner || 'mac' }}"
    )
    for name in ("release.yml", "release-dry-run.yml"):
        assert workflow(name)[True]["workflow_dispatch"]["inputs"]["macos_runner"]["options"] == [
            "mac",
            "github",
        ]


@pytest.mark.parametrize(
    "name", ["build-clients.yml", "release-build.yml", "release.yml", "release-dry-run.yml"]
)
def test_no_workflow_that_reaches_the_mac_runs_on_a_pull_request(name: str) -> None:
    triggers = workflow(name)[True]
    assert not {"pull_request", "pull_request_target"} & set(triggers)


def test_the_setup_keeps_the_runner_unprivileged() -> None:
    text = (RUNNER_DIR / "setup.sh").read_text()
    assert "is an administrator; refusing" in text
    assert "<key>UserName</key><string>$USER_NAME</string>" in text
    assert 'install -o root -g wheel -m 755 "$here/job-started.sh"' in text
    assert 'chown root:wheel "$RUNNER_DIR/.env"' in text
    assert "self-hosted,macOS,ARM64,mac-mini" in text
    assert "shasum -a 256 -c" in text
