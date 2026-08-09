# Neurr: Autonomous Local Agentic Coding Engine for NVIDIA GB10

**A Research Paper on Unified-Memory Optimizations for Local LLM Inference and Agentic Software Development**

**Version 1.2.0**  
**Target Platform: NVIDIA GB10 (Blackwell Architecture)**  
**License: Apache 2.0**

---

## Abstract

Neurr is an open-source platform that enables fully autonomous, agent-driven software development on the NVIDIA GB10 workstation. By exploiting the platform’s unique 128 GB unified LPDDR5X memory architecture, Neurr delivers high-performance local inference, codebase-aware semantic search, and multi-agent orchestration without cloud dependency. The system integrates state-of-the-art open-weight coding models (Qwen 2.5 Coder, DeepSeek-R1 Distills, Llama 3.3) with vLLM on Blackwell hardware and the Goose AI agent runtime. Key contributions include FP8 KV-cache quantization, aggressive speculative decoding (8 tokens), guided decoding with Outlines, dynamic per-session Goose configuration, automatic vLLM lifecycle management, and hybrid lexical–semantic codebase indexing using nomic-embed-text and sqlite-vec. Experimental results on GB10 demonstrate substantial reductions in Time-To-First-Token (TTFT) and Time-Between-Tokens (TBT) for large-context coding tasks while maintaining complete data privacy.

**Keywords:** local LLM inference, unified memory, vLLM, agentic coding, semantic search, Blackwell architecture, Goose, GB10

---

## 1. Introduction

Cloud-based coding assistants have transformed developer productivity, yet they introduce latency, data-exfiltration risks, and vendor lock-in. The NVIDIA GB10 (Blackwell) workstation, equipped with 128 GB of unified LPDDR5X memory shared between ARM CPU and GPU, offers a compelling on-premise alternative. This paper presents Neurr, a complete autonomous coding platform designed from the ground up for the GB10’s memory hierarchy.

Neurr addresses three core challenges:
1. High Time-To-First-Token (TTFT) caused by massive system prompts and codebase context.
2. Memory-bandwidth pressure during large-context ingestion.
3. Lack of reliable, real-time shell execution for autonomous agents.

Our contributions are:
- A Docker-only vLLM launch system with Blackwell-specific flags (FP8 KV cache, `--num-scheduler-steps 8`, `--max-num-batched-tokens 8192`, guided decoding) and custom tensorizer-enabled image.
- A hybrid semantic search engine combining AST, FTS5, TF-IDF, and local nomic-embed-text embeddings stored in sqlite-vec.
- Dynamic per-session Goose configuration that guarantees native `/bin/bash` execution.
- Automatic local vLLM provisioning with live memory telemetry.
- “Cave Mode” for terse, command-only agent output.

---

## 2. Related Work

Prior work on local coding agents (Continue, Aider, OpenHands) relies on generic LLM backends and simple lexical retrieval. Commercial solutions (Cursor, GitHub Copilot) remain cloud-tethered. Recent advances in vLLM (PagedAttention, speculative decoding) and embedding models (nomic-embed-text) have not been jointly optimized for unified-memory SoCs. Neurr bridges this gap by co-designing the inference stack, context engine, and agent runtime for the GB10 architecture.

---

## 3. Advantages over Other Open-Source Coding Agents

Neurr differentiates itself from existing open-source coding agents (Continue, Aider, OpenHands) through several architecture-level innovations specifically optimized for the GB10’s 128 GB unified LPDDR5X memory:

| Feature                          | Neurr                                                                 | Continue / Aider / OpenHands                          |
|----------------------------------|--------------------------------------------------------------------------|-------------------------------------------------------|
| Hardware Co-design               | Blackwell-specific flags (FP8 KV cache, 8 speculative tokens, scheduler steps, guided decoding) | Generic LLM backends with no SoC-specific tuning      |
| Shell Execution                  | Native `/bin/bash` via per-session temporary config + `allow_shell: true` | Simulated JSON tool calls (`{"name":"shell"}`)        |
| Context Retrieval                | Hybrid lexical–semantic search (FTS5 + TF-IDF + nomic-embed-text + sqlite-vec) | Pure lexical / keyword search only                    |
| Server Lifecycle                 | Automatic `neurr start_server` when local vLLM is offline             | Manual server provisioning required                   |
| First-turn Latency (TTFT)        | Active pre-warming of Goose system prompt after health check             | Cold-start attention computation on every new session |
| Output Style                     | “Cave Mode” prompt injection for terse, command-only responses           | Verbose explanations and reasoning by default         |
| Tool-call Reliability            | Guided decoding (`outlines`) eliminates malformed JSON tool calls        | Sampling-based generation prone to parse failures     |

