"""
Pairing with another node over SSH (specs/DREAMFERENCE_MIGHTLING_NODE.md §13.2, §15.1).

Using a node (prompts, search, the web UI) is open to the local network. Controlling one, or
running a job on it, is not: that goes through a key made for nothing else, which the other node
restricts to one forced command, `mling-admin node serve-job`. `mling-admin node add <node>`
sets the key up once; the user never types an SSH command.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

from dreamference.node.node_browser import NodeBrowser

KEY_NAME: Final[str] = "mightling-node_ed25519"

# What the restricted key may not do, beside running anything but the forced command.
KEY_RESTRICTIONS: Final[str] = "no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc"

# The marker that tells Mightling's lines in `authorized_keys` from the user's own.
KEY_COMMENT: Final[str] = "mightling-node"


class NodePairing:
    """Creates the key, installs it on a node, and builds the SSH commands that use it."""

    @classmethod
    def key_path(cls) -> Path:
        """
        Returns:
            Path: `~/.ssh/mightling-node_ed25519`, the private key used for nothing else.
        """
        return Path(os.path.expanduser("~/.ssh")) / KEY_NAME

    @classmethod
    def nodes_dir(cls) -> Path:
        """
        Returns:
            Path: `~/.config/dreamference/nodes`, one record per paired node and their host keys.
        """
        return Path(os.path.expanduser("~/.config/dreamference/nodes"))

    @classmethod
    def known_hosts(cls) -> Path:
        """
        Returns:
            Path: The host-key file for paired nodes, apart from the user's own `known_hosts`.
        """
        return cls.nodes_dir() / "known_hosts"

    @classmethod
    def authorized_line(cls, public_key: str, serve_command: str) -> str:
        """
        The `authorized_keys` line a node writes for a paired sender.

        Args:
            public_key: The sender's public key (`ssh-ed25519 AAAA… comment`).
            serve_command: The forced command, by absolute path.

        Returns:
            str: One line. Whatever the client asks for, sshd runs `serve_command` and passes the
            request in `SSH_ORIGINAL_COMMAND`; no terminal, no forwarding.

        Raises:
            ValueError: If the key is not one public key on one line.
        """
        fields = public_key.strip().split()
        if len(fields) < 2 or "\n" in public_key.strip() or not fields[0].startswith(("ssh-", "ecdsa-", "sk-")):
            raise ValueError("not a public key")
        if '"' in serve_command or "\n" in serve_command:
            raise ValueError("the forced command cannot be quoted")
        return f'command="{serve_command}",{KEY_RESTRICTIONS} {fields[0]} {fields[1]} {KEY_COMMENT}'

    @classmethod
    def ensure_key(cls) -> Optional[str]:
        """
        Creates the key pair on first use.

        Returns:
            Optional[str]: The public key's text, or None if `ssh-keygen` failed.
        """
        key = cls.key_path()
        public = Path(str(key) + ".pub")
        if not key.is_file() or not public.is_file():
            key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            for stale in (key, public):
                if stale.exists():
                    stale.unlink()
            result = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", KEY_COMMENT,
                                     "-f", str(key)], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                return None
        return public.read_text().strip()

    @classmethod
    def host_alias(cls, node_id: str) -> str:
        """
        Args:
            node_id: The node's stable id.

        Returns:
            str: The name its host key is stored under. The key is pinned to the node, not to an
            address: a different machine answering at the same address later is refused, and the
            same node at a new address is not.
        """
        return f"mightling-node-{node_id}"

    @classmethod
    def ssh_options(cls, record: Dict[str, Any], accept_new: bool = False) -> List[str]:
        """
        Args:
            record: A paired node's record.
            accept_new: True only while pairing, when the node's host key is first stored.

        Returns:
            List[str]: The options every connection to that node uses.
        """
        options = [
            "-i", str(cls.key_path()),
            "-p", str(record.get("ssh_port") or 22),
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
            "-o", f"UserKnownHostsFile={cls.known_hosts()}",
            "-o", f"HostKeyAlias={cls.host_alias(record['node'])}",
            "-o", f"StrictHostKeyChecking={'accept-new' if accept_new else 'yes'}",
            "-o", "ConnectTimeout=10",
        ]
        return options

    @classmethod
    def ssh_command(cls, record: Dict[str, Any], request: str) -> List[str]:
        """
        Args:
            record: A paired node's record.
            request: What to ask `serve-job` for (it arrives as `SSH_ORIGINAL_COMMAND`).

        Returns:
            List[str]: The argv.
        """
        return ["ssh", *cls.ssh_options(record), f"{record['user']}@{record['address']}", request]

    @classmethod
    def record_path(cls, node_id: str) -> Path:
        """
        Args:
            node_id: The node's stable id.

        Returns:
            Path: Where its record is kept.
        """
        return cls.nodes_dir() / f"{node_id}.json"

    @classmethod
    def paired(cls) -> List[Dict[str, Any]]:
        """
        Returns:
            List[Dict[str, Any]]: Every paired node's record, by name.
        """
        records = []
        for path in sorted(cls.nodes_dir().glob("*.json")) if cls.nodes_dir().is_dir() else []:
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get("node") and record.get("address"):
                records.append(record)
        return sorted(records, key=lambda record: record.get("name", ""))

    @classmethod
    def find(cls, name: str, browse: bool = True) -> Optional[Dict[str, Any]]:
        """
        Finds a paired node by name, address or id, and refreshes its address from the network:
        the pairing is with the node's id, and DHCP may have moved it.

        Args:
            name: What the user typed.
            browse: Whether to look the node up on the network for its current address.

        Returns:
            Optional[Dict[str, Any]]: The record, or None if no paired node matches.
        """
        wanted = name.strip().lower()
        for record in cls.paired():
            if wanted in (record.get("name", "").lower(), record["address"].lower(), record["node"].lower()) \
                    or (len(wanted) >= 4 and record["node"].lower().startswith(wanted)):
                if browse:
                    for seen in NodeBrowser.browse():
                        if seen.get("node") == record["node"] and seen["address"] != record["address"]:
                            record["address"] = seen["address"]
                            cls._save(record)
                return record
        return None

    @classmethod
    def add(cls, name: str, user: Optional[str] = None, ssh_port: int = 22) -> bool:
        """
        Pairs with a node: finds it, makes the key, and has the node authorise it for
        `serve-job` only. The node's password is typed once, at ssh's own prompt; there is no
        way to authorise a key without authenticating once.

        Args:
            name: The node's name, address or id, as a browse shows it.
            user: The account on the node; this user's name by default.
            ssh_port: The node's SSH port.

        Returns:
            bool: True once the node answers through the restricted key.
        """
        import getpass
        target = cls.discover(name)
        if target is None:
            print(f"❌ No node named {name} answers on this network (`mling-admin node list`).")
            return False
        public_key = cls.ensure_key()
        if public_key is None:
            print("❌ ssh-keygen could not create the pairing key.")
            return False
        record = {"node": target["node"], "name": target["name"], "address": target["address"],
                  "user": user or getpass.getuser(), "ssh_port": ssh_port}
        cls.nodes_dir().mkdir(parents=True, exist_ok=True)
        print(f"🔑 Pairing with {record['name']} ({record['address']}). Its password is asked once, by ssh.")
        if not cls.authorize_on_node(record, public_key):
            print("❌ The node did not authorise the key.")
            return False
        cls._save(record)
        answer = cls.run(record, "info")
        if answer.returncode != 0:
            print(f"❌ The node did not answer through the new key: {answer.stderr.strip()[-200:]}")
            return False
        try:
            info = json.loads(answer.stdout)
        except ValueError:
            info = {}
        if info.get("node") and info["node"] != record["node"]:
            print("❌ The machine that answered is not the node that was advertised; pairing removed.")
            cls.record_path(record["node"]).unlink(missing_ok=True)
            return False
        print(f"✅ Paired with {record['name']}. This key can ask it for `mling-admin node` operations and nothing else.")
        if info.get("linger") is False:
            print(f"⚠️  Lingering is off for {record['user']} on {record['name']}: a job would stop when its "
                  f"sender disconnects. On that node: loginctl enable-linger")
        return True

    @classmethod
    def authorize_on_node(cls, record: Dict[str, Any], public_key: str) -> bool:
        """
        Sends the public key to the node's `mling-admin node authorize`, over an ordinary SSH
        login (password or the user's own key), which writes the restricted line.

        Args:
            record: The node's record.
            public_key: The key to authorise.

        Returns:
            bool: True if the node reported success.
        """
        command = ["ssh", "-p", str(record.get("ssh_port") or 22),
                   "-o", f"UserKnownHostsFile={cls.known_hosts()}",
                   "-o", f"HostKeyAlias={cls.host_alias(record['node'])}",
                   "-o", "StrictHostKeyChecking=accept-new",
                   f"{record['user']}@{record['address']}",
                   "$HOME/.local/bin/mling-admin node authorize"]
        try:
            return subprocess.run(command, input=public_key + "\n", text=True, check=False).returncode == 0
        except OSError:
            return False

    @classmethod
    def remove(cls, name: str) -> bool:
        """
        Unpairs: asks the node to drop the key's line, then forgets the node here.

        Args:
            name: The paired node.

        Returns:
            bool: True if a pairing was removed.
        """
        record = cls.find(name, browse=False)
        if record is None:
            print(f"❌ No paired node named {name}.")
            return False
        answer = cls.run(record, "unpair")
        if answer.returncode != 0:
            print(f"⚠️  {record['name']} did not confirm; its authorized_keys may still hold the key "
                  f"(remove the line ending in `{KEY_COMMENT}` there).")
        cls.record_path(record["node"]).unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-R", cls.host_alias(record["node"]), "-f", str(cls.known_hosts())],
                       capture_output=True, text=True, check=False)
        print(f"✅ {record['name']} is no longer paired.")
        return True

    @classmethod
    def run(cls, record: Dict[str, Any], request: str, capture: bool = True,
            input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        """
        Sends one request to a paired node.

        Args:
            record: The node's record.
            request: The request for `serve-job`.
            capture: Capture the output (True), or let it stream to this terminal.
            input_text: Text for the request's standard input, if any.

        Returns:
            subprocess.CompletedProcess: The ssh process's result.
        """
        try:
            return subprocess.run(cls.ssh_command(record, request), capture_output=capture, text=True,
                                  input=input_text, check=False)
        except OSError as error:
            return subprocess.CompletedProcess([], 255, "", str(error))

    @classmethod
    def discover(cls, name: str) -> Optional[Dict[str, str]]:
        """
        Args:
            name: A node's name, address or id.

        Returns:
            Optional[Dict[str, str]]: The node as a browse shows it, or None.
        """
        wanted = name.strip().lower()
        for node in NodeBrowser.browse():
            if not node.get("node"):
                continue
            if wanted in (node["name"].lower(), node["address"].lower(), node["node"].lower()) \
                    or (len(wanted) >= 4 and node["node"].lower().startswith(wanted)):
                return node
        return None

    @classmethod
    def _save(cls, record: Dict[str, Any]) -> None:
        path = cls.record_path(record["node"])
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        staging.write_text(json.dumps(record, indent=2) + "\n")
        os.replace(staging, path)
