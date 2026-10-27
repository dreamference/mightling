"""The harness's Signal device: a second signal-cli device linked to the owner's account.

It reuses the signal-cli and Java runtime `ling signal setup` pins under /opt/mightling, with its
own data folder (`~/.config/dreamference/signal-live/`, 0700). The live bridge answers only in
Note to Self, so the harness writes Note to Self messages, each starting "[ling test]", and reads
back only Note to Self: `note_to_self` drops every other envelope where it arrives, unread and
unwritten. signal-cli runs with attachments, stories, avatars and stickers ignored.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any, Final

from .telegram_account import NeedsOwner, write_qr

SIGNAL_CLI_VERSION: Final = "0.14.9"
JAVA_VERSION: Final = "25.0.4.1+1"
OPT_DIR: Final = Path("/opt/mightling")
DEVICE_NAME: Final = "ling test"
PREFIX: Final = "[ling test]"


def signal_cli_program() -> Path:
    """The pinned signal-cli `ling signal setup` installed."""
    return OPT_DIR / f"signal-cli-{SIGNAL_CLI_VERSION}" / "bin" / "signal-cli"


def signal_cli_env(arch: str | None = None) -> dict[str, str]:
    """signal-cli's environment, as the bridge's `setup::signal_cli_env` builds it."""
    env = dict(os.environ)
    env["JAVA_HOME"] = str(OPT_DIR / f"jdk-{JAVA_VERSION}-jre")
    if (arch or platform.machine()) == "aarch64":
        env["JAVA_OPTS"] = (
            f"-Djava.library.path={OPT_DIR / f'signal-cli-{SIGNAL_CLI_VERSION}' / 'lib' / 'native'}"
        )
    return env


def base_command(data: Path) -> list[str]:
    """signal-cli on the harness's own data folder."""
    return [str(signal_cli_program()), "--config", str(data)]


def associated_number(line: str) -> str | None:
    """The number in signal-cli's `Associated with: +…` line after a link, or None."""
    marker = "Associated with:"
    if marker not in line:
        return None
    rest = line.split(marker, 1)[1].split()
    number = rest[0] if rest else ""
    return number if number.startswith("+") and number[1:].isdigit() else None


def link(data: Path, qr_png: Path, minutes: float) -> str:
    """Links a new device, rewriting the QR code PNG each time signal-cli's link times out.

    Returns:
        The account's number.

    Raises:
        NeedsOwner: Not scanned in time.
    """
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        child = subprocess.Popen(
            [*base_command(data), "link", "-n", DEVICE_NAME],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=signal_cli_env(),
        )
        assert child.stdout is not None
        number = None
        for line in child.stdout:
            line = line.strip()
            if line.startswith("sgnl://"):
                write_qr(line, qr_png)
            elif associated_number(line):
                number = associated_number(line)
        child.wait()
        if number:
            if qr_png.exists():
                qr_png.unlink()
            return number
    raise NeedsOwner("owner re-link needed: the Signal QR code was not scanned in time")


def own_number(data: Path) -> str | None:
    """The linked account's number, from signal-cli's account list, or None when not linked."""
    out = subprocess.run(
        [*base_command(data), "-o", "json", "listAccounts"],
        capture_output=True,
        text=True,
        env=signal_cli_env(),
        timeout=120,
    )
    try:
        accounts = json.loads(out.stdout or "[]")
    except ValueError:
        return None
    for account in accounts:
        number = account.get("number")
        if number:
            return number
    return None


def note_to_self(envelope: dict[str, Any], number: str) -> dict[str, Any] | None:
    """Keeps only Note to Self: a message the account sent to itself, from any of its devices.

    Args:
        envelope: One `receive` result's `envelope`.
        number: The account's own number.

    Returns:
        `{"timestamp", "text", "edit_of", "styles", "quote", "device", "attachments"}` for a Note to
        Self message or edit, None for everything else (which the caller drops unread).
    """
    if envelope.get("sourceNumber") != number and envelope.get("source") != number:
        return None
    sent = (envelope.get("syncMessage") or {}).get("sentMessage")
    edit_of = None
    if not isinstance(sent, dict):
        return None
    destination = sent.get("destinationNumber") or sent.get("destination")
    if destination != number:
        return None
    message = sent
    edit = sent.get("editMessage")
    if isinstance(edit, dict):
        edit_of = edit.get("targetSentTimestamp")
        message = edit.get("dataMessage") or {}
    return {
        "timestamp": sent.get("timestamp") or envelope.get("timestamp"),
        "text": message.get("message") or "",
        "edit_of": edit_of,
        "styles": message.get("textStyles") or [],
        "quote": (message.get("quote") or {}).get("id"),
        "device": envelope.get("sourceDevice"),
        "attachments": len(message.get("attachments") or []),
    }