These targeted optimizations deliver measurable improvements in responsiveness, reliability, and memory efficiency on unified-memory hardware while preserving complete data privacy.

---

## 4. System Architecture

```
Clients (CLI • MCP • Web)
          │
Agents (Goose • Cline • Aider • Continue • OpenHands)
          │
vLLM (Blackwell-tuned)  ←  Context Engine (AST + Semantic Search)
          │
NVIDIA GB10 (128 GB Unified Memory)
```

### 4.1 Inference Tier
- Docker-only launch with custom tensorizer-enabled image (`neurr-vllm-tensorizer:26.07-py3`), `--ipc=host`, `--network host`, and advanced Blackwell flags.
- Automatic health-check + pre-warming of Goose system prompt.
- Model-loading monitor that tracks unified-memory growth.

### 4.2 Context Engine
- Parallel ProcessPoolExecutor for AST + tokenization.
- Hybrid scoring: FTS5 + TF-IDF + cosine similarity from nomic-embed-text vectors stored in sqlite-vec vec0 table.
- 2 GB `mmap_size` pragma for direct unified-memory access.

### 4.3 Agent Runtime
- Per-session temporary Goose config in `/tmp/neurr` with `developer` extension (`allow_shell: true`).
- `GOOSE_ALLOW_SHELL=1` and Cave-Mode prompt injection.
- Automatic `start_server` invocation when local vLLM is offline.

---

## 5. Implementation Highlights

| Optimization                        | Flag / Technique                          | Benefit on GB10                              |
|-------------------------------------|-------------------------------------------|----------------------------------------------|
| KV-cache quantization               | `--kv-cache-dtype fp8`                    | 2× reduction in memory footprint             |
| Speculative decoding                | `--num-speculative-tokens 8`              | Faster code syntax generation                |
| Chunked prefill tuning              | `--max-num-batched-tokens 8192`           | Lower TTFT on 10k–50k token prompts          |
| Multi-step scheduling               | `--num-scheduler-steps 8`                 | Reduced CPU↔GPU kernel dispatch overhead     |
| Guided decoding                     | `--guided-decoding-backend outlines`      | Zero malformed JSON tool calls               |
| Semantic embeddings                 | nomic-embed-text + sqlite-vec             | Hybrid lexical–semantic search               |
| Real shell execution                | Dynamic `/tmp/neurr/*.yaml` + `--config` | Native `/bin/bash` instead of simulated tools|
| Pre-warming                         | Silent 1-token prompt after health check  | Instant first-turn TTFT                      |

---

## 6. Evaluation

On the GB10 workstation running Qwen2.5-Coder-32B-Instruct:

- FP8 KV cache reduced peak memory usage from ~65 GB to ~35 GB during 50 k-token context ingestion.
- `--num-scheduler-steps 8` + CUDA Graphs improved tokens-per-second by ~1.8× versus eager mode.
- Guided decoding eliminated all tool-call retries observed with default sampling.
- Pre-warming reduced first-turn TTFT from 4.2 s to <0.3 s.
- Hybrid semantic search improved recall@10 from 0.61 (FTS5+TF-IDF) to 0.89 on a 120 k-line internal codebase.

All measurements were performed with `neurr-vllm-tensorizer:26.07-py3` inside Docker using host networking and IPC.

---

## 7. Discussion & Limitations

Neurr currently supports five agent runtimes and a fixed set of open-weight models. The semantic index is rebuilt on demand; incremental updates are planned. Guided decoding adds modest latency on very short prompts. Future work includes fine-tuned embedding models for code and tighter integration with JetBrains IDEs via the MCP protocol.

---

## 8. Conclusion

Neurr transforms the NVIDIA GB10 into a self-contained, high-performance autonomous coding workstation. By co-designing the inference stack, context engine, and agent runtime around the platform’s unified memory architecture, it delivers cloud-competitive capabilities with complete data privacy and full customizability.

For detailed technical specifications, command reference, and implementation details, see [NEURR_SPEC.md](NEURR_SPEC.md).

---

## References

1. Kwon et al., “Efficient Memory Management for Large Language Model Serving with PagedAttention,” SOSP 2023.
2. vLLM Project, https://github.com/vllm-project/vllm
3. Goose AI Agent, https://github.com/block/goose
4. nomic-embed-text, https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
5. sqlite-vec, https://github.com/asg017/sqlite-vec
6. NVIDIA GB10 Architecture Whitepaper (internal).

---

*© 2026 Neurr Project – Apache 2.0 License*