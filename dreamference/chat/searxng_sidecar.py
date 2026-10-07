"""
The SearXNG container behind `mling-search`, the MCP server's web search and Onyx's web search:
`mling-admin searxng start`.

Until 2026-10-01 it was started by hand, from a `docker run` line in an error message, which put
it on Docker's default bridge and gave it a copy of the host's DNS servers taken at start
(`sidecar_network.py` records how that failed at boot). It is now created on the sidecar network,
and a container found on the default bridge is replaced.
"""

import os
import subprocess
from typing import Final, List

from dreamference.chat.sidecar_network import SIDECAR_NETWORK, SidecarNetwork

# The name Onyx's containers resolve SearXNG by once it joins their network. SearXNG publishes
# only on 127.0.0.1, so the bridge gateway that reaches vLLM does not reach it -- attaching the
# container to Onyx's network is what makes it addressable, and exposes no new host port.
SEARXNG_CONTAINER_NAME: Final[str] = "dreamference-searxng"
SEARXNG_IMAGE: Final[str] = "docker.io/searxng/searxng:latest"
SEARXNG_HOST_PORT: Final[int] = 8888


class SearxngSidecar:
    """Starts SearXNG on the sidecar network, replacing a container made on the default bridge."""

    @classmethod
    def config_dir(cls) -> str:
        """
        Returns:
            str: `~/.config/searxng`, mounted as the container's `/etc/searxng`. Its
            `settings.yml` is what enables the JSON API (`web_tools.py` documents the two keys).
        """
        return os.path.expanduser("~/.config/searxng")

    @classmethod
    def run_command(cls) -> List[str]:
        """
        Builds the `docker run` command.

        Returns:
            List[str]: The argv. The port is published on loopback only, unless this node is
            advertised (`mling-admin node enable`), when `mling-search` on other machines needs
            it; the container is created on the sidecar network so its name lookups follow the
            host's resolver.
        """
        from dreamference.node.node_settings import NodeSettings
        return ["docker", "run", "-d", "--name", SEARXNG_CONTAINER_NAME,
                "--restart", "unless-stopped", "--network", SIDECAR_NETWORK,
                "-p", f"{NodeSettings.search_bind_address()}:{SEARXNG_HOST_PORT}:8080",
                "-v", f"{cls.config_dir()}:/etc/searxng",
                SEARXNG_IMAGE]

    @classmethod
    def extra_networks(cls) -> List[str]:
        """
        Lists the user-defined networks the existing container is attached to (Onyx's, once
        `configure` has joined it), so a replacement can rejoin them.

        Returns:
            List[str]: Network names, without the default bridge and the sidecar network.
        """
        result = subprocess.run(
            ["docker", "inspect", SEARXNG_CONTAINER_NAME, "--format",
             "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            return []
        return [name for name in result.stdout.split() if name not in ("bridge", SIDECAR_NETWORK)]

    @classmethod
    def published_address(cls) -> str:
        """
        Returns:
            str: The host address the existing container publishes its port on (`127.0.0.1`,
            `0.0.0.0`), or "" when there is no container or no published port.
        """
        result = subprocess.run(
            ["docker", "inspect", SEARXNG_CONTAINER_NAME, "--format",
             '{{range $p, $b := .HostConfig.PortBindings}}{{range $b}}{{.HostIp}} {{end}}{{end}}'],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            return ""
        addresses = result.stdout.split()
        return addresses[0] if addresses else ""

    @classmethod
    def start(cls) -> bool:
        """
        Makes SearXNG run on the sidecar network, published where the node's settings say.

        A container already created on a user-defined network and published on the right address
        is only started. One created on the default bridge, or published on the other address
        (the node was enabled or disabled since), is removed and created again, and rejoins the
        other networks it was on; it holds no state outside the mounted configuration folder.

        Returns:
            bool: True if the container is running once this returns.
        """
        from dreamference.node.node_settings import NodeSettings
        mode = SidecarNetwork.network_mode(SEARXNG_CONTAINER_NAME)
        on_bridge = bool(mode) and SidecarNetwork.created_on_default_bridge(SEARXNG_CONTAINER_NAME)
        published = cls.published_address() if mode and not on_bridge else ""
        if mode and not on_bridge and published in ("", NodeSettings.search_bind_address()):
            started = subprocess.run(["docker", "start", SEARXNG_CONTAINER_NAME],
                                     capture_output=True, text=True, timeout=60, check=False)
            return started.returncode == 0

        if not SidecarNetwork.ensure():
            print(f"⚠️  Could not create the Docker network {SIDECAR_NETWORK}.")
            return False
        rejoin = cls.extra_networks() if mode else []
        if mode and on_bridge:
            print("🔁 Recreating SearXNG off Docker's default bridge, whose DNS is a copy taken at start...")
        elif mode:
            print(f"🔁 Recreating SearXNG to publish it on {NodeSettings.search_bind_address()}...")
        if mode:
            subprocess.run(["docker", "rm", "-f", SEARXNG_CONTAINER_NAME],
                           capture_output=True, text=True, timeout=60, check=False)
        os.makedirs(cls.config_dir(), exist_ok=True)
        result = subprocess.run(cls.run_command(), capture_output=True, text=True, timeout=600, check=False)
        if result.returncode != 0:
            print(f"⚠️  {result.stderr.strip()[:200]}")
            return False
        for network in rejoin:
            subprocess.run(["docker", "network", "connect", network, SEARXNG_CONTAINER_NAME],
                           capture_output=True, text=True, timeout=30, check=False)
        return True
