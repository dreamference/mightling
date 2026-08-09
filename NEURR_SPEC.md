# Neurr Technical Specification

> - **Version:** 1.2.0 (`neurr.__version__`)
> - **Status:** Implemented / Production-Ready
> - **Target Hardware:** Exclusive to **NVIDIA GB10** (Blackwell architecture with 128 GB Unified Memory)
> - **Deployment Model:** Single-Node Standalone NVIDIA GB10 System
> - **License:** Open Source (Apache 2.0)

---

## Table of Contents

- [1. Executive Summary & Vision](#1-executive-summary--vision)
- [2. Parity & Key Differentiators](#2-parity--key-differentiators)
- [3. Supported NVIDIA GB10 Model Matrix](#3-supported-nvidia-gb10-model-matrix)
- [4. Hardware Integration & Optimization](#4-hardware-integration--optimization)
  - [4.1. NVIDIA GB10 Hardware Specification](#41-nvidia-gb10-hardware-specification)
  - [4.2. GB10 Inference Stack & Auto-Launch Engine](#42-gb10-inference-stack--auto-launch-engine)
    - [4.2.1. Weight Pre-Download](#421-weight-pre-download)
    - [4.2.2. Dual-Model Speculative Decoding](#422-dual-model-speculative-decoding)
    - [4.2.3. Blackwell Performance Flags Actually Applied](#423-blackwell-performance-flags-actually-applied)
    - [4.2.4. Configuration & Base Flags](#424-configuration--base-flags)
    - [4.2.5. Readiness Polling & Live Streaming](#425-readiness-polling--live-streaming)
    - [4.2.6. Instant Signal Handling](#426-instant-signal-handling)
    - [4.2.7. Per-Model Launch Recipes](#427-per-model-launch-recipes)
  - [4.3. Agent Runtimes (Goose, Cline, Aider, Continue, OpenHands)](#43-agent-runtimes-goose-cline-aider-continue-openhands)
    - [4.3.1. Goose (default, `agent_runner=goose`)](#431-goose-default-agent_runnergoose)
    - [4.3.2. Cline (`--agent cline`)](#432-cline-agent-cline)
    - [4.3.3. Aider (`--agent aider`)](#433-aider-agent-aider)
    - [4.3.4. Continue (`--agent continue`)](#434-continue-agent-continue)
    - [4.3.5. OpenHands (`--agent openhands`)](#435-openhands-agent-openhands)
  - [4.4. Codebase Context Engine (AST + SQLite / FTS5)](#44-codebase-context-engine-ast--sqlite--fts5)
  - [4.5. Session Startup Process](#45-session-startup-process)
    - [4.5.1. Failure Modes](#451-failure-modes)
  - [4.6. Goose-vLLM Integration](#46-goose-vllm-integration)
    - [4.6.1. Endpoint Wiring](#461-endpoint-wiring)
    - [4.6.2. Real Shell Execution](#462-real-shell-execution)
    - [4.6.3. MCP Companion](#463-mcp-companion)
    - [4.6.4. Deep-Merge Safety](#464-deep-merge-safety)
    - [4.6.5. vLLM Side](#465-vllm-side)
  - [4.7. Code Indexing Pipeline](#47-code-indexing-pipeline)
    - [4.7.1. Entry Points](#471-entry-points)
    - [4.7.2. Parallel Execution](#472-parallel-execution)
    - [4.7.3. Ingestion Steps](#473-ingestion-steps)
    - [4.7.4. Hybrid Search](#474-hybrid-search)
    - [4.7.5. Cache Behavior](#475-cache-behavior)
    - [4.7.6. Performance Notes](#476-performance-notes)
  - [4.8. Tests Architecture](#48-tests-architecture)
    - [4.8.1. Framework](#481-framework)
    - [4.8.2. Structure](#482-structure)
    - [4.8.3. Style](#483-style)
  - [4.9. Model Download, Hugging Face Caching & Tensorization](#49-model-download-hugging-face-caching--tensorization)
    - [4.9.1. Hugging Face Behavior](#491-hugging-face-behavior)
    - [4.9.2. Tensorizer Behavior](#492-tensorizer-behavior)
    - [4.9.3. Cache Management Commands](#493-cache-management-commands)
    - [4.9.4. Download CLI](#494-download-cli)
    - [4.9.5. Best-Effort Policy](#495-best-effort-policy)
  - [4.10. Docker vLLM Architecture](#410-docker-vllm-architecture)
    - [4.10.1. vLLM Runtime Images](#4101-vllm-runtime-images)
  - [4.11. Docker Agent Architecture](#411-docker-agent-architecture)
    - [4.11.1. OpenHands Agent Image](#4111-openhands-agent-image)
- [5. Client Interfaces & Developer Experience](#5-client-interfaces--developer-experience)
  - [5.1. `neurr` CLI Suite](#51-neurr-cli-suite)
    - [5.1.1. `neurr init`](#511-neurr-init)
    - [5.1.2. `neurr chat`](#512-neurr-chat)
    - [5.1.3. `neurr run`](#513-neurr-run)
    - [5.1.4. `neurr status`](#514-neurr-status)
    - [5.1.5. `neurr start_server`](#515-neurr-start_server)
    - [5.1.6. `neurr stop_server`](#516-neurr-stop_server)
    - [5.1.7. `neurr remove_server`](#517-neurr-remove_server)
    - [5.1.8. `neurr show_request_logs`](#518-neurr-show_request_logs)
    - [5.1.9. `neurr index`](#519-neurr-index)
    - [5.1.10. `neurr mcp`](#5110-neurr-mcp)
    - [5.1.11. `neurr endpoints`](#5111-neurr-endpoints)
    - [5.1.12. `neurr web`](#5112-neurr-web)
    - [5.1.13. `neurr download`](#5113-neurr-download)
    - [5.1.14. `neurr clear-cache`](#5114-neurr-clear-cache)
    - [5.1.15. `neurr clear-tensorize-cache`](#5115-neurr-clear-tensorize-cache)
  - [5.2. IDE Integration via Stdio MCP](#52-ide-integration-via-stdio-mcp)
  - [5.3. Web Canvas UI & Telemetry Pane](#53-web-canvas-ui--telemetry-pane)
- [6. System Requirements & Setup](#6-system-requirements--setup)
  - [6.1. Requirements](#requirements)
  - [6.2. Identifying Your Hardware Variant](#identifying-your-hardware-variant)
  - [6.3. Quickstart Installation](#quickstart-installation)
  - [6.4. Helper Scripts](#helper-scripts)
- [7. Roadmap & Implementation Verification](#7-roadmap--implementation-verification)
- [8. Codebase Architecture & Source Reference](#8-codebase-architecture--source-reference)
  - [8.1. neurr/hardware/](#81-neurrhardware)
  - [8.2. neurr/config/](#82-neurrconfig)
  - [8.3. neurr/runner/](#83-neurrrunner)
  - [8.4. neurr/vllm_server/](#84-neurrvllm_server)
  - [8.5. neurr/context_engine/](#85-neurrcontext_engine)
  - [8.6. neurr/mcp_server/](#86-neurrmcp_server)
  - [8.7. neurr/web_canvas.py](#87-neurrweb_canvaspy)
  - [8.8. neurr/cli/](#88-neurrcli)
- [9. vLLM Parameters for Default Model](#9-vllm-parameters-for-default-model)

---

## 1. Executive Summary & Vision

**Neurr** is an open-source, enterprise-grade, agentic AI software development platform engineered exclusively to run on an **NVIDIA GB10 system** (Blackwell architecture with 128 GB of Unified Memory). Inspired by platforms like Google Antigravity, Neurr delivers end-to-end autonomous pair-programming, codebase AST & vector indexing, multi-agent orchestration, and localized code synthesis with 100% data sovereignty, zero cloud egress, and zero external cluster dependencies.

By leveraging the integrated SoC architecture of the NVIDIA GB10 (Blackwell GPU paired with high-performance ARM Cortex CPU host sharing **128 GB of high-speed Unified LPDDR5X Memory**), Neurr hosts state-of-the-art open coding LLMs with high generation speeds and low latency.

Primary agent runtime is **Goose** (`aaif-goose/goose`). Additional runners selected via `--agent` / `NEURR_AGENT`: **Cline**, **Aider**, **Continue**, and **OpenHands** — all targeting the same local vLLM OpenAI-compatible endpoint.

```text
+-----------------------------------------------------------------------------------+
|                                  Neurr Clients                                 |
|   +----------------------+   +-----------------------+   +--------------------+   |
|   | `neurr` CLI       |   | Stdio MCP Companion   |   | Web Canvas UI      |   |
|   | Terminal Interface   |   | (Goose IDE bridge)    |   | Telemetry Pane     |   |
|   +-----------+----------+   +-----------+-----------+   +----------+---------+   |
+---------------+--------------------------+--------------------------+-------------+
|                                          | Model Context Protocol (MCP)           |
+------------------------------------------v----------------------------------------+
|   Agents: Goose (default) | Cline | Aider | Continue | OpenHands                  |
|  +---------------------+  +---------------------+  +----------------------------+ |
|  | Context Engine      |  | Session Controllers |  | Sandbox / Docker / IDE     | |
|  | AST + FTS5 + TF-IDF |  | (5 runners)         |  | Prefix / Extension / Image | |
|  +---------------------+  +---------------------+  +----------------------------+ |
+------------------------------------------+----------------------------------------+
|                                          | Local OpenAI-compatible HTTP (vLLM)    |
+------------------------------------------v----------------------------------------+
|                      NVIDIA GB10 Hardware & Inference Engine                      |
|  +-----------------------------------------------------------------------------+  |
|  | vLLM Engine (BF16 / INT8 / FP8 / INT4) + Prefix Cache / Chunked Prefill     |  |
|  +-----------------------------------------------------------------------------+  |
|  | GB10 Open Models: Qwen 2.5 Coder 32B/72B | DeepSeek-R1-Distill 32B/70B      |  |
|  |                   Llama 3.3 70B | StarCoder2 15B | Draft 1.5B/3B            |  |
|  +-----------------------------------------------------------------------------+  |
|  | Hardware: 1x NVIDIA GB10 (Blackwell | 128 GB Unified Memory)                |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Parity & Key Differentiators

| Feature                | Google Antigravity                | Neurr                                                                                         |
| :--------------------- | :-------------------------------- | :----------------------------------------------------------------------------------------------- |
| **Model Hosting**      | Cloud (Google Vertex AI / Gemini) | **100% On-Premise NVIDIA GB10 System**                                                           |
| **Source Code**        | Proprietary                       | **Open Source (Apache 2.0)**                                                                     |
| **Supported Models**   | Gemini 1.5 Pro / Flash, Claude    | **Single-Node Open LLMs (Qwen 2.5 Coder, DeepSeek-R1 Distills, Llama 3.3)**                      |
| **Inference Hardware** | Cloud TPUs / GPUs                 | **NVIDIA GB10 (Blackwell Architecture)**                                                         |
| **Data Privacy**       | Cloud Privacy Policy              | **Strict Zero-Egress Air-Gapped Local Execution**                                                |
| **System Memory**      | Cloud Allocation                  | **128 GB Unified LPDDR5X Memory**                                                                |
| **Interfaces**         | Antigravity IDE, CLI, Desktop     | **CLI (`neurr`); agents Goose / Cline / Aider / Continue / OpenHands; stdio MCP; Web Canvas** |

---

## 3. Supported NVIDIA GB10 Model Matrix

Aliases and HuggingFace repos are defined in `ModelMatrixRegistry.MATRIX` (`neurr/hardware/model_matrix_registry.py`):

| Alias                     | Model                         | Parameters | Weight Format / Quantization | Memory Required | Tool Support | GB10     |
| :------------------------ | :---------------------------- | :--------- | :--------------------------- | :-------------- | :----------- | :------- |
| `qwen3.6-35b-a3b-nvfp4`   | Qwen 3.6 35B-A3B (**default**) | 35B (3B active) | NVFP4                     | ~25 - 60 GB     | ✅            | ✅       |
| `qwen2.5-coder-32b`       | Qwen 2.5 Coder 32B            | 32B        | BF16 / INT8 / FP8 / AWQ      | ~35 - 64 GB     | ✅            | ✅       |
| `qwen2.5-coder-72b`       | Qwen 2.5 Coder 72B            | 72B        | INT8 / FP8 / INT4            | ~45 - 80 GB     | ✅            | ✅       |
| `qwen2.5-coder-1.5b`      | Qwen 2.5 Coder 1.5B (Draft)   | 1.5B       | BF16 / FP16 / INT8           | ~3.5 - 6 GB     | ✅            | ✅ Draft |
| `qwen2.5-coder-3b`        | Qwen 2.5 Coder 3B (Draft)     | 3.0B       | BF16 / FP16 / INT8           | ~6.5 - 10 GB    | ✅            | ✅ Draft |
| `deepseek-r1-distill-32b` | DeepSeek-R1-Distill-Qwen-32B  | 32B        | BF16 / INT8 / FP8 / AWQ      | ~35 - 64 GB     | ✅            | ✅       |
| `deepseek-r1-distill-70b` | DeepSeek-R1-Distill-Llama-70B | 70B        | INT8 / FP8 / INT4            | ~45 - 80 GB     | ✅            | ✅       |
| `llama-3.3-70b`           | Llama 3.3 70B Instruct        | 70B        | BF16 / INT8 / FP8 / AWQ      | ~75 - 80 GB     | ✅            | ✅       |
| `starcoder2-15b`          | StarCoder2 15B                | 15B        | BF16 / FP16                  | ~20 - 30 GB     | ❌            | ✅       |
| `deepseek-v3-671b`        | DeepSeek-V3 671B (MoE)        | 671B       | INT4                         | ~350 GB         | ✅            | ❌       |

HF repo examples: `nvidia/Qwen3.6-35B-A3B-NVFP4`, `Qwen/Qwen2.5-Coder-32B-Instruct`, `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B`, `meta-llama/Llama-3.3-70B-Instruct`, `bigcode/starcoder2-15b`.

#### Default Model Rationale

`qwen3.6-35b-a3b-nvfp4` is the default because decode speed on GB10 is bounded by memory bandwidth,
not compute: a mixture-of-experts model with ~3B active parameters generates far faster than a dense
model of comparable quality, and 4-bit weights leave most of the 128 GB for KV cache at long context.

**This model only works on an SM121-safe kernel path.** The CUTLASS FP4 kernels are compiled for the
SM120 ISA and run on GB10 without erroring while producing corrupt output — the recognisable symptom
is a response consisting only of `!` characters. Neurr pins the working path via the model's
`launch_overrides` recipe (see [§4.2.7](#427-per-model-launch-recipes)), and runs an **automated post-launch canary** upon server startup.

The canary sends a single completion request:
1. It asserts the output isn't corrupted (all `!`). If corruption is detected, the server **auto-demotes to an FP8 model** (e.g., `qwen2.5-coder-32b`) to ensure a working baseline.
2. The user should still benchmark against FP8 on their own unit before treating the FP4 numbers as settled — published
   results range from NVFP4 losing to FP8 to winning by ~3x, driven by whether MTP is active and
   which backend was chosen.

The b12x SM12x backends merged upstream in May 2026. If `DEFAULT_VLLM_IMAGE` predates them, the
launch falls back to a slower or broken path — see [§4.10.1](#4101-vllm-runtime-images).

---

## 4. Hardware Integration & Optimization

### 4.1. NVIDIA GB10 Hardware Specification

- **GPU**: NVIDIA GB10 Tensor Core GPU (Blackwell architecture).
- **Unified Memory**: 128 GB LPDDR5X high-speed unified memory shared dynamically between CPU and GPU.
- **CPU Host**: High-performance ARM Cortex CPU cores (`aarch64` architecture).
- **Storage**: NVMe PCIe SSD for high-speed workspace indexing and model caching.

### 4.2. GB10 Inference Stack & Auto-Launch Engine

#### 4.2.1. Weight Pre-Download
- `start_server()` always calls `download_model()` for primary (and draft if set) before spawning the process.
- Download uses `huggingface_hub.snapshot_download`, then `huggingface-cli download`, else defers fetch to vLLM init.

#### 4.2.2. Dual-Model Speculative Decoding
- Speculative decoding requires the draft and target models to share a vocabulary (tokenizer compatibility). For example, `qwen2.5-coder-1.5b` is a valid draft for `qwen2.5-coder-32b`, but invalid for `qwen3.6-35b-a3b-nvfp4` or `llama-3.3-70b`.
- Providing an external `draft_model` when the primary model's recipe already declares in-checkpoint speculation (MTP/Eagle) is a **configuration error** and will fail fast.
- Configured via `--speculative-config` (consolidated from the older `--speculative-model` flag).

#### 4.2.3. Blackwell Performance Flags Actually Applied
- `--enable-prefix-caching` (when enabled; default on)
- `--enable-chunked-prefill` (when enabled; default on)
- `--kv-cache-dtype <dtype>` (default `auto` unless specified otherwise by recipe)
- `--attention-backend <backend>` only when backend ≠ `auto`
- `--moe-backend <backend>` only when set (by argument or model recipe)
- `--reasoning-parser <parser>` only when set (by argument or model recipe)
- `--speculative-config <json>` (used for both external drafts and in-checkpoint MTP speculation)
- `--quantization fp8` auto-selected for model names containing `70b` or `72b` when quantization is
  unset **and** the checkpoint does not declare its own format. Self-declaring formats (NVFP4, MXFP4,
  AWQ, GPTQ) are detected by vLLM from `config.json`; Neurr suppresses the flag rather than
  overriding that detection with a guess derived from the model name.

#### 4.2.7. Per-Model Launch Recipes

Not every model runs correctly on one global flag set. `ModelSpec.launch_overrides`
(`neurr/hardware/model_spec.py`) carries a per-model vLLM recipe as registry data, and
`build_launch_command` merges it with this precedence:

```text
explicit caller argument  >  model launch_overrides  >  module default
```

An argument left as `None` is treated as unset; `attention_backend='auto'` also counts as unset,
since `auto` delegates the choice by definition. Models with no recipe resolve
to exactly the previous defaults, so the mechanism is inert unless a model opts in. For example, `deepseek-r1-distill-32b` and `deepseek-r1-distill-70b` opt-in to specify `reasoning_parser: "deepseek_r1"`.

Recognised keys mirror `build_launch_command` parameters, plus:

| Key | Effect |
| :-- | :----- |
| `env` | `Dict[str, str]` exported into the vLLM container as `docker run -e K=V`. Required because some kernel-backend selection (notably NVFP4 MoE) is read from the process environment, not CLI flags. |
| `speculative_config` | Dict serialised to `--speculative-config`. For in-checkpoint speculation, which has no separate draft model. |
| `extra_args` | Verbatim flags appended to the launch command, for recipe settings without a first-class parameter. |

The default model's recipe is `ModelMatrixRegistry.MATRIX['qwen3.6-35b-a3b-nvfp4'].launch_overrides`
and follows NVIDIA's published DGX Spark recipe: 131072 (`131k`) context, `gpu_memory_utilization` 0.3,
FP8 KV cache, FlashInfer attention, `qwen3_xml` tool parser, `qwen3` reasoning parser,
`8192` max batched tokens, `flashinfer_b12x` MoE backend, MTP speculation (`num_speculative_tokens: 3`, `moe_backend: triton`),
`--max-num-seqs 4`, tensorizer disabled (`use_tensorizer: False`). Required env vars: `VLLM_NVFP4_GEMM_BACKEND=flashinfer-b12x`,
`VLLM_MARLIN_USE_ATOMIC_ADD=1`, `VLLM_DISABLED_KERNELS=MarlinNvFp4LinearKernel`.


#### 4.2.4. Configuration & Base Flags
- **Base Launch Flags**: `--host 0.0.0.0 --port <port> --max-model-len 16384 --gpu-memory-utilization 0.50 --trust-remote-code --async-scheduling --load-format fastsafetensors` plus the optional flags above. (A low `0.50` default prevents OOMs on the 128GB unified memory SoC).
- **Default num_speculative_tokens**: `8`

#### 4.2.5. Readiness Polling & Live Streaming
- `neurr start_server` (and agent runners) use `ModelLoadingMonitor` + `VLLMServerManager`.
- The monitor thread constantly pipes raw vLLM container logs to stdout, prints Docker reserved memory usage every 10 seconds (`[HH:MM:SS] 📊 Reserved memory (Docker): …`), tracks loading stages from logs, and polls `/v1/models` until healthy. `VLLMStartupMonitor` provides additional memory-growth tracking and stall detection with explicit progress percentages (`~X% (Y/Z GB since start)`).
- `start_server` exits once the health check passes (server keeps running in background).

#### 4.2.6. Instant Signal Handling
- Poll loop sleeps in short intervals so `Ctrl+C` is handled promptly.

### 4.3. Agent Runtimes (Goose, Cline, Aider, Continue, OpenHands)

CLI selects the runner via `--agent` / `NEURR_AGENT` / `NEURR_RUNNER` / config `agent_runner` (default `goose`). Choices: `goose`, `cline`, `aider`, `continue`, `openhands`. Non-Goose runners reuse `GooseRunner.wait_for_vllm()` for shared vLLM auto-launch.

#### 4.3.1. Goose (default, `agent_runner=goose`)

- **Execution Runtime**: Goose AI Agent (`aaif-goose/goose` v1.45+).
- **Auto-Installation**: `curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes`.
- **Executable Resolution**: `PATH` → `~/.local/bin/goose` → `~/.goose/bin/goose` → `sys.prefix/bin/goose` → `sys.prefix/bin/goose-ai`.
- **OpenAI-Compatible Bridge**: `ensure_goose_config()` writes `~/.config/goose/config.yaml` with provider `openai`, host `{vllm_host}`, `base_path: v1`, `api_key: gb10-local-token`, model name, and stdio MCP extension `neurr mcp`.
- **Session Commands**: `goose session` (chat) or `goose run --text "<prompt>"` (run); optional `--debug`.
- **Rootless Sandbox Prefix** (`SandboxManager`, Goose path only):
  - `--sandbox {none,apptainer,podman,docker}` (default `none`).
  - **Apptainer**: `apptainer exec --writable-tmpfs --bind {cwd}:/workspace docker://ubuntu:22.04`
  - **Podman**: `podman run --rm -it -v {cwd}:/workspace:Z -w /workspace ubuntu:22.04`
  - **Docker**: `docker run --rm -it -v {cwd}:/workspace -w /workspace ubuntu:22.04`
  - Missing runtime binary → warning and unsandboxed execution.

#### 4.3.2. Cline (`--agent cline`)

- **Runtime**: VS Code (`code`) or VSCodium (`codium`) + marketplace extension `saoudrizwan.claude-dev`.
- **Provisioning**: `ClineInstaller.install_cline_if_missing()` runs `code --install-extension saoudrizwan.claude-dev` when absent.
- **Workspace Rules**: Rewrites `.clinerules` on every launch with local/offline guidance and Cave Mode instructions (if `--cave` is enabled), ensuring rules are never stale.
- **Session Behavior**: Auto-launch vLLM → require VS Code → install extension → rewrite `.clinerules` → print connection details (API key `gb10-local-token`) → `code|codium $(pwd)`. Prompt is printed only (not auto-submitted). Model selection and endpoint configuration do not propagate automatically to the extension; they must be manually entered into the Cline UI.

#### 4.3.3. Aider (`--agent aider`)

- **Runtime**: Aider CLI (`aider` from package `aider-chat`).
- **Provisioning**: `pip install aider-chat`, then `pipx install aider-chat` if still missing.
- **Launch Flags**: `--openai-api-base {vllm_host}/v1/` , `--openai-api-key gb10-local-token`, `--model openai/{hf_repo}`.
  - `sandbox != none` → `--no-auto-commits`; otherwise `--auto-commits` (does **not** wrap with `SandboxManager` prefixes).
  - `run` prompt → `--message <prompt>`; `--debug` → `--verbose`.

#### 4.3.4. Continue (`--agent continue`)

- **Runtime**: VS Code / VSCodium + marketplace extension `Continue.continue`.
- **Provisioning**: `code --install-extension Continue.continue` when absent.
- **Config**: Writes/merges `~/.continue/config.json` with:
  - chat `models[]` entry: provider `openai`, model = primary HF repo, `apiBase` `{vllm_host}/v1/`, key `gb10-local-token`
  - `tabAutocompleteModel`: Unset by default. A real autocomplete model requires a separate vLLM instance on another port, as speculative draft models are not served as separate endpoints.
- **Session Behavior**: Auto-launch vLLM → require VS Code → install extension → write Continue config → open workspace. Prompt argument is unused.

#### 4.3.5. OpenHands (`--agent openhands`)

- **Runtime**: Docker image `ghcr.io/all-hands-ai/openhands:main` (container name `neurr-openhands`).
- **Provisioning**: Requires working Docker (`docker ps`); `docker pull` image if missing.
- **Launch**: `docker run --rm -it --name neurr-openhands` with:
  - Env: `LLM_MODEL=openai/{hf_repo}`, `LLM_BASE_URL={vllm_host}/v1`, `LLM_API_KEY=gb10-local-token`, `WORKSPACE_BASE={cwd}`
  - Mounts: `/var/run/docker.sock`, `{cwd}:/opt/workspace_base`
  - Port: **3000:3000** (UI at `http://localhost:3000`)
- **Session Behavior**: Auto-launch vLLM → Docker check → pull image → remove stale container → run OpenHands UI. Prompt argument is unused (not passed into the container).

### 4.4. Codebase Context Engine (AST + SQLite / FTS5)

See dedicated section [4.7 Code Indexing Pipeline](#47-code-indexing-pipeline) for full architecture, ProcessPoolExecutor parallelization, SQLite mmap optimization, ingestion pipeline, and hybrid FTS5+TF-IDF search.

### 4.5. Session Startup Process

#### Goose path (`neurr chat|run` with default agent)

```text
neurr chat | run [--agent goose]
       |
       v
[1] Resolve config (CLI > Env > config file > defaults)
       |
       v
[2] ensure_goose_config()  -->  ~/.config/goose/config.yaml
       |
       v
[3] validate_model()       -->  hard-fail if model exceeds memory budget (e.g. deepseek-v3-671b)
       |
       v
[4] GET {vllm_host}/v1/models  --healthy?--+
       |                                   |
       | no                                | yes
       v                                   |
[5] Auto-launch vLLM (background)          |
       |  a. download_model(primary/draft) |
       |  b. launch tier resolve + Popen   |
       |  c. log streamer                  |
       v                                   |
[6] Poll readiness (0.1s slices, [vLLM] logs)
       |                                   |
       +------------------+----------------+
                          |
                          v
[7] Pre-warm Goose system prompt + MCP tools (silent max_tokens=1 chat request)
       |
       v
[8] Provision Goose CLI if missing
       |
       v
[9] Optional sandbox prefix
       |
       v
[10] Exec goose session | goose run --text "<prompt>"
```

#### Cline path (`--agent cline`)

1. Health-check / auto-launch vLLM via `GooseRunner.wait_for_vllm()`.
2. Require `code` or `codium` on `PATH`.
3. Install Cline extension if missing.
4. Ensure `.clinerules` exists.
5. Print connection details; launch VS Code on the workspace. Prompt text is printed only (not auto-submitted to Cline).

#### Aider path (`--agent aider`)

1. Health-check / auto-launch vLLM via `GooseRunner.wait_for_vllm()`.
2. Install `aider-chat` via pip (then pipx) if `aider` missing.
3. Launch `aider` with OpenAI-compatible base/key/model flags; optional architect/editor split when `draft_model` is set; `--message` for `run`; `--verbose` for `--debug`.

#### Continue path (`--agent continue`)

1. Health-check / auto-launch vLLM.
2. Require VS Code / VSCodium.
3. Install `Continue.continue` if missing.
4. Write/merge `~/.continue/config.json` (chat model + tab autocomplete).
5. Launch `code|codium $(pwd)`. Prompt unused.

#### OpenHands path (`--agent openhands`)

1. Health-check / auto-launch vLLM.
2. Require Docker daemon.
3. Pull `ghcr.io/all-hands-ai/openhands:main` if missing.
4. `docker rm -f neurr-openhands`; run container on port **3000** with workspace + docker.sock mounts and LLM env pointing at local vLLM. Prompt unused.

#### `neurr start_server` Variant

Launches `VLLMServerManager.start_server(background=True)`, starts a `ModelLoadingMonitor` thread (live log piping + 10s Docker memory stats + stage detection + health polling), prints progress, and **exits once the model health check passes** (the vLLM server/container continues running). Uses CLI `--model` (defaults to `qwen3.6-35b-a3b-nvfp4`) and other tuning flags from config. No agent runner is started.

#### 4.5.1. Failure Modes

- **vLLM launch failure**: Hint to run `neurr start_server --model <model>`; `chat`/`run` exit `1`.
- **Process crash during wait**: Drain remaining logs; return failure.
- **Ctrl+C during wait**: Cancel without starting the agent.
- **Goose install failure**: Print manual curl install command; exit `1`.
- **Cline / Continue without VS Code**: Exit `1` with PATH install hint.
- **Aider install failure**: Exit `1` with `pip install aider-chat` hint.
- **OpenHands without Docker / pull failure**: Exit `1` with Docker daemon hint.

### 4.6. Goose-vLLM Integration

`NeurrConfig` (`neurr/config/neurr_config.py`) wires Goose directly to the local vLLM OpenAI-compatible endpoint and guarantees real OS shell execution.

#### 4.6.1. Endpoint Wiring
`get_env_vars` + `ensure_goose_config` set:
- `GOOSE_PROVIDER=openai`
- `OPENAI_BASE_URL={vllm_host}/v1`
- `OPENAI_API_KEY=gb10-local-token`
- `GOOSE_MODEL=<resolved HF repo>`

#### 4.6.2. Real Shell Execution
- `GOOSE_ALLOW_SHELL=1` and `GOOSE_ALLOW_READ=1` exported on every launch.
- Built-in `developer` extension registered with `"allow_shell": true` so Goose invokes the native `/bin/bash -c` instead of emitting simulated JSON `{"name":"shell"}` tool calls.

#### 4.6.3. MCP Companion
Always registers the `jetbrains_mcp` stdio extension (`neurr mcp`).

#### 4.6.4. Deep-Merge Safety
`ensure_goose_config` performs a targeted deep-merge of the `extensions` dict so `developer` + `jetbrains_mcp` are never overwritten when the user already has an `extensions` section in `~/.config/goose/config.yaml`.

#### 4.6.5. vLLM Side
`start_server` passes `--enable-auto-tool-choice` plus the parser resolved for the target model,
satisfying Goose function-calling requirements without extra flags. Parser resolution order is
explicit `--tool-call-parser` → the model's `launch_overrides` recipe → a family guess from the model
name (`mistral` → `mistral`, otherwise `hermes`). Parser choice is not a per-family constant: Qwen 2.5
emits Hermes-style `<tool_call>` blocks while Qwen 3.6 emits XML, so the default model resolves to
`qwen3_xml` and Qwen 2.5 Coder still resolves to `hermes`.

Correspondingly, `NeurrConfig.build_instructions()` injects `HERMES_TOOL_CALL_PROMPT` into the
Goose `instructions` block **only** when the resolved parser is `hermes`. Teaching a model to emit
Hermes tags while the server runs an XML parser produces tool calls the server cannot parse. Cave
Mode instructions are appended independently of parser choice.

Result: `neurr chat` / `run` produce a fully-functional Goose session that can execute real shell commands and call tools against the GB10 vLLM instance out-of-the-box.

### 4.7. Code Indexing Pipeline

#### 4.7.1. Entry Points
- `neurr init` always forces a full re-index (`ContextEngine.index_workspace(force_reindex=True)`).
- `neurr index [--dir PATH] [--force]` — manual indexing of any directory (defaults to CWD).

#### 4.7.2. Parallel Execution
`index_workspace` uses `ProcessPoolExecutor(max_workers = min(32, cpu_count*2))` to bypass the GIL on the ARM Cortex host (`context_engine.py:156-159`).

#### 4.7.3. Ingestion Steps
1. Walk workspace, skip `IGNORE_DIRS` (`.git`, `.venv`, `node_modules`, `.idea`, `.neurr`, …) and `IGNORE_EXTENSIONS` (binaries, images, archives).
2. For every accepted file: read content, tokenize (camelCase/snake_case aware), extract Python AST symbols (class/function, signature, docstring, line ranges) via `ASTSymbolExtractor`.
3. Persist to SQLite: `files`, `symbols`, FTS5 virtual table `fts_context` (full original content), and `vec_context` (vec0) virtual table for dense embeddings — plus `PRAGMA mmap_size = 2147483648` (2 GB) for unified-memory access on GB10.
4. Compute in-memory TF-IDF matrix (`TFIDFCalculator.compute_matrix`).
5. Compute 768-dim semantic embeddings via `EmbeddingCalculator` (sentence-transformers + nomic-ai/nomic-embed-text-v1.5) and store in `vec_context` (`sqlite_context_storage.insert_vectors`). Note: To maintain the air-gap, the embedding model is pre-downloaded offline during `init`. It requires `trust_remote_code=True` and correctly applies `search_document: ` (for ingestion) and `search_query: ` (for search) prefixes.
6. Write JSON cache `.neurr/context_index.json` (symbols + metadata only) and close DB.

#### 4.7.4. Hybrid Search
`search_code` combines FTS5 rank + TF-IDF score + cosine similarity from stored embeddings (weighted ×3). Returns top-k files with symbols and paths. Used by MCP `workspace_search_code` tool.

#### 4.7.5. Cache Behavior
Without `--force`, `load_index()` returns cached summary instantly; FTS5 + vector queries still work from SQLite even if TF-IDF/embeddings are cold.

#### 4.7.6. Performance Notes
ProcessPoolExecutor + SQLite mmap together keep indexing fast on the 128 GB unified memory SoC. Local 100-300 MB embedding model runs alongside vLLM.

### 4.8. Tests Architecture

#### 4.8.1. Framework
pytest (invoked via `pytest tests/ -q`).

#### 4.8.2. Structure
One `test_*.py` per major subsystem:
- `test_config.py` — NeurrConfig, env vars, temporary Goose config, cave mode.
- `test_context_engine.py` — indexing, AST extraction, hybrid search (FTS5 + TF-IDF + embeddings).
- `test_hardware.py` — GB10 detection, model matrix, download helpers.
- `test_mcp_server.py` — MCP tools and IDE state.
- `test_runner.py` — all agent runners (Goose, Cline, …) and sandbox logic.
- `test_vllm_server.py` — VLLMLaunchOptions, server manager, health checks.

#### 4.8.3. Style
Lightweight unit tests; tmp_path fixtures for filesystem isolation; no external services required. Existing tests remain green after every change.

### 4.9. Model Download, Hugging Face Caching & Tensorization

#### 4.9.1. Hugging Face Behavior
`ModelDownloader`:
- Primary cache: `~/.cache/huggingface/hub/` (or `$HF_HOME/hub` if `HF_HOME` set).
- Pre-download via `huggingface_hub.snapshot_download` (preferred) or `huggingface-cli download` fallback.
- `download_model()` and `download_all_models()` check `is_model_downloaded()` first; only fetch if missing.
- Invoked automatically by `init`, `start_server`, and explicit `download` command.

#### 4.9.2. Tensorizer Behavior
- Secondary cache: `~/.cache/neurr/tensorizer/`.
- After HF download (when `auto_tensorize=True`, default is False), `tensorize_model()` serializes weights to `<repo>--/model.tensors`.
- `is_model_tensorized()` checks for non-empty `model.tensors` file.
- Used by vLLM server when `--tensorize` flag is enabled (default off) for faster loading on GB10.

#### 4.9.3. Cache Management Commands
- `neurr clear-cache`: removes both HF and tensorizer parent directories.
- `neurr clear-tensorize-cache`: removes only the tensorizer cache directory.

#### 4.9.4. Download CLI
`neurr download` supports `--model`, `--all`, `--tensorize/--no-tensorize`.

#### 4.9.5. Best-Effort Policy
All operations are best-effort; failures fall back to on-demand fetch by vLLM.

### 4.10. Docker vLLM Architecture

Neurr uses Docker for the primary vLLM inference runtime. All Docker operations require a working Docker daemon (`docker ps` must succeed).

#### 4.10.1. vLLM Runtime Images
- **Base Image** (`DEFAULT_VLLM_IMAGE`): `nvcr.io/nvidia/vllm:26.07-py3`
  - Dockerfile dynamically builds:
    ```dockerfile
    FROM nvcr.io/nvidia/vllm:26.07-py3
    RUN python -m pip install --no-cache-dir --no-deps "xgrammar==0.2.4"
    RUN pip install "vllm[tensorizer]"
    ```
  - This custom image is tagged as `neurr-vllm-tensorizer:26.07-py3`.
  - Used by default in `build_launch_command` and `start_server`.
  - Provides tensorizer support for fast GB10 model loading.
  - Container name pattern: `neurr-vllm-<port>`; pre-removed before launch.
  - Launch form: `docker run --rm --name ... --gpus all -p {port}:{port} -v ~/.cache/huggingface:/root/.cache/huggingface [-e HF_TOKEN=…] neurr-vllm-tensorizer:26.07-py3 <hf_repo> …`

### 4.11. Docker Agent Architecture

Neurr uses Docker for the optional OpenHands agent UI. All Docker operations require a working Docker daemon (`docker ps` must succeed).

#### 4.11.1. OpenHands Agent Image
- **Image**: `ghcr.io/all-hands-ai/openhands:main` (constant `OPENHANDS_IMAGE` in `openhands_installer.py`)
  - Pulled on-demand by `OpenHandsInstaller.pull_image_if_missing()` when `--agent openhands` is selected.
  - Container name: `neurr-openhands`
  - Mounts: workspace directory + Docker socket; LLM endpoint pointed at local vLLM.
  - Port mapping: host 3000 → container 3000.
  - No local build; always pulled from GHCR.

---

## 5. Client Interfaces & Developer Experience

### 5.1. `neurr` CLI Suite

Implemented by `NeurrCLIController` (`neurr/cli/`). Rich-powered terminal UI. **15** subcommands.

#### Global Options

| Flag                                             | Description                                               |
| :----------------------------------------------- | :-------------------------------------------------------- |
| `--config PATH`                                  | Custom Neurr config (`.yaml` / `.json`)                |
| `--sandbox {none,apptainer,podman,docker}`       | Rootless sandbox prefix (**Goose only**)                  |
| `--agent {goose,cline,aider,continue,openhands}` | Primary agent runner (default: `goose`)                   |
| `--hf-token TOKEN`                               | HuggingFace token (else `HF_TOKEN` / `NEURR_HF_TOKEN`) |

#### Subcommand Summary

| Subcommand                  | Description                                                                                                      |
| :-------------------------- | :--------------------------------------------------------------------------------------------------------------- |
| **`init`**                  | Pre-download models, save `.neurr/config.yaml`, write Goose config, force-index workspace                     |
| **`chat`**                  | Interactive session for selected agent (Goose / Aider CLI, or VS Code / OpenHands UI)                            |
| **`run`**                   | Non-interactive task where supported (Goose `--text`, Aider `--message`; others launch UI and may ignore prompt) |
| **`status`**                | Rich panels: hardware, vLLM/agent readiness (all 5 runners), context index                                       |
| **`start_server`**          | Launch local vLLM server optimized for GB10                                                                      |
| **`stop_server`**           | Stop the running vLLM Docker container                                                                           |
| **`remove_server`**         | Remove the vLLM Docker container                                                                                 |
| **`show_request_logs`**     | Tail the vLLM Docker container logs                                                                              |
| **`index`**                 | AST + FTS5 + TF-IDF workspace index                                                                              |
| **`mcp`**                   | Stdio MCP server for IDE companion tools                                                                         |
| **`endpoints`**             | Lists all vLLM/OpenAI-compatible REST endpoints + credentials                                                    |
| **`web`**                   | Web Canvas UI on port 8501 (default)                                                                             |
| **`download`**              | Pre-download model weights to HF cache                                                                           |
| **`clear-cache`**           | Clear both HF and tensorizer model caches                                                                        |
| **`clear-tensorize-cache`** | Clear only the tensorizer model cache                                                                            |

#### Command Specification Subsections

##### 5.1.1. `neurr init`

```text
neurr init [--model MODEL] [--draft-model DRAFT_MODEL] [--vllm-host HOST] [--sandbox …] [--agent …] [--hf-token …]
```

- **Behavior**: Downloads primary/draft weights → `save_config()` → `ensure_goose_config()` → `ContextEngine.index_workspace(force_reindex=True)`.
- **Example**: `neurr init --model qwen3.6-35b-a3b-nvfp4 --agent goose`

##### 5.1.2. `neurr chat`

```text
neurr chat [--model MODEL] [--draft-model DRAFT_MODEL] [--agent goose|cline|aider|continue|openhands] [--sandbox …] [--hf-token …] [--debug] [--cave]
```

- **Behavior**: Instantiates `GooseRunner` / `ClineRunner` / `AiderRunner` / `ContinueRunner` / `OpenHandsRunner` from `config.agent_runner`, then `run_session(debug=…)`. See [§4.3](#43-agent-runtimes-goose-cline-aider-continue-openhands) and [§4.5](#45-session-startup-process).
- **Function calling (Goose)**: Enabled by default via `--enable-auto-tool-choice`. The `--tool-call-parser` is resolved automatically per-model (e.g., `qwen3_xml` for the default model, `hermes` for Qwen 2.5 Coder). No extra flags needed for `neurr chat`.
- **Cave Mode (`--cave`)**: When enabled, Neurr injects the strict Cave Mode system prompt ("You are in Cave Mode...") into Goose `instructions` or `.clinerules` for Cline. Forces terse, command-only output.
- **Example**: `neurr chat --agent aider --debug`

##### 5.1.3. `neurr run`

```text
neurr run "PROMPT" [--model MODEL] [--draft-model DRAFT_MODEL] [--agent …] [--sandbox …] [--hf-token …] [--debug] [--cave]
```

- **Behavior**: Same runner selection. Goose: `goose run --text "<prompt>"`. Aider: `aider … --message "<prompt>"`. Cline: prints prompt and opens VS Code. Continue / OpenHands: launch UI; prompt unused.
- **Example**: `neurr run "Refactor database connection pool to use async pg" --agent goose`

##### 5.1.4. `neurr status`

- **Behavior**: Panels for:
  - **Hardware**: GB10 qualification, GPU name, driver, total/used/available unified memory, VRAM usage, architecture.
  - **vLLM & Agent**: endpoint health, served models, active agent (`goose`/`cline`/`aider`/`continue`/`openhands`), configured/draft model, sandbox, HF token presence, prefix/chunked label, `num_scheduler_steps`, `kv_cache_dtype`, Goose CLI, Cline extension, Aider CLI, Continue extension, OpenHands Docker image readiness, config paths.
  - **Context**: indexed file count, AST symbol count, JSON + SQLite paths (if index loaded).

##### 5.1.5. `neurr start_server`

```text
neurr start_server [--model MODEL] [--port PORT] [--quantization QUANT] [--draft-model DRAFT] [--num-speculative-tokens N] [--hf-token …] [--attention-backend …] [--kv-cache-dtype …] [--api-key KEY] [--enable-auto-tool-choice] [--tool-call-parser PARSER] [--max-num-batched-tokens N] [--guided-decoding-backend BACKEND] [--tensorize/--no-tensorize]
```

- **Behavior**: Starts vLLM in background + `ModelLoadingMonitor` (live logs + memory every 10s). Exits cleanly once health check passes (server keeps running). `--api-key KEY` enables optional OpenAI-compatible auth (not set by default). Function calling for Goose is enabled **by default** (`--enable-auto-tool-choice`), with `--tool-call-parser` automatically resolved per-model. `--max-num-batched-tokens 8192` is passed automatically when `--enable-chunked-prefill` (default) to improve TTFT on large codebase prompts. See [start_server variant](#neurr-start_server-variant).
- **Docker Command**: Executes `docker rm -f neurr-vllm-<port>` followed by:
  ```bash
  docker run --ipc=host --network host --name neurr-vllm-<port> --gpus all \
    -v ~/.cache/huggingface:/root/.cache/huggingface \
    -v ~/.cache/neurr:/root/.cache/neurr \
    -e HF_TOKEN=<token> \
    -e CUTE_DSL_ARCH=sm_121a \
    -e VLLM_LOGGING_LEVEL=DEBUG \
    [recipe-specific environment variables] \
    --entrypoint vllm <image> serve <model_id> [vllm-flags]
  ```
- **Example**: `neurr start_server --model qwen3.6-35b-a3b-nvfp4 --port 8000`

##### 5.1.6. `neurr stop_server`
```text
neurr stop_server [--port PORT]
```

- **Behavior**: Stops the Docker container `neurr-vllm-<port>` (safe no-op if not running). `--port` defaults to 8000.
- **Docker Command**: `docker stop neurr-vllm-<port>`
- **Example**: `neurr stop_server --port 8000`

##### 5.1.7. `neurr remove_server`
```text
neurr remove_server [--port PORT]
```
- **Behavior**: Forces removal of the Docker container `neurr-vllm-<port>`.
- **Docker Command**: `docker rm -f neurr-vllm-<port>`
- **Example**: `neurr remove_server --port 8000`

##### 5.1.8. `neurr show_request_logs`
```text
neurr show_request_logs [--port PORT]
```
- **Behavior**: Tails the logs of the running vLLM container.
- **Docker Command**: `docker logs -f neurr-vllm-<port>`
- **Example**: `neurr show_request_logs --port 8000`

##### 5.1.9. `neurr index`

```text
neurr index [--dir PATH] [--force]
```

- **Behavior**: Indexes workspace (Python AST + FTS5 + TF-IDF + nomic-embed-text semantic embeddings); persists `.neurr/context_index.json` and `.neurr/context.db` (vec0 table).
- **Example**: `neurr index --force`

##### 5.1.10. `neurr mcp`

- **Behavior**: Stdio JSON-RPC MCP server. Tools: `ide_get_active_editor`, `ide_get_diagnostics`, `ide_get_open_files`, `ide_open_file`, `ide_apply_diff`, `workspace_search_code`. IDE fields live in in-process `IDEState` (empty unless populated by a companion); `workspace_search_code` uses `ContextEngine.search_code`.

##### 5.1.11. `neurr endpoints`

- **Behavior**: Prints two Rich tables: (1) all standard OpenAI-compatible endpoints (`/v1/models`, `/v1/chat/completions`, etc.) with HTTP methods and short descriptions; (2) credentials showing base URL, optional API key (enabled via `--api-key` on start_server), and `Authorization: Bearer <key>` when used. Note: `--served-model-name` is never set, so external clients must use the full HF repo paths returned by `/v1/models`. Intended for quick copy-paste into external clients.
- **Example**: `neurr endpoints`

##### 5.1.12. `neurr web`

```text
neurr web [--port PORT]
```

- **Behavior**: HTTP server on `0.0.0.0:{port}` (default `8501`). Serves static Glassmorphism SPA + `GET /api/status` (`hardware`, `vllm`, `context`). Memory gauge updates from telemetry; Mermaid diagram and diff pane are **static placeholders**; KV gauge shows fixed `45%` width when vLLM is healthy.
- **Example**: `neurr web --port 8501`

##### 5.1.13. `neurr download`
```text
neurr download [--model MODEL] [--all] [--tensorize/--no-tensorize]
```

- **Behavior**: Pre-downloads into `~/.cache/huggingface/hub/`. Without `--all`, downloads `args.model or config.model` and optional draft. `--all` iterates **sequentially** over all `compatible_gb10` matrix entries. `--tensorize` (disabled by default) also converts the model to tensorizer format. Also invoked automatically from `init` and `start_server`.
- **Example**: `neurr download --model qwen3.6-35b-a3b-nvfp4`

##### 5.1.14. `neurr clear-cache`

- **Behavior**: Clears both Hugging Face (`~/.cache/huggingface`) and tensorizer (`~/.cache/neurr`) parent cache directories using `shutil.rmtree`. Invokes `ModelDownloader.clear_cache()`. Prints status messages (`🗑️`, `ℹ️`, `✅`).
- **Example**: `neurr clear-cache`

##### 5.1.15. `neurr clear-tensorize-cache`

- **Behavior**: Clears only the tensorizer cache directory (`~/.cache/neurr/tensorizer` parent). Invokes `ModelDownloader.clear_tensorizer_cache()`. Prints status messages (`🗑️`, `ℹ️`, `✅`).
- **Example**: `neurr clear-tensorize-cache`

#### Configuration Hierarchy & Resolution Order

1. **CLI parameters** (`--config`, `--model`, `--agent`, `--sandbox`, …) — highest
2. **Environment variables** (`NEURR_*`, `HF_TOKEN`, …)
3. **Config file** — `.neurr/config.yaml` (or `.json`) if present, else `~/.config/neurr/config.yaml`, else default path `.neurr/config.yaml`
4. **Built-in defaults** — lowest

#### Neurr Config File (defaults example)

```yaml
vllm_host: http://localhost:8000
model: qwen3.6-35b-a3b-nvfp4
draft_model: null
num_speculative_tokens: 8
sandbox: none
agent_runner: goose
hf_token: null
enable_prefix_caching: true
enable_chunked_prefill: true
num_scheduler_steps: 8
attention_backend: auto
kv_cache_dtype: fp8
```

#### Environment Variables

| Variable                             | Description                                                               | Default                 |
| :----------------------------------- | :------------------------------------------------------------------------ | :---------------------- |
| `NEURR_CONFIG_PATH`               | Custom config file path                                                   | (resolver default)      |
| `NEURR_VLLM_HOST`                 | vLLM endpoint URL                                                         | `http://localhost:8000` |
| `NEURR_MODEL`                     | Primary model alias                                                       | `qwen3.6-35b-a3b-nvfp4` |
| `NEURR_DRAFT_MODEL`               | Draft model alias                                                         | unset                   |
| `NEURR_SPECULATIVE_TOKENS`        | Speculative token count                                                   | `8`                     |
| `NEURR_SANDBOX`                   | Sandbox engine                                                            | `none`                  |
| `NEURR_AGENT` / `NEURR_RUNNER` | Agent runner (`goose` \| `cline` \| `aider` \| `continue` \| `openhands`) | `goose`                 |
| `HF_TOKEN` / `NEURR_HF_TOKEN`     | HuggingFace token                                                         | unset                   |
| `GOOSE_PROVIDER`                     | Set for Goose processes                                                   | `openai`                |
| `OPENAI_HOST`                        | Set for Goose processes                                                   | `{vllm_host}`           |
| `OPENAI_BASE_PATH`                   | Set for Goose processes                                                   | `v1`                    |
| `OPENAI_API_KEY`                     | Set for Goose processes                                                   | `gb10-local-token`      |
| `GOOSE_MODEL`                        | Set for Goose processes                                                   | `{model}`               |

Tuning keys `enable_prefix_caching`, `enable_chunked_prefill`, `num_scheduler_steps`, `attention_backend`, `kv_cache_dtype` are config-file / CLI only (no dedicated `NEURR_*` env vars).

### 5.2. IDE Integration via Stdio MCP

Registered in Goose config as stdio extension (`cmd: neurr`, `args: [mcp]`). This repository ships the **MCP server**, not a JetBrains plugin or VS Code extension package. Editor tools read/write in-memory `IDEState`; search uses the local context engine.

### 5.3. Web Canvas UI & Telemetry Pane

`neurr web --port 8501`:

- Glassmorphism dark SPA (CDN Mermaid).
- Static architecture Mermaid snippet and placeholder diff lines.
- Live unified-memory gauge from `/api/status`; illustrative KV fill when healthy.
- REST: `GET /api/status` → `{ hardware, vllm, context }`.

---

## 6. System Requirements & Setup

### 6.1. Requirements

- **System**: 1x NVIDIA GB10 (Blackwell, 128 GB Unified Memory) — or host with ≥100 GB RAM for detection fallback
- **OS**: Linux ARM64 (Ubuntu 22.04 LTS or compatible)
- **Drivers**: NVIDIA Linux Driver 580+ / CUDA 13.x (typical GB10 stack)
- **Dependencies**: Python 3.10+, PyYAML, Rich, Requests; optional Docker (vLLM fallback + OpenHands); optional VS Code/`codium` (Cline, Continue); optional `aider-chat` (Aider)

### 6.2. Identifying Your Hardware Variant

```bash
nvidia-smi --query-gpu=name --format=csv
free -h
```

Qualified when GPU name contains `GB10`/`BLACKWELL`, or when total memory ≥ ~100 GiB per detection heuristic.

### 6.3. Quickstart Installation

```bash
git clone https://github.com/neurr/neurr.git
cd neurr
./scripts/install_gb10.sh qwen3.6-35b-a3b-nvfp4   # positional MODEL arg (default qwen3.6-35b-a3b-nvfp4)
neurr status
neurr chat
```

`scripts/install_gb10.sh`:

1. Installs Goose via `releases/latest/download/download_cli.sh` (falls back to `pip install goose-ai`).
2. `pip install -e .`
3. `neurr init --model "${1:-qwen3.6-35b-a3b-nvfp4}"`

Note: runtime Goose auto-install uses `releases/download/stable/…`; the install script uses `releases/latest/…`.

### 6.4. Helper Scripts

| Script                                                     | Role                                                                                           |
| :--------------------------------------------------------- | :--------------------------------------------------------------------------------------------- |
| `scripts/install_gb10.sh [MODEL]`                          | Package + Goose + `neurr init`                                                              |
| `scripts/run_vllm_gb10.sh [MODEL] [PORT] [DRAFT] [TOKENS]` | Thin foreground Python-module vLLM launch (no prefix-cache / chunked-prefill / kv-cache flags) |
| `scripts/run_goose.sh`                                     | Sets Goose OpenAI env vars and runs `goose session`                                            |

Prefer `neurr start_server` / `neurr chat` for full GB10-tuned behavior.

---

## 7. Roadmap & Implementation Verification

- [x] **Phase 1: NVIDIA GB10 Exclusive Specification** — model matrix & unified-memory targeting
- [x] **Phase 2: GB10 Inference Pipeline & Auto-Launch Engine** — multi-tier vLLM, live logs, weight pre-download, CLI suite including `download`
- [x] **Phase 3: Agentic Engine, Provisioning & MCP** — Goose auto-install, stdio MCP tools, multi-agent runners (Cline, Aider, Continue, OpenHands)
- [x] **Phase 4: Context Engine & Web Canvas** — parallel AST, SQLite/FTS5, TF-IDF (in-process), Web Canvas telemetry UI
- [x] **Phase 5: Modular Package Layout** — split packages under `neurr/{hardware,config,runner,vllm_server,context_engine,mcp_server,cli}/` with shim modules for stable imports
- [x] **Phase 6: Expanded Agent Matrix** — Aider CLI, Continue IDE, OpenHands Docker UI wired through `--agent`

---

## 8. Codebase Architecture & Source Reference

Package version: `neurr.__version__ == "1.2.0"`. Top-level `neurr/*.py` modules are thin re-export shims; implementations live in subpackages.

```text
neurr/
├── __init__.py                      # __version__ = "1.2.0"
├── cli.py                           # shim → cli package
├── cli/
│   └── neurr_cli_controller.py   # NeurrCLIController (13 subcommands)
├── config.py                        # shim
├── config/
│   ├── config_path_resolver.py
│   ├── config_file_storage_manager.py
│   └── neurr_config.py
├── context_engine.py                # shim
├── context_engine/
│   ├── context_engine.py
│   ├── ast_symbol_extractor.py
│   ├── tfidf_calculator.py
│   ├── sqlite_context_storage.py
│   ├── code_symbol.py
│   ├── indexed_file.py
│   └── embedding_calculator.py
├── hardware.py                      # shim + detect/download helpers
├── hardware/
│   ├── model_matrix_registry.py
│   ├── model_downloader.py
│   ├── hardware_manager.py
│   ├── model_spec.py
│   ├── memory_metrics.py
│   └── hardware_telemetry.py
├── mcp_server.py                    # shim
├── mcp_server/
│   ├── mcp_server.py
│   ├── mcp_tool_registry.py
│   ├── ide_state.py
│   └── editor_selection.py
├── runner.py                        # shim
├── runner/
│   ├── goose_runner.py              # wait_for_vllm + Goose session
│   ├── goose_installer.py
│   ├── sandbox_manager.py           # Goose sandbox prefixes only
│   ├── cline_runner.py
│   ├── cline_installer.py
│   ├── aider_runner.py
│   ├── aider_installer.py
│   ├── continue_runner.py
│   ├── continue_installer.py
│   ├── openhands_runner.py
│   └── openhands_installer.py
├── vllm_server.py                   # shim
├── vllm_server/
│   ├── vllm_server_manager.py
│   ├── vllm_log_streamer.py
│   ├── vllm_launch_options.py
│   ├── vllm_server_status.py
│   ├── vllm_startup_monitor.py
│   └── model_loading_monitor.py
└── web_canvas.py                    # CanvasHandler + start_web_canvas_server
```

### 8.1. `neurr/hardware/`

GB10 detection, model matrix, speculative memory checks, HF cache downloads.

### 8.2. `neurr/config/`

4-tier config merge, Goose YAML sync, env vars for Goose (`OPENAI_*`, `GOOSE_*`).

### 8.3. `neurr/runner/`

Session startup orchestration across 5 AI Agent runners (`goose` [default], `cline`, `aider`, `continue`, `openhands`), sandbox prefixes (`Apptainer`, `Podman`, `Docker`), and automatic binary/extension provisioning (`GooseInstaller`, `ClineInstaller`, `AiderInstaller`, `ContinueInstaller`, `OpenHandsInstaller`).

### 8.4. `neurr/vllm_server/`

Multi-tier launch, pre-download, health checks, background log queue, loading stage & memory progress monitoring (`ModelLoadingMonitor`, `VLLMStartupMonitor`).

### 8.5. `neurr/context_engine/`

Parallel index, SQLite/FTS5, TF-IDF, dense semantic embeddings (`EmbeddingCalculator`), hybrid `search_code`.

### 8.6. `neurr/mcp_server/`

Async stdio MCP + tool registry + in-memory `IDEState`.

### 8.7. `neurr/web_canvas.py`

HTTP SPA + `/api/status` telemetry.

### 8.8. `neurr/cli/`

`NeurrCLIController` — argparse, Rich status, subcommand dispatch (`main()` → `run_cli()`) (15 subcommands: `init`, `chat`, `run`, `status`, `start_server`, `stop_server`, `remove_server`, `show_request_logs`, `index`, `mcp`, `endpoints`, `web`, `download`, `clear-cache`, `clear-tensorize-cache`).


---

## 9. vLLM Parameters for Default Model

Detailed specification of parameters generated by `VLLMServerManager.build_launch_command(model="qwen3.6-35b-a3b-nvfp4")` for the default model (`qwen3.6-35b-a3b-nvfp4`):

| Parameter / Flag | Passed Value | Source / Recipe Setting | Purpose & Function |
| :--- | :--- | :--- | :--- |
| **Docker Image** | `nvcr.io/nvidia/vllm:26.07-py3` | `DEFAULT_VLLM_IMAGE` | NGC container pinned with Blackwell SM121 driver & library compatibility. |
| `--model` | `nvidia/Qwen3.6-35B-A3B-NVFP4` | `ModelMatrixRegistry.resolve_hf_repo` | HuggingFace repository ID for default NVFP4 weights. |
| `--max-model-len` | `131072` | `launch_overrides["max_model_len"]` | Context window size (128K tokens). |
| `--gpu-memory-utilization` | `0.3` | `launch_overrides["gpu_memory_utilization"]` | 30% memory reservation for GB10 unified memory pool. |
| `--kv-cache-dtype` | `fp8` | `launch_overrides["kv_cache_dtype"]` | Enables FP8 quantization for key-value cache tensors. |
| `--attention-backend` | `flashinfer` | `launch_overrides["attention_backend"]` | Blackwell-optimized FlashInfer attention implementation. |
| `--tool-call-parser` | `qwen3_xml` | `launch_overrides["tool_call_parser"]` | Qwen 3.6 XML tool call parser (`<tool_call>`). |
| `--reasoning-parser` | `qwen3` | `launch_overrides["reasoning_parser"]` | Qwen 3.6 reasoning channel parser. |
| `--max-num-batched-tokens` | `8192` | `launch_overrides["max_num_batched_tokens"]` | Max batched token count for chunked prefill iterations. |
| `--speculative-config` | `{"method": "mtp", "num_speculative_tokens": 3, "moe_backend": "triton"}` | `launch_overrides["speculative_config"]` | In-checkpoint Multi-Token Prediction (3 speculative tokens/step). |
| `--enable-prefix-caching` | *(boolean flag)* | `DEFAULT_PREFIX_CACHING=True` | Automatic KV cache prefix reuse across multi-turn sessions. |
| `--enable-chunked-prefill` | *(boolean flag)* | `DEFAULT_CHUNKED_PREFILL=True` | Prefill chunking for responsive TTFT during long prompt processing. |
| `--max-num-seqs` | `4` | `launch_overrides["extra_args"]` | Caps maximum concurrent sequence count at 4. |
| `--tensor-parallel-size` | `1` | `launch_overrides["extra_args"]` | Single-GPU execution level for single-chip GB10. |
| `--dtype` | `auto` | `launch_overrides["extra_args"]` | Auto-detect model weight precision. |
| `--async-scheduling` | *(boolean flag)* | `VLLMServerManager.build_launch_command` | Enables asynchronous request scheduling. |
| `--load-format` | `fastsafetensors` | `VLLMServerManager.build_launch_command` | Faster model loading format. |

### Note on NVIDIA vLLM Container Version
The default launch configuration pins the NVIDIA vLLM runtime to `nvcr.io/nvidia/vllm:26.07-py3`. This version is explicitly chosen because it contains the upstreamed `flashinfer-b12x` SM12x backends (merged in May 2026) necessary to correctly accelerate and resolve silent corruption issues for NVFP4 on the Blackwell SM121 architecture. Using older containers that predate these merges will fall back to slower or broken paths.
