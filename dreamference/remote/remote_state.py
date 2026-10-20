"""Where remote access keeps its files on the node, and what it remembers (REMOTE_ACCESS §2, §3).

Everything lives under `~/.config/dreamference/remote/`, a folder only the user can read: the CA
(`ca/`, its key 0600), the control plane's configuration and certificate (`server/`), the relay's
certificate for the box (`relay/`), the tunnel's own SSH key and the box's pinned host key, and
`remote.json` with the names, the relay's secret, the store's key and the administrator token.
Nothing in it is an address: the box is its DNS name, every peer a NetBird name.

Paths are resolved at call time, so a test's own home folder is used.
"""

import json
import os
from pathlib import Path

STATE_FILE = "remote.json"
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class RemoteState:
    """The node's remote-access folder and `remote.json`."""

    @classmethod
    def base_dir(cls) -> Path:
        """`~/.config/dreamference/remote`.

        Returns:
            Path: The folder.
        """
        return Path.home() / ".config" / "dreamference" / "remote"

    @classmethod
    def ca_dir(cls) -> Path:
        """The CA's folder.

        Returns:
            Path: `remote/ca`.
        """
        return cls.base_dir() / "ca"

    @classmethod
    def server_dir(cls) -> Path:
        """The control plane's folder, mounted read-only into its container.

        Returns:
            Path: `remote/server`.
        """
        return cls.base_dir() / "server"

    @classmethod
    def relay_dir(cls) -> Path:
        """The relay's certificate and key, which `remote setup` copies to the box.

        Returns:
            Path: `remote/relay`.
        """
        return cls.base_dir() / "relay"

    @classmethod
    def tunnel_key(cls) -> Path:
        """The tunnel's own SSH key: the box lets it forward one port and nothing else.

        Returns:
            Path: `remote/tunnel_ed25519`.
        """
        return cls.base_dir() / "tunnel_ed25519"

    @classmethod
    def known_hosts(cls) -> Path:
        """The box's host key, pinned on first use.

        Returns:
            Path: `remote/known_hosts`.
        """
        return cls.base_dir() / "known_hosts"

    @classmethod
    def ensure_dir(cls, path: Path) -> Path:
        """Makes a folder only the user can read.

        Args:
            path: The folder.

        Returns:
            Path: The same folder.
        """
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(PRIVATE_DIR_MODE)
        return path

    @classmethod
    def write_private(cls, path: Path, text: str) -> None:
        """Writes a file readable only by the user, replacing it whole.

        Args:
            path: The file.
            text: Its contents.
        """
        cls.ensure_dir(path.parent)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        staging.replace(path)

    @classmethod
    def load(cls) -> dict[str, object]:
        """Reads `remote.json`.

        Returns:
            dict[str, object]: What is remembered; empty when remote access was never set up.
        """
        try:
            value = json.loads((cls.base_dir() / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @classmethod
    def save(cls, state: dict[str, object]) -> None:
        """Writes `remote.json` (0600).

        Args:
            state: What to remember.
        """
        cls.write_private(
            cls.base_dir() / STATE_FILE, json.dumps(state, indent=2, sort_keys=True) + "\n"
        )

    @classmethod
    def text(cls, state: dict[str, object], key: str) -> str:
        """A string member of the state.

        Args:
            state: The state.
            key: The member.

        Returns:
            str: Its value, or "" when absent or not a string.
        """
        value = state.get(key)
        return value if isinstance(value, str) else ""

    @classmethod
    def section(cls, state: dict[str, object], key: str) -> dict[str, object]:
        """An object member of the state.

        Args:
            state: The state.
            key: The member.

        Returns:
            dict[str, object]: Its value, or an empty dict when absent or not an object.
        """
        value = state.get(key)
        return value if isinstance(value, dict) else {}
