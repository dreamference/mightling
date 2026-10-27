"""The forge's container and its HTTP API (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §0.4, §2.1).

One container per node, `mightling-forge`, on a user-defined bridge of its own (so the
containers of a SWE-bench night or any other sidecar cannot reach it), capped at 1 GiB with a
low CPU share, published on loopback and the advertised LAN addresses only. A mirror's container
also knows the forge of record as `forge-record`, an `--add-host` entry resolved by node id when
it starts: containers cannot resolve `.local` names, and an address is never stored.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final

from dreamference.forge.forge_app_ini import RECORD_ALIAS, WORK_DIR
from dreamference.forge.forge_bind import ForgeBind
from dreamference.forge.forge_settings import (
    CONTAINER,
    CONTAINER_HTTP_PORT,
    CONTAINER_SSH_PORT,
    CPU_SHARES,
    FORGE_IMAGE,
    HTTP_PORT,
    MEMORY_CAP_GIB,
    NETWORK,
    SSH_PORT,
)

API_TIMEOUT_S: Final[float] = 30.0
LABEL: Final[str] = "dreamference.forge=1"
# What `docker inspect -f {{.State.Running}}` prints for a running container.
RUNNING_TRUE: Final[str] = "true"
# The character that starts a URL's query.
QUERY_START: Final[str] = "?"


class ForgeContainer:
    """Builds the `docker run` command, runs `forgejo` inside it, and calls its API."""

    # Seams for the throwaway instance of tests/test_forge_private.py.
    name: str = CONTAINER
    network: str = NETWORK
    image: str = FORGE_IMAGE
    http_port: int = HTTP_PORT
    ssh_port: int = SSH_PORT

    @classmethod
    def docker(
        cls, argv: list[str], timeout: int = 120, input_text: str | None = None
    ) -> subprocess.CompletedProcess:
        """Run one docker command.

        Args:
            argv: The arguments after `docker`.
            timeout: Seconds to allow.
            input_text: Standard input, if any.

        Returns:
            subprocess.CompletedProcess: Its result; exit 127 with the reason when it could not run.
        """
        try:
            return subprocess.run(
                ["docker", *argv],
                capture_output=True,
                text=True,
                timeout=timeout,
                input=input_text,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(["docker", *argv], 127, "", str(error))

    @classmethod
    def run_arguments(
        cls,
        data_dir: Path,
        addresses: list[str],
        record_address: str | None = None,
        extra_mounts: list[tuple[str, str]] | None = None,
    ) -> list[str]:
        """Build the `docker run` command line of the forge's container.

        Args:
            data_dir: The forge's data folder, mounted as `/var/lib/gitea`.
            addresses: The advertised LAN addresses (`ForgeBind.lan_addresses`).
            record_address: The forge of record's address, for a mirror; None on the record.
            extra_mounts: Read-only (host, container) mounts, such as the anonymity patterns.

        Returns:
            list[str]: `docker run` and its arguments, in the foreground with `--rm`: the user
            unit owns the container's life.
        """
        argv = [
            "docker",
            "run",
            "--rm",
            "--name",
            cls.name,
            "--label",
            LABEL,
            "--network",
            cls.network,
            "--memory",
            f"{int(MEMORY_CAP_GIB * 1024)}m",
            "--memory-swap",
            f"{int(MEMORY_CAP_GIB * 1024)}m",
            "--cpu-shares",
            str(CPU_SHARES),
            # The rootless image's user is 1000; running as the node's own user keeps the
            # data folder theirs on a machine where that is another id.
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--pids-limit",
            "1024",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=3",
            "-v",
            f"{data_dir}:{WORK_DIR}",
        ]
        for host_path, container_path in extra_mounts or []:
            argv += ["-v", f"{host_path}:{container_path}:ro"]
        if record_address:
            argv += ["--add-host", f"{RECORD_ALIAS}:{record_address}"]
        argv += ForgeBind.publish_arguments(addresses, cls.http_port, CONTAINER_HTTP_PORT)
        argv += ForgeBind.publish_arguments(addresses, cls.ssh_port, CONTAINER_SSH_PORT)
        return [*argv, cls.image]

    @classmethod
    def ensure_network(cls) -> bool:
        """Make sure the forge's own bridge exists, creating it when missing.

        Returns:
            bool: Whether the forge's own bridge exists (created when missing).
        """
        if cls.docker(["network", "inspect", cls.network], timeout=30).returncode == 0:
            return True
        return (
            cls.docker(["network", "create", "--label", LABEL, cls.network], timeout=60).returncode
            == 0
        )

    @classmethod
    def ensure_image(cls) -> bool:
        """Make sure the pinned image is present, pulling it when missing.

        Returns:
            bool: Whether the pinned image is present (pulled when missing).
        """
        if cls.docker(["image", "inspect", cls.image], timeout=30).returncode == 0:
            return True
        print(f"⬇️  Pulling the forge's image ({cls.image.split('@')[0]}, pinned by digest)…")
        return cls.docker(["pull", cls.image], timeout=1800).returncode == 0

    @classmethod
    def running(cls) -> bool:
        """Tell whether the forge's container is running.

        Returns:
            bool: Whether the forge's container is running.
        """
        result = cls.docker(["inspect", "-f", "{{.State.Running}}", cls.name], timeout=30)
        return result.returncode == 0 and result.stdout.strip() == RUNNING_TRUE

    @classmethod
    def memory_used_bytes(cls) -> int | None:
        """Measure the container's memory use now.

        Returns:
            int | None: The container's memory use now, or None when it is not running.
        """
        result = cls.docker(
            ["stats", "--no-stream", "--format", "{{json .}}", cls.name], timeout=30
        )
        if result.returncode != 0:
            return None
        try:
            used = (
                json.loads(result.stdout.strip().splitlines()[0])["MemUsage"].split("/")[0].strip()
            )
        except (ValueError, IndexError, KeyError, AttributeError):
            return None
        units = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "B": 1}
        for suffix, factor in units.items():
            if used.endswith(suffix):
                try:
                    return int(float(used[: -len(suffix)]) * factor)
                except ValueError:
                    return None
        return None

    @classmethod
    def forgejo(cls, arguments: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        """Run the `forgejo` command inside the running container, as its own user.

        Args:
            arguments: The arguments after `forgejo`.
            timeout: Seconds to allow.

        Returns:
            subprocess.CompletedProcess: Its result.
        """
        return cls.docker(["exec", cls.name, "forgejo", *arguments], timeout=timeout)

    @classmethod
    def base_url(cls) -> str:
        """Return the API's root on loopback.

        Returns:
            str: The API's root on loopback.
        """
        return f"http://127.0.0.1:{cls.http_port}"

    @classmethod
    def api(
        cls,
        method: str,
        path: str,
        token: str | None,
        body: object = None,
        timeout: float = API_TIMEOUT_S,
    ) -> tuple[int, Any]:
        """Call the forge's API on loopback.

        Args:
            method: The HTTP method.
            path: The path after `/api/v1`.
            token: An access token, or None.
            body: A JSON body, if any.
            timeout: Seconds to allow.

        Returns:
            tuple[int, Any]: The status (0 when nothing answered) and the decoded JSON body (or
            the raw text, or None).
        """
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{cls.base_url()}/api/v1{path}", data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"token {token}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        except (OSError, ValueError):
            return 0, None
        text = raw.decode("utf-8", "replace")
        try:
            return status, json.loads(text) if text else None
        except ValueError:
            return status, text

    @classmethod
    def wait_until_answering(cls, seconds: float = 120.0) -> bool:
        """Wait until the forge's application answers.

        Args:
            seconds: How long to wait.

        Returns:
            bool: Whether the application answered in time.
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            # With sign-in required for everything, an anonymous `/version` is refused: any
            # answer from the application is an answer.
            status, _ = cls.api("GET", "/version", None, timeout=5)
            if HTTPStatus.OK <= status < HTTPStatus.INTERNAL_SERVER_ERROR:
                return True
            time.sleep(2)
        return False

    @classmethod
    def paginate(cls, path: str, token: str | None, limit: int = 50) -> list[dict[str, Any]]:
        """Read every page of an API listing.

        Args:
            path: A listing's path after `/api/v1` (without paging parameters).
            token: An access token.
            limit: The page size.

        Returns:
            list[dict[str, Any]]: Every item of every page.

        Raises:
            RuntimeError: A page could not be read: a caller deciding what may leave the forge
                fails closed on it.
        """
        items: list[dict[str, Any]] = []
        page = 1
        separator = "&" if QUERY_START in path else QUERY_START
        while True:
            status, body = cls.api("GET", f"{path}{separator}limit={limit}&page={page}", token)
            if status != HTTPStatus.OK or not isinstance(body, list):
                raise RuntimeError(f"the forge did not list {path} (HTTP {status})")
            items += body
            if len(body) < limit:
                return items
            page += 1
