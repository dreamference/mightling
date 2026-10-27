"""The Signal half of the suite: a second device on the owner's account writes Note to Self.

The live bridge (the owner's, never restarted or reconfigured) answers Note to Self from its own
linked device. The harness's device runs signal-cli's JSON-RPC mode and keeps only Note to Self
messages sent by the bridge's device (`signal_device.note_to_self` and the device filter); the
owner's own notes and every other conversation are dropped as they arrive. Every message the
harness sends starts "[ling test]", except the bridge's commands and YES/NO, which are only
commands when they are the whole message.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from . import checks, sessions, signal_device, voice
from .report import Result, Run
from .signal_device import PREFIX
from .telegram_suite import (
    GATE_BUSY,
    HOSTILE_PROMPT,
    LONG_PROMPT,
    RICH_PROMPT,
    SEARCH_PROMPT,
    TYPO_PROMPT,
)

ANSWER_TIMEOUT: Final = 300.0
QUIET: Final = 20.0
STREAM_WINDOW: Final = 45.0
WORKING: Final = "Working on it…"


@dataclass
class Note:
    """One of the bridge's Note to Self messages, with its edits."""

    timestamp: int
    at: float
    text: str
    styles: list[Any]
    edits: list[float] = field(default_factory=list)
    # When the note first held words of an answer (not the status line).
    answered_at: float | None = None


class SignalRpc:
    """signal-cli's JSON-RPC mode on the harness's device, filtered to the bridge's Note to Self."""

    def __init__(self, data: Path, number: str, debug: Path | None = None) -> None:
        """Starts signal-cli and the reader thread.

        Args:
            data: The device's signal-cli folder.
            number: The account's number.
            debug: Where kept (already filtered) envelopes are written, or None.
        """
        self.number = number
        self.debug = debug
        command = [
            *signal_device.base_command(data),
            "-a",
            number,
            "jsonRpc",
            "--ignore-attachments",
            "--ignore-stories",
            "--ignore-avatars",
            "--ignore-stickers",
            "--receive-mode",
            "on-start",
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=signal_device.signal_cli_env(),
        )
        self.lock = threading.Lock()
        self.counter = 0
        self.answers: dict[str, dict[str, Any]] = {}
        self.notes: dict[int, Note] = {}
        self.order: list[int] = []
        self.bridge_device: int | None = None
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if "id" in value:
                with self.lock:
                    self.answers[str(value["id"])] = value
                continue
            if value.get("method") != "receive":
                continue
            envelope = (value.get("params") or {}).get("envelope") or {}
            kept = signal_device.note_to_self(envelope, self.number)
            if kept is None or self.bridge_device is None or kept["device"] != self.bridge_device:
                continue
            self._keep(kept)

    def _keep(self, kept: dict[str, Any]) -> None:
        now = time.monotonic()
        if self.debug is not None:
            with self.debug.open("a") as out:
                out.write(json.dumps(kept) + "\n")
        text = checks.strip_mark(kept["text"])
        with self.lock:
            target = kept["edit_of"]
            if target is not None and target in self.notes:
                note = self.notes[target]
                note.text = text
                note.styles = kept["styles"]
                note.edits.append(now)
            else:
                note = Note(kept["timestamp"], now, text, kept["styles"])
                self.notes[kept["timestamp"]] = note
                self.order.append(kept["timestamp"])
            if note.answered_at is None and text and text != WORKING:
                note.answered_at = now

    def request(self, method: str, params: dict[str, Any], timeout: float = 60.0) -> Any:
        """Calls a JSON-RPC method and returns its result.

        Raises:
            RuntimeError: signal-cli answered with an error or not at all.
        """
        assert self.process.stdin is not None
        with self.lock:
            self.counter += 1
            ident = f"r{self.counter}"
        self.process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": params}) + "\n"
        )
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                answer = self.answers.pop(ident, None)
            if answer is not None:
                if "error" in answer:
                    raise RuntimeError(f"signal-cli {method}: {answer['error'].get('message', '')}")
                return answer.get("result")
            time.sleep(0.1)
        raise RuntimeError(f"signal-cli did not answer {method}")

    def find_bridge(self) -> int | None:
        """Finds the bridge's device on the account: the linked device whose name starts "Mightling"."""
        for device in self.request("listDevices", {}) or []:
            if str(device.get("name") or "").startswith("Mightling"):
                self.bridge_device = int(device["id"])
        return self.bridge_device

    def send(self, text: str) -> float:
        """Writes a Note to Self; returns when it was sent."""
        at = time.monotonic()
        self.request("send", {"noteToSelf": True, "message": text})
        return at

    def send_voice(self, path: Path) -> float:
        """Sends the voice-note fixture to Note to Self; returns when it was sent."""
        at = time.monotonic()
        self.request(
            "send", {"noteToSelf": True, "attachments": [str(path)], "voiceNote": True}, timeout=120
        )
        return at

    def since(self, after: float) -> list[Note]:
        """The bridge's notes first seen since `after`, oldest first."""
        with self.lock:
            return [self.notes[t] for t in self.order if self.notes[t].at >= after]

    def last_change(self, after: float) -> float:
        """When the bridge last wrote or edited a note since `after`."""
        times = [max([note.at, *note.edits]) for note in self.since(after)]
        return max(times, default=after)

    def wait(self, predicate: Any, timeout: float) -> bool:
        """Waits until `predicate()` holds or `timeout` passes."""
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline or self.process.poll() is not None:
                return False
            time.sleep(0.5)
        return True

    def wait_answer(
        self, after: float, timeout: float = ANSWER_TIMEOUT, quiet: float = QUIET
    ) -> list[Note]:
        """Waits for an answer since `after`: a note that is not the status line, then `quiet` seconds of calm."""

        def done() -> bool:
            real = [
                n
                for n in self.since(after)
                if n.text and n.text != WORKING and not n.text.startswith("Heard:")
            ]
            return bool(real) and time.monotonic() - self.last_change(after) > quiet

        self.wait(done, timeout)
        return self.since(after)

    def close(self) -> None:
        """Stops signal-cli."""
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()


