"""
What the night run needs to know about the machine: the model server, memory, and who else is
using either (specs/DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md §5.2, §5.5).

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

# Environment marker on every process a night run starts, so the run never mistakes its own
# `puffin exec` sessions for someone working interactively.
NIGHT_RUN_ENV: Final[str] = "PUFFIN_NIGHT_RUN"

# `puffin` subcommands that are not an interactive session. Anything else (no subcommand, a
# prompt, `resume`, `fork`) is the TUI.
NON_INTERACTIVE: Final[frozenset] = frozenset({
    "exec", "e", "app-server", "mcp-server", "mcp", "sandbox", "apply", "a", "completion",
    "debug", "features", "doctor", "night", "update", "app", "help", "review", "--version", "-V",
    "--help", "-h",
})

GIB: Final[int] = 1024 ** 3


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
    def interactive_puffin_pids(cls, puffin_bin: str) -> List[int]:
        """
        Finds `puffin` TUI sessions: processes of the `puffin` binary that are not one of a night
        run's own sessions, not a subcommand that runs without a user, and not one of Codex's
        helper re-executions (its sandbox runs as the same binary under another `argv[0]`).

        Args:
            puffin_bin: The installed `puffin` executable.

        Returns:
            List[int]: Their process ids.
        """
        target = os.path.realpath(puffin_bin)
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
            if not args or os.path.basename(args[0]) not in ("puffin", "codex"):
                continue
            if any(item.startswith(f"{NIGHT_RUN_ENV}=".encode()) for item in environ):
                continue
            if cls.is_interactive(args[1:]):
                pids.append(int(entry.name))
        return pids

    @classmethod
    def is_interactive(cls, args: List[str]) -> bool:
        """
        Tells whether a `puffin` command line opens the TUI.

        Args:
            args: The arguments after the program name.

        Returns:
            bool: False when any argument names a non-interactive subcommand.
        """
        return not any(arg in NON_INTERACTIVE for arg in args)

    @classmethod
    def heavy_jobs(cls) -> List[str]:
        """
        Names the heavy work already running that a night run must not join: a `puffin` build, a
        `puffin-admin index` run, a `puffin-code` index run, or a Codex test run.

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
                    found.append("a puffin build holds the build lock")
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                args = [arg.decode("utf-8", "replace")
                        for arg in (entry / "cmdline").read_bytes().split(b"\0") if arg]
            except OSError:
                continue
            names = [os.path.basename(arg) for arg in args[:2]]
            if "puffin-admin" in names and "index" in args[1:3]:
                found.append(f"puffin-admin index is running (pid {entry.name})")
        try:
            units = subprocess.run(
                ["systemctl", "--user", "list-units", "--no-legend", "--plain", "--state=active",
                 "codex-tests-*", "puffin-index-*"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            units = ""
        for line in units.splitlines():
            if line.strip():
                unit = line.split()[0]
                kind = "an index run" if unit.startswith("puffin-index-") else "a Codex test run"
                found.append(f"{kind} is active ({unit})")
        return found
