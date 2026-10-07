"""
The one place `mling-admin swe-bench` runs `docker` (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md).

Every container, image and network command of the benchmark goes through `SweBenchDocker.run`
or `SweBenchDocker.popen`, so the test suite replaces two methods and nothing in it can start a
container, pull an image or reach the network.
"""

import json
import subprocess
from typing import IO, Any, Dict, List, Optional


class SweBenchDocker:
    """Thin wrappers over the `docker` command line."""

    @classmethod
    def run(cls, args: List[str], timeout: Optional[float] = None,
            input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        """
        Runs `docker <args>` to completion.

        Args:
            args: The arguments after `docker`.
            timeout: Seconds to wait; None waits for ever.
            input_text: Text for the command's standard input, if any.

        Returns:
            subprocess.CompletedProcess: With `stdout` and `stderr` as text. A timeout is
            reported as return code 124.
        """
        try:
            return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                                  input=input_text, stdin=None if input_text is not None else subprocess.DEVNULL)
        except subprocess.TimeoutExpired as error:
            return subprocess.CompletedProcess(["docker", *args], 124, error.stdout or "", "timed out")
        except OSError as error:
            return subprocess.CompletedProcess(["docker", *args], 127, "", str(error))

    @classmethod
    def popen(cls, args: List[str], stdout: IO[Any]) -> subprocess.Popen:
        """
        Starts `docker <args>` and returns at once, for the agent's long `docker exec`.

        Args:
            args: The arguments after `docker`.
            stdout: Where its output (and errors) go.

        Returns:
            subprocess.Popen: The running client.
        """
        return subprocess.Popen(["docker", *args], stdin=subprocess.DEVNULL, stdout=stdout,
                                stderr=subprocess.STDOUT, start_new_session=True)

    @classmethod
    def image_digest(cls, image: str) -> Optional[str]:
        """
        Identifies a local image.

        Args:
            image: The image reference.

        Returns:
            Optional[str]: Its repository digest (or image id when it has none); None if the
            image is not present locally.
        """
        result = cls.run(["image", "inspect", image, "--format", "{{json .RepoDigests}} {{.Id}}"], timeout=60)
        if result.returncode != 0:
            return None
        digests, _, image_id = result.stdout.strip().rpartition(" ")
        try:
            listed = json.loads(digests)
        except ValueError:
            listed = []
        return listed[0].split("@")[-1] if listed else image_id

    @classmethod
    def ensure_image(cls, image: str) -> Optional[str]:
        """
        Makes an image present locally, pulling it if needed.

        Args:
            image: The image reference.

        Returns:
            Optional[str]: Its digest, or None when it could not be pulled.
        """
        digest = cls.image_digest(image)
        if digest:
            return digest
        if cls.run(["pull", "-q", image], timeout=3600).returncode != 0:
            return None
        return cls.image_digest(image)

    @classmethod
    def ensure_network(cls, name: str) -> Optional[str]:
        """
        Creates the internal network if it does not exist, and finds its gateway.

        Args:
            name: The network's name.

        Returns:
            Optional[str]: The gateway address (where the model server answers from inside the
            network), or None when the network could not be made or is not internal.
        """
        described = cls.network(name)
        if described is None:
            if cls.run(["network", "create", "--internal", name], timeout=60).returncode != 0:
                return None
            described = cls.network(name)
        if not described or not described.get("Internal"):
            return None
        for config in (described.get("IPAM") or {}).get("Config") or []:
            if config.get("Gateway"):
                return str(config["Gateway"])
        return None

    @classmethod
    def network(cls, name: str) -> Optional[Dict[str, Any]]:
        """
        Describes a network.

        Args:
            name: The network's name.

        Returns:
            Optional[Dict[str, Any]]: `docker network inspect`'s record, or None if absent.
        """
        result = cls.run(["network", "inspect", name], timeout=60)
        if result.returncode != 0:
            return None
        try:
            records = json.loads(result.stdout)
        except ValueError:
            return None
        return records[0] if records else None
