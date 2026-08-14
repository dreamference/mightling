# Dreamference GB10 Inference Stack

> **Version:** 1.2.0
> **Subject:** vLLM Launch Engine, Auto-Configuration, & Performance Optimization

---

## Table of Contents

- [1. GB10 Inference Stack Overview](#1-gb10-inference-stack-overview)
- [2. Weight Pre-Download & Management](#2-weight-pre-download--management)
- [3. Speculative Decoding](#3-speculative-decoding)
- [4. Blackwell Performance Flags](#4-blackwell-performance-flags)
- [5. Per-Model Launch Recipes](#5-per-model-launch-recipes)
- [6. Base Configuration & Flags](#6-base-configuration--flags)
- [7. Readiness Polling & Live Streaming](#7-readiness-polling--live-streaming)
- [8. Instant Signal Handling](#8-instant-signal-handling)

---

## 1. GB10 Inference Stack Overview

The vLLM launch engine is the heart of Dreamference's inference subsystem. Every model load passes through a carefully orchestrated sequence:

```
Request: dream chat --model <alias>
    ↓
Resolve model alias → HF repo
    ↓
Resolve vLLM image & recipe
    ↓
Check host safety (swap, PSI, oomd)
    ↓
Pre-download model weights (HF cache)
    ↓
Build launch command (flags + recipes + overrides)
    ↓
Docker: pull image (if missing) + run container
    ↓
Stream logs + poll /v1/models until healthy
    ↓
Serve (model remains loaded in background)
```

---

## 2. Weight Pre-Download & Management

- `start_server()` always calls `download_model()` for primary (and draft if set) before spawning the process.
- Download uses `huggingface_hub.snapshot_download` (preferred), then `huggingface-cli download`, else defers fetch to vLLM init.
- Weights are cached in `~/.cache/huggingface/hub/` (or `$HF_HOME/hub` if set).

### 2.1. HF Token Handling

Set via:
1. CLI `--hf-token TOKEN`
2. Environment `HF_TOKEN` or `DREAMFERENCE_HF_TOKEN`
3. HuggingFace CLI credentials (`~/.cache/huggingface/token`)

Exported to the vLLM container as `-e HF_TOKEN=...` for gated model access.

---

## 3. Speculative Decoding

### 3.1. Draft Model Compatibility

- Speculative decoding requires the draft and target models to share a vocabulary (tokenizer compatibility).
  - ✅ Valid: `qwen2.5-coder-1.5b` as draft for `qwen2.5-coder-32b`
  - ❌ Invalid: `qwen2.5-coder-1.5b` as draft for `qwen3.6-35b-a3b-nvfp4` (different tokenizers)
  - ❌ Invalid: `qwen2.5-coder-1.5b` as draft for `llama-3.3-70b` (different architectures)

### 3.2. In-Checkpoint vs. External Drafts

- Providing an external `--draft-model` when the primary model's recipe already declares in-checkpoint speculation (MTP/Eagle) is a **configuration error** and will fail fast.
- Example: `qwen3.6-35b-a3b-nvfp4` has MTP speculation built-in; do not also pass `--draft-model`.

### 3.3. Configuration

- Configured via `--speculative-config` (consolidated from the older `--speculative-model` flag).
- For in-checkpoint speculation, the `launch_overrides` dict carries `speculative_config: {...}` (e.g., `{"method": "mtp", "num_speculative_tokens": 3, "moe_backend": "triton"}`).

---

## 4. Blackwell Performance Flags

Flags that are actually applied to the vLLM launch command (conditionally or always):

| Flag                        | Default | Applied When | Effect |
| :-------------------------- | :------ | :----------- | :----- |
| `--enable-prefix-caching`   | ✅ On   | Always       | Automatic KV cache prefix reuse across multi-turn sessions |
| `--enable-chunked-prefill`  | ✅ On   | Always       | Prefill chunking for responsive TTFT during long prompts |
| `--kv-cache-dtype`          | `auto`  | Model recipe | Quantized KV cache (default auto unless specified) |
| `--attention-backend`       | `auto`  | Model recipe (if ≠ `auto`) | Blackwell-optimized attention (e.g., `flashinfer`) |
| `--moe-backend`             | unset   | Model recipe (if set) | Mixture-of-Experts backend (e.g., `triton`, `flashinfer-b12x`) |
| `--reasoning-parser`        | unset   | Model recipe (if set) | Parser for reasoning tags (e.g., `qwen3`, `deepseek_r1`) |
| `--speculative-config`      | unset   | Model recipe or CLI | JSON for external drafts or in-checkpoint MTP |
| `--quantization`            | unset   | Model recipe or auto-detected | Model weight quantization (fp8, int8, int4, awq, gptq) |
| `--tool-call-parser`        | `hermes` (fallback) | Model recipe or explicit | Tool call format parser (e.g., `qwen3_xml`, `mistral`) |

### 4.1. Quantization Auto-Detection

- For model names containing `70b` or `72b` when quantization is unset **and** the checkpoint does not declare its own format, vLLM auto-selects `--quantization fp8`.
- Self-declaring formats (NVFP4, MXFP4, AWQ, GPTQ) are detected by vLLM from `config.json`; Dreamference suppresses the flag rather than overriding that detection with a guess derived from the model name.

---

## 5. Per-Model Launch Recipes

Not every model runs correctly on one global flag set. `ModelSpec.launch_overrides` (`dreamference/hardware/model_spec.py`) carries a per-model vLLM recipe as registry data, and `build_launch_command` merges it with this precedence:

```
explicit caller argument  >  model launch_overrides  >  module default
```

An argument left as `None` is treated as unset; `attention_backend='auto'` also counts as unset, since `auto` delegates the choice by definition. Models with no recipe resolve to exactly the previous defaults, so the mechanism is inert unless a model opts in.

**Example**: `deepseek-r1-distill-32b` and `deepseek-r1-distill-70b` opt-in to specify `reasoning_parser: "deepseek_r1"`.

### 5.1. Recipe Structure

Recognised keys mirror `build_launch_command` parameters, plus:

| Key | Effect |
| :-- | :----- |
| `env` | `Dict[str, str]` exported into the vLLM container as `docker run -e K=V`. Required because some kernel-backend selection (notably NVFP4 MoE) is read from the process environment, not CLI flags. |
| `speculative_config` | Dict serialised to `--speculative-config`. For in-checkpoint speculation, which has no separate draft model. |
| `extra_args` | Verbatim flags appended to the launch command, for recipe settings without a first-class parameter. |

### 5.2. Default Model Recipe

The default model's recipe is `ModelMatrixRegistry.MATRIX['qwen3.6-35b-a3b-nvfp4'].launch_overrides` and follows NVIDIA's published DGX Spark recipe:

| Setting | Value | Purpose |
| :------ | :---- | :------ |
| `max_model_len` | `131072` (131k tokens) | Extended context window |
| `gpu_memory_utilization` | `0.3` | 30% reservation for GB10 unified memory |
| `kv_cache_dtype` | `fp8` | 8-bit KV cache for efficiency |
| `attention_backend` | `flashinfer` | Blackwell-optimized attention |
| `tool_call_parser` | `qwen3_xml` | Qwen 3.6 XML tool syntax |
| `reasoning_parser` | `qwen3` | Qwen 3.6 reasoning tag parser |
| `max_num_batched_tokens` | `8192` | Chunked prefill iteration size |
| `speculative_config` | `{"method": "mtp", "num_speculative_tokens": 3, "moe_backend": "triton"}` | In-checkpoint 3-token speculation |
| `max_num_seqs` | `4` | Caps concurrent sequences |
| `use_tensorizer` | `False` | Disabled by default |

**Required Environment Variables**:
- `VLLM_NVFP4_GEMM_BACKEND=flashinfer-b12x`
- `VLLM_MARLIN_USE_ATOMIC_ADD=1`
- `VLLM_DISABLED_KERNELS=MarlinNvFp4LinearKernel`

---

## 6. Base Configuration & Flags

### 6.1. Global Base Flags

Applied to every vLLM launch:

```
--host 0.0.0.0
--port <port>
--max-model-len 16384         (baseline; override per recipe)
--gpu-memory-utilization 0.50 (conservative default; override per recipe)
--trust-remote-code
--async-scheduling
--load-format fastsafetensors
```

The low `0.50` default prevents OOMs on the 128 GB unified memory SoC. Models with stricter memory budgets override via `launch_overrides["gpu_memory_utilization"]`.

### 6.2. Default Speculative Tokens

- Default `num_speculative_tokens`: `8` (used only if speculative decoding is enabled for the model)

---

## 7. Readiness Polling & Live Streaming

### 7.1. Monitoring Components

- `dream server start` (and agent runners) use `ModelLoadingMonitor` + `VLLMServerManager`.
- The monitor thread constantly pipes raw vLLM container logs to stdout.
- Prints Docker reserved memory usage every 10 seconds (`[HH:MM:SS] 📊 Reserved memory (Docker): …`).
- Tracks loading stages from logs and polls `/v1/models` until healthy.
- `VLLMStartupMonitor` provides additional memory-growth tracking and stall detection with explicit progress percentages (`~X% (Y/Z GB since start)`).

### 7.2. Server Startup Exit Behavior

- `server start` exits once the health check passes (server keeps running in background).

---

## 8. Instant Signal Handling

- Poll loop sleeps in short intervals so `Ctrl+C` is handled promptly.
- Graceful shutdown: cancel pending health checks, drain remaining logs, return failure status.

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md)** — Supported models & default model details
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md)** — vLLM image management & tensorization
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md)** — Session startup & agent integration
