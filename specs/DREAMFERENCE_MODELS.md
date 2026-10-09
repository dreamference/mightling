# Mightling Supported Models & Hardware

> **Version:** 1.5.1
> **Subject:** NVIDIA GB10 Model Matrix & Default Model Selection
> **Checked against the code:** 2026-10-09 (`dreamference/hardware/model_matrix_registry.py` and `hardware_manager.py`: every alias, repo, memory range and recipe value below was compared with `MATRIX`)

---

## Table of Contents

- [1. Supported NVIDIA GB10 Model Matrix](#1-supported-nvidia-gb10-model-matrix)
- [2. Default Model Rationale](#2-default-model-rationale)
- [3. NVIDIA GB10 Hardware Specification](#3-nvidia-gb10-hardware-specification)
- [4. Hardware Detection & Qualification](#4-hardware-detection--qualification)

---

## 1. Supported NVIDIA GB10 Model Matrix

Aliases, HF repos and launch recipes are defined in `ModelMatrixRegistry.MATRIX`. Each entry is a `ModelSpec`, and its `notes` field carries the full history of the recipe; read it before retuning one. All three entries are `compatible_gb10`, and Mightling serves one main model.

| Alias | Model | Params | Format | Memory (min–max GB) | Vision | Role |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `qwen3.8-27b-nvfp4-dflash2` | Qwen 3.8 27B (NVFP4 + DFlash2, SGLang) | 27B | NVFP4 | 20–70 | ✅ | **The** main model (`DEFAULT_MODEL_ALIAS`, since 2026-09-29). Served by **SGLang**, not vLLM (`launch_overrides['engine']`) |
| `qwen3.8-27b-dflash2-draft` | Qwen 3.8 27B DFlash2 drafter (NVFP4) | 1B | NVFP4 | 1–2 | — | Drafter named by the main recipe's `speculative_config`; not served on its own. Listed so the memory gates have a size before the snapshot is on disk |
| `tiny-a2d-coder-0.5b-diffusion` | Tiny-A2D Qwen2.5-Coder 0.5B (bd3lm diffusion) | 0.6B | BF16 | 1.5–3 | — | Default diffusion model (`DEFAULT_DIFFUSION_MODEL_ALIAS`); **not offered while diffusion is switched off** (`DIFFUSION_ENABLED = False` since 2026-10-03: never started, downloaded or listed). `is_diffusion`: **not** servable by vLLM; runs in the diffusion sidecar |

**HF repos:**
- `RadixArk/Qwen3.8-27B-NVFP4`: the main model, pinned to revision `52d1adc5…`;
- `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal`: its drafter, pinned to revision `bd7a9342…`;
- `dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1`: the diffusion model.

**The recipe (`qwen3.8-27b-nvfp4-dflash2`)** runs on SGLang from `lmsysorg/sglang` pinned by digest (v0.5.19, from github.com/hasso5703/dgx-spark-qwen38 v1.18):
- context 262,144 tokens (`max_model_len`); `gpu_memory_utilization` 0.50; `container_headroom_gb` 24;
- tool calls `qwen3_coder`, reasoning parser `qwen3`;
- DFlash2 drafter (`method: DFLASH`, `quantization: modelopt_fp4`) with 16 speculative tokens;
- `extra_args`: flashinfer attention, the PyTorch sampler (`--sampling-backend pytorch`, not FlashInfer), chunked prefill 8,192, no prefill CUDA graph, CUDA graphs up to batch 8, no FlashInfer autotune, the mamba radix cache in `extra_buffer` mode with a bf16 SSM state and at most 96 cached states, at most 8 running requests, torch.compile up to batch 4, two continuous decode steps, `--sleep-on-idle` and `--enable-metrics`;
- two `chat_template_patches`, applied on a copy at launch by `ChatTemplatePatcher`: reasoning efforts `max`/`high` map to `xhigh` and `minimal` to `low` (the stock template answered HTTP 400 to Codex's efforts), and a system message after the first becomes a `<system-reminder>` user turn instead of raising an exception.

See `DREAMFERENCE_INFERENCE.md` and `DREAMFERENCE_CODEBASE.md` §5 for the full launch command.

**Removed models.** On 2026-10-07 the Qwen 3.5 122B-A10B entries (`qwen3.5-122b-a10b-hybrid-dflash`, `-int4-dflash`, `-nvfp4`), `qwen3.6-35b-a3b-nvfp4` and the 122B drafter `qwen3.5-122b-a10b-dflash-draft` were removed, with `Dockerfile.dflash`, `Dockerfile.dense` and `runtime/`. `REMOVED_MODELS` keeps their aliases, display names and repos, all mapped to release `1.5.1`. `ModelMatrixRegistry.removed_in()` recognises them, `is_offered()` refuses them, and `server start`, `model download` and `main-model set` print `removed_message()`: which release removed the model, whether it came from the configuration, and the `ling-admin main-model set` command that switches to the main model. The vLLM launcher is kept and tested against test-only recipes (`vllm_recipes` in `tests/conftest.py`).

**Aliases outside the matrix:** a name that is neither a matrix key nor a removed model (e.g. `qwen2.5-coder-32b`) still resolves to a tool-call parser by name guess (`hermes` / `mistral`), but has no recipe.

**Vision:** `supports_vision` is recorded per checkpoint from its `config.json`, never inferred from the alias. `ling-admin chat configure` sends it to Onyx as `supports_image_input`, and registers the default vision model.

---

## 2. Default Model Rationale

Decode speed on GB10 is bounded by memory bandwidth, not compute. That favours speculative decoding, which turns one bandwidth-bound step into several accepted tokens, and small weights.

**`qwen3.8-27b-nvfp4-dflash2`**, the main model since 2026-09-29, is Qwen3.8-27B in NVFP4 with the DFlash2 drafter. DFlash2 runs only in SGLang (vLLM supports it only through an unmerged pull request), which is why this entry names its engine. Measured single-stream, temperature 0, thinking off, on this machine: prose 25.5, code 50.3, JSON 87.0 tok/s; time to first token 0.22 s; prefill about 1,700 tok/s (about 1,000 at 116K tokens); first boot 7.5 min (torch.compile); about 38.7 GB of host memory still free while serving; four `ling` tasks at once finished in 23 s. Against the removed 122B it matched decode speed on code (49.9) and prose (23.8), with a 262K context where the 122B had 32K, about 20 GB of weights against about 71, and the same `ling exec` task in 10 s against 34 s. `DREAMFERENCE_INFERENCE.md` records the four traps its recipe handles.

**History.** From 2026-08-23 to 2026-09-29 the default was `qwen3.5-122b-a10b-hybrid-dflash`: Intel's AutoRound INT4 checkpoint of Qwen 3.5 122B-A10B with the z-lab DFlash drafter (12 speculative tokens) and the dense-bandwidth stack from `github.com/Entrpi/qwen3.5-122B-A10B-on-spark` in a pinned image, served by vLLM at 32K context and `gpu_memory_utilization` 0.7. Two upstream defaults were deliberately not used there and the lessons still hold for any vLLM recipe: 0.82 memory utilisation (it assumes a headless machine; this one froze at 0.80) and `fastsafetensors` loading (without GPUDirect Storage it is a double-residency load peak).

### 2.1. NVFP4 and the SM121 kernel path

NVFP4 needs an SM121-safe kernel path. The CUTLASS FP4 kernels are compiled for the SM120 ISA, and on GB10 they run without erroring while producing corrupt output; the recognisable symptom is a response made only of `!` characters. The removed vLLM recipes pinned the FlashInfer b12x path for that reason. On SGLang the same symptom came from FlashInfer's untruncated-sampling kernel on the completions path (token 0, `!`, for every sampled request), which is why the recipe samples with PyTorch.

**Post-launch canary:** `ling-admin server start` runs one completion (`"Hello"`, 10 tokens) after the server is ready, **only when the alias contains `nvfp4`** (the default's does). It prints `✅ NVFP4 Canary Passed` or `❌ NVFP4 Canary Failed: Output corrupted (all '!')`. It does **not** switch models or relaunch; recovery is manual. `ling-admin main-model inspect` runs a broader correctness canary on any model.

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
- the GPU name `nvidia-smi` reports contains `GB10`; **or**
- the GB10's GPU is on the PCI bus (vendor `0x10de`, device `0x2e12`, read from `/sys/bus/pci/devices`), which covers a machine whose driver does not answer yet. The GPU is then reported as "NVIDIA GB10 (no driver answering: nvidia-smi is missing or failed)".

Nothing else qualifies: "Blackwell" also names discrete cards (RTX PRO 6000 Blackwell), and a large x86 server has 100 GB of RAM, so the earlier `BLACKWELL` and memory-size rules were dropped. The vendor's DMI strings are never used either, since each GB10 machine names itself differently (this one says `GX10`). `HardwareTelemetry` also carries the machine and OS names.

There is no environment override. The `DREAMFERENCE_GB10_OVERRIDE` variable this document used to describe does not exist in the code.

---

## See Also

- **[DREAMFERENCE_ARCHITECTURE.md](./DREAMFERENCE_ARCHITECTURE.md):** system overview
- **[DREAMFERENCE_INFERENCE.md](./DREAMFERENCE_INFERENCE.md):** vLLM and SGLang launch recipes and performance flags
