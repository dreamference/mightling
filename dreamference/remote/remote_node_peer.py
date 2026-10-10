"""
The node as the overlay's first peer (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §3, §5).

Clients reach the model server, `ling web` and Matrix at the node's overlay name, so the node runs
NetBird's client too. Its peer name is `mightling-<node id>`, which makes its overlay name the
node's own and nobody else's: a client that resolves it has found this node. Installing needs root
once, in one script the user sees before sudo asks: NetBird's client from its release archive,
pinned by SHA-256, the node's CA in the system trust store (NetBird's client trusts the system store
and nothing else), the client's system service, and `netbird up` with a one-time setup key.

Downloads go through `_download`, everything run as root through `_run_root`; the tests replace both.
"""

import hashlib
import io
import platform
import shlex
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import List, Optional

from dreamference.remote.remote_settings import (
    CLIENT_ARCHIVES,
    CLIENT_URL,
    NETBIRD_VERSION,
    RemoteSettings,
)

TRUST_STORE_FILE: str = "/usr/local/share/ca-certificates/mightling-node-ca.crt"
CLIENT_PATH: str = "/usr/local/bin/netbird"
ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


class RemoteNodePeer:
    """NetBird's client on the node."""

    @classmethod
    def _download(cls, url: str) -> Optional[bytes]:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except OSError as error:
            print(f"❌ Could not download {url}: {error}")
            return None

    @classmethod
    def _run_root(cls, script: Path, purpose: str, yes: bool) -> bool:
        from dreamference.node.node_advertiser import NodeAdvertiser
        return NodeAdvertiser.run_privileged(["bash", str(script)], purpose, yes=yes)

    @classmethod
    def arch(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: This machine's architecture as NetBird names it, or None if unsupported.
        """
        return ARCHITECTURES.get(platform.machine().lower())

    @classmethod
    def client_binary(cls) -> Optional[Path]:
        """
        Downloads NetBird's client archive, checks it against the pinned digest and keeps the binary
        in the remote folder for the root script to install.

        Returns:
            Optional[Path]: The checked binary, or None.
        """
        arch = cls.arch()
        if arch is None:
            print(f"❌ NetBird's client is not pinned for {platform.machine()}.")
            return None
        url = CLIENT_URL.format(version=NETBIRD_VERSION, arch=arch)
        data = cls._download(url)
        if data is None:
            return None
        digest = hashlib.sha256(data).hexdigest()
        if digest != CLIENT_ARCHIVES[arch]:
            print(f"❌ {url} has SHA-256 {digest}, not the pinned {CLIENT_ARCHIVES[arch]}; nothing is installed.")
            return None
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = next((entry for entry in archive.getmembers() if Path(entry.name).name == "netbird" and entry.isfile()), None)
            if member is None:
                print(f"❌ {url} holds no `netbird` binary.")
                return None
            handle = archive.extractfile(member)
            binary = handle.read() if handle else b""
        target = RemoteSettings.path("box", "netbird")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(binary)
        target.chmod(0o755)
        return target

    @classmethod
    def installed(cls) -> Optional[str]:
        """
        Returns:
            Optional[str]: The installed client's path when there is one, else None.
        """
        return shutil.which("netbird")

    @classmethod
    def script(cls, binary: Optional[Path], ca_certificate: Path, management_url: str, setup_key: str,
               peer_name: str) -> str:
        """
        Args:
            binary: The checked client binary to install, or None to keep the installed one.
            ca_certificate: The node's CA certificate.
            management_url: Where management is (the box's name).
            setup_key: A one-time setup key.
            peer_name: The node's peer name.

        Returns:
            str: The root script.
        """
        lines: List[str] = ["set -eu"]
        if binary is not None:
            lines.append(f"install -m 0755 {shlex.quote(str(binary))} {CLIENT_PATH}")
        lines += [
            f"install -m 0644 {shlex.quote(str(ca_certificate))} {TRUST_STORE_FILE}",
            "update-ca-certificates >/dev/null",
            "netbird service install >/dev/null 2>&1 || true",
            "netbird service start >/dev/null 2>&1 || true",
            f"netbird up --management-url {shlex.quote(management_url)} --setup-key {shlex.quote(setup_key)}"
            f" --hostname {shlex.quote(peer_name)}",
        ]
        return "\n".join(lines) + "\n"

    @classmethod
    def enrol(cls, ca_certificate: Path, management_url: str, setup_key: str, node_id: str, yes: bool) -> bool:
        """
        Installs the client and the CA if needed, and brings the node up as a peer.

        Args:
            ca_certificate: The node's CA certificate.
            management_url: Where management is.
            setup_key: A one-time setup key, spent here.
            node_id: The node's id.
            yes: Never wait for input.

        Returns:
            bool: True when `netbird up` succeeded.
        """
        binary = None if cls.installed() else cls.client_binary()
        if binary is None and not cls.installed():
            return False
        script = RemoteSettings.write_private(
            "node-peer.sh", cls.script(binary, ca_certificate, management_url, setup_key, RemoteSettings.node_peer_name(node_id)))
        try:
            return cls._run_root(script, "install NetBird's client and the node's CA, and join the overlay", yes)
        finally:
            script.unlink(missing_ok=True)

    @classmethod
    def leave_script(cls) -> str:
        """
        Returns:
            str: The root script `remove --purge` runs: the node leaves the overlay and its CA
            leaves the trust store; NetBird's client stays installed.
        """
        return "\n".join([
            "set -u",
            "netbird down >/dev/null 2>&1",
            "netbird service stop >/dev/null 2>&1",
            "netbird service uninstall >/dev/null 2>&1",
            f"rm -f {TRUST_STORE_FILE}",
            "update-ca-certificates --fresh >/dev/null",
            "",
        ])

    @classmethod
    def leave(cls, yes: bool) -> bool:
        """
        Args:
            yes: Never wait for input.

        Returns:
            bool: True when the script ran.
        """
        script = RemoteSettings.write_private("node-leave.sh", cls.leave_script())
        try:
            return cls._run_root(script, "take the node out of the overlay and its CA out of the trust store", yes)
        finally:
            script.unlink(missing_ok=True)
