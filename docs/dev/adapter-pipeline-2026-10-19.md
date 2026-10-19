# Per-language adapters: the Rust data and training pipeline (2026-10-19)

The owner decided on 2026-10-19 that per-language specialists come first (Rust, C++/CUDA, TypeScript), published as signed LoRA adapters over the served base, each with a small drafter adapter later. This note covers the first one, **Rust** (the owner's choice, the same day): data sources and licences, the generation pipeline, the training tool and configs, serving, the evaluation against the owner's publishing bar, and time estimates. No GPU was used: the scripts were checked on the CPU, against the local checkpoint's headers and tensors, and on a tiny model of the same architecture.

Everything named below is in `scripts/adapters/` (tests: `tests/test_adapter_pipeline.py`).

## 1. Decisions in one screen

| Question | Decision | Why, in one line |
| --- | --- | --- |
| First language | **Rust** | About 3,100 real Rust tasks with fail-to-pass tests and recipes, plus 5,300 synthetic ones, against about 400 usable C/C++ tasks; one `cargo test` environment shape; `rust:` base images are multi-arch (§3) |
| Evaluation set | **SWE-bench Multilingual, Rust subset, 43 tasks** (owner) | Held out by instance, by repository at its base commit, and by repository (§3.3) |
| Training data | Our own `ling` trajectories against the served model, rejection-sampled (tests pass) on SWE-rebench-V2 and SWE-smith-rs tasks | Same tools, prompt, template and model as at use; no third-party outputs (§4) |
| Training sequence | The exact prompt token ids SGLang fed the model, from its request dumps | No re-implementation of the Responses-to-chat-template translation (§4.4) |
| Frozen base | **The served FP8/NVFP4 bytes, dequantized inside each matmul** (`ServedLinear`) | Exactly the served weight values at about 21 GB; BF16 dequantization is exact too but does not fit 48K; NF4 is a different model (§5.2) |
| Tool | **PEFT on transformers 5.x, with a ~300-line loop** (`train_lora.py`); Unsloth/Axolotl NF4 QLoRA as the A/B arm and fallback | The served-format base needs a custom layer anyway; nothing else trains against it (§5.1) |
| Targets | `q/k/v/o_proj`, `linear_attn.in_proj_qkv/in_proj_z/out_proj`, `gate/up/down_proj`; rank 16 (108.8M parameters), rank 32 as the second arm | Exactly what the pinned SGLang serves with LoRA (§5.3) |
| Context | `max_seq_len` 51,200 | The nights' 49,152-token `task_context` caps the prompt; the extra 2K holds the last completion; 100% of recorded segments fit, 62% fit 32K (§4.4) |
| Serving | Unmerged, SGLang multi-LoRA, adapter exported in the split `in_proj_q/k/v/z` form | SGLang v0.5.21 does not read PEFT's `in_proj_qkv` name (§6) |

## 2. The base as it is served

`RadixArk/Qwen3.8-27B-NVFP4` (from `Qwen/Qwen3.8-27B` revision `e13a4f0e`, Apache-2.0) is ModelOpt **`MIXED_PRECISION`**, not plain NVFP4 (read from `hf_quant_config.json` and the shard headers):

| Tensors | Format | Count |
| --- | --- | --- |
| `self_attn.{q,k,v,o}_proj` | FP8 E4M3, per-tensor weight and static input scale (W8A8) | 16 layers |
| `linear_attn.{in_proj_qkv,in_proj_z,out_proj}` | FP8 E4M3 (W8A8) | 48 layers |
| `mlp.{gate,up,down}_proj`, `lm_head` | NVFP4: E2M1 values, E4M3 scale per 16 inputs, FP32 global scale; inputs quantized the same way (W4A4) | 64 layers + 1 |
| embeddings, norms, `linear_attn.{in_proj_a,in_proj_b,conv1d,A_log,dt_bias}`, `mtp.*`, vision | BF16 | |

KV cache FP8. Architecture `Qwen3_5ForConditionalGeneration`, text model `qwen3_5_text`: 64 layers in a 3:1 pattern of Gated DeltaNet (48) and gated full attention (16, at layers 3, 7, …, 63), hidden 5,120, MLP 17,408, vocabulary 248,320, untied embeddings. The DFlash2 drafter (`maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal`) reads the target's hidden states at layers 5, 19, 33, 47 and 61, which is why any adapter moves its acceptance (§7.3).

The owner's memory figures (50 GB at 32K, 75 GB at 64K) assumed a 4-bit base of about 14 GB; the served bytes are 21 GB, and a bottom-up estimate gives somewhat more activation memory (§5.6).

## 3. Language, sources and licences

### 3.1 Task sources (counts read from the datasets on 2026-10-19)

| Source (HF id, revision) | Rust tasks | Licence | Images | Use |
| --- | --- | --- | --- | --- |
| `nebius/SWE-rebench-V2` (`10483de0`, 429 MB parquet) | 3,123 real PRs, 307 repos; per-task licence: Apache-2.0 2,010, MIT 661, unclassified 311, CC0 55, BSD-3 49 | CC-BY-4.0 (attribute) | `swerebenchv2/*`, amd64 only; each row has an install recipe (`rust_version`, commands, `test_cmd`, `parse_log_cargo`) | **Main source.** Kept: permissive licence, no flag from the dataset's own task audit (B1–B6: ambiguous issue, tests checking more than the issue asks, …), no evaluation repository: 2,239 tasks in 245 repos; capped at 25 per repository (swc alone has 356): **1,661** |
| `SWE-bench/SWE-smith-rs` (`1ae20d63`) | 5,311 synthetic bugs, 39 repos | none on the card; SWE-smith's code is MIT; per-repo licences | one image per repo, 37.8 GB, amd64; SWE-smith's builder handles arm64 (`FROM rust:<ver>`) | **Second source.** Without ripgrep (evaluation repo) and the four MPL-2.0 repos (lightningcss, mdBook, rpds, shellharden): 34 repos; capped at 60 per repo: **1,586** |
| `SWE-bench/SWE-bench_Multilingual` (`846e647b`) | 43 (tokio 9, bat 8, ruff 7, axum 7, nushell 5, coreutils 5, ripgrep 2) | MIT | amd64 only | **Evaluation only** (§7.1) |
| `ByteDance-Seed/Multi-SWE-bench`, `-mini`, `-flash` | 239 / 50 / 45 (clap 132, tokio 25, …) | CC0 + notice | amd64 | Not used: overlaps the evaluation repositories and clap is already in the pool |
| `ByteDance-Seed/Multi-SWE-RL` | 215, 14 repos | CC0 + notice | amd64; a third party's retest flagged the Rust images | Later, if the pool runs short |
| `SWE-bench-Live/MultiLang` | 171, 55 repos | MIT | amd64 | Later; good as a second, fresher evaluation set |
| Rust-SWE-bench (ICSE 2026) | 500, 34 repos | not stated | built locally | Not used until the licence is stated |

`rust_tasks.py build` makes the pool from the three parquet files: **3,247 candidates** (Apache-2.0 1,598, MIT 836, MIT-or-Apache 624, Unlicense-or-MIT 60, ISC 51, BSD-3 42, CC0 36). The SWE-smith-rs licences are in `SMITH_LICENCES`; those not cross-checked against SWE-rebench-V2's column must be re-read from each repository before an adapter is published.

C/C++ for comparison: about 230 + 180 tasks in SWE-rebench-V2 (two-thirds of the C++ in zeek), 128 + 129 in Multi-SWE-bench, 377 + 449 in Multi-SWE-RL, of which a third party's retest passed 84 C and **0** C++ gold patches. CUDA has no repository-level task data at all.

### 3.2 Trajectories published by others: not used

Rust trajectory sets exist (SWE-smith-rs runs, Multi-SWE-bench's, a GLM-4.6 set of 142), but the larger ones were generated by closed commercial models whose terms restrict training on their outputs, and all of them are in other agents' formats (tool names, prompts, templates). Training on them would teach a different agent. Every training token here is generated by our own served model through `ling`.

### 3.3 What is held out, and the evidence

- **The Rust evaluation set** (`docs/dev/adapters/eval-multilingual-rust.txt`, 43 ids): its instances, its repositories at the same base commit, and its seven repositories entirely. Removing whole repositories costs SWE-smith-rs's ripgrep (426 tasks) and SWE-rebench-V2's 9 ripgrep/bat tasks, and keeps the 43 fixes from leaking in through later snapshots of the same code.
- **The SWE-bench Verified lists our nights score on** (`~/.cache/dreamference/swe-bench/*.txt`, read only): 739 lines, all in the 12 Python repositories of Verified (django 371, sympy 148, sphinx 52, …). `select_trajectories.py overlap` refuses any task on a list or from a Verified repository: on the Rust pool it refused **0 of 3,247**.
- **Night trajectories are never training data**: they are Verified tasks. The selection tool works on them (it found 104 resolved attempts on 56 tasks in the existing night runs, used here only to measure lengths), but no Python night run feeds a sample file.

## 4. Data pipeline

```
rust_tasks.py build ──► pool (3,247) ──► overlap guard ──► arm64 images, validated ──► ling-admin swe-bench run ×k
     ──► grading (cargo test, F2P/P2P) ──► select_trajectories.py select ──► SGLang request dumps ──► build_sft.py ──► samples
```

### 4.1 Environments on arm64

No published image is arm64. Both sources build from multi-arch `rust:<version>` bases:

- SWE-rebench-V2: a Dockerfile per task from its `install_config` (`FROM rust:<rust_version>`, clone at `base_commit`, the listed install commands, which end in `cargo build`). Toolchains seen: 1.89.0 (1,089), unspecified (598, use the toolchain file or stable), 1.88.0-nightly (420), older pins.
- SWE-smith-rs: one image per repository with SWE-smith's own builder, which passes `--platform linux/arm64/v8` itself; each task is a patch applied at run time.

Build in batches and delete after use: SWE-rebench-V2 images are 1.1–1.6 GB each, so the whole pool would be 2–3 TB. A shared registry cache (`CARGO_HOME` registry and git directories mounted read-only into builds) cuts download time. Each built task is **validated** exactly as Verified's were here: the gold patch resolves and a no-op patch does not; expect to lose 10–30% (flaky or network-dependent tests, toolchain drift).

### 4.2 What the harness needs (the follow-up branch, not built today)

`ling-admin swe-bench` today knows `verified`/`lite`/`full` and one arm64 image source (`greynewell/swe-bench-arm64`), and grades with the upstream Python harness. For Rust it needs:

1. a dataset given as a local JSONL (the `rust_tasks.py` rows) with a per-row image map, instead of the community repository's tags (`SweBenchImages.image_for`);
2. a grader for rows that are not upstream SWE-bench's: apply the patch, reset the test files file by file (as `per_file_reset` does), apply `test_patch`, run `test_cmd`, parse `cargo test` output (swebench 5.0.2 ships `log_parsers/rust.py`; SWE-rebench-V2 names `parse_log_cargo`), and write the same `grading.json` (`resolved`, `f2p`, `f2p_failed`, `p2p`, `p2p_failed`), so `select_trajectories.py` and the reports work unchanged;
3. the container set-up without the Python assumptions (conda `testbed` first on `PATH`), with `cargo` and the toolchain on `PATH`, `CARGO_TARGET_DIR` inside the container, and a CPU and memory cap per task (Rust test builds are the heaviest step; host safety applies);
4. the validation step for the new image source.

The task prompt, `ling`'s flags, the model gate, the patchelf'd `ling` and the run store stay as they are, so the agent sees what it sees in a night.

### 4.3 Generation: rejection sampling

- **Arm**: the production default (the arm the nights call `default`, 49,152-token `task_context`, the default model server with DFlash2), so the trajectories are on-policy for the base the adapter starts from.
- **Attempts**: 2 per task, then 2 more only on tasks with no resolved attempt yet (adaptive k, at most 4). Sampling is the server's default; distinct attempts come from it.
- **Acceptance** (`select_trajectories.py select`): the newest plain grading says `resolved`, at least one fail-to-pass test ran, no pass-to-pass test failed, the test patch applied, the final context fits `max_seq_len`; at most 2 attempts per task, the shortest first, so easy tasks do not dominate.
- **Expected yield**: pass@1 of the base on these tasks is not measured yet; at 25–35% and adaptive k≤4, about 40–50% of tasks give at least one accepted attempt.

### 4.4 The training sequence: exact tokens from the server

`ling` records rollouts (`codex-home/sessions/…/rollout-*.jsonl`: `response_item` messages, `function_call`, `custom_tool_call` and their outputs, `compacted` records, `token_usage_record` per request), but not the tool schemas nor how SGLang renders them; reconstructing the prompt from the rollout would mean re-implementing SGLang's Responses-to-template translation and the `exec` tool's translation. Instead, during generation runs the server dumps each finished request:

```bash
# on a generation machine, after `ling-admin server start`; the engine listens on 127.0.0.1:<port+10000>
curl -s http://127.0.0.1:18000/configure_logging -H 'content-type: application/json' \
  -d '{"dump_requests_folder": "/tmp/sglang-dumps", "dump_requests_threshold": 100}'
docker cp dreamference-vllm-8000:/tmp/sglang-dumps ./dumps      # periodically, and at the end
```

(Read in the pinned image's `tokenizer_manager.py`: a dump is a pickle of `(GenerateReqInput, out_dict, created, finished)`, written every `threshold` finished requests that carry `log_metrics`; `GenerateReqInput.input_ids` is the rendered prompt the Responses endpoint built.) Dumps are pickles: our own server writes them, but `build_sft.py extract` still reads them with stand-in classes (no SGLang needed, nothing executed beyond plain containers). To verify on the first generation day: that Responses requests carry `log_metrics`, and whether a streamed request's `out_dict` holds the whole completion (`build_sft.py` falls back to the completion text when it does not).

`build_sft.py build` then:

- keeps the **end of each segment** (a request whose prompt no later request extends; a compaction starts a new segment; the chat template renders past assistant turns exactly as generated, `<think>\n\n</think>\n\n` included, so a session's prompts are append-only — the server's prefix-cache hits in the rollouts confirm it);
- matches segments to **accepted attempts** by text only that attempt's prompts contain (its tool outputs with their `Wall time` lines; a failed attempt at the same task has the same issue text);
- labels **assistant tokens only**: after each `<|im_start|>assistant\n<think>\n\n</think>\n\n`, through its `<|im_end|>`, plus the final completion; system prompt, tool schemas, user turns and tool outputs are context, never targets. A sample whose count of assistant headers differs from its count of labelled spans is refused (a tokenization surprise at a turn boundary).

Lengths, measured on the 193 sessions of nights 9–11 (final request per session, prompt plus completion): p10 19,360, **p50 28,486**, p75 38,429, p90 44,619, p99 48,984, max 49,192 tokens; **62% fit 32K, 100% fit 64K**, because compaction caps a segment's prompt at the 49,152-token `task_context` (the longest segment, 49,192, is that prompt plus its completion). Hence `max_seq_len = 51200`. 46 of the 104 resolved night attempts compacted at least once, so an accepted attempt gives about 1.4 segments.

### 4.5 Running generation on the new DGX machines

Both new machines run SWE-bench nights (owner, 2026-10-19); the engine work stays on second-puffin. Generation is the same command family as a night, so it shares the night's admission and lock (a run and a night exclude each other) and the model gate.

- **Schedule**: generation by day on both machines; nights untouched. Image builds can run during a night's grading-free stretches only if they stay inside the night's memory budget; simpler to build by day before the runs.
- **Per machine**: the night runs measure a mean of 630 s per attempt (median 266 s, p90 1,443 s) at 3 in parallel, about 17 attempts per hour. Rust builds and tests are slower; assume 1.5×: **about 11 attempts per hour per machine**, 4 parallel if the CPU allows (20 cores; cap each container at 4 CPUs and 8 GB).
- **Disk**: images built per batch of ~100 tasks (~150 GB), deleted after grading; a dumped request is about 0.1–0.3 MB (up to 49K token ids), ~60 requests per attempt, so ~10–20 MB per attempt before `build_sft.py` keeps only segment ends.
- **Nodes by id**: the machines are reached by node id at use time, never a stored address (as everywhere else).

## 5. Training pipeline

### 5.1 Tool

| Tool | Hybrid GDN + attention LoRA | aarch64 / SM121 | Trains against the served FP8/NVFP4 base | Verdict |
| --- | --- | --- | --- | --- |
| **PEFT + transformers 5.x (own loop)** | yes: transformers has `qwen3_5`, names match the checkpoint | runs where PyTorch cu130 runs; GDN through `fla` (Triton) | **yes, with `ServedLinear`** (PEFT's default dispatch wraps it; checked) | **Primary** |
| Unsloth | yes (has a Qwen3.8-27B guide and a bnb-4bit checkpoint of the original) | most proven on DGX Spark (NGC PyTorch ≥ 26.03, `fla`) | no: NF4 of the BF16 original | A/B arm and fallback |
| Axolotl | yes (`examples/qwen3.8/27b-qlora.yaml`) | yes | loads ModelOpt checkpoints by dequantizing to BF16 (54 GB: no 48K) | fallback |
| NVIDIA NeMo AutoModel | yes, and the only one that trains MTP | Spark support for this model unverified | no | for a drafter/MTP stage, later |
| torchtune | no Qwen3.5 support; development stopped in 2025 | | | no |

TRL's `SFTTrainer` brings nothing a 300-line loop does not, once the model, the loss and the samples are ours: batch 1, one trajectory per micro-step (transformers' GDN layers support neither packing nor padding-free batches), `grad_accum` trajectories per optimizer step, the loss summed over assistant tokens and divided by the step's count.

### 5.2 The frozen base: three stand-ins

| Base | Weights | 32K | 48K | 64K | Fidelity |
| --- | --- | --- | --- | --- | --- |
| **Served bytes, dequantized per matmul** (`base = "served"`) | 21 GB + 2.5 GB BF16 head for the loss | 66 GB | **82 GB** | 97 GB | weight values exactly the served ones; with `fake_quant_inputs`, the base path's inputs rounded as the W8A8/W4A4 kernels round them (static FP8 scale; NVFP4 per-16 blocks under the static global scale), straight-through gradient |
| Served values as BF16 matrices | 54 GB | 97 GB | 112 GB | 128 GB | exact weights; does not fit 48K beside anything |
| NF4 QLoRA of the BF16 original (`base = "nf4"`) | ~21 GB with BF16 embeddings and head | 64 GB | 79 GB | 95 GB | a different quantization of the same model: the adapter learns to correct NF4's error, not NVFP4's |

At the configs' 51,200 tokens the served base needs 84 GB. Estimates from `train_lora.py`'s `memory_estimate_gb` (one BF16 hidden state per layer kept by checkpointing, one layer's recompute, a 2,048-token loss chunk, LoRA in FP32 with Adam, 6 GB runtime): conservative, to be replaced by the first GPU hour's measurement. A GB10 has 121 GB, so training never runs beside a model server (`train` refuses to start if one is up) and has the PSI guard of §5.5.

`ServedLinear` costs three dequantizations of the 27B weights per step (forward, recompute, input gradient), small against the matmuls at 48K tokens. Never NF4-quantize the dequantized NVFP4 model: that stacks two quantization errors.

### 5.3 Targets (`adapter_format.py audit`, run on the meta device)

| HF module | in → out | Layers | SGLang module | Trained |
| --- | --- | --- | --- | --- |
| `self_attn.q_proj` (query and output gate) | 5,120 → 12,288 | 16 | `qkv_proj` | yes |
| `self_attn.k_proj`, `v_proj` | 5,120 → 1,024 | 16 | `qkv_proj` | yes |
| `self_attn.o_proj` | 6,144 → 5,120 | 16 | `o_proj` | yes |
| `linear_attn.in_proj_qkv` | 5,120 → 10,240 (q 2,048, k 2,048, v 6,144) | 48 | `in_proj_qkvz` | yes |
| `linear_attn.in_proj_z` | 5,120 → 6,144 | 48 | `in_proj_qkvz` | yes |
| `linear_attn.out_proj` | 6,144 → 5,120 | 48 | `out_proj` | yes |
| `mlp.gate_proj`, `up_proj` | 5,120 → 17,408 | 64 | `gate_up_proj` | yes |
| `mlp.down_proj` | 17,408 → 5,120 | 64 | `down_proj` | yes |
| `linear_attn.in_proj_a`, `in_proj_b` | 5,120 → 48 | 48 | `in_proj_ba`: no LoRA in SGLang | no |
| `lm_head`, embeddings | | | the DFlash2 worker unwraps LoRA there; the drafter shares them | no |
| `conv1d`, norms, `mtp.*`, vision | | | | no |

Every fused module's dimensions match SGLang's `get_hidden_dim` (`qkv_proj` 5,120→14,336, `in_proj_qkvz` 5,120→16,384, `gate_up_proj` 5,120→34,816). PEFT trains exactly these ten module kinds, 800 tensors, all under `model.layers.*` (the text-only `Qwen3_5ForCausalLM`, whose names SGLang's `should_apply_lora` accepts): **rank 16 = 108.8M parameters (218 MB in BF16), rank 32 = 217.6M (435 MB)**.

### 5.4 Configs (`scripts/adapters/configs/`)

- `rust-r16-48k.toml`: the first adapter. Served base with input rounding, rank 16, alpha 32, no dropout, 51,200 tokens, 2 epochs, 16 trajectories per step, AdamW at 1e-4, 3% warmup, cosine to 10%, gradient clipping at 1.0, 5% of samples held out for validation loss, a save every 50 steps.
- `rust-r32-48k.toml`: the same at rank 32.
- `rust-r16-48k-nf4.toml`: the NF4 QLoRA arm (needs `Qwen/Qwen3.8-27B` at revision `e13a4f0e`, 54 GB, on the training machine, not this one), to measure what precision fidelity is worth.

### 5.5 Kernels and safety on SM121

- **Gated DeltaNet**: transformers uses `fla` (flash-linear-attention, Triton) when it is installed and otherwise a pure-PyTorch chunked loop, about 6× slower on GB10, with only a warning. `train` refuses to start without `fla` (`allow_torch_gdn` overrides). `causal-conv1d` has no aarch64 wheel; its fallback costs a few % and is allowed. Reported working on GB10: `fla` 0.5.x forward and backward against a reference recurrence; validate on our stack in the first hour.
- **Loss**: `chunked_cross_entropy` builds logits 2,048 tokens at a time inside a checkpoint, so the 49,152 × 248,320 logits (49 GB in FP32) never exist; plain PyTorch, no Liger or Cut-Cross-Entropy dependency (both unverified on SM121).
- **Attention**: SDPA; only 16 layers are full attention.
- **Environment**: NGC PyTorch ≥ 26.03 (reported: 25.10's inductor breaks Triton 3.6 on Spark), transformers 5.x with `qwen3_5`, `peft`, `fla`.
- **Host safety**: `train` refuses beside a running model server or with less available memory than the estimate plus 8 GB, and a thread exits the process when `/proc/pressure/memory` `full avg10` passes 20 (unified memory stalls the host instead of raising out-of-memory).

### 5.6 Checked today, without a GPU

- `served_base.py check`: decodes layer 0's `in_proj_z` (FP8) and `mlp.down_proj` (NVFP4) of the local checkpoint on the CPU; the E2M1 decoder agrees with SGLang's reference (`quant_ref_utils.break_fp4_bytes`, low nibble first); every NVFP4 block's largest code is 4 or 6, as max calibration implies; `ServedLinear`'s forward and input gradient equal a dense layer with the dequantized weights, the frozen bytes take no gradient, and PEFT wraps it.
- `adapter_format.py audit`: the table of §5.3, against the checkpoint headers, the meta-device model, PEFT and SGLang's list and dimensions; the export's split is exact (`B_slice @ A` equals the rows of `B @ A`).
- `smoke_tiny.py`: a 4-layer model of the same architecture written in the served layout (FP8 attention and GDN projections, NVFP4 MLP and head, sharded safetensors with an index): the loader's logits equal a plain model holding the dequantized weights (difference 0 in FP32), 8 LoRA steps through the served layers with chunked loss and checkpointing lower the loss (6.24 → 5.67), no frozen tensor changes, and the SGLang export splits `in_proj_qkv`.
- `tests/test_adapter_pipeline.py` (12 tests, about 5 s; the end-to-end one is skipped without `peft`).

### 5.7 The first GPU hour (on a new machine, model server stopped)

1. `smoke_tiny.py --device cuda` (seconds).
2. `fla` numerics: one GDN layer's forward and backward against the PyTorch path at 4K tokens.
3. `train_lora.py train` on 20 samples at 49,152 tokens: peak memory, tokens per second, loss falling; replace the estimates of §5.2 and §8 with the measurements.
4. Served-numerics check: held-out loss of the base under `ServedLinear` with input rounding against the same samples' log-probabilities from SGLang (both should agree within noise; a gap means the rounding emulation is off).

## 6. After training: export, merge, serve

- **Export** (`adapter_format.py export`, run by `train` at the end): SGLang v0.5.21's loader (`LoRAAdapter._normalize_in_proj_qkvz`) accepts the GDN input projection either split into `in_proj_q/k/v/z` or merged as `in_proj_qkvz`, never PEFT's `in_proj_qkv`. The export repeats `in_proj_qkv`'s `lora_A` for q, k and v, cuts its `lora_B` into the 2,048/2,048/6,144-row slices (the order of `Qwen3_5GatedDeltaNet.forward`'s split) and keeps `in_proj_z`; SGLang stacks the four into the 16,384-row `in_proj_qkvz`. Exact. Its `adapter_config.json` names the fused **`in_proj_qkvz`**, not the split names: SGLang normalizes the config's `target_modules` by name and refuses an adapter naming a module outside `--lora-target-modules` (or, without the flag, adds the unknown names as they are and leaves `in_proj_qkvz` unwrapped), so pass `--lora-target-modules` explicitly either way. It also renames `model.language_model.layers` to `model.layers` for an adapter trained on the multimodal class (the NF4 arm).
- **Serve unmerged** (the product path): `--enable-lora --lora-paths rust=<dir> --max-lora-rank 32 --lora-target-modules qkv_proj o_proj in_proj_qkvz out_proj gate_up_proj down_proj --max-loras-per-batch 2 --lora-backend csgmv`, in the launch recipe as registry flags (never in the launch builder). The pinned image allows LoRA with `DFLASH` (`lora_hook.check_lora_speculative_compatibility`; the draft runs unadapted, and `--speculative-adaptive` is refused); with LoRA on, SGLang turns off its fused `in_proj_qkvz`+`in_proj_ba` GEMM, a speed cost to measure. **For `research/lora-serving-probe`** (no findings committed yet when this was written), the points this pipeline depends on: that the exported adapter (split tensors, `in_proj_qkvz` in its config) loads and changes logits exactly as PEFT does on the same input (the slice order inside `in_proj_qkvz`'s stacked buffer), that LoRA works over the FP8 and NVFP4 base layers on SM121, the per-request adapter selection field, and the tok/s with one adapter loaded but not selected.
- **Merge for evaluation only**: `merge_and_unload()` onto the served values as BF16 (54 GB, an evaluation machine with no server) gives an HF model for log-likelihood checks. Never merge back into NVFP4: a rank-16 delta is mostly below one NVFP4 step and rounds away.
- **Publishing**: the adapter directory (SGLang form), a manifest naming the base checkpoint and revision, the drafter adapter, the training data's source revisions and licences (CC-BY-4.0 attribution for SWE-rebench-V2), SHA-256 of each file, signed with the release key as releases are.

## 7. Evaluation, against the owner's bar

The bar (owner, 2026-10-19): **a clear gain on Rust; at most 1 task lost on a Python control night; at most 5% tok/s lost with its drafter adapter.**

### 7.1 Rust: SWE-bench Multilingual's 43 Rust tasks

- Images built for arm64 and validated like the training tasks (gold resolves, no-op does not); report the validated count (expect 35–40).
- Base against adapter, both unmerged on the same server build (the adapter selected per request), the production arm, **3 runs each** (43 tasks is small: night 11's three runs of one arm on the same 30 tasks resolved 15, 13 and 12).
- Report the mean of the 3 runs and the paired per-task comparison with `swe_bench_best_of.py`'s exact sign-flip test. Proposed meaning of "clear" (the owner's to confirm): the adapter's mean is at least 4 tasks higher (~10 points) and the paired p ≤ 0.1. A second, fresher check when it exists: SWE-bench-Live/MultiLang's Rust tasks minus anything in the training pool.

### 7.2 Python control night

A normal night pair on a fresh Verified list (the rotation rules apply), arm A the base, arm B the same server with the Rust adapter **selected on every request** (the worst case: the launcher would select it only in Rust repositories). Pass: arm B loses at most 1 task net. If it loses more, the remedy is a replay share: 10–20% of samples from SWE-rebench-V2's Python tasks outside Verified's repositories, generated the same way (the overlap guard already refuses Verified repositories).

### 7.3 Speed and the drafter

The adapter changes the hidden states DFlash2 reads (layers 5, 19, 33, 47, 61) and the token distribution it predicts, so acceptance falls. Measure with `scripts/decode_speed.py` on a Rust prompt set (from held-out training-pool tasks, not the evaluation set) and on the Python set, three configurations: base + DFlash2; adapter + DFlash2 as is; adapter + DFlash2 with its drafter adapter. Record decode tok/s and the server's mean accepted length per step. Pass: the third within 5% of the first's tok/s. The second tells how much the drafter adapter must recover; LoRA without speculation, against base without speculation, isolates the LoRA kernels' own cost.

**The drafter adapter** (next, after the target adapter is final): LoRA on the 5-layer DFlash2 drafter, trained to predict the adapted target's own continuations from the adapted target's hidden states: re-run the accepted samples through the adapted target (teacher-forced, forward only) to record the five layers' states and its predictions, then train the drafter on them. Tooling to decide then (SGLang's speculative-training tools for DFlash, or a loop like this one).

## 8. Time estimates

| Step | Basis | Estimate |
| --- | --- | --- |
| arm64 images, 800 tasks | 5–15 min per SWE-rebench-V2 task (clone, `cargo build`), 2 builds in parallel; 34 SWE-smith-rs images at ~20 min | ~50–60 machine-hours, by day on both machines: ~2 days |
| Validation | gold + no-op per task, ~5 min | ~35 machine-hours, overlapping the builds |
| Generation, 800 tasks, adaptive k ≤ 4 (~2,400 attempts) | ~11 attempts/h/machine (§4.5) | ~220 machine-hours: **~8 days of day slots on two machines**; 400 tasks halve it |
| Yield | 25–35% pass@1, ≤ 2 per task | ~500–600 accepted attempts, **~700–850 segments** |
| Training, 800 segments × 2 epochs (52M tokens) | 6N FLOPs per token + attention (frozen-base LoRA with full recompute), GB10 at 30 / 45 / 60 effective BF16 TFLOPS (unmeasured; assumed 25–50% of peak) | **89 / 60 / 45 h** on one GB10 (163 / 245 / 326 tok/s); two GB10s data-parallel (LoRA gradients are 0.4 GB per step) about half |
| Evaluation | 43 Rust tasks × 3 runs × 2 arms (~260 attempts) + a 50-task control pair + decode speed | ~1 day on one machine, plus one night |

Training needs a machine without a model server for 2–4 days, so it is a day slot plus an explicitly allocated night or weekend, not something beside a night. `train_lora.py plan <config>` reprints these numbers from the real sample file once it exists.

## 9. Risks and open points

1. **Slow fallbacks that report no error** (GDN without `fla`, a Triton kernel falling back on SM121): the refusal of §5.5 and the first-hour throughput check.
2. **The activation-rounding emulation** may differ from the served kernels in detail (block-scale rounding, saturation): the served-numerics check of §5.7; the arm without rounding is one config line.
3. **SGLang serving details** (§6): the probe's points. If the split form fails, the fallback is the merged `in_proj_qkvz` form (one shared `lora_A`, which the export can produce exactly at rank 2r).
4. **Drafter acceptance** may fall more than 5% even with a drafter adapter; then the adapter's rank or targets (MLP only) are the levers, measured against the same bar.
5. **Generation cost**: 8 days of day slots is long; start with 400 tasks, train a first adapter on those, and grow the set only if the Rust gain needs it.
6. **Dataset licences**: SWE-rebench-V2 rows carry a licence; SWE-smith-rs's card states none, and its per-repo licences above must be re-read from each repository before publishing; SWE-rebench-V2's 311 unclassified rows are left out.

## 10. Files

- `scripts/adapters/served_base.py`: decoding, `ServedLinear`, the served-base loader, `check`.
- `scripts/adapters/adapter_format.py`: targets, `audit`, `export`.
- `scripts/adapters/rust_tasks.py`: the task pool and the held-out set.
- `scripts/adapters/select_trajectories.py`: `select` (rejection sampling over runs), `overlap` (the guard).
- `scripts/adapters/build_sft.py`: `extract` (request dumps), `build` (samples with assistant-only labels).
- `scripts/adapters/train_lora.py`: `plan`, `train`; `scripts/adapters/configs/*.toml`.
- `scripts/adapters/smoke_tiny.py`: the end-to-end check on a tiny model.
- `docs/dev/adapters/eval-multilingual-rust.txt`: the 43 held-out task ids.
- `tests/test_adapter_pipeline.py`.
