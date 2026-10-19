# LoRA serving probe: SGLang v0.5.21, Qwen3.8-27B NVFP4, DFlash2 and adapters together (2026-10-19)

Why: the owner wants LoRA adapters, several published and signed by Dreamference and switched per session. Before anything is built, this probe asks whether the production stack can serve them: SGLang at the recipe's pinned image (`lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469`, v0.5.21), `qwen3.8-27b-nvfp4-dflash2` as `QWEN38_SGLANG_RECIPE` launches it (DFlash2 drafter, 12 drafts, mem-fraction 0.50, torch.compile, decode graphs to batch 8), plus `--enable-lora`.

## Answer

**Yes, with two conditions, and not yet on the endpoint `ling` speaks.**

1. **Adapters on the FP8 layers (full-attention q/k/v/o and the GDN projections) work with the production launch unchanged**: torch.compile, the fused SiLU+FP4 MLP path, DFlash2 with 12 drafts, decode graphs. A request without an adapter on that server is token for token the production server's output, at the same speed (57.4 against 57.7 tok/s). With an adapter active, step time goes up by 3-4 %.
2. **Adapters on the MLP (NVFP4 gate/up/down) work only with two production optimizations off**: the fused SiLU+mul+FP4-quant MLP path crashes the start (`AttributeError: 'RowParallelLinearWithLoRA' object has no attribute 'input_scale_inv'`), and with that switched off torch.compile then fails in the FP4 GEMM (`[FP4 gemm Runner] Failed to run cutlass FP4 gemm on sm120/sm121. Error: Error Internal`). Without both, everything works; against production, step time is 2 % longer for requests without an adapter and 9 % longer with one (54.5 and 51.2 tok/s against 57.7, part of that gap being a different output text).
3. **`/v1/responses` ignores the adapter.** It accepts `RadixArk/Qwen3.8-27B-NVFP4:rand` as the model and then runs the base model: its `GenerateReqInput` is built without `lora_path` (`serving_responses.py`, both call sites), while `/v1/chat/completions` and `/generate` pass it. `ling` and agentlong speak Responses, so per-session switching needs a one-line SGLang patch (`lora_path=self._resolve_lora_path(request.model, None)`) or a different route before it can be built.

Also true, all measured: a zero adapter gives the base model's output token for token at greedy (all modules, and attention or GDN alone); two to four requests with different adapters run in one batch, each under its own adapter; adapters load and unload at runtime through `/load_lora_adapter`, but only into the module set fixed at start; the GPU cost is a pool preallocated at start, about 0.30 GiB per `--max-loras-per-batch` slot for adapters on every module at rank 16 (0.17 GiB on the FP8 layers alone), and the drafter's acceptance did not drop with these adapters (it runs unadapted; see the caveat).

## The adapters

Dummy PEFT adapters made on CPU (`make_adapters.py`, kept with the raw results in `node-b:~/lora-probe/`): rank 16, `lora_alpha` 16, bf16, keys as PEFT writes them for this checkpoint (`base_model.model.model.language_model.layers.N.<module>.lora_A/B.weight`). Module names and shapes come from the checkpoint's own `config.json` and safetensors index, and every module is checked to exist there. A is PEFT's default init (uniform, bound 1/sqrt(fan_in)); B is zero for a *zero* adapter and N(0, 0.02) for a *random* one, a perturbation of a few per cent of each projection's output.

