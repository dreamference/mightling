# Dreamference Architecture Overview

> - **Version:** 1.2.0 (`dreamference.__version__`)
> - **Status:** Implemented / Production-Ready
> - **Target Hardware:** Exclusive to **NVIDIA GB10** (Blackwell architecture with 128 GB Unified Memory)
> - **Deployment Model:** Single-Node Standalone NVIDIA GB10 System
> - **License:** Open Source (AGPL-3.0-or-later)

---

## 1. Executive Summary & Vision

**Dreamference** is an open-source, enterprise-grade, agentic AI software development platform engineered exclusively to run on an **NVIDIA GB10 system** (Blackwell architecture with 128 GB of Unified Memory). Inspired by platforms like Google Antigravity, Dreamference delivers end-to-end autonomous pair-programming, codebase AST & vector indexing, multi-agent orchestration, and localized code synthesis with 100% data sovereignty, zero cloud egress, and zero external cluster dependencies.

By leveraging the integrated SoC architecture of the NVIDIA GB10 (Blackwell GPU paired with high-performance ARM Cortex CPU host sharing **128 GB of high-speed Unified LPDDR5X Memory**), Dreamference hosts state-of-the-art open coding LLMs with high generation speeds and low latency.

Primary agent runtime is **Goose** (`aaif-goose/goose`). Additional runners selected via `--agent` / `DREAMFERENCE_AGENT`: **Cline**, **Aider**, **Continue**, and **OpenHands** — all targeting the same local vLLM OpenAI-compatible endpoint.

```text
+-----------------------------------------------------------------------------------+
|                                  Dreamference Clients                                 |
|   +----------------------+   +-----------------------+   +--------------------+   |
|   | `dreamference` CLI       |   | Stdio MCP Companion   |   | Web Canvas UI      |   |
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

| Feature                | Google Antigravity                | Dreamference                                                                                         |
| :--------------------- | :-------------------------------- | :----------------------------------------------------------------------------------------------- |
| **Model Hosting**      | Cloud (Google Vertex AI / Gemini) | **100% On-Premise NVIDIA GB10 System**                                                           |
| **Source Code**        | Proprietary                       | **Open Source (AGPL-3.0-or-later)**                                                                     |
| **Supported Models**   | Gemini 1.5 Pro / Flash, Claude    | **Single-Node Open LLMs (Qwen 2.5 Coder, DeepSeek-R1 Distills, Llama 3.3)**                      |
| **Inference Hardware** | Cloud TPUs / GPUs                 | **NVIDIA GB10 (Blackwell Architecture)**                                                         |
| **Data Privacy**       | Cloud Privacy Policy              | **Strict Zero-Egress Air-Gapped Local Execution**                                                |
| **System Memory**      | Cloud Allocation                  | **128 GB Unified LPDDR5X Memory**                                                                |
| **Interfaces**         | Antigravity IDE, CLI, Desktop     | **CLI (`dreamference`); agents Goose / Cline / Aider / Continue / OpenHands; stdio MCP; Web Canvas** |

---

## 3. System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLI & User Interfaces                        │
│  • dream chat/run (interactive agents)                          │
│  • dream server start (vLLM lifecycle)                          │
│  • dream status (hardware/vLLM/context telemetry)               │
│  • dream index (workspace indexing)                             │
└────────────┬────────────────────────────────────────────────────┘
             │
┌────────────v────────────────────────────────────────────────────┐
│                    Config & State Management                    │
│  • 4-tier config resolution (CLI → Env → TOML → defaults)      │
│  • Goose config synthesis & environment setup                  │
│  • HF token management & caching                               │
└────────────┬────────────────────────────────────────────────────┘
             │
┌────────────v────────────────────────────────────────────────────┐
│              Agent Runners (5 strategies)                       │
│  • Goose (default): Native shell + MCP tools                   │
│  • Cline: VS Code extension bridge                             │
│  • Aider: CLI-driven pair programming                          │
│  • Continue: IDE autocomplete & chat                           │
│  • OpenHands: Docker UI for autonomous agents                  │
└────────────┬────────────────────────────────────────────────────┘
             │
┌────────────v────────────────────────────────────────────────────┐
│         Subsystems (parallel to agent runners)                  │
│  • vLLM Server: Docker-based inference management              │
│  • Context Engine: AST + FTS5 + TF-IDF + embeddings            │
│  • MCP Server: IDE state & workspace tools                     │
│  • Hardware Manager: GB10 detection & telemetry                │
└────────────┬────────────────────────────────────────────────────┘
             │
┌────────────v────────────────────────────────────────────────────┐
│       Hardware Layer (NVIDIA GB10 / 128 GB Unified Memory)      │
│  • Local vLLM endpoint (OpenAI-compatible REST API)            │
│  • Blackwell GPU inference + ARM Cortex CPU                    │
│  • Unified LPDDR5X memory model (no discrete copies)           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Core Subsystems

### 4.1. Configuration & Initialization

**Package**: `dreamference/config/`

The 4-tier config resolution ensures all components (Goose, vLLM, agents) inherit the same unified configuration:

```
Explicit CLI arguments
    ↓
Environment Variables (DREAMFERENCE_*, HF_TOKEN, …)
    ↓
