"""
vLLM Server Lifecycle & Process Manager for DGXCoder.

This module provides the VLLMServerManager class which handles building vLLM server launch commands
(native CLI > python module > docker container), executing server subprocesses, pre-downloading weights,
polling HTTP health checks, and capturing logs.
"""

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
    """
    Supervisor class managing local vLLM OpenAI API server startup, container fallback, and health monitoring.
    """

    def __init__(self, host: str = DEFAULT_VLLM_HOST):
        """
        Initializes VLLMServerManager with target host endpoint URL.

        Args:
            host (str): Target HTTP endpoint URL (default 'http://localhost:8000').
        """
        self.host: str = host.rstrip("/")
        self.process: Optional[subprocess.Popen] = None
        self.streamer: VLLMLogStreamer = VLLMLogStreamer()

    def check_health(self, timeout: float = 0.5) -> bool:
        """
        Performs non-blocking HTTP GET request to /v1/models to verify vLLM server health.

        Args:
            timeout (float): Request timeout in seconds.

        Returns:
            bool: True if server returns HTTP status 200 OK.
        """
        try:
            url = f"{self.host}/v1/models"
            resp = requests.get(url, timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def get_models(self, timeout: float = 2.0) -> List[str]:
        """
        Queries /v1/models to list active served model IDs.

        Args:
            timeout (float): Request timeout in seconds.

        Returns:
            List[str]: List of model ID strings served on endpoint.
        """
        try:
            url = f"{self.host}/v1/models"
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                return [m.get("id") for m in data.get("data", []) if "id" in m]
        except Exception:
            pass
        return []

    def get_container_reserved_memory(self) -> Optional[str]:
        """
        Returns reserved memory usage of the Docker container (if running via docker).
        Uses docker stats --no-stream to get current memory usage.
        """
        if not self.process or not shutil.which("docker"):
            return None
        try:
            # Extract port from host for container name
            port = self.host.split(":")[-1] if ":" in self.host else "8000"
            container_name = f"dgxcoder-vllm-{port}"
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container_name],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def is_vllm_installed(self) -> bool:
        """
        Checks if vLLM CLI or python package is installed in environment.

        Returns:
            bool: True if vLLM binary or python package exists.
        """
        if shutil.which("vllm") is not None:
            return True
        try:
            res = subprocess.run([sys.executable, "-c", "import vllm"], capture_output=True)
            return res.returncode == 0
        except Exception:
            return False

    def is_docker_available(self) -> bool:
        """
        Checks if Docker daemon is running and accessible.

        Returns:
            bool: True if docker binary exists and `docker ps` succeeds.
        """
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
        api_key: Optional[str] = None,
        enable_auto_tool_choice: bool = True,
        tool_call_parser: Optional[str] = None,
        max_num_batched_tokens: Optional[int] = 8192,
        guided_decoding_backend: Optional[str] = "outlines",
    ) -> List[str]:
        """
        Constructs the shell command array to launch vLLM OpenAI API server.

        Applies tier fallback:
        Tier 1: `vllm serve <model>` native binary
        Tier 2: `python -m vllm.entrypoints.openai.api_server`
        Tier 3: `docker run --gpus all ... vllm/vllm-openai:latest`

        Args:
            model (str): Target model short alias or HuggingFace repo ID.
            port (int): Port number for server.
            quantization (Optional[str]): Quantization method.
            max_model_len (int): Context length limit.
            gpu_memory_utilization (float): Memory allocation fraction.
            draft_model (Optional[str]): Speculative decoding draft model.
            num_speculative_tokens (int): Proposed draft tokens per iteration.
            hf_token (Optional[str]): HuggingFace token.
            enable_prefix_caching (bool): Flag to enable prefix KV cache.
            enable_chunked_prefill (bool): Flag to enable chunked prefill.
            num_scheduler_steps (int): Multi-step scheduling iteration count.
            attention_backend (str): Attention implementation backend.
            kv_cache_dtype (str): Datatype for KV cache.
            api_key (Optional[str]): Optional API key for OpenAI-compatible auth (not set by default).
            enable_auto_tool_choice (bool): Enable automatic tool choice.
            tool_call_parser (Optional[str]): Parser name for tool calls.
            max_num_batched_tokens (Optional[int]): Max tokens per batch when chunked prefill active (GB10).
            guided_decoding_backend (Optional[str]): Guided decoding backend for deterministic JSON/tool calls.

        Returns:
            List[str]: Complete executable command list.
        """
        from dgxcoder.hardware import resolve_model_hf_repo
        hf_model = resolve_model_hf_repo(model)
        hf_draft_model = resolve_model_hf_repo(draft_model) if draft_model else None

        token_env = hf_token or os.getenv("HF_TOKEN") or os.getenv("DGXCODER_HF_TOKEN")

        # Auto FP8 resolution for 70B/72B models on GB10
        if not quantization and ("70b" in model.lower() or "72b" in model.lower()):
            quantization = "fp8"

        # Auto-resolve optimal tool_call_parser based on model architecture if None or default 'hermes'
        if not tool_call_parser or tool_call_parser == "auto":
            model_lower = model.lower()
            if "qwen" in model_lower or "llama" in model_lower:
                tool_call_parser = "hermes"
            elif "mistral" in model_lower:
                tool_call_parser = "mistral"
            else:
                tool_call_parser = "hermes"


        base_args: List[str] = [
            "--host", "0.0.0.0",
            "--port", str(port),
            "--max-model-len", str(max_model_len),
            "--gpu-memory-utilization", str(gpu_memory_utilization),
            "--trust-remote-code",
        ]

        if enable_prefix_caching:
            base_args.append("--enable-prefix-caching")
        if enable_chunked_prefill:
            base_args.append("--enable-chunked-prefill")
            if max_num_batched_tokens and max_num_batched_tokens > 0:
                base_args.extend(["--max-num-batched-tokens", str(max_num_batched_tokens)])
        if attention_backend and attention_backend != "auto":
            base_args.extend(["--attention-backend", attention_backend])
        if kv_cache_dtype:
            base_args.extend(["--kv-cache-dtype", kv_cache_dtype])
        if api_key:
            base_args.extend(["--api-key", api_key])
        if enable_auto_tool_choice:
            base_args.append("--enable-auto-tool-choice")
        if tool_call_parser:
            base_args.extend(["--tool-call-parser", tool_call_parser])

        if shutil.which("vllm"):
            cmd = ["vllm", "serve", hf_model] + base_args
        elif self.is_vllm_installed():
            cmd = [sys.executable, "-m", "vllm.entrypoints.openai.api_server", "--model", hf_model] + base_args
        elif self.is_docker_available():
            hf_cache = os.path.expanduser("~/.cache/huggingface")
            os.makedirs(hf_cache, exist_ok=True)
            cmd = [
                "docker", "run", "--rm",
                "--ipc=host",
                "--network", "host",
                "--name", f"dgxcoder-vllm-{port}",
                "--gpus", "all",
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
        api_key: Optional[str] = None,
        enable_auto_tool_choice: bool = True,
        tool_call_parser: Optional[str] = None,
        max_num_batched_tokens: Optional[int] = 8192,

        guided_decoding_backend: Optional[str] = "outlines",
        background: bool = True
    ) -> Optional[subprocess.Popen]:
        """
        Pre-downloads model weights and starts local vLLM OpenAI API server.

        Args:
            model (str): Primary model name.
            port (int): Endpoint port.
            quantization (Optional[str]): Quantization format.
            draft_model (Optional[str]): Draft model for speculative decoding.
            num_speculative_tokens (int): Speculative token length.
            hf_token (Optional[str]): HuggingFace token.
            enable_prefix_caching (bool): Enable prefix KV caching.
            enable_chunked_prefill (bool): Enable chunked prefill.
            num_scheduler_steps (int): Multi-step scheduling count.
            attention_backend (str): Attention backend.
            kv_cache_dtype (str): KV cache precision.
            api_key (Optional[str]): Optional API key (not set by default).
            enable_auto_tool_choice (bool): Enable automatic tool choice.
            tool_call_parser (Optional[str]): Tool call parser name.
            background (bool): If True, run asynchronously as Popen subprocess.

        Returns:
            Optional[subprocess.Popen]: Popen object if background=True, else None.
        """
        if not self.is_vllm_installed() and not self.is_docker_available():
            print("⚠️ vLLM Python package is not installed and Docker is unavailable.")
            print("💡 Install vLLM via: `pip install vllm` or `pip install vllm --extra-index-url https://download.pytorch.org/whl/cu121`")

        # Step 1: Pre-download model weights into local HuggingFace cache
        from dgxcoder.hardware import download_model
        download_model(model, hf_token=hf_token)
        if draft_model:
            download_model(draft_model, hf_token=hf_token)

        # Step 2: Build launch command
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
            api_key=api_key,
            enable_auto_tool_choice=enable_auto_tool_choice,
            tool_call_parser=tool_call_parser,
            max_num_batched_tokens=max_num_batched_tokens,
        )

        # Step 3: Cleanup potential container name conflicts prior to launch
        if cmd and cmd[0] == "docker":
            container_name = f"dgxcoder-vllm-{port}"
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        print(f"🚀 Starting GB10 vLLM Server: {' '.join(cmd)}")
        
        env = os.environ.copy()
        token_val = hf_token or os.getenv("HF_TOKEN") or os.getenv("DGXCODER_HF_TOKEN")
        
        # Configure HuggingFace to use local cache exclusively when possible
        from dgxcoder.hardware.model_downloader import ModelDownloader
        hf_cache_dir = str(ModelDownloader.get_hf_cache_dir().parent)
        env["HF_HOME"] = hf_cache_dir
        
        # Set HF_HUB_OFFLINE to 0 (default) to allow cache-first behavior
        # Models must be pre-downloaded or vLLM will download on first load
        env["HF_HUB_OFFLINE"] = "0"
        
        # Disable auth token usage for anonymous downloads (use cache-only mode)
        # Only set token if explicitly provided
        if token_val:
            env["HF_TOKEN"] = token_val
            env["HUGGING_FACE_HUB_TOKEN"] = token_val
            print(f"   HuggingFace Token: Configured")
        else:
            print(f"   HuggingFace Token: Not configured (cache-only mode)")

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

    def stop_server(self, port: int = 8000) -> None:
        """
        Stops and removes the vLLM Docker container for the given port.
        Safe no-op if container is not running.
        """
        container_name = f"dgxcoder-vllm-{port}"
        print(f"🛑 Stopping vLLM container: {container_name}")
        subprocess.run(["docker", "stop", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"✅ Container {container_name} stopped and removed.")

    def get_new_logs(self) -> List[str]:
        """
        Retrieves newly accumulated server stdout log lines.

        Returns:
            List[str]: List of unread stdout log strings.
        """
        return self.streamer.pop_logs()

    def get_server_status(self) -> Dict[str, Any]:
        """
        Gathers complete server status report.

        Returns:
            Dict[str, Any]: Dictionary with 'host', 'healthy', 'models', 'pid'.
        """
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
