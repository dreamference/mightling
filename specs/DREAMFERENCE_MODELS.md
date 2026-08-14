# Dreamference Supported Models & Hardware

> **Version:** 1.2.0
> **Subject:** NVIDIA GB10 Model Matrix & Default Model Selection

---

## Table of Contents

- [1. Supported NVIDIA GB10 Model Matrix](#1-supported-nvidia-gb10-model-matrix)
- [2. Default Model Rationale](#2-default-model-rationale)
- [3. NVIDIA GB10 Hardware Specification](#3-nvidia-gb10-hardware-specification)

---

## 1. Supported NVIDIA GB10 Model Matrix

Aliases and HuggingFace repos are defined in `ModelMatrixRegistry.MATRIX` (`dreamference/hardware/model_matrix_registry.py`):

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

---

## 2. Default Model Rationale

`qwen3.6-35b-a3b-nvfp4` is the default because decode speed on GB10 is bounded by memory bandwidth, not compute: a mixture-of-experts model with ~3B active parameters generates far faster than a dense model of comparable quality, and 4-bit weights leave most of the 128 GB for KV cache at long context.

### 2.1. SM121 Kernel Path & Corruption Detection

**This model only works on an SM121-safe kernel path.** The CUTLASS FP4 kernels are compiled for the SM120 ISA and run on GB10 without erroring while producing corrupt output — the recognisable symptom is a response consisting only of `!` characters. Dreamference pins the working path via the model's `launch_overrides` recipe (see [DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md)), and runs an **automated post-launch canary** upon server startup.

The canary sends a single completion request:
1. It asserts the output isn't corrupted (all `!`). If corruption is detected, the server **auto-demotes to an FP8 model** (e.g., `qwen2.5-coder-32b`) to ensure a working baseline.
2. The user should still benchmark against FP8 on their own unit before treating the FP4 numbers as settled — published results range from NVFP4 losing to FP8 to winning by ~3x, driven by whether MTP is active and which backend was chosen.

### 2.2. vLLM Version Dependency

The b12x SM12x backends merged upstream in May 2026. If `DEFAULT_VLLM_IMAGE` predates them, the launch falls back to a slower or broken path — see [DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md) for vLLM runtime image details.

---

## 3. NVIDIA GB10 Hardware Specification

### 3.1. Unified Hardware Architecture

- **GPU**: NVIDIA GB10 Tensor Core GPU (Blackwell architecture).
- **Unified Memory**: 128 GB LPDDR5X high-speed unified memory shared dynamically between CPU and GPU.
- **CPU Host**: High-performance ARM Cortex CPU cores (`aarch64` architecture).
- **Storage**: NVMe PCIe SSD for high-speed workspace indexing and model caching.

### 3.2. Key Architectural Advantages

1. **Unified Memory** eliminates discrete CPU ↔ GPU copies; data moves at full memory bandwidth once.
2. **128 GB capacity** enables full-precision or lightly quantized models to fit in unified cache.
3. **Blackwell compute** provides state-of-the-art inference density for open-weight models.
4. **ARM Cortex host** reduces system complexity while preserving development workflows (standard Linux tools, Python).

---

## 4. Hardware Detection & Qualification

### 4.1. Automated Detection

```bash
nvidia-smi --query-gpu=name --format=csv
free -h
```

**Qualification Heuristics**:
- GPU name contains `GB10`/`BLACKWELL`, OR
- Total memory ≥ ~100 GiB per detection fallback heuristic

### 4.2. Manual Override

Set `DREAMFERENCE_GB10_OVERRIDE=1` to force GB10 behavior on unsupported hardware (for testing only).

---

## See Also

- **[DREAMFERENCE_ARCHITECTURE.md](./DREAMFERENCE_ARCHITECTURE.md)** — System overview
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md)** — vLLM launch recipes & performance flags