Config File (dreamference.toml / .json)
    ↓
Built-in Defaults
```

Every field resolves through the same chain. `save_config()` deliberately writes only values that differ from the defaults, so a round-trip does not fossilize defaults into the TOML.

### 4.2. Hardware Layer

**Package**: `dreamference/hardware/`

**Responsibilities**:
- GB10 detection and qualification (GPU name, total memory, architecture)
- Model matrix registry with per-model vLLM launch recipes
- Weight pre-download & tensorization caching
- Memory budget validation before model loading

**Key Components**:
- `hardware_manager.py`: Detection, telemetry, memory metrics
- `model_matrix_registry.py`: Source-of-truth model specs with launch overrides
- `model_downloader.py`: HuggingFace + tensorizer cache management

### 4.3. vLLM Server Management

**Package**: `dreamference/vllm_server/`

**Responsibilities**:
- Docker container lifecycle (pull, run, stop, remove)
- Launch-arg construction from model recipes & config
- Health checks and readiness polling
- Live log streaming & memory monitoring

**Key Components**:
- `vllm_server_manager.py`: Main orchestrator
- `model_loading_monitor.py`: Progress tracking & memory telemetry
- `vllm_launch_options.py`: Flag merging from recipes

### 4.4. Agent Runners

**Package**: `dreamference/runner/`

**Responsibilities**:
- Per-agent provisioning (Goose, Cline, Aider, Continue, OpenHands)
- Session startup orchestration
- Sandbox prefixes (Apptainer, Podman, Docker) for Goose
- Environment variable synthesis & config passing

**Key Components**:
- `goose_runner.py`: Shared vLLM waitloop + Goose session launch
- `{agent}_installer.py`: Binary/extension provisioning (5 installers)
- `sandbox_manager.py`: Rootless container wrapping for Goose

### 4.5. Context Engine

**Package**: `dreamference/context_engine/`

**Responsibilities**:
- Parallel AST symbol extraction from codebase
- SQLite FTS5 indexing + sqlite-vec embeddings
- TF-IDF ranking + semantic search
- Hybrid ranking (FTS5 + TF-IDF + cosine similarity)

**Key Components**:
- `context_engine.py`: Orchestration & search
- `ast_symbol_extractor.py`: Python symbol extraction
- `sqlite_context_storage.py`: Persistent storage + queries
- `embedding_calculator.py`: Dense semantic embeddings (nomic-embed-text-v1.5)

### 4.6. MCP & IDE Integration

**Package**: `dreamference/mcp_server/`

**Responsibilities**:
- Stdio MCP server for IDE companions (JetBrains, VS Code)
- In-memory IDE state tracking
- Workspace search via context engine

**Tools Exposed**:
- `ide_get_active_editor`, `ide_open_file`, `ide_apply_diff`
- `workspace_search_code` (hybrid search)
- IDE diagnostics & file management

### 4.7. CLI & User Experience

**Package**: `dreamference/cli/`

**19 Subcommands**:
- Initialization: `init`, `main-model {set,inspect}`
- Agent Sessions: `chat`, `run`
- Server Management: `server {start,stop,remove}`, `logs request`, `benchmark_server`
- Indexing: `index`
- Utilities: `status`, `endpoints`, `web`, `model {list,download}`, `clear {model-cache,tensorize-cache}`, `mcp`

---

## 5. Roadmap & Implementation Verification

- [x] **Phase 1: NVIDIA GB10 Exclusive Specification** — model matrix & unified-memory targeting
- [x] **Phase 2: GB10 Inference Pipeline & Auto-Launch Engine** — multi-tier vLLM, live logs, weight pre-download, CLI suite including `model list`, `model download`
- [x] **Phase 3: Agentic Engine, Provisioning & MCP** — Goose auto-install, stdio MCP tools, multi-agent runners (Cline, Aider, Continue, OpenHands)
- [x] **Phase 4: Context Engine & Web Canvas** — parallel AST, SQLite/FTS5, TF-IDF (in-process), Web Canvas telemetry UI
- [x] **Phase 5: Modular Package Layout** — split packages under `dreamference/{hardware,config,runner,vllm_server,context_engine,mcp_server,cli}/` with shim modules for stable imports
- [x] **Phase 6: Expanded Agent Matrix** — Aider CLI, Continue IDE, OpenHands Docker UI wired through `--agent`

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md)** — Supported GB10 model matrix and default model rationale
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md)** — GB10 inference stack, vLLM launch recipes, performance flags
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md)** — Agent runtimes, session startup, integration details
- **[DREAMFERENCE_CONTEXT.md](./DREAMFERENCE_CONTEXT.md)** — Code indexing pipeline, AST extraction, hybrid search
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md)** — Docker vLLM architecture, model caching, tensorization
- **[DREAMFERENCE_CLI.md](./DREAMFERENCE_CLI.md)** — CLI suite, configuration, environment variables
- **[DREAMFERENCE_SETUP.md](./DREAMFERENCE_SETUP.md)** — System requirements, installation, helper scripts
- **[DREAMFERENCE_CODEBASE.md](./DREAMFERENCE_CODEBASE.md)** — Codebase structure, module layout, architecture reference
