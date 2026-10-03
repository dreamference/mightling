"""
One Night Shift task, from worktree to branch (specs/DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md §5.3).

The agent works in a git worktree of its own, on `night/<id>`, through `puffin exec` under a
transient systemd scope with a memory cap, so neither the user's checkout nor the model server is
at risk. The runner, not the agent, commits: the agent's sandbox cannot write the repository's
`.git`, and nothing is merged, pushed or rebased. The runner's own test run executes code the agent
wrote, so it goes through the same sandbox as the agent's commands (`puffin sandbox`), with a
policy the runner fixes: nothing the agent wrote runs with the user's full rights.
"""

import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Final, List, Optional, Tuple

from dreamference.config.dreamference_config import DreamferenceConfig, PUFFIN_AIRGAPPED_LEVELS
from dreamference.night_shift.night_shift_host import NIGHT_RUN_ENV
from dreamference.night_shift.night_shift_queue import NightShiftQueue
from dreamference.night_shift.night_shift_settings import NightShiftSettings

PREAMBLE: Final[str] = """This is a Night Shift task. It runs unattended overnight, in a git worktree of {repo} on branch {branch}, and nobody will answer questions: where something is unclear, make the reasonable choice and say which you made.

- Work only inside this worktree.
- Make the change itself; do not stop at describing or planning it.
- Do not commit, push, merge or switch branches. The night run commits your changes.
{test_line}- End by listing the files you changed.

Task:
{task}"""

NUDGE: Final[str] = "Go ahead and make the change now."
RESUME: Final[str] = "This task was cut off by the end of last night's window. Continue it where you left off and finish it."

# A last message that announces work instead of reporting it: the stall the live `/init` tests
# showed twice. Checked only when the worktree has no change.
ANNOUNCES_WORK: Final[re.Pattern] = re.compile(
    r"\b(I'll|I will|I'm going to|I am going to|Let me|Next,? I|I can now|I'm about to|I shall|"
    r"now I'll|I'll now|going to (?:make|add|update|implement|write|create|fix))\b", re.IGNORECASE)

KILL_GRACE_S: Final[int] = 30

# `/airgapped` (specs/DREAMFERENCE_PUFFIN_AIRGAPPED.md §3): the variable that overrides the
# configuration files, and the key those files carry. Mirrors puffin-rs/airgapped/src/lib.rs.
AIRGAPPED_ENV: Final[str] = "DREAMFERENCE_PUFFIN_AIRGAPPED"
AIRGAPPED_KEY: Final[str] = "puffin_airgapped"
SEALED: Final[str] = PUFFIN_AIRGAPPED_LEVELS[-1]
# The system prompt a new session starts with (specs/DREAMFERENCE_PUFFIN_PROMPT.md §7); `[night] prompt`.
PROMPT_ENV: Final[str] = "DREAMFERENCE_PUFFIN_PROMPT"
TEST_TAIL_LINES: Final[int] = 200


