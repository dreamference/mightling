"""
vLLM Server Lifecycle & Process Manager for Dreamference.

This module provides the VLLMServerManager class which handles building vLLM server launch commands
(native CLI > python module > docker container), executing server subprocesses, pre-downloading weights,
polling HTTP health checks, and capturing logs.
"""

import os
import re
import sys
import shutil
import subprocess
import requests
from pathlib import Path
from typing import Dict, Any, Optional, List, Final

from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS as DEFAULT_MODEL
from dreamference.vllm_server.vllm_server_status import VLLMServerStatus
from dreamference.vllm_server.vllm_log_streamer import VLLMLogStreamer
from dreamference.vllm_server.psi_watchdog import MemoryPressureWatchdog
from dreamference.config.dreamference_config import (
    DreamferenceConfig,
    DEFAULT_GUIDED_DECODING_BACKEND,
)

DEFAULT_VLLM_HOST: Final[str] = "http://localhost:8000"
DEFAULT_MAX_MODEL_LEN: Final[int] = 16384
DEFAULT_GPU_MEMORY_UTILIZATION: Final[float] = 0.50
# Memory held back from the vLLM container so the host keeps a responsive desktop and enough
# page cache to stream weights. On unified memory every byte vLLM pins is a byte the compositor,
# journald and the input stack no longer have.
HOST_MEMORY_RESERVE_GB: Final[float] = 12.0
# Swap below this leaves the kernel no cheap way to shed cold anonymous pages under load, which
# is when it starts stalling on reclaim instead. Advisory only — a small swap is a warning, not a
# refusal, since it degrades the odds rather than guaranteeing a hang.
MIN_SWAP_GB: Final[float] = 64.0
# Nominates the vLLM container as the OOM killer's preferred victim. The memory cap bounds how
# much the container can take, but says nothing about who dies when the host runs short — and the
# default leaves the compositor as plausible a target as the server. Positive values need no
# privilege (only lowering below zero does), and children inherit it, so the engine workers that
# actually hold the weights are covered. 800 rather than the 1000 maximum: a strongly preferred
# victim, without being so eager that a transient spike takes the server down for no reason.
CONTAINER_OOM_SCORE_ADJ: Final[int] = 800
# ~1 GB of emergency free pages. The stock value scales with memory size but is only ~44 MB on this
# 128 GB machine, which a loader pulling gigabytes per second blows through between reclaim passes —
# at which point allocators enter direct reclaim and stall.
MIN_FREE_KBYTES: Final[int] = 1_048_576
# Tenths of a percent of memory between the low and high watermarks. The default of 10 (0.1%, about
# 128 MB here) gives kswapd almost no runway; 200 (2%) lets it reclaim in the background instead of
# handing the stall to whoever allocated next.
WATERMARK_SCALE_FACTOR: Final[int] = 200
DEFAULT_KV_CACHE_DTYPE: Final[str] = "auto"

# Launch defaults applied when neither the caller nor the model's registry recipe specifies a value.

# Pinned vLLM runtime container built from project Dockerfile with tensorizer support.
# Pinned to an exact tag (never ':latest') so that upstream vLLM CLI changes cannot silently break launches.
DEFAULT_VLLM_IMAGE: Final[str] = "dreamference-vllm-tensorizer:26.07-py3"

# vLLM release that removed `--guided-decoding-backend` in favour of `--structured-outputs-config.*`
STRUCTURED_OUTPUTS_MIN_VERSION: Final[tuple] = (0, 12)

# Backends selectable on current vLLM ('auto' lets vLLM pick per request)
SUPPORTED_STRUCTURED_OUTPUT_BACKENDS: Final[tuple] = ("auto", "xgrammar", "guidance", "outlines")

# Backends that only exist on pre-v0.12 vLLM
LEGACY_STRUCTURED_OUTPUT_BACKENDS: Final[tuple] = ("lm-format-enforcer",)

