# Qwen3.8-Flash-Next on one GB10: feasibility (2026-10-19)

Why: the owner asked (2026-10-19) whether **Qwen3.8-Flash-Next** can be Mightling's model on one GB10, as preparation for a SWE-bench A/B night against production, `qwen3.8-27b-nvfp4-dflash2` on SGLang v0.5.21. Qwen's model card puts it ahead of the 27B on agentic coding: SWE-bench Pro 62.5 against 61.7, DeepSWE 1.1 58.7 against 42.2, SWE-bench Multilingual 81.0 against 73.8.

**Short answer.** Yes, it can be served, with conditions. Two quantization-aware-distilled (QAD) 4-bit checkpoints fit in memory with the n-gram table resident. Neither loads on any SGLang release, our pinned v0.5.21 included. On vLLM nightly, one of them, `local-inference-lab/Qwen3.8-Flash-Next-NVFP4`, loads with upstream code. Community recipes measure about **49 tok/s on prose and 68 on code** single-stream with MTP on one GB10. Production does 25.5 and 50.3, so Flash-Next is faster on both. The fit is tight. About 15 GiB of host memory stays free, where a SWE-bench night at its default parallelism of 3 needs about 32. Today's start gate (`LOAD_TRANSIENT_FRACTION`) also refuses the load. The A/B therefore needs two small code changes beyond a registry entry (§5), and it runs at parallelism 1 or 2 unless the table moves out of memory.

The download of the recommended checkpoint started on second-puffin (§4).

## 1. Checkpoints

The model is about 125B parameters with 6B active, plus a 51B n-gram ("PLE") embedding table and a 4B MTP head. The architecture is `Qwen4ExpForConditionalGeneration` (`qwen4_exp`) and includes a vision tower. Its 48 layers follow the pattern 12 × (3 Gated DeltaNet + 1 Qwen Sparse Attention). Each layer has 512 experts, of which 10 routed and 1 shared are active. The native context is 262,144 tokens. Sizes below are the sums of the files' sizes from the Hugging Face API (`?blobs=true`), in GB (10^9); GiB are in brackets where a source quotes them.

