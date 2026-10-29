"""Where the forge keeps its data and secrets, whether it is on, and which role this node plays.

The spec: specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §0, §2.3, §2.5.

The switch is `[forge] enabled` in the node's own config (`~/.config/dreamference/config.toml`,
never a repository's `dreamference.toml`), overridden by `DREAMFERENCE_FORGE`. The role
(`record` or `mirror`) and the forge of record's node id are decided once, at the first
`node enable`, and kept in `~/.config/dreamference/forge/state.json`: a browse that misses the
forge of record later never turns a mirror into a second writable forge.
"""

import json
import os
import tomllib
from pathlib import Path
from typing import Any, Final

# Forgejo's long-term-support line on 2026-10-27 (v15, supported until 2027-07), rootless so the
# data folder belongs to the node's own user. Pinned by the index digest, as every image
# Mightling runs (specs/DREAMFERENCE_DOCKER.md).
FORGE_IMAGE: Final[str] = (
    "codeberg.org/forgejo/forgejo:15.0.11-rootless"
    "@sha256:3cd6bc2b74c87cbde0a0fd527c9665cb416f5a0540dee54fe5afb3fa2df9f40c"
)
CONTAINER: Final[str] = "mightling-forge"
NETWORK: Final[str] = "mightling-forge"
UNIT: Final[str] = "mightling-forge.service"
# The ports on the host: HTTP for the UI, the API and git, and Forgejo's own SSH server (the
# host's sshd is never touched). Inside the rootless image they are 3000 and 2222.
HTTP_PORT: Final[int] = 3200
SSH_PORT: Final[int] = 2222
CONTAINER_HTTP_PORT: Final[int] = 3000
# Browsers reach the forge through `ling web`'s `/forge/` proxy, signed in (§2.2); its port and
# that path are the forge's `ROOT_URL`. Git and the API also answer on HTTP_PORT, at its root.
WEB_PORT: Final[int] = 3100
WEB_PATH: Final[str] = "/forge/"
CONTAINER_SSH_PORT: Final[int] = 2222
# The cap counted by the model server's pre-load memory check (§2.6).
MEMORY_CAP_GIB: Final[float] = 1.0
# Below the default share of 1024: the model server keeps the CPU when both want it.
CPU_SHARES: Final[int] = 256
SERVICE_TYPE: Final[str] = "_mightling-forge._tcp"
ROLE_RECORD: Final[str] = "record"
ROLE_MIRROR: Final[str] = "mirror"
ROLES: Final[tuple] = (ROLE_RECORD, ROLE_MIRROR)
# What `DREAMFERENCE_FORGE` may say to turn the forge off.
OFF_WORDS: Final[tuple] = ("0", "false", "no", "off")
FORGE_TABLE: Final[str] = "[forge]"
ENABLED_KEY: Final[str] = "enabled"
# The owner's and the maintenance accounts (§2.2), and the read-only account mirrors pull with.
OWNER: Final[str] = "owner"
ADMIN: Final[str] = "forge-admin"
MIRROR_ACCOUNT: Final[str] = "mirror"
PASSWORD_LENGTH: Final[int] = 24
# The storage budget on the forge of record (§5.2): what the forge may use in all, and the free
# space it keeps above the node's floor before it prunes.
BUDGET_GB: Final[float] = 10.0
REPOSITORY_ALARM_GB: Final[float] = 1.0
DISK_FLOOR_GB: Final[float] = 150.0
NIGHT_DISK_FLOOR_GB: Final[float] = 175.0
PRUNE_MARGIN_GB: Final[float] = 10.0
ARTEFACT_DAYS_RECORD: Final[int] = 7
ARTEFACT_DAYS_MIRROR: Final[int] = 14
# Backups kept on the receiving nodes (§6).
DAILY_KEPT: Final[int] = 7
WEEKLY_KEPT: Final[int] = 4
BACKUP_COPIES: Final[int] = 2


