"""
What the night run needs to know about the machine: the model server, memory, and who else is
using either (specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md §5.2, §5.5).

Every probe reads; none starts, stops or loads anything. The model server may be SGLang (the
default) or vLLM, so each metric is read under both engines' names.
"""

import fcntl
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict, Final, List, Optional, Tuple

from dreamference.runner.codex_branded_builder import BUILD_CACHE_DIR
from dreamference.runner.codex_installer import CodexInstaller

# Environment marker on every process a night run starts, so the run never mistakes its own
# `ling exec` sessions for someone working interactively.
NIGHT_RUN_ENV: Final[str] = "MIGHTLING_NIGHT_RUN"

# `ling` subcommands that are not an interactive session. Anything else (no subcommand, a
# prompt, `resume`, `fork`) is the TUI.
NON_INTERACTIVE: Final[frozenset] = frozenset({
    "exec", "e", "app-server", "mcp-server", "mcp", "sandbox", "apply", "a", "completion",
    "debug", "features", "doctor", "night", "node", "airgapped", "skill", "prompt", "update", "app", "help", "review",
    "--version", "-V",
    "--help", "-h",
})

# Where `ling-app` marks a `ling app-server` that is running a turn, under `$CODEX_HOME/night`
# (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §8.3); `desktop/electron/src/server.ts` writes it.
BUSY_DIR_NAME: Final[str] = "busy"

GIB: Final[int] = 1024 ** 3

# The part of the KV pool a night run divides between its tasks. Codex compacts after the turn that
# crosses its limit, so a session can overshoot by one turn's tokens; the rest of the pool absorbs
# that instead of the server retracting a request.
NIGHT_POOL_SHARE: Final[float] = 0.9

# The launcher's limit for any session, as a percentage of the pool (`POOL_SHARE_PERCENT` in
# ling-rs/src/compaction.rs); a lone night task is not given more than an interactive one.
LAUNCHER_POOL_SHARE_PERCENT: Final[int] = 60


