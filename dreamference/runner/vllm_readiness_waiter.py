"""
Model Server Readiness Waiter.

This module provides the VLLMReadinessWaiter class, which the IDE and container agent runners use
to wait for the local model server to answer before they start their agent.
"""

import json
import sys
import time
import urllib.request
from typing import Optional

from dreamference.config import DreamferenceConfig
from dreamference.vllm_server import VLLMServerManager


class VLLMReadinessWaiter:
    """
    Polls the configured model server until it is healthy and able to complete a chat request.
    """

    def __init__(self, config: Optional[DreamferenceConfig] = None):
        """
        Initializes the waiter with a configuration and a server manager for its host.

        Args:
            config (Optional[DreamferenceConfig]): Configuration instance (defaults to DreamferenceConfig()).
        """
        self.config: DreamferenceConfig = config or DreamferenceConfig()
        self.vllm_manager: VLLMServerManager = VLLMServerManager(host=self.config.vllm_host)

    def wait_for_vllm(self, poll_interval: float = 1.0, max_wait: Optional[float] = 600.0) -> bool:
        """
        Polls the model server's health endpoint, printing progress dots, until it can serve.

        Args:
            poll_interval (float): Seconds between polls.
            max_wait (Optional[float]): Seconds to wait before giving up, or None to wait forever.

        Returns:
            bool: True once the server is healthy and answers a completion, False on timeout or
                cancellation.
        """
        start_time = time.time()
        print(f"⏳ Waiting for local vLLM server at {self.config.vllm_host} to become available...")

        while True:
            try:
                if self.vllm_manager.check_health(timeout=1.0) and self._pre_warm():
                    print("\n✅ vLLM server is online and ready!")
                    return True
            except (KeyboardInterrupt, SystemExit):
                print("\n🛑 Cancelled checking vLLM server.")
                return False

            if max_wait is not None and (time.time() - start_time) > max_wait:
                print(f"\n❌ Timed out waiting for vLLM server after {max_wait} seconds.")
                print("💡 Start vLLM in another terminal via: `ling-admin server start`")
                return False

            sys.stdout.write(".")
            sys.stdout.flush()
            time.sleep(poll_interval)

    def _pre_warm(self) -> bool:
        """
        Sends a one-token chat completion, which succeeds only once the model can generate.

        A healthy `/health` answer arrives before the engine is ready to serve, so this is the
        check that the agent's first request will not fail.

        Returns:
            bool: True if the completion returned HTTP 200, False otherwise.
        """
        try:
            from dreamference.hardware import resolve_model_hf_repo

            payload = {
                # The repo ID, not the alias: the server names the model after the checkpoint it
                # was launched with, so a request for the alias answers 404 and this never passes.
                "model": resolve_model_hf_repo(self.config.model),
                "messages": [{"role": "user", "content": "Ready?"}],
                "max_tokens": 1,
                "temperature": 0.0,
            }
            req = urllib.request.Request(
                f"{self.config.vllm_host}/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False