| Adapter | Modules | Layers | Params | File |
|---|---|---|---|---|
| `zero`, `rand`, `rand2` | q, k, v, o + gate, up, down | 16 attention + 64 MLP | 79.7 M | 152 MiB |
| `rand-attn`, `zero-attn` | q, k, v, o | 16 full-attention layers | 10.5 M | 20 MiB |
| `rand-mlp` | gate, up, down | 64 | 69.2 M | 132 MiB |
| `rand-gdn-hf` | `in_proj_qkv`, `in_proj_z`, `out_proj` (the checkpoint's own GDN names) | 48 linear-attention layers | 29.1 M | 56 MiB |
| `rand-gdn`, `zero-gdn` | the same weights in SGLang's split form `in_proj_q/k/v/z` + `out_proj` | 48 | 37.0 M | 71 MiB |
| `loud` | as `rand`, B with std 0.5 (gibberish on purpose) | | 79.7 M | 152 MiB |

**Which layers take LoRA.** The checkpoint is 64 layers: 48 Gated DeltaNet (linear attention: `in_proj_qkv`, `in_proj_z`, `out_proj` in FP8; `in_proj_a/b` in bf16) and 16 full attention (`q/k/v/o_proj`, FP8; `q_proj` is 12288 wide because of the output gate), every layer with a dense MLP (`gate/up/down_proj`, NVFP4). SGLang's Qwen3.5 model lists `qkv_proj`, `o_proj`, `out_proj`, `in_proj_qkvz`, `gate_up_proj`, `down_proj` and `lm_head` as LoRA modules, so the GDN layers do take LoRA, but only in SGLang's naming: its loader stacks `in_proj_q/k/v/z` (or an already-fused `in_proj_qkvz`) into its packed projection, and has no rule for the checkpoint's own `in_proj_qkv` + `in_proj_z`. An adapter trained with PEFT on this checkpoint names exactly those, and SGLang refuses it (below). The conversion is exact in arithmetic (not yet checked against PEFT in SGLang's kernels; see the caveats) and mechanical: split `in_proj_qkv`'s B rows into q (2048), k (2048) and v (6144) and give each a copy of its A. `in_proj_a/b` and the conv are not LoRA targets.

## Method

Everything on node-b, under the GPU lock (`flock ~/.ling-gpu.lock`, holds logged in `~/.ling-gpu-holds.log`), with m0-prod8000 stopped, in a scratch container `lora-probe` on port 30123: production's `docker run` (85 GB cap, 14 CPUs, the same mounts) and production's launch line flag for flag (`launch.sh`), with the adapters mounted at `/lora/adapters` and a separate inductor cache (`inductor-lora-probe`) so production's compile cache is untouched. m0-prod8000 itself still runs v0.5.19 (digest `d6e72886…`, 16 drafts, mem-fraction 0.6, the pre-v0.5.21 `--cuda-graph-max-bs`): it predates the recipe's move to v0.5.21; this probe tests the recipe, not that container.

The client (`probe.py`) sends six fixed coding prompts, one at a time, greedy, thinking off (`enable_thinking: false`), 384 tokens at most, once streamed (decode tok/s from the first streamed token to the last, client side) and once non-streamed with `return_token_ids` (token-for-token identity). Tokens per step is the server's own: generated tokens over `sglang:spec_verify_calls_total` from `/metrics`, the first token (from prefill) included, as ling-engine's `bench/agentlong/run.py` counts it. agentlong itself could not be used for adapter arms: it speaks `/v1/responses`, which ignores the adapter (below).

## What failed, and the variants

Every launch below is the production line plus `--enable-lora --max-loras-per-batch 4 --max-lora-rank 16 --lora-strict-loading` and the `--lora-paths` named. `--lora-strict-loading` makes any adapter key the loader cannot place an error, so a silently dropped adapter cannot pass for a working one.

| # | Launch | Adapters at start | Result |
|---|---|---|---|
| 1 | production, unchanged | `zero`, `rand`, `rand-gdn` (all modules) | **fails** at target-verify CUDA graph capture, 2.5 min in |
| 2 | + `SGLANG_DISABLE_SILU_FP4_QUANT_FUSION=1` | same | **fails** at the same capture |
| 3 | + no `--enable-torch-compile` | same | **up** in 190 s; every measurement below |
| 4 | production, unchanged | `rand-attn`, `zero-attn`, `rand-gdn`, `zero-gdn` (FP8 layers only) | **up** in 371 s (cold compile); measured |
| 5 | as 3, `--max-loras-per-batch 8` | all modules + `loud` | up in 211 s; routing and memory |

Launch 1, the exact error (`qwen2_moe.py:223`, `Qwen2MoeMLP._silu_fp4_quant_fused`, the dense MLP Qwen3.5 shares):

```
AttributeError: 'RowParallelLinearWithLoRA' object has no attribute 'input_scale_inv'
```

The fused path (`_maybe_enable_silu_fp4_quant_fusion` in `qwen3_5.py`, on for every NVFP4 `down_proj`) reads the base layer's `input_scale_inv` through the module that LoRA has replaced with its wrapper, and hands `down_proj` a prequantized `(fp4, scale)` tuple, which a LoRA wrapper could not use even if the attribute were forwarded: LoRA needs the bf16 input. Its kill switch `SGLANG_DISABLE_SILU_FP4_QUANT_FUSION=1` is the fix, and the model should switch it off itself when LoRA wraps `down_proj` (it already does so for the fused GDN in-projection, `qwen3_5.py:645`).

