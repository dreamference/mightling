#!/usr/bin/env python3
"""Release on green: decide whether main should be released now, and start the Release workflow.

Run by ``.github/workflows/release-on-green.yml`` when a Release dry run or a Release finishes (and
by hand). It reads the state of the repository each time, so what started it does not matter. It
starts ``release.yml`` (a ``workflow_dispatch`` on ``main``) only when every rule below holds, and
otherwise exits 0 with one ``skip: <reason>`` line, so a skipped run is a green run.

Rules, in this order:

1. The kill switch: the repository variable ``RELEASE_ON_GREEN`` must be ``true``. Unset, ``false``
   or anything else means off.
2. Serialization: no Release run is queued, waiting or running.
3. Gap: the newest Release run (any outcome, a manual one too) started at least ``MIN_GAP_HOURS``
   (3) hours ago, so a failing release cannot loop and a release never overlaps the next one's
   builds.
4. Green: every workflow run on main's head commit has finished without failing, and the Release
   dry run (the one full build and test pass on main) has succeeded on that commit.
5. Matters to users: the head differs from the newest release tag in a path outside docs, specs,
   tests, the website, the paper, notes and ``.github``.
6. The version is the newest stable tag with its patch plus one; its tag must be free.

Release notes: ``specs/DREAMFERENCE_RELEASE_<version>.md`` is used when it exists on main;
otherwise the release goes out with GitHub's generated notes (``generated_notes``).

Standard library only; the ``gh`` command does every GitHub call (``GH_TOKEN`` in the workflow).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

MIN_GAP_HOURS: Final[int] = 3
DRY_RUN_WORKFLOW: Final[str] = "release-dry-run.yml"
RELEASE_WORKFLOW: Final[str] = "release.yml"
# Workflows that never gate a release: the release itself and its trigger, and the rehearsal of a
# future rename (it must never hold a 1.x release).
NOT_A_GATE: Final[frozenset[str]] = frozenset(
    {RELEASE_WORKFLOW, "release-on-green.yml", "ragon-rename-rehearsal.yml"}
)
ACTIVE: Final[frozenset[str]] = frozenset(
    {"queued", "in_progress", "waiting", "pending", "requested"}
)
FAILED: Final[frozenset[str]] = frozenset(
    {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
)
STABLE_TAG: Final[re.Pattern[str]] = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# A change confined to these paths does not matter to users of a release.
NOT_USER_FACING: Final[tuple[str, ...]] = (
    "docs/",
    "specs/",
    "tests/",
    "website/",
    "paper/",
    "images/",
    ".github/",
    "codex-tests/",
    "scripts/qa/",
    "mkdocs.yml",
)
SWITCH_ON: Final[str] = "true"
SUCCESS: Final[str] = "success"
ROOT_NOTES: Final[re.Pattern[str]] = re.compile(r"^[^/]+\.md$")

Runner = Callable[[Sequence[str]], str]


def run(argv: Sequence[str]) -> str:
    """Run a command and return its standard output.

    Args:
        argv: The command and its arguments.

    Returns:
        Standard output, stripped.

    Raises:
        subprocess.CalledProcessError: The command failed.
    """
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()


def matters_to_users(paths: Sequence[str]) -> bool:
    """Say whether any changed path is outside the paths that do not reach a user.

    Args:
        paths: Paths from ``git diff --name-only``.

    Returns:
        True when at least one path is not documentation, specs, tests, notes or CI configuration.
    """
    return any(
        not (path.startswith(NOT_USER_FACING) or ROOT_NOTES.match(path) or path == "")
        for path in paths
    )


def newest_stable_tag(tags: Sequence[str]) -> str | None:
    """Return the highest tag like ``v1.7.2`` (pre-releases ignored), or None.

    Args:
        tags: Tag names.

    Returns:
        The newest stable tag.
    """
    found = [
        (tuple(int(n) for n in m.groups()), tag) for tag in tags if (m := STABLE_TAG.match(tag))
    ]
    return max(found)[1] if found else None


def next_patch(tag: str) -> str:
    """Return the version after a tag, patch plus one, without the ``v``.

    Args:
        tag: A stable tag such as ``v1.7.2``.

    Returns:
        ``1.7.3`` for ``v1.7.2``.
    """
    major, minor, patch = (int(n) for n in STABLE_TAG.match(tag).groups())  # type: ignore[union-attr]
    return f"{major}.{minor}.{patch + 1}"


class SkipError(Exception):
    """A rule says no release now; the message is the reason."""


class ReleaseOnGreen:
    """One evaluation of the rules; ``decide`` returns the dispatch to make or the reason not to."""

    def __init__(self, repo: str, runner: Runner = run, now: datetime | None = None) -> None:
        """Set the repository and the seams a test replaces.

        Args:
            repo: ``owner/name``.
            runner: Runs a command and returns its output (the ``gh`` and ``git`` calls).
            now: The current time.
        """
        self.repo = repo
        self.run = runner
        self.now = now or datetime.now(UTC)
        self.reason = ""

    def api(self, path: str) -> dict:
        """Read one GitHub API path with ``gh``.

        Args:
            path: The path below ``repos/<repo>/``.

        Returns:
            The decoded JSON.
        """
        return json.loads(self.run(["gh", "api", f"repos/{self.repo}/{path}"]))

    def workflow_runs(self, workflow: str, query: str = "") -> list[dict]:
        """List a workflow's newest runs.

        Args:
            workflow: The workflow file name.
            query: Extra query parameters (``head_sha=...``).

        Returns:
            Run objects, newest first.
        """
        return self.api(f"actions/workflows/{workflow}/runs?per_page=30{query}")["workflow_runs"]

    def decide(self, switch: str | None) -> tuple[list[str] | None, str]:
        """Apply the rules.

        Args:
            switch: The value of the ``RELEASE_ON_GREEN`` variable.

        Returns:
            ``(command, reason)``: the ``gh workflow run`` command to execute, or None and why not.
        """
        try:
            command = self.dispatch(switch)
            return command, self.reason
        except SkipError as skip:
            return None, str(skip)

    def dispatch(self, switch: str | None) -> list[str]:
        """Check the rules in order and build the dispatch.

        Args:
            switch: The value of the ``RELEASE_ON_GREEN`` variable.

        Returns:
            The ``gh workflow run`` command.

        Raises:
            SkipError: A rule says not now; the message is the reason.
        """
        if (switch or "").strip().lower() != SWITCH_ON:
            raise SkipError("RELEASE_ON_GREEN is not true")
        runs = self.workflow_runs(RELEASE_WORKFLOW)
        busy = [r for r in runs if r["status"] in ACTIVE]
        if busy:
            raise SkipError(
                f"a release is running (run {busy[0]['id']}); the next evaluation takes the latest green main"
            )
        if runs:
            started = datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00"))
            if self.now - started < timedelta(hours=MIN_GAP_HOURS):
                raise SkipError(
                    f"the last release run started {started:%Y-%m-%d %H:%M} UTC, under {MIN_GAP_HOURS} h ago"
                )
        head = self.api("commits/main")["sha"]
        reason = self.not_green(head)
        if reason:
            raise SkipError(reason)
        version, last = self.next_version(head)
        notes = self.run(
            ["git", "ls-tree", "--name-only", head, f"specs/DREAMFERENCE_RELEASE_{version}.md"]
        )
        command = [
            "gh", "workflow", "run", RELEASE_WORKFLOW, "--repo", self.repo, "--ref", "main",
            "-f", f"version={version}", "-f", "prerelease=false", "-f", "draft=false",
            "-f", f"generated_notes={'false' if notes else 'true'}",
        ]  # fmt: skip
        if self.api("commits/main")["sha"] != head:
            raise SkipError("main moved while deciding; the next evaluation decides")
        self.reason = f"release {version} from {head[:10]} (after {last})"
        return command

    def next_version(self, head: str) -> tuple[str, str]:
        """Return the version to release and the tag it follows, or skip.

        Args:
            head: The head commit of main.

        Returns:
            ``(version, last tag)``.

        Raises:
            SkipError: No stable tag, nothing user-facing since it, or the tag is taken.
        """
        self.run(["git", "fetch", "--tags", "--quiet", "origin", "main"])
        tags = self.run(["git", "tag", "--list", "v*"]).split()
        last = newest_stable_tag(tags)
        if last is None:
            raise SkipError("no stable release tag to compare with")
        changed = self.run(["git", "diff", "--name-only", f"{last}..{head}"]).splitlines()
        if not matters_to_users(changed):
            raise SkipError(
                f"nothing since {last} matters to users ({len(changed)} changed paths, all docs, specs, tests or CI)"
            )
        version = next_patch(last)
        if f"v{version}" in tags:
            raise SkipError(f"tag v{version} exists")
        return version, last

    def not_green(self, head: str) -> str | None:
        """Say why main's head is not green, or None when it is.

        Args:
            head: The head commit of main.

        Returns:
            The reason, or None.
        """
        runs = self.api(f"actions/runs?head_sha={head}&per_page=100")["workflow_runs"]
        others = [r for r in runs if r["path"].rsplit("/", 1)[-1] not in NOT_A_GATE]
        for r in others:
            if r["status"] in ACTIVE:
                return f"{r['name']} is still {r['status']} on {head[:10]}"
            if r["conclusion"] in FAILED:
                return f"{r['name']} {r['conclusion']} on {head[:10]}"
        passed = self.workflow_runs(DRY_RUN_WORKFLOW, f"&head_sha={head}")
        if not any(r["conclusion"] == SUCCESS for r in passed):
            return f"no successful Release dry run on {head[:10]}"
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate, and start the release unless ``--dry`` or a rule says no.

    Args:
        argv: Arguments (``--dry`` prints the command instead of running it).

    Returns:
        0 always, except a failed API call.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry", action="store_true", help="print the dispatch, do not start it")
    args = parser.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        sys.stderr.write("GITHUB_REPOSITORY is not set\n")
        return 1
    command, reason = ReleaseOnGreen(repo).decide(os.environ.get("RELEASE_ON_GREEN"))
    if command is None:
        sys.stdout.write(f"skip: {reason}\n")
        return 0
    sys.stdout.write(f"release: {reason}\n{' '.join(command)}\n")
    if not args.dry:
        run(command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
