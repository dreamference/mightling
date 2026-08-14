# Dreamference Codebase Architecture & Reference

> **Version:** 1.2.0
> **Subject:** Source Code Layout, Module Organization, Package Structure

---

## Table of Contents

- [1. Package Overview](#1-package-overview)
- [2. Module Structure](#2-module-structure)
- [3. Subsystem Packages](#3-subsystem-packages)
- [4. Import Conventions](#4-import-conventions)
- [5. vLLM Default Model Parameters](#5-vllm-default-model-parameters)

---

## 1. Package Overview

**Version**: `dreamference.__version__ == "1.2.0"`

**Architecture**: Six subsystems under `dreamference/`, each a package whose `__init__.py` is a re-export facade with explicit `__all__`.

**Key Principle**: Top-level `dreamference/*.py` modules are thin re-export shims; implementations live in subpackages.

### 1.1. Subsystem Packages

| Package | Role |
| :--- | :--- |
| `dreamference/config/` | 4-tier config resolution, Goose YAML sync, env vars |
| `dreamference/hardware/` | GB10 detection, model matrix, HF downloads/tensorization |
| `dreamference/vllm_server/` | Docker vLLM lifecycle, launch-arg construction, host-safety guards |
| `dreamference/runner/` | Per-agent installer + runner pairs, sandbox prefixes |
| `dreamference/context_engine/` | AST symbol extraction + TF-IDF/dense retrieval |
| `dreamference/mcp_server/` | Stdio MCP server for JetBrains/VS Code |

---

## 2. Module Structure

```
dreamference/
├── __init__.py                      # __version__ = "1.2.0"
│
├── cli.py                           # shim → cli package
├── cli/
│   └── dreamference_cli_controller.py   # DreamferenceCLIController (19 subcommands)
│
├── config.py                        # shim
├── config/
│   ├── config_path_resolver.py
│   ├── config_file_storage_manager.py
│   └── dreamference_config.py       # Main config resolution & Goose env setup
│
├── context_engine.py                # shim
├── context_engine/
│   ├── context_engine.py            # Main orchestrator
│   ├── ast_symbol_extractor.py      # Python AST extraction
│   ├── tfidf_calculator.py          # TF-IDF ranking
│   ├── sqlite_context_storage.py    # Persistent storage
│   ├── code_symbol.py               # Symbol data model
│   ├── indexed_file.py              # File metadata
│   └── embedding_calculator.py      # Dense embeddings
│
├── hardware.py                      # shim + detect/download helpers
├── hardware/
│   ├── model_matrix_registry.py     # Source-of-truth model specs
│   ├── model_downloader.py          # HF + tensorizer caching
│   ├── hardware_manager.py          # GB10 detection & telemetry
│   ├── model_spec.py                # Per-model launch recipes
│   ├── memory_metrics.py            # Memory metrics & queries
│   └── hardware_telemetry.py        # GPU/memory telemetry
│
├── mcp_server.py                    # shim
├── mcp_server/
│   ├── mcp_server.py                # Main MCP server
│   ├── mcp_tool_registry.py         # Tool definitions
│   ├── ide_state.py                 # IDE state tracking
│   └── editor_selection.py          # Editor integration helpers
│
├── runner.py                        # shim
├── runner/
│   ├── goose_runner.py              # wait_for_vllm + Goose session
│   ├── goose_installer.py           # Goose CLI provisioning
│   ├── sandbox_manager.py           # Apptainer/Podman/Docker wrapping
│   ├── cline_runner.py              # Cline/VS Code integration
│   ├── cline_installer.py           # Cline extension provisioning
│   ├── aider_runner.py              # Aider CLI integration
│   ├── aider_installer.py           # Aider package provisioning
│   ├── continue_runner.py           # Continue IDE integration
│   ├── continue_installer.py        # Continue extension provisioning
│   ├── openhands_runner.py          # OpenHands Docker UI
│   └── openhands_installer.py       # OpenHands image provisioning
│
├── vllm_server.py                   # shim
├── vllm_server/
│   ├── vllm_server_manager.py       # Main orchestrator
│   ├── vllm_log_streamer.py         # Log streaming & parsing
│   ├── vllm_launch_options.py       # Flag construction & merging
│   ├── vllm_server_status.py        # Server status & health checks
│   ├── vllm_startup_monitor.py      # Startup progress tracking
│   └── model_loading_monitor.py     # Memory & progress monitoring
│
├── web_canvas.py                    # CanvasHandler + start_web_canvas_server
│
└── scripts/
    ├── install_gb10.sh              # Full installation
    ├── run_vllm_gb10.sh             # Foreground vLLM launch
    └── run_goose.sh                 # Goose session wrapper
```

---

## 3. Subsystem Packages

### 3.1. `dreamference/config/`

**Responsibilities**:
- 4-tier config merge (CLI → Env → TOML → defaults)
- Goose YAML config synthesis
- Environment variable setup for agent processes
- Config persistence & reloading

**Key Classes**:
- `DreamferenceConfig`: Main config object with `__init__` implementing 4-tier resolution
- `ConfigPathResolver`: TOML/YAML file discovery
- `ConfigFileStorageManager`: File I/O & serialization

**Public API**: Re-exported via `dreamference/config/__init__.py`

### 3.2. `dreamference/hardware/`

**Responsibilities**:
- GB10 detection & qualification (GPU name, memory, architecture)
- Model matrix registry with per-model vLLM recipes
- Weight download & caching (HuggingFace)
- Tensorizer caching (optional)
- Memory budget validation

**Key Classes**:
- `ModelMatrixRegistry`: Source-of-truth model specs with `MATRIX` dict
- `ModelSpec`: Per-model launch recipe container
- `ModelDownloader`: HF download & cache management
- `HardwareManager`: GB10 detection & telemetry

**Public API**: Re-exported via `dreamference/hardware/__init__.py`

**Note**: `launch_overrides` in `ModelSpec` carry the per-model vLLM recipe as registry data.

### 3.3. `dreamference/vllm_server/`

**Responsibilities**:
- Docker container lifecycle (pull, run, stop, remove)
- Launch-arg construction from model recipes & config
- Health checks & readiness polling
- Live log streaming & memory monitoring
- Host-safety guards (PSI watchdog, swap checks)

**Key Classes**:
- `VLLMServerManager`: Main orchestrator
- `ModelLoadingMonitor`: Progress tracking, memory telemetry, log streaming
- `VLLMStartupMonitor`: Startup percentage & stall detection
- `VLLMLaunchOptions`: Flag merging from recipes
- `VLLMLogStreamer`: Background log consumption & parsing

**Public API**: Re-exported via `dreamference/vllm_server/__init__.py`

### 3.4. `dreamference/runner/`

**Responsibilities**:
- Per-agent provisioning (Goose, Cline, Aider, Continue, OpenHands)
- Session startup orchestration
- Sandbox prefixes (Apptainer, Podman, Docker) for Goose only
- Environment variable synthesis
- Config passing to agents

**Key Classes** (5 runner pairs):
- `GooseRunner` / `GooseInstaller`
- `ClineRunner` / `ClineInstaller`
- `AiderRunner` / `AiderInstaller`
- `ContinueRunner` / `ContinueInstaller`
- `OpenHandsRunner` / `OpenHandsInstaller`
- `SandboxManager`: Rootless container wrapping (Goose only)

**Shared Pattern**: Each runner has:
1. `*_installer.py`: Provisioning logic (binary/extension/image checks)
2. `*_runner.py`: Session execution logic

**Public API**: Re-exported via `dreamference/runner/__init__.py`

### 3.5. `dreamference/context_engine/`

**Responsibilities**:
- Parallel AST symbol extraction from codebase
- SQLite FTS5 indexing + sqlite-vec dense embeddings
- TF-IDF ranking computation
- Hybrid search (FTS5 + TF-IDF + cosine similarity)
- JSON caching of symbol metadata

**Key Classes**:
- `ContextEngine`: Main orchestrator & search interface
- `ASTSymbolExtractor`: Python AST extraction
- `SqliteContextStorage`: Persistent storage & queries
- `TFIDFCalculator`: In-memory TF-IDF matrix
- `EmbeddingCalculator`: Dense embeddings (nomic-embed-text-v1.5)
- `CodeSymbol`: Symbol data model
- `IndexedFile`: File metadata

**Public API**: Re-exported via `dreamference/context_engine/__init__.py`

### 3.6. `dreamference/mcp_server/`

**Responsibilities**:
- Stdio MCP server for IDE companions
- In-memory IDE state tracking
- Tool registry for workspace search
- Editor integration helpers

**Key Classes**:
- `MCPServer`: Main MCP server (async)
- `MCPToolRegistry`: Tool definitions & handlers
- `IDEState`: In-memory IDE state container
- `EditorSelection`: File selection helpers

**Tools Exposed**:
- `ide_get_active_editor`, `ide_get_diagnostics`, `ide_get_open_files`, `ide_open_file`, `ide_apply_diff`
- `workspace_search_code`

**Public API**: Re-exported via `dreamference/mcp_server/__init__.py`

---

## 4. Import Conventions

### 4.1. User Imports (Stable)

```python
# Recommended: Use package shims (stable API)
from dreamference import DreamferenceConfig, ContextEngine, HardwareManager
from dreamference.runner import GooseRunner
from dreamference.vllm_server import VLLMServerManager
```

### 4.2. Internal Imports

```python
# Internal (less stable): Direct module imports
from dreamference.config.dreamference_config import DreamferenceConfig
from dreamference.context_engine.context_engine import ContextEngine
from dreamference.hardware.hardware_manager import HardwareManager
```

### 4.3. Re-Export Pattern

Each package `__init__.py` follows this pattern:

```python
# dreamference/config/__init__.py
from dreamference.config.dreamference_config import DreamferenceConfig
from dreamference.config.config_path_resolver import ConfigPathResolver
# ...

__all__ = ["DreamferenceConfig", "ConfigPathResolver", ...]
```

This enables stable imports from the package level while allowing internal reorganization.

---

## 5. vLLM Default Model Parameters

Detailed specification of parameters generated by `VLLMServerManager.build_launch_command(model="qwen3.6-35b-a3b-nvfp4")`:

| Parameter / Flag | Passed Value | Source / Recipe Setting | Purpose & Function |
| :--- | :--- | :--- | :--- |
| **Docker Image** | `nvcr.io/nvidia/vllm:26.07-py3` | `DEFAULT_VLLM_IMAGE` | NGC container pinned with Blackwell SM121 driver & library compatibility |
| `--model` | `nvidia/Qwen3.6-35B-A3B-NVFP4` | `ModelMatrixRegistry.resolve_hf_repo` | HuggingFace repository ID for default NVFP4 weights |
| `--max-model-len` | `131072` | `launch_overrides["max_model_len"]` | Context window size (128K tokens) |
| `--gpu-memory-utilization` | `0.3` | `launch_overrides["gpu_memory_utilization"]` | 30% memory reservation for GB10 unified memory pool |
| `--kv-cache-dtype` | `fp8` | `launch_overrides["kv_cache_dtype"]` | Enables FP8 quantization for KV cache tensors |
| `--attention-backend` | `flashinfer` | `launch_overrides["attention_backend"]` | Blackwell-optimized FlashInfer attention |
| `--tool-call-parser` | `qwen3_xml` | `launch_overrides["tool_call_parser"]` | Qwen 3.6 XML tool call parser |
| `--reasoning-parser` | `qwen3` | `launch_overrides["reasoning_parser"]` | Qwen 3.6 reasoning channel parser |
| `--max-num-batched-tokens` | `8192` | `launch_overrides["max_num_batched_tokens"]` | Max batched token count for chunked prefill |
| `--speculative-config` | `{"method": "mtp", "num_speculative_tokens": 3, "moe_backend": "triton"}` | `launch_overrides["speculative_config"]` | In-checkpoint Multi-Token Prediction |
| `--enable-prefix-caching` | (flag) | `DEFAULT_PREFIX_CACHING=True` | Automatic KV cache prefix reuse |
| `--enable-chunked-prefill` | (flag) | `DEFAULT_CHUNKED_PREFILL=True` | Prefill chunking for responsive TTFT |
| `--max-num-seqs` | `4` | `launch_overrides["extra_args"]` | Caps maximum concurrent sequence count |
| `--tensor-parallel-size` | `1` | `launch_overrides["extra_args"]` | Single-GPU execution |
| `--dtype` | `auto` | `launch_overrides["extra_args"]` | Auto-detect model weight precision |
| `--async-scheduling` | (flag) | `VLLMServerManager.build_launch_command` | Enables asynchronous request scheduling |
| `--load-format` | `fastsafetensors` | `VLLMServerManager.build_launch_command` | Faster model loading format |

---

## See Also

- **[DREAMFERENCE_ARCHITECTURE.md](./DREAMFERENCE_ARCHITECTURE.md)** — System overview & interaction model
- **[DREAMFERENCE_CONTEXT.md](./DREAMFERENCE_CONTEXT.md)** — Context engine internals
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md)** — vLLM configuration & launch
