import os
import sys
import time
import queue
import shutil
import threading
import subprocess
import requests
from typing import Dict, Any, Optional, List
from dgxcoder.hardware import detect_gb10_hardware, MODEL_MATRIX

DEFAULT_VLLM_HOST = "http://localhost:8000"

class VLLMServerManager:
    """Manages the local vLLM / TensorRT-LLM server on NVIDIA GB10 hardware."""

    def __init__(self, host: str = DEFAULT_VLLM_HOST):
        self.host = host.rstrip("/")
        self.process: Optional[subprocess.Popen] = None
        self.log_queue: queue.Queue = queue.Queue()
        self.log_thread: Optional[threading.Thread] = None

    def _enqueue_output(self, out):
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

    def check_health(self, timeout: float = 0.5) -> bool:
        """Checks if vLLM server is active and responding to HTTP requests."""
        try:
            url = f"{self.host}/v1/models"
            resp = requests.get(url, timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def get_models(self, timeout: float = 2.0) -> List[str]:
        """Lists models currently served by vLLM endpoint."""
        try:
            url = f"{self.host}/v1/models"
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                return [m.get("id") for m in data.get("data", []) if "id" in m]
        except Exception:
            pass
        return []

    def is_vllm_installed(self) -> bool:
        """Checks if vLLM binary or python package is available in the current environment."""
        if shutil.which("vllm") is not None:
            return True
        try:
            res = subprocess.run([sys.executable, "-c", "import vllm"], capture_output=True)
            return res.returncode == 0
        except Exception:
            return False

    def is_docker_available(self) -> bool:
        """Checks if docker engine is available and executable by current user."""
        if shutil.which("docker") is None:
            return False
        try:
            res = subprocess.run(["docker", "ps"], capture_output=True)
            return res.returncode == 0
        except Exception:
            return False

    def build_launch_command(
        self,
        model: str = "qwen2.5-coder-32b",
        port: int = 8000,
        quantization: Optional[str] = None,
        max_model_len: int = 16384,
        gpu_memory_utilization: float = 0.90,
        draft_model: Optional[str] = None,
        num_speculative_tokens: int = 5,
    ) -> List[str]:
        """Generates vLLM command line (native CLI, Python module, or Docker container) for NVIDIA GB10 with Speculative Decoding support."""
        from dgxcoder.hardware import resolve_model_hf_repo
        hf_model = resolve_model_hf_repo(model)
        hf_draft_model = resolve_model_hf_repo(draft_model) if draft_model else None

        if shutil.which("vllm"):
            cmd = [
                "vllm", "serve", hf_model,
                "--host", "0.0.0.0",
                "--port", str(port),
                "--max-model-len", str(max_model_len),
                "--gpu-memory-utilization", str(gpu_memory_utilization),
                "--trust-remote-code",
                "--enforce-eager",
            ]
        elif self.is_vllm_installed():
            cmd = [
                sys.executable, "-m", "vllm.entrypoints.openai.api_server",
                "--host", "0.0.0.0",
                "--port", str(port),
                "--model", hf_model,
                "--max-model-len", str(max_model_len),
                "--gpu-memory-utilization", str(gpu_memory_utilization),
                "--trust-remote-code",
                "--enforce-eager",
            ]
        elif self.is_docker_available():
            hf_cache = os.path.expanduser("~/.cache/huggingface")
            os.makedirs(hf_cache, exist_ok=True)
            cmd = [
                "docker", "run", "--rm",
                "--name", f"dgxcoder-vllm-{port}",
                "--gpus", "all",
                "-p", f"{port}:{port}",
                "-v", f"{hf_cache}:/root/.cache/huggingface",
                "vllm/vllm-openai:latest",
                "--model", hf_model,
                "--max-model-len", str(max_model_len),
                "--gpu-memory-utilization", str(gpu_memory_utilization),
                "--trust-remote-code",
                "--enforce-eager",
            ]
        else:
            cmd = [
                sys.executable, "-m", "vllm.entrypoints.openai.api_server",
                "--host", "0.0.0.0",
                "--port", str(port),
                "--model", hf_model,
                "--max-model-len", str(max_model_len),
                "--gpu-memory-utilization", str(gpu_memory_utilization),
                "--trust-remote-code",
                "--enforce-eager",
            ]

        if quantization:
            cmd.extend(["--quantization", quantization])

        if hf_draft_model:
            cmd.extend(["--speculative-model", hf_draft_model, "--num-speculative-tokens", str(num_speculative_tokens)])
        
        hw = detect_gb10_hardware()
        if hw.get("is_gb10") and "--kv-cache-dtype" not in cmd:
            cmd.extend(["--kv-cache-dtype", "auto"])
            
        return cmd

    def start_server(
        self,
        model: str = "qwen2.5-coder-32b",
        port: int = 8000,
        quantization: Optional[str] = None,
        draft_model: Optional[str] = None,
        num_speculative_tokens: int = 5,
        background: bool = True
    ) -> Optional[subprocess.Popen]:
        """Launches vLLM server instance on NVIDIA GB10 with optional speculative decoding."""
        if not self.is_vllm_installed() and not self.is_docker_available():
            print("⚠️ vLLM Python package is not installed and Docker is unavailable.")
            print("💡 Install vLLM via: `pip install vllm` or `pip install vllm --extra-index-url https://download.pytorch.org/whl/cu121`")

        cmd = self.build_launch_command(
            model=model,
            port=port,
            quantization=quantization,
            draft_model=draft_model,
            num_speculative_tokens=num_speculative_tokens
        )
        print(f"🚀 Starting GB10 vLLM Server: {' '.join(cmd)}")
        
        if background:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self.log_thread = threading.Thread(
                target=self._enqueue_output,
                args=(self.process.stdout,),
                daemon=True
            )
            self.log_thread.start()
            return self.process
        else:
            subprocess.run(cmd, check=True)
            return None

    def get_new_logs(self) -> List[str]:
        """Retrieves unread log lines from the vLLM server process output queue."""
        logs = []
        while not self.log_queue.empty():
            try:
                logs.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        return logs

    def get_server_status(self) -> Dict[str, Any]:
        """Returns health, models, and host info."""
        healthy = self.check_health()
        models = self.get_models() if healthy else []
        return {
            "host": self.host,
            "healthy": healthy,
            "models": models,
            "pid": self.process.pid if self.process else None
        }
