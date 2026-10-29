"""The Google service without the web UI: `ling-admin google start|stop|status`.

Specified in specs/DREAMFERENCE_MIGHTLING_APPS.md §5.1.

The service (`gmail_search_service.py`, container `dreamference-gmail`) holds the Google tokens and
answers `/status`, the connect pages and the read-only Gmail, Drive and Calendar endpoints on
`127.0.0.1:8767`. Until Mightling's apps it was created only by `ling-admin chat configure`, so a
node without the web UI had none and `/apps` could not connect anything. It is now created here
too, on the sidecar network (never Docker's default bridge, DOCKER §6), and started by default on a
node by `server start` when it is absent. One the retired Onyx web chat's `configure` created on
its network publishes the same loopback port and is adopted as it is. The shared secret is this
module's (`GoogleService.secret`).

Nothing here prints: a failure is kept in `GoogleService.problem` for the CLI to say.
"""

import contextlib
import os
import pathlib
import shutil
import subprocess
from typing import ClassVar, Final

from dreamference.chat.sidecar_network import SIDECAR_NETWORK, SidecarNetwork

GOOGLE_CONTAINER_NAME: Final[str] = "dreamference-gmail"
GOOGLE_SERVICE_IMAGE: Final[str] = "python:3-slim"
GOOGLE_HOST_PORT: Final[int] = 8767
RUNNING: Final[str] = "running"

# The header every request but `/status` and the connect pages carries the shared secret in; kept
# in step with the service module (`gmail_search_service.AUTH_HEADER`), which enforces it.
GMAIL_AUTH_HEADER: Final[str] = "X-Mightling-Gmail-Token"
# The shared secret's file, inside the credentials folder; created once, mode 0600.
SECRET_FILE_NAME: Final[str] = "service-secret"
# The variable the container hands the secret to the service in (`gmail_search_service.serve`).
SECRET_ENV: Final[str] = "MIGHTLING_GMAIL_SECRET"

# The files staged into the mounted credentials folder; the service imports the reader from
# beside itself.
STAGED_SERVICE: Final[str] = "service.py"
STAGED_READER: Final[str] = "google_workspace_reader.py"


