"""
`puffin-admin node enable|disable|status`: offering this machine to the local network as a Puffin
node (specs/DREAMFERENCE_PUFFIN_NODE.md §4, §5.2).

Enabling does three things: it installs the Avahi service file that advertises the node, it
publishes the web UI and SearXNG beyond loopback, and it records that it did so, so that a later
`puffin-admin puffin configure` or `searxng start` keeps those addresses. Disabling undoes all
three. Root is needed once, for the file under `/etc/avahi`; the command is printed before it
runs and sudo prompts on the terminal.
"""

import getpass
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
from typing import Dict, List, Optional
from urllib.parse import urlparse

from dreamference.node.node_browser import NodeBrowser
from dreamference.node.node_identity import NodeIdentity
from dreamference.node.node_service_file import NodeServiceFile
from dreamference.node.node_settings import LOOPBACK, NodeSettings

WEB_PORT = 3000
# Ubuntu puts the daemon in /usr/sbin, which is not on every user's PATH.
AVAHI_DAEMON = "/usr/sbin/avahi-daemon"
DEFAULT_MODEL_PORT = 8000

SHARING_NOTICE = (
    "⚠️  Anyone on the local network can now use this node: send prompts to its model, search\n"
    "   through its SearXNG, and open its web UI. Nothing is encrypted or authenticated."
)
WEB_NOTICE = (
    "⚠️  The web UI has one account, shared by everyone who opens it: one chat history, the admin\n"
    "   panel, and the Gmail tool, which searches the mail connected on this node.\n"
    "   `puffin-admin node enable --no-web` keeps the web UI on this machine."
)


