# Supported Models & vLLM Parameters

All models listed below are qualified for single-node **NVIDIA GB10** workstations with 128 GB Unified LPDDR5X Memory.

---

## NVIDIA GB10 Model Qualification Matrix

| Alias | Model Name | Parameters | Precision | Memory Required | GB10 Status | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`qwen3.6-35b-a3b-nvfp4`** | Qwen 3.6 35B-A3B | 35B (3B active) | NVFP4 | ~25 – 60 GB | ✅ **Default** | MoE with ~3B active parameters. FlashInfer b12x NVFP4 path. |
| **`qwen2.5-coder-32b`** | Qwen 2.5 Coder 32B | 32B | BF16 / INT8 / FP8 | ~35 – 64 GB | ✅ Qualified | Fits comfortably in 128 GB Unified Memory. |
| **`qwen2.5-coder-72b`** | Qwen 2.5 Coder 72B | 72B | INT8 / FP8 / INT4 | ~45 – 80 GB | ✅ Qualified | Supported via INT8/FP8 quantization. |
| **`deepseek-r1-distill-32b`** | DeepSeek-R1-Distill-Qwen-32B | 32B | BF16 / INT8 / FP8 | ~35 – 64 GB | ✅ Qualified | High-reasoning 32B distilled model. |
| **`deepseek-r1-distill-70b`** | DeepSeek-R1-Distill-Llama-70B | 70B | INT8 / FP8 / INT4 | ~45 – 80 GB | ✅ Qualified | High-reasoning 70B distilled model. |
| **`llama-3.3-70b`** | Llama 3.3 70B Instruct | 70B | INT8 / FP8 | ~75 GB | ✅ Qualified | Quantized FP8 fit. |
| **`qwen2.5-coder-1.5b`** | Qwen 2.5 Coder 1.5B | 1.5B | BF16 / FP16 / INT8 | ~3.5 – 6 GB | ✅ Draft | Speculative decoding draft model. |
| **`qwen2.5-coder-3b`** | Qwen 2.5 Coder 3B | 3.0B | BF16 / FP16 / INT8 | ~6.5 – 10 GB | ✅ Draft | Speculative decoding draft model. |
| **`starcoder2-15b`** | StarCoder2 15B | 15B | BF16 / FP16 | ~20 – 30 GB | ✅ Qualified | Fits easily in Unified Memory. |
| `deepseek-v3-671b` | DeepSeek-V3 671B | 671B | INT4 | ~350 GB | ❌ Unqualified | Exceeds 128 GB Unified Memory capacity. |

---

## vLLM Parameters for Default Model (`qwen3.6-35b-a3b-nvfp4`)

When starting the local inference server using the default model (`qwen3.6-35b-a3b-nvfp4`), `DGXCoder` applies a specialized per-model launch recipe (`ModelMatrixRegistry.MATRIX['qwen3.6-35b-a3b-nvfp4'].launch_overrides`) tuned specifically for NVIDIA GB10 (Blackwell SM121) hardware.

### 1. Model & Container Identity

| Parameter | Value | Description |
| :--- | :--- | :--- |
| **Model Alias** | `qwen3.6-35b-a3b-nvfp4` | Primary default short alias passed to `dgxcoder init / start_server / chat`. |
| **HuggingFace Repo ID** | `nvidia/Qwen3.6-35B-A3B-NVFP4` | Official HuggingFace repository containing NVFP4 weights. |
| **Docker Image** | `nvcr.io/nvidia/vllm:26.07-py3` | Pinned NGC vLLM container image with Blackwell SM121 kernel support. |

### 2. Core vLLM CLI Parameters

The table below lists all CLI flags passed to the vLLM engine when launching `qwen3.6-35b-a3b-nvfp4`:

| CLI Flag | Value | Description |
| :--- | :--- | :--- |
| `--max-model-len` | `131072` | Configures a 128K token context window for large codebase analysis. |
| `--gpu-memory-utilization` | `0.81` | Allocates 81% of GB10 memory to vLLM, leaving space for host OS and processes. |
| `--kv-cache-dtype` | `fp8` | Uses FP8 precision for KV cache tensors to maximize context capacity. |
| `--attention-backend` | `flashinfer` | Uses FlashInfer attention implementation optimized for Blackwell SM121. |
| `--moe-backend` | `marlin` | Selects Marlin MoE kernel path to avoid SM120 CUTLASS kernel corruption on SM121. |
| `--tool-call-parser` | `qwen3_xml` | Specifies the Qwen 3.6 XML parser for agent tool calls (`<tool_call>`). |
| `--reasoning-parser` | `qwen3` | Specifies the Qwen 3.6 parser for the separate reasoning channel. |
| `--max-num-batched-tokens` | `32768` | Sets maximum batched tokens per iteration for chunked prefill efficiency. |
| `--speculative-config` | `{"method": "mtp", "num_speculative_tokens": 3}` | Enables internal Multi-Token Prediction (MTP) with 3 speculative tokens per step. |
| `--enable-prefix-caching` | *(flag present)* | Enables automatic KV cache prefix reuse across multi-turn agent conversations. |
| `--enable-chunked-prefill` | *(flag present)* | Breaks long prompt prefills into chunks to ensure low time-to-first-token latency. |
| `--max-num-seqs` | `4` | Caps maximum concurrent request sequences at 4. |
| `--tensor-parallel-size` | `1` | Runs single-GPU tensor parallelism for single-chip GB10 hardware. |
| `--dtype` | `auto` | Auto-detects model tensor data types from checkpoint configuration. |
| `--trust-remote-code` | *(flag present)* | Allows remote code execution required by Qwen architecture modules. |

### 3. Container Environment Variables

Blackwell SM121 MoE kernel routing is set via environment variables passed into the Docker container (`docker run -e ...`):

| Environment Variable | Value | Description |
| :--- | :--- | :--- |
| `VLLM_NVFP4_GEMM_BACKEND` | `flashinfer-b12x` | Forces the SM121-compatible FlashInfer b12x NVFP4 GEMM backend path. |
| `VLLM_MARLIN_USE_ATOMIC_ADD` | `1` | Enables atomic addition optimizations in Marlin MoE kernels. |

---

See [DGXCODER_SPEC.md](../DGXCODER_SPEC.md#427-per-model-launch-recipes) for full implementation details.