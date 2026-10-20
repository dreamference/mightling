"""The node's SSH tunnel to the box (REMOTE_ACCESS §2, §3, §14.3 item 4).

The node has no public address and opens no port: a user unit, `mightling-remote-tunnel.service`,
dials the box and forwards the box's `127.0.0.1:10443` back to the control plane's loopback port,
where the box's SNI router hands it the connections for the box's own name. systemd restarts it
after any failure, which is the retry loop `autossh` would have been.

It logs in as `mightling-tunnel`, a box account whose only key is the one made here and whose
`authorized_keys` line allows forwarding that one port and nothing else; the owner's own key is
never used by the unit. The box's host key is pinned on first use into a file of its own and
checked strictly afterwards.
"""

import shlex
from pathlib import Path

from dreamference.remote.remote_control_plane import PORT
from dreamference.remote.remote_executor import RemoteExecutor
from dreamference.remote.remote_state import RemoteState

UNIT = "mightling-remote-tunnel.service"
TUNNEL_USER = "mightling-tunnel"
BOX_TUNNEL_PORT = 10443
KEY_COMMENT = "mightling-remote-tunnel"
SSH = "/usr/bin/ssh"
KEEPALIVE_S = 15
KEEPALIVE_COUNT = 3
RESTART_S = 10


class RemoteTunnel:
    """The tunnel's key and its user unit."""

    @classmethod
    def ensure_key(cls) -> bool:
        """Makes the tunnel's key once.

        Returns:
            bool: Whether the key exists afterwards.
        """
        key = RemoteState.tunnel_key()
        if key.is_file():
            return True
        RemoteState.ensure_dir(key.parent)
        RemoteExecutor.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", KEY_COMMENT, "-f", str(key)]
        )
        return key.is_file()

    @classmethod
    def public_key(cls) -> str:
        """The tunnel key's public half, for the box's `authorized_keys`.

        Returns:
            str: One `ssh-ed25519 …` line, or "" before the key exists.
        """
        try:
            return Path(f"{RemoteState.tunnel_key()}.pub").read_text(encoding="ascii").strip()
        except OSError:
            return ""

    @classmethod
    def authorized_line(cls) -> str:
        """The box's `authorized_keys` line for the tunnel account.

        Returns:
            str: The key, restricted to forwarding the one port.
        """
        options = f'restrict,port-forwarding,permitlisten="127.0.0.1:{BOX_TUNNEL_PORT}",command="/bin/false"'
        return f"{options} {cls.public_key()}"

    @classmethod
    def ssh_command(cls, dns_name: str) -> list[str]:
        """The tunnel's `ssh`.

        Args:
            dns_name: The box's name, resolved by DNS each time it connects.

        Returns:
            list[str]: The argv.
        """
        options = {
            "IdentitiesOnly": "yes",
            "BatchMode": "yes",
            "ExitOnForwardFailure": "yes",
            "ServerAliveInterval": str(KEEPALIVE_S),
            "ServerAliveCountMax": str(KEEPALIVE_COUNT),
            "StrictHostKeyChecking": "yes",
            "UserKnownHostsFile": str(RemoteState.known_hosts()),
            "GlobalKnownHostsFile": "/dev/null",
        }
        argv = [SSH, "-N", "-T", "-i", str(RemoteState.tunnel_key())]
        for key, value in options.items():
            argv += ["-o", f"{key}={value}"]
        return [
            *argv,
            "-R",
            f"127.0.0.1:{BOX_TUNNEL_PORT}:127.0.0.1:{PORT}",
            f"{TUNNEL_USER}@{dns_name}",
        ]

    @classmethod
    def unit_text(cls, dns_name: str) -> str:
        """The user unit.

        Args:
            dns_name: The box's name.

        Returns:
            str: The unit file's text.
        """
        return (
            "# Written by `ling-admin remote setup` (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2).\n"
            f"[Unit]\nDescription=Mightling remote access: the tunnel to {dns_name}\n"
            "Wants=network-online.target\nAfter=network-online.target\n\n"
            f"[Service]\nExecStart={shlex.join(cls.ssh_command(dns_name))}\n"
            f"Restart=always\nRestartSec={RESTART_S}\n\n"
            "[Install]\nWantedBy=default.target\n"
        )

    @classmethod
    def unit_path(cls) -> Path:
        """Where the unit is written.

        Returns:
            Path: `~/.config/systemd/user/mightling-remote-tunnel.service`.
        """
        return Path.home() / ".config" / "systemd" / "user" / UNIT

    @classmethod
    def install(cls, dns_name: str) -> bool:
        """Writes the unit and (re)starts it.

        Args:
            dns_name: The box's name.

        Returns:
            bool: Whether systemd took it.
        """
        path = cls.unit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cls.unit_text(dns_name), encoding="utf-8")
        steps = [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", UNIT],
            ["systemctl", "--user", "restart", UNIT],
        ]
        return all(RemoteExecutor.ok(step) for step in steps)

    @classmethod
    def remove(cls) -> None:
        """Stops the tunnel and deletes its unit."""
        RemoteExecutor.run(["systemctl", "--user", "disable", "--now", UNIT])
        cls.unit_path().unlink(missing_ok=True)
        RemoteExecutor.run(["systemctl", "--user", "daemon-reload"])

    @classmethod
    def state(cls) -> str:
        """The unit's state.

        Returns:
            str: `active`, `activating`, `failed`, … as systemd says, or `not installed`.
        """
        if not cls.unit_path().is_file():
            return "not installed"
        result = RemoteExecutor.run(["systemctl", "--user", "is-active", UNIT])
        return result.stdout.strip() or "unknown"