class NightShiftHost:
    """Read-only probes of the model server and the host."""

    @classmethod
    def served_model(cls, host: str, timeout: float = 3.0) -> Optional[Tuple[str, int]]:
        """
        Asks the model server what it serves.

        Args:
            host: The server's base URL.
            timeout: Seconds to wait.

        Returns:
            Optional[Tuple[str, int]]: The model id and its context length, or None if the server
            does not answer.
        """
        try:
            with urllib.request.urlopen(f"{host.rstrip('/')}/v1/models", timeout=timeout) as response:
                data = json.load(response)
            model = data["data"][0]
            return str(model["id"]), int(model.get("max_model_len") or 0)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return None

    @classmethod
    def metrics(cls, host: str, timeout: float = 3.0) -> Optional[Dict[str, float]]:
        """
        Reads the engine's request gauges, a request counter and the KV pool from `/metrics`.

        Args:
            host: The server's base URL.
            timeout: Seconds to wait.

        Returns:
            Optional[Dict[str, float]]: `running` (running plus queued requests), `served` (prompt
            tokens processed so far, a counter any request moves) and `kv_pool` (tokens of KV
            cache, 0 if unknown); None if `/metrics` does not answer.
        """
        try:
            with urllib.request.urlopen(f"{host.rstrip('/')}/metrics", timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
        except OSError:
            return None
        return cls.parse_metrics(text)

    @classmethod
    def parse_metrics(cls, text: str) -> Dict[str, float]:
        """
        Extracts what `metrics()` returns from Prometheus text.

        Args:
            text: The `/metrics` body.

        Returns:
            Dict[str, float]: See `metrics()`.
        """
        sums: Dict[str, float] = {}
        kv_pool = 0.0
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            match = re.match(r"([a-zA-Z_:][\w:]*)(\{[^}]*\})?\s+(\S+)", line)
            if not match:
                continue
            name, labels, raw = match.groups()
            try:
                value = float(raw)
            except ValueError:
                continue
            sums[name] = sums.get(name, 0.0) + value
            if name == "vllm:cache_config_info" and labels:
                blocks = re.search(r'num_gpu_blocks="(\d+)"', labels)
                size = re.search(r'block_size="(\d+)"', labels)
                if blocks and size:
                    kv_pool = float(int(blocks.group(1)) * int(size.group(1)))
        running = sum(sums.get(name, 0.0) for name in (
            "sglang:num_running_reqs", "sglang:num_queue_reqs",
            "vllm:num_requests_running", "vllm:num_requests_waiting"))
        served = sums.get("sglang:prompt_tokens_total", 0.0) + sums.get("vllm:prompt_tokens_total", 0.0)
        kv_pool = kv_pool or sums.get("sglang:max_total_num_tokens", 0.0)
        return {"running": running, "served": served, "kv_pool": kv_pool}

    @classmethod
    def parallelism(cls, max_parallel: int, kv_pool: float, task_context: int) -> int:
        """
        How many tasks may run at once.

        Args:
            max_parallel: The configured upper bound.
            kv_pool: Tokens of KV cache, 0 if unknown.
            task_context: Tokens budgeted per task.

        Returns:
            int: At least 1, at most `max_parallel`.
        """
        by_pool = int(kv_pool // task_context) if kv_pool else max_parallel
        return max(1, min(max_parallel, by_pool))

    @classmethod
    def task_budget(cls, max_parallel: int, kv_pool: float, task_context: int,
                    compact_at: Optional[int]) -> Tuple[int, Optional[int]]:
        """
        How many night tasks run at once, and the context each may hold, such that together they
        fit in the KV pool: `parallel × limit ≤ 90% of the pool`. The limit is enforced by passing
        it to every `ling exec` of a task as its compaction limit.

        Args:
            max_parallel: The configured upper bound.
            kv_pool: Tokens of KV cache, 0 if unknown.
            task_context: The smallest budget a task may be given.
            compact_at: `[night] compact_at`: None for the pool's share, 0 for no limit, or a limit.

        Returns:
            Tuple[int, Optional[int]]: Tasks at once (at least 1), and each task's compaction limit
            in tokens (None: no limit is passed and the launcher's own applies).
        """
        if not kv_pool:
            # Nothing to divide: one task at a time cannot overcommit a pool it shares with no one.
            return 1, compact_at or None
        usable = int(kv_pool * NIGHT_POOL_SHARE)
        if compact_at == 0:
            return max(1, min(max_parallel, usable // task_context)), None
        if compact_at:
            return max(1, min(max_parallel, usable // compact_at)), compact_at
        parallel = max(1, min(max_parallel, usable // task_context))
        return parallel, min(usable // parallel, int(kv_pool) * LAUNCHER_POOL_SHARE_PERCENT // 100)

    @classmethod
    def host_safety_ok(cls) -> bool:
        """
        Runs the model server's own pre-load host-safety checks (swap, sysctls, earlyoom), which
        print what is wrong and exit when anything is.

        Returns:
            bool: True when every check passes.
        """
        from dreamference.vllm_server import VLLMServerManager
        try:
            VLLMServerManager.check_host_safety()
        except SystemExit:
            return False
        return True

    @classmethod
    def mem_available_bytes(cls) -> int:
        """
        Returns:
            int: `MemAvailable` from `/proc/meminfo`, in bytes.
        """
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        return 0

    @classmethod
    def mem_total_bytes(cls) -> int:
        """
        Returns:
            int: `MemTotal` from `/proc/meminfo`, in bytes.
        """
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        return 0

    @classmethod
    def parse_size(cls, size: str) -> int:
        """
        Parses a systemd-style size (`8G`, `512M`, bytes).

        Args:
            size: The size.

        Returns:
            int: Bytes.
        """
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?)i?B?\s*", size, re.IGNORECASE)
        if not match:
            raise ValueError(f"not a size: {size!r}")
        factor = {"": 1, "K": 1024, "M": 1024 ** 2, "G": GIB, "T": 1024 ** 4}[match.group(2).upper()]
        return int(float(match.group(1)) * factor)

    @classmethod
    def scope_memory_current(cls, unit: str) -> int:
        """
        Memory a running task's scope uses now.

        Args:
            unit: The scope's unit name.

        Returns:
            int: Bytes, 0 if unknown.
        """
        try:
            output = subprocess.run(
                ["systemctl", "--user", "show", unit, "-p", "MemoryCurrent", "--value"],
                capture_output=True, text=True, timeout=10).stdout.strip()
            return int(output)
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0

    @classmethod
    def interactive_mightling_pids(cls, mightling_bin: str) -> List[int]:
        """
        Finds `ling` TUI sessions: processes of the `ling` binary that are not one of a night
        run's own sessions, not a subcommand that runs without a user, and not one of Codex's
        helper re-executions (its sandbox runs as the same binary under another `argv[0]`).

        Args:
            mightling_bin: The installed `ling` executable.

        Returns:
            List[int]: Their process ids.
        """
        target = os.path.realpath(mightling_bin)
        pids = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if os.path.realpath(entry / "exe") != target:
                    continue
                argv = (entry / "cmdline").read_bytes().split(b"\0")
                environ = (entry / "environ").read_bytes().split(b"\0")
            except OSError:
                continue
            args = [arg.decode("utf-8", "replace") for arg in argv if arg]
            if not args or os.path.basename(args[0]) not in ("ling", "codex"):
                continue
            if any(item.startswith(f"{NIGHT_RUN_ENV}=".encode()) for item in environ):
                continue
            if cls.is_interactive(args[1:]):
                pids.append(int(entry.name))
        return pids + [pid for pid in cls.busy_app_server_pids(mightling_bin) if pid not in pids]

    @classmethod
    def busy_app_server_pids(cls, mightling_bin: str, codex_home: str | None = None) -> list[int]:
        """Finds `ling app-server` processes running a turn for the desktop app's Work window.

        An app-server is not a session by its command line (`NON_INTERACTIVE`): an idle
        window left open must not hold every night back. While a turn runs, `ling-app` keeps a
        marker named after the server's pid in `$CODEX_HOME/night/busy/`
        (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §8.3). A marker counts when its pid is alive and is
        the installed `ling`; any other marker is left by a window killed hard, and is deleted.

        Args:
            mightling_bin: The installed `ling` executable.
            codex_home: `ling`'s home folder; defaults to the one `ling` resolves.

        Returns:
            list[int]: The busy servers' process ids.
        """
        busy_dir = Path(codex_home or CodexInstaller.home_dir()) / "night" / BUSY_DIR_NAME
        try:
            markers = list(busy_dir.iterdir())
        except OSError:
            return []
        target = os.path.realpath(mightling_bin)
        pids = []
        for marker in markers:
            pid = int(marker.name) if marker.name.isdigit() else 0
            if pid > 0 and cls._process_alive(pid) and os.path.realpath(f"/proc/{pid}/exe") == target:
                pids.append(pid)
            else:
                marker.unlink(missing_ok=True)
        return pids

    @classmethod
    def _process_alive(cls, pid: int) -> bool:
        """Signal 0: whether a process exists, without touching it.

        Args:
            pid: The process id.

        Returns:
            bool: True when it exists, including one owned by another user.
        """
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def is_interactive(cls, args: List[str]) -> bool:
        """
        Tells whether a `ling` command line opens the TUI.

        Args:
            args: The arguments after the program name.

        Returns:
            bool: False when any argument names a non-interactive subcommand.
        """
        return not any(arg in NON_INTERACTIVE for arg in args)

    @classmethod
    def heavy_jobs(cls) -> List[str]:
        """
        Names the heavy work already running that a night run must not join: a `ling` build, a
        `ling-admin index` run, a `ling-code` index run, or a Codex test run.

        Returns:
            List[str]: One description per job found; empty when the machine is free.
        """
        found = []
        lock_path = os.path.join(BUILD_CACHE_DIR, ".build.lock")
        if os.path.exists(lock_path):
            with open(lock_path, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle, fcntl.LOCK_UN)
                except BlockingIOError:
                    found.append("a ling build holds the build lock")
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                args = [arg.decode("utf-8", "replace")
                        for arg in (entry / "cmdline").read_bytes().split(b"\0") if arg]
            except OSError:
                continue
            names = [os.path.basename(arg) for arg in args[:2]]
            if "ling-admin" in names and "index" in args[1:3]:
                found.append(f"ling-admin index is running (pid {entry.name})")
        try:
            units = subprocess.run(
                ["systemctl", "--user", "list-units", "--no-legend", "--plain", "--state=active",
                 "codex-tests-*", "mightling-index-*"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            units = ""
        for line in units.splitlines():
            if line.strip():
                unit = line.split()[0]
                kind = "an index run" if unit.startswith("mightling-index-") else "an upstream test run"
                found.append(f"{kind} is active ({unit})")
        return found
