"""
vLLM Server Startup Monitor for Dreamng.

This module provides the VLLMStartupMonitor class which runs a dedicated background thread
monitoring vLLM server startup progress, estimating load percentage from vLLM-process-specific
memory growth, detecting stalled log output, and logging explicit diagnostic status updates.
"""

import re
import time
import threading
from typing import Optional, Callable, Tuple

class VLLMStartupMonitor:
    """
    Dedicated monitoring thread that detects and logs potential vLLM startup stalls,
    estimates weight-load progress from vLLM-process-specific memory growth, and reports status.
    """

    def __init__(
        self,
        warn_timeout_sec: float = 45.0,
        stuck_threshold_sec: float = 120.0,
        check_interval_sec: float = 1.0,
        progress_interval_sec: float = 5.0,
        expected_memory_gb: Optional[float] = None,
        log_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Initializes VLLMStartupMonitor.

        Args:
            warn_timeout_sec (float): Inactivity threshold before printing progress status (default 45s).
            stuck_threshold_sec (float): Inactivity threshold before flagging potential stall (default 120s).
            check_interval_sec (float): Monitoring thread polling frequency in seconds (default 1s).
            progress_interval_sec (float): Minimum interval between memory-progress updates (default 5s).
            expected_memory_gb (Optional[float]): Expected model memory footprint in GB for % progress.
            log_callback (Optional[Callable[[str], None]]): Callback function for logging messages.
        """
        self.warn_timeout_sec: float = warn_timeout_sec
        self.stuck_threshold_sec: float = stuck_threshold_sec
        self.check_interval_sec: float = check_interval_sec
        self.progress_interval_sec: float = progress_interval_sec
        self.expected_memory_gb: Optional[float] = expected_memory_gb
        self.log_callback: Callable[[str], None] = log_callback or (lambda msg: print(msg))

        self.last_log_time: float = time.time()
        self.start_time: float = time.time()
        self.is_running: bool = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.warned: bool = False
        self.baseline_used_gb: float = 0.0
        self.last_progress_time: float = 0.0
        self.last_reported_pct: int = -1
        self.vllm_pid: Optional[int] = None

    def notify_log_received(self, log_line: str = "") -> None:
        """Resets inactivity timer whenever a new vLLM log line is received. Extracts PID if present."""
        self.last_log_time = time.time()
        self.warned = False
        # Extract PID from log lines like "[vLLM] (EngineCore pid=149)"
        if log_line and not self.vllm_pid:
            match = re.search(r'EngineCore\s+pid[=:\s]+(\d+)', log_line)
            if match:
                self.vllm_pid = int(match.group(1))

    def _read_used_memory_gb(self) -> float:
        """
        Reads current memory used by the vLLM process from /proc/<pid>/status.
        Falls back to system memory if PID is not yet available.
        """
        # If we have the vLLM PID, measure only that process's memory
        if self.vllm_pid:
            try:
                with open(f"/proc/{self.vllm_pid}/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            # VmRSS is resident set size in KB
                            rss_kb = float(line.split()[1])
                            return rss_kb / (1024 * 1024)  # Convert KB to GB
            except (FileNotFoundError, ValueError, IOError):
                pass
        
        # Fallback to system memory if PID not available yet
        try:
            from dreamng.hardware import get_system_memory
            return float(get_system_memory().get("used_gb", 0.0))
        except Exception:
            return 0.0

    def estimate_progress(self) -> Tuple[Optional[int], float, Optional[float]]:
        """
        Estimates load progress from memory growth since monitor start.

        Returns:
            Tuple[Optional[int], float, Optional[float]]: (percent 0-99, delta_gb, expected_gb).
            Percent is None when expected_memory_gb is unset or baseline is unavailable.
        """
        current_used = self._read_used_memory_gb()
        delta_gb = max(0.0, current_used - self.baseline_used_gb)
        if not self.expected_memory_gb or self.expected_memory_gb <= 0:
            return None, delta_gb, self.expected_memory_gb
        # Cap at 99 until the HTTP health check confirms readiness.
        pct = min(99, int((delta_gb / self.expected_memory_gb) * 100))
        return pct, delta_gb, self.expected_memory_gb

    def _format_progress_suffix(self) -> str:
        """Builds a human-readable progress suffix from memory measurements."""
        pct, delta_gb, expected = self.estimate_progress()
        if pct is not None and expected:
            return f"~{pct}% ({delta_gb:.1f}/{expected:.0f} GB since start)"
        if delta_gb > 0:
            return f"+{delta_gb:.1f} GB used since start"
        return "awaiting memory growth"

    def start(self) -> None:
        """Starts background monitoring thread and captures baseline memory usage."""
        self.start_time = time.time()
        self.last_log_time = time.time()
        self.last_progress_time = 0.0
        self.last_reported_pct = -1
        self.is_running = True
        self.warned = False
        self.baseline_used_gb = self._read_used_memory_gb()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop(self) -> None:
        """Stops background monitoring thread."""
        self.is_running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=1.0)

    def _maybe_emit_progress(self, now: float, elapsed_total: int, force: bool = False) -> None:
        """Emits a progress line when interval elapsed or percentage advanced."""
        pct, _delta_gb, _expected = self.estimate_progress()
        interval_elapsed = (now - self.last_progress_time) >= self.progress_interval_sec
        pct_advanced = pct is not None and pct >= self.last_reported_pct + 1
        if not force and not interval_elapsed and not pct_advanced:
            return

        progress = self._format_progress_suffix()
        self.log_callback(
            f"⏳ [vLLM Monitor] Loading weights into GB10 memory: {progress} "
            f"({elapsed_total}s elapsed)"
        )
        self.last_progress_time = now
        if pct is not None:
            self.last_reported_pct = pct

    def _monitor_loop(self) -> None:
        """Background thread loop inspecting memory growth, elapsed time, and log freshness."""
        while self.is_running:
            time.sleep(self.check_interval_sec)
            if not self.is_running:
                break

            now = time.time()
            elapsed_total = int(now - self.start_time)
            elapsed_since_log = int(now - self.last_log_time)
            progress = self._format_progress_suffix()

            if (now - self.last_log_time) >= self.stuck_threshold_sec:
                if not self.warned:
                    self.log_callback(
                        f"⚠️ [vLLM Monitor] No new logs for {elapsed_since_log}s "
                        f"(total {elapsed_total}s, {progress}). "
                        f"vLLM may still be loading weights or capturing CUDA graphs on GB10."
                    )
                    self.warned = True
                    self.last_progress_time = now
            elif (now - self.last_log_time) >= self.warn_timeout_sec:
                if not self.warned:
                    self.log_callback(
                        f"⏳ [vLLM Monitor] Quiet for {elapsed_since_log}s while loading "
                        f"({progress}, {elapsed_total}s elapsed)"
                    )
                    self.warned = True
                    self.last_progress_time = now
            else:
                self._maybe_emit_progress(now, elapsed_total)
