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
import time
import requests
from pathlib import Path
from typing import Dict, Any, Optional, List, Final

from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS as DEFAULT_MODEL
from dreamference.vllm_server.vllm_server_status import VLLMServerStatus
from dreamference.vllm_server.vllm_log_streamer import VLLMLogStreamer
from dreamference.vllm_server.psi_watchdog import MemoryPressureWatchdog
from dreamference.vllm_server.sglang_launch_builder import SGLangLaunchBuilder
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
# Extra memory a load needs *transiently*, beyond the arena it settles into: page cache for the
# shards being streamed, the loader's staging buffers, and the host's own growth while it waits.
# Expressed as a fraction of checkpoint size because that is what it scales with — a 78 GB
# checkpoint churns an order of magnitude more cache than an 8 GB one.
#
# 0.15 is calibrated against the 2026-08-14 14:11 freeze, which is the tightest known-bad point:
# a 78 GB checkpoint into a 97.3 GB arena with 105.8 GB available, i.e. 8.5 GB of slack, took the
# machine down. 0.15 x 78 = 11.7 GB required, so that launch is refused. Raise it if a load ever
# freezes with this gate satisfied; do not lower it without a load that failed for room to spare.
LOAD_TRANSIENT_FRACTION: Final[float] = 0.15
# Floor for the above, so a small checkpoint still leaves the host something to breathe with.
MIN_LOAD_TRANSIENT_GB: Final[float] = 4.0
# Slack between the arena and the container's cgroup cap. The cap only means something if it sits
# *below* the point at which the host dies and *above* the arena vLLM legitimately needs; sized
# against total memory it did neither. This is what turns hitting the cap into a dead container
# rather than a dead machine, so it covers the container's own transient loading overhead only.
CONTAINER_MEM_HEADROOM_GB: Final[float] = 8.0
# earlyoom arguments that actually arm it on this hardware, and the command that installs them.
#
# `-s 100` is the load-bearing part: earlyoom acts only when available memory *and* free swap are
# both under their minimums, and driver-pinned pages never reach swap, so any lower value is a
# gate that can never open. `-r 60` replaces the stock hourly report, so the journal carries a
# memory trace into the next freeze instead of one line an hour.
#
# `-m 5,2` rather than the stock 10: on this hardware a *healthy* load sits below 10% for its
# entire duration. The driver pins the whole arena at CUDA init, before any weights are read —
# vLLM logs `worker requested memory: 87.57GiB` and `non_torch_memory=19.76GiB` at 0/9 shards —
# so MemAvailable collapses within seconds of launch and stays there. Measured on 2026-08-14 it
# troughed at 9.82%, 9.79% and 9.90% across three consecutive loads, and stock earlyoom SIGTERMed
# EngineCore every time. A free-memory percentage cannot tell a working load from a dying one
# here; both look the same. That distinction is the PSI watchdog's job (see psi_watchdog), and
# earlyoom's is to be the last resort before a livelock, which 5% still comfortably is.
EARLYOOM_ARGS: Final[str] = '-m 5,2 -s 100 -r 60'
# Above this, earlyoom's memory threshold kills healthy loads rather than dying ones — see the
# measured troughs above. Checked as well as the swap gate, since either misconfiguration makes
# the handler worse than useless: one never fires, the other fires on every successful launch.
MAX_EARLYOOM_MEM_PCT: Final[float] = 6.0
EARLYOOM_CONFIGURE_CMD: Final[str] = (
    f"printf '%s\\n' 'EARLYOOM_ARGS=\"{EARLYOOM_ARGS}\"' | sudo tee /etc/default/earlyoom"
)
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

# Sampling defaults pushed onto the server, overriding whatever the checkpoint's
# generation_config.json asks for.
#
# This is not a preference, it is a correctness requirement for the clients this project drives.
# An OpenAI-compatible request that omits `temperature` does not mean "use zero" — it means "use
# the server's default", and the served checkpoint ships temperature 0.6 / top_p 0.95 / top_k 20.
# Codex has no sampling setting at all (`model_reasoning` and `model_verbosity` exist; nothing for
# temperature), so it can never ask, and every one of its turns sampled at 0.6 until this was set.
# Measured on 2026-08-15 from the request log: 52 requests at temperature=0.6 against 2 at 0.0,
# and the only two at 0.0 were probes that passed it explicitly.
#
# A server default, not a clamp: a client that does send temperature still wins, so benchmarking
# and any deliberately creative use keep working.
DEFAULT_GENERATION_OVERRIDES: Final[Dict[str, Any]] = {
    "temperature": 0.0,
    "top_p": 1.0,
    # 0 disables top-k in vLLM. Leaving it at the checkpoint's 20 would keep sampling from a
    # truncated distribution even at temperature 0.
    "top_k": 0,
}
# Let vLLM pick the weight loader. See the resolution site in build_launch_command for why this
# is not fastsafetensors: without GDS — which GB10 does not have — it double-resides the
# checkpoint in host RAM, which on unified memory is the whole memory budget.
DEFAULT_LOAD_FORMAT: Final[str] = "auto"

# Where vLLM keeps everything it can rebuild but would rather not: the torch.compile artefacts
# above all. It defaults to ~/.cache/vllm *inside* the container, which is a fresh tmpfs-like layer
# on every `docker run`, so each restart recompiled the graph from scratch — 8-12 minutes of a
# silent container before the port opens. Pointing it inside the already-mounted dreamference cache
# makes it survive restarts without needing a second volume.
CONTAINER_VLLM_CACHE_ROOT: Final[str] = "/root/.cache/dreamference/vllm"
# Host-side view of the same directory, used to invalidate the compile cache before a launch.
CONTAINER_TELEMETRY_OPT_OUT: Final[Dict[str, str]] = {"VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1"}

