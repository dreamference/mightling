# DGXCoder Technical Specification

> **Version:** 1.2.0 (`dgxcoder.__version__`)  
> **Status:** Implemented / Production-Ready  
> **Target Hardware:** Exclusive to **NVIDIA GB10** (Blackwell architecture with 128 GB Unified Memory)  
> **Deployment Model:** Single-Node Standalone NVIDIA GB10 System  
> **License:** Open Source (Apache 2.0)  

---

## Table of Contents

- [1. Executive Summary & Vision](#1-executive-summary--vision)
- [2. Parity & Key Differentiators](#2-parity--key-differentiators)
- [3. Supported NVIDIA GB10 Model Matrix](#3-supported-nvidia-gb10-model-matrix)
- [4. Hardware Integration & Optimization](#4-hardware-integration--optimization)
  - [4.1. NVIDIA GB10 Hardware Specification](#41-nvidia-gb10-hardware-specification)
  - [4.2. GB10 Inference Stack & Auto-Launch Engine](#42-gb10-inference-stack--auto-launch-engine)
  - [4.3. Agent Runtimes (Goose & Cline)](#43-agent-runtimes-goose--cline)
  - [4.4. Codebase Context Engine (AST + SQLite / FTS5)](#44-codebase-context-engine-ast--sqlite--fts5)
  - [4.5. Session Startup Process](#45-session-startup-process)
- [5. Client Interfaces & Developer Experience](#5-client-interfaces--developer-experience)
  - [5.1. `dgxcoder` CLI Suite](#51-dgxcoder-cli-suite)
    - [5.1.1. `dgxcoder init`](#511-dgxcoder-init)
    - [5.1.2. `dgxcoder chat`](#512-dgxcoder-chat)
    - [5.1.3. `dgxcoder run`](#513-dgxcoder-run)
    - [5.1.4. `dgxcoder status`](#514-dgxcoder-status)
    - [5.1.5. `dgxcoder serve`](#515-dgxcoder-serve)
    - [5.1.6. `dgxcoder index`](#516-dgxcoder-index)
    - [5.1.7. `dgxcoder mcp`](#517-dgxcoder-mcp)
    - [5.1.8. `dgxcoder web`](#518-dgxcoder-web)
    - [5.1.9. `dgxcoder download`](#519-dgxcoder-download)
  - [5.2. IDE Integration via Stdio MCP](#52-ide-integration-via-stdio-mcp)
  - [5.3. Web Canvas UI & Telemetry Pane](#53-web-canvas-ui--telemetry-pane)
- [6. System Requirements & Setup](#6-system-requirements--setup)
  - [Requirements](#requirements)
  - [Identifying Your Hardware Variant](#identifying-your-hardware-variant)
  - [Quickstart Installation](#quickstart-installation)
  - [Helper Scripts](#helper-scripts)
- [7. Roadmap & Implementation Verification](#7-roadmap--implementation-verification)
- [8. Codebase Architecture & Source Reference](#8-codebase-architecture--source-reference)

---

## 1. Executive Summary & Vision

**DGXCoder** is an open-source, enterprise-grade, agentic AI software development platform engineered exclusively to run on an **NVIDIA GB10 system** (Blackwell architecture with 128 GB of Unified Memory). Inspired by platforms like Google Antigravity, DGXCoder delivers end-to-end autonomous pair-programming, codebase AST & vector indexing, multi-agent orchestration, and localized code synthesis with 100% data sovereignty, zero cloud egress, and zero external cluster dependencies.

By leveraging the integrated SoC architecture of the NVIDIA GB10 (Blackwell GPU paired with high-performance ARM Cortex CPU host sharing **128 GB of high-speed Unified LPDDR5X Memory**), DGXCoder hosts state-of-the-art open coding LLMs with high generation speeds and low latency.

Primary agent runtime is **Goose** (`aaif-goose/goose`). An optional **Cline** path (`--agent cline`) configures VS Code / VSCodium with the Cline extension against the same local vLLM endpoint.

```
+-----------------------------------------------------------------------------------+
|                                  DGXCoder Clients                                 |
|   +-----------------------+   +------------------------+   +-------------------+  |
|   |  `dgxcoder` CLI       |   | Stdio MCP Companion    |   | Web Canvas UI     |  |
|   |  Terminal Interface   |   | (Goose IDE bridge)     |   | Telemetry Pane    |  |
|   +-----------+-----------+   +-----------+------------+   +---------+---------+  |
+---------------+---------------------------+--------------------------+------------+
                                            | Model Context Protocol (MCP)
+-------------------------------------------v---------------------------------------+
|              Agent Execution (Goose default | Cline optional)                     |
|  +--------------------+  +--------------------+  +------------------------------+ |
|  | Context Engine     |  | Goose / Cline      |  | Sandbox + MCP Tool Runtime   | |
|  | AST + FTS5 + TF-IDF|  | Session Controllers|  | Rootless Container Prefix    | |
|  +--------------------+  +--------------------+  +------------------------------+ |
+-------------------------------------------+---------------------------------------+
|                                           | Local OpenAI-compatible HTTP (vLLM)
+-------------------------------------------v---------------------------------------+
|                  NVIDIA GB10 Hardware & Inference Engine                          |
|  +------------------------------------------------------------------------------+  |
|  | vLLM Engine (BF16 / INT8 / FP8 / INT4) + Prefix Cache / Chunked Prefill       |  |
|  +------------------------------------------------------------------------------+  |
|  | GB10 Open Models: Qwen 2.5 Coder 32B/72B | DeepSeek-R1-Distill 32B/70B         |  |
|  |                   Llama 3.3 70B | StarCoder2 15B | Draft 1.5B/3B              |  |
|  +------------------------------------------------------------------------------+  |
|  | Hardware: 1x NVIDIA GB10 (Blackwell | 128 GB Unified Memory)                  |  |
|  +------------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Parity & Key Differentiators

| Feature | Google Antigravity | DGXCoder |
| :--- | :--- | :--- |
| **Model Hosting** | Cloud (Google Vertex AI / Gemini) | **100% On-Premise NVIDIA GB10 System** |
| **Source Code** | Proprietary | **Open Source (Apache 2.0)** |
| **Supported Models** | Gemini 1.5 Pro / Flash, Claude | **Single-Node Open LLMs (Qwen 2.5 Coder, DeepSeek-R1 Distills, Llama 3.3)** |
| **Inference Hardware** | Cloud TPUs / GPUs | **NVIDIA GB10 (Blackwell Architecture)** |
| **Data Privacy** | Cloud Privacy Policy | **Strict Zero-Egress Air-Gapped Local Execution** |
| **System Memory** | Cloud Allocation | **128 GB Unified LPDDR5X Memory** |
| **Interfaces** | Antigravity IDE, CLI, Desktop | **CLI (`dgxcoder`), Goose + stdio MCP, optional Cline/VS Code, Web Canvas** |

---

## 3. Supported NVIDIA GB10 Model Matrix

Aliases and HuggingFace repos are defined in `ModelMatrixRegistry.MATRIX` (`dgxcoder/hardware/model_matrix_registry.py`):

| Alias | Model | Parameters | Precision | Memory Required | GB10 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `qwen2.5-coder-32b` | Qwen 2.5 Coder 32B | 32B | BF16 / INT8 / FP8 | ~35 - 64 GB | ✅ |
| `qwen2.5-coder-72b` | Qwen 2.5 Coder 72B | 72B | INT8 / FP8 / INT4 | ~45 - 80 GB | ✅ |
| `qwen2.5-coder-1.5b` | Qwen 2.5 Coder 1.5B (Draft) | 1.5B | BF16 / FP16 / INT8 | ~3.5 - 6 GB | ✅ Draft |
| `qwen2.5-coder-3b` | Qwen 2.5 Coder 3B (Draft) | 3.0B | BF16 / FP16 / INT8 | ~6.5 - 10 GB | ✅ Draft |
| `deepseek-r1-distill-32b` | DeepSeek-R1-Distill-Qwen-32B | 32B | BF16 / INT8 / FP8 | ~35 - 64 GB | ✅ |
| `deepseek-r1-distill-70b` | DeepSeek-R1-Distill-Llama-70B | 70B | INT8 / FP8 / INT4 | ~45 - 80 GB | ✅ |
| `llama-3.3-70b` | Llama 3.3 70B Instruct | 70B | INT8 / FP8 | ~75 GB | ✅ |
| `starcoder2-15b` | StarCoder2 15B | 15B | BF16 / FP16 | ~20 - 30 GB | ✅ |
| `deepseek-v3-671b` | DeepSeek-V3 671B (MoE) | 671B | INT4 | ~350 GB | ❌ |

HF repo examples: `Qwen/Qwen2.5-Coder-32B-Instruct`, `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B`, `meta-llama/Llama-3.3-70B-Instruct`, `bigcode/starcoder2-15b`.

---

## 4. Hardware Integration & Optimization

### 4.1. NVIDIA GB10 Hardware Specification
* **GPU**: NVIDIA GB10 Tensor Core GPU (Blackwell architecture).
* **Unified Memory**: 128 GB LPDDR5X high-speed unified memory shared dynamically between CPU and GPU.
* **CPU Host**: High-performance ARM Cortex CPU cores (`aarch64` architecture).
* **Storage**: NVMe PCIe SSD for high-speed workspace indexing and model caching.
* **Detection** (`HardwareManager.detect_gb10_hardware`):
  1. Query `nvidia-smi --query-gpu=name,driver_version,memory.total`.
  2. Read `/proc/meminfo` for total/available/used system memory.
  3. Mark `is_gb10=True` if GPU name contains `GB10` or `BLACKWELL`, **or** if total system memory ≥ **100 GB** (fallback; may label GPU as `NVIDIA GB10 (Simulated / Unified Memory Node)` when name is missing).
* Telemetry fields returned as a dict: `is_gb10`, `gpu_name`, `driver_version`, `total_unified_memory_gb`, `available_memory_gb`, `used_memory_gb`, `vram_gb`, `arch`. (`vram_gb` is collected but not shown in `dgxcoder status`.)

### 4.2. GB10 Inference Stack & Auto-Launch Engine
* **Multi-Tiered Launch Resolution** (`VLLMServerManager.build_launch_command`):
  1. **Native CLI**: `vllm serve <hf_repo>` when `vllm` is on `PATH`.
  2. **Python Module**: `python -m vllm.entrypoints.openai.api_server --model <hf_repo>` when the `vllm` package imports successfully.
  3. **Docker Fallback**: `docker run --rm --name dgxcoder-vllm-{port} --gpus all -p {port}:{port} -v ~/.cache/huggingface:/root/.cache/huggingface [-e HF_TOKEN=…] vllm/vllm-openai:latest <hf_repo> …` when Docker is available (`docker ps` succeeds). Pre-removes conflicting container name before launch.
  4. If neither vLLM nor Docker is available, still emits the Python-module command form and prints an install hint.
* **Weight Pre-Download**: `start_server()` always calls `download_model()` for primary (and draft if set) before spawning the process. Download uses `huggingface_hub.snapshot_download`, then `huggingface-cli download`, else defers fetch to vLLM init.
* **Dual-Model Speculative Decoding**: When `draft_model` is set, appends `--speculative-model <hf_draft> --num-speculative-tokens <N>`.
* **Blackwell Performance Flags Actually Applied**:
  - `--enable-prefix-caching` (when enabled; default on)
  - `--enable-chunked-prefill` (when enabled; default on)
  - `--kv-cache-dtype <dtype>` (default `auto`)
  - `--attention-backend <backend>` only when backend ≠ `auto`
  - `--quantization fp8` auto-selected for model names containing `70b` or `72b` when quantization is unset
* **Config-Stored but Not Passed to vLLM**: `num_scheduler_steps` (default `8`) is accepted on CLI/config/status display but is **not** appended as `--num-scheduler-steps` in `build_launch_command` today.
* **Base Launch Flags**: `--host 0.0.0.0 --port <port> --max-model-len 16384 --gpu-memory-utilization 0.90 --trust-remote-code --enforce-eager` plus the optional flags above.
* **Readiness Polling & Live Streaming**: `GooseRunner.wait_for_vllm()` auto-launches background vLLM when offline, streams `[vLLM]` logs, and polls `GET /v1/models` until HTTP 200.
* **Instant Signal Handling**: Poll loop sleeps in 0.1s slices so `Ctrl+C` is handled promptly.

### 4.3. Agent Runtimes (Goose & Cline)

#### Goose (default, `agent_runner=goose`)
* **Execution Runtime**: Goose AI Agent (`aaif-goose/goose` v1.45+).
* **Auto-Installation**: `curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash -s -- --yes`.
* **Executable Resolution**: `PATH` → `~/.local/bin/goose` → `~/.goose/bin/goose` → `sys.prefix/bin/goose` → `sys.prefix/bin/goose-ai`.
* **OpenAI-Compatible Bridge**: `ensure_goose_config()` writes `~/.config/goose/config.yaml` with provider `openai`, host `{vllm_host}`, `base_path: v1`, `api_key: gb10-local-token`, model name, and stdio MCP extension `dgxcoder mcp`.
* **Session Commands**: `goose session` (chat) or `goose run --text "<prompt>"` (run); optional `--debug`.
* **Rootless Sandbox Prefix** (`SandboxManager`, Goose path only):
  - `--sandbox {none,apptainer,podman,docker}` (default `none`).
  - **Apptainer**: `apptainer exec --writable-tmpfs --bind {cwd}:/workspace docker://ubuntu:22.04`
  - **Podman**: `podman run --rm -it -v {cwd}:/workspace:Z -w /workspace ubuntu:22.04`
  - **Docker**: `docker run --rm -it -v {cwd}:/workspace -w /workspace ubuntu:22.04`
  - Missing runtime binary → warning and unsandboxed execution.

#### Cline (optional, `--agent cline` / `DGXCODER_AGENT=cline`)
* **Runtime**: VS Code (`code`) or VSCodium (`codium`) + marketplace extension `saoudrizwan.claude-dev`.
* **Provisioning**: `ClineInstaller.install_cline_if_missing()` runs `code --install-extension saoudrizwan.claude-dev` when absent.
* **Workspace Rules**: Writes `.clinerules` (once, if missing) with OpenAI-compatible base URL `{vllm_host}/v1`, resolved HF model ID, and local/offline guidance.
* **Session Behavior**: Reuses GooseRunner’s `wait_for_vllm()` for auto-launch, prints Cline connection details (including API key `gb10-local-token`), then launches `code|codium $(pwd)`. Does **not** apply sandbox prefixes or invoke Goose.

### 4.4. Codebase Context Engine (AST + SQLite / FTS5)
* **Parallel Parsing**: `ThreadPoolExecutor` with `max_workers = min(32, cpu_count * 2)` over non-ignored workspace files.
* **AST**: Python (`.py`) only — class/function symbols, signatures, docstrings, line ranges via `ASTSymbolExtractor`.
* **Tokenization / TF-IDF**: All indexed text files tokenized (camelCase / snake_case aware). TF-IDF matrix is computed in-memory during `index_workspace()` and used for hybrid search in the same process.
* **SQLite + FTS5**: `.dgxcoder/context.db` tables `files`, `symbols`, and FTS virtual table `fts_context` storing **full file content** (not pre-tokenized terms).
* **JSON Cache**: `.dgxcoder/context_index.json` stores symbols and file metadata **without** tokens or TF-IDF. After `load_index()`, `tokens=[]` and TF-IDF is empty until a fresh in-process reindex; FTS5 search still works from SQLite.
* **Hybrid Search**: `search_code()` combines FTS5 ranks with in-memory TF-IDF scores.
* **Ignore Sets**: VCS/venv/node_modules/build/dist/`.dgxcoder`, plus common binary extensions.

### 4.5. Session Startup Process

#### Goose path (`dgxcoder chat|run` with default agent)

```
dgxcoder chat | run [--agent goose]
       |
       v
[1] Resolve config (CLI > Env > config file > defaults)
       |
       v
[2] ensure_goose_config()  -->  ~/.config/goose/config.yaml
       |
       v
[3] validate_model()       -->  warn if model exceeds memory budget
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
[7] Provision Goose CLI if missing
       |
       v
[8] Optional sandbox prefix
       |
       v
[9] Exec goose session | goose run --text "<prompt>"
```

#### Cline path (`--agent cline`)

1. Health-check / auto-launch vLLM via `GooseRunner.wait_for_vllm()` (same download + launch path).
2. Require `code` or `codium` on `PATH`.
3. Install Cline extension if missing.
4. Ensure `.clinerules` exists.
5. Print connection details; launch VS Code on the workspace. Prompt text is printed only (not auto-submitted to Cline).

#### `dgxcoder serve` Variant

Resolves `DGXCoderConfig`, then calls `VLLMServerManager.start_server(background=False)` with **CLI `args.model` / `args.draft_model` / `args.num_speculative_tokens` passed through directly**. When those flags are omitted, Python `None` is passed into `start_server` (overriding the function’s default `"qwen2.5-coder-32b"`). Prefer explicit `--model` on `serve`, or rely on `chat`/`run` auto-launch which uses `config.model`. Tuning flags (`enable_prefix_caching`, etc.) come from the resolved config. Foreground process owns the terminal; Goose/Cline are not started.

#### Failure Modes

* **vLLM launch failure**: Hint to run `dgxcoder serve --model <model>`; `chat`/`run` exit `1`.
* **Process crash during wait**: Drain remaining logs; return failure.
* **Ctrl+C during wait**: Cancel without starting the agent.
* **Goose install failure**: Print manual curl install command; exit `1`.
* **Cline without VS Code**: Exit `1` with PATH install hint.

---

## 5. Client Interfaces & Developer Experience

### 5.1. `dgxcoder` CLI Suite

Implemented by `DGXCoderCLIController` (`dgxcoder/cli/`). Rich-powered terminal UI. **9** subcommands.

#### Global Options
| Flag | Description |
| :--- | :--- |
| `--config PATH` | Custom DGXCoder config (`.yaml` / `.json`) |
| `--sandbox {none,apptainer,podman,docker}` | Rootless sandbox (Goose sessions) |
| `--agent {goose,cline}` | Primary agent runner (default: `goose`) |
| `--hf-token TOKEN` | HuggingFace token (else `HF_TOKEN` / `DGXCODER_HF_TOKEN`) |

#### Subcommand Summary
| Subcommand | Description |
| :--- | :--- |
| **`init`** | Pre-download models, save `.dgxcoder/config.yaml`, write Goose config, force-index workspace |
| **`chat`** | Interactive session (Goose) or launch VS Code+Cline |
| **`run`** | Non-interactive Goose task, or Cline launch with printed prompt |
| **`status`** | Rich panels: hardware, vLLM/agent, context index |
| **`serve`** | Foreground vLLM server (multi-tier launch) |
| **`index`** | AST + FTS5 + TF-IDF workspace index |
| **`mcp`** | Stdio MCP server for IDE companion tools |
| **`download`** | Pre-download model weights to HF cache |
| **`web`** | Web Canvas UI on port 8501 (default) |

#### Command Specification Subsections

##### 5.1.1. `dgxcoder init [--model MODEL] [--draft-model DRAFT_MODEL] [--vllm-host HOST] [--sandbox …] [--agent …] [--hf-token …]`
* **Behavior**: Downloads primary/draft weights → `save_config()` → `ensure_goose_config()` → `ContextEngine.index_workspace(force_reindex=True)`.
* **Example**: `dgxcoder init --model qwen2.5-coder-32b --draft-model qwen2.5-coder-1.5b --agent goose`

##### 5.1.2. `dgxcoder chat [--model MODEL] [--draft-model DRAFT_MODEL] [--agent goose|cline] [--sandbox …] [--hf-token …] [--debug]`
* **Behavior**: Selects `GooseRunner` or `ClineRunner` from `config.agent_runner`, then `run_session(debug=…)`. Goose follows [§4.5](#45-session-startup-process); Cline follows the Cline path.
* **Example**: `dgxcoder chat --agent goose --debug`

##### 5.1.3. `dgxcoder run "PROMPT" [--model MODEL] [--draft-model DRAFT_MODEL] [--agent …] [--sandbox …] [--hf-token …] [--debug]`
* **Behavior**: Same runner selection; Goose executes `goose run --text "<prompt>"`; Cline prints the prompt and opens VS Code.
* **Example**: `dgxcoder run "Refactor database connection pool to use async pg"`

##### 5.1.4. `dgxcoder status`
* **Behavior**: Panels for:
  * **Hardware**: GB10 qualification, GPU name, driver, total/used/available unified memory, architecture (no VRAM row).
  * **vLLM & Agent**: endpoint health, served models, active agent (`goose`/`cline`), configured/draft model, sandbox, HF token presence, prefix/chunked label, `num_scheduler_steps`, `kv_cache_dtype`, Goose install state, Cline extension state, config paths.
  * **Context**: indexed file count, AST symbol count, JSON + SQLite paths (if index loaded).

##### 5.1.5. `dgxcoder serve [--model MODEL] [--port PORT] [--quantization QUANT] [--draft-model DRAFT] [--num-speculative-tokens N] [--hf-token …] [--num-scheduler-steps N] [--attention-backend …] [--kv-cache-dtype …]`
* **Behavior**: Foreground `start_server` as in [serve variant](#dgxcoder-serve-variant). Pass `--model` explicitly; bare `serve` currently forwards `model=None` from argparse.
* **Example**: `dgxcoder serve --model qwen2.5-coder-32b --draft-model qwen2.5-coder-1.5b --port 8000`

##### 5.1.6. `dgxcoder index [--dir PATH] [--force]`
* **Behavior**: Indexes workspace (Python AST + full-text FTS + in-memory TF-IDF); persists `.dgxcoder/context_index.json` and `.dgxcoder/context.db`.
* **Example**: `dgxcoder index --force`

##### 5.1.7. `dgxcoder mcp`
* **Behavior**: Stdio JSON-RPC MCP server. Tools: `ide_get_active_editor`, `ide_get_diagnostics`, `ide_get_open_files`, `ide_open_file`, `ide_apply_diff`, `workspace_search_code`. IDE fields live in in-process `IDEState` (empty unless populated by a companion); `workspace_search_code` uses `ContextEngine.search_code`.

##### 5.1.8. `dgxcoder web [--port PORT]`
* **Behavior**: HTTP server on `0.0.0.0:{port}` (default `8501`). Serves static Glassmorphism SPA + `GET /api/status` (`hardware`, `vllm`, `context`). Memory gauge updates from telemetry; Mermaid diagram and diff pane are **static placeholders**; KV gauge shows fixed `45%` width when vLLM is healthy.
* **Example**: `dgxcoder web --port 8501`

##### 5.1.9. `dgxcoder download [--model MODEL] [--all]`
* **Behavior**: Pre-downloads into `~/.cache/huggingface/hub/`. Without `--all`, downloads `args.model or config.model` and optional draft. `--all` iterates **sequentially** over all `compatible_gb10` matrix entries. Also invoked automatically from `init` and `start_server`.
* **Example**: `dgxcoder download --model qwen2.5-coder-32b`

#### Configuration Hierarchy & Resolution Order
1. **CLI parameters** (`--config`, `--model`, `--agent`, `--sandbox`, …) — highest
2. **Environment variables** (`DGXCODER_*`, `HF_TOKEN`, …)
3. **Config file** — `.dgxcoder/config.yaml` (or `.json`) if present, else `~/.config/dgxcoder/config.yaml`, else default path `.dgxcoder/config.yaml`
4. **Built-in defaults** — lowest

#### DGXCoder Config File (defaults example)
```yaml
vllm_host: http://localhost:8000
model: qwen2.5-coder-32b
draft_model: null
num_speculative_tokens: 5
sandbox: none
agent_runner: goose
hf_token: null
enable_prefix_caching: true
enable_chunked_prefill: true
num_scheduler_steps: 8
attention_backend: auto
kv_cache_dtype: auto
```

#### Environment Variables
| Variable | Description | Default |
| :--- | :--- | :--- |
| `DGXCODER_CONFIG_PATH` | Custom config file path | (resolver default) |
| `DGXCODER_VLLM_HOST` | vLLM endpoint URL | `http://localhost:8000` |
| `DGXCODER_MODEL` | Primary model alias | `qwen2.5-coder-32b` |
| `DGXCODER_DRAFT_MODEL` | Draft model alias | unset |
| `DGXCODER_SPECULATIVE_TOKENS` | Speculative token count | `5` |
| `DGXCODER_SANDBOX` | Sandbox engine | `none` |
| `DGXCODER_AGENT` / `DGXCODER_RUNNER` | Agent runner (`goose` \| `cline`) | `goose` |
| `HF_TOKEN` / `DGXCODER_HF_TOKEN` | HuggingFace token | unset |
| `GOOSE_PROVIDER` | Set for Goose processes | `openai` |
| `OPENAI_HOST` | Set for Goose processes | `{vllm_host}` |
| `OPENAI_BASE_PATH` | Set for Goose processes | `v1` |
| `OPENAI_API_KEY` | Set for Goose processes | `gb10-local-token` |
| `GOOSE_MODEL` | Set for Goose processes | `{model}` |

Tuning keys `enable_prefix_caching`, `enable_chunked_prefill`, `num_scheduler_steps`, `attention_backend`, `kv_cache_dtype` are config-file / CLI only (no dedicated `DGXCODER_*` env vars).

### 5.2. IDE Integration via Stdio MCP
Registered in Goose config as stdio extension (`cmd: dgxcoder`, `args: [mcp]`). This repository ships the **MCP server**, not a JetBrains plugin or VS Code extension package. Editor tools read/write in-memory `IDEState`; search uses the local context engine.

### 5.3. Web Canvas UI & Telemetry Pane
`dgxcoder web --port 8501`:
* Glassmorphism dark SPA (CDN Mermaid).
* Static architecture Mermaid snippet and placeholder diff lines.
* Live unified-memory gauge from `/api/status`; illustrative KV fill when healthy.
* REST: `GET /api/status` → `{ hardware, vllm, context }`.

---

## 6. System Requirements & Setup

### Requirements
* **System**: 1x NVIDIA GB10 (Blackwell, 128 GB Unified Memory) — or host with ≥100 GB RAM for detection fallback
* **OS**: Linux ARM64 (Ubuntu 22.04 LTS or compatible)
* **Drivers**: NVIDIA Linux Driver 580+ / CUDA 13.x (typical GB10 stack)
* **Dependencies**: Python 3.10+, PyYAML, Rich, Requests; optional Docker for containerized vLLM; optional VS Code/`codium` for Cline

### Identifying Your Hardware Variant
```bash
nvidia-smi --query-gpu=name --format=csv
free -h
```
* Qualified when GPU name contains `GB10`/`BLACKWELL`, or when total memory ≥ ~100 GiB per detection heuristic.

### Quickstart Installation
```bash
git clone https://github.com/dgxcoder/dgxcoder.git
cd dgxcoder
./scripts/install_gb10.sh qwen2.5-coder-32b   # positional MODEL arg (default qwen2.5-coder-32b)
dgxcoder status
dgxcoder chat
```

`scripts/install_gb10.sh`:
1. Installs Goose via `releases/latest/download/download_cli.sh` (falls back to `pip install goose-ai`).
2. `pip install -e .`
3. `dgxcoder init --model "${1:-qwen2.5-coder-32b}"`

Note: runtime Goose auto-install uses `releases/download/stable/…`; the install script uses `releases/latest/…`.

### Helper Scripts
| Script | Role |
| :--- | :--- |
| `scripts/install_gb10.sh [MODEL]` | Package + Goose + `dgxcoder init` |
| `scripts/run_vllm_gb10.sh [MODEL] [PORT] [DRAFT] [TOKENS]` | Thin foreground Python-module vLLM launch (no prefix-cache / chunked-prefill / kv-cache flags) |
| `scripts/run_goose.sh` | Sets Goose OpenAI env vars and runs `goose session` |

Prefer `dgxcoder serve` / `dgxcoder chat` for full GB10-tuned behavior.

---

## 7. Roadmap & Implementation Verification

- [x] **Phase 1: NVIDIA GB10 Exclusive Specification** — model matrix & unified-memory targeting
- [x] **Phase 2: GB10 Inference Pipeline & Auto-Launch Engine** — multi-tier vLLM, live logs, weight pre-download, CLI suite including `download`
- [x] **Phase 3: Agentic Engine, Provisioning & MCP** — Goose auto-install, stdio MCP tools, optional Cline/VS Code path
- [x] **Phase 4: Context Engine & Web Canvas** — parallel AST, SQLite/FTS5, TF-IDF (in-process), Web Canvas telemetry UI
- [x] **Phase 5: Modular Package Layout** — split packages under `dgxcoder/{hardware,config,runner,vllm_server,context_engine,mcp_server,cli}/` with shim modules for stable imports

---

## 8. Codebase Architecture & Source Reference

Package version: `dgxcoder.__version__ == "1.2.0"`. Top-level `dgxcoder/*.py` modules are thin re-export shims; implementations live in subpackages.

```
dgxcoder/
├── __init__.py                 # __version__ = "1.2.0"
├── cli.py                      # shim → cli package
├── cli/
│   └── dgxcoder_cli_controller.py   # DGXCoderCLIController (9 subcommands)
├── config.py                   # shim
├── config/
│   ├── config_path_resolver.py
│   ├── config_file_storage_manager.py
│   └── dgxcoder_config.py
├── context_engine.py           # shim
├── context_engine/
│   ├── context_engine.py
│   ├── ast_symbol_extractor.py
│   ├── tfidf_calculator.py
│   ├── sqlite_context_storage.py
│   ├── code_symbol.py
│   └── indexed_file.py
├── hardware.py                 # shim + detect/download helpers
├── hardware/
│   ├── model_matrix_registry.py
│   ├── model_downloader.py
│   ├── hardware_manager.py
│   ├── model_spec.py
│   ├── memory_metrics.py
│   └── hardware_telemetry.py
├── mcp_server.py               # shim
├── mcp_server/
│   ├── mcp_server.py
│   ├── mcp_tool_registry.py
│   ├── ide_state.py
│   └── editor_selection.py
├── runner.py                   # shim
├── runner/
│   ├── goose_runner.py         # wait_for_vllm + Goose session
│   ├── goose_installer.py
│   ├── sandbox_manager.py
│   ├── cline_runner.py
│   └── cline_installer.py
├── vllm_server.py              # shim
├── vllm_server/
│   ├── vllm_server_manager.py
│   ├── vllm_log_streamer.py
│   ├── vllm_launch_options.py
│   └── vllm_server_status.py
└── web_canvas.py               # CanvasHandler + start_web_canvas_server
```

### 8.1. `dgxcoder/hardware/`
GB10 detection, model matrix, speculative memory checks, HF cache downloads.

### 8.2. `dgxcoder/config/`
4-tier config merge, Goose YAML sync, env vars for Goose (`OPENAI_*`, `GOOSE_*`).

### 8.3. `dgxcoder/runner/`
Session startup orchestration across 5 AI Agent runners (`goose` [default], `cline`, `aider`, `continue`, `openhands`), sandbox prefixes (`Apptainer`, `Podman`, `Docker`), and automatic binary/extension provisioning (`GooseInstaller`, `ClineInstaller`, `AiderInstaller`, `ContinueInstaller`, `OpenHandsInstaller`).

### 8.4. `dgxcoder/vllm_server/`
Multi-tier launch, pre-download, health checks, background log queue.

### 8.5. `dgxcoder/context_engine/`
Parallel index, SQLite/FTS5, TF-IDF, hybrid `search_code`.

### 8.6. `dgxcoder/mcp_server/`
Async stdio MCP + tool registry + in-memory `IDEState`.

### 8.7. `dgxcoder/web_canvas.py`
HTTP SPA + `/api/status` telemetry.

### 8.8. `dgxcoder/cli/`
`DGXCoderCLIController` — argparse, Rich status, subcommand dispatch (`main()` → `run_cli()`).
