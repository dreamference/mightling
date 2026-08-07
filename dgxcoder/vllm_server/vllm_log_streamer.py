import queue
import threading
from typing import Any, List, Optional

class VLLMLogStreamer:
    """Handles non-blocking background output queueing for vLLM server logs."""

    def __init__(self):
        self.log_queue: queue.Queue = queue.Queue()
        self.log_thread: Optional[threading.Thread] = None

    def start_streaming(self, stdout_pipe: Any) -> None:
        self.log_thread = threading.Thread(
            target=self._enqueue_output,
            args=(stdout_pipe,),
            daemon=True
        )
        self.log_thread.start()

    def _enqueue_output(self, out: Any) -> None:
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
        logs: List[str] = []
        while not self.log_queue.empty():
            try:
                logs.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        return logs
