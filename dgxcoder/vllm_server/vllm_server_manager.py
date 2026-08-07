import os
import sys
import shutil
import subprocess
import requests
from typing import Dict, Any, Optional, List, Final

from dgxcoder.vllm_server.vllm_server_status import VLLMServerStatus
from dgxcoder.vllm_server.vllm_log_streamer import VLLMLogStreamer

DEFAULT_VLLM_HOST: Final[str] = "http://localhost:8000"

class VLLMServerManager:
    """Manages local vLLM / TensorRT-LLM server lifecycle on NVIDIA GB10 hardware."""

    def __init__(self, host: str = DEFAULT_VLLM_HOST):
        self.host: str = host.rstrip("/")
        self.process: Optional[subprocess.Popen] = None
        self.streamer: VLLMLogStreamer = VLLMLogStreamer()

    def check_health(self, timeout: float = 0.5) -> bool:
        try:
            url = f"{self.host}/v1/models"
            resp = requests.get(url, timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def get_models(self, timeout: float = 2.0) -> List[str]:
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
        if shutil.which("vllm") is not None:
            return True
        try:
            res = subprocess.run([sys.executable, "-c", "import vllm"], capture_output=True)
            return res.returncode == 0
        except Exception:
            return False

    def is_docker_available(self) -> bool:
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
        hf_token: Optional[str] = None,
        enable_prefix_caching: bool = True,
        enable_chunked_prefill: bool = True,
        num_scheduler_steps: int = 8,
        attention_backend: str = "auto",
        kv_cache_dtype: str = "auto",
    ) -> List[str]:
        from dgxcoder.hardware import resolve_model_hf_repo
        hf_model = resolve_model_hf_repo(model)
        hf_draft_model = resolve_model_hf_repo(draft_model) if draft_model else None

        token_env = hf_token or os.getenv("HF_TOKEN") or os.getenv("DGXCODER_HF_TOKEN")

        if not quantization and ("70b" in model.lower() or "72b" in model.lower()):
            quantization = "fp8"

        base_args: List[str] = [
            "--host", "0.0.0.0",
            "--port", str(port),
            "--max-model-len", str(max_model_len),
            "--gpu-memory-utilization", str(gpu_memory_utilization),
            "--trust-remote-code",
            "--enforce-eager",
        ]

        if enable_prefix_caching:
            base_args.append("--enable-prefix-caching")
        if enable_chunked_prefill:
            base_args.append("--enable-chunked-prefill")
        if attention_backend and attention_backend != "auto":
            base_args.extend(["--attention-backend", attention_backend])
        if kv_cache_dtype:
            base_args.extend(["--kv-cache-dtype", kv_cache_dtype])

        if shutil.which("vllm"):
            cmd = ["vllm", "serve", hf_model] + base_args
        elif self.is_vllm_installed():
            cmd = [sys.executable, "-m", "vllm.entrypoints.openai.api_server", "--model", hf_model] + base_args
        elif self.is_docker_available():
            hf_cache = os.path.expanduser("~/.cache/huggingface")
            os.makedirs(hf_cache, exist_ok=True)
            cmd = [
                "docker", "run", "--rm",
                "--name", f"dgxcoder-vllm-{port}",
                "--gpus", "all",
                "-p", f"{port}:{port}",
                "-v", f"{hf_cache}:/root/.cache/huggingface",
            ]
            if token_env:
                cmd.extend(["-e", f"HF_TOKEN={token_env}"])
            cmd.extend(["vllm/vllm-openai:latest", hf_model] + base_args)
        else:
            cmd = [sys.executable, "-m", "vllm.entrypoints.openai.api_server", "--model", hf_model] + base_args

        if quantization:
            cmd.extend(["--quantization", quantization])

        if hf_draft_model:
            cmd.extend(["--speculative-model", hf_draft_model, "--num-speculative-tokens", str(num_speculative_tokens)])
            
        return cmd

    def start_server(
        self,
        model: str = "qwen2.5-coder-32b",
        port: int = 8000,
        quantization: Optional[str] = None,
        draft_model: Optional[str] = None,
        num_speculative_tokens: int = 5,
        hf_token: Optional[str] = None,
        enable_prefix_caching: bool = True,
        enable_chunked_prefill: bool = True,
        num_scheduler_steps: int = 8,
        attention_backend: str = "auto",
        kv_cache_dtype: str = "auto",
        background: bool = True
    ) -> Optional[subprocess.Popen]:
        if not self.is_vllm_installed() and not self.is_docker_available():
            print("⚠️ vLLM Python package is not installed and Docker is unavailable.")
            print("💡 Install vLLM via: `pip install vllm` or `pip install vllm --extra-index-url https://download.pytorch.org/whl/cu121`")

        from dgxcoder.hardware import download_model
        download_model(model, hf_token=hf_token)
        if draft_model:
            download_model(draft_model, hf_token=hf_token)

        cmd = self.build_launch_command(
            model=model,
            port=port,
            quantization=quantization,
            draft_model=draft_model,
            num_speculative_tokens=num_speculative_tokens,
            hf_token=hf_token,
            enable_prefix_caching=enable_prefix_caching,
            enable_chunked_prefill=enable_chunked_prefill,
            num_scheduler_steps=num_scheduler_steps,
            attention_backend=attention_backend,
            kv_cache_dtype=kv_cache_dtype,
        )

        if cmd and cmd[0] == "docker":
            container_name = f"dgxcoder-vllm-{port}"
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        print(f"🚀 Starting GB10 vLLM Server: {' '.join(cmd)}")
        
        env = os.environ.copy()
        token_val = hf_token or os.getenv("HF_TOKEN") or os.getenv("DGXCODER_HF_TOKEN")
        if token_val:
            env["HF_TOKEN"] = token_val
            env["HUGGING_FACE_HUB_TOKEN"] = token_val

        if background:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env
            )
            self.streamer.start_streaming(self.process.stdout)
            return self.process
        else:
            subprocess.run(cmd, check=True, env=env)
            return None

    def get_new_logs(self) -> List[str]:
        return self.streamer.pop_logs()

    def get_server_status(self) -> Dict[str, Any]:
        healthy = self.check_health()
        models = self.get_models() if healthy else []
        status = VLLMServerStatus(
            host=self.host,
            healthy=healthy,
            models=models,
            pid=self.process.pid if self.process else None
        )
        return {
            "host": status.host,
            "healthy": status.healthy,
            "models": status.models,
            "pid": status.pid
        }
