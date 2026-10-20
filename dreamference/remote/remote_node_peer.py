"""The node as the overlay's first peer (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §3, §5).

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
import logging
import platform
import shlex
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import Final

from dreamference.remote.remote_settings import (
    CLIENT_ARCHIVES,
    CLIENT_URL,
    NETBIRD_VERSION,
    RemoteSettings,
)

# Failures go to standard error through this logger; the console-facing `RemoteAccess` prints
# the rest (specs/DREAMFERENCE_PYTHON_QUALITY.md §3.9).
LOG: Final[logging.Logger] = logging.getLogger(__name__)
CLIENT_NAME: Final[str] = "netbird"

TRUST_STORE_FILE: str = "/usr/local/share/ca-certificates/mightling-node-ca.crt"
CLIENT_PATH: str = "/usr/local/bin/netbird"
ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


class RemoteNodePeer:
    """NetBird's client on the node."""

    @classmethod
    def _download(cls, url: str) -> bytes | None:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except OSError as error:
            LOG.error(f"❌ Could not download {url}: {error}")
            return None

    @classmethod
    def _run_root(cls, script: Path, purpose: str, yes: bool) -> bool:
        from dreamference.node.node_advertiser import NodeAdvertiser

        return NodeAdvertiser.run_privileged(["bash", str(script)], purpose, yes=yes)

    @classmethod
    def arch(cls) -> str | None:
        """This machine's architecture, in NetBird's words.

        Returns:
            Optional[str]: This machine's architecture as NetBird names it, or None if unsupported.
        """
        return ARCHITECTURES.get(platform.machine().lower())

    @classmethod
    def client_binary(cls) -> Path | None:
        """Downloads NetBird's client archive and checks it against the pinned digest.

        The binary is kept in the remote folder for the root script to install.

        Returns:
            Optional[Path]: The checked binary, or None.
        """
        arch = cls.arch()
        if arch is None:
            LOG.error(f"❌ NetBird's client is not pinned for {platform.machine()}.")
            return None
        url = CLIENT_URL.format(version=NETBIRD_VERSION, arch=arch)
        data = cls._download(url)
        if data is None:
            return None
        digest = hashlib.sha256(data).hexdigest()
        if digest != CLIENT_ARCHIVES[arch]:
            LOG.error(
                f"❌ {url} has SHA-256 {digest}, not the pinned {CLIENT_ARCHIVES[arch]}; nothing is installed."
            )
            return None
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = next(
                (
                    entry
                    for entry in archive.getmembers()
                    if Path(entry.name).name == CLIENT_NAME and entry.isfile()
                ),
                None,
            )
            if member is None:
                LOG.error(f"❌ {url} holds no `netbird` binary.")
                return None
            handle = archive.extractfile(member)
            binary = handle.read() if handle else b""
        target = RemoteSettings.path("box", "netbird")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(binary)
        target.chmod(0o755)
        return target

    @classmethod
    def installed(cls) -> str | None:
        """Where NetBird's client is installed, if it is.

        Returns:
            Optional[str]: The installed client's path when there is one, else None.
        """
        return shutil.which("netbird")

    @classmethod
    def script(
        cls,
        binary: Path | None,
        ca_certificate: Path,
        management_url: str,
        setup_key_file: Path,
        peer_name: str,
    ) -> str:
        """The root script that installs the client and joins the overlay.

        Args:
            binary: The checked client binary to install, or None to keep the installed one.
            ca_certificate: The node's CA certificate.
            management_url: Where management is (the box's name).
            setup_key_file: The one-time setup key, in a file only the user can read: never on a
                command line, where `ps` and sudo's log would show it.
            peer_name: The node's peer name.

        Returns:
            str: The root script.
        """
        lines: list[str] = ["set -eu"]
        if binary is not None:
            lines.append(f"install -m 0755 {shlex.quote(str(binary))} {CLIENT_PATH}")
        lines += [
            f"install -m 0644 {shlex.quote(str(ca_certificate))} {TRUST_STORE_FILE}",
            "update-ca-certificates >/dev/null",
            "netbird service install >/dev/null 2>&1 || true",
            "netbird service start >/dev/null 2>&1 || true",
            f"netbird up --management-url {shlex.quote(management_url)}"
            f" --setup-key-file {shlex.quote(str(setup_key_file))}"
            f" --hostname {shlex.quote(peer_name)}",
        ]
        return "\n".join(lines) + "\n"

    @classmethod
    def enrol(
        cls, ca_certificate: Path, management_url: str, setup_key: str, node_id: str, yes: bool
    ) -> bool:
        """Installs the client and the CA if needed, and brings the node up as a peer.

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
        key_file = RemoteSettings.write_private("node-setup-key", setup_key + "\n")
        script = RemoteSettings.write_private(
            "node-peer.sh",
            cls.script(
                binary,
                ca_certificate,
                management_url,
                key_file,
                RemoteSettings.node_peer_name(node_id),
            ),
        )
        try:
            return cls._run_root(
                script, "install NetBird's client and the node's CA, and join the overlay", yes
            )
        finally:
            script.unlink(missing_ok=True)
            key_file.unlink(missing_ok=True)

    @classmethod
    def leave_script(cls) -> str:
        """The root script with which the node leaves the overlay.

        Returns:
            str: The root script `remove --purge` runs: the node leaves the overlay and its CA
            leaves the trust store; NetBird's client stays installed.
        """
        return "\n".join(
            [
                "set -u",
                "netbird down >/dev/null 2>&1",
                "netbird service stop >/dev/null 2>&1",
                "netbird service uninstall >/dev/null 2>&1",
                f"rm -f {TRUST_STORE_FILE}",
                "update-ca-certificates --fresh >/dev/null",
                "",
            ]
        )

    @classmethod
    def leave(cls, yes: bool) -> bool:
        """Takes the node off the overlay.

        Args:
            yes: Never wait for input.

        Returns:
            bool: True when the script ran.
        """
        script = RemoteSettings.write_private("node-leave.sh", cls.leave_script())
        try:
            return cls._run_root(
                script, "take the node out of the overlay and its CA out of the trust store", yes
            )
        finally:
            script.unlink(missing_ok=True)
