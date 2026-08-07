"""
vLLM Server Startup Monitor for DGXCoder.

This module provides the VLLMStartupMonitor class which runs a dedicated background thread
monitoring vLLM server startup progress, detecting stalled log output, and logging explicit
diagnostic status updates instead of printing raw dots.
"""

import time
import threading
from typing import Optional, Callable

class VLLMStartupMonitor:
    """
    Dedicated monitoring thread that detects and logs potential vLLM startup stalls,
    weight loading progress, and CUDA graph capture phases.
    """

    def __init__(
        self,
        warn_timeout_sec: float = 45.0,
        stuck_threshold_sec: float = 120.0,
        check_interval_sec: float = 1.0,
        log_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Initializes VLLMStartupMonitor.

        Args:
            warn_timeout_sec (float): Inactivity threshold before printing progress status (default 45s).
            stuck_threshold_sec (float): Inactivity threshold before flagging potential stall (default 120s).
            check_interval_sec (float): Monitoring thread polling frequency in seconds (default 1s).
            log_callback (Optional[Callable[[str], None]]): Callback function for logging messages.
        """
        self.warn_timeout_sec: float = warn_timeout_sec
        self.stuck_threshold_sec: float = stuck_threshold_sec
        self.check_interval_sec: float = check_interval_sec
        self.log_callback: Callable[[str], None] = log_callback or (lambda msg: print(msg))

        self.last_log_time: float = time.time()
        self.start_time: float = time.time()
        self.is_running: bool = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.warned: bool = False

    def notify_log_received(self) -> None:
        """Resets inactivity timer whenever a new vLLM log line is received."""
        self.last_log_time = time.time()
        self.warned = False

    def start(self) -> None:
        """Starts background monitoring thread."""
        self.start_time = time.time()
        self.last_log_time = time.time()
        self.is_running = True
        self.warned = False
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop(self) -> None:
        """Stops background monitoring thread."""
        self.is_running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=1.0)

    def _monitor_loop(self) -> None:
        """Background thread loop inspecting startup elapsed time and log freshness."""
        while self.is_running:
            time.sleep(self.check_interval_sec)
            if not self.is_running:
                break

            now = time.time()
            elapsed_total = int(now - self.start_time)
            elapsed_since_log = int(now - self.last_log_time)

            if (now - self.last_log_time) >= self.stuck_threshold_sec:
                if not self.warned:
                    self.log_callback(
                        f"⚠️ [vLLM Monitor] No new logs received for {elapsed_since_log}s "
                        f"(Total elapsed: {elapsed_total}s). vLLM may be performing CUDA graph capture or model weight loading on GB10."
                    )
                    self.warned = True
            elif (now - self.last_log_time) >= self.warn_timeout_sec:
                if not self.warned:
                    self.log_callback(
                        f"⏳ [vLLM Monitor] Initializing model weights into GB10 memory... "
                        f"({elapsed_total}s elapsed, quiet for {elapsed_since_log}s)"
                    )
                    self.warned = True
