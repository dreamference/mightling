"""
One interactive `mling` session driven on a pseudo-terminal, for `mling-admin audit egress --tui`
(specs/DREAMFERENCE_MIGHTLING_EGRESS.md §3.1 step 3).

This module provides the TuiSession class. `mling exec` and the full-screen interface start
different parts of Codex: the announcement tip that patch `0015` closed was fetched by the
interface alone, and no trace of `exec` could have shown it. So the audit also opens the real
interface, types a prompt, waits for the reply and quits, with strace around all of it.

The terminal is driven with `pexpect` and rendered with `pyte`, as the live slash-command tests
do. Neither is a dependency of the package: their imports stay inside methods, and the audit names
them when they are missing, the way it names a missing `strace`.
"""

import glob
import json
import os
import signal
import time
from typing import Dict, Final, List, Optional

ROWS: Final[int] = 50
COLS: Final[int] = 160

# The launcher may first wait for the model server, and the first prompt compiles a template.
READY_TIMEOUT_S: Final[int] = 120
# Feature flags that decide what the composer accepts load just after it appears.
SETTLE_AFTER_READY_S: Final[float] = 3.0
# After the reply, so that anything the interface starts once a turn ends is in the trace too.
SETTLE_AFTER_REPLY_S: Final[float] = 3.0
QUIT_TIMEOUT_S: Final[int] = 15
# Between typing a line and submitting it: the command popup has to settle on what was typed.
TYPE_PAUSE_S: Final[float] = 1.0


class TuiSession:
    """Opens the interface, holds one exchange with the model and quits."""

    @classmethod
    def missing_modules(cls) -> List[str]:
        """
        Names what the pseudo-terminal session needs and this environment lacks.

        Returns:
            List[str]: Any of `pexpect` and `pyte` that cannot be imported; empty when both can.
        """
        missing = []
        for name in ("pexpect", "pyte"):
            try:
                __import__(name)
            except ImportError:
                missing.append(name)
        return missing

    @classmethod
    def trust(cls, home: str, repo: str) -> None:
        """
        Marks `repo` as trusted in a throwaway `CODEX_HOME`, so the interface opens on its
        composer instead of on the question whether to trust the directory. The launcher edits
        this file in place and keeps the table.

        Args:
            home (str): The throwaway `CODEX_HOME`.
            repo (str): The throwaway repository.
        """
        with open(os.path.join(home, "config.toml"), "a") as handle:
            handle.write(f"[projects.{json.dumps(os.path.realpath(repo))}]\ntrust_level = \"trusted\"\n")

    @classmethod
    def reply_in(cls, home: str) -> Optional[str]:
        """
        Reads the model's reply from the session file the interface writes, not from the screen:
        the screen also shows the prompt that was typed, which may itself contain the reply's
        words.

        Args:
            home (str): The session's `CODEX_HOME`.

        Returns:
            Optional[str]: The last message of a completed turn, or None while there is none.
        """
        for path in glob.glob(os.path.join(home, "sessions", "**", "*.jsonl"), recursive=True):
            try:
                with open(path, errors="replace") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        payload = event.get("payload") if isinstance(event, dict) else None
                        if isinstance(payload, dict) and payload.get("type") == "task_complete":
                            message = payload.get("last_agent_message")
                            if isinstance(message, str) and message.strip():
                                return message
            except OSError:
                continue
        return None

    @classmethod
    def run(cls, command: List[str], cwd: str, env: Dict[str, str], home: str, prompt: str,
            timeout: float) -> Dict[str, object]:
        """
        Runs `command` (strace around `mling`) on a pseudo-terminal: waits for the composer,
        types `prompt`, waits for the reply, and quits with `/quit`. A session that does not end
        by itself is stopped with everything it started.

        Args:
            command (List[str]): The program and its arguments.
            cwd (str): The directory to start it in.
            env (Dict[str, str]): Its environment; `TERM` is set here.
            home (str): The session's `CODEX_HOME`, where its reply is read from.
            prompt (str): The prompt to type. One line: a newline would submit it early.
            timeout (float): Seconds allowed for the whole session.

        Returns:
            Dict[str, object]: `replied` (bool), `stage` (how far it got: `start`, `composer`,
            `reply` or `quit`) and `screen` (the last screen, for the report when it failed).
        """
        import pexpect
        import pyte

        screen = pyte.Screen(COLS, ROWS)
        stream = pyte.ByteStream(screen)
        deadline = time.monotonic() + timeout
        child = pexpect.spawn(command[0], command[1:], cwd=cwd, env=dict(env, TERM="xterm-256color"),
                              dimensions=(ROWS, COLS))

        def text() -> str:
            return "\n".join(line.rstrip() for line in screen.display)

        def pump(seconds: float) -> bool:
            """Renders output for `seconds`; False once the process has exited."""
            end = min(time.monotonic() + seconds, deadline)
            while time.monotonic() < end:
                try:
                    stream.feed(child.read_nonblocking(65536, timeout=0.2))
                except pexpect.TIMEOUT:
                    continue
                except (pexpect.EOF, OSError):
                    return False
            return True

        def wait(done, seconds: float) -> bool:
            end = min(time.monotonic() + seconds, deadline)
            while time.monotonic() < end:
                alive = pump(0.5)
                if done():
                    return True
                if not alive:
                    return False
            return False

        stage = "start"
        replied = False
        try:
            if wait(lambda: ">_ Mightling" in text() and "›" in text(), READY_TIMEOUT_S):
                stage = "composer"
                pump(SETTLE_AFTER_READY_S)
                child.send(" ".join(prompt.split()))
                pump(TYPE_PAUSE_S)
                child.send("\r")
                if wait(lambda: cls.reply_in(home) is not None, timeout):
                    stage = "reply"
                    replied = True
                    pump(SETTLE_AFTER_REPLY_S)
                    child.send("/quit")
                    pump(TYPE_PAUSE_S)
                    child.send("\r")
                    wait(lambda: not child.isalive(), QUIT_TIMEOUT_S)
                    # The pseudo-terminal closes a moment before the process is reaped.
                    time.sleep(0.5)
                    if not child.isalive():
                        stage = "quit"
        finally:
            last_screen = text()
            cls._stop(child)
        return {"replied": replied, "stage": stage, "screen": last_screen}

    @classmethod
    def _stop(cls, child) -> None:
        """Ends the session's whole process group: strace alone, killed, would leave `mling`."""
        if child.isalive():
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                end = time.monotonic() + 10
                while time.monotonic() < end and child.isalive():
                    time.sleep(0.2)
                if not child.isalive():
                    break
        child.close(force=True)
