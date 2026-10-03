"""
One SWE-bench instance, from container to prediction
(specs/DREAMFERENCE_PUFFIN_SWE_BENCH.md §5.1, §12).

The agent runs inside the instance's own image, where the repository sits at `/testbed` with its
pinned environment, in a container on an internal network that reaches the model server and
nothing else. The container is the sandbox: Codex's own cannot start inside one. The runner, not
the agent, collects the patch.
"""

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

from dreamference.night_shift.night_shift_host import NIGHT_RUN_ENV
from dreamference.night_shift.night_shift_task_run import NUDGE, NightShiftTaskRun
from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_docker import SweBenchDocker
from dreamference.swe_bench.swe_bench_run_store import SweBenchRunStore
from dreamference.swe_bench.swe_bench_runtime import CONTAINER_MOUNT

# The whole prompt: the issue and nothing else from the dataset row (§5.4).
PROMPT: Final[str] = """This is an unattended task in the repository at /testbed. Nobody will answer questions:
where something is unclear, make the reasonable choice.

- Fix the issue below by changing the repository's source files.
- You may run the repository's tests. There is no network.
- Do not commit. Your changes are collected when you stop.

Issue:
{problem_statement}"""

# The image's default PATH puts conda's *base* environment first, which has none of the
# repository's dependencies; `conda activate testbed` only happens in the grading script. Without
# this the agent's `python -m pytest` fails on imports and it spends its budget on a non-problem.
CONTAINER_PATH: Final[str] = ("/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:"
                              "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

# Where the instance's scratch directory (CODEX_HOME, HOME, the collected patch) is mounted.
SCRATCH_MOUNT: Final[str] = "/puffin-scratch"

# The three scripts below run in the container as the agent's user. `TESTBED` is only ever set by
# the tests, which run the same scripts against a scratch repository on the host.

# Removes what gives the answer away. An image may keep the upstream repository's later history:
# every ref but the checked-out branch goes, and `git gc --prune=now` drops the objects only they
# reached, the fixing commit among them. Run as root, because the image's `.git` belongs to root
# and a repack as the agent's user cannot replace its files; `.git` is then opened up again so
# the agent's own git commands keep working.
SCRUB_SCRIPT: Final[str] = r"""
set -u
cd "${TESTBED:-/testbed}" || exit 3
branch=$(git symbolic-ref -q HEAD || true)
git for-each-ref --format='%(refname)' | while read -r ref; do
    [ "$ref" = "$branch" ] || git update-ref -d "$ref"
done
for remote in $(git remote); do git remote remove "$remote"; done
rm -rf .git/ORIG_HEAD .git/FETCH_HEAD .git/refs/remotes .git/logs/refs/remotes
git reflog expire --expire=now --all
git gc --prune=now --quiet || echo "note: git gc failed"
chmod -R a+rwX .git
echo "refs: $(git for-each-ref | wc -l)"
"""

# Records the tree the agent starts from: the checked-out commit plus whatever the image build
# left untracked, so the collected patch holds the agent's changes and nothing else.
PREPARE_SCRIPT: Final[str] = r"""
set -u
cd "${TESTBED:-/testbed}" || exit 3
git rev-parse -q --verify "$BASE_COMMIT^{commit}" >/dev/null 2>&1 || echo "note: base commit not in the object store"
if git diff --quiet "$BASE_COMMIT" HEAD 2>/dev/null; then echo "tree: equal"; else echo "tree: differs"; fi
git add -A >/dev/null 2>&1 || exit 4
git write-tree > "$SCRATCH/base-tree" || exit 5
# The index goes back to HEAD without `git reset`, which would write a reflog entry.
git read-tree HEAD
"""

# Says whether the working tree differs from the tree the agent started from.
CHANGED_SCRIPT: Final[str] = r"""
cd "${TESTBED:-/testbed}" || exit 3
git add -A >/dev/null 2>&1
tree=$(git write-tree)
git read-tree HEAD
[ "$tree" != "$(cat "$SCRATCH/base-tree")" ]
"""

# Writes the agent's changes as a patch against the tree it started from. Binary files are left
# out and named: a "Binary files differ" stub makes `git apply` reject the whole patch.
COLLECT_SCRIPT: Final[str] = r"""
cd "${TESTBED:-/testbed}" || exit 3
base=$(cat "$SCRATCH/base-tree")
git cat-file -e "$base^{tree}" 2>/dev/null || base=HEAD
git add -A >/dev/null 2>&1
git -c core.quotePath=false diff --cached --numstat "$base" | awk -F'\t' '$1 == "-" && $2 == "-" {print $3}' > "$SCRATCH/binary-files"
set -- .
while IFS= read -r file; do set -- "$@" ":(exclude,literal)$file"; done < "$SCRATCH/binary-files"
git -c core.fileMode=false diff --cached --no-color --no-ext-diff "$base" -- "$@" > "$SCRATCH/patch.diff"
"""

STOP_GRACE_S: Final[int] = 30
LAST_MESSAGE_LIMIT: Final[int] = 2000


class SweBenchInstanceRun:
    """Runs the agent on one instance; `run()` returns the status written."""

    def __init__(self, store: SweBenchRunStore, row: Dict[str, Any], image: str, model_name: str,
                 settings: "swe_bench_settings.SweBenchSettings", runtime_dir: Path, model_url: str,
                 deadline: float, extra_env: Optional[Dict[str, str]] = None,
                 code_index: Optional[Dict[str, Any]] = None,
                 extra_mounts: Optional[List[str]] = None) -> None:
        """
        Args:
            store: The run's files.
            row: The instance's dataset row.
            image: The instance image for this machine.
            model_name: The run's `model_name_or_path`.
            settings: Benchmark settings.
            runtime_dir: The relocated `puffin` runtime on the host.
            model_url: The model server as the container reaches it (the network's gateway).
            deadline: `time.time()` by which the instance must stop (its timeout, or the run's
                `--until`).
            extra_env: More environment for the container (the cave-mode and air-gap levels).
            code_index: How the container is given a code index (`mounts`, `env`, `path`, and
                the index's `record`); None for the arm without one.
            extra_mounts: More `docker run -v` values (a custom prompt's file, read-only).
        """
        self.store = store
        self.instance_id: str = row["instance_id"]
        self.base_commit: str = row["base_commit"]
        self.problem_statement: str = row["problem_statement"]
        self.image = image
        self.model_name = model_name
        self.settings = settings
        self.runtime_dir = runtime_dir
        self.model_url = model_url
        self.deadline = deadline
        self.extra_env = dict(extra_env or {})
        self.code_index = code_index
        self.extra_mounts: List[str] = list(extra_mounts or [])
        self.container: str = self.container_name(store.name, self.instance_id)
        self.scratch: Path = store.directory / "scratch" / self.instance_id
        self.log_path: Path = store.log_path(self.instance_id)
        self.user: str = f"{os.getuid()}:{os.getgid()}"
        self.session: Optional[str] = None
        self.nudges_used = 0
        self.started = time.time()
        self.notes: List[str] = []
        # What Night Shift's start check reads from a running task: whether it is waiting on the
        # model, and its systemd scope. A container has no scope, so the check assumes it may
        # still grow to its full memory cap, which is the conservative reading.
        self.in_model = False
        self.current_unit: Optional[str] = None
        self.stop_event = threading.Event()

    @classmethod
    def container_name(cls, run: str, instance_id: str) -> str:
        """
        Args:
            run: The run's name.
            instance_id: The instance.

        Returns:
            str: The agent container's name.
        """
        return "puffin-swe-" + re.sub(r"[^a-zA-Z0-9_.-]", "-", f"{run}-{instance_id}".lower())

    @classmethod
    def compose_prompt(cls, problem_statement: str) -> str:
        """
        Builds the prompt: the fixed preamble and the issue text, verbatim.

        Args:
            problem_statement: The dataset row's `problem_statement`.

        Returns:
            str: The prompt.
        """
        return PROMPT.format(problem_statement=problem_statement)

    def run(self) -> str:
        """
        Runs the instance through every step of §5.1 and writes its prediction and state.

        Returns:
            str: The status written: `done`, `empty`, `stalled`, `timeout` or `error`; or
            `interrupted` when the run itself was stopped, in which case no prediction is
            written and the instance runs again when the run is resumed.
        """
        state: Dict[str, Any] = {"instance_id": self.instance_id, "image": self.image, "status": "running",
                                 "started": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.started))}
        if self.code_index:
            # Built before the agent started and outside its time limit; recorded beside it.
            record = self.code_index.get("record", {})
            state["index"] = {key: record.get(key) for key in ("layers", "seconds", "cached", "bytes")}
        self.store.write_state(self.instance_id, state)
        patch = ""
        try:
            status, patch = self._run(state)
        except Exception as error:  # A runner bug must not leave the instance `running` for ever.
            status = "error"
            self.notes.append(f"runner error: {error}")
        finally:
            SweBenchDocker.run(["rm", "-f", self.container], timeout=120)
        if self.stop_event.is_set():
            status = "interrupted"
        state.update(status=status, session=self.session, nudges=self.nudges_used,
                     wall_s=int(time.time() - self.started), patch_bytes=len(patch.encode()),
                     last_message=self._last_message()[:LAST_MESSAGE_LIMIT], notes=self.notes)
        if status != "interrupted":
            self.store.append_prediction(self.instance_id, self.model_name, patch)
        self.store.write_state(self.instance_id, state)
        return status

    def _run(self, state: Dict[str, Any]) -> tuple:
        problem = self._start_container()
        if problem:
            self.notes.append(problem)
            return "error", ""
        scrubbed = self._script(SCRUB_SCRIPT, timeout=900, root=True)
        prepared = self._script(PREPARE_SCRIPT, timeout=300)
        state["base_tree_equal"] = "tree: equal" in prepared.stdout
        self.notes += [line for line in (scrubbed.stdout + prepared.stdout).splitlines()
                       if line.startswith("note:")]
        if scrubbed.returncode != 0:
            self.notes.append(f"scrubbing /testbed failed ({scrubbed.returncode}): "
                              f"{(scrubbed.stderr or scrubbed.stdout).strip()[-300:]}")
            return "error", ""
        if prepared.returncode != 0:
            self.notes.append(f"preparing /testbed failed ({prepared.returncode}): "
                              f"{(prepared.stderr or prepared.stdout).strip()[-300:]}")
            return "error", ""

        outcome = self._exec(self.compose_prompt(self.problem_statement), resume=False)
        while outcome == "ok" and self.nudges_used < self.settings.nudges and not self._changed() \
                and NightShiftTaskRun.announces_work(self._last_message()):
            self.nudges_used += 1
            outcome = self._exec(NUDGE, resume=True)
        state["exec"] = outcome

        if self.stop_event.is_set():
            return "interrupted", ""
        if outcome == "timeout":
            # The container was stopped to end the agent; its filesystem is still there.
            SweBenchDocker.run(["start", self.container], timeout=120)
        collected = self._script(COLLECT_SCRIPT, timeout=600)
        patch = self._read(self.scratch / "patch.diff")
        state["binary_files"] = self._read(self.scratch / "binary-files").split("\n")[:-1]
        if collected.returncode != 0:
            self.notes.append(f"collecting the patch failed: {collected.stderr.strip()[-300:]}")
        if outcome == "timeout":
            # A partial patch is still submitted; the status says the agent did not finish.
            return "timeout", patch
        if patch.strip():
            return "done", patch
        if outcome == "error":
            return "error", ""
        return ("stalled" if NightShiftTaskRun.announces_work(self._last_message()) else "empty"), ""

    # -- steps ---------------------------------------------------------------------------------

    def _start_container(self) -> Optional[str]:
        shutil.rmtree(self.scratch, ignore_errors=True)
        for sub in ("codex-home", "home"):
            (self.scratch / sub).mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        SweBenchDocker.run(["rm", "-f", self.container], timeout=120)
        environment = {
            "PATH": CONTAINER_PATH,
            "HOME": f"{SCRATCH_MOUNT}/home",
            "CODEX_HOME": f"{SCRATCH_MOUNT}/codex-home",
            "SCRATCH": SCRATCH_MOUNT,
            "BASE_COMMIT": self.base_commit,
            "DREAMFERENCE_VLLM_HOST": self.model_url,
            NIGHT_RUN_ENV: "1",
            "GIT_TERMINAL_PROMPT": "0",
            # /testbed belongs to root in the image and the agent runs as the host's user.
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "/testbed",
            **self.extra_env,
        }
        mounts: List[str] = list(self.extra_mounts)
        if self.code_index:
            environment.update(self.code_index["env"])
            environment["PATH"] = f"{self.code_index['path']}:{CONTAINER_PATH}"
            mounts += list(self.code_index["mounts"])
        command = ["run", "-d", "--init", "--name", self.container,
                   "--label", f"puffin.swe-bench.run={self.store.name}",
                   "--network", swe_bench_settings.NETWORK_NAME,
                   "--user", self.user,
                   "--memory", self.settings.task_memory, "--memory-swap", self.settings.task_memory,
                   "--cpus", str(self.settings.task_cpus), "--pids-limit", "4096",
                   "-v", f"{self.runtime_dir}:{CONTAINER_MOUNT}:ro",
                   "-v", f"{self.scratch}:{SCRATCH_MOUNT}",
                   "-w", "/testbed"]
        for mount in mounts:
            command += ["-v", mount]
        for key, value in environment.items():
            command += ["-e", f"{key}={value}"]
        command += [self.image, "sleep", "infinity"]
        started = SweBenchDocker.run(command, timeout=600)
        if started.returncode != 0:
            return f"docker run failed: {started.stderr.strip()[-300:]}"
        return None

    def _exec(self, prompt: str, resume: bool) -> str:
        """Runs one `puffin exec` turn in the container; returns `ok`, `error` or `timeout`."""
        command = ["exec", self.container, f"{CONTAINER_MOUNT}/bin/puffin", "exec", "--json",
                   "-o", f"{SCRATCH_MOUNT}/last.txt", "--dangerously-bypass-approvals-and-sandbox",
                   "-C", "/testbed", "-c", f"model_auto_compact_token_limit={self.settings.task_context}"]
        command += ["resume", self.session, prompt] if resume and self.session else [prompt]
        offset = self.log_path.stat().st_size if self.log_path.exists() else 0
        self.in_model = True
        try:
            with open(self.log_path, "ab") as sink:
                process = SweBenchDocker.popen(command, sink)
                code: Any = None
                while code is None:
                    try:
                        code = process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        if time.time() >= self.deadline or self.stop_event.is_set():
                            SweBenchDocker.run(["stop", "-t", str(STOP_GRACE_S), self.container],
                                               timeout=STOP_GRACE_S + 60)
                            try:
                                process.wait(timeout=STOP_GRACE_S)
                            except subprocess.TimeoutExpired:
                                process.kill()
                            code = "timeout"
        finally:
            self.in_model = False
        session = NightShiftTaskRun.thread_id_in(self.log_path, offset)
        if session:
            self.session = session
        if code == "timeout":
            return "timeout"
        return "ok" if code == 0 else "error"

    def _changed(self) -> bool:
        return self._script(CHANGED_SCRIPT, timeout=300).returncode == 0

    def _script(self, script: str, timeout: int, root: bool = False) -> Any:
        as_root = ["-u", "0", "-e", "HOME=/root"] if root else []
        return SweBenchDocker.run(["exec", *as_root, self.container, "bash", "-c", script], timeout=timeout)

    def _last_message(self) -> str:
        return self._read(self.scratch / "last.txt").strip()

    @classmethod
    def _read(cls, path: Path) -> str:
        try:
            return path.read_text(errors="replace")
        except OSError:
            return ""