Launch 2, the exact error (FlashInfer's `mm_fp4`, called from the inductor-compiled `ColumnParallelLinearWithLoRA.forward`, `lora/layers.py:498`, for `gate_up_proj`):

```
RuntimeError: [FP4 gemm Runner] Failed to run cutlass FP4 gemm on sm120/sm121. Error: Error Internal
Exception: Capture cuda graph failed: CUDA error: operation failed due to a previous error during capture
```

The same GEMM runs without torch.compile (launch 3), and torch.compile runs with LoRA around FP8 layers (launch 4), so the failure is the NVFP4 GEMM inside a compiled LoRA wrapper; not chased further. The variants the brief asked for: **LoRA on attention only** (and on GDN, the other FP8 layers) works with the production launch as is (launch 4); **LoRA on the MLP** needs launch 3; **without the drafter** was not needed, since LoRA works with it; **runtime loading** works (below).

## Numbers

One stream, greedy, the six prompts, 2,000-2,300 decoded tokens per row. ms per step is decode time over verify passes; tokens per step counts the accepted drafts plus the bonus token.

| Server | Request | Decode tok/s | Tokens/step | ms/step |
|---|---|---|---|---|
| Production, no LoRA (launch line of the recipe) | base | 57.71 | 5.75 | 99.6 |
| Launch 4 (FP8-layer LoRA, compile and fusion on) | base | 57.40 | 5.75 | 100.2 |
| | `zero-attn` | 55.75 | 5.77 | 103.6 |
| | `rand-attn` | 56.21 | 5.83 | 103.7 |
| | `rand-gdn` | 53.81 | 5.59 | 103.9 |
| Launch 3 without LoRA (no compile, fusion off) | base | 55.71 | 5.53 | 99.2 |
| Launch 3 (all-module LoRA) | base | 54.53 | 5.55 | 101.8 |
| | `zero` | 51.18 | 5.55 | 108.5 |
| | `rand` | 51.30 | 5.58 | 108.7 |
| | `rand-attn` | 53.47 | 5.81 | 108.7 |
| | `rand-mlp` | 51.12 | 5.56 | 108.8 |
| | `rand-gdn` | 55.24 | 6.00 | 108.7 |

So: an active adapter costs 3-4 % of step time on the FP8-only server and 7 % on the all-module server (any adapter: the zero one costs as much as the random one, the kernels run either way); on the all-module server a request without an adapter takes 2.6 % longer per step than on the same launch without LoRA, and switching torch.compile and the fusion off costs no step time at this batch size (99.2 against 99.6 ms; the lower tok/s of that row is its different text accepting fewer drafts). Tokens per step moves with the output text, not with the adapter (rows with a different text have a different count; zero adapters, with the same text, have the same count).

**Zero adapter = base, token for token** (non-streamed, `return_token_ids`, 2,182 tokens over the six prompts): `zero` against base on launch 3, `zero-gdn` against base on launch 3, `zero-attn` and `zero-gdn` against base on launch 4: 6 of 6 identical each time. Base on launch 4 is identical to the production server's (6 of 6), and base on launch 3 identical to launch 3 without LoRA (6 of 6): enabling LoRA does not change the base model's output. (Production and launch 3 without LoRA differ from each other, 0 of 6: torch.compile and the fusion change the numerics.) Every random adapter's output differs from base on all six prompts, so the adapters do act; GDN ones included.

**Concurrency.** Pairs (`rand` + `rand2`, `rand` + base) and a four-way batch (`rand-attn`, `rand-mlp`, `rand-gdn`, base), two prompts, all ran in one batch and completed (384 tokens each, 26-53 tok/s per stream). Greedy token identity with each request's solo run is no test here: two *base* requests sent together already leave their solo output after 10-60 tokens and each other's after 30-58 (they enter the batch at different steps, and the batch shape changes the numerics). The test that does discriminate is the `loud` adapter (B with std 0.5, gibberish): sent at once with two base requests, the loud request stays gibberish and both base requests stay the base model's code (one identical to solo base for all 96 tokens); with `--max-loras-per-batch 8` and six adapters in one prefill, `loud`'s prompt log-probabilities stay at its own (-14.2 nats per token, base -0.66). Per-request routing in a mixed batch works, on prefill and on the speculative verify path. Finer than that this probe cannot see: the random adapters move the prompt log-probabilities by 0.08-0.10 nats per token, the same as batching moves base (0.07).

**Runtime loading.** `/load_lora_adapter` loaded `rand2`, `rand-attn`, `rand-mlp` and `zero-gdn` into the running launch-3 server in well under a second each (152 MiB and less, local disk); `/unload_lora_adapter` and a reload work; `/v1/models` lists each adapter by name. The limits, both hit:

- Only into the pool's module set, fixed at start. `rand-mlp` into launch 4 (started with FP8-layer adapters only): `LoRA adapter rand-mlp with rank 16 is incompatible with the current LoRA memory pool configuration. Please ensure that the LoRA adapter's rank is within the configured --max-lora-rank and that the target modules are included in --lora-target-modules.` The GDN modules cannot even be named in `--lora-target-modules` (its choices list neither `in_proj_qkvz` nor `out_proj`); they enter the pool only through an adapter given at start, or `all`. A server that will take any Dreamference adapter later should start with one zero adapter covering the union of modules.
- Not in the checkpoint's own GDN names. `rand-gdn-hf` (`in_proj_qkv`, `in_proj_z`): the same error message; the same weights converted to `in_proj_q/k/v/z` (`rand-gdn`) load and act.

**Memory per adapter.** The GPU cost is the LoRA pool, preallocated at start for `--max-loras-per-batch` slots at `--max-lora-rank`, for every one of the 64 layers whatever its type (`lora/mem_pool.py`, `init_buffers`), whether or not an adapter is loaded; loading more adapters does not add GPU memory. From the buffer shapes, at rank 16 in bf16: **306 MiB (0.30 GiB) per slot** for all modules (qkv 30 M, o 12 M, gate_up 46 M, down 23 M, in_proj_qkvz 38 M, out_proj 12 M parameters), **174 MiB per slot** for the FP8 layers alone. Measured, as the drop in free memory at LoRA init and as the KV cache the pool displaces at mem-fraction 0.50 (52 KiB per token, target and drafter):

| Pool | Init drop over no LoRA | KV tokens lost | Computed |
|---|---|---|---|
| all modules, 4 slots | 0.89 GiB | 23,158 (1.15 GiB) | 1.20 GiB |
| all modules, 8 slots | 2.42 GiB | 53,258 (2.64 GiB) | 2.39 GiB |
| FP8 layers, 4 slots | 0.61 GiB | 20,228 (1.00 GiB) | 0.68 GiB |

About 0.3 GiB, or ~6,000 tokens of KV cache, per slot for all-module adapters at rank 16; it scales with rank. Loaded adapters sit in host memory at their file size (152 MiB each with all modules, 20 MiB attention only). The KV counts are from launches that started at the same free memory (103-104 GB); free memory on this machine moves with the other jobs, so take them to ±10 %.

## Caveats

- **Acceptance with a real adapter is not measured.** The drafter runs unadapted (SGLang applies adapters to the target only), and these adapters are random perturbations of a few per cent, which do not move the output distribution the way a fine-tune does. A real adapter that changes style or vocabulary will lower the accepted length; how much needs a trained one. A drafter per adapter would need DFlash2 trained against each.
- Numbers are six short coding prompts at one stream, not the agentlong set: agentlong replays Responses bodies, and Responses ignores adapters. The production and FP8-LoRA base rows can be rerun on agentlong as they are.
- `lm_head`, `embed_tokens` and the vision tower were not tested as targets.
- **SGLang's forward was not checked against PEFT's.** "Adapters act" here means their output differs from base, and the zero adapters pass whatever the A/B layout, since B is zero. Neither shows that SGLang's stacked layout (A repeated per slice, B concatenated: `qkv_proj` with the gated 12288-wide q, and the SGLang-only `in_proj_q/k/v/z` stacked into `in_proj_qkvz` that the GDN conversion above targets) computes what a PEFT forward on the HF model computes. Before the first real adapter ships, compare SGLang's logits for a nonzero adapter with a PEFT/Transformers reference on a short prompt, GDN layers included; that is the test that catches a slice-order bug.

## Hold times

Two windows under `~/.ling-gpu.lock`, both logged: 12:56:33-13:36:01 UTC (39.5 min; launches 1-3 and launch 3 without LoRA) and 15:47:34-16:12:25 UTC (25 min; production, launches 4 and 5). m0-prod8000 was found stopped both times and left stopped for the next agent queued on the lock, as the sharing rule says. Scripts (`make_adapters.py`, `launch.sh`, `probe.py`, `route.py`) and raw results are in `node-b:~/lora-probe/`.
