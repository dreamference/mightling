# Puffin Supported Models & Hardware

> **Version:** 1.2.0
> **Subject:** NVIDIA GB10 Model Matrix & Default Model Selection
> **Checked against the code:** 2026-10-01 (`dreamference/hardware/model_matrix_registry.py`: every alias, repo, memory range and recipe value below was compared with `MATRIX`)

---

## Table of Contents

- [1. Supported NVIDIA GB10 Model Matrix](#1-supported-nvidia-gb10-model-matrix)
- [2. Default Model Rationale](#2-default-model-rationale)
- [3. NVIDIA GB10 Hardware Specification](#3-nvidia-gb10-hardware-specification)
- [4. Hardware Detection & Qualification](#4-hardware-detection--qualification)

---

## 1. Supported NVIDIA GB10 Model Matrix

Aliases, HF repos and launch recipes are defined in `ModelMatrixRegistry.MATRIX`. Each entry is a `ModelSpec`, and its `notes` field carries the full history of the recipe; read it before retuning one. All eight entries are `compatible_gb10`.

| Alias | Model | Params | Format | Memory (min–max GB) | Vision | Role |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `qwen3.8-27b-nvfp4-dflash2` | Qwen 3.8 27B (NVFP4 + DFlash2, SGLang) | 27B | NVFP4 | 20–70 | ✅ | **Default** main model (`DEFAULT_MODEL_ALIAS`, since 2026-09-29). Served by **SGLang**, not vLLM (`launch_overrides['engine']`) |
| `qwen3.5-122b-a10b-hybrid-dflash` | Qwen 3.5 122B-A10B (INT4+FP8 hybrid + DFlash + dense-bandwidth stack) | 122B (10B active) | INT4+FP8 hybrid | 71.5–120 | ✅ | Fallback; the default from 2026-08-23 to 2026-09-29 |
| `qwen3.5-122b-a10b-int4-dflash` | Qwen 3.5 122B-A10B (INT4 AutoRound + DFlash) | 122B (10B active) | AutoRound INT4 | 71.5–120 | ✅ | Tested fallback; the default until 2026-08-23 |
| `qwen3.5-122b-a10b-nvfp4` | Qwen 3.5 122B-A10B (NVFP4) | 122B (10B active) | NVFP4 | 78–120 | ✅ | Earlier default (until 2026-08-15); fallback if DFlash does not come up |
| `qwen3.6-35b-a3b-nvfp4` | Qwen 3.6 35B-A3B (NVFP4) | 35B (3B active) | NVFP4 | 25–60 | — | Small-model option |
| `qwen3.8-27b-dflash2-draft` | Qwen 3.8 27B DFlash2 drafter (NVFP4) | 1B | NVFP4 | 1–2 | — | Drafter named by the default recipe's `speculative_config`; not served on its own |
| `qwen3.5-122b-a10b-dflash-draft` | Qwen 3.5 122B-A10B DFlash drafter | 0.8B | BF16 | 1.5–2.5 | — | Drafter named by the DFlash recipes' `speculative_config`; not served on its own. Listed so memory gates and pre-download know its size |
| `tiny-a2d-coder-0.5b-diffusion` | Tiny-A2D Qwen2.5-Coder 0.5B (bd3lm diffusion) | 0.6B | BF16 | 1.5–3 | — | **Default** diffusion model (`DEFAULT_DIFFUSION_MODEL_ALIAS`); **not offered while diffusion is switched off** (`DIFFUSION_ENABLED = False` since 2026-10-03: never started, downloaded or listed). `is_diffusion`: **not** servable by vLLM; runs in the diffusion sidecar |

**HF repos:**
- `RadixArk/Qwen3.8-27B-NVFP4`: the default, pinned to a revision;
- `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal`: its drafter, pinned to a revision;
- `Intel/Qwen3.5-122B-A10B-int4-AutoRound`: both DFlash entries;
- `nvidia/Qwen3.5-122B-A10B-NVFP4`;
- `nvidia/Qwen3.6-35B-A3B-NVFP4`;
- `z-lab/Qwen3.5-122B-A10B-DFlash`;
- `dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1`.

**Recipes:**
- **The default (`qwen3.8-27b-nvfp4-dflash2`)** runs on SGLang at a 262,144-token context (`max_model_len = 262144`), from the image `lmsysorg/sglang` pinned by digest. Tool calls use `qwen3_coder` with reasoning parser `qwen3`; the DFlash2 drafter speculates 16 tokens; sampling uses PyTorch, not FlashInfer (`--sampling-backend pytorch`); at most 8 requests run at once; `gpu_memory_utilization` is 0.5. Its chat template is patched on a copy at launch (`chat_template_patches`, applied by `ChatTemplatePatcher`).
- **The three vLLM main-model recipes** (the two 122B DFlash entries, the 122B NVFP4 and the 35B) run at a 32k context (`max_model_len = 32768`). Tool calls use `qwen3_xml` with reasoning parser `qwen3`. The two DFlash entries pin their own Docker images (`dreamference-vllm-dflash:0.23.0-aeon-dense5` for hybrid, `…-dense9` for int4) and disable thinking via the chat template.

 See `DREAMFERENCE_INFERENCE.md` and `DREAMFERENCE_CODEBASE.md` §5 for the full launch command.

**Aliases outside the matrix:** a name that is not a matrix key (e.g. `qwen2.5-coder-32b`) still resolves to a tool-call parser by name guess (`hermes` / `mistral`), but has no recipe. The Qwen 2.5 Coder, DeepSeek-R1-Distill, Llama 3.3, StarCoder2 and DeepSeek-V3 entries this document used to list are no longer in the registry.

**Vision:** `supports_vision` is recorded per checkpoint from its `config.json`, never inferred from the alias. `puffin-admin puffin configure` sends it to Onyx as `supports_image_input`, and registers the default vision model.

---

## 2. Default Model Rationale

Decode speed on GB10 is bounded by memory bandwidth, not compute. That favours mixture-of-experts models, whose speed tracks *active* parameters, and speculative decoding, which turns one bandwidth-bound step into several accepted tokens.

**`qwen3.8-27b-nvfp4-dflash2`**, the default since 2026-09-29, is Qwen3.8-27B in NVFP4 with the DFlash2 drafter. DFlash2 runs only in SGLang, which is why this entry names its engine. Measured single-stream, greedy, on this machine: prose 25.5, code 50.3, JSON 87.0 tok/s; prefill about 1,700 tok/s (about 1,000 at 116K tokens); about 38.7 GB of host memory still free; four `puffin` tasks at once finished in 23 s. It matches the 122B's decode speed with a 262k context where the 122B has 32k, and leaves the host far more memory. `DREAMFERENCE_INFERENCE.md` records the four traps its recipe handles.

**`qwen3.5-122b-a10b-hybrid-dflash`**, the fallback, combines:
- Qwen 3.5 122B-A10B as Intel's AutoRound INT4 checkpoint;
- the **z-lab DFlash drafter**, block-speculative: it drafts a whole block in one parallel forward, with 12 speculative tokens;
- the **dense-bandwidth stack** from `github.com/Entrpi/qwen3.5-122B-A10B-on-spark`, baked into the pinned image: FP8 dispatch for dense layers, an int8 w8a16 Triton GEMV lm-head (which frees ~1.4 GiB back to KV), and FLA sm121 shared-memory tuning.

It serves at 32k context with 8 sequences. Measured single-stream on this machine: prose 23.8, code 49.9, JSON 53.1 tok/s. Its first 131k-context launch was refused for KV (9.03 GiB needed, 5.78 free), and that refusal is where the 32k / 8-sequence tuning comes from.

Two upstream defaults are deliberately **not** used:
- **`gpu_memory_utilization` 0.82:** upstream's number assumes a headless machine. This one runs a desktop and froze at 0.80. The launched value is 0.7; the entry's notes still say 0.68.
- **`fastsafetensors` loading:** without GPUDirect Storage, it is a double-residency load peak, which is what freezes this host. The launch passes no `--load-format`, so vLLM's default loader is used.

### 2.1. NVFP4 and the SM121 kernel path

The NVFP4 entries only work on an SM121-safe kernel path. The CUTLASS FP4 kernels are compiled for the SM120 ISA, and on GB10 they run without erroring while producing corrupt output. The recognisable symptom is a response made only of `!` characters. The recipes pin the FlashInfer b12x path, which needs vLLM with the May 2026 SM12x backends.

**Post-launch canary:** `puffin-admin server start` runs one completion (`"Hello"`, 10 tokens) after the server is ready, **only when the alias contains `nvfp4`** (the default's does). It prints `✅ NVFP4 Canary Passed` or `❌ NVFP4 Canary Failed: Output corrupted (all '!')`. It does **not** switch models or relaunch; recovery is manual. `puffin-admin main-model inspect` runs a broader correctness canary on any model.

---

## 3. NVIDIA GB10 Hardware Specification

- **GPU:** NVIDIA GB10 (Blackwell, compute capability SM121).
- **Memory:** 128 GB LPDDR5X, *unified*: CPU and GPU share it. Host RAM exhaustion and GPU memory exhaustion are therefore indistinguishable, and a bad model load can freeze the whole host. That is why the host-safety pre-flight and the PSI watchdog exist (`DREAMFERENCE_INFERENCE.md`).
- **CPU:** Arm cores (`aarch64`).
- **Storage:** NVMe SSD for model caches and indexes.

---

## 4. Hardware Detection & Qualification

`HardwareManager` reads total and available memory from `/proc/meminfo`, and the GPU name, driver and memory from `nvidia-smi --query-gpu=name,driver_version,memory.total`.

**Qualification** (`is_gb10`):
- the GPU name contains `GB10` or `BLACKWELL`; **or**
- total memory ≥ 100 GB. With no `nvidia-smi` GPU name, the GPU is then reported as "NVIDIA GB10 (Simulated / Unified Memory Node)".

There is no environment override. The `DREAMFERENCE_GB10_OVERRIDE` variable this document used to describe does not exist in the code.

---

## See Also

- **[DREAMFERENCE_ARCHITECTURE.md](./DREAMFERENCE_ARCHITECTURE.md):** system overview
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM and SGLang launch recipes and performance flags
