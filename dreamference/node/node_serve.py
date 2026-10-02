"""
What a paired key may ask this node for (specs/DREAMFERENCE_PUFFIN_NODE.md §12.4, §13.2).

`puffin-admin node serve-job` is the forced command of every paired key: sshd ignores what the
client asked to run and starts this instead, with the request in `SSH_ORIGINAL_COMMAND`. Anything
that is not one of the operations below is refused, so the key cannot open a shell, and every
operation is carried out by this node's own `puffin-admin`, with its own host-safety checks.
Nothing sent from outside can skip them.
"""

import getpass
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import sys
from pathlib import Path
from typing import Final, List, Optional

from dreamference.node.node_identity import NodeIdentity
from dreamference.node.node_pairing import KEY_COMMENT, NodePairing

# A model key as the matrix spells them. Checked before the matrix is consulted, so nothing with
# a space, a slash or an option's leading dash ever reaches a command line.
MODEL_KEY: Final[re.Pattern] = re.compile(r"[a-z0-9][a-z0-9._-]{1,80}")

REFUSAL: Final[str] = ("puffin-admin node serve-job: this key may only ask for Puffin node operations "
                       "(info, status, start, stop, set-model <key>, unpair).")


class NodeServe:
    """The node's side of a pairing."""

    @classmethod
    def authorized_keys(cls) -> Path:
        """
        Returns:
            Path: `~/.ssh/authorized_keys`.
        """
        return Path(os.path.expanduser("~/.ssh/authorized_keys"))

    @classmethod
    def admin_executable(cls) -> str:
        """
        Returns:
            str: The absolute path of this node's `puffin-admin`. sshd runs the forced command
            with a minimal PATH, so the line names the program by path.
        """
        linked = Path(os.path.expanduser("~/.local/bin/puffin-admin"))
        if linked.exists():
            return str(linked)
        candidate = Path(sys.executable).parent / "puffin-admin"
        return str(candidate if candidate.exists() else Path(sys.argv[0]).resolve())

    @classmethod
    def key_tag(cls, public_key: str) -> str:
        """
        Args:
            public_key: A public key's text.

        Returns:
            str: A short digest of the key, written into its forced command so that `unpair`,
            which sshd does not tell which key connected, removes that key's line and no other.
        """
        body = public_key.strip().split()[1]
        return hashlib.sha256(body.encode()).hexdigest()[:16]

    @classmethod
    def authorize(cls, public_key: str) -> bool:
        """
        Authorises a sender's key for `serve-job` only. Run on the node, by an ordinary SSH
        login, during `puffin-admin node add`.

        Args:
            public_key: The sender's public key.

        Returns:
            bool: True if the line is in place (it is not added twice).
        """
        try:
            tag = cls.key_tag(public_key)
            line = NodePairing.authorized_line(
                public_key, f"{cls.admin_executable()} node serve-job --key {tag}")
        except (ValueError, IndexError):
            print("❌ That is not a public key.")
            return False
        NodeIdentity.ensure()
        path = cls.authorized_keys()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        existing = path.read_text().splitlines() if path.is_file() else []
        kept = [entry for entry in existing if f"--key {tag}" not in entry]
        kept.append(line)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text("\n".join(kept) + "\n")
        os.chmod(staging, 0o600)
        os.replace(staging, path)
        print(f"✅ Authorised for Puffin node operations only ({path}).")
        return True

    @classmethod
    def unauthorize(cls, tag: str) -> bool:
        """
        Removes one paired key's line.

        Args:
            tag: The key's tag, as `authorize` wrote it.

        Returns:
            bool: True if a line was removed.
        """
        path = cls.authorized_keys()
        if not path.is_file() or not re.fullmatch(r"[0-9a-f]{16}", tag or ""):
            return False
        lines = path.read_text().splitlines()
        kept = [entry for entry in lines
                if not (f"--key {tag}" in entry and entry.rstrip().endswith(KEY_COMMENT))]
        if len(kept) == len(lines):
            return False
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text("\n".join(kept) + ("\n" if kept else ""))
        os.chmod(staging, 0o600)
        os.replace(staging, path)
        return True

    @classmethod
    def serve(cls, request: Optional[str], key_tag: Optional[str] = None) -> int:
        """
        Carries out one request from a paired node.

        Args:
            request: `SSH_ORIGINAL_COMMAND`: what the other machine asked for.
            key_tag: The connecting key's tag, from the forced command.

        Returns:
            int: The exit code sent back over SSH.
        """
        try:
            words = shlex.split(request or "")
        except ValueError:
            words = []
        operation = words[0] if words else ""
        arguments = words[1:]
        if operation == "info" and not arguments:
            print(json.dumps(cls.info()))
            return 0
        if operation == "status" and not arguments:
            return cls.run_admin(["status"])
        if operation == "stop" and not arguments:
            return cls.run_admin(["server", "stop"])
        if operation == "start" and not arguments:
            return cls.run_admin(["server", "start"])
        if operation == "set-model" and len(arguments) == 1:
            return cls.set_model(arguments[0])
        if operation == "unpair" and not arguments:
            removed = cls.unauthorize(key_tag or "")
            print("unpaired" if removed else "no such key")
            return 0 if removed else 1
        print(REFUSAL, file=sys.stderr)
        return 2

    @classmethod
    def set_model(cls, model_key: str) -> int:
        """
        Assigns a model to this node and starts it: the node keeps its own assignment, in its
        own config, so it comes back with it after a reboot without the other machine being up.

        Args:
            model_key: A key of this node's own model matrix; never a repository name.

        Returns:
            int: 0 when the server started; the failing command's code otherwise.
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
        if not MODEL_KEY.fullmatch(model_key) or model_key not in ModelMatrixRegistry.MATRIX:
            known = ", ".join(sorted(ModelMatrixRegistry.MATRIX))
            print(f"❌ {model_key!r} is not a model this node knows. Its models: {known}", file=sys.stderr)
            return 2
        if ModelMatrixRegistry.is_diffusion(model_key):
            print(f"❌ {model_key} is a diffusion model and cannot be a node's main model.", file=sys.stderr)
            return 2
        for arguments in (["main-model", "set", model_key], ["server", "stop"], ["server", "start"]):
            code = cls.run_admin(arguments)
            if code != 0:
                return code
        return 0

    @classmethod
    def info(cls) -> dict:
        """
        Returns:
            dict: This node's id, name, user, version and whether lingering is on (a job or a
            model load started over SSH stops with the connection when it is off).
        """
        from dreamference import __version__
        return {"node": NodeIdentity.read(), "name": socket.gethostname(), "user": getpass.getuser(),
                "version": __version__, "linger": cls.linger()}

    @classmethod
    def linger(cls) -> Optional[bool]:
        """
        Returns:
            Optional[bool]: Whether systemd lingering is on for this user; None if unknown.
        """
        try:
            result = subprocess.run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"],
                                    capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        value = result.stdout.strip().lower()
        return {"yes": True, "no": False}.get(value)

    @classmethod
    def run_admin(cls, arguments: List[str]) -> int:
        """
        Runs this node's own `puffin-admin`, from the home folder, with its output going back
        over the connection.

        Args:
            arguments: The command line after `puffin-admin`.

        Returns:
            int: Its exit code.
        """
        try:
            return subprocess.run([cls.admin_executable(), *arguments], cwd=os.path.expanduser("~"),
                                  stdin=subprocess.DEVNULL, check=False).returncode
        except OSError as error:
            print(f"❌ could not run puffin-admin: {error}", file=sys.stderr)
            return 1
