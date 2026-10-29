"""What the workflows take from outside the repository's own trusted runs (security review 2026-10).

Every action is pinned to a commit, with its tag in a comment, so a moved tag cannot change what
runs. The Mac preview's "Client binaries (another run)" step runs here with a stand-in `gh` that
answers from fixtures: a pull request's run, a fork's among them, can upload an artifact of the
same name, and the step must never bundle it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
TARGET = "aarch64-apple-darwin"
REPO = "dreamference/mightling"
REPO_ID = 1


def test_every_action_is_pinned_to_a_commit_with_its_tag_in_a_comment() -> None:
    unpinned = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            match = re.match(r"\s*(?:-\s+)?uses:\s*(\S+)(.*)$", line)
            if not match or match.group(1).startswith("./"):
                continue
            if not re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", match.group(1)) or not re.match(
                r"\s+#\s*\S", match.group(2)
            ):
                unpinned.append(f"{path.name}:{number}: {line.strip()}")
    assert unpinned == []


def another_run_step() -> str:
    data = yaml.safe_load((WORKFLOWS / "mac-preview.yml").read_text())
    step = next(
        s
        for s in data["jobs"]["build"]["steps"]
        if s.get("name") == "Client binaries (another run)"
    )
    return step["run"].replace("${{ matrix.target }}", TARGET)


def artifact(
    run: int, head_repository_id: int = REPO_ID, expired: bool = False
) -> dict[str, object]:
    return {
        "name": f"mightling-client-{TARGET}",
        "expired": expired,
        "workflow_run": {
            "id": run,
            "repository_id": REPO_ID,
            "head_repository_id": head_repository_id,
        },
    }


def run(run_id: int, event: str, head: str = REPO) -> dict[str, object]:
    return {
        "id": run_id,
        "event": event,
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": head},
    }


def take(
    tmp_path: Path,
    client_run_id: str,
    artifacts: list[dict[str, object]],
    runs: list[dict[str, object]],
):
    """Runs the step with a `gh` that answers from the fixtures; returns the result and the run downloaded."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "artifacts.json").write_text(json.dumps({"artifacts": artifacts}))
    for item in runs:
        (fixtures / f"run-{item['id']}.json").write_text(json.dumps(item))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        f'fixtures="{fixtures}"\n'
        'if [[ "$1" == run && "$2" == download ]]; then echo "$3" > "$fixtures/downloaded"; exit 0; fi\n'
        'path="$2"; shift 2; [[ "$1" == --jq ]] || exit 2\n'
        'case "$path" in\n'
        '  */actions/artifacts\\?*) file="$fixtures/artifacts.json" ;;\n'
        '  */actions/runs/*) file="$fixtures/run-${path##*/}.json" ;;\n'
        "  *) exit 2 ;;\n"
        "esac\n"
        '[[ -f "$file" ]] || { echo "HTTP 404" >&2; exit 1; }\n'
        'jq -r "$2" "$file"\n'
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CLIENT_RUN_ID": client_run_id,
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_SERVER_URL": "https://github.com",
    }
    result = subprocess.run(
        ["bash", "-e", "-c", another_run_step()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    downloaded = fixtures / "downloaded"
    return result, downloaded.read_text().strip() if downloaded.exists() else None


needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="the stand-in gh filters with jq")


@needs_jq
def test_the_newest_artifact_of_a_fork_or_a_pull_request_is_passed_over_for_the_repositorys_own(
    tmp_path: Path,
) -> None:
    artifacts = [
        artifact(30, head_repository_id=99),
        artifact(20),
        artifact(15, expired=True),
        artifact(10),
    ]
    runs = [
        run(30, "pull_request", head="someone/mightling"),
        run(20, "pull_request"),
        run(10, "push"),
    ]
    result, downloaded = take(tmp_path, "", artifacts, runs)
    assert result.returncode == 0, result.stderr
    assert downloaded == "10"


@needs_jq
def test_with_only_untrusted_artifacts_it_packages_without_ling(tmp_path: Path) -> None:
    artifacts = [artifact(30, head_repository_id=99), artifact(20)]
    runs = [run(30, "pull_request_target", head="someone/mightling"), run(20, "pull_request")]
    result, downloaded = take(tmp_path, "", artifacts, runs)
    assert result.returncode == 0, result.stderr
    assert downloaded is None
    assert "packaging without ling" in result.stdout


@needs_jq
@pytest.mark.parametrize(
    "named",
    [
        run(40, "pull_request", head="someone/mightling"),
        run(40, "pull_request"),
        run(40, "push", head="someone/fork"),
    ],
)
def test_a_named_run_that_is_not_the_repositorys_own_is_refused(
    tmp_path: Path, named: dict[str, object]
) -> None:
    result, downloaded = take(tmp_path, "40", [], [named])
    assert result.returncode == 1
    assert downloaded is None
    assert "not bundled" in result.stdout


@needs_jq
def test_a_named_run_of_the_repository_is_taken_and_none_packages_without_ling(
    tmp_path: Path,
) -> None:
    result, downloaded = take(tmp_path, "40", [], [run(40, "workflow_dispatch")])
    assert result.returncode == 0, result.stderr
    assert downloaded == "40"
    elsewhere = tmp_path / "none"
    elsewhere.mkdir()
    result, downloaded = take(elsewhere, "none", [], [])
    assert result.returncode == 0 and downloaded is None
