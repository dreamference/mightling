"""The image search sidecar without the web chat: `ling-admin images start|stop|status`.

Specified in specs/DREAMFERENCE_MIGHTLING_ASK.md §6 and specs/DREAMFERENCE_IMAGE_SEARCH.md. The
service (`image_search_service.py`, container `dreamference-image-search`) searches SearXNG's image
category, ranks the candidates with the served model's vision and keeps the winners in a store on
this machine (`~/.config/dreamference/image-search/data/images`), from which `ling web` and the
desktop app serve them at `/images/<id>.jpg`. The agent reaches it through the `image_search` MCP
tool (`ling-admin images mcp`, `image_search_mcp.py`).

Until Onyx was retired it was created only by `ling-admin chat configure`, on Onyx's Docker
network. It is now created here, on the sidecar network (DOCKER §6), and a container found on any
other network is replaced, which is how one an older install left on Onyx's network comes home.
The SigLIP pre-filter (`dreamference-siglip`) is best-effort: without it the service ranks its
first candidates directly (IMAGE_SEARCH §2), and it has no arm64 image upstream.

Nothing here prints: a failure is kept in `ImageSearchSidecar.problem` for the CLI to say.
"""

import json
import os
import secrets as secrets_module
import shutil
import subprocess
import time
import urllib.request
from typing import ClassVar, Final, List, Optional

from dreamference.chat.docker_bridge import DockerBridge
from dreamference.chat.searxng_sidecar import SEARXNG_CONTAINER_NAME
from dreamference.chat.sidecar_network import SIDECAR_NETWORK, SidecarNetwork

IMAGE_SEARCH_CONTAINER_NAME: Final[str] = "dreamference-image-search"
IMAGE_SEARCH_HOST_PORT: Final[int] = 8768
IMAGE_SEARCH_SERVICE_IMAGE: Final[str] = "python:3-slim"
# One folder holds everything: the staged service, its secret and its store (`data/`), mounted as
# the container's `/config` and owned by the user, so `ling web` can read the images it wrote.
IMAGE_SEARCH_DATA_DIR: Final[str] = os.path.expanduser("~/.config/dreamference/image-search")
IMAGE_SEARCH_SECRET_FILE: Final[str] = os.path.join(IMAGE_SEARCH_DATA_DIR, "secret")
IMAGE_STORE_DIR: Final[str] = os.path.join(IMAGE_SEARCH_DATA_DIR, "data", "images")
STAGED_SERVICE: Final[str] = "service.py"

# SearXNG as the sidecar network names it; it publishes only on loopback, so the network is the way.
SEARXNG_CONTAINER_URL: Final[str] = f"http://{SEARXNG_CONTAINER_NAME}:8080"

SIGLIP_CONTAINER_NAME: Final[str] = "dreamference-siglip"
SIGLIP_IMAGE: Final[str] = "michaelf34/infinity:latest-cpu"
SIGLIP_MODEL_ID: Final[str] = "google/siglip-base-patch16-224"
SIGLIP_PORT: Final[int] = 9100
SIGLIP_CONTAINER_URL: Final[str] = f"http://{SIGLIP_CONTAINER_NAME}:{SIGLIP_PORT}"

RUNNING: Final[str] = "running"
HEALTH_WAIT_SECONDS: Final[int] = 30

# Pillow is the service's one third-party need; the stock image lacks it and the container runs as
# the user, so it is installed into /tmp at boot (kept across a plain restart). Offline the install
# fails and the service runs without the stages that decode images.
BOOT_SCRIPT: Final[str] = (
    "export PIP_TARGET=/tmp/pylib PYTHONPATH=/tmp/pylib;"
    "python3 -c 'import PIL' 2>/dev/null"
    " || pip install -q --no-cache-dir pillow || true;"
    f"python3 /config/{STAGED_SERVICE}"
)