class VLLMServerManager:
    """
    Supervisor class managing local vLLM OpenAI API server startup, container fallback, and health monitoring.
    """

    # Cache of docker image name -> probe results ('vllm_version', 'has_tensorizer').
    # Probing costs a container start, so it is done at most once per image per process.
    _image_probe_cache: Dict[str, Dict[str, Any]] = {}

    def __init__(self, host: str = DEFAULT_VLLM_HOST):
        """
        Initializes VLLMServerManager with target host endpoint URL.

        Args:
            host (str): Target HTTP endpoint URL (default 'http://localhost:8000').
        """
        self.host: str = host.rstrip("/")
        self.process: Optional[subprocess.Popen] = None
        self.streamer: VLLMLogStreamer = VLLMLogStreamer()
        # Armed at launch and left armed for the life of the container: a serving process that
        # stalls the whole host is as dangerous as one that stalls while loading.
        self.watchdog: Optional[MemoryPressureWatchdog] = None
        
        from dreamference.vllm_server.diagnostics import ContainerDiagnostics
        self.diagnostics = ContainerDiagnostics(self.host)

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
        Returns reserved memory usage and CPU usage of the Docker container (if running via docker).
        Delegates to ContainerDiagnostics.
        """
        if not self.process:
            return None
        return self.diagnostics.get_diagnostics_line()

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

    def is_image_present(self, docker_image: str) -> bool:
        """
        Checks whether target Docker image is present in local Docker registry.

        Args:
            docker_image (str): Name of Docker image to check.

        Returns:
            bool: True if image exists in local Docker daemon.
        """
        if shutil.which("docker") is None:
            return False
        try:
            res = subprocess.run(
                ["docker", "image", "inspect", docker_image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return res.returncode == 0
        except Exception:
            return False

    def ensure_docker_image(self, docker_image: str = DEFAULT_VLLM_IMAGE) -> bool:
        """
        Ensures that the Docker image exists locally. If missing and a local Dockerfile exists,
        builds the image locally using `docker build`.

        Args:
            docker_image (str): Target Docker image name.

        Returns:
            bool: True if image is present or successfully built, False otherwise.
        """
        if self.is_image_present(docker_image):
            return True

        project_root = Path(__file__).resolve().parent.parent.parent
        dockerfile_path = project_root / "Dockerfile"

        if dockerfile_path.is_file():
            print(f"📦 Docker image '{docker_image}' not found locally. Building from {dockerfile_path}...")
            try:
                res = subprocess.run(
                    ["docker", "build", "-t", docker_image, "-f", str(dockerfile_path), str(project_root)],
                    check=True
                )
                if res.returncode == 0 and self.is_image_present(docker_image):
                    print(f"✅ Successfully built Docker image '{docker_image}'.")
                    return True
            except subprocess.CalledProcessError as e:
                print(f"❌ Failed to build Docker image '{docker_image}': {e}")
            except Exception as e:
                print(f"❌ Unexpected error building Docker image '{docker_image}': {e}")

        print(f"⚠️  Docker image '{docker_image}' is missing locally and could not be built.")
        return False

    def probe_image(self, docker_image: str) -> Dict[str, Any]:
        """
        Inspects the target Docker image for the capabilities Dreamference's launch command depends on.

        Runs one short throwaway container that reports the installed vLLM version and whether the
        `tensorizer` package is importable. Both facts change which flags are valid, and neither can
        be inferred from the image name. Results are cached per image name because the probe costs
        a container start.

        Args:
            docker_image (str): Docker image name to probe.

        Returns:
            Dict[str, Any]: {'vllm_version': (major, minor) or None, 'has_tensorizer': bool}.
        """
        if docker_image in self._image_probe_cache:
            return self._image_probe_cache[docker_image]

        result: Dict[str, Any] = {"vllm_version": None, "has_tensorizer": False}
        if not self.ensure_docker_image(docker_image):
            self._image_probe_cache[docker_image] = result
            return result

        script = (
            "import importlib.util as u\n"
            "try:\n"
            "    import vllm; print('vllm=' + vllm.__version__)\n"
            "except Exception: pass\n"
            "print('tensorizer=' + str(u.find_spec('tensorizer') is not None))\n"
        )
        try:
            res = subprocess.run(
                ["docker", "run", "--rm", "--entrypoint", "python3", docker_image, "-c", script],
                capture_output=True, text=True, timeout=120
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.startswith("vllm="):
                        # Strip local/build suffixes such as '0.12.1+cu129' or '0.11.0rc1'
                        parts = line.split("=", 1)[1].strip().split("+")[0].split(".")
                        result["vllm_version"] = (int(parts[0]), int(re.sub(r"\D.*$", "", parts[1])))
                    elif line.startswith("tensorizer="):
                        result["has_tensorizer"] = line.split("=", 1)[1].strip() == "True"
        except Exception:
            pass

        self._image_probe_cache[docker_image] = result
        return result

    def detect_image_vllm_version(self, docker_image: str) -> Optional[tuple]:
        """
        Returns the (major, minor) vLLM version inside the target image, or None if undetectable.

        Args:
            docker_image (str): Docker image name to probe.

        Returns:
            Optional[tuple]: (major, minor) version tuple, or None.
        """
        return self.probe_image(docker_image)["vllm_version"]

    def image_has_tensorizer(self, docker_image: str) -> bool:
        """
        Returns True when the target image can import `tensorizer`.

        Args:
            docker_image (str): Docker image name to probe.

        Returns:
            bool: True if tensorizer is importable inside the image.
        """
        return bool(self.probe_image(docker_image)["has_tensorizer"])

    def build_structured_outputs_args(self, backend: str, docker_image: str) -> List[str]:
        """
        Builds the CLI flag that selects the structured-outputs (guided decoding) backend.

        vLLM renamed this interface: `--guided-decoding-backend <b>` was deprecated and removed in
        v0.12.0 in favour of `--structured-outputs-config.backend <b>`. The correct spelling depends
        on the vLLM inside the target image, so probe it. When the version cannot be determined the
        modern form is used, because the default image tracks `vllm/vllm-openai:latest`.

        Args:
            backend (str): Requested backend name (e.g. 'auto', 'xgrammar', 'guidance').
            docker_image (str): Docker image the server will run in.

        Returns:
            List[str]: Two-element flag/value pair to append to the launch command.
        """
        version = self.detect_image_vllm_version(docker_image)

        if version is not None and version < STRUCTURED_OUTPUTS_MIN_VERSION:
            return ["--guided-decoding-backend", backend]

        if backend in LEGACY_STRUCTURED_OUTPUT_BACKENDS:
            print(
                f"⚠️  Structured-outputs backend '{backend}' was removed in vLLM v{STRUCTURED_OUTPUTS_MIN_VERSION[0]}."
                f"{STRUCTURED_OUTPUTS_MIN_VERSION[1]}. Supported backends are: "
                f"{', '.join(SUPPORTED_STRUCTURED_OUTPUT_BACKENDS)}. Passing it through as requested."
            )
        return ["--structured-outputs-config.backend", backend]

    def resolve_tool_call_parser(self, model: str, requested: Optional[str] = None) -> str:
        """
        Determines which vLLM tool-call parser matches the target model.

        Resolution order is explicit request, then the model's registry recipe, then a family guess
        from the model name. The registry step matters because parser choice is not a per-family
        constant: Qwen 2.5 emits Hermes-style `<tool_call>` blocks while Qwen 3.6 emits XML, so a
        name-substring guess silently produces a model whose tool calls never parse.

        Args:
            model (str): Model short alias or HuggingFace repo ID.
            requested (Optional[str]): Caller-supplied parser; 'auto' is treated as unset.

        Returns:
            str: Parser name to pass to --tool-call-parser.
        """
        if requested and requested != "auto":
            return requested

        from dreamference.hardware import get_model_launch_overrides
        recipe_parser = get_model_launch_overrides(model).get("tool_call_parser")
        if recipe_parser:
            return recipe_parser

        model_lower = model.lower()
        if "mistral" in model_lower:
            return "mistral"
        return "hermes"

    def build_launch_command(
        self,
        model: str = DEFAULT_MODEL,
        port: int = 8000,
        quantization: Optional[str] = None,
        max_model_len: Optional[int] = None,
        gpu_memory_utilization: Optional[float] = None,
        draft_model: Optional[str] = None,
        num_speculative_tokens: int = 5,
        hf_token: Optional[str] = None,
        enable_prefix_caching: bool = True,
        enable_chunked_prefill: bool = True,
        num_scheduler_steps: int = 8,
        attention_backend: Optional[str] = None,
        kv_cache_dtype: Optional[str] = None,
        api_key: Optional[str] = None,
        enable_auto_tool_choice: bool = True,
        tool_call_parser: Optional[str] = None,
        reasoning_parser: Optional[str] = None,
        moe_backend: Optional[str] = None,
        max_num_batched_tokens: Optional[int] = 8192,
        guided_decoding_backend: Optional[str] = None,
        use_tensorizer: Optional[bool] = None,
        docker_image: str = DEFAULT_VLLM_IMAGE,
    ) -> List[str]:
        """
        Constructs the shell command array to launch vLLM OpenAI API server.

        Docker-only policy: always uses Docker (docker run --gpus all ...).
        Fails cleanly with RuntimeError if Docker is unavailable (no native/python fallbacks).

        Optional arguments left as None fall back to the target model's `launch_overrides` recipe in
        `ModelMatrixRegistry`, and then to the module defaults. Models without a recipe therefore see
        exactly the previous behaviour, while a model that needs specific backends (NVFP4 on SM121)
        carries them as registry data instead of requiring a caller to remember the flag set.
        `attention_backend='auto'` counts as unset, since 'auto' delegates the choice by definition.

        Args:
            model (str): Target model short alias or HuggingFace repo ID.
            port (int): Port number for server.
            quantization (Optional[str]): Quantization method. Suppressed for checkpoints that declare
                their own format (NVFP4, AWQ, …), which vLLM auto-detects.
            max_model_len (Optional[int]): Context length limit.
            gpu_memory_utilization (Optional[float]): Memory allocation fraction.
            draft_model (Optional[str]): Speculative decoding draft model.
            num_speculative_tokens (int): Proposed draft tokens per iteration.
            hf_token (Optional[str]): HuggingFace token.
            enable_prefix_caching (bool): Flag to enable prefix KV cache.
            enable_chunked_prefill (bool): Flag to enable chunked prefill.
            num_scheduler_steps (int): Multi-step scheduling iteration count.
            attention_backend (Optional[str]): Attention implementation backend.
            kv_cache_dtype (Optional[str]): Datatype for KV cache.
            api_key (Optional[str]): Optional API key for OpenAI-compatible auth (not set by default).
            enable_auto_tool_choice (bool): Enable automatic tool choice.
            tool_call_parser (Optional[str]): Parser name for tool calls.
            reasoning_parser (Optional[str]): Parser for models that emit a separate reasoning channel.
            moe_backend (Optional[str]): Mixture-of-experts kernel backend. Load-bearing on GB10: the
                CUTLASS FP4 experts path is compiled for SM120 and corrupts output on SM121.
            max_num_batched_tokens (Optional[int]): Max tokens per batch when chunked prefill active (GB10).
            guided_decoding_backend (Optional[str]): Structured-outputs backend for deterministic JSON/tool
                calls ('auto', 'xgrammar', 'guidance'). When None, no flag is emitted and vLLM applies its
                own default ('auto').
            use_tensorizer (Optional[bool]): Pass model in tensorize (.tensors) format to vLLM if available.
            docker_image (str): Docker image to launch vLLM in (default: pinned DEFAULT_VLLM_IMAGE).

        Returns:
            List[str]: Complete executable command list.
        """
        import json

        from dreamference.hardware import (
            resolve_model_hf_repo,
            is_model_tensorized,
            get_tensorized_path,
            get_model_launch_overrides,
            model_declares_own_quantization,
        )
        from dreamference.hardware.model_downloader import ModelDownloader

        hf_model = resolve_model_hf_repo(model)
        hf_draft_model = resolve_model_hf_repo(draft_model) if draft_model else None

        token_env = hf_token or os.getenv("HF_TOKEN") or os.getenv("DREAMFERENCE_HF_TOKEN")

        recipe = get_model_launch_overrides(model)

        def resolved(value: Any, key: str, fallback: Any) -> Any:
            """Explicit caller argument wins; otherwise the model recipe; otherwise the module default."""
            if value is not None:
                return value
            return recipe.get(key, fallback)

        max_model_len = resolved(max_model_len, "max_model_len", DEFAULT_MAX_MODEL_LEN)
        gpu_memory_utilization = resolved(
            gpu_memory_utilization, "gpu_memory_utilization", DEFAULT_GPU_MEMORY_UTILIZATION
        )
        kv_cache_dtype = resolved(kv_cache_dtype, "kv_cache_dtype", DEFAULT_KV_CACHE_DTYPE)
        moe_backend = resolved(moe_backend, "moe_backend", None)
        reasoning_parser = resolved(reasoning_parser, "reasoning_parser", None)
        use_tensorizer = resolved(use_tensorizer, "use_tensorizer", False)
        guided_decoding_backend = resolved(guided_decoding_backend, "guided_decoding_backend", DEFAULT_GUIDED_DECODING_BACKEND)
        max_num_batched_tokens = resolved(max_num_batched_tokens, "max_num_batched_tokens", 8192)
        if not attention_backend or attention_backend == "auto":
            attention_backend = recipe.get("attention_backend", "auto")

        # 70B/72B checkpoints published in BF16 need an explicit FP8 request to fit GB10. Checkpoints
        # that are already quantized announce their format in config.json, so adding a guess here would
        # override vLLM's own detection with a value derived from nothing but the model name.
        if not quantization and not model_declares_own_quantization(model):
            if "70b" in model.lower() or "72b" in model.lower():
                quantization = "fp8"

        tool_call_parser = self.resolve_tool_call_parser(model, tool_call_parser)

        base_args: List[str] = [
            "--host", "0.0.0.0",
            "--port", str(port),
            "--max-model-len", str(max_model_len),
            "--gpu-memory-utilization", str(gpu_memory_utilization),
            "--trust-remote-code",
            "--async-scheduling",
            "--load-format", "fastsafetensors",
            "--enable-log-requests",
            "--enable-log-outputs",
            "--max-log-len", "2048",
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
        if moe_backend:
            base_args.extend(["--moe-backend", moe_backend])
        if api_key:
            base_args.extend(["--api-key", api_key])
        if enable_auto_tool_choice:
            base_args.append("--enable-auto-tool-choice")
        if tool_call_parser:
            base_args.extend(["--tool-call-parser", tool_call_parser])
        if reasoning_parser:
            base_args.extend(["--reasoning-parser", reasoning_parser])
        if guided_decoding_backend:
            base_args.extend(self.build_structured_outputs_args(guided_decoding_backend, docker_image))

        docker_available = self.is_docker_available()
        is_docker_launch = docker_available  # Always prefer Docker when available

        # Ask the image whether it can import tensorizer. The image name says nothing about this.
        if use_tensorizer and is_model_tensorized(model):
            has_tensorizer = self.image_has_tensorizer(docker_image)
            if not has_tensorizer:
                if is_docker_launch:
                    print(f"⚠️  Notice: Running vLLM via Docker container ({docker_image}) which does not include the 'tensorizer' package.")
                else:
                    print("⚠️  Notice: 'tensorizer' package is not installed in vLLM environment. (Install via: pip install 'vllm[tensorizer]')")
            else:
                tpath = get_tensorized_path(model)
                if tpath:
                    t_uri = str(tpath)
                    if is_docker_launch:
                        dgx_cache_host = os.path.expanduser("~/.cache/dreamference")
                        if t_uri.startswith(dgx_cache_host):
                            t_uri = t_uri.replace(dgx_cache_host, "/root/.cache/dreamference", 1)
                    base_args.extend(["--load-format", "tensorizer"])
                    base_args.extend(["--model-loader-extra-config", json.dumps({"tensorizer_uri": t_uri, "tensorizer_dir": None})])

        if docker_available:
            hf_cache = os.path.expanduser("~/.cache/huggingface")
            dgx_cache = os.path.expanduser("~/.cache/dreamference")
            os.makedirs(hf_cache, exist_ok=True)
            os.makedirs(dgx_cache, exist_ok=True)
            
            # Leave the host some CPU so the desktop keeps scheduling during model load.
            total_cpus = os.cpu_count() or 1
            cpus_limit = max(1.0, total_cpus * 0.7)

            # The memory cap is what actually stops a hard freeze. On unified memory the weights
            # vLLM pins come out of the same pool as the compositor's, and the kernel cannot
            # reclaim driver-pinned pages — so it livelocks in reclaim instead of OOM-killing
            # anything (journals from the freezes show NVRM NV_ERR_NO_MEMORY and page-cache
            # flushing, but no oom-kill). Capping the container's cgroup gives the kernel a
            # bounded scope it *can* kill, turning a power-button reset into a dead container.
            # --memory-swap equal to --memory disables container swap: swapping 70+ GB of weights
            # to a 16 GB swapfile is what drags the desktop under before the cap is ever reached.
            from dreamference.hardware.hardware_manager import HardwareManager
            container_mem_gb = max(
                1.0,
                HardwareManager.detect_gb10_hardware().total_unified_memory_gb - HOST_MEMORY_RESERVE_GB,
            )

            cmd = [
                "docker", "run",
                "--ipc=host",
                "--network", "host",
                "--name", f"dreamference-vllm-{port}",
                "--gpus", "all",
                f"--cpus={cpus_limit:.1f}",
                f"--memory={container_mem_gb:.0f}g",
                f"--memory-swap={container_mem_gb:.0f}g",
                f"--oom-score-adj={CONTAINER_OOM_SCORE_ADJ}",
                "-v", f"{hf_cache}:/root/.cache/huggingface",
                "-v", f"{dgx_cache}:/root/.cache/dreamference",
            ]
            if token_env:
                cmd.extend(["-e", f"HF_TOKEN={token_env}"])
            # Kernel-backend selection for some layers (notably NVFP4 MoE) is read from the process
            # environment rather than CLI flags, so the recipe's env has to cross the container boundary.
            for env_key, env_val in sorted(recipe.get("env", {}).items()):
                cmd.extend(["-e", f"{env_key}={env_val}"])
            cmd.extend(["-e", "CUTE_DSL_ARCH=sm_121a"])
            cmd.extend(["-e", "VLLM_LOGGING_LEVEL=DEBUG"])
            cmd.extend(["--entrypoint", "vllm", docker_image, "serve", hf_model] + base_args)
        else:
            raise RuntimeError(
                "Docker is required to run vLLM but is not available. "
                "Please ensure Docker is installed and the daemon is running (`docker ps` must succeed)."
            )

        if quantization:
            cmd.extend(["--quantization", quantization])

        if hf_draft_model:
            cmd.extend(["--speculative-model", hf_draft_model, "--num-speculative-tokens", str(num_speculative_tokens)])
        elif recipe.get("speculative_config"):
            # Self-speculation (MTP/Eagle heads shipped inside the checkpoint) has no separate draft
            # model, so it is configured as a blob rather than via --speculative-model.
            cmd.extend(["--speculative-config", json.dumps(recipe["speculative_config"])])

        extra_args = recipe.get("extra_args")
        if extra_args:
            cmd.extend(str(a) for a in extra_args)

        return cmd

    @classmethod
    def check_host_safety(cls) -> None:
        """
        Verifies the host can survive loading a large model, and exits with instructions if not.

        Applies to anything that materialises a full checkpoint — serving it or serialising it —
        because both pin the same tens of GB. Every check here failed open before 2026-08-14, when
        six loads in one hour took the machine down hard enough to need the power button.

        Every condition here is blocking. They are reported together rather than one at a time,
        because each fix needs a separate sudo and discovering them serially would mean five
        edit-rerun cycles to get to a first load.

        Raises:
            SystemExit: If any check fails.
        """
        problems: List[str] = []

        # sysstat's sar/sadc history is the only record of memory and I/O pressure that outlives a
        # hard reset. Without it a freeze leaves nothing to diagnose from.
        if shutil.which("sar") is None:
            problems.append(
                "sysstat is not installed.\n"
                "   Loading a model can freeze the machine, and sysstat's sar/sadc history is the\n"
                "   only post-mortem evidence that survives a hard reset.\n"
                "     sudo apt install sysstat\n"
                "     sudo sed -i 's/^ENABLED=.*/ENABLED=\"true\"/' /etc/default/sysstat\n"
                "     sudo systemctl enable --now sysstat"
            )

        # An OOM handler is the difference between a dead container and a dead machine. Every
        # freeze on 2026-08-14 shared one trait: the kernel OOM killer never ran. Driver-pinned
        # pages are unreclaimable and are not charged to any process the killer would pick, so the
        # kernel grinds in reclaim instead of ending anything. systemd-oomd and earlyoom watch PSI
        # pressure and act *before* that point, which is the only thing that reliably breaks the
        # livelock from inside.
        if not cls._oom_handler_active():
            problems.append(
                "No userspace OOM handler is running.\n"
                "   Loading a large model on unified memory can starve the host without ever\n"
                "   tripping the kernel OOM killer — the pages the driver pins are unreclaimable\n"
                "   and belong to no killable process, so the machine livelocks instead of\n"
                "   dropping the server. A PSI-driven handler ends it before that.\n"
                "     sudo systemctl enable --now systemd-oomd\n"
                "   or, covering every cgroup without per-slice opt-in:\n"
                "     sudo apt install earlyoom && sudo systemctl enable --now earlyoom"
            )

        # Swap is where the kernel puts cold anonymous pages when it needs room. Too little and it
        # has nowhere to shed them to, so it stalls on reclaim instead.
        # 1% tolerance: mkswap reserves a header page, so a file created at exactly the target size
        # reports slightly under it. Without this, `fallocate -l 64G` yields 63.99 GB and the check
        # can never pass however large the file is made.
        swap_gb = cls._swap_total_gb()
        if swap_gb < MIN_SWAP_GB * 0.99:
            problems.append(
                f"Swap is {swap_gb:.2f} GB; {MIN_SWAP_GB:.0f} GB or more is required.\n"
                f"   Swap is what lets the kernel shed cold anonymous pages slowly instead of\n"
                f"   stalling, and it costs only disk.\n"
                f"     sudo swapoff /swap.img\n"
                f"     sudo fallocate -l {MIN_SWAP_GB:.0f}G /swap.img && sudo chmod 600 /swap.img\n"
                f"     sudo mkswap /swap.img && sudo swapon /swap.img"
            )

        # Reclaim headroom. Both of these decide how early kswapd starts freeing pages relative to
        # how fast a loader can consume them. At stock values on a 128 GB box the gap between "we
        # should reclaim" and "we are out" is small enough that a 78 GB load crosses it faster than
        # reclaim can respond, which is the direct-reclaim stall the freezes were made of. Raising
        # them buys the kernel room to work ahead instead of blocking allocators.
        for name, wanted, why in (
            (
                "vm.min_free_kbytes",
                MIN_FREE_KBYTES,
                "keeps a larger emergency pool so allocations do not stall waiting on reclaim",
            ),
            (
                "vm.watermark_scale_factor",
                WATERMARK_SCALE_FACTOR,
                "starts background reclaim earlier, so kswapd stays ahead of the loader",
            ),
        ):
            current = cls._sysctl_int(name)
            if current is not None and current < wanted:
                problems.append(
                    f"{name} is {current}; {wanted} or more is required\n"
                    f"   ({why}).\n"
                    f"     sudo sysctl -w {name}={wanted}\n"
                    f"     echo '{name}={wanted}' | sudo tee -a /etc/sysctl.d/99-dreamference.conf"
                )

        if problems:
            listed = "\n\n".join(f"{i}. {p}" for i, p in enumerate(problems, 1))
            print(
                f"\n❌ Refusing to load: {len(problems)} host safety "
                f"{'check' if len(problems) == 1 else 'checks'} failed.\n\n"
                f"{listed}\n\n"
                f"These guard against the freeze mode this hardware is prone to: unreclaimable\n"
                f"driver-pinned pages starving the host with no OOM kill to end it.\n"
            )
            sys.exit(1)

    @staticmethod
    def _oom_handler_active() -> bool:
        """
        Reports whether a userspace OOM handler is running.

        Checks systemd-oomd and earlyoom, the two PSI-driven handlers packaged on Ubuntu. Either
        one is sufficient; the point is only that *something* will act on memory pressure before
        the kernel livelocks in reclaim.

        Returns:
            bool: True if systemd-oomd or earlyoom is active, False otherwise.
        """
        for unit in ("systemd-oomd", "earlyoom"):
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", "--quiet", unit],
                    timeout=5,
                )
                if result.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
        return False

    @staticmethod
    def _sysctl_int(name: str) -> Optional[int]:
        """
        Reads an integer sysctl from /proc/sys without shelling out.

        Args:
            name (str): Dotted sysctl name, e.g. 'vm.min_free_kbytes'.

        Returns:
            Optional[int]: The value, or None if the knob is absent or unreadable.
        """
        try:
            with open("/proc/sys/" + name.replace(".", "/"), "r") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _swap_total_gb() -> float:
        """
        Reads total configured swap in GB from /proc/meminfo.

        Returns:
            float: Total swap in GB, or 0.0 if /proc/meminfo is unreadable.
        """
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("SwapTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
        except (OSError, ValueError, IndexError):
            pass
        return 0.0

    @staticmethod
    def _evict_model_page_cache(model: str) -> float:
        """
        Drops the model's shards from the page cache ahead of a load.

        A failed or repeated launch leaves tens of GB of shard pages cached — 98 GB was resident
        when this machine last froze. Those pages are clean and reclaimable in principle, but
        reclaiming them *while* the loader streams the same files is what turns a slow load into a
        livelock. Evicting first means the loader starts against free memory instead of racing
        reclaim for it.

        Uses posix_fadvise(POSIX_FADV_DONTNEED), which needs no privileges and touches only this
        model's files, rather than /proc/sys/vm/drop_caches, which needs root and evicts globally.

        Args:
            model (str): Model key or alias whose snapshot should be evicted.

        Returns:
            float: Approximate GB of file data the eviction was requested for.
        """
        from dreamference.hardware import ModelDownloader

        try:
            snapshot_dir = ModelDownloader.get_model_snapshot_dir(model)
        except Exception:
            snapshot_dir = None
        if not snapshot_dir:
            return 0.0

        evicted_bytes = 0
        for path in sorted(snapshot_dir.rglob("*")):
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
                fd = os.open(path, os.O_RDONLY)
            except OSError:
                continue
            try:
                # length 0 means "to end of file"; DONTNEED is advisory and silently declines to
                # drop pages that are still mapped or dirty, so this can only ever help.
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                evicted_bytes += size
            except (OSError, AttributeError):
                pass
            finally:
                os.close(fd)

        return evicted_bytes / (1024 ** 3)

    @staticmethod
    def _estimate_model_weights_gb(model: str) -> float:
        """
        Estimates the resident weight footprint of a model in GB.

        Prefers the on-disk size of the downloaded HuggingFace snapshot, which is exact for the
        quantized checkpoints this project serves. Falls back to the registry's declared minimum
        when the model has not been fetched yet, so the check still has a number to work with on
        a first run.

        Args:
            model (str): Model key or alias to size.

        Returns:
            float: Estimated weight footprint in GB, or 0.0 if neither source knows the model.
        """
        from dreamference.hardware import ModelDownloader, ModelMatrixRegistry

        try:
            snapshot_dir = ModelDownloader.get_model_snapshot_dir(model)
        except Exception:
            snapshot_dir = None

        if snapshot_dir:
            total_bytes = 0
            # Snapshots are trees of symlinks into the blob store; follow them so the shards are
            # counted at full size rather than as zero-byte links.
            for path in snapshot_dir.rglob("*"):
                try:
                    if path.is_file():
                        total_bytes += path.stat().st_size
                except OSError:
                    continue
            if total_bytes:
                return total_bytes / (1024 ** 3)

        try:
            spec = ModelMatrixRegistry.get_spec(model)
        except Exception:
            spec = None
        return float(spec.min_memory_gb) if spec else 0.0

    def start_server(
        self,
        model: str = DEFAULT_MODEL,
        port: int = 8000,
        quantization: Optional[str] = None,
        draft_model: Optional[str] = None,
        num_speculative_tokens: int = 5,
        hf_token: Optional[str] = None,
        enable_prefix_caching: bool = True,
        enable_chunked_prefill: bool = True,
        num_scheduler_steps: int = 8,
        attention_backend: Optional[str] = None,
        kv_cache_dtype: Optional[str] = None,
        api_key: Optional[str] = None,
        enable_auto_tool_choice: bool = True,
        tool_call_parser: Optional[str] = None,
        reasoning_parser: Optional[str] = None,
        moe_backend: Optional[str] = None,
        max_num_batched_tokens: Optional[int] = 8192,
        guided_decoding_backend: Optional[str] = None,
        use_tensorizer: Optional[bool] = None,
        background: bool = True,
        docker_image: str = DEFAULT_VLLM_IMAGE,
    ) -> Optional[subprocess.Popen]:
        """
        Pre-downloads model weights, saves in tensorize format, and starts local vLLM OpenAI API server.

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
            attention_backend (Optional[str]): Attention backend.
            kv_cache_dtype (Optional[str]): KV cache precision.
            api_key (Optional[str]): Optional API key (not set by default).
            enable_auto_tool_choice (bool): Enable automatic tool choice.
            tool_call_parser (Optional[str]): Tool call parser name.
            reasoning_parser (Optional[str]): Reasoning-channel parser name.
            moe_backend (Optional[str]): Mixture-of-experts kernel backend.
            max_num_batched_tokens (Optional[int]): Max tokens per batch when chunked prefill active.
            guided_decoding_backend (Optional[str]): Structured-outputs backend; None leaves vLLM's default.
            use_tensorizer (Optional[bool]): Convert and load model using tensorize (.tensors) format.
            background (bool): If True, run asynchronously as Popen subprocess.
            docker_image (str): Docker image to launch vLLM in (default: DEFAULT_VLLM_IMAGE).

        Returns:
            Optional[subprocess.Popen]: Popen object if background=True, else None.
        """
        if not self.is_docker_available():
            raise RuntimeError(
                "Docker is required to run vLLM but is not available. "
                "Please ensure Docker is installed and the daemon is running (`docker ps` must succeed)."
            )

        if not self.ensure_docker_image(docker_image):
            raise RuntimeError(
                f"Docker image '{docker_image}' is missing locally and could not be built. "
                "Please check the Dockerfile or Docker daemon status."
            )

        # GB10 hardware check
        from dreamference.hardware.hardware_manager import HardwareManager
        hw = HardwareManager.detect_gb10_hardware()
        if not hw.is_gb10:
            raise RuntimeError(
                "dream server_start requires NVIDIA GB10 hardware (or ≥100 GB unified memory). "
                "Current system does not meet the target specs."
            )

        self.check_host_safety()

        # Check memory availability.
        #
        # The budget that matters is the weight footprint, not a fraction of installed memory:
        # gpu_memory_utilization sizes the KV/activation arena vLLM carves out, and scaling total
        # memory by it says nothing about whether the checkpoint itself fits. A 78 GB checkpoint
        # under a 0.3 recipe used to "require" ~38 GB and sail through this gate.
        from dreamference.hardware import get_model_launch_overrides
        overrides = get_model_launch_overrides(model)
        gpu_memory_utilization = overrides.get("gpu_memory_utilization", 0.9)

        actual_total_gb = hw.total_unified_memory_gb
        actual_free_gb = hw.available_memory_gb

        weights_gb = self._estimate_model_weights_gb(model)
        if draft_model:
            weights_gb += self._estimate_model_weights_gb(draft_model)

        # vLLM sizes its whole allocation — weights included — as a fraction of total memory,
        # so this arena is the ceiling on everything the server will pin.
        arena_gb = actual_total_gb * gpu_memory_utilization

        def _abort(headline: str, detail: str) -> None:
            try:
                ps_output = subprocess.check_output(
                    ["ps", "-eo", "pid,user,%mem,rss,cmd", "--sort=-%mem"],
                    text=True
                )
                top_procs = "\n".join(ps_output.splitlines()[:11])
            except Exception:
                top_procs = "Could not retrieve process list."
            print(f"\n❌ {headline}\n{detail}\n\nTop memory consuming processes:\n{top_procs}\n")
            sys.exit(1)

        # Weights are pinned by the driver and therefore unreclaimable, so they have to fit
        # alongside the host's reserve on their own. This is a static floor only: it cannot model
        # the reclaim storm that actually froze this machine, which is what the container's cgroup
        # memory cap in build_launch_command is there to bound.
        if weights_gb and weights_gb > actual_total_gb - HOST_MEMORY_RESERVE_GB:
            _abort(
                f"'{model}' is too large to load on this machine.",
                f"   Weights:     {weights_gb:.2f} GB\n"
                f"   Usable:      {actual_total_gb - HOST_MEMORY_RESERVE_GB:.2f} GB "
                f"({actual_total_gb:.2f} GB total - {HOST_MEMORY_RESERVE_GB:.2f} GB host reserve)\n\n"
                f"gpu_memory_utilization does not govern this — the weights overrun memory before the\n"
                f"arena is consulted, so lowering it will not help. Serve a smaller checkpoint.",
            )

        # The checkpoint also has to fit the arena it will be loaded into, or vLLM's own budget is
        # incoherent: a 78 GB checkpoint under a 0.3 recipe asks for an arena less than half its
        # own weights. The `total * utilization` check this replaced could not see that.
        if weights_gb and weights_gb >= arena_gb:
            _abort(
                f"Model weights do not fit the configured memory arena for '{model}'.",
                f"   Weights:     {weights_gb:.2f} GB\n"
                f"   Arena:       {arena_gb:.2f} GB "
                f"({gpu_memory_utilization:.2f} x {actual_total_gb:.2f} GB total)\n\n"
                f"This recipe's gpu_memory_utilization is too low to hold its own weights — it needs\n"
                f"at least {weights_gb / actual_total_gb:.2f}. Fix the recipe rather than the machine.",
            )

        # The arena has to leave the host enough to stay alive. Driver-pinned pages are not
        # reclaimable, so an arena that crowds the host does not get an OOM kill — it freezes.
        if actual_total_gb - arena_gb < HOST_MEMORY_RESERVE_GB:
            _abort(
                f"Memory arena leaves too little for the host.",
                f"   Arena:       {arena_gb:.2f} GB "
                f"({gpu_memory_utilization:.2f} x {actual_total_gb:.2f} GB total)\n"
                f"   Host left:   {actual_total_gb - arena_gb:.2f} GB\n"
                f"   Reserve:     {HOST_MEMORY_RESERVE_GB:.2f} GB\n\n"
                f"Lower gpu_memory_utilization to at most "
                f"{(actual_total_gb - HOST_MEMORY_RESERVE_GB) / actual_total_gb:.2f} for this machine.",
            )

        # Hand the loader free memory rather than memory it has to reclaim out from under itself.
        # Done before the availability check below so that check scores the state the loader will
        # actually see, not the state a previous failed attempt left behind.
        evicted_gb = self._evict_model_page_cache(model)
        if evicted_gb:
            actual_free_gb = HardwareManager.detect_gb10_hardware().available_memory_gb
            print(
                f"🧹 Dropped ~{evicted_gb:.2f} GB of cached shards for '{model}' "
                f"({actual_free_gb:.2f} GB now available)."
            )

        # And the arena has to be free right now, not merely installed.
        if actual_free_gb < arena_gb:
            _abort(
                "Not enough memory to start vLLM.",
                f"   Required:    {arena_gb:.2f} GB\n"
                f"   Available:   {actual_free_gb:.2f} GB\n"
                f"   Missing:     {arena_gb - actual_free_gb:.2f} GB",
            )

        # Step 1: Pre-download model weights into local cache and convert to tensorize format.
        # Resolve the tensorizer decision up front: some checkpoints opt out in their registry recipe,
        # and converting one anyway would burn time and disk on an artifact the launcher will not use.
        from dreamference.hardware import download_model, get_model_launch_overrides
        tensorize = use_tensorizer
        if tensorize is None:
            tensorize = get_model_launch_overrides(model).get("use_tensorizer", False)
        download_model(model, hf_token=hf_token, auto_tensorize=tensorize)
        if draft_model:
            download_model(draft_model, hf_token=hf_token, auto_tensorize=tensorize)

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
            reasoning_parser=reasoning_parser,
            moe_backend=moe_backend,
            max_num_batched_tokens=max_num_batched_tokens,
            guided_decoding_backend=guided_decoding_backend,
            use_tensorizer=use_tensorizer,
            docker_image=docker_image,
        )

        # Step 3: Cleanup potential container name conflicts prior to launch
        if cmd and cmd[0] == "docker":
            container_name = f"dreamference-vllm-{port}"
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        print(f"🚀 Starting GB10 vLLM Server: {' '.join(cmd)}")
        
        env = os.environ.copy()
        token_val = hf_token or os.getenv("HF_TOKEN") or os.getenv("DREAMFERENCE_HF_TOKEN")
        
        # Configure HuggingFace to use local cache exclusively when possible
        from dreamference.hardware.model_downloader import ModelDownloader
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

        # Guard the load itself. The preflight checks are static and the cgroup cap only bounds
        # what the container can take; neither can see the host sliding into a reclaim livelock,
        # which is the state that actually required the power button. This watches for it and
        # ends the container first.
        self.watchdog = None
        if cmd and cmd[0] == "docker":
            def _report(pressure: float, held_for_s: float) -> None:
                print(
                    f"\n🛑 Aborting load: memory stalled {pressure:.0f}% of the last 10s for "
                    f"{held_for_s:.0f}s straight.\n"
                    f"   Every task on the host was blocked on memory — the state that precedes a\n"
                    f"   freeze. Killing '{container_name}' to give the memory back.\n"
                )

            self.watchdog = MemoryPressureWatchdog(container_name, on_trip=_report)
            if not self.watchdog.start():
                self.watchdog = None
                print("⚠️  Kernel does not expose /proc/pressure/memory; load is unguarded.")

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
            try:
                subprocess.run(cmd, check=True, env=env)
            finally:
                if self.watchdog is not None:
                    self.watchdog.stop()
            return None

    def stop_server(self, port: int = 8000) -> None:
        """
        Stops the vLLM Docker container for the given port.
        Safe no-op if container is not running.
        """
        container_name = f"dreamference-vllm-{port}"
        print(f"🛑 Stopping vLLM container: {container_name}")
        # Disarm first, so the watchdog cannot race the shutdown and report a kill of its own.
        if self.watchdog is not None:
            self.watchdog.stop()
            self.watchdog = None
        subprocess.run(["docker", "stop", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"✅ Container {container_name} stopped.")

    def remove_server(self, port: int = 8000) -> None:
        """
        Removes the vLLM Docker container for the given port.
        Safe no-op if container does not exist.
        """
        container_name = f"dreamference-vllm-{port}"
        print(f"🗑️ Removing vLLM container: {container_name}")
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"✅ Container {container_name} removed.")

    def show_request_logs(self, port: int = 8000) -> None:
        """
        Tails the vLLM Docker container logs for the given port.
        """
        container_name = f"dreamference-vllm-{port}"
        print(f"📄 Tailing logs for vLLM container: {container_name} (Ctrl+C to exit)")
        subprocess.run(["docker", "logs", "-f", container_name])

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