class NightShiftTaskRun:
    """Runs one task; `run()` returns its final status."""

    # The memory and CPU cap around each `puffin exec` and test run. Tests switch it off: nothing in
    # the suite may create a real systemd scope.
    USE_SCOPE: bool = True

    # Whether the test run goes through `puffin sandbox`. Tests switch it off unless they supply a
    # stand-in for `puffin`: nothing in the suite may run the installed binary or bubblewrap.
    USE_SANDBOX: bool = True

    def __init__(self, night_dir: Path, task: Dict[str, Any], settings: NightShiftSettings,
                 puffin_bin: str, deadline: float, model_host: Optional[str] = None,
                 context_budget: Optional[int] = None) -> None:
        """
        Args:
            night_dir: The queue directory.
            task: The task record as queued.
            settings: Night Shift settings.
            puffin_bin: The `puffin` executable.
            deadline: `time.time()` by which the task must stop (task timeout or window end).
            model_host: The model server the night run was admitted against. Named to every
                `puffin exec`, so the agent talks to that server and the launcher never browses
                the network for a node from a worktree (specs/DREAMFERENCE_PUFFIN_NODE.md §6.1).
            context_budget: The task's share of the KV pool (`NightShiftHost.task_budget`), passed to
                every `puffin exec` as its compaction limit. None falls back to `[night] compact_at`.
        """
        self.model_host = model_host
        self.context_budget: Optional[int] = context_budget if context_budget is not None \
            else (settings.compact_at or None)
        self.night_dir = night_dir
        self.task_id: str = task["id"]
        self.repo = Path(task["repo"])
        self.base: str = task["base"]
        self.branch: str = task.get("branch") or f"night/{self.task_id}"
        self.text: str = task["task"]
        self.test_override: Optional[str] = task.get("test")
        self.session: Optional[str] = task.get("session")
        # The `/airgapped` level this task runs at, fixed before its first command (`_fix_level`).
        self.airgapped: Optional[str] = None
        self._recorded_level: Optional[str] = task.get("airgapped")
        self.settings = settings
        self.puffin_bin = puffin_bin
        self.deadline = deadline
        self.worktree = night_dir / "worktrees" / self.task_id
        self.log_path = night_dir / "logs" / f"{self.task_id}.jsonl"
        self.last_message_path = night_dir / "logs" / f"{self.task_id}.last.txt"
        self.stop_event = threading.Event()
        self.in_model = False
        self.attempts: int = int(task.get("attempts") or 0)
        self.nudges_used = 0
        self.unit_counter = 0
        self.current_unit: Optional[str] = None
        self.started = time.time()

    @property
    def unit_prefix(self) -> str:
        """The prefix of this task's scope names."""
        return f"puffin-night-{self.task_id}"

    def run(self) -> str:
        """
        Runs the task through every step of §5.3, rereading its record between steps so a
        `/night drop` takes effect at the next one.

        Returns:
            str: The status written.
        """
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
        test_command, test_source = self.detect_test_command(
            self.worktree, self.test_override, self.settings.test, self.repo)
        if self._cancelled():
            return self._cleanup_and_finish("cancelled")
        self._fix_level()

        prompt = RESUME if self.session else self.compose_prompt(test_command)
        outcome = self._exec(prompt, resume=bool(self.session))
        if outcome == "interrupted":
            return self._interrupted()
        if outcome == "error" and not self._has_changes():
            return self._cleanup_and_finish(
                "failed", last_message=self._last_message() or "puffin exec exited with an error; see the log")

        while not self._has_changes() and self.nudges_used < self.settings.nudges \
                and self.announces_work(self._last_message()):
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

    def _prepare_worktree(self) -> Optional[str]:
        if self.worktree.is_dir():
            return None
        if self._git(self.repo, "cat-file", "-e", f"{self.base}^{{commit}}")[0] != 0:
            return f"the base commit {self.base[:10]} no longer exists in {self.repo}"
        self.worktree.parent.mkdir(parents=True, exist_ok=True)
        branch_exists = self._git(self.repo, "rev-parse", "--verify", "--quiet",
                                  f"refs/heads/{self.branch}")[0] == 0
        args = ["worktree", "add", "-q", str(self.worktree)]
        args += [self.branch] if branch_exists else ["-b", self.branch, self.base]
        code, output = self._git(self.repo, *args)
        return None if code == 0 else f"git worktree add failed: {output.strip()[-300:]}"

    def compose_prompt(self, test_command: Optional[str]) -> str:
        """
        Builds the first prompt: the fixed preamble, then the task verbatim.

        Args:
            test_command: The command that decides pass or fail, if any.

        Returns:
            str: The prompt.
        """
        test_line = (f"- When the change is made, run `{test_command}` and fix what it reports.\n"
                     if test_command else "")
        return PREAMBLE.format(repo=self.repo, branch=self.branch, test_line=test_line, task=self.text)

    def _exec(self, prompt: str, resume: bool) -> str:
        """Runs one `puffin exec` turn; returns `ok`, `error` or `interrupted`."""
        command = [self.puffin_bin, "exec", "--json", "-o", str(self.last_message_path),
                   "-C", str(self.worktree), "-s", "workspace-write", "--skip-git-repo-check"]
        if self.context_budget:
            # The task's share of the KV pool. On the command line it beats the launcher's own limit,
            # which is sized for one interactive session, not for several tasks sharing the pool.
            command += ["-c", f"model_auto_compact_token_limit={self.context_budget}"]
        command += ["resume", self.session, prompt] if resume and self.session else [prompt]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        offset = self.log_path.stat().st_size if self.log_path.exists() else 0
        self.in_model = True
        try:
            code = self._run_capped(command, self.worktree, self.log_path, timeout=None)
        finally:
            self.in_model = False
        session = self.thread_id_in(self.log_path, offset)
        if session and session != self.session:
            self.session = session
            NightShiftQueue.transition(self.night_dir, self.task_id, "running", session=session)
        if code == "timeout":
            return "interrupted"
        return "ok" if code == 0 else "error"

    REFUSED_UNSEALED: Final[str] = ("/airgapped is on and `[night] test_sandbox` is false, so nothing "
                                    "would keep the tests off the network")

    def _fix_level(self) -> str:
        """
        Fixes the task's `/airgapped` level, once, before the agent has run anything.

        Every later command of the task (each `puffin exec` and the test run) gets it in
        `DREAMFERENCE_PUFFIN_AIRGAPPED`, which outranks the configuration files: the agent can
        edit the worktree's `dreamference.toml`, and a level read again before the test run would
        be a level the agent could loosen for its own tests. A task resumed on a later night
        keeps at least the level its first night recorded, for the same reason.

        Returns:
            str: The level.
        """
        if self.airgapped is None:
            levels = [self.airgapped_level(self.worktree, self.session, self.night_dir.parent, self.repo),
                      DreamferenceConfig.parse_airgapped_level(getattr(self.settings, "airgapped", None)),
                      DreamferenceConfig.parse_airgapped_level(self._recorded_level)]
            self.airgapped = max((level for level in levels if level), key=PUFFIN_AIRGAPPED_LEVELS.index)
            if self.airgapped != self._recorded_level:
                NightShiftQueue.transition(self.night_dir, self.task_id, "running", airgapped=self.airgapped)
        return self.airgapped

    def _run_tests(self, test_command: str) -> Dict[str, Any]:
        output_path = self.night_dir / "logs" / f"{self.task_id}.test.log"
        level = self._fix_level()
        sandboxed = self.USE_SANDBOX and self.settings.test_sandbox
        if level == SEALED and not self.settings.test_sandbox:
            return {"command": test_command, "refused": True}
        command, extra_env = ["bash", "-c", test_command], {}
        if sandboxed:
            command, extra_env = self.sandboxed_test_command(self.puffin_bin, test_command, level, self.session)
        code = self._run_capped(command, self.worktree, output_path,
                                timeout=self.settings.test_timeout_s, append=False, extra_env=extra_env)
        lines = output_path.read_text(errors="replace").splitlines() if output_path.exists() else []
        result: Dict[str, Any] = {"command": test_command, "tail": "\n".join(lines[-TEST_TAIL_LINES:])}
        if sandboxed:
            result["sandbox"] = "workspace-write, no network (/airgapped on)" if level == SEALED \
                else "workspace-write"
        else:
            result["sandbox"] = "off ([night] test_sandbox = false)"
        if code == "timeout":
            window_over = time.time() >= self.deadline or self.stop_event.is_set()
            result["timed_out"] = "window" if window_over else "test_timeout"
            result["exit_code"] = None
        else:
            result["exit_code"] = code
        return result

    def _commit(self, test_command: Optional[str], test_source: str,
                test_result: Optional[Dict[str, Any]]) -> str:
        first_line = (self.text.strip().splitlines() or ["task"])[0][:72]
        code, output = self._git(self.worktree, "commit", "-q", "-m", f"night: {first_line}")
        if code != 0:
            return self._finish("failed", note=f"git commit failed: {output.strip()[-300:]}",
                                result=self._result(test_command, test_source, test_result))
        stat = self._git(self.repo, "diff", "--stat", f"{self.base}..{self.branch}")[1].rstrip()
        self._git(self.repo, "worktree", "remove", "--force", str(self.worktree))
        result = self._result(test_command, test_source, test_result)
        result["branch"] = self.branch
        result["diff_stat"] = stat
        return self._finish("done", result=result)

    def _interrupted(self) -> str:
        """Window or timeout: keep the worktree, branch and session for the next night."""
        result = {"branch": self.branch, "worktree": str(self.worktree),
                  "last_message": self._last_message(), **self._timing()}
        return self._finish("interrupted", result=result)

    def _cleanup_and_finish(self, status: str, last_message: Optional[str] = None) -> str:
        """No change to keep: remove the worktree and delete the branch."""
        if self.worktree.is_dir():
            self._git(self.repo, "worktree", "remove", "--force", str(self.worktree))
        self._git(self.repo, "branch", "-D", self.branch)
        result: Dict[str, Any] = self._timing()
        if last_message is not None and status in ("stalled", "failed"):
            result["last_message"] = last_message
        if status in ("stalled", "failed"):
            result["log"] = str(self.log_path)
        return self._finish(status, result=result)

    def _finish(self, status: str, note: Optional[str] = None,
                result: Optional[Dict[str, Any]] = None) -> str:
        result = dict(result or self._timing())
        if status == "failed" and note:
            result.setdefault("last_message", note)
        return NightShiftQueue.transition(self.night_dir, self.task_id, status, note=note,
                                          result=result, attempts=self.attempts)

    def _result(self, test_command: Optional[str], test_source: str,
                test_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = self._timing()
        result["test_command"] = test_command or "none"
        result["test_source"] = test_source
        if test_result:
            if test_result.get("timed_out"):
                result["test_result"] = f"timed out after {self.settings.test_timeout_s // 60} min"
            else:
                result["test_result"] = "passed" if test_result["exit_code"] == 0 \
                    else f"failed (exit {test_result['exit_code']})"
            result["test_tail"] = test_result["tail"]
            result["test_sandbox"] = test_result.get("sandbox", "")
        else:
            result["test_result"] = "untested"
        return result

    def _timing(self) -> Dict[str, Any]:
        return {"attempts": self.attempts, "nudges": self.nudges_used,
                "wall_s": int(time.time() - self.started)}

    # -- processes ---------------------------------------------------------------------------

    def _run_capped(self, command: List[str], cwd: Path, output: Path,
                    timeout: Optional[int], append: bool = True,
                    extra_env: Optional[Dict[str, str]] = None) -> Any:
        """
        Runs `command` under the task's scope until it exits, the deadline passes, `timeout`
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
        prefix: List[str] = []
        if self.USE_SCOPE:
            prefix = ["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}",
                      f"-p", f"MemoryMax={self.settings.task_memory}", "-p", "MemorySwapMax=0",
                      "-p", "CPUQuota=400%", "--", "choom", "-n", "500", "--"]
        env = dict(os.environ)
        env[NIGHT_RUN_ENV] = "1"
        if self.model_host:
            env["DREAMFERENCE_VLLM_HOST"] = self.model_host
        env["GIT_TERMINAL_PROMPT"] = "0"
        if self.airgapped:
            env[AIRGAPPED_ENV] = self.airgapped
        if getattr(self.settings, "prompt", None):
            env[PROMPT_ENV] = self.settings.prompt
        env.update(extra_env or {})
        limit = self.deadline if timeout is None else min(self.deadline, time.time() + timeout)
        with open(output, "ab" if append else "wb") as sink:
            process = subprocess.Popen(prefix + command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=sink, stderr=subprocess.STDOUT,
                                       start_new_session=True)
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
                subprocess.run(["systemctl", "--user", "kill", f"--signal={sig.name}", f"{unit}.scope"],
                               capture_output=True)
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=KILL_GRACE_S)
                return
            except subprocess.TimeoutExpired:
                continue

    def _git(self, cwd: Path, *args: str) -> Tuple[int, str]:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, env=env)
        return result.returncode, result.stdout + result.stderr

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
    def sandboxed_test_command(cls, puffin_bin: str, test_command: str, level: str,
                               session: Optional[str]) -> Tuple[List[str], Dict[str, str]]:
        """
        Wraps the test command in the sandbox the agent's own commands run in.

        The test command executes what the agent wrote, unattended, so it gets the agent's rights
        and no more: writes inside the worktree and `/tmp` only, the rest of the machine read-only.
        The policy is fixed here and not inherited from `~/.puffin/config.toml`: that file lists
        `~/.puffin/skills` as writable for the skill installer, and a test run that could write
        there could leave instructions behind for every later session. Only the `/airgapped`
        level comes from outside.

        Args:
            puffin_bin: The `puffin` executable.
            test_command: The shell command that decides pass or fail.
            level: The `/airgapped` level in force (`airgapped_level`).
            session: The task's session id, which the sandbox helper looks the level up by.

        Returns:
            Tuple[List[str], Dict[str, str]]: The command line, and variables to set for it.
        """
        sealed = level == SEALED
        command = [puffin_bin, "sandbox",
                   "-c", 'sandbox_mode="workspace-write"',
                   "-c", "sandbox_workspace_write.writable_roots=[]",
                   "-c", f"sandbox_workspace_write.network_access={'false' if sealed else 'true'}",
                   "--", "bash", "-c", test_command]
        # The sandbox helper (patch 0019) resolves the level again by itself, from the variable the
        # runner sets for every command of the task and from the session's own file if it has one.
        return command, ({"CODEX_THREAD_ID": session} if session else {})

    @classmethod
    def airgapped_level(cls, worktree: Path, session: Optional[str], codex_home: Path,
                        repo: Optional[Path] = None) -> str:
        """
        Resolves the `/airgapped` level for a command run in `worktree`, in the launcher's order
        (puffin-rs/airgapped): the session's file, the environment variable, then the strictest
        of the configuration files (a repository's file may tighten the user's level and never
        loosen it), then `off`.

        The main checkout's `dreamference.toml` is read beside the worktree's: it is usually
        untracked, so a worktree has no copy of it, and a level the user set there for this
        repository would otherwise not reach its night tasks.

        Args:
            worktree: The directory the command runs in.
            session: The task's session id, if it has one.
            codex_home: `$CODEX_HOME`, where `airgapped/<session>` lives.
            repo: The main checkout, if its file is to count too.

        Returns:
            str: `off`, `duckduckgo` or `on`.
        """
        tiers: List[Optional[str]] = []
        if session and re.fullmatch(r"[A-Za-z0-9-]+", session):
            try:
                tiers.append((codex_home / "airgapped" / session).read_text())
            except OSError:
                pass
        tiers.append(os.environ.get(AIRGAPPED_ENV))
        for value in tiers:
            level = DreamferenceConfig.parse_airgapped_level(value)
            if level:
                return level
        named = os.environ.get("DREAMFERENCE_CONFIG_PATH")
        files = [Path(named)] if named else [worktree / "dreamference.toml"] + (
            [repo / "dreamference.toml"] if repo else [])
        files.append(Path(os.path.expanduser("~/.config/dreamference/config.toml")))
        strictest = PUFFIN_AIRGAPPED_LEVELS[0]
        for path in files:
            level = DreamferenceConfig.parse_airgapped_level(cls._toml_top_level(path, AIRGAPPED_KEY))
            if level and PUFFIN_AIRGAPPED_LEVELS.index(level) > PUFFIN_AIRGAPPED_LEVELS.index(strictest):
                strictest = level
        return strictest

    @classmethod
    def _toml_top_level(cls, path: Path, key: str) -> Optional[str]:
        """The value of a top-level `key = "value"` line before the first table, as the helper reads it."""
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("["):
                return None
            if not line.startswith(key):
                continue
            rest = line[len(key):].lstrip()
            if rest.startswith("="):
                return rest[1:].split("#")[0].strip().strip("\"'")
        return None

    @classmethod
    def announces_work(cls, message: str) -> bool:
        """
        Tells whether a last message announces work rather than reporting it.

        Args:
            message: The agent's last message.

        Returns:
            bool: True for "I'll now…", "Next I will…" and the like.
        """
        return bool(message) and bool(ANNOUNCES_WORK.search(message))

    @classmethod
    def thread_id_in(cls, log_path: Path, offset: int) -> Optional[str]:
        """
        Finds the session id in the `--json` events written after `offset`.

        Args:
            log_path: The task's event log.
            offset: Where this attempt's events start.

        Returns:
            Optional[str]: The `thread.started` thread id, if one was written.
        """
        import json
        try:
            with open(log_path, "rb") as handle:
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
    def detect_test_command(cls, worktree: Path, override: Optional[str], configured: Optional[str],
                            repo: Path) -> Tuple[Optional[str], str]:
        """
        Picks the command that decides pass or fail (§5.4); the first match wins.

        Args:
            worktree: The task's worktree, at the task's base commit.
            override: The task's `--test`.
            configured: `[night] test` from the user's config.
            repo: The main checkout, whose `.venv` serves a worktree that has none.

        Returns:
            Tuple[Optional[str], str]: The command (None if nothing matched) and where it came from.
        """
        if override:
            return override, "--test"
        repo_table = NightShiftSettings.read_table(worktree / "dreamference.toml")
        if repo_table.get("test"):
            return str(repo_table["test"]), "dreamference.toml [night] test"
        if configured:
            return configured, "config [night] test"
        if any((worktree / name).is_file() for name in ("pyproject.toml", "pytest.ini", "setup.py")) \
                and (worktree / "tests").is_dir():
            for venv in (worktree / ".venv", repo / ".venv"):
                if (venv / "bin" / "python").is_file():
                    return f"{venv / 'bin' / 'python'} -m pytest -q", "pytest (repository .venv)"
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
