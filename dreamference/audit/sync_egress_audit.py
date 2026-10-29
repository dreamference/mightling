"""`ling-admin audit egress --sync`: where a sync agent's cycle connects.

This module provides the SyncEgressAudit class (specs/DREAMFERENCE_MIGHTLING_SYNC.md §9,
specs/DREAMFERENCE_MIGHTLING_EGRESS.md). It builds a throwaway node and a throwaway device on this
machine: the node's `ling web serve` on a free loopback port with its sync hub turned on
(`ling sync enable`), and a device paired with it by a code from `ling web pair`, exactly as a user
pairs a laptop. The device holds two rollouts, an interactive thread and a `ling exec` session. It
then runs one cycle of the device's agent (`ling sync agent --once`) under `strace -f`, and passes
only if every connection went to the hub's port, no name was asked of a resolver, the interactive
thread reached the hub and the `ling exec` session did not (O2). The node is named to the device in
`DREAMFERENCE_VLLM_HOST`, as every unattended caller names it, so the cycle browses for nothing.
No model server is needed: sync never talks to one.
"""

import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Final

from rich.console import Console

from dreamference.audit.egress_trace import EgressTrace
from dreamference.audit.egress_verdict import FAIL, PASS, TRACE_FAILED, EgressVerdict
from dreamference.audit.strace_parser import StraceParser

TRACED_SYSCALLS: Final[str] = "connect,sendto,sendmsg,sendmmsg,execve,write,writev"
STEP_TIMEOUT_S: Final[int] = 120
# How a step that ran past STEP_TIMEOUT_S reports itself.
TIMED_OUT: Final[str] = "did not finish"
WEB_START_TIMEOUT_S: Final[int] = 30
# The session name in the result file's name and body, beside `exec`, `tui`, `app`, `web` and `docs`.
SYNC_SESSION: Final[str] = "sync"
# The two rollouts on the throwaway device: an interactive thread, which must reach the hub, and a
# `ling exec` session, which must not.
THREAD_ID: Final[str] = "01a0f1ae-2da4-7942-99ae-c3151dc1a0d1"
EXEC_ID: Final[str] = "01a0f1ae-2da4-7942-99ae-c3151dc1e8ec"
PAIRING_CODE: Final[re.Pattern[str]] = re.compile(r"Pairing code: (\d{4}) (\d{4})")
# The report's voice; no markup, since the lines quote `[sync]` settings.
CONSOLE: Final[Console] = Console(markup=False, highlight=False)


