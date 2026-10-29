"""The one line `ling-admin` shows for a failure an ordinary mistake or a missing dependency causes."""

import json
import subprocess
import urllib.error

ORDINARY_ERRORS = (OSError, subprocess.SubprocessError, json.JSONDecodeError)

_DOCKER = "docker"
_UNREACHABLE = (
    "Cannot reach the server this command talks to. If it is the model server, "
    "start it with `ling-admin server start` and check `ling-admin status`."
)
_TOOLS_NEEDING_INSTALL = ("git", "ssh", "sudo", "systemctl", "nvidia-smi")


def _command_text(command: object) -> str:
    return command if isinstance(command, str) else " ".join(str(part) for part in command)  # type: ignore[attr-defined]


def _missing_file(error: FileNotFoundError) -> str:
    name = error.filename
    if name == _DOCKER:
        return (
            "`docker` is not installed or not on PATH. Install Docker "
            "(the model server and the services run in containers), then retry."
        )
    if name in _TOOLS_NEEDING_INSTALL:
        return f"`{name}` is not installed or not on PATH."
    return (
        f"No such file or directory: {name}"
        if name
        else "A file this command needs does not exist."
    )


def _os_error(error: OSError) -> str | None:
    if isinstance(error, FileNotFoundError):
        return _missing_file(error)
    if isinstance(error, PermissionError):
        return f"Permission denied: {error.filename}" if error.filename else "Permission denied."
    if isinstance(error, (ConnectionError, urllib.error.URLError)):
        return _UNREACHABLE
    if error.errno:
        return f"{error.strerror}: {error.filename}" if error.filename else str(error.strerror)
    return None


def describe_error(error: BaseException) -> str | None:
    """Gives the line to show after the ❌ for an error an ordinary mistake raises, or None for a bug.

    A bug keeps its traceback.
    """
    if isinstance(error, OSError):
        return _os_error(error)
    if isinstance(error, subprocess.CalledProcessError):
        return f"`{_command_text(error.cmd)}` failed (exit {error.returncode})."
    if isinstance(error, subprocess.TimeoutExpired):
        return f"`{_command_text(error.cmd)}` timed out after {error.timeout} s."
    if isinstance(error, json.JSONDecodeError):
        return f"A file or reply that should be JSON is not: {error}"
    return None
