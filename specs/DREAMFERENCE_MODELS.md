# Mightling Supported Models & Hardware

> **Version:** 1.5.1
> **Subject:** NVIDIA GB10 Model Matrix & Default Model Selection
> **Checked against the code:** 2026-10-09 (`dreamference/hardware/model_matrix_registry.py` and `hardware_manager.py`: every alias, repo, memory range and recipe value below was compared with `MATRIX`)

> **2026-10-09: one candidate added.** `qwen3.8-27b-minima-nvfp4-dflash2` (Minima, the all-NVFP4 Qwen3.8-27B) is in `MATRIX` for night 2's A/B (§2.2). It is not the default and is served only when named (`server start --model …`).

---

## Table of Contents

- [1. Supported NVIDIA GB10 Model Matrix](#1-supported-nvidia-gb10-model-matrix)
- [2. Default Model Rationale](#2-default-model-rationale) (§2.2: the Minima candidate and night 2)
- [3. NVIDIA GB10 Hardware Specification](#3-nvidia-gb10-hardware-specification)
- [4. Hardware Detection & Qualification](#4-hardware-detection--qualification)

---

## 1. Supported NVIDIA GB10 Model Matrix

Aliases, HF repos and launch recipes are defined in `ModelMatrixRegistry.MATRIX`. Each entry is a `ModelSpec`, and its `notes` field carries the full history of the recipe; read it before retuning one. All four entries are `compatible_gb10` (Minima, the fourth, since 2026-10-09), and Mightling serves one main model.

| Alias | Model | Params | Format | Memory (min–max GB) | Vision | Role |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `qwen3.8-27b-nvfp4-dflash2` | Qwen 3.8 27B (NVFP4 + DFlash2, SGLang) | 27B | NVFP4 | 20–70 | ✅ | **The** main model (`DEFAULT_MODEL_ALIAS`, since 2026-09-29). Served by **SGLang**, not vLLM (`launch_overrides['engine']`) |
| `qwen3.8-27b-minima-nvfp4-dflash2` | Qwen 3.8 27B Minima (all-NVFP4 + DFlash2, SGLang) | 27B | NVFP4 (compressed-tensors) | 19–70 | — | Candidate for night 2's A/B (§2.2); production's recipe with the checkpoint swapped. Added 2026-10-09 |
| `qwen3.8-27b-dflash2-draft` | Qwen 3.8 27B DFlash2 drafter (NVFP4) | 1B | NVFP4 | 1–2 | — | Drafter named by the main recipe's `speculative_config`; not served on its own. Listed so the memory gates have a size before the snapshot is on disk |
| `tiny-a2d-coder-0.5b-diffusion` | Tiny-A2D Qwen2.5-Coder 0.5B (bd3lm diffusion) | 0.6B | BF16 | 1.5–3 | — | Default diffusion model (`DEFAULT_DIFFUSION_MODEL_ALIAS`); **not offered while diffusion is switched off** (`DIFFUSION_ENABLED = False` since 2026-10-03: never started, downloaded or listed). `is_diffusion`: **not** servable by vLLM; runs in the diffusion sidecar |

**HF repos:**
- `RadixArk/Qwen3.8-27B-NVFP4`: the main model, pinned to revision `52d1adc5…`;
- `maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal`: its drafter, pinned to revision `bd7a9342…`;
- `minima-ai/mnma_qwen3.8_27b_nvfp4`: the night-2 candidate, pinned to revision `16e768e7…`;
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

**Tool-call check (since 2026-10-09):** after the NVFP4 canary, and for every model, `server start` sends one request the way `ling` does: a streamed `/v1/responses` request (upstream Codex speaks only the Responses API) with one trivial tool, `get_weather(city)`, reasoning `none`, at most 256 output tokens and 60 s in all (`ToolCallCanary`, `dreamference/vllm_server/tool_call_canary.py`). It passes when the stream carries a `function_call` item naming the tool, with a `call_id` and arguments that are a JSON object holding `city`, and prints `✅ Tool call well formed: …`. Otherwise it prints `⚠️  Tool-call check failed: …` with the likely culprit, named from the served model's registry entry (looked up from `/v1/models`, not from the configuration): the call written as text means the tool-call parser (`--tool-call-parser qwen3_coder`); a call inside the reasoning means the reasoning parser (`qwen3`) or the template's thinking switch; prose alone, or an HTTP 400, points at the chat template and its launch-time `chat_template_patches`. It warns and does not stop the start: the server is up and the chat UI needs no tools. The existing chat-completions probe in `main-model inspect` does not cover this, because the Responses path converts tools and parses calls in code of its own. Whether a failure should block a start is open: Night Shift and SWE-bench are useless without tool calls, so their own start checks are where a block would belong.

### 2.2. Candidate: Minima, the all-NVFP4 Qwen3.8-27B (night 2; prepared 2026-10-09, not yet run)

**The decision.** The ling-engine survey (ling-engine `SPEC.md` §16.2) found Minima, a published Qwen3.8-27B with every linear layer in NVFP4. Night 2 is a SWE-bench A/B of it against production on this machine with today's SGLang. Production switches only if Minima's quality holds **and** it is faster here. Its own paper saw it decode *slower* than the served recipe at batch 1 (47 against 51 tok/s, one RTX PRO 6000, vLLM 0.27.1), so being faster is not assumed.

**The checkpoint.** `minima-ai/mnma_qwen3.8_27b_nvfp4` at commit `16e768e7d0461b0b86e565ecedd08a24eca53e9a` (published 2026-09-04, Apache 2.0; the paper's link, and the account's only model), from the paper [Why Gated DeltaNet Survives 4-Bit Quantization](https://arxiv.org/abs/2609.04098) (arXiv 2609.04098).
- **What is quantized:** all 496 linear layers in NVFP4 W4A4, group 16. That covers the 48 Gated DeltaNet layers (`in_proj_qkv`, `in_proj_z`, `in_proj_a`, `in_proj_b`, `out_proj`), the 16 attention layers and all 64 MLPs.
- **What stays BF16:** embeddings, `lm_head`, `conv1d`, the norms and `A_log`/`dt_bias`.
- **KV cache:** static FP8 KV-cache scales are calibrated and shipped.
- **Tool and format:** made with llm-compressor; the format is compressed-tensors `nvfp4-pack-quantized`, version 0.18.0.
- **Size:** one 18,788,354,104-byte `model.safetensors`, against about 20 GB for production's FP8/NVFP4 mix.
- **Text only:** the architecture is `Qwen3_5ForCausalLM`, model type `qwen3_5_text`. There is no vision tower and there are no MTP weights.
- **Downloaded** to the HuggingFace cache at that commit, niced, on 2026-10-09: 18 GB, 5 minutes. Both LFS files match the Hub's SHA-256. 252 GB stayed free.

**Servable as-is by the pinned image (`lmsysorg/sglang@sha256:d6e7288…`, v0.5.19, build `0bcd822`): yes.** Checked by reading that image's SGLang source and the checkpoint. Nothing was started.

| Question | Finding |
| :-- | :-- |
| Architecture | `Qwen3_5ForCausalLM` is an `EntryClass` of `models/qwen3_5_text.py`, the text-only entry of the same hybrid stack. It is listed for the mamba radix cache and for its `extra_buffer` strategy, so production's `--mamba-radix-cache-strategy extra_buffer` applies. |
| Format | compressed-tensors NVFP4 is `CompressedTensorsW4A4Fp4`. Its minimum capability is 100 (GB10 is 121), and it calls the same `fp4_quantize`/`fp4_gemm` FlashInfer path that production's NVFP4 MLP layers already run on this machine. |
| Split checkpoint, fused GEMMs | SGLang fuses `in_proj_qkv`+`in_proj_z` into `in_proj_qkvz`, and `in_proj_b`+`in_proj_a`, `q/k/v` and `gate/up` the same way. The separate tensors load through the loader for split shards (`weight_loader_v2`, which takes shard-id tuples). |
| **Global scales of fused groups** | `CompressedTensorsW4A4Fp4.process_weights_after_loading` takes the **largest** global scale of a fused group and does not rescale the block scales. This is the defect the paper describes: per-module calibration (up to 2.8× apart) silently mis-scales the DeltaNet gates, and long-context perplexity looks *better* for it. Minima says it ships harmonized groups. **Checked on the download:** `scripts/check_nvfp4_fused_scales.py` read all 992 global scales, and **352 of 352** fused-group scales are equal (weight and input, every layer). Night 2's `start` repeats the check. |
| Chat template | Byte-identical to production's `chat_template.jinja` (8,952 bytes), so production's `chat_template_patches` anchors match. |
| KV cache | Under `--kv-cache-dtype auto`, SGLang turns a compressed-tensors `kv_cache_scheme` into an FP8 pool (`kv_cache_quant_algo`, duck-typed). Production's pool is BF16. The entry passes `--kv-cache-dtype bfloat16`; the shipped scales load and go unused. |
| DFlash2 drafter | **Applies.** The drafter has no embeddings or `lm_head` (186 tensors: `layers.*`, `fc`, `hidden_norm`, `norm`, `candidate_selector`), so it uses the target's, which here are BF16. It reads hidden states of width 5,120 at layers 5, 19, 33, 47 and 61 of 64 (`num_target_layers` 64, vocabulary 248,320), and Minima has exactly that shape. The text class implements `set_dflash_layers_to_capture`. The drafter was trained against other hidden states, so its acceptance is measured, not assumed. A lower acceptance shows up as lower decode speed, never as different output. |

**What reading could not settle**, and what the night's first start settles:
- **New FP4 GEMM shapes.** Production runs FP4 GEMMs only in its MLPs. Minima adds the DeltaNet projections, the attention projections and the 96-row `in_proj_ba`; vLLM served those shapes on SM120.
- **torch.compile on this model.**
- **The drafter's acceptance.**

A broken kernel on SM121 corrupts silently, so the night checks the output three ways:
1. `server start`'s NVFP4 canary (the alias contains `nvfp4` on purpose);
2. the speed script's answer checks: the prose must be about the topic, the code must compile, the JSON must parse, and a 13K-token retrieval must be right;
3. the benchmark itself.

A start that fails or fails the canary skips arm B and serves production again.

**The registry entry** is `qwen3.8-27b-minima-nvfp4-dflash2`. It is not the default and `DEFAULT_MODEL_ALIAS` is unchanged.
- **One shared recipe.** Production's launch overrides were moved, unchanged, into the module constant `QWEN38_SGLANG_RECIPE`. The entry takes that constant whole and changes two things: the checkpoint's `revision`, and `--kv-cache-dtype bfloat16` appended to `extra_args`.
- **Everything else is shared:** the image, the 0.50 memory fraction, the 262,144-token context, the parsers, the template patches, the drafter at 16 tokens, the PyTorch sampler and torch.compile.
- `supports_vision` is false. Switching production to it would take image input away from the web chat, which is a cost of the switch, not of the A/B.

**Fairness.** The two arms differ in the target's weights alone:
- **Same server setup:** one image, one recipe, one drafter at one depth, one KV dtype, one chat template.
- **Same harness:** one task list and one build of `ling` and the harness. Night 1's default-arm settings are used for both arms: code index universal, masking off, prompt `default`, no task rules, `max_parallel = 2`. `ARM_FLAGS` changes them for both arms at once, for example to `--task-rules tests` if night 1 adopts it.
- **Quality is unaffected by the drafter.** Speculative decoding verifies every token against the target, so the drafter changes speed only and not what is generated.
- **Order.** The arms run one after the other, production first, because a run's instance list is fixed in its manifest. To expose drift, production's speed is measured before arm A and again after arm B.
- **Speed is measured on the night.** The prompts behind production's published figures (25.5 / 50.3 / 87.0) were not recorded. `scripts/decode_speed.py` therefore measures both checkpoints on the same night with one method: single stream, temperature 0, thinking off, a warm-up, the median of three, decode net of TTFT, and a 13,400-token fresh-prefill probe.

**Decision rule**, read in the morning:
- **Quality holds** if Minima's resolved count is no more than 3 below production's on the same 50 tasks, and it has no new failure class (empty patches, timeouts, tool-call errors).
  - Two identical arms disagree on about 15% of tasks (MIGHTLING_SWE_BENCH_FAILURES §6.5), which is 6 to 8 discordant tasks of 50 from noise alone. A drop of more than 3 is therefore read as a loss until a second 50 says otherwise.
- **Faster today** means two things together:
  - Minima's median decode on prose and on code exceeds the higher of production's two readings by more than the gap between those two readings;
  - its median wall time per instance in the reports (`Median wall time`) is not longer.
- **Both must hold to switch.** Prefill and JSON speed are recorded, but neither decides.

**The night** is `scripts/swe_bench_night2.sh`, modelled on night 1's script.
- **`check`:** makes every check and changes nothing. It checks that the 100-task unit and night 1 have finished (night 1's `done` file), that both snapshots are present, that the fused-group scales are harmonized, and that the disk is enough.
- **`start`:** draws the list if it is missing (`scripts/swe_bench_fresh.py draw --count 50 --seed 20261010`), the same stratified draw from the validated pool outside `sample-100.txt` as night 1, with a new seed, written to `~/.cache/dreamference/swe-bench/fresh-50-night2.txt` with its purpose in the header. It then starts the user unit `ling-swe-night2`.
  - The pool is about 80 tasks, so this list overlaps night 1's. That does not bias a comparison of models.
- **`run`** goes through these steps:
  1. Serve production and measure its speed.
  2. Run `n2-prod`, graded as it goes.
  3. Swap to Minima by the normal path (`ling-admin server stop`, then `server start --model qwen3.8-27b-minima-nvfp4-dflash2`), so the host-safety pre-flight, the PSI watchdog, the warm-up and the canary all apply. The swap counts only if the start said ready, the canary did not fail and `/v1/models` names Minima. A swap waits for a Night Shift run to release the runner lock (`puffin-night.timer` fires at 01:00).
  4. Measure Minima's speed and run `n2-minima`.
  5. Serve production again and measure it again.
  6. Write the reports, including `n2-minima --against n2-prod`, into `~/.local/share/dreamference/swe-bench/night2/`.
- **Production comes back whatever happens:**
  - On an error, an EXIT trap restores it, with two attempts, and leaves `RESTORE-FAILED` if both fail.
  - On a stop or a signal, the separate unit `ling-swe-night2-restore` restores it, since a stopped unit's own processes die with it.
  - After a reboot mid-night, Docker restarts whichever container ran last, so `swe_bench_night2.sh restore` is run by hand.
- **Dry runs.** `DRY_RUN=1 … run` prints every step and executes none. `DRY_FAIL=<key>` plays a failed start of that model. Both were run, along with the normal path and the paths where Minima, or production's restore, fails.

**Not done, by instruction:** no quantizing or converting, no model server started or stopped, and no request to the model server.

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
