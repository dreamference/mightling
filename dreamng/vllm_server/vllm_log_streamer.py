"""
Non-blocking vLLM Server Log Streamer for Dreamng.

This module provides the VLLMLogStreamer class which reads stdout from spawned vLLM server
subprocesses in a background thread and queues lines for non-blocking consumption.
"""

import queue
import threading
from typing import Any, List, Optional

class VLLMLogStreamer:
    """
    Background log reader that streams output lines from vLLM subprocess into a thread-safe queue.
    """

    def __init__(self):
        """Initializes empty thread-safe Queue and worker thread reference."""
        self.log_queue: queue.Queue = queue.Queue()
        self.log_thread: Optional[threading.Thread] = None

    def start_streaming(self, stdout_pipe: Any) -> None:
        """
        Launches background daemon thread to read lines from process stdout pipe.

        Args:
            stdout_pipe (Any): Readable file-like object connected to subprocess stdout.
        """
        self.log_thread = threading.Thread(
            target=self._enqueue_output,
            args=(stdout_pipe,),
            daemon=True
        )
        self.log_thread.start()

    def _enqueue_output(self, out: Any) -> None:
        """
        Worker thread function reading lines continuously until process exit.

        Args:
            out (Any): Process stdout pipe handle.
        """
        try:
            for line in iter(out.readline, ''):
                if line:
                    self.log_queue.put(line.rstrip("\r\n"))
                else:
                    break
        except Exception:
            pass
        finally:
            try:
                out.close()
            except Exception:
                pass

    def pop_logs(self) -> List[str]:
        """
        Retrieves all currently queued log lines without blocking.

        Returns:
            List[str]: List of log line strings accumulated since last call.
        """
        logs: List[str] = []
        while not self.log_queue.empty():
            try:
                logs.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        return logs