class ForgeSettings:
    """The forge's paths, switch and role."""

    @classmethod
    def data_dir(cls) -> Path:
        """Return the forge's data folder.

        Returns:
            Path: `~/.local/share/dreamference/forge/data`, mounted as the container's
            `/var/lib/gitea` (repositories, the SQLite database, `app.ini`).
        """
        return Path("~/.local/share/dreamference/forge/data").expanduser()

    @classmethod
    def share_dir(cls) -> Path:
        """Return the folder that holds everything the forge keeps on this node.

        Returns:
            Path: `~/.local/share/dreamference/forge`: the data folder, the GitHub mirror's
            work folder, local dumps and the backups other nodes sent.
        """
        return cls.data_dir().parent

    @classmethod
    def make_private_dirs(cls) -> Path:
        """Create the share and data folders 0700 and keep them so, whatever the umask.

        They hold the SQLite database and the private repositories, which other local users
        must not read, as the secrets and the backups already are not.

        Returns:
            Path: The data folder.
        """
        data = cls.data_dir()
        data.mkdir(parents=True, exist_ok=True)
        for folder in (cls.share_dir(), data):
            folder.chmod(0o700)
        return data

    @classmethod
    def secrets_dir(cls) -> Path:
        """Return the folder of the forge's secrets.

        Returns:
            Path: `~/.config/dreamference/forge` (0700): passwords, tokens and the state file.
        """
        return Path("~/.config/dreamference/forge").expanduser()

    @classmethod
    def secret_path(cls, name: str) -> Path:
        """Return a secret's path in the secrets folder.

        Args:
            name: The secret's file name (`owner-password`, `admin-token`, …).

        Returns:
            Path: Its path in the secrets folder.
        """
        return cls.secrets_dir() / name

    @classmethod
    def read_secret(cls, name: str) -> str | None:
        """Read a secret from the secrets folder.

        Args:
            name: The secret's file name.

        Returns:
            str | None: The secret, or None when it was never written.
        """
        try:
            value = cls.secret_path(name).read_text().strip()
        except OSError:
            return None
        return value or None

    @classmethod
    def write_secret(cls, name: str, value: str) -> Path:
        """Write a secret 0600 in a 0700 folder, never through a world-readable moment.

        Args:
            name: The secret's file name.
            value: The secret.

        Returns:
            Path: Where it was written.
        """
        folder = cls.secrets_dir()
        folder.mkdir(parents=True, exist_ok=True)
        Path(folder).chmod(0o700)
        path = folder / name
        staging = folder / f".{name}.{os.getpid()}.tmp"
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(value + "\n")
        Path(staging).replace(path)
        return path

    @classmethod
    def config_path(cls) -> Path:
        """Return the node's own config, `~/.config/dreamference/config.toml`.

        Returns:
            Path: The node's own config, `~/.config/dreamference/config.toml`.
        """
        return Path("~/.config/dreamference/config.toml").expanduser()

    @classmethod
    def enabled(cls) -> bool:
        """Tell whether the forge is on for this node.

        Returns:
            bool: Whether the forge is on for this node: `DREAMFERENCE_FORGE` when set, else
            `[forge] enabled` in the node's config, else True (on by default, O1).
        """
        override = os.environ.get("DREAMFERENCE_FORGE", "").strip().lower()
        if override:
            return override not in OFF_WORDS
        try:
            table = tomllib.loads(cls.config_path().read_text()).get("forge", {})
        except (OSError, tomllib.TOMLDecodeError):
            return True
        return not (isinstance(table, dict) and table.get("enabled") is False)

    @classmethod
    def set_enabled(cls, enabled: bool) -> None:
        """Write `[forge] enabled` into the node's config, keeping the rest of the file as it is.

        Args:
            enabled: The new value.
        """
        path = cls.config_path()
        try:
            lines = path.read_text().splitlines()
        except OSError:
            lines = []
        value = "true" if enabled else "false"
        out, in_forge, written = [], False, False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                if in_forge and not written:
                    out.append(f"enabled = {value}")
                    written = True
                # `[ forge ]` and `[forge]  # a comment` are the same table to TOML.
                in_forge = "".join(stripped.split("#", 1)[0].split()) == FORGE_TABLE
            elif in_forge and stripped.split("=", 1)[0].strip() == ENABLED_KEY:
                if not written:
                    out.append(f"enabled = {value}")
                    written = True
                continue
            out.append(line)
        if in_forge and not written:
            out.append(f"enabled = {value}")
            written = True
        if not written:
            out += ([""] if out else []) + ["[forge]", f"enabled = {value}"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(out) + "\n")

    @classmethod
    def state_path(cls) -> Path:
        """Return the path of the forge's state file.

        Returns:
            Path: `~/.config/dreamference/forge/state.json`.
        """
        return cls.secret_path("state.json")

    @classmethod
    def state(cls) -> dict[str, Any]:
        """Read the forge's state file.

        Returns:
            dict[str, Any]: `role` (`record`, `mirror` or absent before the first setup),
            `record_node` and `record_name` (the forge of record, for a mirror), `previous_record`
            (the node a `move` came from, for `move --back`), `frozen`, and what a run wrote.
        """
        try:
            data = json.loads(cls.state_path().read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def update_state(cls, **changes: object) -> dict[str, Any]:
        """Merge changes into the state file; a value of None removes its key.

        Args:
            **changes: The keys to set.

        Returns:
            dict[str, Any]: The new state.
        """
        state = cls.state()
        for key, value in changes.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        cls.write_secret("state.json", json.dumps(state, indent=2, sort_keys=True))
        return state

    @classmethod
    def role(cls) -> str | None:
        """Return this node's role in the forge.

        Returns:
            str | None: `record`, `mirror`, or None before the forge was ever set up here.
        """
        role = cls.state().get("role")
        return role if role in ROLES else None

    @classmethod
    def disk_floor_gb(cls, night: bool) -> float:
        """Return the free space the node keeps on its disk.

        Args:
            night: Whether a SWE-bench night runs on this node.

        Returns:
            float: The free space the node keeps (§5.2): 175 GB during a night, 150 GB otherwise.
        """
        return NIGHT_DISK_FLOOR_GB if night else DISK_FLOOR_GB