| Checkpoint | Revision | Size | n-gram table | Fits 121.6 GiB resident? | Engines |
| --- | --- | ---: | --- | --- | --- |
| `Qwen/Qwen3.8-Flash-Next` (BF16) | — | 360.0 | BF16 | no | all |
| `Qwen/Qwen3.8-Flash-Next-FP8` (official) | — | ~185 (172.8 GiB per the vLLM recipe) | FP8 | no | vLLM, SGLang |
| `nvidia/Qwen3.8-Flash-Next-NVFP4` (ModelOpt MIXED_PRECISION) | `fc694b54…` | 132.7 | FP8, 47.7 GiB | **no** (123.6 GiB); only with the table file-backed on NVMe | SGLang ≥ v0.5.20 (#39126), vLLM ≥ 0.30 |
| `RadixArk/Qwen3.8-Flash-Next-NVFP4` (routed experts only; dense layers BF16) | `7b719225…` | 135.3 [126.0 GiB] | FP8, 47.7 GiB | **no**; only file-backed | SGLang |
| **`local-inference-lab/Qwen3.8-Flash-Next-NVFP4`** (QAD: experts and table NVFP4, attention and shared experts MXFP8) | `6c01ac37…` | **106.4** [~98 GiB; loads at 97.5 GiB per vLLM #56273] | **NVFP4, 26.8 GiB** | **yes**, ~20 GiB left | vLLM nightly (#56273, merged 2026-10-14; not in v0.32.0); **not SGLang** (#41624 open) |
| `nvidia/Qwen3.8-Flash-Next-FP4-Local` (QAD, NVIDIA; `ple_embedding_dtype: nvfp4`, compressed scale factors) | `b9ce18a6…` | 102.3 [~96 GiB] | NVFP4 | **yes** | community vLLM containers only (`eugr/spark-vllm-b12x`, `ghcr.io/local-inference-lab/vllm`); TensorFold; not upstream vLLM, not SGLang |
| `amd/…-Quark-MXFP4`, `primitive-ai/…-NVFP4`, `Inferact/…-NVFP4` | — | 182–187 | BF16 | no | — |
| `Intel/…-W4A16-AutoRound` / `cyankiwi/…-AWQ-INT4` | — | 181.2 / 188.3 | BF16 | no | vLLM |
| `azampatti/…-125B-A5B-INT4-AutoRound` (keeps 5 of 10 experts per token) | — | 130.8 [~122 GiB] | FP8 | no | vLLM; quality about 10% lower on its authors' harness |
| GGUF: unsloth UD-IQ4_XS / UD-Q4_K_XL, bartowski IQ4_XS / Q4_K_M, ggml-org IQ4_NL | — | 93.7 / 111.3, 97.7 / 119.6, 102.0 | quantized in-file | the 4-bit ones yes | llama.cpp only, which Mightling does not run |

**The n-gram table can be held at lower precision, or kept out of GPU memory.**
- *Lower precision.* The QAD exports store it in NVFP4 (26.8 GiB), where the plain exports store it in FP8 (47.7 GiB). NVIDIA trained the table during the distillation ("joint refinement").
- *Outside memory.* On GB10, "CPU offload" to pinned host memory saves nothing, because pinned memory comes from the same unified pool. Two ways actually free memory:
  - SGLang's file-backed table (`--ple-offload-embedding --ple-offload-backend file`, #39126, in v0.5.20 and v0.5.21). It keeps the table in a sparse file on NVMe, with hot pages in at most 8 GiB of page cache.
  - vLLM's disk offload (`--engram-config '{"disk_offload": true}'`, #59958, **open**). It reads rows straight from the checkpoint and saves 27.0 GiB, with decode within 3.5% of the resident table, as its author measured. The community vLLM images ship an mmap variant of the same idea (`VLLM_PLE_TABLE_MEMORY=disk`).

## 2. Engine support

**SGLang v0.5.21 (our pin, `lmsysorg/sglang@sha256:b1259f3e…`, confirmed as the `v0.5.21` index digest).**

What works:
- The architecture: #37500, in the v0.5.20 release notes.
- The NVIDIA mixed-precision loader, the file-backed table and the router PDL fix: #39126, merged to main on 2026-09-13.
- The meta-device fix for the offloaded table: #39928.

GitHub's compare API reports all three as ancestors of `v0.5.21`. The `--trust-remote-code` flag is not needed.

What does not work:
- **Packed NVFP4 n-gram tables.** #41624 is open, so neither QAD checkpoint loads on any SGLang release.
- **Fit.** The two exports SGLang can load do not fit resident. They run on one GB10 only with the table on NVMe.
- **Speed.** The SGLang cookbook's own single-Spark cell, measured on a `qwen4-main-squashed` tip (`4ccff141db`), not on v0.5.21, reaches **27.5 tok/s with MTP and 15.9 without**. It allows at most 8 running requests with MTP, and every boot rewrites the table: about 10 min on a fresh sparse file, about 55 min on an existing one. The cookbook says to delete `ple_table_*.bin` before each boot, and the fix (#42400) is open.
- A community SGLang recipe (github.com/hashd1ve/qwen38-flash-next-one-dgx-spark) reached 41.5 tok/s on code and 22.8 on prose with patches and `--decode-attention-backend trtllm_mha`.

The flags the cookbook requires are no `--quantization`, `--moe-runner-backend flashinfer_cutlass` (the automatic choice, `flashinfer_trtllm`, fails at autotune on GB10), the `--ple-offload-*` flags above, `--mem-fraction-static 0.85` and `--max-mamba-cache-size` = requests × slots. v0.5.22 (2026-10-16, `lmsysorg/sglang@sha256:e3a4f060…`, arm64 `06441be3…`) adds Flash-Next decode fusions and an important fix for hybrid models: wrong mamba states under speculation combined with structured output (#43029). That fix concerns production too. v0.5.22 does not add NVFP4-table support.

**vLLM.**
- Stable releases from 0.30.0 run the model (BF16, FP8 and the NVIDIA NVFP4 export). The vLLM recipe is tuned on nightly.
- The local-inference-lab checkpoint needs #56273 (packed NVFP4 tables), which is in nightly and in the `v0.32.1rc0` git tag, but **not in `v0.32.0`**. There is no `v0.32.1rc0` image on Docker Hub. The nightly `nightly-e68efc685ab1…` (2026-10-19) contains #56273 and is pinned as `vllm/vllm-openai@sha256:1ff6716fe240a922d0d518b8884261e25b0c02eda8e2ce90888e364978249cad` (arm64 `sha256:c6c3c615…`).
- **MTP as drafter on SM121.** It works on GB10 in community builds. MiaAI-Lab (github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark, patched vLLM) measured k = 3 accepting about 2.8–3.0 tokens per step: 48.7 tok/s single-stream on prose, against 26–33 with MTP off (k = 0 loses 32–46%). NVIDIA's FP4-Local card ships `{"method":"mtp","num_speculative_tokens":3–4}` for DGX Spark on a community image. **Upstream with this checkpoint and MTP on GB10 is unmeasured.** #56273's own GB10 check ran MTP off at a 4,096-token context.
- SM121 work is landing: the skinny-GEMM plans (#59632, merged) and per-forward W4A4/W4A16 experts (#54617, open).

**TensorFold** (github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark-TensorFold, a Zig engine) serves FP4-Local at 46–55 tok/s on prose and 67.9 on code single-stream, with prefill around 2,300–2,600 tok/s. It is the fastest published single-Spark result, but it is a one-person engine outside both runtimes Mightling supports. It is useful as a ceiling, not as a candidate.

## 3. Expected speed

The owner's estimate of 3.5 GB read per token assumes every active weight in 4 bits. No checkpoint stores it that way. Counting from `config.json`, a token reads:

| Part | Active params | QAD exports (lil, FP4-Local) | Plain exports (nvidia, RadixArk) |
| --- | ---: | ---: | ---: |
| Routed experts (10 × 3 × 2,560 × 640 × 48) | 2.36 B | NVFP4 ≈ 1.33 GB | NVFP4 ≈ 1.33 GB |
| Attention: 36 GDN (~58 M) + 12 QSA (~51 M) | 2.70 B | MXFP8 ≈ 2.8 GB | BF16 ≈ 5.4 GB |
| Shared experts and routers | 0.30 B | MXFP8 ≈ 0.3 GB | BF16 ≈ 0.6 GB |
| `lm_head` (248,320 × 2,560) | 0.64 B | BF16 1.27 GB | BF16 1.27 GB |
| n-gram rows (3 lookups) | — | negligible | negligible, but NVMe page faults when file-backed |
| **Total** | **≈ 6.0 B** | **≈ 5.6 GB** | **≈ 8.6 GB** |

Small-M GEMMs on GB10 reach about 125–205 GB/s of the 273 GB/s peak (SGLang #43250), which the estimates below assume.

| Bytes per token | Ceiling at 273 GB/s | Expected without MTP | Expected with MTP (k = 3, ~2.8 accepted) | Measured |
| --- | ---: | ---: | ---: | --- |
| 3.5 GB (owner's figure; no such checkpoint) | 78 | 40–55 | 65–90 | — |
| **5.6 GB (QAD, resident)** | **49** | **27–36** | **45–55** | vLLM 26–33 / 48.7 prose; TensorFold 67.9 code |
| 8.6 GB (plain, file-backed) | 32 | 15–22 | 25–35 | SGLang cookbook 15.9 / 27.5; hashd1ve 41.5 code |

Production does 25.5 prose, 50.3 code and 87.0 JSON tok/s. **Only a QAD checkpoint on vLLM is likely to beat it on code.** The SGLang route roughly ties on prose and loses on code. Expect MTP acceptance to be lower on thinking output: the SGLang cookbook measured an accept length of about 2.5 on thinking against 3.5 on plain answers.

## 4. Disk and download

- This machine: `/` is 916 G, with 653 G used and **216 G free**. Tonight's night needs about 193 G, so nothing was downloaded here.
- second-puffin: `/` is 916 G, with 246 G used and **623 G free** before the download.
- **Started on second-puffin**: `local-inference-lab/Qwen3.8-Flash-Next-NVFP4` at revision `6c01ac378d22f6652c9f392c87700496eca8e5dc` (106.4 GB, not gated). It goes into the HF cache (`~/.cache/huggingface/hub`), using the `hf` of the `ling-admin` venv. It runs detached (`setsid nohup … < /dev/null &`, pid 1385733), logs to `~/flash-next-download.log`, and was running at about 110 MB/s when it started. About 515 G stays free afterwards.
  - The first attempt failed because the cache's `.locks/` directory was owned by root, left by an earlier container download. Its owner was changed back to the user. `~/.cache/huggingface/xet/` is root-owned too, which only costs xet its log file.
- The model was not loaded on second-puffin's GPU.
- The SGLang-path checkpoint, `nvidia/Qwen3.8-Flash-Next-NVFP4` at `fc694b54fb0174e0913e6adf86691ef85a4ead47` (132.7 GB), is one more `hf download` away if wanted. It needs another ~51 GB for the file-backed table, and disk allows both.
- To run the night here, the snapshot moves over with `ling-admin node sync-model` (or rsync by node name) once tonight's night has freed the disk. Otherwise the A/B runs on second-puffin.

## 5. What a registry entry needs, and what it does not cover

A sketch for `ModelMatrixRegistry.MATRIX`, to be verified by a smoke boot. The engine is vLLM, because SGLang cannot load the checkpoint. The launch builder already reads `engine`, `docker_image`, `revision`, `max_model_len`, `gpu_memory_utilization`, `container_headroom_gb`, `tool_call_parser`, `reasoning_parser`, `speculative_config`, `chat_template_patches`, `extra_args`, `env`, `attention_backend` and `enable_prefix_caching`. Per AGENTS.md, every launch flag goes here and not into the builder. The alias keeps `nvfp4`, so the post-launch NVFP4 canary runs.

```python
"qwen3.8-flash-next-nvfp4-mtp": ModelSpec(
    name="Qwen 3.8 Flash-Next (QAD NVFP4 + MTP, vLLM)",
    params_b=180.0,                      # 125 B + 51 B n-gram + 4 B MTP
    supported_precisions=["NVFP4"],      # self-declaring (modelopt MIXED_PRECISION): no --quantization
    min_memory_gb=105.0, max_memory_gb=118.0,
    compatible_gb10=True,
    hf_repo_id="local-inference-lab/Qwen3.8-Flash-Next-NVFP4",
    supports_vision=True,                # vision tower and MTP head are in the checkpoint
    launch_overrides={
        "engine": "vllm",
        "docker_image": "vllm/vllm-openai@sha256:1ff6716fe240a922d0d518b8884261e25b0c02eda8e2ce90888e364978249cad",
        "revision": "6c01ac378d22f6652c9f392c87700496eca8e5dc",
        "max_model_len": 65536,          # SWE-bench task_context is 49,152; KV at 64K is ~1.1 GiB FP8
        "gpu_memory_utilization": 0.90,  # the builder's cap is (total - 12 GB reserve) / total
        "tool_call_parser": "qwen3_coder",   # the vLLM recipe's choice; NVIDIA's card uses qwen3_xml
        "reasoning_parser": "qwen3",
        "speculative_config": {"method": "mtp", "num_speculative_tokens": 3},
        "chat_template_patches": QWEN38_SGLANG_RECIPE["chat_template_patches"],
        "enable_prefix_caching": True,
        "extra_args": [
            "--kv-cache-dtype", "fp8",
            "--mamba-cache-mode", "align",
            "--max-num-seqs", "4",
            "--max-num-batched-tokens", "4096",
            "--enable-chunked-prefill",
        ],
        "env": {},                       # fill from the smoke boot; the community images set b12x/V2-runner variables
    },
),
```

- **The chat template is Qwen's own**, byte-identical to the base model's. Both of production's `chat_template_patches` anchors (the `xhigh` default and "System message must be at the beginning") occur in it exactly once, so the patches carry over unchanged. The template does honour `enable_thinking: false`, despite the SGLang cookbook saying thinking cannot be turned off.
- **The server is asked which model runs.** The served id is the repository name, which `model_key_for_served_id` maps back through `hf_repo_id`. Nothing reads the configuration for that.
- `HardwareManager`'s compatibility check (`min_memory_gb` at most 0.95 × total, about 115 GB) passes at 105.
- **Not covered by a registry entry. Both items need code changes, with tests:**
  1. **The start gate refuses the load.** `start_server()` requires the arena plus `max(4, 0.15 × weights)` free. That is about 109 GB for the arena (0.90 × 121.6) plus 16 GB of load transient, against roughly 115 GB available on an idle machine. The calibration came from a 78 GB checkpoint loaded through fastsafetensors. Options:
     - A per-recipe transient figure measured on a guarded first load: PSI watchdog on, desktop session closed, nothing else resident. This is the safety layer, and changing it needs the owner's call.
     - Wait for table disk offload (#59958) to shrink the resident set by 27 GiB.
  2. **SWE-bench parallelism.** The night needs `max_parallel` 3 × `task_memory` 8 G plus the 8 GiB reserve, about 32 GiB. With the table resident, about 14–16 GiB stays available: MiaAI-Lab measured 14.2–16.4 GiB MemAvailable over 2.5 h of agent traffic, even with the table memory-mapped. The night must run at `max_parallel` 1 (2 at most, with `task_memory` lowered), or wait for disk offload. At parallelism 1 the A/B takes about three times as long, so it needs its own night and a reduced instance list.
- **Two-engine night.** Production is SGLang and the candidate is vLLM, so the A/B compares checkpoint and engine together. Night 2's fairness argument (only the checkpoint differs) does not hold here. The report should say so.

## 6. Recommended plan for the A/B night

1. **Checkpoint:** `local-inference-lab/Qwen3.8-Flash-Next-NVFP4` at `6c01ac37…`. It is QAD, its table is in NVFP4 and fits resident, upstream vLLM loads it, and MiaAI-Lab's recipe is built on it. NVIDIA's FP4-Local is the better-documented QAD export, but it needs a community container.
2. **Engine and image:** vLLM nightly pinned by digest, `vllm/vllm-openai@sha256:1ff6716f…`, with MTP k = 3. Fallback: SGLang v0.5.21 (no new image) with `nvidia/Qwen3.8-Flash-Next-NVFP4` and the file-backed table. That route is slower than production (§3) and is worth running only to check quality, not as a candidate to switch to.
3. **Before the night, on a day slot** (one model server at a time; m0-prod8000 stays up on second-puffin and the GPU lock is respected):
   - Smoke-boot the candidate.
   - Record peak load memory, MemAvailable while serving, and boot time.
   - Run the NVFP4 canary and the Responses-API tool-call canary.
   - Run a greedy determinism probe above 2,048 prompt tokens (vLLM #54521).
   - Run needle retrieval at 120K and 200K. hashd1ve saw XQA corrupt long-context output silently on GB10.
   - Measure decode speed with production's prose/code/JSON prompts.
4. **The night:** the same validated instance list as production's last run, cut to what parallelism 1 finishes, with production's numbers taken from that run on the same instances. Switch only if quality holds **and** it is faster, as in night 2.

**Risks.**
- **Correctness on SM121:**
  - vLLM #54521 (open): greedy decoding is nondeterministic, and text gets corrupted, once a prompt exceeds `indexer_budget` (2,048 tokens). Every agent prompt does.
  - SGLang #37111 (open): QSA + NEXTN decode graphs silently corrupt output on GB10 (seen at TP 2).
  - The SM121 NVFP4 path, whose symptom is all-`!` output.
- **Memory:** about 15 GiB of headroom on a machine that has needed the power button. The PSI watchdog and the host-safety gates stay on.
- **Image:** the nightly is unreleased code, pinned by digest but untested by us. MTP on upstream with this checkpoint has not been measured.
- **Expectations:** the published gains are on DeepSWE and SWE-bench Multilingual. On SWE-bench Pro the gap (62.5 against 61.7) is within noise, and Qwen ran these benchmarks in third-party agent harnesses at 256K context and temperature 1.0. A SWE-bench Verified A/B here may show no quality gain.
- **Logistics:** the checkpoint is on second-puffin, and this machine has 216 G free before tonight's night.
- **Licence:** the Qwen Community License 1.0 applies, inherited by the local-inference-lab checkpoint. Check its terms before shipping, not before measuring.

## Sources

- Model card, `config.json` and chat template: huggingface.co/Qwen/Qwen3.8-Flash-Next.
- Checkpoint cards and file sizes: the Hugging Face API, `?blobs=true`.
- SGLang cookbook: docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-Flash-Next.
- vLLM recipe: recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next.
- Release notes: SGLang v0.5.20, v0.5.21 and v0.5.22. Pull requests and issues as numbered above, with release containment checked with GitHub's compare API.
- Image digests: the Docker Hub API.
- Community recipes: github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark, github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark-TensorFold and github.com/hashd1ve/qwen38-flash-next-one-dgx-spark.
