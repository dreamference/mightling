"""One Night Shift task, from worktree to branch (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §5.3).

The agent works in a git worktree of its own, on `night/<id>`, through `ling exec` under a
transient systemd scope with a memory cap, so neither the user's checkout nor the model server is
at risk. The runner, not the agent, commits: the agent's sandbox cannot write the repository's
`.git`, and nothing is merged, pushed or rebased. The runner's own test run executes code the agent
wrote, so it goes through the same sandbox as the agent's commands (`ling sandbox`), with a
policy the runner fixes: nothing the agent wrote runs with the user's full rights. So does the
commit, whose hooks are files the agent could write (`commit_in_sandbox`); the runner's own `git`
on the host runs no hook at all.

A task handed over by another node (`remote` in its record, specs/DREAMFERENCE_MIGHTLING_NODE.md
§13.5) runs every process inside bubblewrap as well: the node owner's home folder is an empty
tmpfs, the task's worktree and its own `CODEX_HOME` are the only writable places, and the network
namespace is the host's, because `ling exec` reaches this node's model server on loopback. The
agent's commands are confined by Codex's own sandbox inside that, as on any node.
"""

import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from dreamference.config.airgapped_resolver import AirgappedResolver
from dreamference.config.dreamference_config import MIGHTLING_AIRGAPPED_LEVELS, DreamferenceConfig
from dreamference.config.proxy_bypass import ProxyBypass
from dreamference.night_shift.night_shift_host import NIGHT_RUN_ENV
from dreamference.night_shift.night_shift_queue import NightShiftQueue
from dreamference.night_shift.night_shift_settings import NightShiftSettings
from dreamference.night_shift.refine_prompt import NIGHT_WRITES, RefinePrompt
from dreamference.runner.codex_installer import ADAPTER_ENV
from dreamference.runner.safe_git import SafeGit

PREAMBLE: Final[
    str
] = """This is a Night Shift task. It runs unattended overnight, in a git worktree of {repo} on branch {branch}, and nobody will answer questions: where something is unclear, make the reasonable choice and say which you made.

- Work only inside this worktree.
- Make the change itself; do not stop at describing or planning it.
- Do not commit, push, merge or switch branches. The night run commits your changes.
{test_line}- End by listing the files you changed.

Task:
{task}"""

NUDGE: Final[str] = "Go ahead and make the change now."
RESUME: Final[str] = (
    "This task was cut off by the end of last night's window. Continue it where you left off and finish it."
)

# A last message that announces work instead of reporting it: the stall the live `/init` tests
# showed twice. Checked only when the worktree has no change.
ANNOUNCES_WORK: Final[re.Pattern] = re.compile(
    r"\b(I'll|I will|I'm going to|I am going to|Let me|Next,? I|I can now|I'm about to|I shall|"
    r"now I'll|I'll now|going to (?:make|add|update|implement|write|create|fix))\b",
    re.IGNORECASE,
)

KILL_GRACE_S: Final[int] = 30

