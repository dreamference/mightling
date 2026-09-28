"""
Diffusion sidecar lifecycle for Dreamference.

This module provides DiffusionServerManager, which runs the configured diffusion model beside
the main vLLM server. It lives in `vllm_server/` rather than a package of its own because this
package is, in practice, the model-serving-lifecycle subsystem — one more manager and one
serving script did not earn an eighth top-level package.

A diffusion checkpoint cannot be served by vLLM (its remote code generates by block denoising,
not autoregression), so the sidecar is a plain `docker run` of the same image the main model
uses — guaranteed present on any machine that serves the main model, and guaranteed to carry
the torch + transformers build for this architecture — with
`diffusion_openai_service.py` bind-mounted in as the entrypoint.

No PSI watchdog and no host-safety gate: the container runs under a fixed memory cap small
enough that its worst case sits below any pressure threshold, which is the hardening a 0.6B
model needs. `dream server start` starts the sidecar *before* the vLLM launch on purpose —
vLLM's pre-flight reads current free memory, so a sidecar already resident is accounted for,
where the reverse order lets a marginal KV check pass and then lose the sidecar's memory
mid-load.
"""

import os
import subprocess
from pathlib import Path
from typing import Final, Optional

import requests

from dreamference.vllm_server.vllm_server_manager import DEFAULT_VLLM_IMAGE

DEFAULT_DIFFUSION_PORT: Final[int] = 8001
DEFAULT_DIFFUSION_HOST: Final[str] = "http://localhost:8001"

# Worst case for the models this serves is ~3 GB (weights + activations + CUDA context); the cap
# turns a runaway load into a contained OOM kill instead of host memory pressure.
CONTAINER_MEMORY_CAP_GB: Final[int] = 8

# Same first-to-die bias the vLLM container gets.
CONTAINER_OOM_SCORE_ADJ: Final[int] = 800

# Where the serving script lands inside the container.
CONTAINER_SERVICE_PATH: Final[str] = "/opt/dreamference/diffusion_openai_service.py"


