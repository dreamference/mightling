"""Night Shift fills its own queue (the owner's decision of 2026-10-28).

On a night that is not a benchmark night, and only where ``[night] autofill = true`` is set (the box
that runs Night Shift), up to five bounded tasks are drawn from three sources and queued through the
launcher's own ``ling night add``, so each is an ordinary task (best-of-N, the window and the report
treat it as any other, and the result is a branch ``night/<id>`` that Night Shift never merges):

1. failing CI tests: the latest failed push, scheduled or dispatched run on ``main`` (``gh``; never a
   fork's pull request, whose branch may also be called ``main``), only test ids that look like ids;
2. QA findings: the failing checks of the daily desktop QA report, and the open "Windows QA failing"
   issue the workflow opened (its number and title only, never its body: the repository is public);
3. open items in ``specs/``: unchecked ``- [ ]`` boxes and ``TODO:`` lines.

A finding is skipped when it is already queued or was queued within ``COOLDOWN_DAYS`` (the ledger
``night/autofill.json`` and the ``[autofill:<key>]`` marker line of the task), or when it touches a
security-sensitive path or word (``SENSITIVE``). Each task names the acceptance test, and tells the
agent to leave the sensitive paths alone. Nothing here merges, pushes or starts the model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Final

from dreamference.night_shift.autofill_candidate import AutofillCandidate
from dreamference.night_shift.night_shift_queue import NightShiftQueue
from dreamference.night_shift.night_shift_recurring import ADD_TIMEOUT_S, MAX_TASK_CHARS, QUEUED
from dreamference.night_shift.night_shift_settings import NightShiftSettings

#: The most tasks a night is given, whatever ``[night] autofill_max`` says.
HARD_MAX: Final[int] = 5

#: A finding queued or tried within this many days is not queued again.
COOLDOWN_DAYS: Final[int] = 14

#: Where the ledger of what was queued lives, under the night folder.
LEDGER: Final[str] = "autofill.json"

#: The marker line that starts every autofilled task; the key follows.
MARKER: Final[str] = "[autofill:"

#: What a finding may not touch: the owner's list (sandbox, policy.json, sudo/sshd, signing,
#: conftest, egress, codex-patches) with the words and paths that go with them. A match anywhere in a
#: candidate's key, title or detail makes the whole candidate skipped.
SENSITIVE: Final[re.Pattern[str]] = re.compile(
    r"sandbox|bwrap|bubblewrap|apparmor|policy\.json|sudo|sshd|authorized_keys|"
    r"\bsign(?:ing|ature|ed)?\b|release[-_ ]sign|\.pem\b|adapter[-_]sign|conftest|egress|"
    r"codex-patches|airgapped|\bsecrets?\b|credential|\bpasswords?\b",
    re.IGNORECASE,
)

#: The paths the agent is told to leave alone, whatever the finding says.
FORBIDDEN_PATHS: Final[tuple[str, ...]] = (
    "codex/",
    "codex-patches/",
    "tests/conftest.py",
    "ling-rs/web/policy.json",
    "ling-rs/airgapped/",
    ".github/",
    "sudoers or sshd configuration",
    "anything that signs a release or holds a key",
)

#: Test ids as pytest prints them; anything else from a CI log is dropped (no ``..`` segment).
TEST_ID: Final[re.Pattern[str]] = re.compile(r"^tests/(?!.*\.\.)[\w./-]+\.py::[\w.\[\]-]+$")

#: The events whose runs on ``main`` are the repository's own. A pull request from a fork whose
#: branch is named ``main`` also lists under ``--branch main``, with a workflow name and a log its
#: author wrote, so only these events are read.
TRUSTED_EVENTS: Final[frozenset[str]] = frozenset({"push", "schedule", "workflow_dispatch"})

#: Who opens the Windows QA issue (the workflow's ``GITHUB_TOKEN``); anyone can open an issue with
#: the same title on a public repository.
WINDOWS_ISSUE_AUTHOR: Final[str] = "app/github-actions"

#: A spec line shorter than this is not an item worth a task.
MIN_ITEM_CHARS: Final[int] = 12

#: The longest piece of a finding's detail kept, and of a spec line.
DETAIL_CHARS: Final[int] = 400

#: The full suite, the acceptance test of a finding no narrower test names.
FULL_SUITE: Final[str] = ".venv/bin/python -m pytest tests/ -q"

#: Where the desktop QA writes its report, under the user's state directory.
QA_DESKTOP_DIR: Final[Path] = Path.home() / ".local/state/dreamference/qa/desktop"

#: What names a benchmark holder of the runner lock, a QA workflow, a failed check and the open issue.
BENCHMARK_HOLDER: Final[str] = "swe-bench"
QA_WORKFLOW: Final[str] = "qa"
FAIL: Final[str] = "fail"
WINDOWS_ISSUE: Final[str] = "Windows QA failing"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class NightShiftAutofill:
    """Picks, filters and queues the night's self-made tasks."""

    #: How commands run, and where the desktop QA report is; tests replace both.
    runner: Runner = staticmethod(subprocess.run)
    qa_dir: Path = QA_DESKTOP_DIR

    @classmethod
    def benchmark_night(cls, night_dir: Path, run: Runner = subprocess.run) -> str | None:
        """Tells whether a benchmark holds this node, in which case nothing is filled.

        Args:
            night_dir: Night Shift's folder.
            run: How a command runs (tests pass a stand-in).

        Returns:
            Why the night is a benchmark night, or None.
        """
        holder = NightShiftQueue.runner_holder(night_dir)
        if holder and BENCHMARK_HOLDER in holder.lower():
            return holder
        try:
            done = run(
                [
                    "systemctl",
                    "--user",
                    "list-units",
                    "--state=active",
                    "--no-legend",
                    "--plain",
                    "ling-swe-*",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if (done.stdout or "").strip():
            return "a ling-swe-* unit is active"
        return None

    @classmethod
    def clean(cls, text: str, limit: int = DETAIL_CHARS) -> str:
        """One line without control characters, shortened.

        Args:
            text: Text from a report, a log or a spec.
            limit: The most characters kept.

        Returns:
            The cleaned text.
        """
        flat = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
        return re.sub(r"\s+", " ", flat).strip()[:limit]

    @classmethod
    def slug(cls, text: str) -> str:
        """A short stable id of a text, for keys.

        Args:
            text: The text.

        Returns:
            Lowercase words and a hash, so a reworded finding is a new one and the same one is not.
        """
        words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
        return f"{words}-{hashlib.sha256(text.encode()).hexdigest()[:8]}"

    @classmethod
    def is_sensitive(cls, candidate: AutofillCandidate) -> bool:
        """Whether a candidate touches a security-sensitive path or word.

        Args:
            candidate: The candidate.

        Returns:
            True when the key, the title or the detail matches ``SENSITIVE``.
        """
        return bool(SENSITIVE.search(f"{candidate.key}\n{candidate.title}\n{candidate.detail}"))

    @classmethod
    def ci_candidates(
        cls, run: Runner = subprocess.run, repo: Path | None = None
    ) -> list[AutofillCandidate]:
        """Failing tests of the latest failed CI run on main, through ``gh``.

        Args:
            run: How a command runs (tests pass a stand-in).
            repo: The repository ``gh`` asks about (the timer starts the run in the home folder).

        Returns:
            One candidate per distinct, well-formed test id (empty without ``gh`` or a failed run).
        """

        def gh(*args: str) -> str:
            try:
                done = run(
                    ["gh", *args], cwd=repo, capture_output=True, text=True, timeout=60, check=False
                )
            except (OSError, subprocess.SubprocessError):
                return ""
            return (done.stdout or "") if done.returncode == 0 else ""

        try:
            runs = json.loads(
                gh(
                    "run",
                    "list",
                    "--branch",
                    "main",
                    "--status",
                    "failure",
                    "--limit",
                    "10",
                    "--json",
                    "databaseId,workflowName,event",
                )
                or "[]"
            )
        except ValueError:
            return []
        for entry in runs:
            if (
                not isinstance(entry, dict)
                or entry.get("event") not in TRUSTED_EVENTS
                or QA_WORKFLOW in str(entry.get("workflowName", "")).lower()
            ):
                continue
            log = gh("run", "view", str(entry.get("databaseId")), "--log-failed")
            ids = dict.fromkeys(
                match for match in re.findall(r"FAILED (\S+)", log) if TEST_ID.match(match)
            )
            if not ids:
                continue
            return [
                AutofillCandidate(
                    key=f"ci-test:{test}",
                    source="ci",
                    title=f"failing CI test {test}",
                    detail=f"CI on main failed {test} (workflow {cls.clean(str(entry.get('workflowName')), 60)}).",
                    test=f".venv/bin/python -m pytest {test} -q",
                    acceptance=f"`.venv/bin/python -m pytest {test} -q` passes and the full suite still passes.",
                )
                for test in list(ids)[:HARD_MAX]
            ]
        return []

    @classmethod
    def qa_candidates(
        cls, qa_dir: Path = QA_DESKTOP_DIR, run: Runner = subprocess.run, repo: Path | None = None
    ) -> list[AutofillCandidate]:
        """Failing checks of the latest desktop QA report, and the open Windows QA issue.

        Args:
            qa_dir: The desktop QA report folder (``latest.json`` in it).
            run: How a command runs (tests pass a stand-in).
            repo: The repository ``gh`` asks about (the timer starts the run in the home folder).

        Returns:
            The candidates, desktop checks first.
        """
        found: list[AutofillCandidate] = []
        try:
            report = json.loads((qa_dir / "latest.json").read_text())
            checks = report.get("checks", []) if isinstance(report, dict) else []
        except (OSError, ValueError):
            checks = []
        for check in checks:
            if not isinstance(check, dict) or check.get("status") != FAIL:
                continue
            name = cls.clean(str(check.get("name", "")), 120)
            if not name:
                continue
            found.append(
                AutofillCandidate(
                    key=f"qa-desktop:{cls.slug(name)}",
                    source="qa",
                    title=f"desktop QA check failing: {name}",
                    detail=f"Check '{name}' of the daily desktop QA failed: {cls.clean(str(check.get('detail', '')))}",
                    test=FULL_SUITE,
                    acceptance=(
                        "a regression test for the finding exists and passes, and "
                        f"`{FULL_SUITE}` still passes."
                    ),
                )
            )
        try:
            done = run(
                [
                    "gh",
                    "issue",
                    "list",
                    "--state",
                    "open",
                    "--search",
                    f"{WINDOWS_ISSUE} in:title author:{WINDOWS_ISSUE_AUTHOR}",
                    "--json",
                    "number,title",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            issues = json.loads(done.stdout or "[]") if done.returncode == 0 else []
        except (OSError, subprocess.SubprocessError, ValueError):
            issues = []
        for issue in issues:
            if (
                isinstance(issue, dict)
                and isinstance(issue.get("number"), int)
                and str(issue.get("title", "")).strip() == WINDOWS_ISSUE
            ):
                number = issue["number"]
                found.append(
                    AutofillCandidate(
                        key=f"qa-windows:{number}",
                        source="qa",
                        title=f"Windows QA failing (issue {number})",
                        detail=(
                            f"The daily Windows QA is failing (open issue number {number}). Find which test "
                            "or step fails from the repository's Windows tests and workflows "
                            "(.github/workflows/windows-qa.yml is read-only for you), and fix "
                            "the code or test under dreamference/, tests/ or scripts/."
                        ),
                        test=FULL_SUITE,
                        acceptance=(
                            "a regression test for the finding exists and passes, and "
                            f"`{FULL_SUITE}` still passes."
                        ),
                    )
                )
        return found

    @classmethod
    def spec_candidates(cls, repo: Path) -> list[AutofillCandidate]:
        """Open items in ``specs/``: unchecked boxes and ``TODO:`` lines.

        Args:
            repo: The repository.

        Returns:
            One candidate per item, in file order.
        """
        found: list[AutofillCandidate] = []
        for path in sorted((repo / "specs").glob("*.md")):
            try:
                lines = path.read_text().splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                # A TODO opens its line: one named mid-line is prose about TODOs, not an item.
                found_item = re.match(r"\s*[-*] \[ \] (.+)", line) or re.match(
                    r"\s*(?:[-*]\s+)?TODO:\s*(.+)", line
                )
                item = cls.clean(found_item.group(1)) if found_item else ""
                if len(item) < MIN_ITEM_CHARS:
                    continue
                found.append(
                    AutofillCandidate(
                        key=f"spec:{path.name}:{cls.slug(item)}",
                        source="spec",
                        title=f"open item in {path.name}: {item[:80]}",
                        detail=(
                            f'specs/{path.name} line {number} has an open item: "{item}". Do the smallest '
                            "change that closes it, in the code the spec names, and update the spec line "
                            "(tick the box or drop the TODO) only if the work is complete."
                        ),
                        test=FULL_SUITE,
                        acceptance=(
                            "a test for the new behaviour exists and passes, and "
                            f"`{FULL_SUITE}` still passes."
                        ),
                    )
                )
        return found

    @classmethod
    def ledger(cls, night_dir: Path) -> dict[str, str]:
        """What was queued before: key to ISO date.

        Args:
            night_dir: Night Shift's folder.

        Returns:
            The ledger (empty when absent or unreadable).
        """
        try:
            data = json.loads((night_dir / LEDGER).read_text())
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    @classmethod
    def queued_keys(cls, night_dir: Path) -> set[str]:
        """Keys of autofilled tasks that are still waiting or running.

        Args:
            night_dir: Night Shift's folder.

        Returns:
            The keys, read from each task's marker line.
        """
        keys: set[str] = set()
        for task in NightShiftQueue.tasks(night_dir):
            if task.get("status") in ("done", "no-change", "failed", "stalled", "cancelled"):
                continue
            # The whole first line: a parametrized test's key holds `]` itself.
            match = re.match(r"\[autofill:(.+)\]$", str(task.get("task", "")).split("\n", 1)[0])
            if match:
                keys.add(match.group(1))
        return keys

    @classmethod
    def task_text(cls, candidate: AutofillCandidate) -> str:
        """The words of the queued task: marker, finding, limits and acceptance test.

        Args:
            candidate: The candidate.

        Returns:
            The task text, within what ``ling night add`` takes.
        """
        text = (
            f"{MARKER}{candidate.key}]\n"
            f"Night Shift's own task, from {candidate.source}: {candidate.detail}\n"
            "Keep the change small and bounded to this one finding; do not refactor beyond it.\n"
            f"Acceptance test: {candidate.acceptance}\n"
            "Do not modify: " + ", ".join(FORBIDDEN_PATHS) + ". If the fix needs one of them, stop "
            "and say so in your final message instead of changing it."
        )
        return text[:MAX_TASK_CHARS]

    @classmethod
    def pick(
        cls,
        night_dir: Path,
        candidates: list[AutofillCandidate],
        limit: int,
        today: date,
    ) -> tuple[list[AutofillCandidate], list[str]]:
        """Filters candidates and takes the first ``limit``.

        Args:
            night_dir: Night Shift's folder.
            candidates: In priority order (CI, QA, spec).
            limit: The most to take.
            today: The night's date.

        Returns:
            The chosen candidates and one line for each that was skipped for a reason.
        """
        ledger = cls.ledger(night_dir)
        waiting = cls.queued_keys(night_dir)
        chosen: list[AutofillCandidate] = []
        skipped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.key in seen:
                continue
            seen.add(candidate.key)
            if cls.is_sensitive(candidate):
                skipped.append(f"skipped (security-sensitive): {candidate.title}")
                continue
            if candidate.key in waiting:
                continue
            try:
                last = date.fromisoformat(ledger.get(candidate.key, ""))
            except ValueError:
                last = None
            if last is not None and today - last < timedelta(days=COOLDOWN_DAYS):
                continue
            if len(chosen) < limit:
                chosen.append(candidate)
        return chosen, skipped

    @classmethod
    def add_task(cls, repo: Path, mightling_bin: str, candidate: AutofillCandidate) -> str | None:
        """Queues one candidate with ``ling night add`` in the repository.

        Args:
            repo: The repository.
            mightling_bin: The ``ling`` executable.
            candidate: The candidate.

        Returns:
            None when it was queued, else the reason it was not.
        """
        args = [mightling_bin, "night", "add"]
        if candidate.test:
            args += ["--test", candidate.test]
        args.append(cls.task_text(candidate))
        try:
            done = cls.runner(
                args,
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=ADD_TIMEOUT_S,
                env={**os.environ},
            )
        except (OSError, subprocess.SubprocessError) as error:
            return cls.clean(str(error), 200)
        if done.returncode != 0 or not (done.stdout or "").lstrip().startswith(QUEUED):
            return cls.clean(done.stderr or done.stdout or f"exit {done.returncode}", 200)
        return None

    @classmethod
    def fill(
        cls,
        night_dir: Path,
        repo: Path,
        mightling_bin: str,
        today: date,
        maximum: int = HARD_MAX,
        dry_run: bool = False,
    ) -> list[str]:
        """Queues the night's tasks.

        ``cls.runner`` runs commands and ``cls.qa_dir`` is the QA report folder; tests replace them.

        Args:
            night_dir: Night Shift's folder.
            repo: The repository the tasks belong to (``ling night add`` runs there).
            mightling_bin: The ``ling`` executable.
            today: The night's date.
            maximum: The most tasks tonight (at most ``HARD_MAX``); tasks of an earlier fill that
                still wait count against it.
            dry_run: Say what would be queued, queue nothing and write no ledger.

        Returns:
            Lines for the run's output and the morning report.
        """
        why = cls.benchmark_night(night_dir, cls.runner)
        if why:
            return [f"Autofill: none tonight, a benchmark holds the node ({why})."]
        if not repo.is_dir():
            return [f"Autofill: none, {repo} is not there."]
        limit = max(0, min(int(maximum), HARD_MAX) - len(cls.queued_keys(night_dir)))
        if limit == 0:
            return ["Autofill: the queue already holds the night's autofilled tasks."]
        candidates = (
            cls.ci_candidates(cls.runner, repo)
            + cls.qa_candidates(cls.qa_dir, cls.runner, repo)
            + cls.spec_candidates(repo)
        )
        chosen, said = cls.pick(night_dir, candidates, limit, today)
        ledger = cls.ledger(night_dir)
        before = dict(ledger)
        for candidate in chosen:
            if dry_run:
                said.append(f"Autofill would queue [{candidate.source}] {candidate.title}")
                continue
            reason = cls.add_task(repo, mightling_bin, candidate)
            if reason:
                said.append(f"Autofill could not queue {candidate.title}: {reason}")
                continue
            ledger[candidate.key] = today.isoformat()
            said.append(f"Autofill queued [{candidate.source}] {candidate.title}")
        if not dry_run and ledger != before:
            night_dir.mkdir(parents=True, exist_ok=True)
            temporary = night_dir / f"{LEDGER}.tmp"
            temporary.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
            temporary.replace(night_dir / LEDGER)
        if not said:
            said.append("Autofill: no new findings tonight.")
        return said

    @classmethod
    def for_run(
        cls, night_dir: Path, settings: NightShiftSettings, mightling_bin: str, today: date
    ) -> list[str]:
        """What ``night run`` does at its start: fill when ``[night] autofill`` is on, never fail.

        Args:
            night_dir: Night Shift's folder.
            settings: Night Shift's settings.
            mightling_bin: The ``ling`` executable.
            today: The night's date.

        Returns:
            The lines for the report's notes (empty when autofill is off).
        """
        if not settings.autofill:
            return []
        try:
            return cls.fill(
                night_dir,
                cls.repo_for(settings.autofill_repo),
                mightling_bin,
                today,
                settings.autofill_max,
            )
        except Exception as error:  # noqa: BLE001 - a night must not fail on its own queueing
            return [f"Autofill failed: {error}"]

    @classmethod
    def repo_for(cls, configured: str | None) -> Path:
        """The repository autofilled tasks belong to.

        Args:
            configured: ``[night] autofill_repo``, or None.

        Returns:
            That path, else this checkout when it is one (a release install has no source).
        """
        if configured:
            return Path(configured).expanduser()
        from dreamference.runner.codex_branded_builder import REPO_ROOT

        return Path(REPO_ROOT)
