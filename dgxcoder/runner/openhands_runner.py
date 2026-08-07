"""
OpenHands Autonomous Software Engineer Session Runner for DGXCoder.

This module provides the OpenHandsRunner class which verifies Docker availability, pulls
the OpenHands image, mounts workspace directory into container sandbox, configures local vLLM endpoint,
and launches the OpenHands web UI.
"""

import os
import subprocess
import time
from typing import Optional, List

from dgxcoder.config import DGXCoderConfig
from dgxcoder.vllm_server import VLLMServerManager
from dgxcoder.runner.openhands_installer import OpenHandsInstaller, OPENHANDS_IMAGE
from dgxcoder.hardware import resolve_model_hf_repo

class OpenHandsRunner:
    """
    Runner class orchestrating OpenHands docker container agent sessions on local GB10 vLLM endpoints.
    """

    def __init__(self, config: Optional[DGXCoderConfig] = None):
        """
        Initializes OpenHandsRunner with configuration and vLLM server manager instances.

        Args:
            config (Optional[DGXCoderConfig]): Configuration instance (defaults to DGXCoderConfig()).
        """
        self.config: DGXCoderConfig = config or DGXCoderConfig()
        self.vllm_manager: VLLMServerManager = VLLMServerManager(host=self.config.vllm_host)

    def run_session(self, prompt: Optional[str] = None, debug: bool = False) -> int:
        """
        Ensures local vLLM server is running, checks Docker daemon, pulls OpenHands image,
        and launches OpenHands web UI container mapped to http://localhost:3000.

        Args:
            prompt (Optional[str]): Task prompt text.
            debug (bool): Enable debug logging.

        Returns:
            int: Subprocess exit code (0 for success).
        """
        # Step 1: Ensure local vLLM endpoint is online
        if not self.vllm_manager.check_health():
            from dgxcoder.runner.goose_runner import GooseRunner
            goose_runner = GooseRunner(config=self.config)
            if not goose_runner.wait_for_vllm():
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
        container_name = "dgxcoder-openhands"

        # Remove stale container if present
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        api_base = f"{self.config.vllm_host.rstrip('/')}/v1"

        cmd: List[str] = [
            "docker", "run", "--rm", "-it",
            "--name", container_name,
            "-e", f"LLM_MODEL=openai/{hf_model}",
            "-e", f"LLM_BASE_URL={api_base}",
            "-e", "LLM_API_KEY=gb10-local-token",
            "-e", f"WORKSPACE_BASE={cwd}",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{cwd}:/opt/workspace_base",
            "-p", "3000:3000",
            OPENHANDS_IMAGE
        ]

        print(f"\n🚀 Launching OpenHands Autonomous Agent Web UI on http://localhost:3000...")
        print(f"   Local LLM Model: openai/{hf_model} ({self.config.vllm_host})")
        print(f"   Workspace Dir:   {cwd}\n")

        try:
            return subprocess.call(cmd)
        except Exception as e:
            print(f"❌ Failed to launch OpenHands: {e}")
            return 1