class NodeAdvertiser:
    """Enables, disables and reports the node's advertisement."""

    @classmethod
    def enable(cls, no_web: bool = False) -> bool:
        """
        Advertises this machine as a node and publishes what clients need.

        Args:
            no_web: Keep the web UI on loopback; clients then get `puffin` and web search only.

        Returns:
            bool: True when the service file is installed and the binds applied; False, with
            nothing published, when the file could not be installed.
        """
        if not cls.is_gb10():
            print("⚠️  This machine does not look like a GB10 (DGX Spark); a node on anything else is "
                  "untested.")
        # DGX OS has Avahi (GNOME depends on it); a GB10 reinstalled as Ubuntu Server does not, and
        # without the daemon there is neither a folder to put the file in nor anyone to publish it.
        if not cls.avahi_installed() and not cls.run_privileged(
                ["apt-get", "install", "-y", "avahi-daemon"], "install Avahi, which publishes the advertisement"):
            print("❌ Avahi is not installed, so this node cannot be advertised: `sudo apt install avahi-daemon`, "
                  "then run this again. Until then a client reaches it with PUFFIN_NODE=<this machine's address>.")
            return False
        node_id = NodeIdentity.ensure()
        before = NodeSettings.load()
        NodeSettings.save(advertise=True, web=not no_web)
        port = cls.model_port()
        text = NodeServiceFile.render(
            port=port, node_id=node_id, version=cls.version(),
            state="ready" if cls.model_answers(port) else "stopped",
            web_port=WEB_PORT if cls.web_installed() and not no_web else None,
            search_port=cls.search_port(),
            main=cls.serves_main_model(),
        )
        # The advertisement first, the binds only once it is in place: a node whose web UI is on
        # the LAN but which nobody can find is the worst of both states.
        if not cls.install_service_file(text):
            NodeSettings.save(advertise=before["advertise"], web=before["web"])
            print("⚠️  Nothing was published. Run `puffin-admin node enable` from a terminal, where sudo "
                  "can ask for the password once.")
            return False
        print(SHARING_NOTICE)
        if not no_web:
            print(WEB_NOTICE)
        cls.apply_binds()
        print(f"✅ This node is advertised as {socket.gethostname()} (_puffin-node._tcp, port {port}, "
              f"id {node_id[:8]}…). Clients find it with no address typed.")
        return True

    @classmethod
    def disable(cls) -> bool:
        """
        Stops advertising and puts the web UI and SearXNG back on loopback.

        Returns:
            bool: True when no advertisement is left.
        """
        NodeSettings.save(advertise=False)
        cls.apply_binds()
        path = NodeServiceFile.service_path
        if not path.exists():
            print("✅ This node is not advertised.")
            return True
        if cls.run_privileged(["rm", "-f", str(path)], "remove the advertisement"):
            print("✅ The node is no longer advertised; the web UI and SearXNG are on this machine only.")
            return True
        # Without root the file cannot be removed, but its owner can empty it: Avahi publishes
        # nothing from a file that is not a service group.
        try:
            path.write_text("")
            print(f"⚠️  {path} was emptied, which stops the advertisement; remove it with the command above.")
            return True
        except OSError:
            print(f"❌ {path} is still in place and still advertises this node.")
            return False

    @classmethod
    def status(cls) -> str:
        """
        Returns:
            str: The node's id, what it shares, the advertised records, the published addresses,
            and what a browse of the network returns.
        """
        settings = NodeSettings.load()
        node_id = NodeIdentity.read()
        lines = [f"Node id: {node_id or 'none (this machine has not been a node yet)'}"]
        if not settings["advertise"]:
            lines.append("Advertised: no (`puffin-admin node enable` offers this node to the local network)")
        else:
            lines.append("Advertised: yes" + ("" if settings["web"] else ", without the web UI (--no-web)"))
        records = NodeServiceFile.read()
        if records is None:
            lines.append(f"Service file: none at {NodeServiceFile.service_path}")
            if settings["advertise"]:
                lines.append("  The node is enabled but not advertised: run `puffin-admin node enable` again.")
        else:
            shown = ", ".join(f"{key}={value}" for key, value in records.items())
            lines.append(f"Service file: {shown}")
        lines.append(f"Web UI (port {WEB_PORT}) published on: {cls.web_published_address() or 'not installed'}")
        searxng = cls.searxng_published_address()
        lines.append(f"SearXNG published on: {searxng or 'not running'}")
        found = NodeBrowser.browse()
        if not found:
            lines.append("Seen on the network: no node (a browse of _puffin-node._tcp returned nothing)")
        for node in found:
            mine = " (this node)" if node_id and node.get("node") == node_id else ""
            lines.append(f"Seen on the network: {node['name']} at {node['address']}:{node['port']} "
                         f"state={node.get('state', '?')} version={node.get('version', '?')}{mine}")
        return "\n".join(lines)

    # -- what `server start` and `server stop` report ----------------------------------------------

    @classmethod
    def on_server_starting(cls, model_key: str, port: int) -> None:
        """
        Called when a model load begins: the machine is a node from here on, and an advertised
        node says `loading`, because a refused connection looks the same for a stopped node and
        a loading one.

        Args:
            model_key: The model being loaded.
            port: The model server's port.
        """
        NodeIdentity.ensure()
        cls._report(NodeServiceFile.update(state="loading", port=port, version=cls.version(),
                                           main=cls.is_main_model(model_key)))

    @classmethod
    def on_server_ready(cls) -> None:
        """Called when the model server first answers."""
        cls._report(NodeServiceFile.update(state="ready"))

    @classmethod
    def on_server_stopped(cls) -> None:
        """Called when the model server is stopped, removed, or failed to start."""
        cls._report(NodeServiceFile.update(state="stopped"))

    @classmethod
    def on_searxng_started(cls) -> None:
        """Called when `puffin-admin searxng start` has SearXNG running: an advertised node offers it."""
        if NodeSettings.advertised():
            cls._report(NodeServiceFile.update(search_port=cls.search_port()))

    @classmethod
    def on_web_ui_bound(cls) -> None:
        """Called when the web UI's publish address was applied: the advert follows it."""
        if NodeSettings.advertised():
            shared = NodeSettings.web_bind_address() != LOOPBACK and cls.web_installed()
            cls._report(NodeServiceFile.update(web_port=WEB_PORT if shared else None))

    @classmethod
    def web_installed(cls) -> bool:
        """
        Returns:
            bool: True if the web UI is installed on this machine (its `.env` exists).
        """
        from dreamference.chat.onyx_runner import ONYX_ENV_FILE
        return os.path.isfile(ONYX_ENV_FILE)

    @classmethod
    def _report(cls, outcome: Optional[bool]) -> None:
        if outcome is False:
            print(f"⚠️  Could not update {NodeServiceFile.service_path}; clients may see a stale state. "
                  "Run `puffin-admin node enable` again.")

    # -- pieces, each a seam for the tests ---------------------------------------------------------

    @classmethod
    def apply_binds(cls) -> bool:
        """
        Publishes the web UI and SearXNG where the settings say, recreating a container only when
        its address changes. Neither is installed or started by this: a node without the web UI
        simply does not offer it.

        Returns:
            bool: True when the web UI is installed and published beyond loopback.
        """
        from dreamference.chat.onyx_runner import OnyxRunner
        from dreamference.chat.searxng_sidecar import SEARXNG_CONTAINER_NAME, SearxngSidecar
        from dreamference.chat.sidecar_network import SidecarNetwork
        web_shared = False
        if cls.web_installed():
            OnyxRunner().bind_to_loopback()
            web_shared = NodeSettings.web_bind_address() != LOOPBACK
        if SidecarNetwork.network_mode(SEARXNG_CONTAINER_NAME):
            SearxngSidecar.start()
        return web_shared

    @classmethod
    def avahi_installed(cls) -> bool:
        """
        Returns:
            bool: True if the Avahi daemon, which publishes the service file, is installed.
        """
        return shutil.which("avahi-daemon") is not None or os.path.exists(AVAHI_DAEMON)

    @classmethod
    def install_service_file(cls, text: str) -> bool:
        """
        Puts the service file in place: written directly when it is already this user's, created
        through sudo (owned by this user, so later updates need no root) otherwise.

        Args:
            text: The file's content.

        Returns:
            bool: True when the file is installed with this content.
        """
        if NodeServiceFile.write(text):
            return True
        path = NodeServiceFile.service_path
        with tempfile.NamedTemporaryFile("w", suffix=".service", delete=False) as staged:
            staged.write(text)
        try:
            user = getpass.getuser()
            command = ["install", "-m", "644", "-o", user, staged.name, str(path)]
            if cls.run_privileged(command, "advertise the node (Avahi publishes files in this folder)"):
                return True
        finally:
            os.unlink(staged.name)
        print(f"💡 To advertise the node, save the following as {path}, owned by {getpass.getuser()}:\n")
        print(text)
        return False

    @classmethod
    def run_privileged(cls, command: List[str], purpose: str) -> bool:
        """
        Runs one command as root, visibly: it is printed first and sudo prompts on the terminal.
        Where sudo cannot prompt (no terminal) the command is only printed.

        Args:
            command: The argv, without `sudo`.
            purpose: What it is for, for the message.

        Returns:
            bool: True if the command ran and succeeded.
        """
        line = "sudo " + " ".join(command)
        if not sys.stdin.isatty():
            print(f"💡 Run this to {purpose}:\n   {line}")
            return False
        print(f"🔑 Needs root once, to {purpose}:\n   {line}")
        try:
            return subprocess.run(["sudo", *command], check=False).returncode == 0
        except OSError:
            return False

    @classmethod
    def model_port(cls) -> int:
        """
        Returns:
            int: The model server's port, from the configured host.
        """
        from dreamference.config import DreamferenceConfig
        return urlparse(DreamferenceConfig().vllm_host).port or DEFAULT_MODEL_PORT

    @classmethod
    def model_answers(cls, port: int) -> bool:
        """
        Args:
            port: The model server's port.

        Returns:
            bool: True if `/v1/models` answers on loopback.
        """
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2) as response:
                return response.status == 200
        except (OSError, ValueError):
            return False

    @classmethod
    def search_port(cls) -> Optional[int]:
        """
        Returns:
            Optional[int]: SearXNG's host port when its container exists, else None.
        """
        from dreamference.chat.searxng_sidecar import SEARXNG_CONTAINER_NAME, SEARXNG_HOST_PORT
        from dreamference.chat.sidecar_network import SidecarNetwork
        return SEARXNG_HOST_PORT if SidecarNetwork.network_mode(SEARXNG_CONTAINER_NAME) else None

    @classmethod
    def serves_main_model(cls) -> bool:
        """
        Returns:
            bool: True if the model assigned to this node is one a coding client can use.
        """
        from dreamference.config import DreamferenceConfig
        return cls.is_main_model(DreamferenceConfig().model)

    @classmethod
    def is_main_model(cls, model_key: str) -> bool:
        """
        Args:
            model_key: A model matrix key.

        Returns:
            bool: True for a chat model; False for a diffusion model or an unknown key. It is a
            property of the matrix entry, so nobody sets it.
        """
        from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
        return model_key in ModelMatrixRegistry.MATRIX and not ModelMatrixRegistry.is_diffusion(model_key)

    @classmethod
    def is_gb10(cls) -> bool:
        """
        Returns:
            bool: True if the hardware detection qualifies this machine as a GB10.
        """
        try:
            from dreamference.hardware.hardware_manager import HardwareManager
            return bool(HardwareManager.detect_gb10_hardware().is_gb10)
        except Exception:
            return False

    @classmethod
    def version(cls) -> str:
        """
        Returns:
            str: Puffin's version on this node.
        """
        from dreamference import __version__
        return __version__

    @classmethod
    def web_published_address(cls) -> str:
        """
        Returns:
            str: The address the web UI's `.env` publishes port 3000 on, or "" when the web UI is
            not installed.
        """
        from dreamference.chat.onyx_runner import ONYX_ENV_FILE
        try:
            with open(ONYX_ENV_FILE) as handle:
                lines = handle.read().splitlines()
        except OSError:
            return ""
        for line in lines:
            if line.strip().startswith("HOST_PORT="):
                value = line.split("=", 1)[1].strip().strip('"')
                return value.rsplit(":", 1)[0] if ":" in value else "0.0.0.0"
        return "0.0.0.0 (Onyx's default: every interface)"

    @classmethod
    def searxng_published_address(cls) -> str:
        """
        Returns:
            str: The address SearXNG's container publishes on, or "" when there is none.
        """
        from dreamference.chat.searxng_sidecar import SearxngSidecar
        return SearxngSidecar.published_address()

    @classmethod
    def summary(cls) -> Dict[str, object]:
        """
        Returns:
            Dict[str, object]: The settings and the node id, for `puffin-admin status`.
        """
        return {"node": NodeIdentity.read(), **NodeSettings.load()}