# One throwaway request after every boot, before `server start` reports ready: the first batch a
# freshly started engine serves runs at about two-thirds speed (the hasso5703 measurements on this
# hardware: 23.8 against 40-48 tok/s), so without it the user's first real request is the slow one.
# Code, because it runs the speculative drafter at full depth, and long enough to leave prefill.
WARM_UP_PROMPT: Final[str] = "Write a Python function that returns the n-th Fibonacci number iteratively."
WARM_UP_MAX_TOKENS: Final[int] = 128
WARM_UP_TIMEOUT_S: Final[float] = 180.0

VLLM_CACHE_HOME: Final[Path] = Path.home() / ".cache" / "dreamference" / "vllm"

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
            if resp.status_code != 200:
                return False
                
            # Extra check: ensure completions endpoint is actually ready
            import urllib.request
            import json
            chat_url = f"{self.host}/v1/chat/completions"
            payload = {
                "model": resp.json().get("data", [{}])[0].get("id", ""),
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 1
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(chat_url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as chat_resp:
                return chat_resp.status == 200
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

    def warm_up(self, timeout: float = WARM_UP_TIMEOUT_S) -> Optional[float]:
        """
        Sends one discarded chat request so the first real one runs at full speed.

        Engine-neutral: it goes through the OpenAI-compatible API both vLLM and SGLang serve.
        A failure never fails the start; the server is up either way.

        Args:
            timeout (float): Request timeout in seconds.

        Returns:
            Optional[float]: Seconds the request took, or None if it could not be made.
        """
        models = self.get_models()
        if not models:
            return None
        start = time.monotonic()
        try:
            response = requests.post(
                f"{self.host}/v1/chat/completions",
                json={
                    "model": models[0],
                    "messages": [{"role": "user", "content": WARM_UP_PROMPT}],
                    "max_tokens": WARM_UP_MAX_TOKENS,
                    "temperature": 0.0,
                },
                timeout=timeout,
            )
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        return time.monotonic() - start

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

        # A registry-qualified reference belongs to somebody else and has to be fetched, not built.
        # Building it would tag this project's own Dockerfile output with a third-party name and
        # then launch it believing it was theirs — which, now that recipes can pin their own image,
        # is a live possibility rather than a hypothetical.
        if "/" in docker_image:
            print(f"📦 Docker image '{docker_image}' not found locally. Pulling...")
            try:
                subprocess.run(["docker", "pull", docker_image], check=True)
            except (subprocess.CalledProcessError, OSError) as e:
                print(f"❌ Failed to pull Docker image '{docker_image}': {e}")
                return False
            if self.is_image_present(docker_image):
                print(f"✅ Pulled Docker image '{docker_image}'.")
                return True
            return False

        project_root = Path(__file__).resolve().parent.parent.parent
        dockerfile_path = project_root / "Dockerfile"

        # The plain Dockerfile produces DEFAULT_VLLM_IMAGE and nothing else. A recipe that pins
        # another local tag has to be built by hand under that tag; building the plain Dockerfile
        # under its name used to hand vLLM the wrong engine with the right label.
        if docker_image != DEFAULT_VLLM_IMAGE:
            print(f"❌ Docker image '{docker_image}' is not present, and it is not the image "
                  f"the project Dockerfile builds ({DEFAULT_VLLM_IMAGE}).")
            print("💡 Build that image by hand and tag it as the recipe pins it.")
            return False

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

        elif not dockerfile_path.is_file():
            # A release install has the package but not the repository's Dockerfile beside it.
            print(f"❌ Docker image '{docker_image}' is not present, and it is built from the "
                  "Dockerfile in Mightling's repository, which a release install does not have.")
            print("💡 Clone the repository and run `docker build -t "
                  f"{docker_image} .` there, or use a model whose recipe pulls its image "
                  "(the default model does).")
            return False

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
        # Probing reports on an image; it does not go and get one. This used to call
        # ensure_docker_image, which was harmless while that only ever built a local Dockerfile —
        # but once recipes could pin a registry image, and ensure_docker_image learned to pull,
        # merely *building a command* for a model whose image was not yet on disk started a
        # multi-gigabyte download. Acquisition belongs to start_server, which does it deliberately
        # and once.
        #
        # The unprobed result is deliberately not cached: the image is usually absent only until
        # start_server pulls it, and a cached "unknown" would outlive the reason for it and pick
        # the wrong flag spelling for the rest of the process.
        if not self.is_image_present(docker_image):
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
        # None, not 8192. Every optional argument here means "unset, fall back to the model's
        # recipe"; a literal default would satisfy `resolved()` before the recipe is ever
        # consulted, which is what silently pinned this at 8192 for every model regardless of what
        # its launch_overrides asked for. The 8192 fallback lives in `resolved()` below.
        max_num_batched_tokens: Optional[int] = None,
        guided_decoding_backend: Optional[str] = None,
        use_tensorizer: Optional[bool] = None,
        # None so the model's recipe can pin its own image. A concrete default here would shadow
        # `docker_image` in launch_overrides exactly the way the old 8192 shadowed
        # max_num_batched_tokens.
        docker_image: Optional[str] = None,
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
            enable_prefix_caching (bool): Flag to enable prefix KV cache. A recipe carrying
                `enable_prefix_caching: False` overrides a True passed here, because that setting
                records a checkpoint that cannot run with it rather than a preference.
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
            docker_image (Optional[str]): Docker image to launch vLLM in. None falls back to the
                model's `docker_image` recipe entry, then to the pinned DEFAULT_VLLM_IMAGE.

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
        # Resolved before anything inspects the image: image_has_tensorizer and
        # build_structured_outputs_args both probe it, and probing the wrong one produces flags
        # built for a vLLM that is not the one about to run.
        docker_image = resolved(docker_image, "docker_image", DEFAULT_VLLM_IMAGE)
        if not attention_backend or attention_backend == "auto":
            attention_backend = recipe.get("attention_backend", "auto")

        # Prefix caching is the one knob a recipe may veto outright rather than merely default.
        # The others express a preference the caller is free to overrule; this one can express an
        # impossibility. A hybrid GDN/mamba target fronted by a drafter with a larger attention
        # page ends up with KV cache groups whose block sizes do not divide the hash granularity,
        # and vLLM's coordinator aborts at startup rather than degrading — so a config-level
        # `enable_prefix_caching = true` has to lose to the checkpoint that cannot honour it.
        if recipe.get("enable_prefix_caching") is False:
            enable_prefix_caching = False

        # 70B/72B checkpoints published in BF16 need an explicit FP8 request to fit GB10. Checkpoints
        # that are already quantized announce their format in config.json, so adding a guess here would
        # override vLLM's own detection with a value derived from nothing but the model name.
        if not quantization and not model_declares_own_quantization(model):
            if "70b" in model.lower() or "72b" in model.lower():
                quantization = "fp8"

        tool_call_parser = self.resolve_tool_call_parser(model, tool_call_parser)

        if SGLangLaunchBuilder.is_sglang(recipe):
            if not self.is_docker_available():
                raise RuntimeError(
                    "Docker is required to run the model server but is not available. "
                    "Please ensure Docker is installed and the daemon is running (`docker ps` must succeed)."
                )
            cmd = self._docker_run_prefix(port, gpu_memory_utilization, recipe, token_env)
            for env_key, env_val in sorted(SGLangLaunchBuilder.container_env().items()):
                cmd.extend(["-e", f"{env_key}={env_val}"])
            # The image's own entrypoint, as the recipe this entry follows runs it.
            cmd.append(docker_image)
            cmd.extend(SGLangLaunchBuilder.server_args(
                hf_model, recipe, port, max_model_len, gpu_memory_utilization,
                tool_call_parser, reasoning_parser, api_key,
            ))
            return cmd

        base_args: List[str] = [
            "--host", "0.0.0.0",
            "--port", str(port),
            "--max-model-len", str(max_model_len),
            "--gpu-memory-utilization", str(gpu_memory_utilization),
            "--trust-remote-code",
            "--async-scheduling",
            "--enable-log-requests",
            "--enable-log-outputs",
            "--max-log-len", "2048",
        ]

        if enable_prefix_caching:
            base_args.append("--enable-prefix-caching")
        else:
            # Stated in the negative rather than omitted. vLLM's CacheConfig defaults
            # enable_prefix_caching to True, so leaving the flag off asks for whatever vLLM
            # prefers — which for every model here is "on". Turning it off has to be said.
            base_args.append("--no-enable-prefix-caching")
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

        # Resolved like any other recipe key, so a model that genuinely wants the checkpoint's own
        # sampling can set `generation_overrides` to None and get it.
        generation_overrides = resolved(None, "generation_overrides", DEFAULT_GENERATION_OVERRIDES)
        if generation_overrides:
            base_args.extend(["--override-generation-config", json.dumps(generation_overrides)])

        docker_available = self.is_docker_available()
        is_docker_launch = docker_available  # Always prefer Docker when available

        # One resolved loader, emitted once. This used to hardcode fastsafetensors here and append
        # a second --load-format for tensorizer below, leaving two copies of the flag on the
        # command line and the winner decided by argparse rather than by intent.
        #
        # The default is vLLM's own choice rather than fastsafetensors, because fastsafetensors
        # is a pessimisation on this hardware: it is built around GDS, the GB10 platform has no
        # GDS support ("GDS is not supported in this platform but nogds is False"), and its
        # fallback stages every shard through host bounce buffers. On unified memory the bounce
        # buffer and the destination tensor are the same physical RAM, so a 78 GB checkpoint is
        # resident twice at the peak. vLLM's default mmaps the shards instead, which is
        # page-cache backed and therefore reclaimable.
        load_format = resolved(None, "load_format", DEFAULT_LOAD_FORMAT)

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
                    load_format = "tensorizer"
                    base_args.extend(["--model-loader-extra-config", json.dumps({"tensorizer_uri": t_uri, "tensorizer_dir": None})])

        # 'auto' is vLLM's own default, so passing it explicitly only adds noise to the command
        # line and to any future diff of it.
        if load_format and load_format != "auto":
            base_args.extend(["--load-format", load_format])

        if docker_available:
            cmd = self._docker_run_prefix(port, gpu_memory_utilization, recipe, token_env)
            # Persist torch.compile output across container lifetimes. This lands inside the
            # dreamference cache volume _docker_run_prefix mounts, so no extra -v is needed; the recipe
            # env above cannot override it, since a per-model cache root would defeat the point.
            cmd.extend(["-e", f"VLLM_CACHE_ROOT={CONTAINER_VLLM_CACHE_ROOT}"])
            cmd.extend(["-e", "CUTE_DSL_ARCH=sm_121a"])
            cmd.extend(["-e", "VLLM_LOGGING_LEVEL=DEBUG"])
            cmd.extend(["-e", "VLLM_DEBUG_LOG_API_SERVER_RESPONSE=1"])
            cmd.extend(["-e", "VLLM_DEBUG_LOG_API_SERVER_REQUEST=1"])
            cmd.extend(["--entrypoint", "vllm", docker_image, "serve", hf_model] + base_args)
        else:
            raise RuntimeError(
                "Docker is required to run vLLM but is not available. "
                "Please ensure Docker is installed and the daemon is running (`docker ps` must succeed)."
            )

        if quantization:
            cmd.extend(["--quantization", quantization])

        speculative = self.resolve_speculative_config(model, draft_model, num_speculative_tokens)
        if speculative:
            cmd.extend(["--speculative-config", json.dumps(speculative)])

        extra_args = recipe.get("extra_args")
        if extra_args:
            cmd.extend(str(a) for a in extra_args)

        return cmd

    def _docker_run_prefix(
        self, port: int, gpu_memory_utilization: float, recipe: Dict[str, Any], token_env: Optional[str]
    ) -> List[str]:
        """
        Builds the engine-independent part of the `docker run` command.

        Shared by the vLLM and SGLang launches, so both get the same container name, cgroup
        memory cap, CPU limit, OOM score, mounts and recipe environment -- the host-safety layer
        does not depend on which engine runs inside.

        Args:
            port (int): Serving port; names the container.
            gpu_memory_utilization (float): The engine's memory fraction; sizes the cgroup cap.
            recipe (Dict[str, Any]): The model's launch_overrides ('env' and
                'container_headroom_gb' are read here).
            token_env (Optional[str]): HuggingFace token to pass through, if any.

        Returns:
            List[str]: `docker run` and its options, up to but excluding the image.
        """
        from dreamference.hardware.model_downloader import ModelDownloader
        dgx_cache = os.path.expanduser("~/.cache/dreamference")
        os.makedirs(dgx_cache, exist_ok=True)
        os.makedirs(VLLM_CACHE_HOME, exist_ok=True)
        
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
        #
        # Size it against the arena, not against total memory. `total - reserve` produced a
        # 110 GB cap for a 97.3 GB arena on 2026-08-14 — a ceiling the container could never
        # reach, so it bounded nothing and the host froze underneath it. Anchored to the arena
        # the cap is reachable by construction, and hitting it kills the container instead.
        # Still clamped by the host reserve, so a reckless recipe cannot raise it back out of
        # range.
        from dreamference.hardware.hardware_manager import HardwareManager
        total_mem_gb = HardwareManager.detect_gb10_hardware().total_unified_memory_gb
        container_mem_gb = max(
            1.0,
            min(
                total_mem_gb * float(gpu_memory_utilization)
                + float(recipe.get("container_headroom_gb", CONTAINER_MEM_HEADROOM_GB)),
                total_mem_gb - HOST_MEMORY_RESERVE_GB,
            ),
        )

        cmd = [
            "docker", "run",
            "--ipc=host",
            "--network", "host",
            "--restart", "unless-stopped",
            "--name", f"dreamference-vllm-{port}",
            "--gpus", "all",
            f"--cpus={cpus_limit:.1f}",
            f"--memory={container_mem_gb:.0f}g",
            f"--memory-swap={container_mem_gb:.0f}g",
            f"--oom-score-adj={CONTAINER_OOM_SCORE_ADJ}",
            *ModelDownloader.container_volume_args(),
            "-v", f"{dgx_cache}:/root/.cache/dreamference",
        ]
        if token_env:
            cmd.extend(["-e", f"HF_TOKEN={token_env}"])
        # vLLM reports hardware, model architecture, quantisation and settings to stats.vllm.ai at
        # start and every ten minutes after (no prompts). Found running on 2026-09-29; these turn
        # it off, and DO_NOT_TRACK covers the other libraries in either engine's image that honour
        # it.
        for env_key, env_val in CONTAINER_TELEMETRY_OPT_OUT.items():
            cmd.extend(["-e", f"{env_key}={env_val}"])
        # Kernel-backend selection for some layers (notably NVFP4 MoE) is read from the process
        # environment rather than CLI flags, so the recipe's env has to cross the container boundary.
        for env_key, env_val in sorted(recipe.get("env", {}).items()):
            cmd.extend(["-e", f"{env_key}={env_val}"])
        return cmd

    @classmethod
    def _stop_index_scopes(cls) -> None:
        """
        Stops every running `mling-code` index run before a model loads.

        mling-code runs its indexers in `mightling-index-*` scopes of the user's systemd
        (specs/DREAMFERENCE_MIGHTLING_CODE_INDEX.md §9.2) and stops them itself when it sees a load,
        but only after the load has begun; stopping them here keeps `check_host_safety()`'s view
        of free memory true. A stopped run is recorded `deferred: stopped` (no peak is recorded for it) and retried later.
        Without a user systemd (a container, CI) there is nothing to stop.
        """
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", "mightling-index-*"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

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
        oom_problem = cls._oom_handler_problem()
        if oom_problem:
            problems.append(oom_problem)

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
                f"driver-pinned pages starving the host with no OOM kill to end it.\n\n"
                f"💡 `mling-admin host setup` applies these for you (sudo asks for your password).\n"
            )
            sys.exit(1)

    @classmethod
    def _oom_handler_problem(cls) -> Optional[str]:
        """
        Reports why no userspace OOM handler will act on this host, if none will.

        Checks systemd-oomd and earlyoom, the two PSI-driven handlers packaged on Ubuntu. Either
        one is sufficient in principle; the point is that *something* acts on memory pressure
        before the kernel livelocks in reclaim.

        A running unit is not the same as an armed one, which is what the 2026-08-14 14:11 freeze
        cost a power button to establish. earlyoom was active throughout and never so much as
        logged: it only acts when available memory *and* free swap are both under their minimums,
        and the pages that starve this host are driver-pinned and unswappable, so free swap sits
        at 100% and the conjunction can never be satisfied. Adding a 64 GB swapfile that morning
        made it strictly worse — a small swap could at least fill. So earlyoom's actual argv is
        checked, not merely its unit state.

        Returns:
            Optional[str]: A description and fix, or None if a handler is running and armed.
        """
        if cls._unit_is_active("systemd-oomd"):
            return None

        earlyoom_argv = cls._process_argv("earlyoom")
        if earlyoom_argv is not None:
            fault = cls._earlyoom_fault(earlyoom_argv)
            if fault is None:
                return None
            return (
                f"earlyoom is running but is misconfigured for this hardware.\n"
                f"   {fault}\n"
                f"   Install arguments that work here, and report every minute so the next\n"
                f"   freeze leaves a trail:\n"
                f"     {EARLYOOM_CONFIGURE_CMD}\n"
                f"     sudo systemctl restart earlyoom\n"
                f"   Current arguments: {' '.join(earlyoom_argv[1:]) or '(none)'}"
            )

        return (
            "No userspace OOM handler is running.\n"
            "   Loading a large model on unified memory can starve the host without ever\n"
            "   tripping the kernel OOM killer — the pages the driver pins are unreclaimable\n"
            "   and belong to no killable process, so the machine livelocks instead of\n"
            "   dropping the server. A PSI-driven handler ends it before that.\n"
            "     sudo systemctl enable --now systemd-oomd\n"
            "   or, covering every cgroup without per-slice opt-in:\n"
            "     sudo apt install earlyoom\n"
            "     # then arm it — the stock swap gate leaves it unable to fire on this hardware\n"
            f"     {EARLYOOM_CONFIGURE_CMD}\n"
            "     sudo systemctl enable --now earlyoom"
        )

    @staticmethod
    def _unit_is_active(unit: str) -> bool:
        """
        Reports whether a systemd unit is active.

        Args:
            unit (str): Unit name, without the .service suffix.

        Returns:
            bool: True if systemctl reports the unit active, False otherwise or on error.
        """
        try:
            return subprocess.run(
                ["systemctl", "is-active", "--quiet", unit], timeout=5
            ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _process_argv(name: str) -> Optional[List[str]]:
        """
        Returns the argv of a running process, found by executable name.

        Read from /proc directly rather than via pgrep, because the arguments are the whole point:
        a unit file's ExecStart still contains the unexpanded `$EARLYOOM_ARGS`, so only the live
        process knows what the daemon is actually enforcing.

        Args:
            name (str): Executable basename to match, e.g. 'earlyoom'.

        Returns:
            Optional[List[str]]: The process's argv, or None if no such process is running.
        """
        try:
            entries = os.listdir("/proc")
        except OSError:
            return None

        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as f:
                    raw = f.read()
            except OSError:
                # Process exited between listdir and open, or belongs to another user.
                continue
            argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
            if argv and os.path.basename(argv[0]) == name:
                return argv
        return None

    @staticmethod
    def _earlyoom_flag_value(argv: List[str], flag: str) -> Optional[float]:
        """
        Reads a numeric earlyoom flag, in either the separated or attached form.

        Args:
            argv (List[str]): earlyoom's argv, including argv[0].
            flag (str): Flag to read, e.g. '-m'.

        Returns:
            Optional[float]: The SIGTERM component of the value — `-m 10,5` means SIGTERM at 10%
            and SIGKILL at 5%, and the SIGTERM point is the one reached first, so it governs.
            None if the flag is absent or unparseable.
        """
        for index, arg in enumerate(argv):
            if arg == flag:
                value = argv[index + 1] if index + 1 < len(argv) else ""
            elif arg.startswith(flag) and len(arg) > len(flag):
                value = arg[len(flag):]
            else:
                continue
            try:
                return float(value.split(",")[0])
            except ValueError:
                return None
        return None

    @classmethod
    def _earlyoom_fault(cls, argv: List[str]) -> Optional[str]:
        """
        Reports why earlyoom's thresholds are wrong for this hardware, if they are.

        Two ways to get this wrong, and the stock configuration manages both:

        * The swap gate. earlyoom acts only when available memory *and* free swap are below their
          minimums, and the pages that exhaust this host are driver-pinned and never reach swap,
          so free swap stays pegged near 100% and any `-s` under 100 holds it shut forever. This
          is why it was silent through the 2026-08-14 freeze.
        * The memory threshold. A healthy load on this hardware runs its whole duration under
          10% available, because the driver pins the arena at CUDA init before any weights are
          read. Stock `-m 10` therefore kills every successful launch, which it did three times
          in a row before this was calibrated.

        Args:
            argv (List[str]): earlyoom's argv, including argv[0].

        Returns:
            Optional[str]: A one-line description of the fault, or None if the thresholds work.
        """
        swap_pct = cls._earlyoom_flag_value(argv, "-s")
        swap_kib = cls._earlyoom_flag_value(argv, "-S")
        gate_open = (
            (swap_pct is not None and swap_pct >= 100.0)
            or (swap_kib is not None and swap_kib >= cls._swap_total_gb() * (1024 ** 2))
        )
        if not gate_open:
            return (
                f"Its swap gate ({'-s %g' % swap_pct if swap_pct is not None else 'default -s 10'}) "
                f"means it can never fire: it acts only when memory AND free swap are both low, "
                f"and driver-pinned pages never reach swap, so free swap stays at 100%."
            )

        mem_pct = cls._earlyoom_flag_value(argv, "-m")
        mem_pct = 10.0 if mem_pct is None else mem_pct
        if mem_pct > MAX_EARLYOOM_MEM_PCT:
            return (
                f"Its memory threshold (-m {mem_pct:g}) kills healthy loads: the driver pins the "
                f"whole arena at CUDA init, so a working load sits under 10% available for its "
                f"entire duration and earlyoom SIGTERMs the engine every time."
            )
        return None

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
        Reads the swap that is backed by disk, in GB.

        Compressed swap in memory (zram) is left out: on unified memory its pages are kept in the
        same RAM the model is short of, so it cannot be where cold pages go to make room, which is
        the whole point of the check. Ubuntu and DGX OS set up a swap file, not zram, but not
        every GB10 machine has been seen (specs/DREAMFERENCE_SETUP.md §3.5).

        Returns:
            float: Disk-backed swap in GB from /proc/swaps; where that cannot be read, SwapTotal
            from /proc/meminfo; 0.0 if neither can.
        """
        try:
            with open("/proc/swaps", "r") as f:
                lines = f.read().splitlines()[1:]
            total_kb = 0
            for line in lines:
                fields = line.split()
                if len(fields) >= 3 and not os.path.basename(fields[0]).startswith("zram"):
                    total_kb += int(fields[2])
            return total_kb / (1024 ** 2)
        except (OSError, ValueError):
            pass
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

    @classmethod
    def resolve_speculative_config(
        cls, model: str, draft_model: Optional[str] = None, num_speculative_tokens: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        The speculative-decoding config a launch uses: the recipe's, with a `--draft-model` layered on.

        vLLM 0.2x takes speculation only as `--speculative-config` JSON; the `--speculative-model`
        and `--num-speculative-tokens` flags this used to emit no longer exist, so a `--draft-model`
        launch failed at argument parsing. Layering onto the recipe's config, instead of replacing
        it, keeps what the recipe chose that a draft model does not change -- the method, and the
        drafter's attention backend.

        Args:
            model (str): Model alias whose recipe may carry a speculative config.
            draft_model (Optional[str]): Draft model alias or repo from `--draft-model`, if any.
            num_speculative_tokens (Optional[int]): Depth override; None keeps the recipe's.

        Returns:
            Optional[Dict[str, Any]]: The config to pass, or None when the launch has no speculation.
        """
        from dreamference.hardware import get_model_launch_overrides, resolve_model_hf_repo

        recipe = dict(get_model_launch_overrides(model).get("speculative_config") or {})
        if not draft_model:
            # No override: the recipe's config exactly, whatever the depth argument's default.
            return recipe or None
        # Carry the recipe's settings over only when it too uses an external drafter. A
        # self-speculation recipe (MTP heads inside the checkpoint, no "model") has a method that
        # means nothing for a separate draft model, so that one is replaced, not layered onto.
        speculative = recipe if recipe.get("model") else {}
        speculative["model"] = resolve_model_hf_repo(draft_model)
        if num_speculative_tokens is not None:
            speculative["num_speculative_tokens"] = num_speculative_tokens
        return speculative

    @classmethod
    def _compile_cache_signature(
        cls, model: str, draft_model: Optional[str] = None, num_speculative_tokens: Optional[int] = None
    ) -> str:
        """
        Builds the identity of the compiled graph for a model, for the factors vLLM does not hash.

        vLLM names its compile cache directory after a hash of the engine config, the traced source
        files and the compiler version, so almost any change already lands in a fresh directory.
        `SpeculativeConfig.compute_hash()` is the gap: it contributes only whether the method needs
        auxiliary hidden states and which layers they come from, so retuning `num_speculative_tokens`
        — the first tuning move the DFlash recipe suggests — leaves the key unchanged and the stale
        graph eligible for reuse. Upstream hit the same thing and clears on a `profile:nspec`
        signature; this is that signature.

        Args:
            model (str): Model alias, HF repo ID, or display name.

        Returns:
            str: Opaque signature string; a change means the cached graph must not be reused.
        """
        from dreamference.hardware import resolve_model_hf_repo

        # The launched config, not the recipe's: a --draft-model or depth override changes the graph.
        spec = cls.resolve_speculative_config(model, draft_model, num_speculative_tokens) or {}
        return "|".join(
            str(part)
            for part in (
                resolve_model_hf_repo(model),
                spec.get("method", "none"),
                spec.get("num_speculative_tokens", 0),
            )
        )

    @classmethod
    def _reset_stale_compile_cache(
        cls,
        model: str,
        docker_image: str = DEFAULT_VLLM_IMAGE,
        draft_model: Optional[str] = None,
        num_speculative_tokens: Optional[int] = None,
    ) -> bool:
        """
        Drops the persisted torch.compile cache when the graph it holds no longer matches the recipe.

        Removal runs inside the vLLM image rather than on the host: the cache is written by a
        container running as root into a directory the host user only owns the top of, so a
        host-side rmtree fails on the first root-owned subdirectory. Failure here is not fatal —
        the worst case is a recompile or a stale hit, neither of which justifies blocking a launch —
        so problems are reported and swallowed.

        Args:
            model (str): Model whose recipe defines the expected signature.
            docker_image (str): Image used to perform the removal (any image with the mount works).

        Returns:
            bool: True if a stale cache was found and cleared.
        """
        signature = cls._compile_cache_signature(model, draft_model, num_speculative_tokens)
        sig_file = VLLM_CACHE_HOME / ".compile_signature"

        try:
            previous = sig_file.read_text().strip() if sig_file.exists() else ""
        except OSError:
            previous = ""

        if previous == signature:
            return False

        cleared = False
        if previous and (VLLM_CACHE_HOME / "torch_compile_cache").exists():
            print(
                f"🧹 Speculative depth or target changed since the last launch "
                f"({previous} -> {signature}); dropping the torch.compile cache, because vLLM's own "
                f"cache key does not cover num_speculative_tokens."
            )
            try:
                subprocess.run(
                    [
                        "docker", "run", "--rm",
                        "-v", f"{os.path.expanduser('~/.cache/dreamference')}:/root/.cache/dreamference",
                        "--entrypoint", "rm", docker_image,
                        "-rf", f"{CONTAINER_VLLM_CACHE_ROOT}/torch_compile_cache",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                cleared = True
            except (subprocess.SubprocessError, OSError) as exc:
                print(
                    f"⚠️  Could not clear the compile cache ({exc}). If this launch behaves oddly "
                    f"after a speculative-depth change, remove {VLLM_CACHE_HOME}/torch_compile_cache "
                    f"by hand."
                )

        try:
            VLLM_CACHE_HOME.mkdir(parents=True, exist_ok=True)
            sig_file.write_text(signature)
        except OSError:
            pass

        return cleared

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
        max_num_batched_tokens: Optional[int] = None,
        guided_decoding_backend: Optional[str] = None,
        use_tensorizer: Optional[bool] = None,
        background: bool = True,
        # None so a model's recipe may pin its own image; resolved once below and then used
        # for the pull, the compile-cache reset and the launch alike.
        docker_image: Optional[str] = None,
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
            docker_image (Optional[str]): Docker image to launch vLLM in. None falls back to the
                model's `docker_image` recipe entry, then to the pinned DEFAULT_VLLM_IMAGE.

        Returns:
            Optional[subprocess.Popen]: Popen object if background=True, else None.
        """
        if not self.is_docker_available():
            raise RuntimeError(
                "Docker is required to run vLLM but is not available. "
                "Please ensure Docker is installed and the daemon is running (`docker ps` must succeed)."
            )

        # Resolved here rather than left to build_launch_command, because everything below this
        # line — the pull, the compile-cache signature, the tensorizer probe — has to be about the
        # same image the server will actually run.
        from dreamference.hardware import get_model_launch_overrides as _recipe_for_image
        if docker_image is None:
            docker_image = _recipe_for_image(model).get("docker_image", DEFAULT_VLLM_IMAGE)

        if not self.ensure_docker_image(docker_image):
            raise RuntimeError(
                f"Docker image '{docker_image}' is missing locally and could not be obtained. "
                "A registry-qualified image is pulled; the project's own image is built from the "
                "Dockerfile. Check the Docker daemon and network."
            )

        # GB10 hardware check
        from dreamference.hardware.hardware_manager import HardwareManager
        hw = HardwareManager.detect_gb10_hardware()
        if not hw.is_gb10:
            raise RuntimeError(
                "mling-admin server start requires an NVIDIA GB10 machine (DGX Spark or one of its OEM "
                "siblings): the model recipes and host-safety checks are written for its unified memory."
            )

        # The code index's runs must not share the machine with a model load: a frozen or running
        # indexer holds memory the pre-flight below would count as free.
        self._stop_index_scopes()
        self.check_host_safety()

        # Check memory availability.
        #
        # The budget that matters is the weight footprint, not a fraction of installed memory:
        # gpu_memory_utilization sizes the KV/activation arena vLLM carves out, and scaling total
        # memory by it says nothing about whether the checkpoint itself fits. A 78 GB checkpoint
        # under a 0.3 recipe used to "require" ~38 GB and sail through this gate.
        from dreamference.hardware import get_model_launch_overrides, get_speculative_draft_repo
        overrides = get_model_launch_overrides(model)
        # Same fallback the launch builder uses, and it has to stay that way. This read used to
        # default to 0.9 against build_launch_command's 0.50, so every model without a recipe — most
        # of the registry — was gated against a 109 GB arena it would never be given, aborted on a
        # 61 GB launch that fits comfortably, and was told to lower a gpu_memory_utilization it does
        # not have. A gate that scores a different configuration than the one that runs is not a
        # gate.
        gpu_memory_utilization = overrides.get(
            "gpu_memory_utilization", DEFAULT_GPU_MEMORY_UTILIZATION
        )

        actual_total_gb = hw.total_unified_memory_gb
        actual_free_gb = hw.available_memory_gb

        # A drafter can arrive two ways and both are resident at once with the target. The caller
        # can name one, or the model's own recipe can bring one (DFlash). The recipe's was
        # invisible here until 2026-08-15, which under-counted the footprint by the drafter's size
        # and — worse — left it out of the pre-download below, so vLLM would fetch it from inside
        # the container during the load, the one phase where this host cannot afford surprises.
        recipe_draft_model = get_speculative_draft_repo(model)
        draft_models = [m for m in (draft_model, recipe_draft_model) if m]

        weights_gb = self._estimate_model_weights_gb(model)
        for extra_model in draft_models:
            weights_gb += self._estimate_model_weights_gb(extra_model)

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

        # And the arena has to be free right now, not merely installed — with room to spare for
        # the load itself.
        #
        # This gate used to compare the arena against MemAvailable alone, and that is what let the
        # 2026-08-14 14:11 freeze through: a 97.30 GB arena against 105.83 GB available passed
        # with 8.5 GB to spare, and the machine still needed the power button. The arena is the
        # steady state, not the peak. Streaming a 78 GB checkpoint through fastsafetensors churns
        # page cache, the loader stages tensors before they are pinned, and the desktop keeps
        # growing throughout — all of it out of whatever is left over. Budget for the peak.
        transient_gb = max(MIN_LOAD_TRANSIENT_GB, weights_gb * LOAD_TRANSIENT_FRACTION)
        required_gb = arena_gb + transient_gb
        if actual_free_gb < required_gb:
            # The arena is a fraction of total memory, so the fix is expressed as the fraction
            # that would fit — otherwise the operator is left to work backwards from four numbers.
            max_utilization = max(0.0, (actual_free_gb - transient_gb) / actual_total_gb)
            _abort(
                "Not enough memory to start vLLM.",
                f"   Arena:       {arena_gb:.2f} GB "
                f"({gpu_memory_utilization:.2f} x {actual_total_gb:.2f} GB total)\n"
                f"   Load peak:   {transient_gb:.2f} GB (page cache and staging for "
                f"{weights_gb:.2f} GB of weights)\n"
                f"   Required:    {required_gb:.2f} GB\n"
                f"   Available:   {actual_free_gb:.2f} GB\n"
                f"   Missing:     {required_gb - actual_free_gb:.2f} GB\n\n"
                f"The arena alone would fit; the load peak is what does not. Either free memory on\n"
                f"the host (closing the desktop session recovers the most), or lower this model's\n"
                f"gpu_memory_utilization to at most {max_utilization:.2f}.",
            )

        # Step 1: Pre-download model weights into local cache and convert to tensorize format.
        # Resolve the tensorizer decision up front: some checkpoints opt out in their registry recipe,
        # and converting one anyway would burn time and disk on an artifact the launcher will not use.
        from dreamference.hardware import download_model, get_model_launch_overrides, resolve_model_hf_repo
        tensorize = use_tensorizer
        if tensorize is None:
            tensorize = get_model_launch_overrides(model).get("use_tensorizer", False)
        # A recipe may pin the commits it was measured on; the engine is then told the same
        # revision, so what is served is what was fetched.
        download_model(model, hf_token=hf_token, auto_tensorize=tensorize, revision=overrides.get("revision"))
        if draft_model:
            download_model(draft_model, hf_token=hf_token, auto_tensorize=tensorize)
        if recipe_draft_model:
            # Not tensorized, unlike a caller-named drafter: the format buys nothing on a 1-2 GiB
            # checkpoint, and this one is named by HF repo ID rather than by the registry alias
            # the tensorizer cache keys on.
            download_model(
                recipe_draft_model, hf_token=hf_token, auto_tensorize=False,
                revision=(overrides.get("speculative_config") or {}).get("revision"),
            )

        # The compile cache persists across container lifetimes now, which means it also outlives
        # the recipe that produced it. Reconcile the two before the graph is loaded rather than
        # after.
        # vLLM's cache only: SGLang keeps its own under a separate directory, and running the
        # reset for an SGLang model would re-stamp the signature and cost vLLM its graph when
        # the default model comes back.
        # The recipe's chat-template patches, written before the launch names the file. A missing
        # anchor means the checkpoint's template is not the one the recipe was written for, and
        # serving it unpatched would bring back the refusals the patches exist to remove.
        patches = overrides.get("chat_template_patches")
        if patches and overrides.get("revision"):
            from dreamference.vllm_server.chat_template_patcher import ChatTemplatePatcher

            if ChatTemplatePatcher.prepare(resolve_model_hf_repo(model), overrides["revision"], patches) is None:
                print("❌ The recipe's chat template could not be prepared. Either it could not be written\n"
                      "   (reported above), or a patch no longer matches this checkpoint's template, which\n"
                      "   then differs from the one the registry entry was written for: update the patches in\n"
                      "   model_matrix_registry.py before serving it.")
                raise SystemExit(1)

        if not SGLangLaunchBuilder.is_sglang(overrides):
            self._reset_stale_compile_cache(
                model, docker_image=docker_image, draft_model=draft_model,
                num_speculative_tokens=num_speculative_tokens,
            )

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
        env["HF_HOME"] = str(ModelDownloader.get_hf_home())
        
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
            def _report(reason: str, pressure: float, window_s: float) -> None:
                print(
                    f"\n🛑 Aborted the load: memory stalled {pressure:.0f}% of the last "
                    f"{window_s:.0f}s ({reason}).\n"
                    f"   Every task on the host was blocked on memory — the state that precedes a\n"
                    f"   freeze. '{container_name}' has been killed to give the memory back.\n"
                    f"   Lower this model's gpu_memory_utilization before retrying.\n"
                )

            self.watchdog = MemoryPressureWatchdog(container_name, on_trip=_report)
            if not self.watchdog.start():
                self.watchdog = None
                print("⚠️  Kernel does not expose /proc/pressure/memory; load is unguarded.")

        if background:
            if cmd and cmd[0] == "docker":
                cmd.insert(2, "-d")
                subprocess.run(cmd, check=True, env=env)
                self.process = subprocess.Popen(
                    ["docker", "logs", "-f", container_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )
            else:
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
            Dict[str, Any]: Dictionary with 'host', 'healthy', 'models', 'pid', 'loading_status'.
        """
        healthy = self.check_health()
        models = self.get_models() if healthy else []
        
        loading_status = None
        if not healthy:
            import subprocess
            port = self.host.split(":")[-1] if ":" in self.host else "8000"
            container_name = f"dreamference-vllm-{port}"
            try:
                res = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container_name], capture_output=True, text=True)
                if res.stdout.strip() == "true":
                    log_res = subprocess.run(["docker", "logs", "--tail", "100", container_name], capture_output=True, text=True)
                    logs = log_res.stdout + "\n" + log_res.stderr
                    for line in reversed(logs.splitlines()):
                        line_lower = line.lower()
                        if "loading safetensors checkpoint shards" in line_lower:
                            loading_status = line.strip()
                            break
                        elif "warm up the model" in line_lower or "warming up" in line_lower:
                            loading_status = "Warming up model (CUDA graphs)..."
                            break
                        elif "loading model" in line_lower or "load model" in line_lower:
                            loading_status = "Loading model weights into GPU memory..."
                            break
                        elif "downloading" in line_lower or "hf_hub_download" in line_lower:
                            loading_status = "Downloading model components..."
                            break
                    if not loading_status:
                        loading_status = "Initializing container..."
            except Exception:
                pass

        status = VLLMServerStatus(
            host=self.host,
            healthy=healthy,
            models=models,
            pid=self.process.pid if self.process else None,
            loading_status=loading_status
        )
        return {
            "host": status.host,
            "healthy": status.healthy,
            "models": status.models,
            "pid": status.pid,
            "loading_status": status.loading_status
        }