class ImageSearchSidecar:
    """Starts, stops and reports the image search sidecar, independently of any web UI."""

    # Why the last start failed, for the CLI to print; empty after a success.
    problem: ClassVar[str] = ""

    @classmethod
    def state(cls, container: str = IMAGE_SEARCH_CONTAINER_NAME) -> str:
        """Reports a container's state.

        Args:
            container (str): The container's name.

        Returns:
            str: `running`, `exited`, another Docker state, or "" when there is no container.
        """
        result = subprocess.run(
            ["docker", "inspect", container, "--format", "{{.State.Status}}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    @classmethod
    def secret(cls) -> Optional[str]:
        """Returns the shared secret `/search` requires, creating it once (mode 0600).

        The MCP server reads the same file, so its existence is also what tells the launcher that
        image search was set up on this machine.

        Returns:
            Optional[str]: The secret, or None if it could not be stored (see `problem`).
        """
        try:
            if os.path.exists(IMAGE_SEARCH_SECRET_FILE):
                with open(IMAGE_SEARCH_SECRET_FILE) as handle:
                    value = handle.read().strip()
                if value:
                    return value
            os.makedirs(IMAGE_SEARCH_DATA_DIR, mode=0o700, exist_ok=True)
            value = secrets_module.token_urlsafe(32)
            with open(IMAGE_SEARCH_SECRET_FILE, "w") as handle:
                handle.write(value)
            os.chmod(IMAGE_SEARCH_SECRET_FILE, 0o600)
            return value
        except OSError as exc:
            cls.problem = f"Could not store the image search secret: {exc}"
            return None

    @classmethod
    def stage(cls) -> bool:
        """Copies the service into the mounted folder and creates the store.

        Returns:
            bool: True when the service file and the store are in place.
        """
        from dreamference.chat import image_search_service

        try:
            os.makedirs(IMAGE_STORE_DIR, exist_ok=True)
            shutil.copyfile(image_search_service.__file__, os.path.join(IMAGE_SEARCH_DATA_DIR, STAGED_SERVICE))
        except OSError as exc:
            cls.problem = f"Could not stage the image search service: {exc}"
            return False
        return True

    @classmethod
    def served_model(cls, vllm_host: str, fallback: str) -> str:
        """Names the model the server is serving, as the vision ranker must ask for it.

        Args:
            vllm_host (str): The model server's base URL on the host.
            fallback (str): What to name when the server does not answer.

        Returns:
            str: The first id in `/v1/models`, or `fallback`.
        """
        try:
            with urllib.request.urlopen(f"{vllm_host.rstrip('/')}/v1/models", timeout=5) as response:
                return str(json.load(response)["data"][0]["id"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return fallback

    @classmethod
    def run_command(cls, secret: str, vision_url: str, vision_model: str, siglip: bool) -> List[str]:
        """Builds the `docker run` command for the service.

        Args:
            secret (str): The shared secret `/search` requires.
            vision_url (str): The model server as the container reaches it, with `/v1`.
            vision_model (str): The served model's id, for the vision re-rank.
            siglip (bool): Whether the SigLIP pre-filter runs; without it the service skips it.

        Returns:
            List[str]: The argv: as the invoking user, on the sidecar network, on loopback only.
        """
        return [
            "docker", "run", "-d", "--name", IMAGE_SEARCH_CONTAINER_NAME,
            "--restart", "unless-stopped", "--network", SIDECAR_NETWORK,
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{IMAGE_SEARCH_DATA_DIR}:/config",
            "-p", f"127.0.0.1:{IMAGE_SEARCH_HOST_PORT}:8768",
            "-e", f"MIGHTLING_IMAGE_SECRET={secret}",
            "-e", f"MIGHTLING_SEARXNG_URL={SEARXNG_CONTAINER_URL}",
            "-e", f"MIGHTLING_SIGLIP_URL={SIGLIP_CONTAINER_URL if siglip else ''}",
            "-e", f"MIGHTLING_VISION_URL={vision_url}",
            "-e", f"MIGHTLING_VISION_MODEL={vision_model}",
            "-e", "MIGHTLING_DATA_DIR=/config/data",
            IMAGE_SEARCH_SERVICE_IMAGE, "sh", "-c", BOOT_SCRIPT,
        ]  # fmt: skip

    @classmethod
    def siglip_command(cls) -> List[str]:
        """Builds the `docker run` command for the SigLIP embeddings server.

        Returns:
            List[str]: The argv, on the sidecar network, its weights in a named volume.
        """
        return [
            "docker", "run", "-d", "--name", SIGLIP_CONTAINER_NAME,
            "--restart", "unless-stopped", "--network", SIDECAR_NETWORK,
            "-v", f"{SIGLIP_CONTAINER_NAME}-cache:/app/.cache",
            "-p", f"127.0.0.1:{SIGLIP_PORT}:{SIGLIP_PORT}",
            SIGLIP_IMAGE, "v2", "--model-id", SIGLIP_MODEL_ID, "--port", str(SIGLIP_PORT),
        ]  # fmt: skip

    @classmethod
    def _off_network(cls, container: str) -> bool:
        """Tells whether a container exists on a network other than the sidecar network.

        Args:
            container (str): The container's name.

        Returns:
            bool: True if it exists and must be recreated (Onyx's network, the default bridge).
        """
        mode = SidecarNetwork.network_mode(container)
        return bool(mode) and mode != SIDECAR_NETWORK

    @classmethod
    def start_siglip(cls) -> bool:
        """Runs the SigLIP server, best-effort: a stopped one is started, one elsewhere replaced.

        Returns:
            bool: True if it is running once this returns.
        """
        if cls._off_network(SIGLIP_CONTAINER_NAME):
            subprocess.run(["docker", "rm", "-f", SIGLIP_CONTAINER_NAME],
                           capture_output=True, timeout=60, check=False)
        state = cls.state(SIGLIP_CONTAINER_NAME)
        if state == RUNNING:
            return True
        if state:
            started = subprocess.run(["docker", "start", SIGLIP_CONTAINER_NAME],
                                     capture_output=True, text=True, timeout=60, check=False)
            return started.returncode == 0
        result = subprocess.run(cls.siglip_command(), capture_output=True, text=True, timeout=300, check=False)
        return result.returncode == 0

    @classmethod
    def healthy(cls, wait_seconds: int = HEALTH_WAIT_SECONDS) -> bool:
        """Waits for the service's health route on loopback.

        Args:
            wait_seconds (int): How long to wait.

        Returns:
            bool: True once `/health` answers.
        """
        for attempt in range(max(1, wait_seconds)):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{IMAGE_SEARCH_HOST_PORT}/health", timeout=2):
                    return True
            except OSError:
                if attempt + 1 < wait_seconds:
                    time.sleep(1)
        return False

    @classmethod
    def start(cls, vllm_host: str, model: str, siglip: bool = True) -> bool:
        """Makes the service run on the sidecar network, recreated so the staged file and the
        served model take effect.

        Args:
            vllm_host (str): The model server's base URL on the host.
            model (str): The configured model, named when the server does not answer.
            siglip (bool): Whether to try the SigLIP pre-filter.

        Returns:
            bool: True if the service answers its health route once this returns.
        """
        cls.problem = ""
        secret = cls.secret()
        if not secret or not cls.stage():
            return False
        if not SidecarNetwork.ensure():
            cls.problem = f"Could not create the Docker network {SIDECAR_NETWORK}."
            return False
        with_siglip = siglip and cls.start_siglip()
        subprocess.run(["docker", "rm", "-f", IMAGE_SEARCH_CONTAINER_NAME],
                       capture_output=True, timeout=60, check=False)
        command = cls.run_command(
            secret, DockerBridge.container_vllm_url(vllm_host), cls.served_model(vllm_host, model), with_siglip)
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
        if result.returncode != 0:
            cls.problem = f"Could not start the image search service: {result.stderr.strip()[:200]}"
            return False
        if not cls.healthy():
            cls.problem = "The image search service did not answer its health check."
            return False
        return True

    @classmethod
    def stop(cls) -> bool:
        """Removes the service and SigLIP containers. The store, the secret and the weights stay.

        Returns:
            bool: True if neither container is left.
        """
        for container in (IMAGE_SEARCH_CONTAINER_NAME, SIGLIP_CONTAINER_NAME):
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=60, check=False)
        return not cls.state() and not cls.state(SIGLIP_CONTAINER_NAME)

    @classmethod
    def status_lines(cls) -> List[str]:
        """Describes the service for `ling-admin images status`.

        Returns:
            List[str]: One line per fact: each container, its network, the store and the setup.
        """
        lines = []
        for label, container in (("Image search", IMAGE_SEARCH_CONTAINER_NAME), ("SigLIP pre-filter", SIGLIP_CONTAINER_NAME)):
            state = cls.state(container)
            network = SidecarNetwork.network_mode(container) if state else ""
            where = f" on {network}" if network else ""
            lines.append(f"{label} ({container}): {state or 'absent'}{where}")
        try:
            count = sum(1 for name in os.listdir(IMAGE_STORE_DIR) if name.endswith(".jpg"))
        except OSError:
            count = 0
        lines.append(f"Store: {IMAGE_STORE_DIR} ({count} images)")
        set_up = os.path.isfile(IMAGE_SEARCH_SECRET_FILE)
        lines.append("Tool: image_search is offered to ling sessions on this machine"
                     if set_up else "Tool: not set up (run `ling-admin images start`)")
        return lines