def first_reply(notes: list[Note], after: float) -> float | None:
    """Seconds from `after` to the first words of the bridge's answer (not its status line)."""
    times = [n.answered_at for n in notes if n.answered_at is not None]
    return min(times) - after if times else None


def answer_of(notes: list[Note]) -> str:
    """The answer's text: the notes other than the status line and "Heard:"."""
    return "\n".join(n.text for n in notes if n.text != WORKING and not n.text.startswith("Heard:"))


class SignalSuite:
    """The Signal checks, one conversation at a time in Note to Self."""

    def __init__(
        self, rpc: SignalRpc, sessions_root: Path, cache: Path, run: Run, queue_night: bool
    ) -> None:
        """Keeps the device, where the owner's rollouts are, and whether `/night add` may really queue."""
        self.rpc = rpc
        self.sessions_root = sessions_root
        self.cache = cache
        self.run = run
        self.queue_night = queue_night
        self.started = time.time()

    def result(
        self,
        check: str,
        severity: str,
        ok: bool | None,
        began: float,
        detail: str,
        latency: float | None = None,
        **data: Any,
    ) -> Result:
        """Records one check (`ok` None means "observed")."""
        status = "observed" if ok is None else ("pass" if ok else "fail")
        return self.run.add(
            Result(
                "signal",
                check,
                severity,
                status,
                round(time.monotonic() - began, 1),
                None if latency is None else round(latency, 1),
                detail,
                data,
            )
        )

    def session_for(self, question: str) -> sessions.Session | None:
        """The thread whose user messages include `question` (only "[ling test]" threads are read)."""
        for session in sessions.recent_sessions(self.sessions_root, self.started - 5, PREFIX):
            if any(question in text for text in session.users):
                return session
        return None

    def ask(self, prompt: str) -> tuple[list[Note], float | None]:
        """Writes a question and waits for its answer."""
        at = self.rpc.send(prompt)
        notes = self.rpc.wait_answer(at)
        return notes, first_reply(notes, at)

    def new_thread(self) -> bool:
        """`/new`, so the test never continues the owner's own thread."""
        at = self.rpc.send("/new")
        return self.rpc.wait(
            lambda: any(n.text.startswith("New thread") for n in self.rpc.since(at)), 60
        )

    def check_rich(self) -> None:
        """The rich answer arrives styled: Signal text styles, no Markdown left over."""
        began = time.monotonic()
        notes, latency = self.ask(RICH_PROMPT)
        text = answer_of(notes)
        if any(mark in text for mark in GATE_BUSY):
            self.result(
                "rich answer (styles)",
                "bridge",
                None,
                began,
                "skipped: the model gate is held",
                latency,
            )
            return
        styles = set().union(*[checks.signal_styles(n.styles) for n in notes]) if notes else set()
        # Tables become monospace lines by design (format.rs); only bold and fences must not stay.
        leftover = [mark for mark in checks.leftover_markdown(text) if mark != "table rule"]
        problems = []
        if not text:
            problems.append("no answer")
        if not styles:
            problems.append("no text styles")
        if leftover:
            problems.append(f"Markdown left over: {leftover}")
        edits = sum(len(n.edits) for n in notes)
        self.result(
            "rich answer (styles)",
            "bridge",
            not problems,
            began,
            "; ".join(problems) or f"styles {sorted(styles)}, {edits} edits",
            latency,
        )

    def check_long_and_stop(self) -> None:
        """A long answer streams by edits; `/stop` while it runs keeps the partial and aborts the turn.

        Streaming means the answer's text grew at least three times within `STREAM_WINDOW` seconds
        of the status line; `/stop` is then sent while the turn still runs.
        """
        began = time.monotonic()
        at = self.rpc.send(LONG_PROMPT)
        self.rpc.wait(lambda: bool(self.rpc.since(at)), 60)
        notes = self.rpc.since(at)
        latency = first_reply(notes, at)
        window_end = (notes[0].at if notes else time.monotonic()) + STREAM_WINDOW

        def finished() -> bool:
            return any(
                len(n.text) > 200 and not n.text.startswith("Working") and not n.edits
                for n in self.rpc.since(at)
            ) or any(
                checks.stopped(n.text) or n.text.startswith("This is taking")
                for n in self.rpc.since(at)
            )

        self.rpc.wait(finished, max(0.0, window_end - time.monotonic()))
        grown = sum(
            1 for n in self.rpc.since(at) for _ in n.edits if not n.text.startswith("Working")
        )
        answer_words = [
            n
            for n in self.rpc.since(at)
            if n.answered_at is not None and not n.text.startswith("Working")
        ]
        streams = bool(answer_words) and grown >= 3
        self.result(
            "long answer streams",
            "bridge",
            streams,
            began,
            f"{grown} answer edits within {STREAM_WINDOW:.0f} s"
            + ("" if streams else "; the answer's words did not stream"),
            latency,
        )
        if finished():
            self.run.add(
                Result(
                    "signal",
                    "/stop ends the answer",
                    "bridge",
                    "skip",
                    None,
                    None,
                    "the answer ended before /stop",
                )
            )
            return
        stop_at = self.rpc.send("/stop")
        kept = self.rpc.wait(lambda: any(checks.stopped(n.text) for n in self.rpc.since(at)), 90)
        session = self.session_for(LONG_PROMPT)
        problems = []
        if not kept:
            problems.append("no message ending (stopped)")
        if not (session and session.aborted):
            problems.append("the session shows no aborted turn" if session else "no session found")
        self.result(
            "/stop ends the answer",
            "bridge",
            not problems,
            stop_at,
            "; ".join(problems) or "partial kept as (stopped), turn aborted",
        )

    def check_hostile(self) -> None:
        """The hostile text comes back as plain text (Signal has no buttons; no link styles exist)."""
        began = time.monotonic()
        notes, latency = self.ask(HOSTILE_PROMPT)
        text = answer_of(notes)
        self.result(
            "hostile answer stays text",
            "bridge",
            bool(text),
            began,
            "answered as text" if text else "no answer",
            latency,
        )

    def check_voice(self) -> None:
        """A voice note is transcribed ("Heard: …") and answered."""
        began = time.monotonic()
        path, _ = voice.ensure_fixture(self.cache)
        at = self.rpc.send_voice(path)
        notes = self.rpc.wait_answer(at)
        heard = [n.text for n in notes if n.text.startswith("Heard:")]
        problems = []
        if not heard:
            problems.append("no Heard: line")
        elif not voice.heard_ok(heard[0]):
            problems.append(f"heard {heard[0][:80]!r}")
        if voice.EXPECTED_ANSWER not in answer_of(notes).lower():
            problems.append("the answer does not say Paris")
        self.result(
            "voice note",
            "bridge",
            not problems,
            began,
            "; ".join(problems) or "heard and answered",
            first_reply(notes, at),
        )

    def check_night(self) -> None:
        """`/night add`: NO cancels; a second add queues it only when allowed (the owner's real queue)."""
        began = time.monotonic()
        at = self.rpc.send(f"/night add {PREFIX} phase 0 probe")
        asked = self.rpc.wait(lambda: any("Night Shift" in n.text for n in self.rpc.since(at)), 90)
        prompt = next((n.text for n in self.rpc.since(at) if "Night Shift" in n.text), "")
        if not asked or "Reply" not in prompt:
            self.result(
                "/night add: NO cancels",
                "bridge",
                False,
                began,
                f"no confirmation ({prompt[:120]!r})",
            )
            return
        no_at = self.rpc.send("NO")
        cancelled = self.rpc.wait(
            lambda: any(n.text.startswith("Not queued") for n in self.rpc.since(no_at)), 60
        )
        self.result(
            "/night add: NO cancels",
            "bridge",
            cancelled,
            began,
            "Not queued." if cancelled else "no 'Not queued.'",
        )
        self.result(
            "approval (YES/NO)",
            "bridge",
            cancelled,
            began,
            "the YES/NO confirmation answered" if cancelled else "the confirmation did not close",
        )
        if not self.queue_night:
            self.run.add(
                Result(
                    "signal",
                    "/night add: queue",
                    "info",
                    "skip",
                    None,
                    None,
                    "not run: it would queue into the owner's real Night Shift (--signal-night-queue)",
                )
            )

    def check_typo(self) -> None:
        """A typo is answered on its best reading, with no question back."""
        began = time.monotonic()
        notes, latency = self.ask(TYPO_PROMPT)
        text = answer_of(notes)
        problems = []
        if checks.clarifying_question(text):
            problems.append("asked a clarifying question")
        if checks.mentions_attachment(text):
            problems.append("talked of an attachment")
        if "unix" not in text.lower():
            problems.append("did not read it as Unix")
        self.result(
            "typo answered on its best reading",
            "model",
            not problems,
            began,
            "; ".join(problems) or "answered",
            latency,
            excerpt=text[:200],
        )

    def check_search(self) -> None:
        """A dated question is searched with ling-search, never curl (2026-10-27's Signal bug)."""
        began = time.monotonic()
        _, latency = self.ask(SEARCH_PROMPT)
        session = self.session_for(SEARCH_PROMPT)
        commands = session.commands if session else []
        curl, search = checks.uses_curl(commands), checks.searched_first(commands)
        detail = (
            "no session found"
            if session is None
            else f"ling-search first: {search}; curl later: {curl}"
        )
        self.result(
            "searches with ling-search, not curl",
            "model",
            session is not None and search,
            began,
            detail,
            latency,
        )

    def run_all(self) -> None:
        """Runs every check in one fresh thread, and leaves a fresh thread for the owner afterwards."""
        began = time.monotonic()
        if self.rpc.find_bridge() is None:
            self.result(
                "bridge device found",
                "bridge",
                False,
                began,
                "no linked device named Mightling on the account",
            )
            return
        if not self.new_thread():
            self.result("/new", "bridge", False, began, "no 'New thread' notice")
            return
        for step in (
            self.check_rich,
            self.check_hostile,
            self.check_voice,
            self.check_night,
            self.check_long_and_stop,
            self.check_typo,
            self.check_search,
        ):
            step_began = time.monotonic()
            try:
                step()
            except Exception as err:  # noqa: BLE001 (any harness error is reported, not raised)
                self.result(
                    step.__name__.removeprefix("check_"),
                    "bridge",
                    False,
                    step_began,
                    f"harness error: {type(err).__name__}: {err}",
                )
        self.new_thread()
        latencies = [
            r.latency for r in self.run.results if r.messenger == "signal" and r.latency is not None
        ]
        middle = checks.median(latencies)
        self.run.add(
            Result(
                "signal",
                "first-reply latency",
                "info",
                "observed",
                None,
                None if middle is None else round(middle, 1),
                f"median of {len(latencies)} questions" if latencies else "no reply timed",
            )
        )
