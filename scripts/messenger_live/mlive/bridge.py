"""The scratch Telegram bridge: `ling web serve` and `ling chat serve` in a home of their own.

The scratch home (`~/.cache/dreamference/messenger-live/home`) has its own HOME, CODEX_HOME and XDG
folders, so the bridge's threads, pairing and Night Shift queue never touch the user's. It answers as
the test bot (token in `~/.config/dreamference/telegram-test-bot-token`), with the owner's Telegram
user paired by writing `chat/telegram.json` directly, and reaches the node's model server as every
`ling` does. Its output is copied to the run's log with the token replaced.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Final

from .paths import cache_dir, config_dir, private_dir

WEB_PORT: Final = 3102
LING: Final = Path.home() / ".local" / "bin" / "ling"
USER_CONFIG: Final = """[chat]
telegram_rich = true
telegram_topics = true
approval_timeout_s = 60
night_repos = ["~/night-repo"]
"""


def scratch_env(home: Path) -> dict[str, str]:
    """The environment of every process of the scratch bridge."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("XDG_", "DBUS_", "CODEX_", "DREAMFERENCE_"))
    }
    run = home.parent / "run"
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".mightling"),
            "DREAMFERENCE_VLLM_HOST": "http://127.0.0.1:8000",
            "XDG_RUNTIME_DIR": str(run),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={run}/nobus",
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
        }
    )
    return env


def telegram_config(token: str, user_id: int, now: float) -> str:
    """`chat/telegram.json` as `ling chat telegram setup` and `/pair` would leave it."""
    return json.dumps({"token": token, "consented": int(now), "users": [user_id]}, indent=2)


def bridges_with_token(token: str, own_home: Path) -> list[int]:
    """The pids of other `ling chat serve` processes answering as the same bot.

    A process is matched by reading the `chat/telegram.json` of the CODEX_HOME in its environment;
    the token is compared, never shown.
    """
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            if b"chat" not in argv or b"serve" not in argv or not argv[0].endswith(b"ling"):
                continue
            env = dict(
                item.split("=", 1)
                for item in (entry / "environ").read_bytes().decode(errors="replace").split("\0")
                if "=" in item
            )
        except OSError:
            continue
        codex_home = Path(
            env.get("CODEX_HOME") or Path(env.get("HOME", "/nonexistent")) / ".mightling"
        )
        if codex_home == own_home / ".mightling":
            continue
        try:
            if (
                json.loads((codex_home / "chat" / "telegram.json").read_text()).get("token")
                == token
            ):
                found.append(int(entry.name))
        except (OSError, ValueError):
            continue
    return found


def answering_as(log: str) -> str | None:
    """The bot's username from the bridge's "answering as @name" line, or None before it."""
    match = re.search(r"Telegram: answering as @(\w+)", log)
    return match.group(1) if match else None


class ScratchBridge:
    """Starts and stops the scratch `ling web` and Telegram bridge."""

    def __init__(self, log: Path) -> None:
        """Prepares the scratch home's paths; nothing runs yet.

        Args:
            log: The run's bridge log (redacted).
        """
        self.home = private_dir(cache_dir() / "home")
        self.codex_home = self.home / ".mightling"
        self.env = scratch_env(self.home)
        self.log = log
        self.token = (config_dir() / "telegram-test-bot-token").read_text().strip()
        self.processes: list[subprocess.Popen[bytes]] = []

    def prepare(self, user_id: int) -> None:
        """Writes the scratch home's config, pairing and Night Shift checkout (idempotent)."""
        private_dir(self.home.parent / "run")
        private_dir(self.codex_home)
        user_config = self.home / ".config" / "dreamference" / "config.toml"
        user_config.parent.mkdir(parents=True, exist_ok=True)
        user_config.write_text(USER_CONFIG)
        chat = private_dir(self.codex_home / "chat")
        target = chat / "telegram.json"
        fd = os.open(target.with_suffix(".part"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(telegram_config(self.token, user_id, time.time()))
        target.with_suffix(".part").rename(target)
        # `ling night add` queues only where a runner exists (night.rs `runner_present`): a stub
        # `ling-admin` makes the scratch home one. Nothing ever runs its queue, which starts empty.
        stub = self.home / ".local" / "bin" / "ling-admin"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(
            "#!/bin/sh\necho 'the live messenger test home runs no Night Shift' >&2\nexit 1\n"
        )
        stub.chmod(0o755)
        shutil.rmtree(self.codex_home / "night", ignore_errors=True)
        repo = self.home / "night-repo"
        if not (repo / ".git").exists():
            repo.mkdir(exist_ok=True)
            (repo / "README.md").write_text(
                "A scratch checkout for the live messenger test's /night add.\n"
            )
            git = [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=ling test",
                "-c",
                "user.email=ling-test@localhost",
            ]
            subprocess.run([*git, "init", "-q"], check=True, env=self.env)
            subprocess.run([*git, "add", "README.md"], check=True, env=self.env)
            subprocess.run([*git, "commit", "-q", "-m", "scratch"], check=True, env=self.env)

    def _spawn(self, args: list[str], tag: str) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            args,
            cwd=self.home,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.processes.append(process)
        threading.Thread(target=self._copy, args=(process, tag), daemon=True).start()
        return process

    def _copy(self, process: subprocess.Popen[bytes], tag: str) -> None:
        assert process.stdout is not None
        with self.log.open("a") as out:
            for raw in process.stdout:
                line = raw.decode(errors="replace").replace(self.token, "<token>")
                out.write(f"{time.strftime('%H:%M:%S')} {tag}: {line}")
                out.flush()

    def web_answers(self) -> bool:
        """Says whether the scratch `ling web` answers its health check."""
        try:
            token = (self.codex_home / "web" / "token").read_text().strip()
            request = urllib.request.Request(
                f"http://127.0.0.1:{WEB_PORT}/healthz", headers={"Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status == 200
        except OSError:
            return False

    def start(self, timeout: float = 120.0) -> str:
        """Starts `ling web serve` and then `ling chat serve`; waits until the bridge answers.

        Returns:
            The test bot's username, as the bridge reports it.

        Raises:
            RuntimeError: Another bridge answers as the bot, or either process did not come up.
        """
        others = bridges_with_token(self.token, self.home)
        if others:
            raise RuntimeError(
                f"another bridge answers as the test bot (pid {', '.join(map(str, others))}); stop it first"
            )
        self._spawn([str(LING), "web", "serve", "--port", str(WEB_PORT)], "web")
        deadline = time.monotonic() + timeout
        while not self.web_answers():
            if time.monotonic() > deadline or self.processes[0].poll() is not None:
                raise RuntimeError("the scratch ling web did not come up")
            time.sleep(1)
        self._spawn([str(LING), "chat", "serve"], "bridge")
        while (username := answering_as(self.log.read_text(errors="replace"))) is None:
            if time.monotonic() > deadline or self.processes[-1].poll() is not None:
                raise RuntimeError("the scratch bridge did not reach Telegram")
            time.sleep(1)
        return username

    def stop(self) -> None:
        """Stops the bridge, then `ling web`, by process group."""
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    continue
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
        self.processes.clear()

    def sessions(self) -> Path:
        """Where the scratch threads' rollouts are."""
        return self.codex_home / "sessions"
