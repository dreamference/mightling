# DGXCoder Technical Specification

> **Version:** 1.2.0-draft  
> **Status:** Proposed  
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
  - [4.2. GB10 Inference Stack](#42-gb10-inference-stack)
  - [4.3. Goose Agentic Loop & Container Execution](#43-goose-agentic-loop--container-execution)
- [5. Client Interfaces & Developer Experience](#5-client-interfaces--developer-experience)
  - [5.1. `dgxcoder` CLI & Goose Launcher](#51-dgxcoder-cli--goose-launcher)
  - [5.2. JetBrains & VS Code Integration (via MCP)](#52-jetbrains--vs-code-integration-via-mcp)
- [6. System Requirements & Setup](#6-system-requirements--setup)
  - [Requirements](#requirements)
  - [Identifying Your Hardware Variant](#identifying-your-hardware-variant)
  - [Quickstart Installation](#quickstart-installation)
- [7. Roadmap](#7-roadmap)

---

## 1. Executive Summary & Vision

**DGXCoder** is an open-source, enterprise-grade, agentic AI software development platform engineered exclusively to run on an **NVIDIA GB10 system** (Blackwell architecture with 128 GB of Unified Memory). Inspired by platforms like Google Antigravity, DGXCoder delivers end-to-end autonomous pair-programming, codebase indexing, multi-agent orchestration, and localized code synthesis with 100% data sovereignty, zero cloud egress, and zero external cluster dependencies.

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
| **Interfaces** | Antigravity IDE, CLI, Desktop | **CLI (`dgxcoder`), JetBrains Plugin, VS Code Extension** |

---

## 3. Supported NVIDIA GB10 Model Matrix

All supported models are qualified to run on a single **NVIDIA GB10 system (128 GB Unified Memory)**:

| Model | Parameters | Precision / Quantization | Memory Required | NVIDIA GB10 Compatibility |
| :--- | :--- | :--- | :--- | :--- |
| **Qwen 2.5 Coder 32B** | 32B | BF16 / INT8 | ~35 - 64 GB | ✅ Fits comfortably in 128GB Unified Memory |
| **Qwen 2.5 Coder 72B** | 72B | INT8 / FP8 / INT4 | ~45 - 80 GB | ✅ Supported (INT8/FP8 quantized fit) |
| **DeepSeek-R1-Distill-Qwen-32B** | 32B | BF16 / INT8 | ~35 - 64 GB | ✅ Fits comfortably in 128GB Unified Memory |
| **DeepSeek-R1-Distill-Llama-70B** | 70B | INT8 / FP8 / INT4 | ~45 - 80 GB | ✅ Supported (INT8/FP8 quantized fit) |
| **Llama 3.3 70B Instruct** | 70B | INT8 / FP8 | ~75 GB | ✅ Supported (INT8/FP8 quantized fit) |
| **StarCoder2 15B** | 15B | BF16 | ~30 GB | ✅ Fits easily |
| **DeepSeek-R1 / DeepSeek-V3** | 671B (MoE) | INT4 (AWQ/GPTQ) | ~350 GB | ❌ Exceeds 128GB (Requires multi-node or >128GB hardware) |

---

## 4. Hardware Integration & Optimization

### 4.1. NVIDIA GB10 Hardware Specification
* **GPU**: NVIDIA GB10 Tensor Core GPU (Blackwell architecture).
* **Unified Memory**: 128 GB LPDDR5X high-speed unified memory shared dynamically between CPU and GPU.
* **CPU Host**: High-performance ARM Cortex-X925 / Cortex-A725 cores.
* **Storage**: NVMe PCIe SSD for high-speed workspace indexing and model caching.

### 4.2. GB10 Inference Stack
* **vLLM / TensorRT-LLM Engine**: Configured for NVIDIA Blackwell unified memory architecture.
* **PagedAttention & Chunked Prefill**: Dynamic allocation across the 128 GB unified memory pool for low KV-cache overhead.
* **Speculative Decoding**: Uses a lightweight 1.5B/3B draft model on unified memory to boost 70B/72B inference speeds.

### 4.3. Goose Agentic Loop & Container Execution
* **Goose Execution Engine (`aaif-goose/goose`)**: Operates as the core agent execution runtime on the ARM CPU host cores.
* **Local OpenAI-Compatible API**: Goose communicates directly with the local vLLM / TensorRT-LLM server on `http://localhost:8000/v1` using `GOOSE_PROVIDER=openai`.
* **Sandboxed Execution Container**: Goose tools and commands are executed inside a local rootless Docker or Apptainer container running directly on the Linux environment.

---

## 5. Client Interfaces & Developer Experience

### 5.1. `dgxcoder` CLI & Goose Launcher
* Interactive terminal application powered by `Rich` / `Textual` and `Goose`.
* Automatically configures Goose to interface with the local GB10 model endpoint and load workspace MCP extensions.
* Displays real-time streaming code diffs, subagent tool execution, and local unified memory utilization gauges.

#### Subcommand Summary:
| Subcommand | Description |
| :--- | :--- |
| **`init`** | Initializes `.dgxcoder` workspace and generates `~/.config/goose/config.yaml`. |
| **`chat`** | Launches an interactive pair-programming session with the Goose AI agent. |
| **`run`** | Executes an autonomous coding prompt non-interactively using Goose. |
| **`status`** | Displays local NVIDIA GB10 hardware status, vLLM endpoint, and Goose installation status. |

#### Command Details & Options:
* **`dgxcoder init [--model MODEL] [--vllm-host HOST]`**
  Initializes project configuration and registers local MCP servers into Goose.
  * `--model MODEL`: Model loaded in vLLM (default: `qwen2.5-coder-32b`).
  * `--vllm-host HOST`: Base URL of the vLLM server (default: `http://localhost:8000`).

* **`dgxcoder chat [--model MODEL] [--debug]`**
  Launches interactive Goose agent terminal pair programming.
  * `--model MODEL`: Override session model name (default: `qwen2.5-coder-32b`).
  * `--debug`: Enable verbose debug logging for tool executions.

* **`dgxcoder run "PROMPT" [--model MODEL] [--debug]`**
  Executes autonomous coding task non-interactively.
  * `PROMPT`: Task prompt or instructions for Goose.
  * `--model MODEL`: Override session model name.
  * `--debug`: Enable verbose debug logging.

* **`dgxcoder status`**
  Checks local system config, GB10 hardware target, active model, and Goose CLI installation state.

#### Environment Variables:
| Variable | Description | Default |
| :--- | :--- | :--- |
| `DGXCODER_VLLM_HOST` | Local vLLM server endpoint URL | `http://localhost:8000` |
| `DGXCODER_MODEL` | Default LLM model name served on GB10 | `qwen2.5-coder-32b` |
| `GOOSE_PROVIDER` | Provider setting passed to Goose | `openai` |
| `OPENAI_HOST` | Host URL for local OpenAI-compatible vLLM API | `http://localhost:8000` |
| `OPENAI_BASE_PATH` | Base path for vLLM API | `v1` |

### 5.2. JetBrains & VS Code Integration (via MCP)
* **Model Context Protocol (MCP)**: DGXCoder registers JetBrains / VS Code extensions as local MCP servers within Goose (`~/.config/goose/config.yaml`).
* **JetBrains Companion**: Exposes active editor tabs, selections, and real-time linter diagnostics (`ide_get_diagnostics`, `ide_get_active_editor`) to the Goose agent runtime.
* **Visual Diff Overlays**: Renders red/green inline code diff proposals directly in PyCharm, IntelliJ, and VS Code.

---

## 6. System Requirements & Setup

### Requirements
* **System**: 1x NVIDIA GB10 System (Blackwell Architecture, 128 GB Unified Memory)
* **OS**: Linux ARM64 (Ubuntu 22.04 LTS or compatible)
* **Drivers**: NVIDIA Linux Driver 580+ / CUDA 13.x
* **Dependencies**: Docker 24+, NVIDIA Container Toolkit, vLLM / TensorRT-LLM

### Identifying Your Hardware Variant
To verify that your system is an NVIDIA GB10 with 128 GB Unified Memory, execute:
```bash
nvidia-smi --query-gpu=name --format=csv
free -h
```
* **NVIDIA GB10 System**: `nvidia-smi` returns `NVIDIA GB10` and `free -h` displays ~`128Gi` total system/unified memory.

### Quickstart Installation
```bash
git clone https://github.com/dgxcoder/dgxcoder.git
cd dgxcoder
./scripts/install_gb10.sh --model qwen2.5-coder-32b
dgxcoder chat
```

---

## 7. Roadmap

- [x] **Phase 1: NVIDIA GB10 Exclusive Specification**
  - Scope all memory budgets, model matrices, and unified memory architecture exclusively to NVIDIA GB10 (128 GB Unified Memory) hardware.
- [ ] **Phase 2: GB10 Inference Pipeline**
  - Implement vLLM deployment script optimized for NVIDIA GB10 128GB unified memory.
  - Implement core `dgxcoder` CLI shell and initial tool definitions.
- [ ] **Phase 3: Agentic Engine & MCP Integration**
  - Implement Director / Subagent orchestration layer and JetBrains/VS Code MCP bridges.
- [ ] **Phase 4: Quantized Model & FlashAttention Optimization**
  - Enable FP8/INT8 quantized 70B/72B model serving on GB10 unified memory.
  - Build local HTML Canvas auxiliary pane for live diagramming and artifact rendering.
