"""The speech-to-text sidecar without the web chat: `ling-admin voice start|stop|status`.

Specified in specs/DREAMFERENCE_MIGHTLING_ASK.md §7. `dreamference-stt` runs speaches (Whisper,
`faster-whisper-small`) on the CPU: ctranslate2's CUDA support does not cover SM121, and
dictation-length audio takes a few seconds on GB10's cores without touching the model server's GPU.
It answers `/v1/audio/transcriptions` on `127.0.0.1:8100`, where `ling web`'s
`/api/transcribe` and the desktop app's main process send the microphone's audio; the text goes
into the composer, never straight to the model.

Until Onyx was retired it was created by `ling-admin chat configure` on Onyx's network. It is now
created on the sidecar network (DOCKER §6), and one found on any other network is replaced; its
named volume keeps the downloaded model either way. Transcription is local, so voice works at
`/airgapped on`.

Nothing here prints: a failure is kept in `SpeechSidecar.problem` for the CLI to say.
"""

import subprocess
from typing import ClassVar, Final, List, Optional

from dreamference.chat.sidecar_network import SIDECAR_NETWORK, SidecarNetwork

STT_CONTAINER_NAME: Final[str] = "dreamference-stt"
STT_IMAGE: Final[str] = "ghcr.io/speaches-ai/speaches:latest-cpu"
STT_HOST_PORT: Final[int] = 8100
STT_MODEL: Final[str] = "Systran/faster-whisper-small"
# The model downloads into this volume once (~500 MB), not on every boot.
STT_CACHE_VOLUME: Final[str] = f"{STT_CONTAINER_NAME}-cache"
RUNNING: Final[str] = "running"


class SpeechSidecar:
    """Starts, stops and reports the speech-to-text sidecar."""

    # Why the last start failed, for the CLI to print; empty after a success.
    problem: ClassVar[str] = ""

    @classmethod
    def state(cls) -> str:
        """
        Returns:
            str: `running`, `exited`, another Docker state, or "" when there is no container.
        """
        result = subprocess.run(
            ["docker", "inspect", STT_CONTAINER_NAME, "--format", "{{.State.Status}}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    @classmethod
    def run_command(cls) -> List[str]:
        """Builds the `docker run` command.

        Returns:
            List[str]: The argv: on the sidecar network, published on loopback only.
        """
        return [
            "docker", "run", "-d", "--name", STT_CONTAINER_NAME,
            "--restart", "unless-stopped", "--network", SIDECAR_NETWORK,
            "-p", f"127.0.0.1:{STT_HOST_PORT}:8000",
            "-v", f"{STT_CACHE_VOLUME}:/home/ubuntu/.cache/huggingface",
            STT_IMAGE,
        ]  # fmt: skip

    @classmethod
    def start(cls, wait_for_model: bool = True) -> bool:
        """Makes the server run on the sidecar network and fetches its model.

        A stopped container on the sidecar network is started; one on another network (Onyx's,
        the default bridge) is replaced.

        Args:
            wait_for_model (bool): Wait for the model download (~500 MB once); otherwise it runs
                in the container's background (`server start`, which must not wait for it).

        Returns:
            bool: True if the container is running once this returns.
        """
        cls.problem = ""
        if not SidecarNetwork.ensure():
            cls.problem = f"Could not create the Docker network {SIDECAR_NETWORK}."
            return False
        mode = SidecarNetwork.network_mode(STT_CONTAINER_NAME)
        if mode and mode != SIDECAR_NETWORK:
            subprocess.run(["docker", "rm", "-f", STT_CONTAINER_NAME], capture_output=True, timeout=60, check=False)
        state = cls.state()
        if not state:
            result = subprocess.run(cls.run_command(), capture_output=True, text=True, timeout=600, check=False)
            if result.returncode != 0:
                cls.problem = f"Could not start the speech-to-text server: {result.stderr.strip()[:200]}"
                return False
        elif state != RUNNING:
            started = subprocess.run(["docker", "start", STT_CONTAINER_NAME],
                                     capture_output=True, text=True, timeout=60, check=False)
            if started.returncode != 0:
                cls.problem = f"Could not start {STT_CONTAINER_NAME}: {started.stderr.strip()[:200]}"
                return False
        # The model downloads on demand; fetching it now keeps the first dictation from timing out.
        subprocess.run(
            ["docker", "exec", *([] if wait_for_model else ["-d"]), STT_CONTAINER_NAME, "curl", "-s", "-X", "POST",
             f"http://127.0.0.1:8000/v1/models/{STT_MODEL}"],
            capture_output=True, timeout=900, check=False,
        )
        return True

    @classmethod
    def ensure_on_node(cls) -> Optional[bool]:
        """Starts the server on a node when it is absent, as `server start` does for the Google
        service (decided 2026-10-09). Never raises, and the model downloads in the background:
        the model server must start anyway.

        Returns:
            Optional[bool]: None when this machine is not a node or the container exists already
            (running or not); otherwise whether it started (see `problem` when it did not).
        """
        from dreamference.node.node_identity import NodeIdentity

        try:
            if not NodeIdentity.read() or cls.state():
                return None
            return cls.start(wait_for_model=False)
        except (OSError, subprocess.SubprocessError) as exc:
            cls.problem = f"Speech-to-text was not started: {exc}"
            return False

    @classmethod
    def stop(cls) -> bool:
        """Removes the container; the volume keeps the model.

        Returns:
            bool: True if no container is left.
        """
        subprocess.run(["docker", "rm", "-f", STT_CONTAINER_NAME], capture_output=True, timeout=60, check=False)
        return not cls.state()

    @classmethod
    def status_lines(cls) -> List[str]:
        """
        Returns:
            List[str]: The container's state and network, and where the audio goes.
        """
        state = cls.state()
        network = SidecarNetwork.network_mode(STT_CONTAINER_NAME) if state else ""
        lines = [f"Speech-to-text ({STT_CONTAINER_NAME}): {state or 'absent'}" + (f" on {network}" if network else "")]
        lines.append(f"Model: {STT_MODEL}, on the CPU, at http://127.0.0.1:{STT_HOST_PORT}/v1/audio/transcriptions")
        if not state:
            lines.append("The microphone in the app and in `ling web` needs it: run `ling-admin voice start`.")
        return lines
