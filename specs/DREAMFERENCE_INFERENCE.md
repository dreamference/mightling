# Mightling GB10 Inference Stack

> **Version:** 1.5.1
> **Subject:** vLLM and SGLang Launch Engines, Auto-Configuration, & Performance Optimization
> **Checked against the code:** 2026-10-09 (`dreamference/vllm_server/vllm_server_manager.py`, `sglang_launch_builder.py`, `psi_watchdog.py`; constants compared value by value). Since 2026-10-07 the registry has one main model, served by SGLang; the vLLM path is kept and tested against test-only recipes (`vllm_recipes` in `tests/conftest.py`), so the vLLM sections below describe the launcher, not a model in use.

---

## Table of Contents

- [1. GB10 Inference Stack Overview](#1-gb10-inference-stack-overview)
- [2. Weight Pre-Download & Management](#2-weight-pre-download--management)
- [3. Speculative Decoding](#3-speculative-decoding)
- [4. Launch Flags](#4-launch-flags)
- [5. Per-Model Launch Recipes](#5-per-model-launch-recipes)
- [6. Base Configuration & Module Defaults](#6-base-configuration--module-defaults)
- [7. Host Safety](#7-host-safety)
- [8. Readiness Polling & Live Streaming](#8-readiness-polling--live-streaming)

---

## 1. GB10 Inference Stack Overview

The server is started only by `ling-admin server start`. Agents never start it: they wait for it (`DREAMFERENCE_AGENTS.md` §6, or `ling`'s launcher).

```
ling-admin server start [--model <alias>]
    ↓
Resolve alias → HF repo, recipe (launch_overrides), Docker image
    ↓
Host-safety pre-flight (check_host_safety): swap, sysctl, earlyoom / systemd-oomd, memory arena
    ↓
Pre-download weights: main model, --draft-model, and the recipe's own drafter (DFlash2), at pinned revisions
    ↓
Reset a stale vLLM torch.compile cache if the speculative signature changed (vLLM only)
    ↓
Diffusion sidecar: switched off since 2026-10-03, so a leftover dreamference-diffusion-8001 is removed
(with DIFFUSION_ENABLED on: started first, unless --no-diffusion)
    ↓
Build the docker run command: python3 -m sglang.launch_server … for an `engine: sglang` recipe (the main model),
vllm serve … otherwise (explicit args > recipe > module defaults)
    ↓
Run the container under the PSI MemoryPressureWatchdog
    ↓
Stream logs and Docker memory; poll /v1/models until healthy
    ↓
NVFP4 canary (nvfp4 aliases only); tool-call check through /v1/responses (every model; MODELS §2.1); report diffusion sidecar state (when diffusion is on)
    ↓
Exit; the containers keep running (--restart unless-stopped)
```

---

## 2. Weight Pre-Download & Management

- `start_server()` calls `download_model()` for the main model, for `--draft-model` if given, and for the recipe's drafter (`get_speculative_draft_repo`) before the container starts. vLLM therefore never has to fetch weights during the load, which is the one phase this host cannot afford surprises in.
- `ModelDownloader` tries `huggingface_hub.snapshot_download` first, then the `huggingface-cli` binary. If both fail, the fetch is left to vLLM.
- The cache is `~/.cache/huggingface/hub/` (`$HF_HOME/hub` if set, `$HF_HUB_CACHE` if that is set), resolved as huggingface_hub resolves it. `ModelDownloader.container_volume_args()` mounts the resolved home into every model container (server, diffusion sidecar, tensorizer) at `/root/.cache/huggingface`, and a hub moved by `HF_HUB_CACHE` at `/root/.cache/huggingface/hub`, so snapshot paths handed to the engine exist inside it. Until 2026-09-30 the mount was always `~/.cache/huggingface`, whatever `HF_HOME` said.

### 2.1. HF Token Handling

The token is the first of:
1. CLI `--hf-token`;
2. `HF_TOKEN`, then `DREAMFERENCE_HF_TOKEN`;
3. the `hf_token` config key.

It is passed to the container as `-e HF_TOKEN=…`. When none is set, `huggingface_hub` falls back to its own stored login (`~/.cache/huggingface/token`) on the host side.

---

## 3. Speculative Decoding

### 3.1. Two ways to get a drafter

- **Recipe (normal case):** the model's `launch_overrides["speculative_config"]`. On **SGLang** (the main model) it becomes `--speculative-algorithm DFLASH --speculative-draft-model-path <snapshot> --speculative-num-draft-tokens 12 --speculative-draft-model-quantization modelopt_fp4`, from `{"method": "DFLASH", "model": "maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal", "revision": "bd7a934…", "num_speculative_tokens": 12, "quantization": "modelopt_fp4"}` (`SGLangLaunchBuilder.server_args`). On **vLLM** it is serialised to `--speculative-config`; the removed 122B recipes used `{"method": "dflash", "model": "z-lab/Qwen3.5-122B-A10B-DFlash", "num_speculative_tokens": 12, "attention_backend": "FLASH_ATTN"}`, and MTP recipes (heads inside the checkpoint) use the same key with `"method": "mtp"`. Either way the drafter is a separate checkpoint, pre-downloaded and counted in the memory budget.
- **Explicit `--draft-model` (vLLM recipes only; the SGLang path takes its drafter from the recipe alone and ignores the flag):** emitted as `--speculative-config` JSON, built by `resolve_speculative_config()`; vLLM 0.2x has no `--speculative-model` or `--num-speculative-tokens` flag. `"model"` is the draft's resolved HF repo.

**Precedence:** with both present, the explicit draft is **layered onto** the recipe when the recipe also uses an external drafter (it has a `"model"`): `"model"` is replaced, and the recipe's method and drafter attention backend are kept. A self-speculation recipe (MTP, no `"model"`) is replaced outright, since its method means nothing for a separate checkpoint. There is no fail-fast validation of the combination. Until 2026-09-29 the explicit path emitted the removed `--speculative-model` flags and failed at argument parsing.

**Compatibility:** a drafter must share the target's tokenizer. The DFlash2 drafter is built for Qwen3.8-27B.

### 3.2. Speculative token count

- **With a recipe:** its `speculative_config` carries the count. The main model uses 16 (12 was measured +7% at one stream and neutral at two on the second GB10, 2026-10-08; the change waits for the benchmark to finish).
- **With an explicit draft:** `ling-admin server start` passes the config's resolved `num_speculative_tokens` — `--num-speculative-tokens` > `DREAMFERENCE_SPECULATIVE_TOKENS` > file > `DEFAULT_SPECULATIVE_TOKENS = 8` — so a draft without the flag gets 8, not the recipe's 12. `start_server()` / `build_launch_command()` default to 5 only when called directly from Python.
- **Without a draft**, the depth argument is ignored and the recipe's config is passed exactly.

Until 2026-09-29 the raw flag was passed, and an omitted `--num-speculative-tokens` reached the command line as `None`.

### 3.3. torch.compile cache

This applies to vLLM only; SGLang's torch.compile output lives under `~/.cache/dreamference/sglang/inductor` and is not reset. vLLM keys its compile cache on the engine config, but `SpeculativeConfig.compute_hash()` does not include `num_speculative_tokens`. `_reset_stale_compile_cache()` keeps its own `model|method|n` signature and clears the cache when it changes. It clears it *inside* the image, because the cache is root-owned. The cache lives at `VLLM_CACHE_ROOT=/root/.cache/dreamference/vllm`, which is persistent: a cold compile takes 8–12 minutes.

---

## 4. Launch Flags (vLLM)

These are vLLM's flags. The SGLang launch is built separately by `SGLangLaunchBuilder.server_args()` (§5.3).

| Flag | Default | Source | Effect |
| :--- | :--- | :--- | :--- |
| `--enable-prefix-caching` | on | Config; forced **off** when the recipe says `enable_prefix_caching: false` | KV prefix reuse |
| `--enable-chunked-prefill` | on | Config | Chunked prefill |
| `--max-num-batched-tokens` | `8192` | Recipe | Prefill chunk size |
| `--kv-cache-dtype` | `auto` | Recipe; config override | KV precision |
| `--attention-backend` | omitted when `auto` | Recipe | Attention kernels |
| `--moe-backend` | unset | Recipe | MoE kernels (must be SM121-safe) |
| `--tool-call-parser` | resolved | Explicit > recipe > name guess (`mistral` / `hermes`) | Tool-call format (the removed Qwen 3.5/3.6 vLLM recipes used `qwen3_xml`) |
| `--reasoning-parser` | unset | Recipe (`qwen3`) | Reasoning channel |
| `--structured-outputs-config.backend` | `xgrammar` | `guided_decoding_backend` | Structured outputs. On images older than vLLM v0.12 it is spelled `--guided-decoding-backend`, chosen by probing the image |
| `--override-generation-config` | `{"temperature": 0.0, "top_p": 1.0, "top_k": 0}` | `DEFAULT_GENERATION_OVERRIDES`; a recipe may override or disable it (`generation_overrides`) | Deterministic sampling unless the client asks otherwise |
| `--speculative-config` | unset | Recipe | See §3 |
| `--quantization` | unset | Explicit; **fp8** for `70b`/`72b` names whose checkpoint does not declare its own quantization | Self-declaring formats (NVFP4, AutoRound, AWQ, GPTQ, …) are left to vLLM's detection |
| `--load-format` | omitted | Recipe `load_format`; `tensorizer` when tensorized weights are used | `DEFAULT_LOAD_FORMAT = "auto"`, deliberately **not** `fastsafetensors` (see §6) |

---

## 5. Per-Model Launch Recipes

`ModelSpec.launch_overrides` carries each model's vLLM recipe as registry data. `build_launch_command` resolves every setting in this order:

```
explicit caller argument  >  model launch_overrides  >  module default
```

`None` counts as unset, and so does `attention_backend='auto'`. Per-model tuning belongs in the registry entry, not in the launch builder, and the tests assert this layering.

### 5.1. Recipe Keys

The keys mirror `build_launch_command`'s parameters: `max_model_len`, `gpu_memory_utilization`, `kv_cache_dtype`, `attention_backend`, `moe_backend`, `tool_call_parser`, `reasoning_parser`, `max_num_batched_tokens`, `guided_decoding_backend`, `use_tensorizer`, `load_format`. On top of those:

| Key | Effect |
| :-- | :----- |
| `docker_image` | Pins this model's vLLM image, overriding `DEFAULT_VLLM_IMAGE` (the engine is part of the recipe) |
| `env` | `Dict[str, str]` exported with `docker run -e K=V`. Some kernel selection is read from the environment, not flags |
| `speculative_config` | Serialised to `--speculative-config` |
| `enable_prefix_caching` | `false` forces prefix caching off, whatever the config says (hybrid-GDN checkpoints cannot honour it) |
| `generation_overrides` | Replaces, or with `None` disables, the default `--override-generation-config` |
| `extra_args` | Verbatim flags appended to the command |
| `engine` | `sglang` serves the model with SGLang instead of vLLM (§5.3) |
| `revision` | Pinned checkpoint commit: downloaded at that commit and served from its snapshot directory |
| `chat_template_patches` | (anchor, replacement) pairs applied to a copy of the checkpoint's chat template at launch (§5.3) |
| `container_headroom_gb` | Memory added to the engine's fraction when sizing the container's cgroup cap |

### 5.2. Removed vLLM Recipes

Until 2026-10-07 four vLLM recipes were in the registry: the Qwen 3.5 122B-A10B hybrid DFlash (the default from 2026-08-23 to 2026-09-29: image `dreamference-vllm-dflash:0.23.0-aeon-dense5`, 32K context, `gpu_memory_utilization` 0.7, `flash_attn`, 9,048 batched tokens, `qwen3_xml`/`qwen3`, DFlash with 12 tokens, `VLLM_MARLIN_USE_ATOMIC_ADD=1` and `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0`, 8 sequences, thinking off in the chat template), the INT4 DFlash, the 122B NVFP4 and the 35B. They were removed with their images and `runtime/` patches; `DREAMFERENCE_MODELS.md` §1 says how a removed alias is answered. The test suite keeps equivalent recipes (`vllm_recipes` in `tests/conftest.py`) so the vLLM path stays covered.

### 5.3. The SGLang Engine and the Main Model (`qwen3.8-27b-nvfp4-dflash2`, since 2026-09-29)

One entry is served by SGLang, because its speed is in a drafter only SGLang runs: Qwen3.8-27B's DFlash2 is a block-diffusion drafter that vLLM supports only through an unmerged pull request. The recipe follows hasso5703/dgx-spark-qwen38 (MIT), measured on a GB10.

| Setting | Value |
| :------ | :---- |
| `engine` | `sglang` |
| `docker_image` | `lmsysorg/sglang@sha256:d6e7288627be…` (v0.5.19, pinned by digest) |
| checkpoint | `RadixArk/Qwen3.8-27B-NVFP4` @ `52d1adc`, served as its snapshot directory |
| drafter | `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal` @ `bd7a934`, 12 draft tokens (the recipe's 16 until 2026-10-09: 12 measured +7% on the replay set, 48.3 against 45.1 tok/s single stream, two runs each in one session, acceptance 5.23 against 4.99 per step, speculation still exact; 8, 10 and 14 gained nothing), `modelopt_fp4` |
| memory | `--mem-fraction-static 0.50`, cgroup cap = fraction + 24 GB headroom |
| context | 262,144 tokens |
| parsers | `qwen3_coder` tools, `qwen3` reasoning |
| `extra_args` | flashinfer attention, chunked prefill 8192, mamba radix cache `extra_buffer`, bf16 SSM state, 96 mamba slots, 8 running requests, torch.compile to batch 4, 2 continuous decode steps, `--sleep-on-idle`, `--enable-metrics` |

**How it is wired.** `SGLangLaunchBuilder` builds only the arguments after the image: `python3 -m sglang.launch_server --model-path <snapshot> --trust-remote-code --served-model-name <repo> --host 0.0.0.0 --port <port> --tp-size 1 --mem-fraction-static <f> --context-length <n>`, the parsers, the drafter flags (§3.1) and the recipe's `extra_args`; the container comes from the same `docker run` prefix as vLLM's, so the memory cap, CPU limit, OOM score, mounts and PSI watchdog are engine-independent. Both engines get `VLLM_NO_USAGE_STATS=1` and `DO_NOT_TRACK=1`. The served model name is the repository ID, as with vLLM, so clients see no difference.

**Four things found on the first launches, each now handled in code:**
- **Revisions.** SGLang drops `--revision` on some offline config lookups, which then resolve through `refs/main`; a download by commit writes none, and the first launch failed in a restart loop. Pinned checkpoints are passed as their snapshot directories.
- **The chat template.** Qwen3.8's own template answered HTTP 400 to the reasoning efforts `high`/`minimal` that Codex offers, and refused a system message after the first. `ChatTemplatePatcher` writes a patched copy under `~/.cache/dreamference/chat-templates` before launch (`high`/`max` → `xhigh`, `minimal` → `low`, a late system message becomes a `<system-reminder>`), and the start stops if an anchor no longer matches. The default effort also drops from `xhigh` to `medium`: the recipe's author measured `xhigh` at 3.19× the thinking tokens and a lower HumanEval (93.9% against 98.2%). ling sends `none` and is unaffected.
- **The sampler.** FlashInfer's kernel for *untruncated* sampling (top_p 1 and no top_k, which is what the completions endpoint does by default, since only the chat path applies the checkpoint's generation defaults) returned token 0, `!`, for 16 of 16 sampled completions requests on this GB10. Any top_p < 1 or any top_k was clean, and so was greedy decoding, which is why chat and ling never showed it. The NVFP4 canary in `server start`, which samples with defaults on purpose, caught it. The recipe passes `--sampling-backend pytorch`: 0 of 16 corrupted, no measurable speed cost.
- **The compile cache.** vLLM's signature-based reset is skipped for SGLang, whose torch.compile output lives under `~/.cache/dreamference/sglang/inductor`.

**Measured on this GB10 (2026-09-29):** single stream, temperature 0, thinking off, median of three after a warm-up, decode net of time to first token:

| | Qwen3.8-27B (SGLang + DFlash2) | 122B hybrid-dflash (vLLM + DFlash) |
| :-- | --: | --: |
| Prose | 25.5 tok/s | 23.8 |
| Code | 50.3 | 49.9 |
| JSON | 87.0 | 53.1 |
| Prefill, 13K fresh tokens | 1,688 tok/s | not measured |
| Prefill, 116K fresh tokens (needle retrieved) | 1,004 tok/s | beyond its 32K window |
| Image input | correct ("Red; 42") | — |
| 4 ling tasks at once | all pass, 23 s wall, ≥39.8 GB available | not measured |

**Measured again on 2026-10-09 at 12 draft tokens** (`scripts/decode_speed.py --runs 3`, whose prompts are in the script, so this row can be reproduced; the 2026-09-29 prompts were not recorded, so the two rows are not a like-for-like comparison): prose 32.0 tok/s, code 68.6, JSON 84.1 (accepted drafts per step 3.4, 4.4 and 5.9), time to first token 0.19–0.20 s, prefill of 13,243 fresh tokens in 7.3 s (1,805 tok/s); every output passed its check and the three runs of each prompt were identical. The change from 16 to 12 draft tokens was decided on ling-engine's replay of recorded agent sessions (+7% single-stream, 48.3 against 45.1 tok/s; neutral with two streams, +1.7% aggregate, within noise; `ling-engine/reports/M0.md`).
| Host memory available while serving | ~38.7 GB | ~10 GB |
| Live slash-command suite | 75 pass, 2 skip (651 s with the fixed harness; 1,101 s before) | 75 pass, 2 skip (807 s, old harness) |

Qwen3.8 figures are after the sampler change (the first measurement, before it, was 24.1 / 47.5 / 82.5). The 122B figures came from different prompts (`main-model inspect`), so decode is a tie within noise on prose and code. The suite's longer wall clock on the first runs was the harness, not the model: it waited up to 30 s to see a busy marker that a fast turn never showed (see `specs/README.md`).

The resulting command is listed flag by flag in `DREAMFERENCE_CODEBASE.md` §5. The CUTLASS FP4 path corrupts output on SM121 (`DREAMFERENCE_MODELS.md` §2.1); the removed vLLM NVFP4 recipes selected FlashInfer's b12x kernels through `env` and `moe_backend` for that reason.

---

## 6. Base Configuration & Module Defaults

These are always emitted on vLLM:

```
--host 0.0.0.0 --port <port> --max-model-len <n> --gpu-memory-utilization <f>
--trust-remote-code --async-scheduling
--enable-log-requests --enable-log-outputs --max-log-len 2048
--enable-auto-tool-choice            (unless disabled)
```

**Module defaults**, used only when neither the caller nor the recipe sets a value:
- `DEFAULT_MAX_MODEL_LEN = 16384`;
- `DEFAULT_GPU_MEMORY_UTILIZATION = 0.50`;
- `DEFAULT_KV_CACHE_DTYPE = "auto"`;
- `DEFAULT_LOAD_FORMAT = "auto"`.

The main model's recipe sets its own context length and utilisation.

**No `fastsafetensors`.** It used to be hardcoded. Without GPUDirect Storage, which GB10 lacks, it double-resides the checkpoint during load, and that peak is what froze this host. vLLM's own loader is used unless a recipe asks otherwise.

**Container:**
- `--ipc=host --network host --restart unless-stopped --gpus all`;
- `--cpus` and a memory limit derived from `gpu_memory_utilization` plus headroom, below total memory minus a 12 GB host reserve (`HOST_MEMORY_RESERVE_GB`);
- `--memory-swap` equal to the memory limit, and `--oom-score-adj=800`;
- env `VLLM_NO_USAGE_STATS=1` and `DO_NOT_TRACK=1` for every engine; for vLLM also `VLLM_CACHE_ROOT`, `CUTE_DSL_ARCH=sm_121a`, `VLLM_LOGGING_LEVEL=DEBUG` and API request/response debug logging; for SGLang `HF_HUB_OFFLINE=1` and `TORCHINDUCTOR_CACHE_DIR` (§5.3).

---

## 7. Host Safety

On GB10, host RAM and GPU memory are the same memory. A load that exhausts it can freeze the whole machine rather than OOM the container. There are two layers, both of which must be kept when touching `start_server()`:

- **Before the load:** `check_host_safety()` inspects swap (at least 64 GB, `MIN_SWAP_GB`), `sysctl` values (`vm.min_free_kbytes` ≥ 1,048,576 and `vm.watermark_scale_factor` ≥ 200) and whether `earlyoom` or `systemd-oomd` is present and configured (earlyoom's memory threshold at most 6%, `MAX_EARLYOOM_MEM_PCT`; the suggested setting is `-m 5,2 -s 100 -r 60`). `ling-admin server start` also refuses while a Night Shift run holds its lock, and stops running `mightling-index-*` scopes first. `start_server()` then checks that the weights plus drafters fit the arena (`total × gpu_memory_utilization`), that at least `HOST_MEMORY_RESERVE_GB` (12 GB) stays outside it, and that the arena plus transient load overhead fits in currently free memory. Either one aborts with an explanation rather than risking a lockup.
- **During the load, `MemoryPressureWatchdog` (`psi_watchdog.py`):** it samples `/proc/pressure/memory` once a second and resolves the container's cgroup. It trips when `full avg10` ≥ 60% holds for 5 s (`PSI_FULL_LIMIT_PCT`, `PSI_TRIP_DURATION_S`), or at once when `full avg60` ≥ 25% (`PSI_SUSTAINED_AVG60_PCT`). The kill paths, in order:
  1. direct `SIGKILL` to the cgroup's PIDs, if permitted;
  2. a kill request over dockerd's unix socket;
  3. the `docker` CLI.

The diffusion sidecar has no watchdog: its fixed `--memory=8g` limit (swap equal) contains the worst case. It starts before vLLM, so that vLLM's free-memory pre-flight sees it. Since 2026-10-03 diffusion is switched off (`DIFFUSION_ENABLED = False`), so no sidecar runs and none of this applies until the switch is turned back on.

---

## 8. Readiness Polling & Live Streaming

- `ModelLoadingMonitor` pipes the container's logs to stdout and prints `[HH:MM:SS] 📊 Reserved memory (Docker): …` every 10 seconds. It tracks loading stages and polls `/v1/models` until healthy.
- `VLLMStartupMonitor` adds memory-growth tracking and stall detection with progress percentages.
- `server start` exits once healthy, and the server keeps running.
- Ctrl+C during the wait prints `⏹️  Shutting down server...` and terminates the monitored `docker run` process: `terminate`, then `kill` after 5 s. Follow it with `ling-admin server stop` to be certain the container is gone.

---

## See Also

- **[DREAMFERENCE_MODELS.md](./DREAMFERENCE_MODELS.md):** models and the default model
- **[DREAMFERENCE_DOCKER.md](./DREAMFERENCE_DOCKER.md):** images and tensorization
- **[DREAMFERENCE_AGENTS.md](./DREAMFERENCE_AGENTS.md):** agents and how they wait for the server
