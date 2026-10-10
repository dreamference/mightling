"""
The reverse tunnel from the node to the box (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2, §3).

The node has no public address and opens no port: it dials out. A user unit,
`mightling-remote-tunnel.service`, keeps one `ssh -N -R` to the box's tunnel account, which may do
nothing but listen on the box's loopback port that haproxy forwards the control plane's server name
to. The tunnel has a key of its own (`tunnel_ed25519`, made here, used for nothing else) and a
known-hosts file of its own, filled from the box's host key read over the user's own SSH connection
at setup, never by a first-use scan. systemd restarts it when it drops; `ExitOnForwardFailure` makes
a listener that could not be set up a failure, not a silent half-tunnel.

`systemctl` and `ssh-keygen` go through `_run`, the seam the tests replace.
"""

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from dreamference.remote.remote_settings import (
    BOX_TUNNEL_PORT,
    BOX_TUNNEL_USER,
    CONTROL_PLANE_PORT,
    TUNNEL_UNIT,
    RemoteSettings,
)

KEY_COMMENT = "mightling-remote-tunnel"


class RemoteTunnel:
    """The tunnel's key, known-hosts file and user unit."""

    @classmethod
    def _run(cls, argv: List[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(argv, 127, "", str(error))

    @classmethod
    def key(cls) -> Path:
        """
        Returns:
            Path: The tunnel's private key.
        """
        return RemoteSettings.path("tunnel_ed25519")

    @classmethod
    def known_hosts(cls) -> Path:
        """
        Returns:
            Path: The tunnel's own known-hosts file: the box's host key and nothing else.
        """
        return RemoteSettings.path("known_hosts")

    @classmethod
    def unit_path(cls) -> Path:
        """
        Returns:
            Path: Where the user unit is written.
        """
        return Path.home() / ".config" / "systemd" / "user" / TUNNEL_UNIT

    @classmethod
    def public_key(cls) -> Optional[str]:
        """
        Makes the tunnel's key if there is none.

        Returns:
            Optional[str]: The public key's line, or None when ssh-keygen failed.
        """
        if not cls.key().is_file():
            cls.key().parent.mkdir(parents=True, exist_ok=True)
            made = cls._run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", KEY_COMMENT, "-f", str(cls.key())])
            if made.returncode != 0:
                return None
        try:
            return Path(str(cls.key()) + ".pub").read_text().strip()
        except OSError:
            return None

    @classmethod
    def authorized_line(cls, public_key: str) -> str:
        """
        Args:
            public_key: The tunnel's public key.

        Returns:
            str: The box's `authorized_keys` line: a remote listener on the box's loopback tunnel
            port and nothing else (no shell, no TTY, no other forwarding).
        """
        return (f'restrict,port-forwarding,permitlisten="127.0.0.1:{BOX_TUNNEL_PORT}",'
                f'command="/usr/sbin/nologin" {public_key}')

    @classmethod
    def write_known_host(cls, dns_name: str, host_key: str) -> None:
        """
        Args:
            dns_name: The box's public DNS name.
            host_key: The box's host public key line (`ssh-ed25519 AAAA…`).
        """
        fields = host_key.split()
        RemoteSettings.write_private("known_hosts", f"{dns_name} {fields[0]} {fields[1]}\n")

    @classmethod
    def render_unit(cls, dns_name: str) -> str:
        """
        Args:
            dns_name: The box's public DNS name.

        Returns:
            str: The user unit's text.
        """
        ssh = shutil.which("ssh") or "/usr/bin/ssh"
        command = " ".join([
            ssh, "-N", "-i", str(cls.key()), "-F", "/dev/null",
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={cls.known_hosts()}",
            "-R", f"127.0.0.1:{BOX_TUNNEL_PORT}:127.0.0.1:{CONTROL_PLANE_PORT}",
            f"{BOX_TUNNEL_USER}@{dns_name}",
        ])
        return "\n".join([
            "# Written by `ling-admin remote setup` (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §2).",
            "[Unit]",
            f"Description=Mightling remote access: the reverse tunnel to {dns_name}",
            "After=network-online.target",
            "",
            "[Service]",
            f"ExecStart={command}",
            "Restart=always",
            "RestartSec=10",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ])

    @classmethod
    def install(cls, dns_name: str) -> bool:
        """
        Writes, enables and (re)starts the unit.

        Args:
            dns_name: The box's public DNS name.

        Returns:
            bool: True when systemd started it.
        """
        path = cls.unit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cls.render_unit(dns_name))
        cls._run(["systemctl", "--user", "daemon-reload"])
        enabled = cls._run(["systemctl", "--user", "enable", TUNNEL_UNIT])
        started = cls._run(["systemctl", "--user", "restart", TUNNEL_UNIT])
        return enabled.returncode == 0 and started.returncode == 0

    @classmethod
    def remove(cls) -> None:
        """Stops, disables and deletes the unit; the key stays unless the folder is purged."""
        cls._run(["systemctl", "--user", "disable", "--now", TUNNEL_UNIT])
        try:
            cls.unit_path().unlink()
        except OSError:
            pass
        cls._run(["systemctl", "--user", "daemon-reload"])

    @classmethod
    def state(cls) -> str:
        """
        Returns:
            str: `active`, `inactive`, `failed`, … as systemd says; `absent` without a unit.
        """
        if not cls.unit_path().is_file():
            return "absent"
        result = cls._run(["systemctl", "--user", "is-active", TUNNEL_UNIT])
        return result.stdout.strip() or "unknown"
