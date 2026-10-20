"""
The SearXNG container behind `ling-search`, the MCP server's web search and image search:
`ling-admin searxng start`.

Until 2026-10-01 it was started by hand, from a `docker run` line in an error message, which put
it on Docker's default bridge and gave it a copy of the host's DNS servers taken at start
(`sidecar_network.py` records how that failed at boot). It is now created on the sidecar network,
and a container found on the default bridge is replaced.
"""

import os
import subprocess
from collections.abc import Callable
from typing import Final, List

from dreamference.chat.searxng_settings import OWN, UNWRITTEN, WRITTEN, SearxngSettings
from dreamference.chat.sidecar_network import SIDECAR_NETWORK, SidecarNetwork

# The name other sidecars (image search) resolve SearXNG by on the sidecar network. SearXNG publishes
# only on 127.0.0.1, so the bridge gateway that reaches vLLM does not reach it -- the shared
# network is what makes it addressable, and exposes no new host port.
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
            `settings.yml`, written by `SearxngSettings`, enables the JSON API and picks the
            engines.
        """
        return os.path.expanduser("~/.config/searxng")

    @classmethod
    def run_command(cls) -> List[str]:
        """
        Builds the `docker run` command.

        Returns:
            List[str]: The argv. The port is published on loopback only, unless this node is
            advertised (`ling-admin node enable`), when `ling-search` on other machines needs
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
    def apply_settings(cls, say: Callable[[str], None] | None = None) -> str:
        """Makes the settings file this version's (`SearxngSettings.ensure`) and says what it did.

        Args:
            say (Callable[[str], None] | None): Told when the settings were written, kept because
                they are the user's own, or could not be written.

        Returns:
            str: What `SearxngSettings.ensure` returned.
        """
        settings = SearxngSettings.ensure(cls.config_dir(), cls.write_settings_as_owner)
        messages = {
            WRITTEN: f"📝 Wrote SearXNG's settings: {cls.config_dir()}/settings.yml",
            OWN: "💡 Keeping your own SearXNG settings.yml (it has no ling-admin version line).",
            UNWRITTEN: f"⚠️  Could not write {cls.config_dir()}/settings.yml; SearXNG keeps its old settings.",
        }
        if say and settings in messages:
            say(messages[settings])
        return settings

    @classmethod
    def write_settings_as_owner(cls, config_dir: str, text: str) -> bool:
        """Writes `settings.yml` as the user SearXNG's image gives its configuration folder to.

        The image's start script hands `/etc/searxng` to its `searxng` user, so once the container
        has started, the folder on the host belongs to that user and this one cannot write in it.
        A throwaway container of the same image, as root and with no network, writes the file
        and gives it to the folder's owner.

        Args:
            config_dir (str): The folder mounted as `/etc/searxng`.
            text (str): The settings file.

        Returns:
            bool: True if the file was written.
        """
        script = ("cat > /etc/searxng/settings.yml.new && chmod 644 /etc/searxng/settings.yml.new && "
                  "chown \"$(stat -c %u:%g /etc/searxng)\" /etc/searxng/settings.yml.new && "
                  "mv /etc/searxng/settings.yml.new /etc/searxng/settings.yml")
        result = subprocess.run(
            ["docker", "run", "--rm", "-i", "--network", "none", "--user", "0", "--entrypoint", "sh",
             "-v", f"{config_dir}:/etc/searxng", SEARXNG_IMAGE, "-c", script],
            input=text, capture_output=True, text=True, timeout=300, check=False,
        )
        return result.returncode == 0

    @classmethod
    def extra_networks(cls) -> List[str]:
        """
        Lists the user-defined networks the existing container is attached to (the retired Onyx
        web chat's, on an older install), so a replacement can rejoin them.

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
    def start(cls, say: Callable[[str], None] | None = None) -> bool:
        """
        Makes SearXNG run on the sidecar network, published where the node's settings say.

        The settings file is made this version's first (`SearxngSettings.ensure`, which leaves a
        file of the user's own alone). A container already created on a user-defined network and
        published on the right address is only started, or restarted when the settings changed:
        SearXNG reads them once, at start. One created on the default bridge, or published on the other address
        (the node was enabled or disabled since), is removed and created again, and rejoins the
        other networks it was on; it holds no state outside the mounted configuration folder.

        Args:
            say (Callable[[str], None] | None): Told when the settings were written, or kept
                because they are the user's own; nothing is said without it.

        Returns:
            bool: True if the container is running once this returns.
        """
        from dreamference.node.node_settings import NodeSettings
        settings = cls.apply_settings(say)
        mode = SidecarNetwork.network_mode(SEARXNG_CONTAINER_NAME)
        on_bridge = bool(mode) and SidecarNetwork.created_on_default_bridge(SEARXNG_CONTAINER_NAME)
        published = cls.published_address() if mode and not on_bridge else ""
        if mode and not on_bridge and published in ("", NodeSettings.search_bind_address()):
            verb = "restart" if settings == WRITTEN else "start"
            started = subprocess.run(["docker", verb, SEARXNG_CONTAINER_NAME],
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
        result = subprocess.run(cls.run_command(), capture_output=True, text=True, timeout=600, check=False)
        if result.returncode != 0:
            print(f"⚠️  {result.stderr.strip()[:200]}")
            return False
        for network in rejoin:
            subprocess.run(["docker", "network", "connect", network, SEARXNG_CONTAINER_NAME],
                           capture_output=True, text=True, timeout=30, check=False)
        return True