# `/airgapped` (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md §3): the variable that overrides the
# configuration files, as ling-rs/airgapped/src/lib.rs names it.
AIRGAPPED_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_AIRGAPPED"
SEALED: Final[str] = MIGHTLING_AIRGAPPED_LEVELS[-1]
# The system prompt a new session starts with (specs/DREAMFERENCE_MIGHTLING_PROMPT.md §7); `[night] prompt`.
PROMPT_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_PROMPT"
# Refine mode (specs/DREAMFERENCE_MIGHTLING_REFINE.md §5.3). The runner runs the study step itself, so
# every `ling exec` it starts is told to stay one session whatever the configuration says.
REFINE_ENV: Final[str] = "DREAMFERENCE_MIGHTLING_REFINE"
TEST_TAIL_LINES: Final[int] = 200
# The commit's own budget, its hooks included: it may start after the task's deadline has passed.
COMMIT_TIMEOUT_S: Final[int] = 300
# Every `git` the runner runs on the host: the hooks are the agent's to write (`.husky/_`, a
# relative `core.hooksPath`), and they run only inside the task's sandbox (`commit_in_sandbox`).
NO_HOOKS: Final[tuple[str, str]] = ("-c", "core.hooksPath=/dev/null")
# What `_run_capped` returns for a command it stopped, and the outcomes of `commit_in_sandbox`.
TIMED_OUT: Final[str] = "timeout"
DONE: Final[str] = "done"
INTERRUPTED: Final[str] = "interrupted"
# The answers `commit_in_sandbox` asks `git rev-parse` for: common dir, hooks, worktree config, format.
_PATHS_ASKED: Final[int] = 4
_OBJECT_ID: Final[re.Pattern] = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class NightShiftTaskRun:
    """Runs one task; `run()` returns its final status."""

    # The memory and CPU cap around each `ling exec` and test run. Tests switch it off: nothing in
    # the suite may create a real systemd scope.
    USE_SCOPE: bool = True

    # Whether the test run goes through `ling sandbox`. Tests switch it off unless they supply a
    # stand-in for `ling`: nothing in the suite may run the installed binary or bubblewrap.
    USE_SANDBOX: bool = True

    def __init__(
        self,
        night_dir: Path,
        task: dict[str, Any],
        settings: NightShiftSettings,
        mightling_bin: str,
        deadline: float,
        model_host: str | None = None,
        context_budget: int | None = None,
        model_node: str | None = None,
        window_end: float | None = None,
        attempt: int | None = None,
    ) -> None:
        """Args:
        night_dir: The queue directory.
        task: The task record as queued.
        settings: Night Shift settings.
        mightling_bin: The `ling` executable.
        deadline: `time.time()` by which the task must stop (task timeout or window end).
        model_host: The model server the night run was admitted against. Named to every
            `ling exec`, so the agent talks to that server and the launcher never browses
            the network for a node from a worktree (specs/DREAMFERENCE_MIGHTLING_NODE.md §6.1).
        context_budget: The task's share of the KV pool (`NightShiftHost.task_budget`), passed to
            every `ling exec` as its compaction limit. None falls back to `[night] compact_at`.
        model_node: The paired node whose model server `model_host` is, when it is not this
            machine's (a replica lane); recorded in the result.
        window_end: `time.time()` at which the night's window closes. With refine mode the
            step that does the task gets a full task timeout of its own after the study step,
            as in SWE-bench's `--refine` arm, but never past the window.
        attempt: The attempt's number when this run is one attempt of a best-of-N task
            (`NightShiftBestOfN`): its worktree, branch and logs carry `-a<k>`, it starts a
            session of its own, it never writes the task's record, and where a single run
            would commit it stages its change and leaves the worktree for the chooser
            (`outcome`).
        """
        self.model_host = model_host
        self.model_node = model_node
        # The model server the scheduler counts this task against (set by the runner).
        self.lane_host: str | None = None
        # A task another node handed over: sandboxed whole, with a CODEX_HOME of its own.
        self.remote: dict[str, Any] | None = (
            task.get("remote") if isinstance(task.get("remote"), dict) else None
        )
        self.author: dict[str, Any] | None = (
            task.get("author") if isinstance(task.get("author"), dict) else None
        )
        self.context_budget: int | None = (
            context_budget if context_budget is not None else (settings.compact_at or None)
        )
        self.night_dir = night_dir
        self.task_id: str = task["id"]
        self.repo = Path(task["repo"])
        self.base: str = task["base"]
        self.branch: str = task.get("branch") or f"night/{self.task_id}"
        self.text: str = task["task"]
        self.test_override: str | None = task.get("test")
        self.session: str | None = task.get("session")
        # The `/airgapped` level this task runs at, fixed before its first command (`_fix_level`).
        self.airgapped: str | None = None
        self._recorded_level: str | None = task.get("airgapped")
        self.settings = settings
        self.mightling_bin = mightling_bin
        self.deadline = deadline
        self.window_end = window_end
        # The study step's record, when refine mode ran one (`_refine`); kept in the result.
        self.refine_record: dict[str, Any] | None = None
        self.worktree = night_dir / "worktrees" / self.task_id
        self.log_path = night_dir / "logs" / f"{self.task_id}.jsonl"
        self.last_message_path = night_dir / "logs" / f"{self.task_id}.last.txt"
        self.test_log_path = night_dir / "logs" / f"{self.task_id}.test.log"
        # What the sandboxed commit and its hooks printed (`commit_in_sandbox`).
        self.commit_log_path = night_dir / "logs" / f"{self.task_id}.commit.log"
        if self.remote:
            # Written by `ling` from inside the sandbox, where only the task's own home is writable.
            from dreamference.night_shift.night_shift_remote import NightShiftRemote

            self.home = NightShiftRemote.task_home(self.task_id)
            self.last_message_path = self.home / "last-message.txt"
        self.stop_event = threading.Event()
        self.in_model = False
        self.attempts: int = int(task.get("attempts") or 0)
        self.nudges_used = 0
        self.unit_counter = 0
        self.current_unit: str | None = None
        self.started = time.time()
        # Best-of-N: this run is attempt `attempt` of the task; its end is `outcome`, not the record.
        self.attempt: int | None = attempt
        self.outcome: dict[str, Any] | None = None
        if attempt is not None:
            suffix = f"-a{attempt}"
            self.session = None
            self.branch = f"{self.branch}{suffix}"
            self.worktree = night_dir / "worktrees" / f"{self.task_id}{suffix}"
            self.log_path = night_dir / "logs" / f"{self.task_id}{suffix}.jsonl"
            self.last_message_path = night_dir / "logs" / f"{self.task_id}{suffix}.last.txt"
            self.diff_path: Path = night_dir / "logs" / f"{self.task_id}{suffix}.diff"
            # Each attempt's test output is its own, so the report can point at every one.
            self.test_log_path = night_dir / "logs" / f"{self.task_id}{suffix}.test.log"
            self.commit_log_path = night_dir / "logs" / f"{self.task_id}{suffix}.commit.log"

    @property
    def unit_prefix(self) -> str:
        """The prefix of this task's scope names."""
        return f"mightling-night-{self.task_id}"

    def run(self) -> str:
        """Runs the task through every step of §5.3, rereading its record between steps so a
        `/night drop` takes effect at the next one.

        Returns:
            str: The status written.
        """
        if self.attempt is not None:
            # One attempt of a best-of-N task: the coordinator has counted and marked the task.
            if self._cancelled():
                return self._finish("cancelled")
            try:
                return self._run()
            except Exception as error:  # A runner bug must not stop the other attempts.
                return self._finish("failed", note=f"night run error: {error}")
        if self._cancelled():
            return NightShiftQueue.transition(self.night_dir, self.task_id, "cancelled")
        self.attempts += 1
        NightShiftQueue.transition(self.night_dir, self.task_id, "running", attempts=self.attempts)
        try:
            return self._run()
        except Exception as error:  # A runner bug must not leave the task `running` forever.
            return self._finish("failed", note=f"night run error: {error}")

    def _run(self) -> str:
        problem = self._prepare_worktree()
        if problem:
            return self._finish("failed", note=problem)
        # `[night] test` is the node owner's, for the owner's repositories; a task from another
        # machine brings its own `--test` or the repository's own file.
        test_command, test_source = self.detect_test_command(
            self.worktree,
            self.test_override,
            None if self.remote else self.settings.test,
            self.repo,
            self.base,
        )
        if self._cancelled():
            return self._cleanup_and_finish("cancelled")
        self._fix_level()

        # Refine mode: a new task is studied first, by a session of its own whose worktree changes
        # are put back. A task resumed from an earlier night has its working session already.
        refined: str | None = None
        if not self.session and self.refine_enabled():
            refined = self._refine()
            if refined is None:
                return self._interrupted()
            if self._cancelled():
                return self._cleanup_and_finish("cancelled")

        if self.session:
            prompt = RESUME
        else:
            prompt = self.compose_prompt(test_command)
            if refined is not None:
                # The fix rules of the version the study step was given.
                prompt = RefinePrompt.compose_fix(
                    prompt, refined, version=(self.refine_record or {}).get("version", "v1")
                )
        outcome = self._exec(prompt, resume=bool(self.session))
        if outcome == "interrupted":
            return self._interrupted()
        if outcome == "error" and not self._has_changes():
            return self._cleanup_and_finish(
                "failed",
                last_message=self._last_message() or "ling exec exited with an error; see the log",
            )

        while (
            not self._has_changes()
            and self.nudges_used < self.settings.nudges
            and self.announces_work(self._last_message())
        ):
            if self._cancelled():
                return self._cleanup_and_finish("cancelled")
            self.nudges_used += 1
            if self._exec(NUDGE, resume=True) == "interrupted":
                return self._interrupted()

        if self._cancelled():
            return self._cleanup_and_finish("cancelled")
        if not self._has_changes():
            status = "stalled" if self.announces_work(self._last_message()) else "no-change"
            return self._cleanup_and_finish(status, last_message=self._last_message())

        # Staged before the tests run, so what the runner's test run leaves behind (`__pycache__`,
        # coverage files) in a repository that does not ignore it stays out of the commit. What the
        # agent's own commands left is staged with its changes: the runner cannot tell them apart.
        self._git(self.worktree, "add", "-A")
        test_result = self._run_tests(test_command) if test_command else None
        if test_result and test_result.get("refused"):
            test_result = None
            test_source = f"{test_source}; not run: {self.REFUSED_UNSEALED}"
        if self._cancelled():
            return self._cleanup_and_finish("cancelled")
        if test_result and test_result.get("timed_out") == "window":
            return self._interrupted()
        return self._commit(test_command, test_source, test_result)

    # -- steps -------------------------------------------------------------------------------

    def _prepare_worktree(self) -> str | None:
        if self.attempt is not None:
            # An attempt always starts from the base: what an earlier, broken run left is the runner's own.
            if self.worktree.is_dir():
                self._git(self.repo, "worktree", "remove", "--force", str(self.worktree))
            self._git(self.repo, "branch", "-D", self.branch)
        if self.worktree.is_dir():
            return None
        if self._git(self.repo, "cat-file", "-e", f"{self.base}^{{commit}}")[0] != 0:
            return f"the base commit {self.base[:10]} no longer exists in {self.repo}"
        self.worktree.parent.mkdir(parents=True, exist_ok=True)
        branch_exists = (
            self._git(self.repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}")[0]
            == 0
        )
        args = ["worktree", "add", "-q", str(self.worktree)]
        args += [self.branch] if branch_exists else ["-b", self.branch, self.base]
        code, output = self._git(self.repo, *args)
        return None if code == 0 else f"git worktree add failed: {output.strip()[-300:]}"

    def compose_prompt(self, test_command: str | None) -> str:
        """Builds the first prompt: the fixed preamble, then the task verbatim.

        Args:
            test_command: The command that decides pass or fail, if any.

        Returns:
            str: The prompt.
        """
        test_line = (
            f"- When the change is made, run `{test_command}` and fix what it reports.\n"
            if test_command
            else ""
        )
        return PREAMBLE.format(
            repo=self.repo, branch=self.branch, test_line=test_line, task=self.text
        )

    def refine_enabled(self) -> bool:
        """Whether this task is studied first (`[night] refine`, then `mightling_refine`)."""
        check = getattr(self.settings, "refine_enabled", None)
        return bool(check()) if callable(check) else False

    def refine_version(self) -> str:
        """Which refine texts this task gets (`[night] refine_version`, then `mightling_refine_version`)."""
        check = getattr(self.settings, "refine_version_in_force", None)
        return str(check()) if callable(check) else "v1"

    def _refine(self) -> str | None:
        """Refine mode's first step (specs/DREAMFERENCE_MIGHTLING_REFINE.md §5.3): a session that studies
        the task and answers with a refined description, under the task's own deadline and no
        limit of its own. Whatever it changed in the worktree is then put back (it is told not to
        change anything; whether it did is measured and recorded), and the step that does the
        task gets a fresh task timeout, within the window.

        Returns:
            Optional[str]: The description (empty when the step wrote none), or None when the
            window or a stop ended the step.
        """
        started = time.time()
        refined_path = (
            (self.home / "refined.txt")
            if self.remote
            else self.night_dir / "logs" / f"{self.task_id}.refined.txt"
        )
        refined_path.parent.mkdir(parents=True, exist_ok=True)
        refined_path.unlink(missing_ok=True)
        offset = self.log_path.stat().st_size if self.log_path.exists() else 0
        version = self.refine_version()
        outcome = self._exec(
            RefinePrompt.compose_study(self.text, NIGHT_WRITES, version=version),
            resume=False,
            last_message=refined_path,
            record_session=False,
        )
        record: dict[str, Any] = {
            "study_exit": outcome,
            "study_s": int(time.time() - started),
            "version": version,
        }
        session = self.thread_id_in(self.log_path, offset)
        if session:
            record["study_session"] = session
        self.refine_record = record
        if outcome == "interrupted":
            return None
        try:
            refined = refined_path.read_text(errors="replace").strip()
        except OSError:
            refined = ""
        record["refined_chars"] = len(refined)
        record["study_edited"] = self._has_changes()
        if record["study_edited"]:
            # The worktree is the runner's own, at the task's base commit: nothing in it is the user's.
            self._git(self.worktree, "reset", "-q", "--hard", "HEAD")
            self._git(self.worktree, "clean", "-fdq")
        if self.window_end is not None:
            self.deadline = min(self.window_end, time.time() + self.settings.task_timeout_s)
        return refined

    def _exec(
        self,
        prompt: str,
        resume: bool,
        last_message: Path | None = None,
        record_session: bool = True,
    ) -> str:
        """Runs one `ling exec` turn; returns `ok`, `error` or `interrupted`.

        Args:
            prompt: The prompt.
            resume: Whether the task's session is resumed.
            last_message: Where the final message goes; None for the task's own file.
            record_session: Whether a new session becomes the task's (refine mode's study step
                is a session of its own, never resumed).
        """
        command = [
            self.mightling_bin,
            "exec",
            "--json",
            "-o",
            str(last_message or self.last_message_path),
            "-C",
            str(self.worktree),
            "-s",
            "workspace-write",
            "--skip-git-repo-check",
        ]
        if self.context_budget:
            # The task's share of the KV pool. On the command line it beats the launcher's own limit,
            # which is sized for one interactive session, not for several tasks sharing the pool.
            command += ["-c", f"model_auto_compact_token_limit={self.context_budget}"]
        # The worktree only: the launcher's default extra root, `$CODEX_HOME/skills`, is the user's
        # own on a local task, and a skill planted there is read by every later session.
        command += ["-c", "sandbox_workspace_write.writable_roots=[]"]
        command += ["resume", self.session, prompt] if resume and self.session else [prompt]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        offset = self.log_path.stat().st_size if self.log_path.exists() else 0
        self.in_model = True
        try:
            code = self._run_capped(command, self.worktree, self.log_path, timeout=None)
        finally:
            self.in_model = False
        session = self.thread_id_in(self.log_path, offset)
        if record_session and session and session != self.session:
            self.session = session
            if self.attempt is None:
                NightShiftQueue.transition(self.night_dir, self.task_id, "running", session=session)
        if code == "timeout":
            return "interrupted"
        return "ok" if code == 0 else "error"

    REFUSED_UNSEALED: Final[str] = (
        "/airgapped is on and `[night] test_sandbox` is false, so nothing "
        "would keep the tests off the network"
    )

    def _fix_level(self) -> str:
        """Fixes the task's `/airgapped` level, once, before the agent has run anything.

        Every later command of the task (each `ling exec` and the test run) gets it in
        `DREAMFERENCE_MIGHTLING_AIRGAPPED`, which outranks the configuration files: the agent can
        edit the worktree's `dreamference.toml`, and a level read again before the test run would
        be a level the agent could loosen for its own tests. A task resumed on a later night
        keeps at least the level its first night recorded, for the same reason.

        Returns:
            str: The level.
        """
        if self.airgapped is None:
            levels = [
                self.airgapped_level(self.worktree, self.session, self.night_dir.parent, self.repo),
                DreamferenceConfig.parse_airgapped_level(getattr(self.settings, "airgapped", None)),
                DreamferenceConfig.parse_airgapped_level(self._recorded_level),
            ]
            self.airgapped = max(
                (level for level in levels if level), key=MIGHTLING_AIRGAPPED_LEVELS.index
            )
            if self.airgapped != self._recorded_level:
                NightShiftQueue.transition(
                    self.night_dir, self.task_id, "running", airgapped=self.airgapped
                )
        return self.airgapped

    def _run_tests(self, test_command: str) -> dict[str, Any]:
        output_path = self.test_log_path
        level = self._fix_level()
        sandboxed = self.USE_SANDBOX and self.settings.test_sandbox
        if level == SEALED and not self.settings.test_sandbox:
            return {"command": test_command, "refused": True}
        command, extra_env = ["bash", "-c", test_command], {}
        if sandboxed:
            command, extra_env = self.sandboxed_test_command(
                self.mightling_bin, test_command, level, self.session
            )
        code = self._run_capped(
            command,
            self.worktree,
            output_path,
            timeout=self.settings.test_timeout_s,
            append=False,
            extra_env=extra_env,
        )
        lines = output_path.read_text(errors="replace").splitlines() if output_path.exists() else []
        result: dict[str, Any] = {
            "command": test_command,
            "tail": "\n".join(lines[-TEST_TAIL_LINES:]),
        }
        if sandboxed:
            result["sandbox"] = (
                "workspace-write, no network (/airgapped on)"
                if level == SEALED
                else "workspace-write"
            )
        else:
            result["sandbox"] = "off ([night] test_sandbox = false)"
        if code == "timeout":
            window_over = time.time() >= self.deadline or self.stop_event.is_set()
            result["timed_out"] = "window" if window_over else "test_timeout"
            result["exit_code"] = None
        else:
            result["exit_code"] = code
        return result

    def _commit(
        self, test_command: str | None, test_source: str, test_result: dict[str, Any] | None
    ) -> str:
        if self.attempt is not None:
            # A candidate: staged, its diff kept for the report, the worktree left for the chooser.
            # Staged before the tests ran (`_run`), so what the test run left stays out, as in a commit.
            result = self._result(test_command, test_source, test_result)
            result.update(self._staged_change(stage=False))
            return self._finish("candidate", result=result)
        outcome, detail = self.commit_staged()
        if outcome == INTERRUPTED:
            return self._interrupted()
        if outcome != DONE:
            return self._finish(
                "failed",
                note=f"git commit failed: {detail.strip()[-300:]}",
                result=self._result(test_command, test_source, test_result),
            )
        stat = self._git(self.repo, "diff", "--stat", f"{self.base}..{self.branch}")[1].rstrip()
        self._git(self.repo, "worktree", "remove", "--force", str(self.worktree))
        result = self._result(test_command, test_source, test_result)
        result["branch"] = self.branch
        result["diff_stat"] = stat
        return self._finish("done", result=result)

    def commit_staged(self) -> tuple[str, str]:
        """Commits what is staged in the worktree on `self.branch` as `night: <the task's first line>`.

        Returns:
            tuple[str, str]: As `commit_in_sandbox`. A task from another machine commits on the
            host with no hook, as a node job does (`NodeJob._commit`): its hooks are a client's,
            in a repository the runner made.
        """
        first_line = (self.text.strip().splitlines() or ["task"])[0][:72]
        message = f"night: {first_line}"
        identity: list[str] = []
        if self.author:
            # A task from another machine is committed as its sender, in a job repository that has
            # no identity of its own.
            identity = [
                "-c",
                f"user.name={self.author.get('name')}",
                "-c",
                f"user.email={self.author.get('email')}",
            ]
        if not self.remote:
            return self.commit_in_sandbox(message, identity)
        code, output = self._git(self.worktree, *identity, "commit", "-q", "-m", message)
        return (DONE, "") if code == 0 else ("failed", output)

    def commit_in_sandbox(self, message: str, identity: Sequence[str] = ()) -> tuple[str, str]:
        """Commits what is staged in the worktree on `self.branch`, its hooks run in the task's sandbox.

        The hooks are files the agent could write (`.husky/_`, a relative `core.hooksPath`, a
        script a hook calls), so they run where the agent's own commands ran: `ling sandbox` with
        the task's `/airgapped` level, under its scope, with the worktree and one scratch folder
        writable. The repository's own `.git` is not writable there, so the commit is made in a
        scratch repository that borrows its objects (`objects/info/alternates`) and its config
        (`include.path`) and starts from the worktree's index and branch; inside the sandbox it is
        written into a bundle. The host then only reads: it copies the bundle without following a
        link, unbundles it with no hook, checks that its one ref is a single commit on top of the
        branch, and moves the branch.

        Args:
            message: The commit message.
            identity: `-c user.name=… -c user.email=…`, for a task committed as its sender.

        Returns:
            tuple[str, str]: ("done", the commit id), ("failed", why) or ("interrupted", "") when
            the runner was stopped during the commit.
        """
        head = self._git(self.repo, "rev-parse", "--verify", f"refs/heads/{self.branch}^{{commit}}")
        tree = self._git(self.worktree, "write-tree")
        found = SafeGit.run(
            [
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
                "--git-path",
                "hooks",
                "--git-path",
                "config.worktree",
                "--show-object-format",
            ],
            self.worktree,
        )
        lines = found.stdout.splitlines()
        if head[0] != 0 or tree[0] != 0 or found.returncode != 0 or len(lines) != _PATHS_ASKED:
            return "failed", (head[1] if head[0] else tree[1] if tree[0] else found.stderr)
        head_id, tree_id = head[1].strip(), tree[1].strip()
        common, hooks, worktree_config, object_format = lines
        scratch_parent = self.night_dir / "scratch"
        scratch_parent.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=f"{self.task_id}-", dir=scratch_parent))
        received = scratch_parent / f"{scratch.name}.bundle"
        repo, bundle = scratch / "repo", scratch / "commit.bundle"
        ref = f"refs/heads/{self.branch}"
        try:
            init = ["init", "-q", "--bare", "--template=", f"--object-format={object_format}"]
            result = SafeGit.run([*NO_HOOKS, *init, str(repo)])
            if result.returncode != 0:
                return "failed", result.stdout + result.stderr
            (repo / "objects" / "info").mkdir(parents=True, exist_ok=True)
            (repo / "objects" / "info" / "alternates").write_text(f"{common}/objects\n")
            for args in (
                ["--git-dir", str(repo), "config", "--add", "include.path", f"{common}/config"],
                *(
                    [["--git-dir", str(repo), "config", "--add", "include.path", worktree_config]]
                    if Path(worktree_config).is_file()
                    else []
                ),
                ["--git-dir", str(repo), "config", "core.bare", "false"],
                ["--git-dir", str(repo), "update-ref", ref, head_id],
                ["--git-dir", str(repo), "symbolic-ref", "HEAD", ref],
                ["--git-dir", str(repo), "--work-tree", str(self.worktree), "read-tree", tree_id],
            ):
                result = SafeGit.run([*NO_HOOKS, *args])
                if result.returncode != 0:
                    return "failed", result.stdout + result.stderr
            commit = [
                "--work-tree",
                str(self.worktree),
                "-c",
                f"core.hooksPath={self.worktree / hooks}",
                *identity,
                "commit",
                "-q",
                "-m",
                message,
            ]
            pack = ["bundle", "create", str(bundle), ref, f"^{head_id}"]
            # Both command lines are made before the hooks run: making one reads the scratch
            # repository's config (`SafeGit.driver_overrides`), which a hook can rewrite.
            level = self._fix_level()
            steps = [
                self.sandboxed_command(
                    self.mightling_bin,
                    SafeGit.command(args, git_dir=repo),
                    level,
                    self.session,
                    writable_roots=[str(scratch)],
                )
                for args in (commit, pack)
            ]
            # The commit has its own budget, past the task's deadline: the work is done by then.
            self.deadline = max(self.deadline, time.time() + COMMIT_TIMEOUT_S)
            append = False
            for command, extra_env in steps:
                code = self._run_capped(
                    command,
                    self.worktree,
                    self.commit_log_path,
                    timeout=COMMIT_TIMEOUT_S,
                    append=append,
                    extra_env=extra_env,
                )
                append = True
                timed_out = code == TIMED_OUT
                if timed_out and self.stop_event.is_set():
                    return INTERRUPTED, ""
                if code != 0:
                    tail = self.commit_log_path.read_text(errors="replace")[-600:]
                    return "failed", f"timed out after {COMMIT_TIMEOUT_S} s" if timed_out else tail
            return self._receive(bundle, received, ref, head_id)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
            received.unlink(missing_ok=True)

    def _receive(self, bundle: Path, received: Path, ref: str, head_id: str) -> tuple[str, str]:
        """Moves the branch to the commit in `bundle`, which the sandbox wrote and may be hostile.

        Args:
            bundle: The bundle in the scratch folder.
            received: Where the host keeps its own copy, outside every folder the sandbox wrote.
            ref: The branch's full ref name.
            head_id: The branch's commit before the commit.

        Returns:
            tuple[str, str]: ("done", the commit id) or ("failed", why).
        """
        try:
            source = os.open(bundle, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as error:
            return "failed", f"the commit left no bundle: {error}"
        try:
            if not stat.S_ISREG(os.fstat(source).st_mode):
                return "failed", "the commit's bundle is not a regular file"
            with os.fdopen(os.dup(source), "rb") as reader, received.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
        finally:
            os.close(source)
        unbundled = SafeGit.run([*NO_HOOKS, "bundle", "unbundle", str(received)], self.repo)
        output = unbundled.stdout
        tips = [line.split() for line in output.splitlines() if line.strip()]
        if (
            unbundled.returncode != 0
            or len(tips) != 1
            or tips[0][1:] != [ref]
            or not _OBJECT_ID.fullmatch(tips[0][0])
        ):
            why = (output + unbundled.stderr).strip()[-200:]
            return "failed", f"the commit's bundle does not hold one {ref}: {why}"
        tip = tips[0][0]
        parents = self._git(self.repo, "rev-list", "--parents", "-n", "1", tip)
        if parents[0] != 0 or parents[1].split() != [tip, head_id]:
            return "failed", f"the commit is not one commit on top of {head_id[:12]}"
        code, output = self._git(self.repo, "update-ref", ref, tip, head_id)
        return ("done", tip) if code == 0 else ("failed", output)

    def _staged_change(self, stage: bool = True) -> dict[str, Any]:
        """An attempt's change, staged in its worktree: the diff written to `diff_path`, and its stat."""
        if stage:
            self._git(self.worktree, "add", "-A")
        diff = self._git(self.worktree, "diff", "--cached", "--binary")[1]
        self.diff_path.parent.mkdir(parents=True, exist_ok=True)
        self.diff_path.write_text(diff)
        stat = self._git(self.worktree, "diff", "--cached", "--stat")[1].rstrip()
        return {
            "diff": str(self.diff_path),
            "diff_stat": stat,
            "worktree": str(self.worktree),
            "branch": self.branch,
            "session": self.session,
        }

    def window_closed(self) -> bool:
        """Whether the night's window (or a stop of the whole run) ended this run, not its own timeout."""
        return self.stop_event.is_set() or (
            self.window_end is not None and time.time() >= self.window_end
        )

    def _interrupted(self) -> str:
        """Window or timeout: keep the worktree, branch and session for the next night."""
        if self.attempt is not None:
            # An attempt is never resumed. Its own time limit makes what it changed a candidate that
            # did not finish (the filter drops it unless every attempt broke); the window's end
            # leaves it to the coordinator, which discards it.
            if not self.window_closed() and self._has_changes():
                result = {
                    "timed_out": True,
                    "test_result": "untested (the attempt's time ran out)",
                    "last_message": self._last_message(),
                    **self._timing(),
                }
                result.update(self._staged_change())
                return self._finish("candidate", result=result)
            status = "interrupted" if self.window_closed() else "timed-out"
            return self._finish(
                status, result={"last_message": self._last_message(), **self._timing()}
            )
        result = {
            "branch": self.branch,
            "worktree": str(self.worktree),
            "last_message": self._last_message(),
            **self._timing(),
        }
        return self._finish("interrupted", result=result)

    def _cleanup_and_finish(self, status: str, last_message: str | None = None) -> str:
        """No change to keep: remove the worktree and delete the branch."""
        if self.worktree.is_dir():
            self._git(self.repo, "worktree", "remove", "--force", str(self.worktree))
        self._git(self.repo, "branch", "-D", self.branch)
        result: dict[str, Any] = self._timing()
        if last_message is not None and status in ("stalled", "failed"):
            result["last_message"] = last_message
        if status in ("stalled", "failed"):
            result["log"] = str(self.log_path)
        return self._finish(status, result=result)

    def _finish(
        self, status: str, note: str | None = None, result: dict[str, Any] | None = None
    ) -> str:
        result = dict(result or self._timing())
        if status == "failed" and note:
            result.setdefault("last_message", note)
        if self.attempt is not None:
            self.outcome = {"attempt": self.attempt, "status": status, "result": result}
            return status
        return NightShiftQueue.transition(
            self.night_dir, self.task_id, status, note=note, result=result, attempts=self.attempts
        )

    def _result(
        self, test_command: str | None, test_source: str, test_result: dict[str, Any] | None
    ) -> dict[str, Any]:
        result: dict[str, Any] = self._timing()
        result["test_command"] = test_command or "none"
        result["test_source"] = test_source
        if test_result:
            if test_result.get("timed_out"):
                result["test_result"] = f"timed out after {self.settings.test_timeout_s // 60} min"
            else:
                result["test_result"] = (
                    "passed"
                    if test_result["exit_code"] == 0
                    else f"failed (exit {test_result['exit_code']})"
                )
            result["test_tail"] = test_result["tail"]
            result["test_sandbox"] = test_result.get("sandbox", "")
        else:
            result["test_result"] = "untested"
        return result

    def _timing(self) -> dict[str, Any]:
        timing: dict[str, Any] = {
            "attempts": self.attempts,
            "nudges": self.nudges_used,
            "wall_s": int(time.time() - self.started),
        }
        if self.model_node:
            timing["model_node"] = self.model_node
        if self.refine_record:
            timing["refine"] = dict(self.refine_record)
        return timing

    # -- processes ---------------------------------------------------------------------------

    def _run_capped(
        self,
        command: list[str],
        cwd: Path,
        output: Path,
        timeout: int | None,
        append: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> Any:
        """Runs `command` under the task's scope until it exits, the deadline passes, `timeout`
        seconds pass or the runner asks it to stop; then SIGTERM, and SIGKILL 30 s later.

        Args:
            command: The command line.
            cwd: Its working directory.
            output: The file its output goes to.
            timeout: Seconds it may take, beside the task's own deadline.
            append: Whether `output` is appended to or replaced.
            extra_env: Variables set for this command only.

        Returns:
            Any: The exit code, or "timeout".
        """
        self.unit_counter += 1
        unit = f"{self.unit_prefix}-{self.unit_counter}"
        prefix: list[str] = []
        if self.USE_SCOPE:
            prefix = [
                "systemd-run",
                "--user",
                "--scope",
                "--quiet",
                f"--unit={unit}",
                "-p",
                f"MemoryMax={self.settings.task_memory}",
                "-p",
                "MemorySwapMax=0",
                "-p",
                "CPUQuota=400%",
                "--",
                "choom",
                "-n",
                "500",
                "--",
            ]
        env = dict(os.environ)
        env[NIGHT_RUN_ENV] = "1"
        if self.model_host:
            env["DREAMFERENCE_VLLM_HOST"] = self.model_host
        # The base unless an arm names an adapter: never the user's `mightling_adapter` default.
        env[ADAPTER_ENV] = ""
        # The lane's model server may be another node's: it and loopback never go through a proxy.
        ProxyBypass.apply(env, self.model_host)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env[REFINE_ENV] = "off"
        if self.airgapped:
            env[AIRGAPPED_ENV] = self.airgapped
        if getattr(self.settings, "prompt", None):
            env[PROMPT_ENV] = self.settings.prompt
        env.update(extra_env or {})
        if self.remote:
            command = self.remote_sandbox(command, extra_env or {})
        limit = self.deadline if timeout is None else min(self.deadline, time.time() + timeout)
        with Path(output).open("ab" if append else "wb") as sink:
            process = subprocess.Popen(
                prefix + command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.current_unit = unit if self.USE_SCOPE else None
            try:
                while True:
                    try:
                        return process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        if time.time() >= limit or self.stop_event.is_set():
                            self._terminate(process, unit)
                            return "timeout"
            finally:
                self.current_unit = None

    def _terminate(self, process: subprocess.Popen, unit: str) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self.USE_SCOPE:
                subprocess.run(
                    ["systemctl", "--user", "kill", f"--signal={sig.name}", f"{unit}.scope"],
                    capture_output=True,
                )
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=KILL_GRACE_S)
                return
            except subprocess.TimeoutExpired:
                continue

    def _git(self, cwd: Path, *args: str) -> tuple[int, str]:
        # The worktree is the agent's to write: no config-named program runs (SafeGit), and no
        # hook, whichever folder `core.hooksPath` names (`NO_HOOKS`).
        result = SafeGit.run([*NO_HOOKS, *args], cwd)
        return result.returncode, result.stdout + result.stderr

    def remote_sandbox(self, command: list[str], extra_env: dict[str, str]) -> list[str]:
        """Wraps one process of a task from another machine in the job sandbox (§13.5).

        Args:
            command: The process's command line (`ling exec …` or the test run).
            extra_env: Variables set for this command only.

        Returns:
            List[str]: The bubblewrap command line.
        """
        from dreamference.node.node_job import NodeJob

        self.home.mkdir(parents=True, exist_ok=True)
        binaries = os.path.dirname(os.path.realpath(self.mightling_bin))
        variables = {
            "PATH": f"{binaries}:/usr/local/bin:/usr/bin:/bin",
            "CODEX_HOME": str(self.home),
            NIGHT_RUN_ENV: "1",
            "GIT_TERMINAL_PROMPT": "0",
            REFINE_ENV: "off",
            # Gmail is this node's owner's; a task from another machine is never told about it.
            "DREAMFERENCE_MIGHTLING_GMAIL": "false",
            # The task's own CODEX_HOME is not the owner's: its `ling` starts no file index over
            # the owner's folders (docs/dev/testing.md).
            "DREAMFERENCE_MIGHTLING_DOCS": "0",
        }
        if self.model_host:
            variables["DREAMFERENCE_VLLM_HOST"] = self.model_host
        variables[ADAPTER_ENV] = ""
        ProxyBypass.apply(variables, self.model_host)
        if self.airgapped:
            variables[AIRGAPPED_ENV] = self.airgapped
        variables.update(extra_env)
        # The host's network namespace: `ling` reaches this node's model server on loopback.
        argv = NodeJob.sandbox_command(
            self.worktree,
            command,
            True,
            self.task_id,
            writable=[str(self.home)],
            readable=[binaries],
            environment=variables,
        )
        return [
            os.path.realpath(self.mightling_bin) if word == self.mightling_bin else word
            for word in argv
        ]

    # -- reads -------------------------------------------------------------------------------

    def _has_changes(self) -> bool:
        code, output = self._git(self.worktree, "status", "--porcelain")
        return code == 0 and bool(output.strip())

    def _last_message(self) -> str:
        try:
            return self.last_message_path.read_text(errors="replace").strip()
        except OSError:
            return ""

    def _cancelled(self) -> bool:
        record = NightShiftQueue.read(self.night_dir, self.task_id) or {}
        return record.get("status") in ("cancel-requested", "cancelled")

    @classmethod
    def sandboxed_test_command(
        cls, mightling_bin: str, test_command: str, level: str, session: str | None
    ) -> tuple[list[str], dict[str, str]]:
        """The test command in the agent's sandbox (`sandboxed_command`).

        Args:
            mightling_bin: The `ling` executable.
            test_command: The shell command that decides pass or fail.
            level: The `/airgapped` level in force (`airgapped_level`).
            session: The task's session id, which the sandbox helper looks the level up by.

        Returns:
            Tuple[List[str], Dict[str, str]]: The command line, and variables to set for it.
        """
        return cls.sandboxed_command(mightling_bin, ["bash", "-c", test_command], level, session)

    @classmethod
    def sandboxed_command(
        cls,
        mightling_bin: str,
        command: list[str],
        level: str,
        session: str | None,
        writable_roots: Sequence[str] = (),
    ) -> tuple[list[str], dict[str, str]]:
        """Wraps a command in the sandbox the agent's own commands run in.

        The test run and the commit's hooks execute what the agent wrote, unattended, so they get
        the agent's rights and no more: writes inside the worktree, `/tmp` and `writable_roots`
        only, the rest of the machine read-only. The policy is fixed here and not inherited from
        `~/.mightling/config.toml`: that file lists `~/.mightling/skills` as writable for the
        skill installer, and a command that could write there could leave instructions behind for
        every later session. Only the `/airgapped` level comes from outside.

        Args:
            mightling_bin: The `ling` executable.
            command: What runs in the sandbox (the test run, the commit).
            level: The `/airgapped` level in force (`airgapped_level`).
            session: The task's session id, which the sandbox helper looks the level up by.
            writable_roots: Folders writable beside the working directory (the commit's scratch
                repository); none for the test run.

        Returns:
            Tuple[List[str], Dict[str, str]]: The command line, and variables to set for it.
        """
        sealed = level == SEALED
        roots = ", ".join(json.dumps(str(root)) for root in writable_roots)
        command = [
            mightling_bin,
            "sandbox",
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            f"sandbox_workspace_write.writable_roots=[{roots}]",
            "-c",
            f"sandbox_workspace_write.network_access={'false' if sealed else 'true'}",
            "--",
            *command,
        ]
        # The sandbox helper (patch 0019) resolves the level again by itself, from the variable the
        # runner sets for every command of the task and from the session's own file if it has one.
        return command, ({"CODEX_THREAD_ID": session} if session else {})

    @classmethod
    def airgapped_level(
        cls, worktree: Path, session: str | None, codex_home: Path, repo: Path | None = None
    ) -> str:
        """Resolves the `/airgapped` level for a command run in `worktree`, as the one reader does.

        That is `ling airgapped --json` (specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md §6.5): the
        session's file, the environment variable, then the strictest of the configuration files
        (a repository's file may tighten the user's level and never loosen it), then `off`; `on`
        whenever `ling` cannot answer.

        The main checkout's `dreamference.toml` is read beside the worktree's: it is usually
        untracked, so a worktree has no copy of it, and a level the user set there for this
        repository would otherwise not reach its night tasks.

        Args:
            worktree: The directory the command runs in.
            session: The task's session id, if it has one.
            codex_home: `$CODEX_HOME`, where `airgapped/<session>` lives.
            repo: The main checkout, if its file is to count too.

        Returns:
            str: `off` or `on`.
        """
        environment = dict(os.environ, CODEX_HOME=str(codex_home))
        levels = [
            AirgappedResolver.level(cwd=folder, thread=session, environment=environment)
            for folder in [worktree] + ([repo] if repo and repo != worktree else [])
        ]
        return SEALED if SEALED in levels else levels[0]

    @classmethod
    def announces_work(cls, message: str) -> bool:
        """Tells whether a last message announces work rather than reporting it.

        Args:
            message: The agent's last message.

        Returns:
            bool: True for "I'll now…", "Next I will…" and the like.
        """
        return bool(message) and bool(ANNOUNCES_WORK.search(message))

    @classmethod
    def thread_id_in(cls, log_path: Path, offset: int) -> str | None:
        """Finds the session id in the `--json` events written after `offset`.

        Args:
            log_path: The task's event log.
            offset: Where this attempt's events start.

        Returns:
            Optional[str]: The `thread.started` thread id, if one was written.
        """
        import json

        try:
            with Path(log_path).open("rb") as handle:
                handle.seek(offset)
                for raw in handle:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("type") == "thread.started":
                        return event.get("thread_id")
        except OSError:
            return None
        return None

    @classmethod
    def detect_test_command(
        cls,
        worktree: Path,
        override: str | None,
        configured: str | None,
        repo: Path,
        base: str | None = None,
    ) -> tuple[str | None, str]:
        """Picks the command that decides pass or fail (§5.4); the first match wins.

        Args:
            worktree: The task's worktree, at the task's base commit.
            override: The task's `--test`.
            configured: `[night] test` from the user's config.
            repo: The main checkout, whose `.venv` serves a worktree that has none.
            base: The task's base commit; `dreamference.toml` is read from it, since a resumed
                task's worktree holds the agent's edits.

        Returns:
            Tuple[Optional[str], str]: The command (None if nothing matched) and where it came from.
        """
        if override:
            return override, "--test"
        repo_table = (
            cls._base_table(worktree, base)
            if base
            else NightShiftSettings.read_table(worktree / "dreamference.toml")
        )
        if repo_table.get("test"):
            return str(repo_table["test"]), "dreamference.toml [night] test"
        if configured:
            return configured, "config [night] test"
        if (
            any(
                (worktree / name).is_file() for name in ("pyproject.toml", "pytest.ini", "setup.py")
            )
            and (worktree / "tests").is_dir()
        ):
            for venv in (worktree / ".venv", repo / ".venv"):
                if (venv / "bin" / "python").is_file():
                    return (
                        f"{shlex.quote(str(venv / 'bin' / 'python'))} -m pytest -q",
                        "pytest (repository .venv)",
                    )
            return "python3 -m pytest -q", "pytest"
        if (worktree / "Cargo.toml").is_file():
            return "cargo test", "Cargo.toml"
        package = worktree / "package.json"
        if package.is_file():
            import json

            try:
                scripts = json.loads(package.read_text()).get("scripts", {})
            except (OSError, ValueError, AttributeError):
                scripts = {}
            if isinstance(scripts, dict) and scripts.get("test"):
                return "npm test", "package.json"
        return None, "none found"

    @classmethod
    def _base_table(cls, worktree: Path, base: str) -> dict[str, Any]:
        """Reads `[night]` from `dreamference.toml` as committed at the base (empty if absent)."""
        shown = SafeGit.run(["show", f"{base}:dreamference.toml"], worktree)
        try:
            table = tomllib.loads(shown.stdout).get("night", {}) if shown.returncode == 0 else {}
        except tomllib.TOMLDecodeError:
            return {}
        return table if isinstance(table, dict) else {}
