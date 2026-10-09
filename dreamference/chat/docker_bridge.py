"""
The host as a container on Docker's default bridge sees it.

The model server runs with `--network host`, so on the host it answers on localhost:8000. A
container on the default bridge (OpenHands; the retired Onyx web chat was one too) has a
`localhost` of its own, where the model server is not; the bridge gateway is the host from inside
such a container. Moved out of `OnyxRunner` before Onyx's removal (specs/DREAMFERENCE_MIGHTLING_ASK.md
§1, "Other code that leans on Onyx"), because OpenHands needs the same rewrite.
"""

import subprocess
from typing import Final
from urllib.parse import urlparse

# Docker's conventional default-bridge gateway, used when it cannot be read.
DEFAULT_BRIDGE_GATEWAY: Final[str] = "172.17.0.1"
LOOPBACK_HOSTS: Final[tuple] = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


class DockerBridge:
    """Rewrites host-side model server URLs for containers on Docker's default bridge."""

    @classmethod
    def container_vllm_url(cls, vllm_host: str) -> str:
        """
        Rewrites a host-side model server URL into one reachable from inside a bridged container.

        This is the step that silently breaks an otherwise correct setup: a loopback host is the
        container itself on the bridge, so the client merely fails to connect. A loopback host is
        swapped for the gateway address; a non-loopback host is left alone, being routable already.

        Args:
            vllm_host (str): The model server's base URL as configured for host-side use.

        Returns:
            str: A base URL including the `/v1` suffix, reachable from a bridged container.
        """
        parsed = urlparse(vllm_host if "//" in vllm_host else f"http://{vllm_host}")
        host = parsed.hostname or "localhost"
        port = parsed.port or 8000

        if host in LOOPBACK_HOSTS:
            host = cls.gateway()
        return f"http://{host}:{port}/v1"

    @classmethod
    def gateway(cls) -> str:
        """
        Reports the default bridge network's gateway address, which is the host from a container.

        Returns:
            str: The gateway IP, or Docker's conventional 172.17.0.1 if it cannot be read.
        """
        try:
            result = subprocess.run(
                [
                    "docker", "network", "inspect", "bridge",
                    "--format", "{{(index .IPAM.Config 0).Gateway}}",
                ],
                capture_output=True, text=True, timeout=15, check=False,
            )
            gateway = result.stdout.strip()
            if gateway:
                return gateway
        except (OSError, subprocess.SubprocessError):
            pass
        return DEFAULT_BRIDGE_GATEWAY