class GoogleService:
    """Starts, stops and reports the Google service container, independently of the web UI."""

    # Why the last start failed, for the CLI to print; empty after a success.
    problem: ClassVar[str] = ""
    # Whether the last `stage` changed a staged file, so a running service must restart to use it.
    restaged: ClassVar[bool] = False

    @classmethod
    def state(cls) -> str:
        """Reports the container's state.

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
    def secret(cls) -> str | None:
        """Returns the shared secret the service and its clients authenticate with, creating it once.

        Returns:
            str | None: The secret, or None if it could not be stored (see `problem`).
        """
        import secrets as secrets_module

        from dreamference.chat.gmail_credentials import CREDENTIALS_DIR

        path = os.path.join(CREDENTIALS_DIR, SECRET_FILE_NAME)
        try:
            if os.path.exists(path):
                with open(path) as handle:
                    value = handle.read().strip()
                if value:
                    return value
            os.makedirs(CREDENTIALS_DIR, mode=0o700, exist_ok=True)
            value = secrets_module.token_urlsafe(32)
            # Written whole and private under a name of its own, then linked into place. A link
            # never replaces a file, so of two first starts at once the later keeps the earlier's.
            temporary = f"{path}.{secrets_module.token_hex(8)}.tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "w") as handle:
                    handle.write(value)
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    with open(path) as handle:
                        existing = handle.read().strip()
                    if existing:
                        return existing
                    os.replace(temporary, path)
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
            return value
        except OSError as exc:
            cls.problem = f"Could not store the Google service's shared secret: {exc}"
            return None

    @classmethod
    def stage(cls, directory: str) -> bool:
        """Copies the service and its Drive/Calendar reader into the mounted folder.

        Args:
            directory (str): The credentials folder mounted as the container's `/config`.

        Returns:
            bool: True when both files are in place.
        """
        from dreamference.chat import gmail_search_service, google_workspace_reader

        folder = pathlib.Path(directory)
        cls.restaged = False
        try:
            folder.mkdir(mode=0o700, exist_ok=True, parents=True)
            for source, name in (
                (gmail_search_service.__file__, STAGED_SERVICE),
                (google_workspace_reader.__file__, STAGED_READER),
            ):
                target = folder / name
                if not target.is_file() or target.read_bytes() != pathlib.Path(source).read_bytes():
                    shutil.copyfile(source, target)
                    cls.restaged = True
        except OSError as exc:
            cls.problem = f"Could not stage the Google service: {exc}"
            return False
        return True

    @classmethod
    def carries_secret(cls, secret: str) -> bool:
        """Tells whether the existing container was created with today's shared secret.

        A container made before the rename to Mightling (or by the retired web chat) hands the
        service the secret as `PUFFIN_GMAIL_SECRET`, which the current service does not read: the
        old code in it answers every client `unauthorised`, and the current code would check none.

        Args:
            secret (str): The shared secret clients send.

        Returns:
            bool: True when the container's environment hands the service this secret.
        """
        result = subprocess.run(
            [
                "docker",
                "inspect",
                GOOGLE_CONTAINER_NAME,
                "--format",
                "{{range .Config.Env}}{{println .}}{{end}}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return f"{SECRET_ENV}={secret}" in result.stdout.splitlines()

    @classmethod
    def run_command(cls, directory: str, secret: str) -> list[str]:
        """Builds the `docker run` command.

        Args:
            directory (str): The credentials folder, mounted read-write: the service stores the
                token itself when a connection completes in the browser.
            secret (str): The shared secret every request but `/status` and the connect pages needs.

        Returns:
            list[str]: The argv: as the invoking user (files it writes stay the user's), on the
                sidecar network, published on loopback only.
        """
        return [
            "docker", "run", "-d", "--name", GOOGLE_CONTAINER_NAME,
            "--restart", "unless-stopped", "--network", SIDECAR_NETWORK,
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{directory}:/config",
            "-p", f"127.0.0.1:{GOOGLE_HOST_PORT}:8000",
            "-e", f"{SECRET_ENV}={secret}",
            GOOGLE_SERVICE_IMAGE, "python3", f"/config/{STAGED_SERVICE}",
        ]  # fmt: skip

    @classmethod
    def _create(cls, directory: str) -> bool:
        """Creates the container on the sidecar network.

        Args:
            directory (str): The credentials folder to mount.

        Returns:
            bool: True if `docker run` succeeded.
        """
        secret = cls.secret()
        if not secret:
            cls.problem = cls.problem or "Could not store the service's shared secret."
            return False
        if not SidecarNetwork.ensure():
            cls.problem = f"Could not create the Docker network {SIDECAR_NETWORK}."
            return False
        result = subprocess.run(
            cls.run_command(directory, secret),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            cls.problem = f"Could not start the Google service: {result.stderr.strip()[:200]}"
        return result.returncode == 0

    @classmethod
    def start(cls) -> bool:
        """Makes the service run.

        One that exists (the retired web chat's included) is restaged and started, not replaced, and
        restarted when running if a staged file changed, so it serves the installed code. One made
        without today's shared secret (before the rename to Mightling) is replaced; the tokens stay
        in the mounted folder. Otherwise one is created on the sidecar network.

        Returns:
            bool: True if the container is running once this returns.
        """
        from dreamference.chat.gmail_credentials import CREDENTIALS_DIR

        cls.problem = ""
        if not cls.stage(CREDENTIALS_DIR):
            return False
        state = cls.state()
        if state:
            secret = cls.secret()
            if not secret:
                return False
            if not cls.carries_secret(secret):
                if not cls.stop():
                    cls.problem = (
                        f"Could not replace {GOOGLE_CONTAINER_NAME}, made before the rename."
                    )
                    return False
                state = ""
        if not state:
            return cls._create(CREDENTIALS_DIR)
        if state == RUNNING and not cls.restaged:
            return True
        started = subprocess.run(
            ["docker", "restart" if state == RUNNING else "start", GOOGLE_CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if started.returncode != 0:
            cls.problem = f"Could not start {GOOGLE_CONTAINER_NAME}: {started.stderr.strip()[:200]}"
        return started.returncode == 0

    @classmethod
    def ensure_on_node(cls) -> bool | None:
        """Starts the service on a node when it is absent, as `server start` does.

        On by default on a node (decided 2026-10-03). Never raises: the model server must start
        anyway.

        Returns:
            bool | None: None when this machine is not a node or the container exists already;
                otherwise whether it started (see `problem` when it did not).
        """
        from dreamference.node.node_identity import NodeIdentity

        try:
            if not NodeIdentity.read() or cls.state():
                return None
            return cls.start()
        except (OSError, subprocess.SubprocessError) as exc:
            cls.problem = f"The Google service was not started: {exc}"
            return False

    @classmethod
    def stop(cls) -> bool:
        """Removes the container. The tokens stay in the credentials folder.

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
        return not cls.state()
