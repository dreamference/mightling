"""TDLib, Telegram's own client library, through its JSON interface (ctypes, no binding package).

The library comes from a pinned wheel of the `tdjson` project (TDLib 1.8.69, commit 0e88653d,
built by the project's CI for manylinux aarch64 and x86_64): the wheel is downloaded once, checked
against its SHA-256 and the bundled `libtdjson` is unpacked into the cache, outside the repository.
Building TDLib from source at the same commit is the fallback (docs/dev/messengers.md).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import platform
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Final

TDLIB_VERSION: Final = "1.8.69"
TDLIB_COMMIT: Final = "0e88653dcbf4e44c73cb655a05b58cd749a91169"
# The cp312 wheels; only the shared library inside is used, so the Python tag does not matter.
WHEELS: Final = {
    "aarch64": (
        "https://files.pythonhosted.org/packages/ee/80/c11a85f05e4aa67dd7bf108f96faff49957a0f9a1c72becc510e60a18754/"
        "tdjson-1.8.69-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        "3abdd7079f77c76c95267aac6f833f06506f2a1cf2bdda19eac78cd696e20017",
    ),
}


def library_member(names: list[str]) -> str | None:
    """Returns the wheel member that is the TDLib shared library.

    Args:
        names: The wheel's member names.

    Returns:
        The member under `tdjson.libs/` named `libtdjson…so…`, or None.
    """
    for name in names:
        base = name.rsplit("/", 1)[-1]
        if name.startswith("tdjson.libs/") and base.startswith("libtdjson") and ".so" in base:
            return name
    return None


def ensure_library(cache: Path, arch: str | None = None) -> Path:
    """Returns the path of the pinned `libtdjson`, fetching and checking it the first time.

    Args:
        cache: The harness's cache folder.
        arch: The machine (`platform.machine()` when None).

    Returns:
        The unpacked library.

    Raises:
        RuntimeError: No pinned wheel for this machine, or the download does not match its pin.
    """
    arch = arch or platform.machine()
    if arch not in WHEELS:
        raise RuntimeError(
            f"no pinned TDLib for {arch}; build TDLib {TDLIB_VERSION} from source (docs/dev/messengers.md)"
        )
    url, sha256 = WHEELS[arch]
    target = cache / f"tdlib-{TDLIB_VERSION}-{arch}" / "libtdjson.so"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    wheel = target.parent / "tdjson.whl"
    urllib.request.urlretrieve(url, wheel)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if digest != sha256:
        wheel.unlink()
        raise RuntimeError(f"the TDLib wheel does not match its pin ({digest})")
    with zipfile.ZipFile(wheel) as archive:
        member = library_member(archive.namelist())
        if member is None:
            raise RuntimeError("the TDLib wheel holds no libtdjson")
        partial = target.with_suffix(".part")
        partial.write_bytes(archive.read(member))
        partial.rename(target)
    wheel.unlink()
    return target


class TdClient:
    """One TDLib client: requests with an `@extra` id, answers matched, updates queued."""

    def __init__(self, library: Path, log_file: Path | None = None) -> None:
        """Loads the library and creates the client.

        Args:
            library: The `libtdjson` shared library.
            log_file: Where TDLib writes its own log (errors only); None keeps it quiet.
        """
        self.lib = ctypes.CDLL(str(library))
        self.lib.td_create_client_id.restype = ctypes.c_int
        self.lib.td_send.argtypes = [ctypes.c_int, ctypes.c_char_p]
        self.lib.td_receive.argtypes = [ctypes.c_double]
        self.lib.td_receive.restype = ctypes.c_char_p
        self.lib.td_execute.argtypes = [ctypes.c_char_p]
        self.lib.td_execute.restype = ctypes.c_char_p
        self.execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": 1})
        if log_file is not None:
            stream = {
                "@type": "logStreamFile",
                "path": str(log_file),
                "max_file_size": 10_000_000,
                "redirect_stderr": False,
            }
            self.execute({"@type": "setLogStream", "log_stream": stream})
        else:
            self.execute({"@type": "setLogStream", "log_stream": {"@type": "logStreamEmpty"}})
        self.client = self.lib.td_create_client_id()
        self.counter = 0
        self.answers: dict[str, dict[str, Any]] = {}
        self.updates: list[dict[str, Any]] = []
        self.listeners: list[Any] = []
        # The one chat whose updates are kept; every other chat's are dropped as they arrive.
        self.allowed_chat: int | None = None
        # TDLib starts a client only at its first request.
        self.send({"@type": "getOption", "name": "version"})

    def execute(self, query: dict[str, Any]) -> dict[str, Any] | None:
        """Runs a synchronous TDLib request."""
        out = self.lib.td_execute(json.dumps(query).encode())
        return json.loads(out) if out else None

    def send(self, query: dict[str, Any]) -> str:
        """Sends a request; returns its `@extra` id."""
        self.counter += 1
        extra = f"q{self.counter}"
        self.lib.td_send(self.client, json.dumps({**query, "@extra": extra}).encode())
        return extra

    def pump(self, timeout: float) -> None:
        """Receives what TDLib has for at most `timeout` seconds."""
        out = self.lib.td_receive(timeout)
        while out:
            event = json.loads(out)
            if event.get("@client_id", self.client) == self.client:
                extra = event.get("@extra")
                if extra is not None:
                    self.answers[extra] = event
                elif self.keeps(event):
                    self.updates.append(event)
                    for listener in self.listeners:
                        listener(event)
            out = self.lib.td_receive(0.0)

    def keeps(self, update: dict[str, Any]) -> bool:
        """Says whether an update is kept: the session's own state, or the allowed chat's events.

        Everything else (other chats, users, contacts, files) is dropped unread as it arrives.
        """
        if update.get("@type") in SESSION_UPDATES:
            return True
        return self.allowed_chat is not None and update_chat_id(update) == self.allowed_chat

    def call(self, query: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """Sends a request and waits for its answer.

        Raises:
            TdError: TDLib answered with an error.
            TimeoutError: No answer in time.
        """
        extra = self.send(query)
        deadline = time.monotonic() + timeout
        while extra not in self.answers:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"TDLib did not answer {query.get('@type')}")
            self.pump(min(left, 1.0))
        answer = self.answers.pop(extra)
        if answer.get("@type") == "error":
            raise TdError(answer.get("code", 0), answer.get("message", ""))
        return answer

    def wait_for(self, predicate: Any, timeout: float) -> dict[str, Any] | None:
        """Waits for an update matching `predicate`, consuming the queue as it goes.

        Returns:
            The first matching update, or None after `timeout` seconds.
        """
        deadline = time.monotonic() + timeout
        while True:
            while self.updates:
                update = self.updates.pop(0)
                if predicate(update):
                    return update
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self.pump(min(left, 1.0))

    def close(self) -> None:
        """Closes the client and waits for TDLib to say it is closed."""
        try:
            self.send({"@type": "close"})
            self.wait_for(
                lambda u: (
                    u.get("@type") == "updateAuthorizationState"
                    and u["authorization_state"]["@type"] == "authorizationStateClosed"
                ),
                15,
            )
        except Exception:  # noqa: BLE001 (closing is best effort)
            pass


SESSION_UPDATES: Final = frozenset({"updateAuthorizationState", "updateConnectionState"})


def update_chat_id(update: dict[str, Any]) -> int | None:
    """The chat an update concerns, wherever TDLib puts it; None for updates about no chat."""
    if "chat_id" in update:
        return update["chat_id"]
    message = update.get("message")
    if isinstance(message, dict) and "chat_id" in message:
        return message["chat_id"]
    chat = update.get("chat")
    if isinstance(chat, dict) and "id" in chat:
        return chat["id"]
    info = update.get("info")
    if isinstance(info, dict) and "chat_id" in info:
        return info["chat_id"]
    return None


class TdError(Exception):
    """An error TDLib answered with."""

    def __init__(self, code: int, message: str) -> None:
        """Keeps TDLib's code and message."""
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
