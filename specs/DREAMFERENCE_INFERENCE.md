# Dreamference GB10 Inference Stack

> **Version:** 1.2.0
> **Subject:** vLLM Launch Engine, Auto-Configuration, & Performance Optimization
> **Checked against the code:** 2026-09-29 (`dreamference/vllm_server/vllm_server_manager.py`)

---

## Table of Contents

- [1. GB10 Inference Stack Overview](#1-gb10-inference-stack-overview)
- [2. Weight Pre-Download & Management](#2-weight-pre-download--management)
- [3. Speculative Decoding](#3-speculative-decoding)
- [4. Launch Flags](#4-launch-flags)
- [5. Per-Model Launch Recipes](#5-per-model-launch-recipes)
- [6. Base Configuration & Module Defaults](#6-base-configuration--module-defaults)
- [7. Host Safety](#7-host-safety)
- [8. Readiness Polling & Live Streaming](#8-readiness-polling--live-streaming)

---

## 1. GB10 Inference Stack Overview

The server is started only by `puffin-admin server start`. Agents never start it: they wait for it (`DREAMFERENCE_AGENTS.md` §8, or `puffin`'s launcher).

```
puffin-admin server start [--model <alias>]
    ↓
Resolve alias → HF repo, recipe (launch_overrides), Docker image
    ↓
Host-safety pre-flight (check_host_safety): swap, sysctl, earlyoom / systemd-oomd, memory arena
    ↓
Pre-download weights: main model, --draft-model, and the recipe's own drafter (DFlash)
    ↓
Reset a stale torch.compile cache if the speculative signature changed
    ↓
Start the diffusion sidecar (dreamference-diffusion-8001) first, unless --no-diffusion
    ↓
Build the docker run … vllm serve command (explicit args > recipe > module defaults)
    ↓
Run the container under the PSI MemoryPressureWatchdog
    ↓
Stream logs and Docker memory; poll /v1/models until healthy
    ↓
NVFP4 canary (nvfp4 aliases only); report diffusion sidecar state
    ↓
Exit; the containers keep running (--restart unless-stopped)
```

---

## 2. Weight Pre-Download & Management

- `start_server()` calls `download_model()` for the main model, for `--draft-model` if given, and for the recipe's drafter (`get_speculative_draft_repo`) before the container starts. vLLM therefore never has to fetch weights during the load, which is the one phase this host cannot afford surprises in.
- `ModelDownloader` tries `huggingface_hub.snapshot_download` first, then the `huggingface-cli` binary. If both fail, the fetch is left to vLLM.
- The cache is `~/.cache/huggingface/hub/` (`$HF_HOME/hub` if set), mounted into the container at `/root/.cache/huggingface`.

### 2.1. HF Token Handling

The token is the first of:
1. CLI `--hf-token`;
2. `HF_TOKEN`, then `DREAMFERENCE_HF_TOKEN`;
3. the `hf_token` config key.

It is passed to the container as `-e HF_TOKEN=…`. When none is set, `huggingface_hub` falls back to its own stored login (`~/.cache/huggingface/token`) on the host side.

---

## 3. Speculative Decoding

### 3.1. Two ways to get a drafter

- **Recipe (normal case):** the model's `launch_overrides["speculative_config"]` is serialised to `--speculative-config`. The two DFlash entries use `{"method": "dflash", "model": "z-lab/Qwen3.5-122B-A10B-DFlash", "num_speculative_tokens": 12, "attention_backend": "FLASH_ATTN"}`. The drafter is a separate checkpoint, pre-downloaded and counted in the memory budget. MTP recipes (heads inside the checkpoint) use the same key with `"method": "mtp"`.
- **Explicit `--draft-model`:** also emitted as `--speculative-config` JSON, built by `resolve_speculative_config()`; vLLM 0.2x has no `--speculative-model` or `--num-speculative-tokens` flag. `"model"` is the draft's resolved HF repo.

**Precedence:** with both present, the explicit draft is **layered onto** the recipe when the recipe also uses an external drafter (it has a `"model"`): `"model"` is replaced, and the recipe's method and drafter attention backend are kept. A self-speculation recipe (MTP, no `"model"`) is replaced outright, since its method means nothing for a separate checkpoint. There is no fail-fast validation of the combination. Until 2026-09-29 the explicit path emitted the removed `--speculative-model` flags and failed at argument parsing.

**Compatibility:** a drafter must share the target's tokenizer. The DFlash drafter is built for Qwen 3.5 122B-A10B.

### 3.2. Speculative token count

- **With a recipe:** its `speculative_config` carries the count. The DFlash entries use 12.
- **With an explicit draft:** `puffin-admin server start` passes the config's resolved `num_speculative_tokens` — `--num-speculative-tokens` > `DREAMFERENCE_SPECULATIVE_TOKENS` > file > `DEFAULT_SPECULATIVE_TOKENS = 8` — so a draft without the flag gets 8, not the recipe's 12. `start_server()` / `build_launch_command()` default to 5 only when called directly from Python.
- **Without a draft**, the depth argument is ignored and the recipe's config is passed exactly.

Until 2026-09-29 the raw flag was passed, and an omitted `--num-speculative-tokens` reached the command line as `None`.

### 3.3. torch.compile cache

vLLM keys its compile cache on the engine config, but `SpeculativeConfig.compute_hash()` does not include `num_speculative_tokens`. `_reset_stale_compile_cache()` keeps its own `model|method|n` signature and clears the cache when it changes. It clears it *inside* the image, because the cache is root-owned. The cache lives at `VLLM_CACHE_ROOT=/root/.cache/dreamference/vllm`, which is persistent: a cold compile takes 8–12 minutes.

---

## 4. Launch Flags

| Flag | Default | Source | Effect |
| :--- | :--- | :--- | :--- |
| `--enable-prefix-caching` | on | Config; forced **off** when the recipe says `enable_prefix_caching: false` | KV prefix reuse |
| `--enable-chunked-prefill` | on | Config | Chunked prefill |
| `--max-num-batched-tokens` | `8192` | Recipe (default model: `9048`) | Prefill chunk size |
| `--kv-cache-dtype` | `auto` | Recipe; config override | KV precision |
| `--attention-backend` | omitted when `auto` | Recipe (default model: `flash_attn`) | Attention kernels |
| `--moe-backend` | unset | Recipe | MoE kernels (must be SM121-safe) |
| `--tool-call-parser` | resolved | Explicit > recipe > name guess (`mistral` / `hermes`) | Tool-call format (`qwen3_xml` for all Qwen 3.x entries) |
| `--reasoning-parser` | unset | Recipe (`qwen3`) | Reasoning channel |
| `--structured-outputs-config.backend` | `xgrammar` | `guided_decoding_backend` | Structured outputs. On images older than vLLM v0.12 it is spelled `--guided-decoding-backend`, chosen by probing the image |
| `--override-generation-config` | `{"temperature": 0.0, "top_p": 1.0, "top_k": 0}` | `DEFAULT_GENERATION_OVERRIDES`; a recipe may override or disable it (`generation_overrides`) | Deterministic sampling unless the client asks otherwise |
| `--speculative-config` | unset | Recipe | See §3 |
| `--quantization` | unset | Explicit; **fp8** for `70b`/`72b` names whose checkpoint does not declare its own quantization | Self-declaring formats (NVFP4, AutoRound, AWQ, GPTQ, …) are left to vLLM's detection |
| `--load-format` | omitted | Recipe `load_format`; `tensorizer` when tensorized weights are used | `DEFAULT_LOAD_FORMAT = "auto"`, deliberately **not** `fastsafetensors` (see §6) |

---

## 5. Per-Model Launch Recipes

`ModelSpec.launch_overrides` carries each model's vLLM recipe as registry data. `build_launch_command` resolves every setting in this order:

```
explicit caller argument  >  model launch_overrides  >  module default
```

`None` counts as unset, and so does `attention_backend='auto'`. Per-model tuning belongs in the registry entry, not in the launch builder, and the tests assert this layering.

### 5.1. Recipe Keys

The keys mirror `build_launch_command`'s parameters: `max_model_len`, `gpu_memory_utilization`, `kv_cache_dtype`, `attention_backend`, `moe_backend`, `tool_call_parser`, `reasoning_parser`, `max_num_batched_tokens`, `guided_decoding_backend`, `use_tensorizer`, `load_format`. On top of those:

| Key | Effect |
| :-- | :----- |
| `docker_image` | Pins this model's vLLM image, overriding `DEFAULT_VLLM_IMAGE` (the engine is part of the recipe) |
| `env` | `Dict[str, str]` exported with `docker run -e K=V`. Some kernel selection is read from the environment, not flags |
| `speculative_config` | Serialised to `--speculative-config` |
| `enable_prefix_caching` | `false` forces prefix caching off, whatever the config says (hybrid-GDN checkpoints cannot honour it) |
| `generation_overrides` | Replaces, or with `None` disables, the default `--override-generation-config` |
| `extra_args` | Verbatim flags appended to the command |

### 5.2. Default Model Recipe (`qwen3.5-122b-a10b-hybrid-dflash`)

| Setting | Value |
| :------ | :---- |
| `docker_image` | `dreamference-vllm-dflash:0.23.0-aeon-dense5` |
| `max_model_len` | `32768` |
| `gpu_memory_utilization` | `0.7` |
| `kv_cache_dtype` | `auto` |
| `attention_backend` | `flash_attn` |
| `max_num_batched_tokens` | `9048` |
| `tool_call_parser` / `reasoning_parser` | `qwen3_xml` / `qwen3` |
| `speculative_config` | DFlash, 12 tokens (§3.1) |
| `enable_prefix_caching` | `true` (explicit in the recipe) |
| `env` | `VLLM_MARLIN_USE_ATOMIC_ADD=1`, `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0` |
| `extra_args` | `--max-num-seqs 8 --tensor-parallel-size 1 --dtype auto --default-chat-template-kwargs {"enable_thinking": false}` |

The resulting command is listed flag by flag in `DREAMFERENCE_CODEBASE.md` §5. The NVFP4 entries use the FlashInfer b12x kernels, selected through `env` and `moe_backend`, because the CUTLASS FP4 path corrupts output on SM121 (`DREAMFERENCE_MODELS.md` §2.1).

---

## 6. Base Configuration & Module Defaults

These are always emitted:

```
--host 0.0.0.0 --port <port> --max-model-len <n> --gpu-memory-utilization <f>
--trust-remote-code --async-scheduling
--enable-log-requests --enable-log-outputs --max-log-len 2048
--enable-auto-tool-choice            (unless disabled)
```

**Module defaults**, used only when neither the caller nor the recipe sets a value:
- `DEFAULT_MAX_MODEL_LEN = 16384`;
- `DEFAULT_GPU_MEMORY_UTILIZATION = 0.50`;
- `DEFAULT_KV_CACHE_DTYPE = "auto"`;
- `DEFAULT_LOAD_FORMAT = "auto"`.

Every current matrix entry sets its own context length and utilisation.

**No `fastsafetensors`.** It used to be hardcoded. Without GPUDirect Storage, which GB10 lacks, it double-resides the checkpoint during load, and that peak is what froze this host. vLLM's own loader is used unless a recipe asks otherwise.

**Container:**
- `--ipc=host --network host --restart unless-stopped --gpus all`;
- `--cpus` and a memory limit derived from `gpu_memory_utilization` plus headroom, below total memory minus a 12 GB host reserve (`HOST_MEMORY_RESERVE_GB`);
- `--memory-swap` equal to the memory limit, and `--oom-score-adj=800`;
- env `VLLM_CACHE_ROOT`, `CUTE_DSL_ARCH=sm_121a`, `VLLM_LOGGING_LEVEL=DEBUG`, and API request/response debug logging.

---

## 7. Host Safety

On GB10, host RAM and GPU memory are the same memory. A load that exhausts it can freeze the whole machine rather than OOM the container. There are two layers, both of which must be kept when touching `start_server()`:

- **Before the load:** `check_host_safety()` inspects swap, `sysctl` values and whether `earlyoom` or `systemd-oomd` is present and configured. `start_server()` then checks that the weights plus drafters fit the arena (`total × gpu_memory_utilization`), that at least `HOST_MEMORY_RESERVE_GB` (12 GB) stays outside it, and that the arena plus transient load overhead fits in currently free memory. Either one aborts with an explanation rather than risking a lockup.
- **During the load, `MemoryPressureWatchdog` (`psi_watchdog.py`):** it samples `/proc/pressure/memory` once a second and resolves the container's cgroup. It trips when `full avg10` ≥ 60% holds for 5 s (`PSI_FULL_LIMIT_PCT`, `PSI_TRIP_DURATION_S`), or at once when `full avg60` ≥ 25% (`PSI_SUSTAINED_AVG60_PCT`). The kill paths, in order:
  1. direct `SIGKILL` to the cgroup's PIDs, if permitted;
  2. a kill request over dockerd's unix socket;
  3. the `docker` CLI.

The diffusion sidecar has no watchdog: its fixed `--memory=8g` limit (swap equal) contains the worst case. It starts before vLLM, so that vLLM's free-memory pre-flight sees it.

---

## 8. Readiness Polling & Live Streaming

- `ModelLoadingMonitor` pipes the container's logs to stdout and prints `[HH:MM:SS] 📊 Reserved memory (Docker): …` every 10 seconds. It tracks loading stages and polls `/v1/models` until healthy.
- `VLLMStartupMonitor` adds memory-growth tracking and stall detection with progress percentages.
- `server start` exits once healthy, and the server keeps running.
- Ctrl+C during the wait prints `⏹️  Shutting down server...` and terminates the monitored `docker run` process: `terminate`, then `kill` after 5 s. Follow it with `puffin-admin server stop` to be certain the container is gone.

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md):** models and the default model
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md):** images and tensorization
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agents and how they wait for the server
