"""
OpenHands Autonomous Software Engineer Session Runner for Dreamference.

This module provides the OpenHandsRunner class which verifies Docker availability, pulls
the OpenHands image, mounts workspace directory into container sandbox, configures local vLLM endpoint,
and launches the OpenHands web UI.
"""

import os
import subprocess
import time
from typing import Final, List, Optional

from dreamference.config import DreamferenceConfig
from dreamference.vllm_server import VLLMServerManager
from dreamference.runner.openhands_installer import OpenHandsInstaller, OPENHANDS_IMAGE
from dreamference.hardware import resolve_model_hf_repo

# Where the OpenHands web UI is published on the host: 3001, as since the retired Onyx web UI owned 3000.
OPENHANDS_HOST_PORT: Final[int] = 3001

class OpenHandsRunner:
    """
    Runner class orchestrating OpenHands docker container agent sessions on local GB10 vLLM endpoints.
    """

    def __init__(self, config: Optional[DreamferenceConfig] = None):
        """
        Initializes OpenHandsRunner with configuration and vLLM server manager instances.

        Args:
            config (Optional[DreamferenceConfig]): Configuration instance (defaults to DreamferenceConfig()).
        """
        self.config: DreamferenceConfig = config or DreamferenceConfig()
        self.vllm_manager: VLLMServerManager = VLLMServerManager(host=self.config.vllm_host)

    def run_session(self, prompt: Optional[str] = None, debug: bool = False) -> int:
        """
        Ensures local vLLM server is running, checks Docker daemon, pulls OpenHands image,
        and launches the OpenHands web UI container on http://localhost:3001 (this machine only).

        Args:
            prompt (Optional[str]): Task prompt text.
            debug (bool): Enable debug logging.

        Returns:
            int: Subprocess exit code (0 for success).
        """
        # Step 1: Ensure local vLLM endpoint is online
        if not self.vllm_manager.check_health():
            from dreamference.runner.vllm_readiness_waiter import VLLMReadinessWaiter
            if not VLLMReadinessWaiter(config=self.config).wait_for_vllm():
                return 1

        # Step 2: Ensure Docker is running
        if not OpenHandsInstaller.is_docker_available():
            print("❌ Docker daemon is not running or accessible.")
            print("💡 Start Docker to run OpenHands autonomous agent sessions.")
            return 1

        # Step 3: Pull OpenHands image
        OpenHandsInstaller.pull_image_if_missing()

        hf_model = resolve_model_hf_repo(self.config.model)
        cwd = os.getcwd()
        container_name = "dreamference-openhands"

        # Remove stale container if present
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # From inside the container `localhost` is the container itself, so a loopback vLLM URL
        # is rewritten to the Docker bridge gateway.
        from dreamference.chat.docker_bridge import DockerBridge

        api_base = DockerBridge.container_vllm_url(self.config.vllm_host)

        cmd: List[str] = [
            "docker", "run", "--rm", "-it",
            "--name", container_name,
            "-e", f"LLM_MODEL=openai/{hf_model}",
            "-e", f"LLM_BASE_URL={api_base}",
            "-e", "LLM_API_KEY=gb10-local-token",
            "-e", f"WORKSPACE_BASE={cwd}",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{cwd}:/opt/workspace_base",
            # 3001, as it has been since Onyx owned 3000. And 127.0.0.1 only: this container mounts
            # the Docker socket, so its UI on the network would hand root on this host to anyone
            # who can reach it.
            "-p", f"127.0.0.1:{OPENHANDS_HOST_PORT}:3000",
            OPENHANDS_IMAGE
        ]

        print(f"\n🚀 Launching OpenHands Autonomous Agent Web UI on http://localhost:{OPENHANDS_HOST_PORT}...")
        print(f"   Local LLM Model: openai/{hf_model} ({self.config.vllm_host})")
        print(f"   Workspace Dir:   {cwd}\n")

        try:
            return subprocess.call(cmd)
        except Exception as e:
            print(f"❌ Failed to launch OpenHands: {e}")
            return 1
