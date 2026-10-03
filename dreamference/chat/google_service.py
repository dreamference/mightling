"""
The Google service without the web UI: `puffin-admin google start|stop|status`.

Specified in specs/DREAMFERENCE_PUFFIN_APPS.md §5.1.

The service (`gmail_search_service.py`, container `dreamference-gmail`) holds the Google tokens and
answers `/status`, the connect pages and the read-only Gmail, Drive and Calendar endpoints on
`127.0.0.1:8767`. Until Puffin's apps it was created only by `puffin-admin puffin configure`, so a
node without the web UI had none and `/apps` could not connect anything. It is now created here
too, on the sidecar network (never Docker's default bridge, DOCKER §6), and started by default on a
node by `server start` when it is absent. `configure` still creates its own on Onyx's network,
which publishes the same loopback port, so either one serves `/apps`.
"""

import os
import shutil
import subprocess
from typing import Final, List, Optional

from dreamference.chat.sidecar_network import SIDECAR_NETWORK, SidecarNetwork

GOOGLE_CONTAINER_NAME: Final[str] = "dreamference-gmail"
GOOGLE_SERVICE_IMAGE: Final[str] = "python:3-slim"
GOOGLE_HOST_PORT: Final[int] = 8767

# The files staged into the mounted credentials folder; the service imports the reader from
# beside itself.
STAGED_SERVICE: Final[str] = "service.py"
STAGED_READER: Final[str] = "google_workspace_reader.py"


class GoogleService:
    """Starts, stops and reports the Google service container, independently of the web UI."""

    @classmethod
    def state(cls) -> str:
        """
        Reports the container's state.

        Returns:
            str: `running`, `exited`, another Docker state, or "" when there is no container.
        """
        result = subprocess.run(
            ["docker", "inspect", GOOGLE_CONTAINER_NAME, "--format", "{{.State.Status}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    @classmethod
    def stage(cls, directory: str) -> bool:
        """
        Copies the service and its Drive/Calendar reader into the mounted folder.

        Args:
            directory (str): The credentials folder mounted as the container's `/config`.

        Returns:
            bool: True when both files are in place.
        """
        from dreamference.chat import gmail_search_service, google_workspace_reader

        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            shutil.copyfile(gmail_search_service.__file__, os.path.join(directory, STAGED_SERVICE))
            shutil.copyfile(
                google_workspace_reader.__file__, os.path.join(directory, STAGED_READER)
            )
        except OSError as exc:
            print(f"⚠️  Could not stage the Google service: {exc}")
            return False
        return True

    @classmethod
    def run_command(cls, directory: str, secret: str) -> List[str]:
        """
        Builds the `docker run` command.

        Args:
            directory (str): The credentials folder, mounted read-write: the service stores the
                token itself when a connection completes in the browser.
            secret (str): The shared secret every request but `/status` and the connect pages needs.

        Returns:
            List[str]: The argv: as the invoking user (files it writes stay the user's), on the
                sidecar network, published on loopback only.
        """
        return [
            "docker",
            "run",
            "-d",
            "--name",
            GOOGLE_CONTAINER_NAME,
            "--restart",
            "unless-stopped",
            "--network",
            SIDECAR_NETWORK,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{directory}:/config",
            "-p",
            f"127.0.0.1:{GOOGLE_HOST_PORT}:8000",
            "-e",
            f"PUFFIN_GMAIL_SECRET={secret}",
            GOOGLE_SERVICE_IMAGE,
            "python3",
            f"/config/{STAGED_SERVICE}",
        ]

    @classmethod
    def start(cls) -> bool:
        """
        Makes the service run.

        One that exists (the web UI's included) is restaged and started, not replaced; otherwise
        one is created on the sidecar network.

        Returns:
            bool: True if the container is running once this returns.
        """
        from dreamference.chat.gmail_credentials import CREDENTIALS_DIR
        from dreamference.chat.onyx_runner import OnyxRunner

        if not cls.stage(CREDENTIALS_DIR):
            return False
        state = cls.state()
        if state == "running":
            # Restaged files take effect at the next restart; the container is the web UI's or ours.
            return True
        if state:
            started = subprocess.run(
                ["docker", "start", GOOGLE_CONTAINER_NAME],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            return started.returncode == 0
        secret = OnyxRunner._gmail_secret()
        if not secret:
            return False
        if not SidecarNetwork.ensure():
            print(f"⚠️  Could not create the Docker network {SIDECAR_NETWORK}.")
            return False
        result = subprocess.run(
            cls.run_command(CREDENTIALS_DIR, secret),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            print(f"⚠️  Could not start the Google service: {result.stderr.strip()[:200]}")
            return False
        return True

    @classmethod
    def ensure_on_node(cls) -> Optional[bool]:
        """
        Starts the service on a node when it is absent, as `server start` does.

        On by default on a node (decided 2026-10-03). Never raises: the model server must start
        anyway.

        Returns:
            Optional[bool]: None when this machine is not a node or the container exists already;
                otherwise whether it started.
        """
        from dreamference.node.node_identity import NodeIdentity

        try:
            if not NodeIdentity.read() or cls.state():
                return None
            return cls.start()
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"⚠️  The Google service was not started: {exc}")
            return False

    @classmethod
    def stop(cls) -> bool:
        """
        Removes the container. The tokens stay in the credentials folder.

        Returns:
            bool: True if no container is left.
        """
        subprocess.run(
            ["docker", "rm", "-f", GOOGLE_CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return cls.state() == ""