class SyncEgressAudit:
    """Traces one sync agent cycle against a throwaway hub and judges the trace."""

    @classmethod
    def judge(
        cls,
        trace: EgressTrace,
        hub_port: int,
        published: bool,
        exec_held: bool,
        finished: bool = True,
    ) -> EgressVerdict:
        """Decides the verdict: the hub's port and nothing else, no DNS, and a cycle that did its work.

        Args:
            trace (EgressTrace): What the agent's cycle did.
            hub_port (int): The throwaway hub's loopback port.
            published (bool): Whether the interactive thread reached the hub.
            exec_held (bool): Whether the `ling exec` session stayed off it.
            finished (bool): Whether the traced cycle ended before the timeout.

        Returns:
            EgressVerdict: The verdict and its reasons.
        """
        problems: list[str] = []
        for target, count in sorted(trace.destinations.items()):
            address, _, port = target.rpartition(":")
            if not StraceParser.is_loopback(address.strip("[]")) or int(port) != hub_port:
                problems.append(f"connected to {target} ({count}x): not the node's ling web port")
        problems += [
            f"asked a resolver for {name} ({count}x)"
            for name, count in sorted(trace.dns_names.items())
        ]
        if trace.dns_servers and not trace.dns_names:
            servers = ", ".join(sorted(trace.dns_servers))
            problems.append(f"sent a DNS query to {servers} whose name could not be read")
        problems += [
            f"ran a git command that reaches a network: {command}"
            for command in trace.networked_git
        ]
        if not exec_held:
            problems.append(
                "a `ling exec` session reached the hub, which `[sync] exec` (off) keeps on the device"
            )
        if problems:
            return EgressVerdict(FAIL, problems)
        if not trace.processes:
            return EgressVerdict(TRACE_FAILED, ["strace recorded no process: it could not attach"])
        if not finished:
            return EgressVerdict(
                TRACE_FAILED,
                [
                    f"the agent cycle did not finish within {STEP_TIMEOUT_S} s, so the trace is cut off"
                ],
            )
        if not published:
            return EgressVerdict(
                TRACE_FAILED, ["the agent published nothing, so the trace shows no sync"]
            )
        return EgressVerdict(PASS)

    @classmethod
    def rollout(cls, codex_home: str, thread: str, source: str, text: str) -> None:
        """Writes one rollout the way Codex names and starts it.

        Args:
            codex_home (str): The device's `CODEX_HOME`.
            thread (str): The thread id.
            source (str): `session_meta.source` (`vscode` for the app, `exec` for `ling exec`).
            text (str): What the user said in it.
        """
        folder = Path(codex_home) / "sessions" / "2026" / "10" / "22"
        folder.mkdir(parents=True, exist_ok=True)
        said = {"type": "UserMessage", "content": [{"type": "text", "text": text}]}
        lines = [
            {
                "type": "session_meta",
                "payload": {"id": thread, "cwd": "/tmp/audit", "source": source},
            },
            {"type": "event_msg", "payload": {"type": "item_completed", "item": said}},
        ]
        text_of = "".join(json.dumps(line) + "\n" for line in lines)
        (folder / f"rollout-2026-10-22T10-00-00-{thread}.jsonl").write_text(text_of)

    @classmethod
    def environment(cls, work_dir: str, name: str, extra: dict[str, str]) -> dict[str, str]:
        """A throwaway user: its own HOME, CODEX_HOME and runtime folder, and none of the user's configuration.

        Args:
            work_dir (str): The scratch directory.
            name (str): `node` or `device`.
            extra (dict[str, str]): Variables to set besides.

        Returns:
            dict[str, str]: The environment.
        """
        home = Path(work_dir) / name
        run = Path(work_dir) / f"{name}-run"
        home.mkdir(parents=True, exist_ok=True)
        run.mkdir(mode=0o700, parents=True, exist_ok=True)
        dropped = ("CODEX_HOME", "DREAMFERENCE_CONFIG_PATH", "DREAMFERENCE_VLLM_HOST")
        env = {key: value for key, value in os.environ.items() if key not in dropped}
        env.update(
            {
                "HOME": str(home),
                "CODEX_HOME": str(home / ".mightling"),
                "XDG_RUNTIME_DIR": str(run),
                "DREAMFERENCE_CONFIG_PATH": str(home / "dreamference.toml"),
            }
        )
        env.update(extra)
        return env

    @classmethod
    def step(cls, ling: str, args: list[str], env: dict[str, str]) -> str:
        """Runs one untraced `ling` command of the set-up.

        Args:
            ling (str): The `ling` executable.
            args (list[str]): Its arguments.
            env (dict[str, str]): Its environment.

        Returns:
            str: What it printed, or why it could not run.
        """
        try:
            process = subprocess.Popen(
                [ling, *args],
                cwd=env["HOME"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as error:
            return str(error)
        try:
            stdout, stderr = process.communicate(timeout=STEP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # The whole group: killing strace alone left the agent it traced running, untraced.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.communicate(timeout=10)
            return f"{TIMED_OUT} within {STEP_TIMEOUT_S} s"
        return stdout + stderr

    @classmethod
    def trace(cls, ling: str, work_dir: str) -> tuple[EgressTrace, int, bool, bool, dict[str, str]]:
        """Sets up the throwaway node and device, and traces one agent cycle of the device.

        Args:
            ling (str): The `ling` executable.
            work_dir (str): A scratch directory, owned by the caller.

        Returns:
            tuple[EgressTrace, int, bool, bool, dict[str, str]]: The trace, the hub's port, whether
            the thread was published, whether the exec session was held back, and what each step said.
        """
        from dreamference.audit.egress_audit import EgressAudit

        port = EgressAudit.free_port()
        outputs: dict[str, str] = {}
        node = cls.environment(work_dir, "node", {"DREAMFERENCE_VLLM_HOST": "http://127.0.0.1:9"})
        node_id = Path(node["HOME"]) / ".config" / "dreamference" / "node-id"
        node_id.parent.mkdir(parents=True, exist_ok=True)
        node_id.write_text("audit-node\n")
        server_file = Path(node["CODEX_HOME"]) / "web" / "server.json"
        trace_path = Path(work_dir) / "trace.txt"
        try:
            server = subprocess.Popen(
                [ling, "web", "serve", "--port", str(port)],
                cwd=node["HOME"],
                env=node,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            return EgressTrace(), port, False, True, {"web serve": str(error)}
        try:
            deadline = time.monotonic() + WEB_START_TIMEOUT_S
            while (
                not server_file.is_file() and server.poll() is None and time.monotonic() < deadline
            ):
                time.sleep(0.2)
            outputs["hub"] = cls.step(
                ling, ["sync", "enable", "--no-unit", "--port", str(port)], node
            )
            outputs["pair"] = cls.step(ling, ["web", "pair"], node)
            code = PAIRING_CODE.search(outputs["pair"])
            # The device names the node the way unattended callers do, so nothing is browsed for.
            device = cls.environment(
                work_dir, "device", {"DREAMFERENCE_VLLM_HOST": "http://127.0.0.1:8000"}
            )
            if code:
                pairing = ["--code", code.group(1) + code.group(2), "--name", "audit-device"]
                outputs["device"] = cls.step(
                    ling, ["sync", "enable", "--no-unit", "--port", str(port), *pairing], device
                )
            cls.rollout(
                device["CODEX_HOME"],
                THREAD_ID,
                "vscode",
                "An interactive thread, for the sync audit",
            )
            cls.rollout(
                device["CODEX_HOME"],
                EXEC_ID,
                "exec",
                "A ling exec session, which stays on the device",
            )
            strace = [
                "strace",
                "-f",
                "-qq",
                "-yy",
                "-e",
                f"trace={TRACED_SYSCALLS}",
                "-s",
                "256",
                "-o",
                str(trace_path),
            ]
            outputs["agent"] = cls.step(
                "strace", [*strace[1:], ling, "sync", "agent", "--once"], device
            )
        finally:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(server.pid, sig)
                    server.wait(timeout=20)
                    break
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    continue
        rollouts = Path(node["CODEX_HOME"]) / "sync" / "hub" / "rollouts"
        published = (rollouts / f"{THREAD_ID}.json").is_file()
        exec_held = (
            not (rollouts / f"{EXEC_ID}.json").exists()
            and not (rollouts / f"{EXEC_ID}.jsonl").exists()
        )
        text = trace_path.read_text(errors="replace") if trace_path.is_file() else ""
        return StraceParser.parse(text), port, published, exec_held, outputs

    @classmethod
    def write_result(cls, trace: EgressTrace, verdict: EgressVerdict, ling: str) -> str:
        """Writes the full result as JSON under `$CODEX_HOME/audit/`, as `<timestamp>-sync.json`.

        Args:
            trace (EgressTrace): What the cycle did.
            verdict (EgressVerdict): The verdict.
            ling (str): The executable that was traced.

        Returns:
            str: The file written.
        """
        from dreamference.runner.codex_installer import CodexInstaller

        directory = Path(CodexInstaller.home_dir()) / "audit"
        directory.mkdir(exist_ok=True, parents=True)
        now = datetime.datetime.now().astimezone()
        path = directory / f"{now:%Y%m%d-%H%M%S}-{SYNC_SESSION}.json"
        try:
            sha256: str | None = hashlib.sha256(Path(ling).read_bytes()).hexdigest()
        except OSError:
            sha256 = None
        result = {
            "at": now.isoformat(timespec="seconds"),
            "session": SYNC_SESSION,
            "verdict": verdict.status,
            "problems": verdict.problems,
            **asdict(trace),
            "ling": ling,
            "ling_sha256": sha256,
        }
        path.write_text(json.dumps(result, indent=2) + "\n")
        return str(path)

    @classmethod
    def from_cli(cls, prompt: str | None, write_json: bool) -> int:
        """`ling-admin audit egress --sync`: refuses `--prompt`, which no part of the scenario uses.

        Args:
            prompt (str | None): `--prompt`, which must not be given.
            write_json (bool): `--json`.

        Returns:
            int: The audit's exit code, or 2 for a prompt.
        """
        if prompt is not None:
            CONSOLE.print(
                "❌ --prompt has no effect with --sync: it traces one sync agent cycle, with no ling session to prompt."
            )
            return 2
        return cls.run(write_json=write_json)

    @classmethod
    def run(cls, ling: str | None = None, write_json: bool = False) -> int:
        """Runs the scenario and prints its report.

        Args:
            ling (str | None): The `ling` executable; the installed build by default.
            write_json (bool): Also write the full result to `$CODEX_HOME/audit/<timestamp>-sync.json`.

        Returns:
            int: 0 on a pass, 1 on any other destination, 2 when the trace itself failed.
        """
        from dreamference.runner.codex_installer import CodexInstaller

        if shutil.which("strace") is None:
            CONSOLE.print(
                "⚠️  Sync egress audit: trace failed\n   - strace is not installed: sudo apt-get install strace"
            )
            return 2
        ling = ling or CodexInstaller.get_codex_executable()
        if not ling:
            CONSOLE.print(
                "⚠️  Sync egress audit: trace failed\n   - ling is not built: run `ling-admin codex build` first."
            )
            return 2
        CONSOLE.print(
            "🚀 Tracing one sync agent cycle against a throwaway hub on loopback (throwaway node and device)..."
        )
        work_dir = tempfile.mkdtemp(prefix="mightling-sync-audit-")
        try:
            trace, port, published, exec_held, outputs = cls.trace(ling, work_dir)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        finished = not outputs.get("agent", "").startswith(TIMED_OUT)
        verdict = cls.judge(trace, port, published, exec_held, finished)
        mark = {PASS: "✅", FAIL: "❌"}.get(verdict.status, "⚠️ ")
        CONSOLE.print(f"{mark} Sync egress audit: {verdict.status}")
        for problem in verdict.problems:
            CONSOLE.print(f"   - {problem}")
        if verdict.status == TRACE_FAILED:
            for step, said in outputs.items():
                CONSOLE.print(f"   {step}: {said.strip()[-400:]}")
        destinations = ", ".join(
            f"{target} ({count}x)" for target, count in sorted(trace.destinations.items())
        )
        CONSOLE.print(f"Network destinations: {destinations or 'none'} (the hub: 127.0.0.1:{port})")
        CONSOLE.print(f"DNS queries: {', '.join(sorted(trace.dns_names)) or 'none'}")
        processes = sorted(trace.processes.items(), key=lambda item: (-item[1], item[0]))
        CONSOLE.print(
            f"Processes started: {', '.join(f'{name} ({count})' for name, count in processes) or 'none'}"
        )
        if write_json:
            CONSOLE.print(f"💡 Full result: {cls.write_result(trace, verdict, ling)}")
        return verdict.exit_code
