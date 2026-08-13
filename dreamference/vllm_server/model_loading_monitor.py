"""
Model Loading Monitor for vLLM Server.

This module provides real-time monitoring of model loading progress during vLLM server initialization.
It tracks loading stages, memory usage, and provides user-friendly status updates.
"""

import threading
import time
import re
from typing import Optional, Callable, List
from datetime import datetime


class ModelLoadingMonitor:
    """
    Monitors vLLM model loading progress via subprocess logs and HTTP health checks.
    Provides real-time feedback to users during server startup.
    """

    # Key vLLM startup milestones to track
    LOADING_STAGES = [
        ("downloading", r"Downloading|downloading|hf_hub_download"),
        ("loading", r"Loading model|loading model|Deserializing|load.*model"),
        ("warming_up", r"Warm up the model|warming up|Initializing|initialize"),
        ("ready", r"Uvicorn running|started server|ready|listening on"),
    ]

    def __init__(self, get_logs_fn: Callable[[], List[str]], check_health_fn: Callable[[], bool], get_memory_fn: Optional[Callable[[], Optional[str]]] = None, recipe_env_keys: Optional[set] = None):
        """
        Initializes the model loading monitor.

        Args:
            get_logs_fn: Function that returns new log lines from vLLM server.
            check_health_fn: Function that checks if server is healthy (HTTP health check).
            get_memory_fn: Optional function returning Docker container reserved memory string.
            recipe_env_keys: Set of environment variable names injected from launch overrides.
        """
        self.get_logs_fn = get_logs_fn
        self.check_health_fn = check_health_fn
        self.get_memory_fn = get_memory_fn
        self.recipe_env_keys = recipe_env_keys or set()
        self.monitor_thread: Optional[threading.Thread] = None
        self.running = False
        self.stages_reached: set = set()
        self.start_time: Optional[float] = None
        self.server_ready = False

    def start(self) -> None:
        """Starts the monitoring thread."""
        if self.running:
            return
        self.running = True
        self.start_time = time.time()
        self.stages_reached = set()
        self.server_ready = False
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop(self) -> None:
        """Stops the monitoring thread."""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2.0)

    def _monitor_loop(self) -> None:
        """Main monitoring loop that runs in background thread."""
        last_log_check = 0
        health_check_interval = 2
        last_health_check = 0
        memory_print_interval = 10
        last_memory_print = 0

        while self.running:
            current_time = time.time()

            # Check logs frequently for stage updates
            if current_time - last_log_check > 0.5:
                self._check_logs()
                last_log_check = current_time

            # Check HTTP health status periodically
            if current_time - last_health_check > health_check_interval:
                if self._check_server_health():
                    self.server_ready = True
                    self.stages_reached.add("ready")
                last_health_check = current_time

            # Print Docker reserved memory every 10 seconds
            if self.get_memory_fn and current_time - last_memory_print > memory_print_interval:
                mem = self.get_memory_fn()
                if mem:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] 📊 Reserved memory (Docker): {mem}")
                last_memory_print = current_time

            time.sleep(0.2)

    def _check_logs(self) -> None:
        """Checks for new log lines and extracts loading stage information."""
        try:
            logs = self.get_logs_fn()
            for log_line in logs:
                # Suppress benign NVIDIA container warnings about its own injected variables
                if "Unknown vLLM environment variable detected" in log_line:
                    if "VLLM_VERSION" in log_line or "VLLM_FLASH_ATTN_SRC_DIR" in log_line:
                        self._process_log_line(log_line)
                        continue
                print(log_line.rstrip())
                self._process_log_line(log_line)
        except Exception:
            pass

    def _process_log_line(self, line: str) -> None:
        """
        Processes a single log line to detect loading stages.

        Args:
            line (str): A log line from vLLM server.
        """
        line_lower = line.lower()
        
        # Check for unknown vLLM environment variables
        unknown_env_match = re.search(r"unknown vllm environment variable[:\s']*([^'\s]+)", line_lower, re.IGNORECASE)
        if unknown_env_match:
            var_name = unknown_env_match.group(1).upper()
            if var_name in self.recipe_env_keys:
                print(f"\n[bold red]FATAL ERROR: vLLM rejected recipe-supplied environment variable '{var_name}'.[/bold red]")
                print("[bold red]This usually indicates a typo or a change in vLLM version that breaks the required kernel pin.[/bold red]")
                import sys
                sys.exit(1)

        for stage_name, pattern in self.LOADING_STAGES:
            if re.search(pattern, line_lower, re.IGNORECASE):
                self.stages_reached.add(stage_name)
                break

    def _check_server_health(self) -> bool:
        """Checks if server is responding to health checks."""
        try:
            is_healthy = self.check_health_fn()
            if is_healthy:
                self.server_ready = True
            return is_healthy
        except Exception:
            return False

    def get_status(self) -> dict:
        """
        Gets current loading status.

        Returns:
            dict: Status information including elapsed time, stages, and readiness.
        """
        elapsed = time.time() - self.start_time if self.start_time else 0
        return {
            "elapsed_seconds": elapsed,
            "stages_reached": sorted(list(self.stages_reached)),
            "server_ready": self.server_ready,
        }

    def wait_for_ready(self, timeout: float = 300.0) -> bool:
        """
        Blocks until server is ready or timeout is reached.

        Args:
            timeout (float): Maximum seconds to wait.

        Returns:
            bool: True if server became ready, False if timeout.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.server_ready:
                return True
            time.sleep(0.5)
        return False

    def print_progress(self) -> None:
        """Prints current loading progress to stdout."""
        status = self.get_status()
        elapsed = status["elapsed_seconds"]
        stages = status["stages_reached"]
        ready = status["server_ready"]

        timestamp = datetime.now().strftime("%H:%M:%S")

        # Build status line
        status_symbols = {
            "downloading": "📥",
            "loading": "⚙️",
            "warming_up": "🔥",
            "ready": "✅",
        }

        progress = " → ".join(
            f"{status_symbols.get(s, '•')} {s.replace('_', ' ')}"
            for s in stages
        )

        ready_indicator = "✅ READY" if ready else "⏳ Loading"
        print(f"[{timestamp}] {ready_indicator} ({elapsed:.1f}s) {progress}")


def create_model_loading_monitor(vllm_manager, recipe_env_keys: Optional[set] = None) -> ModelLoadingMonitor:
    """
    Factory function to create a monitor for a VLLMServerManager instance.

    Args:
        vllm_manager: VLLMServerManager instance to monitor.
        recipe_env_keys: Optional set of environment variables injected from launch overrides.

    Returns:
        ModelLoadingMonitor: Configured monitor instance.
    """
    return ModelLoadingMonitor(
        get_logs_fn=vllm_manager.get_new_logs,
        check_health_fn=vllm_manager.check_health,
        get_memory_fn=vllm_manager.get_container_reserved_memory,
        recipe_env_keys=recipe_env_keys,
    )