class DiffusionServerManager:
    """
    Manages the Docker container serving the diffusion model.

    Mirrors VLLMServerManager's start/stop/remove/check_health surface at sidecar scale: no
    launch-arg recipe layering, no watchdog, no pre-flight gates — a fixed memory cap does the
    containment a 0.6B load needs.
    """

    def __init__(self, host: str = DEFAULT_DIFFUSION_HOST):
        """
        Initializes DiffusionServerManager with target host endpoint URL.

        Args:
            host (str): Target HTTP endpoint URL (default 'http://localhost:8001').
        """
        self.host: str = host.rstrip("/")

    def check_health(self, timeout: float = 2.0) -> bool:
        """
        Reports whether the sidecar is up with its model loaded.

        Args:
            timeout (float): Request timeout in seconds.

        Returns:
            bool: True when /health answers 200 (the service returns 503 while loading).
        """
        try:
            return requests.get(f"{self.host}/health", timeout=timeout).status_code == 200
        except Exception:
            return False

    def get_load_state(self, timeout: float = 2.0) -> Optional[str]:
        """
        Returns the sidecar's own description of its load state, if reachable.

        Args:
            timeout (float): Request timeout in seconds.

        Returns:
            Optional[str]: 'ok', 'loading', the load error text, or None when unreachable.
        """
        try:
            payload = requests.get(f"{self.host}/health", timeout=timeout).json()
        except Exception:
            return None
        if payload.get("status") == "error":
            return payload.get("error") or "error"
        return payload.get("status")

    @classmethod
    def resolve_docker_image(cls, diffusion_model: str, main_model: str) -> str:
        """
        Picks the image the sidecar runs in.

        The diffusion entry's own `launch_overrides['docker_image']` wins if set — the same
        per-model pin every vLLM recipe gets. Otherwise the sidecar rides in the *main* model's
        resolved image rather than DEFAULT_VLLM_IMAGE: the default is a bare tag that
        `ensure_docker_image` would *build*, and on a machine whose main model pins its own
        image, that default has plausibly never been built — falling back to it would trigger a
        surprise multi-gigabyte docker build to serve a 0.6B model. The main model's image is
        present by construction on any machine that serves the main model.

        Args:
            diffusion_model (str): Diffusion model alias or HF repo ID.
            main_model (str): Main model alias or HF repo ID.

        Returns:
            str: Docker image reference.
        """
        from dreamference.hardware import get_model_launch_overrides

        own = get_model_launch_overrides(diffusion_model).get("docker_image")
        if own:
            return own
        return get_model_launch_overrides(main_model).get("docker_image", DEFAULT_VLLM_IMAGE)

    @classmethod
    def service_script_path(cls) -> Path:
        """
        Returns the host path of the serving script to bind-mount.

        Returns:
            Path: Absolute path of diffusion_openai_service.py in the installed package.
        """
        return Path(__file__).parent / "diffusion_openai_service.py"

    def build_launch_command(
        self,
        model: str,
        main_model: str,
        port: int = DEFAULT_DIFFUSION_PORT,
        hf_token: Optional[str] = None,
    ) -> list:
        """
        Constructs the `docker run` command for the diffusion sidecar.

        Args:
            model (str): Diffusion model alias or HF repo ID.
            main_model (str): Main model alias, used only to resolve the fallback image.
            port (int): Host port the service binds (the container runs with host networking).
            hf_token (Optional[str]): HuggingFace token for gated checkpoints.

        Returns:
            list: Full docker run argv.
        """
        from dreamference.hardware import resolve_model_hf_repo

        hf_repo = resolve_model_hf_repo(model)
        docker_image = self.resolve_docker_image(model, main_model)
        hf_cache = os.path.expanduser("~/.cache/huggingface")
        os.makedirs(hf_cache, exist_ok=True)

        cmd = [
            "docker", "run", "-d",
            "--network", "host",
            "--restart", "unless-stopped",
            "--name", f"dreamference-diffusion-{port}",
            "--gpus", "all",
            f"--memory={CONTAINER_MEMORY_CAP_GB}g",
            f"--memory-swap={CONTAINER_MEMORY_CAP_GB}g",
            f"--oom-score-adj={CONTAINER_OOM_SCORE_ADJ}",
            "-v", f"{hf_cache}:/root/.cache/huggingface",
            "-v", f"{self.service_script_path()}:{CONTAINER_SERVICE_PATH}:ro",
            "-e", f"DREAMFERENCE_DIFFUSION_MODEL_ID={hf_repo}",
            "-e", f"DREAMFERENCE_DIFFUSION_PORT={port}",
        ]
        if hf_token:
            cmd.extend(["-e", f"HF_TOKEN={hf_token}"])
        cmd.extend(["--entrypoint", "python3", docker_image, CONTAINER_SERVICE_PATH])
        return cmd

    def start_server(
        self,
        model: str,
        main_model: str,
        port: int = DEFAULT_DIFFUSION_PORT,
        hf_token: Optional[str] = None,
    ) -> bool:
        """
        Starts the diffusion sidecar container, replacing any previous one on the port.

        The model loads in the background inside the container; callers that need the endpoint
        live should poll :meth:`check_health`.

        Args:
            model (str): Diffusion model alias or HF repo ID.
            main_model (str): Main model alias, used to resolve the fallback image.
            port (int): Host port for the service.
            hf_token (Optional[str]): HuggingFace token for gated checkpoints.

        Returns:
            bool: True when `docker run` accepted the container.
        """
        container_name = f"dreamference-diffusion-{port}"
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # No image-presence check on purpose: on a machine that has never pulled the main
        # model's image, this `docker run` pulls it synchronously and sits silent for minutes —
        # but it is the same image the vLLM launch would pull anyway, so nothing is wasted,
        # merely early. Not worth an ensure_docker_image path.
        cmd = self.build_launch_command(model, main_model, port=port, hf_token=hf_token)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"⚠️  Diffusion sidecar failed to start: {result.stderr.strip()}")
            return False
        print(f"🌫️  Diffusion sidecar '{container_name}' started ({model}) at {self.host}")
        return True

    def stop_server(self, port: int = DEFAULT_DIFFUSION_PORT) -> None:
        """
        Stops the diffusion sidecar container.

        Args:
            port (int): Port the sidecar was started on.
        """
        container_name = f"dreamference-diffusion-{port}"
        print(f"🛑 Stopping diffusion container: {container_name}")
        subprocess.run(["docker", "stop", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"✅ Container {container_name} stopped.")

    def remove_server(self, port: int = DEFAULT_DIFFUSION_PORT) -> None:
        """
        Removes the diffusion sidecar container.

        Args:
            port (int): Port the sidecar was started on.
        """
        container_name = f"dreamference-diffusion-{port}"
        print(f"🗑️ Removing diffusion container: {container_name}")
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
