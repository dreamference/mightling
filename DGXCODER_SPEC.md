# DGXCoder Technical Specification

> **Version:** 1.2.0  
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
  - [4.3. Goose Agentic Loop & Automatic CLI Provisioning](#43-goose-agentic-loop--automatic-cli-provisioning)
  - [4.4. Codebase Context Engine (AST + Vector Index)](#44-codebase-context-engine-ast--vector-index)
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
  - [5.2. JetBrains & VS Code Integration (via Stdio MCP)](#52-jetbrains--vs-code-integration-via-stdio-mcp)
  - [5.3. Web Canvas UI & Live Diff / Telemetry Pane](#53-web-canvas-ui--live-diff--telemetry-pane)
- [6. System Requirements & Setup](#6-system-requirements--setup)
  - [Requirements](#requirements)
  - [Identifying Your Hardware Variant](#identifying-your-hardware-variant)
  - [Quickstart Installation](#quickstart-installation)
- [7. Roadmap & Implementation Verification](#7-roadmap--implementation-verification)
- [8. Codebase Architecture & Source Reference](#8-codebase-architecture--source-reference)
  - [8.1. `dgxcoder/hardware.py`](#81-dgxcoderhardwarepy)
  - [8.2. `dgxcoder/config.py`](#82-dgxcoderconfigpy)
  - [8.3. `dgxcoder/runner.py`](#83-dgxcoderrunnerpy)
  - [8.4. `dgxcoder/vllm_server.py`](#84-dgxcodervllm_serverpy)
  - [8.5. `dgxcoder/context_engine.py`](#85-dgxcodercontext_enginepy)
  - [8.6. `dgxcoder/mcp_server.py`](#86-dgxcodermcp_serverpy)
  - [8.7. `dgxcoder/web_canvas.py`](#87-dgxcoderweb_canvaspy)
  - [8.8. `dgxcoder/cli.py`](#88-dgxcoderclipy)

---

## 1. Executive Summary & Vision

**DGXCoder** is an open-source, enterprise-grade, agentic AI software development platform engineered exclusively to run on an **NVIDIA GB10 system** (Blackwell architecture with 128 GB of Unified Memory). Inspired by platforms like Google Antigravity, DGXCoder delivers end-to-end autonomous pair-programming, codebase AST & vector indexing, multi-agent orchestration, and localized code synthesis with 100% data sovereignty, zero cloud egress, and zero external cluster dependencies.

By leveraging the integrated SoC architecture of the NVIDIA GB10 (Blackwell GPU paired with high-performance ARM Cortex CPU host sharing **128 GB of high-speed Unified LPDDR5X Memory**), DGXCoder hosts state-of-the-art open coding LLMs with high generation speeds and low latency.

```
+-----------------------------------------------------------------------------------+
|                                  DGXCoder Clients                                 |
|   +-----------------------+   +------------------------+   +-------------------+  |
|   |  `dgxcoder` CLI       |   | JetBrains / VS Code    |   | Web Canvas UI     |  |
|   |  Terminal Interface   |   | MCP Companion Plugin   |   | Interactive App   |  |
|   +-----------+-----------+   +-----------+------------+   +---------+---------+  |
+---------------+---------------------------+--------------------------+------------+
                                            | Model Context Protocol (MCP)
+-------------------------------------------v---------------------------------------+
|                       Goose Agent Execution Engine (AAIF)                         |
|  +--------------------+  +--------------------+  +------------------------------+ |
|  | Context Engine     |  | Goose Controller   |  | MCP Tool Execution Runtime   | |
|  | AST + Vector Index |  | Session & Prompts  |  | Sandboxed Container Runner   | |
|  +--------------------+  +--------------------+  +------------------------------+ |
+-------------------------------------------+---------------------------------------+
|                                           | Local Async Requests (vLLM / TRT-LLM OpenAI API)
+-------------------------------------------v---------------------------------------+
|                  NVIDIA GB10 Hardware & Inference Engine                          |
|  +------------------------------------------------------------------------------+  |
|  | vLLM / TensorRT-LLM Engine (BF16 / INT8 / FP8 / INT4 Quantization)            |  |
|  | PagedAttention + FlashAttention-2 / Unified Memory Management                |  |
|  +------------------------------------------------------------------------------+  |
|  | GB10 Open Models: Qwen 2.5 Coder 32B/72B (INT8) | DeepSeek-R1-Distill 32B/70B  |  |
|  |                   Llama 3.3 70B (INT8) | StarCoder2 15B                     |  |
|  +------------------------------------------------------------------------------+  |
|  | Hardware: 1x NVIDIA GB10 (Blackwell Architecture | 128 GB Unified Memory)     |  |
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
| **Interfaces** | Antigravity IDE, CLI, Desktop | **CLI (`dgxcoder`), JetBrains Plugin, VS Code Extension, Web Canvas** |

---

## 3. Supported NVIDIA GB10 Model Matrix

All supported models are qualified to run on a single **NVIDIA GB10 system (128 GB Unified Memory)**:

| Model | Parameters | Precision / Quantization | Memory Required | NVIDIA GB10 Compatibility |
| :--- | :--- | :--- | :--- | :--- |
| **Qwen 2.5 Coder 32B** | 32B | BF16 / INT8 / FP8 | ~35 - 64 GB | ✅ Fits comfortably in 128GB Unified Memory |
| **Qwen 2.5 Coder 72B** | 72B | INT8 / FP8 / INT4 | ~45 - 80 GB | ✅ Supported (INT8/FP8 quantized fit) |
| **Qwen 2.5 Coder 1.5B (Draft)** | 1.5B | BF16 / FP16 / INT8 | ~3.5 - 6 GB | ✅ Ideal Speculative Decoding Draft Model |
| **Qwen 2.5 Coder 3B (Draft)** | 3.0B | BF16 / FP16 / INT8 | ~6.5 - 10 GB | ✅ Ideal Speculative Decoding Draft Model |
| **DeepSeek-R1-Distill-Qwen-32B** | 32B | BF16 / INT8 / FP8 | ~35 - 64 GB | ✅ Fits comfortably in 128GB Unified Memory |
| **DeepSeek-R1-Distill-Llama-70B** | 70B | INT8 / FP8 / INT4 | ~45 - 80 GB | ✅ Supported (INT8/FP8 quantized fit) |
| **Llama 3.3 70B Instruct** | 70B | INT8 / FP8 | ~75 GB | ✅ Supported (INT8/FP8 quantized fit) |
| **StarCoder2 15B** | 15B | BF16 / FP16 | ~20 - 30 GB | ✅ Fits easily |
| **DeepSeek-V3 671B** | 671B (MoE) | INT4 (AWQ/GPTQ) | ~350 GB | ❌ Exceeds 128GB (Requires multi-node or >128GB hardware) |

---

## 4. Hardware Integration & Optimization

### 4.1. NVIDIA GB10 Hardware Specification
* **GPU**: NVIDIA GB10 Tensor Core GPU (Blackwell architecture).
* **Unified Memory**: 128 GB LPDDR5X high-speed unified memory shared dynamically between CPU and GPU.
* **CPU Host**: High-performance ARM Cortex CPU cores (`aarch64` architecture).
* **Storage**: NVMe PCIe SSD for high-speed workspace indexing and model caching.

### 4.2. GB10 Inference Stack & Speculative Decoding Engine
* **Multi-Tiered Launch Resolution**:
  1. **Native CLI**: Uses `vllm serve <model>` if `vllm` CLI binary is available.
  2. **Python Module**: Uses `python -m vllm.entrypoints.openai.api_server` if `vllm` package is installed.
  3. **Docker Container Fallback**: Uses `docker run --rm --gpus all -p 8000:8000 vllm/vllm-openai:latest` when local python vLLM package is absent.
* **Dual-Model Speculative Decoding Pipeline**:
  - Leverages GB10 128GB Unified Memory to run a primary target model (e.g. `Qwen 2.5 Coder 32B/72B`) alongside a lightweight draft model (e.g. `Qwen 2.5 Coder 1.5B/3B`).
  - Appends `--speculative-model <draft_model> --num-speculative-tokens <tokens>` to boost inference generation speed by 2x–3x.
* **GB10 Launch Flags**: `--host 0.0.0.0 --port 8000 --max-model-len 16384 --gpu-memory-utilization 0.90 --trust-remote-code --enforce-eager --kv-cache-dtype auto`.
* **Readiness Polling & Live Streaming**: Automatically launches vLLM in background if offline when `dgxcoder chat` or `run` starts, streaming live `[vLLM]` output logs into the terminal until HTTP 200 OK is returned.
* **Instant Signal Handling**: Polling loop operates on 0.1s sub-second sleep slices to handle `Ctrl+C` (`SIGINT`) instantly.

### 4.3. Goose Agentic Loop & Rootless Container Sandboxing
* **Execution Runtime**: Goose AI Agent (`aaif-goose/goose` v1.45+).
* **Auto-Installation**: Automatically provisions official AAIF Goose binary from `https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh` if missing from system `PATH`.
* **Executable Resolution**: Detects `goose` in `PATH`, `~/.local/bin/goose`, `~/.goose/bin/goose`, and `sys.prefix/bin/goose`.
* **OpenAI-Compatible Bridge**: Configures `~/.config/goose/config.yaml` to point to `http://localhost:8000/v1` with token `gb10-local-token`.
* **Rootless Container Sandbox Isolation**:
  - Supports `--sandbox {none,apptainer,podman,docker}` to isolate subagent tool executions (shell commands, package installs, test runs) from the host system.
  - **Apptainer**: `apptainer exec --writable-tmpfs --bind $(pwd):/workspace docker://ubuntu:22.04` (Unprivileged user namespace isolation).
  - **Podman**: `podman run --rm -it -v $(pwd):/workspace:Z -w /workspace ubuntu:22.04` (Rootless OCI container execution).
  - **Docker**: `docker run --rm -it -v $(pwd):/workspace -w /workspace ubuntu:22.04` (Containerized workspace execution).

### 4.4. Codebase Context Engine (AST + Vector Index)
* **AST Extraction**: Parses Python AST to extract class definitions, method signatures, function arguments, docstrings, and line ranges.
* **TF-IDF Semantic Vector Index**: Tokenizes identifiers, camelCase, and snake_case terms to perform air-gapped zero-egress local semantic retrieval.
* **Index Cache**: Persists workspace metadata at `.dgxcoder/context_index.json`.

---

## 5. Client Interfaces & Developer Experience

### 5.1. `dgxcoder` CLI Suite
Terminal application powered by `Rich` and `Goose`.

#### Subcommand Summary:
| Subcommand | Description |
| :--- | :--- |
| **`init`** | Initializes `.dgxcoder` workspace, generates `~/.config/goose/config.yaml`, and indexes AST symbols. |
| **`chat`** | Launches interactive pair-programming session with Goose AI agent on local GB10 endpoint. |
| **`run`** | Executes autonomous coding prompt non-interactively using Goose. |
| **`status`** | Displays Rich visual panel of GB10 hardware memory, vLLM endpoint health, Goose state, and context index stats. |
| **`serve`** | Launches local vLLM server optimized for GB10 unified memory (supports Docker fallback). |
| **`index`** | Indexes workspace codebase AST definitions and TF-IDF vector context. |
| **`mcp`** | Runs stdio Model Context Protocol (MCP) server for JetBrains & VS Code extensions. |
| **`web`** | Launches interactive Web Canvas UI pane for live diffs and hardware monitoring. |

#### Command Specification Subsections:

##### 5.1.1. `dgxcoder init [--model MODEL] [--draft-model DRAFT_MODEL] [--vllm-host HOST]`
Initializes the current project workspace for DGXCoder agentic pair-programming.
* **Behavior**: Generates local `.dgxcoder/config.yaml`, writes or updates Goose AI Agent config at `~/.config/goose/config.yaml`, registers stdio MCP companion tools, and triggers an initial workspace AST symbol and vector index.
* **Options**:
  * `--model MODEL`: Target LLM served on vLLM GB10 endpoint (default: `qwen2.5-coder-32b`).
  * `--draft-model DRAFT_MODEL`: Optional speculative decoding draft model name (default: `None`).
  * `--vllm-host HOST`: Base URL of local vLLM API endpoint (default: `http://localhost:8000`).
* **Example**: `dgxcoder init --model qwen2.5-coder-32b --draft-model qwen2.5-coder-1.5b`

##### 5.1.2. `dgxcoder chat [--model MODEL] [--draft-model DRAFT_MODEL] [--debug]`
Launches an interactive pair-programming session with the Goose AI agent connected to the local GB10 endpoint.
* **Behavior**: Validates model compatibility on GB10 unified memory. If the local vLLM server is offline, automatically launches vLLM in the background and streams live `[vLLM]` startup logs until ready. Provisions official AAIF Goose binary if missing.
* **Options**:
  * `--model MODEL`: Override target LLM model name for session.
  * `--draft-model DRAFT_MODEL`: Speculative decoding draft model name.
  * `--debug`: Enable verbose Goose debug logging.
* **Example**: `dgxcoder chat --debug`

##### 5.1.3. `dgxcoder run "PROMPT" [--model MODEL] [--draft-model DRAFT_MODEL] [--debug]`
Executes an autonomous coding task non-interactively using Goose AI Agent.
* **Behavior**: Takes a single task prompt string, initializes vLLM/Goose bridges as needed, executes the requested code generation or refactoring task autonomously, and exits upon completion.
* **Options**:
  * `PROMPT`: Mandatory instruction or prompt string for the agent.
  * `--model MODEL`: Override target LLM model name.
  * `--draft-model DRAFT_MODEL`: Speculative decoding draft model.
  * `--debug`: Enable verbose Goose debug output.
* **Example**: `dgxcoder run "Refactor database connection pool to use async pg"`

##### 5.1.4. `dgxcoder status`
Displays Rich visual status panels summarizing GB10 hardware, inference server state, agent runtime, and context index statistics.
* **Behavior**: Queries `nvidia-smi` and `/proc/meminfo` to display GB10 unified memory usage (RAM/VRAM/total), polls vLLM health status, displays active served models, checks Goose CLI installation, and shows total indexed workspace AST symbols.
* **Options**: None.
* **Example**: `dgxcoder status`

##### 5.1.5. `dgxcoder serve [--model MODEL] [--port PORT] [--quantization QUANT] [--draft-model DRAFT_MODEL] [--num-speculative-tokens TOKENS]`
Launches the local vLLM GB10 inference server with unified memory optimizations.
* **Behavior**: Uses a multi-tiered launch resolution strategy (native `vllm` CLI > Python `vllm` module > Docker `vllm/vllm-openai:latest` container). Configures GB10 unified memory flags (`--gpu-memory-utilization 0.90`, `--max-model-len 16384`, `--kv-cache-dtype auto`). Supports dual-model speculative decoding (`--speculative-model` and `--num-speculative-tokens`).
* **Options**:
  * `--model MODEL`: Model name to load and serve (default: `qwen2.5-coder-32b`).
  * `--port PORT`: Port to expose OpenAI-compatible HTTP API (default: `8000`).
  * `--quantization QUANT`: Optional quantization precision (`int8`, `fp8`, `awq`).
  * `--draft-model DRAFT_MODEL`: Optional speculative decoding draft model name.
  * `--num-speculative-tokens TOKENS`: Number of speculative draft tokens to propose (default: `5`).
* **Example**: `dgxcoder serve --model qwen2.5-coder-32b --draft-model qwen2.5-coder-1.5b --port 8000`

##### 5.1.6. `dgxcoder index [--dir PATH] [--force]`
Indexes codebase AST symbol definitions and TF-IDF vector context.
* **Behavior**: Scans source files in workspace, parses Python AST (extracting class/function signatures, docstrings, line ranges), builds a TF-IDF term index, and saves cached index to `.dgxcoder/context_index.json`.
* **Options**:
  * `--dir PATH`: Root directory to index (default: current workspace).
  * `--force`: Force full reindexing from scratch ignoring cache.
* **Example**: `dgxcoder index --force`

##### 5.1.7. `dgxcoder mcp`
Runs stdio Model Context Protocol (MCP) server for JetBrains (PyCharm, IntelliJ) and VS Code IDE companion extensions.
* **Behavior**: Handles JSON-RPC 2.0 requests over stdin/stdout. Exposes IDE diagnostic tools (`ide_get_diagnostics`, `ide_get_active_editor`, `ide_get_open_files`, `ide_open_file`, `ide_apply_diff`, `workspace_search_code`) to the Goose agent.
* **Options**: None.
* **Example**: `dgxcoder mcp`

##### 5.1.8. `dgxcoder web [--port PORT]`
Launches interactive Web Canvas UI server.
* **Behavior**: Starts lightweight HTTP server rendering single-page Glassmorphism UI with live Mermaid.js architecture diagrams, code diff stream, and real-time GB10 unified memory gauges. Exposes REST telemetry endpoint (`GET /api/status`).
* **Options**:
  * `--port PORT`: Port to expose Web Canvas UI (default: `8501`).
* **Example**: `dgxcoder web --port 8501`

#### Configuration Hierarchy & Resolution Order:
DGXCoder supports a 4-tier configuration precedence hierarchy:
1. **Command Line Parameters** (`--config`, `--model`, `--vllm-host`, `--draft-model`) - *Highest Priority*
2. **Environment Variables** (`DGXCODER_CONFIG_PATH`, `DGXCODER_MODEL`, `DGXCODER_VLLM_HOST`, etc.)
3. **DGXCoder Config File** (`.dgxcoder/config.yaml` or `~/.config/dgxcoder/config.yaml`)
4. **Built-in System Defaults** - *Lowest Priority*

#### DGXCoder Config File (`.dgxcoder/config.yaml`):
```yaml
vllm_host: http://localhost:8000
model: qwen2.5-coder-32b
draft_model: qwen2.5-coder-1.5b
num_speculative_tokens: 5
sandbox: apptainer
hf_token: hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

#### Environment Variables:
| Variable | Description | Default |
| :--- | :--- | :--- |
| `DGXCODER_CONFIG_PATH` | Path to custom DGXCoder configuration file | `.dgxcoder/config.yaml` |
| `DGXCODER_VLLM_HOST` | Local vLLM server endpoint URL | `http://localhost:8000` |
| `DGXCODER_MODEL` | Default LLM model name served on GB10 | `qwen2.5-coder-32b` |
| `DGXCODER_DRAFT_MODEL` | Speculative decoding draft model name | `None` (Disabled) |
| `DGXCODER_SPECULATIVE_TOKENS` | Number of speculative draft tokens | `5` |
| `DGXCODER_SANDBOX` | Subagent rootless container sandbox | `none` |
| `HF_TOKEN` / `DGXCODER_HF_TOKEN` | HuggingFace API access token for vLLM downloads | `None` |
| `GOOSE_PROVIDER` | Provider setting passed to Goose | `openai` |
| `OPENAI_HOST` | Host URL for local OpenAI-compatible vLLM API | `http://localhost:8000` |
| `OPENAI_BASE_PATH` | Base path for vLLM API | `v1` |

### 5.2. JetBrains & VS Code Integration (via Stdio MCP)
Registered as stdio MCP server (`dgxcoder mcp`) in `~/.config/goose/config.yaml`.
* **Tools Exposed**:
  * `ide_get_active_editor`: Active file path, line, column, selection.
  * `ide_get_diagnostics`: Workspace linter errors and diagnostics.
  * `ide_get_open_files`: Currently open editor tab paths.
  * `ide_open_file`: Command to open target file at line:column.
  * `ide_apply_diff`: Inline code diff proposal renderer.
  * `workspace_search_code`: Local AST & TF-IDF semantic code search.

### 5.3. Web Canvas UI & Live Diff / Telemetry Pane
Single-page web application (`dgxcoder web --port 8501`) featuring:
* Glassmorphism dark aesthetic (`Inter` + `Fira Code` typography, HSL/vibrant accent palette).
* Live Mermaid.js architecture diagrams.
* Active streaming diff view.
* Real-time GB10 unified memory utilization and KV-cache allocation gauges.
* REST telemetry endpoint (`GET /api/status`).

---

## 6. System Requirements & Setup

### Requirements
* **System**: 1x NVIDIA GB10 System (Blackwell Architecture, 128 GB Unified Memory)
* **OS**: Linux ARM64 (Ubuntu 22.04 LTS or compatible)
* **Drivers**: NVIDIA Linux Driver 580+ / CUDA 13.x
* **Dependencies**: Python 3.10+, PyYAML, Rich, Requests, Docker 24+ (optional for containerized vLLM serving)

### Identifying Your Hardware Variant
```bash
nvidia-smi --query-gpu=name --format=csv
free -h
```
* **NVIDIA GB10 System**: `nvidia-smi` returns `NVIDIA GB10` and system memory displays ~`128Gi` total unified memory.

### Quickstart Installation
```bash
git clone https://github.com/dgxcoder/dgxcoder.git
cd dgxcoder
./scripts/install_gb10.sh --model qwen2.5-coder-32b
dgxcoder status
dgxcoder chat
```

---

## 7. Roadmap & Implementation Verification

- [x] **Phase 1: NVIDIA GB10 Exclusive Specification**
  - Scope memory budgets, model matrices, and unified memory architecture exclusively to NVIDIA GB10 (128 GB Unified Memory) hardware.
- [x] **Phase 2: GB10 Inference Pipeline & Auto-Launch Engine**
  - Implement multi-tiered vLLM deployment engine optimized for GB10 unified memory with Docker container fallback and live log streaming.
  - Implement complete `dgxcoder` CLI suite (`init`, `chat`, `run`, `status`, `serve`, `index`, `mcp`, `web`).
- [x] **Phase 3: Agentic Engine, Automatic Provisioning & MCP Integration**
  - Implement Goose auto-installer (`aaif-goose/goose` 1.45+) and stdio MCP bridge (`ide_get_diagnostics`, `ide_get_active_editor`, `workspace_search_code`).
- [x] **Phase 4: AST/Vector Context Engine & Web Canvas UI**
  - Build local AST symbol parser & TF-IDF vector code indexer (`.dgxcoder/context_index.json`).
  - Build HTML/CSS/JS Glassmorphism Web Canvas UI for live diagramming, diff visualization, and hardware monitoring.

---

## 8. Codebase Architecture & Source Reference

The DGXCoder software stack is organized into modular Python components:

```
dgxcoder/
├── __init__.py           # Package initialization & version (1.2.0)
├── cli.py               # Rich CLI application with 8 subcommands
├── config.py            # 4-Tier configuration hierarchy & Goose sync
├── context_engine.py    # Zero-egress AST symbol parser & TF-IDF vector indexer
├── hardware.py          # NVIDIA GB10 hardware detection & model qualification matrix
├── mcp_server.py        # Stdio Model Context Protocol (MCP) JSON-RPC 2.0 server
├── runner.py            # Goose AI agent runner, auto-installer & vLLM waiting supervisor
├── vllm_server.py       # Multi-tiered vLLM server launcher & live log queueing engine
└── web_canvas.py        # Single-page Glassmorphism Web Canvas UI server
```

### 8.1. `dgxcoder/hardware.py`
Provides NVIDIA GB10 hardware detection, system memory profiling via `/proc/meminfo`, and model compatibility checks for dual-model speculative decoding.

```python
# Key Functions & Data Structures
MODEL_MATRIX: Dict[str, ModelSpec]
def detect_gb10_hardware() -> Dict[str, Any]
def check_model_compatibility(model_key: str) -> Tuple[bool, str]
def check_speculative_compatibility(main_model_key: str, draft_model_key: str) -> Tuple[bool, str]
```

### 8.2. `dgxcoder/config.py`
Implements the 4-tier configuration precedence hierarchy (CLI Args > Env Vars > Config File > Defaults) and writes `~/.config/goose/config.yaml`.

```python
class DGXCoderConfig:
    def __init__(self, config_file=None, vllm_host=None, model=None, draft_model=None, num_speculative_tokens=None)
    def save_config(self, target_path=None) -> Path
    def ensure_goose_config(self, extra_mcp_servers=None) -> None
```

### 8.3. `dgxcoder/runner.py`
Supervisor for launching Goose sessions, auto-provisioning official AAIF Goose 1.45+ binaries, and executing `wait_for_vllm()` with sub-second signal handling.

```python
class GooseRunner:
    def is_goose_installed(self) -> bool
    def install_goose(self) -> bool
    def wait_for_vllm(self, poll_interval=1.0, max_wait=None, auto_launch=True) -> bool
    def run_session(self, prompt=None, debug=False) -> int
```

### 8.4. `dgxcoder/vllm_server.py`
Multi-tiered vLLM server launcher (Native CLI > Python Module > Docker Container) with background thread log queueing and non-blocking streaming.

```python
class VLLMServerManager:
    def check_health(self, timeout=0.5) -> bool
    def build_launch_command(...) -> List[str]
    def start_server(...) -> Optional[subprocess.Popen]
    def get_new_logs() -> List[str]
```

### 8.5. `dgxcoder/context_engine.py`
Zero-egress local AST symbol extractor and TF-IDF vector code indexer persisting workspace context at `.dgxcoder/context_index.json`.

```python
class ContextEngine:
    def index_workspace(self, force_reindex=False) -> Dict[str, Any]
    def search_code(self, query: str, top_k=5) -> List[Dict[str, Any]]
```

### 8.6. `dgxcoder/mcp_server.py`
Stdio Model Context Protocol (MCP) server for JetBrains and VS Code IDE companion extensions.

```python
class MCPServer:
    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]
    def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any
```

### 8.7. `dgxcoder/web_canvas.py`
Lightweight HTTP server serving the interactive Web Canvas UI for live diffs, Mermaid.js diagrams, and GB10 hardware telemetry.

```python
def start_web_canvas_server(port: int = 8501, daemon: bool = True) -> threading.Thread
```

### 8.8. `dgxcoder/cli.py`
Main entrypoint (`dgxcoder`) providing the Rich terminal user interface for all subcommands (`init`, `chat`, `run`, `status`, `serve`, `index`, `mcp`, `web`).

