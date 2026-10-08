"""
Answers ssh's password prompt for a provisioning run that holds the passwords
(specs/DREAMFERENCE_MIGHTLING_FLEET.md §8.2).

With several machines, every password is asked at the start so the slow part runs unattended.
`ssh` then needs the password without a person at its prompt. `sshpass` takes it on argv or in the
environment, and an inherited pipe does not survive (ssh closes every descriptor above stderr
before it runs its askpass program). So the run listens on a Unix socket in its own 0700 folder,
`SSH_ASKPASS` points at a two-line script there that runs `ling-admin node askpass`, and that
command asks the socket for the password of the host named in `MIGHTLING_ASKPASS_HOST` and prints
it to ssh. Only the socket's path and the host name are in the environment; the password crosses
the socket once per connection and is checked against the peer's uid.
"""

import os
import shlex
import socket
import struct
import sys
import threading
from pathlib import Path
from typing import Dict, Final, Optional

# The environment variables the askpass script reads. Neither holds a secret.
SOCKET_VARIABLE: Final[str] = "MIGHTLING_ASKPASS_SOCKET"
HOST_VARIABLE: Final[str] = "MIGHTLING_ASKPASS_HOST"

# The longest host name accepted on the socket.
MAX_REQUEST: Final[int] = 512


class FleetAskpass:
    """Serves held passwords to `ssh` through `SSH_ASKPASS`, for one run."""

    def __init__(self, run_dir: Path, passwords: Dict[str, str]) -> None:
        """
        Args:
            run_dir: The run's 0700 folder.
            passwords: Password by host, as the run asked for them.
        """
        self.run_dir = run_dir
        self.passwords = passwords
        self.socket_path = run_dir / "askpass.sock"
        self.script_path = run_dir / "askpass"
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, admin_command: str) -> None:
        """
        Writes the askpass script and starts answering.

        Args:
            admin_command: How to run this machine's `ling-admin` (an absolute path).
        """
        self.script_path.write_text(f"#!/bin/sh\nexec {shlex.quote(admin_command)} node askpass\n")
        self.script_path.chmod(0o700)
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self._server.listen(8)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def environment(self, host: str) -> Dict[str, str]:
        """
        Args:
            host: The machine the ssh command is for.

        Returns:
            Dict[str, str]: What to add to that ssh's environment.
        """
        return {
            "SSH_ASKPASS": str(self.script_path),
            "SSH_ASKPASS_REQUIRE": "force",
            SOCKET_VARIABLE: str(self.socket_path),
            HOST_VARIABLE: host,
            "DISPLAY": os.environ.get("DISPLAY", ":0"),
        }

    def stop(self) -> None:
        """Stops answering and removes the socket and the script."""
        if self._server is not None:
            self._server.close()
            self._server = None
        for path in (self.socket_path, self.script_path):
            try:
                path.unlink()
            except OSError:
                pass

    def answer_for(self, host: str) -> Optional[str]:
        """
        Args:
            host: A host name.

        Returns:
            Optional[str]: The password held for it, if any.
        """
        return self.passwords.get(host)

    def _serve(self) -> None:
        while self._server is not None:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            with connection:
                if not self._same_user(connection):
                    continue
                host = connection.recv(MAX_REQUEST).decode("utf-8", "replace").strip()
                password = self.answer_for(host)
                if password is not None:
                    connection.sendall(password.encode("utf-8"))

    @classmethod
    def _same_user(cls, connection: socket.socket) -> bool:
        try:
            credentials = connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
        except OSError:
            return False
        _, uid, _ = struct.unpack("3i", credentials)
        return uid == os.getuid()

    @classmethod
    def ask(cls) -> int:
        """
        The `ling-admin node askpass` side: asks the run's socket and prints the password for
        ssh to read.

        Returns:
            int: 0 when a password was printed, 1 otherwise (ssh then fails the login).
        """
        path, host = os.environ.get(SOCKET_VARIABLE), os.environ.get(HOST_VARIABLE)
        if not path or not host:
            return 1
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(path)
                client.sendall(host.encode("utf-8"))
                client.shutdown(socket.SHUT_WR)
                chunks = []
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
        except OSError:
            return 1
        if not chunks:
            return 1
        sys.stdout.write(b"".join(chunks).decode("utf-8") + "\n")
        return 0
