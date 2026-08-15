"""
Model Matrix Registry for Dreamference.

This module provides the ModelMatrixRegistry class which acts as the single source of truth
for supported LLMs, speculative decoding draft models, and short alias resolution.
"""

from typing import Any, Dict, Optional, Final
from dreamference.hardware.model_spec import ModelSpec

# Quantization formats that a checkpoint declares in its own config.json. vLLM auto-detects these,
# and passing an explicit --quantization alongside them fights that detection.
#
# 'AUTOROUND-INT4' is spelled out rather than folded into a bare 'INT4' because the two mean
# different things in this table: several entries list 'INT4' to say "could be served in INT4",
# which declares nothing, while an AutoRound checkpoint ships
# quantization_config.quant_method='auto-round' in config.json and needs no flag.
SELF_DECLARING_PRECISIONS: Final[frozenset] = frozenset(
    {"NVFP4", "MXFP4", "AWQ", "GPTQ", "AUTOROUND-INT4"}
)

# Single source of truth for the model Dreamference serves when nothing else is specified. Imported by
# the config layer and the vLLM launcher so the two cannot drift apart.
DEFAULT_MODEL_ALIAS: Final[str] = "qwen3.5-122b-a10b-int4-dflash"

class ModelMatrixRegistry:
    """
    Registry holding qualified models for NVIDIA GB10 hardware and short alias resolution logic.
    """

    # Static registry of supported target models and speculative draft models
    MATRIX: Final[Dict[str, ModelSpec]] = {
        "qwen3.5-122b-a10b-int4-dflash": ModelSpec(
            name="Qwen 3.5 122B-A10B (INT4 AutoRound + DFlash)",
            params_b=122.0,
            supported_precisions=["AUTOROUND-INT4"],
            # The target shards alone, 71.4 GiB. The DFlash drafter is NOT folded in here:
            # start_server resolves it from speculative_config and adds its own spec's size, so
            # counting it twice would inflate every pre-flight gate.
            min_memory_gb=71.5,
            max_memory_gb=120.0,
            compatible_gb10=True,
            notes=(
                "Default. Same 122B-A10B weights as the NVFP4 entry below, but served as Intel's "
                "AutoRound INT4 checkpoint with the z-lab DFlash drafter in front of it. DFlash is "
                "block-speculative: it drafts a whole block of tokens in one parallel forward "
                "instead of running a head autoregressively, so acceptance is not capped the way "
                "MTP's is. That cap is the entire reason this entry exists — the NVFP4 recipe below "
                "declares mtp_num_hidden_layers=1 and so proposes exactly one token per step, while "
                "the upstream DGX Spark measurements put DFlash at 8.66 accepted tokens per step on "
                "real agent traffic (vs 2.88 for MTP-2) and 5.4 on code (vs 2.77). Agent traffic is "
                "exactly this project's workload.\n\n"
                "Two throughput numbers, kept apart because they are not the same measurement. "
                "Upstream's own like-for-like on one machine: 28.2 tok/s for this INT4 checkpoint "
                "with no speculation, ~81 tok/s for it with DFlash on real agent turns. Separately, "
                "this machine measured 11.0-11.2 tok/s on the NVFP4 checkpoint with speculative_config "
                "unset. Those two baselines are different checkpoints under different recipes, so "
                "the honest expectation here is upstream's ~3x on agent traffic applied to whatever "
                "this box actually does — not a jump from 11 to 81.\n\n"
                "Source: https://forums.developer.nvidia.com/t/"
                "dflash-for-qwen3-5-122b-a10b-80-tok-s-on-1x-spark/374328 and the recipe it "
                "publishes at https://github.com/Entrpi/qwen3.5-122B-A10B-on-spark "
                "(runtime/serve.sh). Several of that recipe's settings are deliberately NOT copied "
                "here — gpu_memory_utilization, load_format, enable_prefix_caching, max_model_len "
                "and max_num_batched_tokens all differ. Each divergence is argued at the flag it "
                "belongs to below; none of them is an oversight.\n\n"
                "Checkpoint choice: serve.sh's own default target, not the INT4+FP8 hybrid the "
                "thread's later posts benchmark. The hybrid needs two source patches to vLLM's INC "
                "quantization layer (patch_inc_hybrid.py, patch_int8_lmhead_v3.py) that are not "
                "upstream and would have to be baked into this project's image, and by the "
                "thread's own numbers they buy ~6% end-to-end and ~0% once DFlash acceptance is "
                "high — which is the regime an agent runs in. Not worth carrying two monkeypatches "
                "for, and one of them no longer applies anyway: it rewrites "
                "quantization/inc.py, which is a package in the pinned image's vLLM rather than "
                "the single module it patches.\n\n"
                "Verified against this project's pinned image on 2026-08-15 rather than assumed: "
                "vLLM 0.24.0 there registers DFlashDraftModel -> qwen3_dflash, accepts "
                "method='dflash' in --speculative-config, and sets parallel_drafting for it. The "
                "drafter's config.json declares architectures=['DFlashDraftModel'], which is the "
                "name that registry entry keys on. The target's architecture "
                "(Qwen3_5MoeForConditionalGeneration) is registered there too, and its "
                "quant_method='auto-round' with packing_format='auto_round:auto_gptq' is one of the "
                "two formats INCConfig.SUPPORTED_FORMATS claims and reroutes to the 'inc' backend. "
                "--async-scheduling stays on: vLLM refuses it for most speculative methods, but "
                "EagleModelTypes nests DFlashModelTypes, so 'dflash' is on the allowed list. No "
                "image change is needed to launch this.\n\n"
                "The flags this recipe generates were then put through "
                "EngineArgs.create_engine_config() inside that image, which resolved both "
                "architectures, detected quantization='inc' without being told, clamped the "
                "drafter's 262144 down to this entry's max_model_len, and reported "
                "method='dflash' n=8 parallel_drafting=True with async scheduling still enabled.\n\n"
                "Flags in reply #53's verbatim `vllm serve` line that this recipe does not carry, "
                "so the gap is a record rather than an omission: --served-model-name qwen (the "
                "runners here address the model by its HF repo ID, which is what vLLM serves it as); "
                "--limit-mm-per-prompt '{\"image\":20}' and --generation-config auto (image input "
                "and generation defaults, neither used by a coding agent); "
                "--override-generation-config '{\"temperature\":0.0,...}' (that is a server-side "
                "default the agent overrides per request anyway); and --ulimit memlock=-1:-1 on the "
                "container, which upstream needs because fastsafetensors stages shards through "
                "pinned host buffers — this recipe does not use fastsafetensors, and the container "
                "here runs at Docker's default 8 MB memlock, which the NVFP4 entry has loaded under "
                "without trouble.\n\n"
                "NOT yet verified: a real load. Every number here is either measured upstream on "
                "another GB10 or derived from this machine's own memory arithmetic. Treat the "
                "first launch as supervised — this is the checkpoint class that froze this host "
                "six times on 2026-08-14, and the pre-flight gates in start_server are what stand "
                "between a bad recipe and the power button."
            ),
            hf_repo_id="Intel/Qwen3.5-122B-A10B-int4-AutoRound",
            launch_overrides={
                # 262144 is the checkpoint's native max and what the upstream recipe serves. The
                # arena below leaves ~12 GiB above the weights, and this checkpoint's KV runs about
                # 24 KiB/token, so 131072 costs ~3.1 GiB of that and 262144 would cost ~6.3 GiB —
                # affordable, but it halves what is left for activations and CUDA graphs, and the
                # KV reservation is claimed during the load, which is the only phase that has ever
                # taken this machine down. 131072 is 4x what the NVFP4 entry dares and still leaves
                # ~9 GiB of slack. Raise to 262144 once a load has completed cleanly and the
                # steady-state footprint is known.
                "max_model_len": 131072,
                # The upstream recipe ships 0.82 and calls it validated. It is not validated *here*.
                # 0.80 passed every static check on this machine on 2026-08-14 and still froze it:
                # driver-pinned weights are unreclaimable, so overshoot livelocks the host in
                # reclaim instead of earning an OOM kill. The difference is not the hardware, it is
                # the desktop — upstream measures headless, and this box runs a session that costs
                # ~17 GB.
                #
                # 0.70 is what the arithmetic supports with that session up: 121.63 GB total, so
                # the arena is 85.1 GB against 71.4 GiB of target shards plus 1.4 GiB of drafter.
                # That leaves ~12.3 GiB above the weights for KV and activations, and it clears
                # start_server's load-peak gate (arena + 15% of weights = 96.1 GB) against ~98.6 GB
                # free with ~2.5 GB to spare. 0.72 clears the same gate by 0.1 GB, which is not a
                # margin. Raise this only when loading headless, where the desktop's ~17 GB comes
                # back.
                "gpu_memory_utilization": 0.70,
                # Unset (bf16 KV), matching the recipe the upstream throughput numbers were taken
                # on. The NVFP4 entry runs fp8 KV; that would halve KV per token here, but it is
                # untested against DFlash's drafter KV geometry and the arena above does not need
                # the space. Left explicit so the choice reads as a choice.
                "kv_cache_dtype": "auto",
                # FA2, not FlashInfer. The DFlash drafter is non-causal and needs FLASH_ATTN; the
                # target is put on the same backend so both KV layouts come from one implementation.
                # FLASH_ATTN also opts into indexes_kv_by_block_stride, which is what lets vLLM
                # 0.24 pad the drafter's larger attention page to unify it with the target's
                # hybrid GDN/mamba page. On 0.23 that padding needed a monkeypatch
                # (runtime/patch_unify2.py upstream); it is upstream in the pinned image, checked
                # in kv_cache_utils.unify_kv_cache_spec_page_size on 2026-08-15.
                "attention_backend": "flash_attn",
                # moe_backend deliberately unset. The NVFP4 entry pins marlin because every
                # FlashInfer FP4 expert path on this box is SM120 code; that argument is about FP4
                # kernels and does not transfer. This checkpoint packs auto_round:auto_gptq, so
                # vLLM's own GPTQ/Marlin selection applies and guessing here would only override it.
                "tool_call_parser": "qwen3_xml",
                "reasoning_parser": "qwen3",
                # 8213, not the upstream recipe's round 8192. vLLM reserves draft-token slots out
                # of this budget — max_num_seqs * (num_speculative_tokens - 1) = 3 * 7 = 21 — and
                # warns that the remainder is what prefill actually gets. At 8192 the chunk lands
                # at 8171; adding the 21 back puts it at exactly 8192. Retuning either
                # max-num-seqs or num_speculative_tokens changes this number.
                "max_num_batched_tokens": 8213,
                # Prefix caching off, and this is the one place the recipe loses something real —
                # upstream measures ~13x warm-prefix TTFT (2.30s -> 0.18s) with it on, which is
                # worth a great deal to an agent re-reading the same files every turn.
                #
                # It cannot be turned on against the pinned image. With DFlash the drafter's
                # attention page is ~2x the target's, so page unification scales the target's
                # mamba+attention block up (2240 -> 4480) while the drafter's group stays at 2240.
                # kv_cache_utils.resolve_kv_cache_block_sizes then sees a MambaSpec whose
                # block_size != cache_config.block_size, takes its back-off branch, and forces
                # hash_block_size to the LCM (4480) — which the drafter's 2240 does not divide, so
                # HybridKVCacheCoordinator aborts at startup. It is a failed launch, not bad output.
                #
                # Upstream fixes this with runtime/patch_prefix_align.py, which makes that back-off
                # fire only for genuinely non-align mamba and falls through to the GCD. That patch
                # is NOT in the pinned image: the exact pre-patch source was read out of
                # kv_cache_utils.py on 2026-08-15 and matches the patch's anchor byte for byte.
                # With prefix caching off, resolve_kv_cache_block_sizes returns before any of that,
                # so this setting is what makes the entry start at all.
                #
                # To get it back, bake patch_prefix_align.py into this project's image build and
                # flip this to True.
                "enable_prefix_caching": False,
                # load_format left at vLLM's default (mmap). Upstream ships fastsafetensors and
                # measures 8 min -> 1 min on load, but that finding does not survive this project's:
                # GB10 has no GDS, so fastsafetensors falls back to staging every shard through
                # host bounce buffers, and on unified memory the bounce buffer and the destination
                # are the same physical RAM — a 71 GiB checkpoint resident twice at the peak. The
                # load peak is precisely what freezes this host. See the load_format comment in
                # build_launch_command.
                "speculative_config": {
                    "method": "dflash",
                    "model": "z-lab/Qwen3.5-122B-A10B-DFlash",
                    # 8, not serve.sh's default of 12, because reply #48 — the post this entry was
                    # added from — runs 8 and calls it "the best compromise for mixed Hermes
                    # traffic". That is a compromise, not a win: upstream's own FINDINGS puts the
                    # task-dependent optimum at "prose -> 4, agent/code -> 12+", and the poster's
                    # spec-bench at n=8 shows structured output accepting 99.6-100% of the block
                    # with tau pinned at 8.0 — the window is saturated, so tool-call traffic is
                    # leaving throughput on the table. Prose is the other end: acceptance 19-38%,
                    # where every unaccepted draft is a wasted verify.
                    #
                    # 8 is kept because it is what the linked configuration actually ran for two
                    # weeks. If this box's traffic turns out to be mostly tool calls and code,
                    # raising this to 12 is the first tuning move to try, and max_num_batched_tokens
                    # below has to move with it.
                    "num_speculative_tokens": 8,
                    "attention_backend": "FLASH_ATTN",
                },
                "extra_args": [
                    # 3, from the upstream recipe. Concurrency is nearly free on this box
                    # (bandwidth-bound decode batches well), but every stream reserves KV.
                    "--max-num-seqs", "3",
                    "--tensor-parallel-size", "1",
                    "--dtype", "auto",
                    # Thinking off, matching reply #48. This is not a no-op: the checkpoint's own
                    # chat_template.jinja prefills '<think>\n' into every assistant turn unless
                    # enable_thinking is explicitly false, in which case it emits an empty
                    # '<think>\n\n</think>' instead. So the shipped default makes the model open
                    # every agent turn with a reasoning block, and an agent turn is mostly tool
                    # calls. #48 ran two weeks of agent traffic this way without a single
                    # tool-call formatting or parsing error.
                    #
                    # reasoning_parser stays set regardless: it costs nothing when there is no
                    # reasoning to split, and it keeps working if a client re-enables thinking
                    # per request, which this only changes the default for.
                    #
                    # Upstream also passes its own patched Jinja file here (--chat-template
                    # /host/unsloth.jinja). That is deliberately not copied: reply #53 describes
                    # it as "a small vLLM/Jinja compatibility adjustment for tool-call arguments",
                    # and shipping a third-party template would put the prompt format outside this
                    # repo. If tool-call arguments turn out to mis-render, that template is the
                    # first place to look.
                    "--default-chat-template-kwargs", '{"enable_thinking": false}',
                ],
                "env": {
                    # vLLM's memory profiler over-reserves for the CUDA graph pool (~0.7 GiB
                    # estimated against ~0.14 GiB actually captured); disabling the estimate hands
                    # the difference back to KV and takes the real capture out of the ~36 GB left
                    # outside the arena at 0.70. Upstream calls this safe at 0.82, where the
                    # headroom is ~21 GiB — there is far more of it here.
                    "VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS": "0",
                    # Same trade as the two entries below: atomic-add reduction is the faster
                    # Marlin path when the block count is high, at the cost of run-to-run bit
                    # identity. Unset it before chasing any bug that needs identical outputs.
                    "VLLM_MARLIN_USE_ATOMIC_ADD": "1",
                },
            },
        ),
        "qwen3.5-122b-a10b-nvfp4": ModelSpec(
            name="Qwen 3.5 122B-A10B (NVFP4)",
            params_b=122.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=78.0,
            max_memory_gb=120.0,
            compatible_gb10=True,
            notes=(
                "Previous default, superseded by the INT4 + DFlash entry above on 2026-08-15 — kept "
                "because its recipe is the one that has actually been loaded on this machine, and it "
                "is the fallback if DFlash does not come up. Its ceiling is speculative: this "
                "checkpoint's MTP head declares one hidden layer, so it proposes one token per step. "
                "At 78 GB this checkpoint is 64% of a GB10's unified memory, and the weights "
                "the driver pins are unreclaimable, so overshooting does not earn an OOM kill — the "
                "host livelocks in reclaim until the power button. That froze this machine six times "
                "on 2026-08-14 at every gpu_memory_utilization from 0.9 down to 0.3 — but not "
                "because the fraction is irrelevant. It has two bounds and those runs violated one "
                "or the other: below ~0.64 the arena is smaller than the weights, so the load "
                "overruns a budget it was never given, and above ~0.76 the arena plus the load's "
                "transient peak exceeds what is actually free. Both are now checked before launch "
                "rather than discovered by reset. The cgroup cap is the backstop, not the primary "
                "bound, and only became one once it was sized against the arena — at total-minus- "
                "reserve it sat above anything the container could reach and never fired. "
                "Tensorizer is deliberately not enabled here — NVFP4 checkpoints opt "
                "out of it, and it would not help regardless, since it does no O_DIRECT or fadvise "
                "and so never bypasses the page cache."
            ),
            hf_repo_id="nvidia/Qwen3.5-122B-A10B-NVFP4",
            launch_overrides={
                # 32k rather than the checkpoint's full 131072: the KV reservation is claimed during
                # the load, which is the only phase that has ever taken this machine down, and the
                # shorter context buys back roughly 15-20 GB of headroom exactly when it is scarcest.
                # Raise it once a load completes cleanly and the steady-state footprint is known.
                "max_model_len": 32768,
                # Must exceed 0.64 to hold the 77.8 GB of weights at all. The ceiling is not the
                # 12 GB host reserve, though — it is what is actually free at launch, minus the
                # peak the load passes through on its way to steady state (see
                # LOAD_TRANSIENT_FRACTION). 0.80 satisfied every static check and still froze the
                # machine on 2026-08-14: 97.3 GB of arena against 104.5 GB available left 7.2 GB
                # to absorb the page cache for a 77.8 GB checkpoint, and it did not.
                #
                # 0.72 puts the arena at 87.6 GB — 9.8 GB above the weights for KV and
                # activations, ample at 32k context with max-num-seqs 4 — and clears the gate by
                # ~5 GB with a browser and an IDE open. That margin is the point: the gate's own
                # answer here is 0.76, which passes by 0.4 GB and would fail again the moment
                # anything on the desktop grows. Erring low costs KV cache and fails loudly if
                # overdone; erring high costs the power button. Raise it only when loading
                # headless, where ~17 GB of desktop comes back.
                "gpu_memory_utilization": 0.70,
                "kv_cache_dtype": "fp8",
                "attention_backend": "flashinfer",
                # Marlin, because every FlashInfer FP4 expert path on this box is SM120 code. That
                # is broader than the CUTLASS caveat in the 35b notes below: flashinfer_b12x fails
                # here too, and for the same reason. Measured 2026-08-14 —
                # `--moe-backend flashinfer_b12x` passes the oracle's arch check
                # ("Using 'FLASHINFER_B12X' NvFp4 MoE backend"), loads all 9 shards, compiles, and
                # then dies in the KV-profiling forward:
                #
                #   flashinfer/fused_moe/cute_dsl/b12x_moe.py -> launch_sm120_moe
                #   RuntimeError: CUDA Error: cudaErrorInvalidValue
                #   kernel 'MoEDynamicKernel...' launch shared memory exceeds curr[ent limit]
                #
                # The dispatch entry point is named launch_sm120_moe: the kernel is built for
                # SM120's shared-memory budget and asks for more than SM121 grants, so the launch
                # is rejected. The oracle only checks the arch family, which is why selection
                # succeeds and launch does not.
                #
                # This one fails loudly, unlike the CUTLASS corruption — a failed load, not bad
                # output. Do not re-try the FlashInfer FP4 backends here without first confirming
                # the shared-memory request against SM121's per-block limit.
                "moe_backend": "marlin",
                "tool_call_parser": "qwen3_xml",
                "reasoning_parser": "qwen3",
                # Self-speculation off the MTP head shipped in this checkpoint. 785 mtp.* tensors
                # sit in the safetensors index and were being loaded and ignored, because
                # speculative_config was unset.
                #
                # Decode here is hard memory-bandwidth-bound: measured 11.0-11.2 tok/s on every
                # one of seven inspect prompts regardless of length, and aggregate throughput
                # doubled to 22 with a second concurrent stream — batching is nearly free, so the
                # per-token cost is dominated by reading weights, not by compute. That is exactly
                # the regime speculation pays in, since verifying k proposed tokens costs about
                # one weight read rather than k.
                #
                # num_speculative_tokens 1, not the 3 used by the qwen3.6-35b recipe below: this
                # checkpoint declares mtp_num_hidden_layers=1, so there is a single head and one
                # proposal per step is what it can actually predict. Asking for 3 drives the head
                # autoregressively and acceptance collapses.
                #
                # Lossless by construction — the full model verifies every proposal and keeps it
                # only if it matches what it would have produced, so a bad head costs speed, not
                # quality.
                # "speculative_config": {"method": "mtp", "num_speculative_tokens": 1, "moe_backend": "triton"},
                "extra_args": [
                    "--max-num-seqs", "4",
                    "--tensor-parallel-size", "1",
                    "--dtype", "auto",
                ],
                # Marlin accumulates partial products per thread block and reduces them at the end.
                # The atomic-add reduction is the faster path when the block count is high, which it
                # is here: 256 experts at intermediate_size 1024 makes every expert GEMM small and
                # numerous. Set on the 35b recipe since before this model was added; carried over
                # now that the 122b is on marlin too, for the same reason.
                #
                # Not bit-identical run to run — atomic float adds commit in nondeterministic order.
                # That is a throughput-for-reproducibility trade, so unset it before chasing any bug
                # that needs identical outputs across runs.
                "env": {
                    "VLLM_MARLIN_USE_ATOMIC_ADD": "1",
                },
            },
        ),
        "qwen3.6-35b-a3b-nvfp4": ModelSpec(
            name="Qwen 3.6 35B-A3B (NVFP4)",
            params_b=35.0,
            supported_precisions=["NVFP4"],
            min_memory_gb=25.0,
            max_memory_gb=60.0,
            compatible_gb10=True,
            notes=(
                "Small-model option. MoE with ~3B active params — decode speed tracks active params, not total, "
                "which is what the GB10's memory bandwidth rewards. Requires the FlashInfer b12x NVFP4 "
                "path (vLLM >= the May 2026 SM12x backends); the CUTLASS FP4 path is compiled for SM120 "
                "and silently emits garbage on SM121. Launch values follow NVIDIA's DGX Spark recipe."
            ),
            hf_repo_id="nvidia/Qwen3.6-35B-A3B-NVFP4",
            launch_overrides={
                "max_model_len": 131072,
                "gpu_memory_utilization": 0.3,
                "kv_cache_dtype": "fp8",
                "attention_backend": "flashinfer",
                "tool_call_parser": "qwen3_xml",
                "reasoning_parser": "qwen3",
                "max_num_batched_tokens": 8192,
                # MTP ships inside this checkpoint. Without it NVFP4 lands at the low end of the
                # published throughput range, so it is part of the recipe rather than a tuning extra.
                "speculative_config": {"method": "mtp", "num_speculative_tokens": 3, "moe_backend": "triton"},
                "extra_args": [
                    "--max-num-seqs", "4",
                    "--tensor-parallel-size", "1",
                    "--dtype", "auto",
                ],
                "env": {
                    "VLLM_MARLIN_USE_ATOMIC_ADD": "1",
                },
            },
        ),
        "qwen2.5-coder-32b": ModelSpec(
            name="Qwen 2.5 Coder 32B",
            params_b=32.0,
            supported_precisions=["BF16", "INT8", "FP8"],
            min_memory_gb=35.0,
            max_memory_gb=64.0,
            compatible_gb10=True,
            notes="Fits comfortably in 128GB Unified Memory",
            hf_repo_id="Qwen/Qwen2.5-Coder-32B-Instruct"
        ),
        "qwen2.5-coder-72b": ModelSpec(
            name="Qwen 2.5 Coder 72B",
            params_b=72.0,
            supported_precisions=["INT8", "FP8", "INT4"],
            min_memory_gb=45.0,
            max_memory_gb=80.0,
            compatible_gb10=True,
            notes="Supported (INT8/FP8 quantized fit)",
            hf_repo_id="Qwen/Qwen2.5-Coder-72B-Instruct"
        ),
        "deepseek-r1-distill-32b": ModelSpec(
            name="DeepSeek-R1-Distill-Qwen-32B",
            params_b=32.0,
            supported_precisions=["BF16", "INT8", "FP8"],
            min_memory_gb=35.0,
            max_memory_gb=64.0,
            compatible_gb10=True,
            notes="Fits comfortably in 128GB Unified Memory",
            hf_repo_id="deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"
        ),
        "deepseek-r1-distill-70b": ModelSpec(
            name="DeepSeek-R1-Distill-Llama-70B",
            params_b=70.0,
            supported_precisions=["INT8", "FP8", "INT4"],
            min_memory_gb=45.0,
            max_memory_gb=80.0,
            compatible_gb10=True,
            notes="Supported (INT8/FP8 quantized fit)",
            hf_repo_id="deepseek-ai/DeepSeek-R1-Distill-Llama-70B"
        ),
        "llama-3.3-70b": ModelSpec(
            name="Llama 3.3 70B Instruct",
            params_b=70.0,
            supported_precisions=["INT8", "FP8"],
            min_memory_gb=75.0,
            max_memory_gb=80.0,
            compatible_gb10=True,
            notes="Supported (INT8/FP8 quantized fit)",
            hf_repo_id="meta-llama/Llama-3.3-70B-Instruct"
        ),
        "qwen3.5-122b-a10b-dflash-draft": ModelSpec(
            name="Qwen 3.5 122B-A10B DFlash Drafter (Draft Model)",
            params_b=0.8,
            supported_precisions=["BF16"],
            # Measured, not estimated: 1.44 GiB is what _estimate_model_weights_gb reports for the
            # fetched snapshot on 2026-08-15. Rounded up, so the pre-download run and the runs
            # after it put the same number through the gates.
            min_memory_gb=1.5,
            max_memory_gb=2.5,
            compatible_gb10=True,
            notes=(
                "Block-diffusion drafter for the default INT4 + DFlash entry, named by that entry's "
                "speculative_config rather than served on its own. Listed here so the pre-flight "
                "memory gates and the pre-download step have a size to work with before it has been "
                "fetched — _estimate_model_weights_gb falls back to min_memory_gb until the "
                "snapshot exists on disk, and without an entry the drafter would count as 0 GB on "
                "the one run where nothing is cached yet. Its config.json declares "
                "architectures=['DFlashDraftModel'], which is the key vLLM's model registry maps to "
                "qwen3_dflash."
            ),
            hf_repo_id="z-lab/Qwen3.5-122B-A10B-DFlash",
        ),
        "qwen2.5-coder-1.5b": ModelSpec(
            name="Qwen 2.5 Coder 1.5B (Draft Model)",
            params_b=1.5,
            supported_precisions=["BF16", "FP16", "INT8"],
            min_memory_gb=3.5,
            max_memory_gb=6.0,
            compatible_gb10=True,
            notes="Speculative decoding draft model (~3.5GB memory)",
            hf_repo_id="Qwen/Qwen2.5-Coder-1.5B-Instruct"
        ),
        "qwen2.5-coder-3b": ModelSpec(
            name="Qwen 2.5 Coder 3B (Draft Model)",
            params_b=3.0,
            supported_precisions=["BF16", "FP16", "INT8"],
            min_memory_gb=6.5,
            max_memory_gb=10.0,
            compatible_gb10=True,
            notes="Speculative decoding draft model (~6.5GB memory)",
            hf_repo_id="Qwen/Qwen2.5-Coder-3B-Instruct"
        ),
        "starcoder2-15b": ModelSpec(
            name="StarCoder2 15B",
            params_b=15.0,
            supported_precisions=["BF16", "FP16"],
            min_memory_gb=20.0,
            max_memory_gb=30.0,
            compatible_gb10=True,
            notes="Fits easily",
            hf_repo_id="bigcode/starcoder2-15b"
        ),
        "deepseek-v3-671b": ModelSpec(
            name="DeepSeek-V3 671B (MoE)",
            params_b=671.0,
            supported_precisions=["INT4"],
            min_memory_gb=350.0,
            max_memory_gb=400.0,
            compatible_gb10=False,
            notes="Exceeds 128GB (Requires multi-node or >128GB hardware)",
            hf_repo_id="deepseek-ai/DeepSeek-V3"
        ),
    }

    @classmethod
    def resolve_hf_repo(cls, model_key: str) -> str:
        """
        Resolves model alias, repo string, or display name to official HuggingFace repository ID.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            str: Official HuggingFace repository identifier (or original string if unmapped).
        """
        if not model_key:
            return model_key
        spec = cls.get_spec(model_key)
        return spec.hf_repo_id if spec else model_key

    @classmethod
    def get_spec(cls, model_key: str) -> Optional[ModelSpec]:
        """
        Retrieves ModelSpec for given short alias, HuggingFace repo ID, or display name.

        Args:
            model_key (str): Short model name key, HF repo ID, or display name.

        Returns:
            Optional[ModelSpec]: ModelSpec object or None if key is unrecognized.
        """
        if not model_key:
            return None
        key = model_key.strip().lower()
        if key in cls.MATRIX:
            return cls.MATRIX[key]
        for spec in cls.MATRIX.values():
            if spec.hf_repo_id.lower() == key or spec.name.lower() == key:
                return spec
        norm_key = key.replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
        for alias, spec in cls.MATRIX.items():
            norm_alias = alias.replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
            norm_hf = spec.hf_repo_id.lower().replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
            norm_name = spec.name.lower().replace(" ", "").replace("_", "").replace("-", "").replace("/", "")
            if norm_key in (norm_alias, norm_hf, norm_name):
                return spec
        return None

    @classmethod
    def get_launch_overrides(cls, model_key: str) -> Dict[str, Any]:
        """
        Retrieves the per-model vLLM launch recipe for the given alias, repo ID, or display name.

        Args:
            model_key (str): Model alias, HF repo ID, or display name. Unrecognized keys
                yield an empty recipe, leaving the caller on global defaults.

        Returns:
            Dict[str, Any]: Copy of the model's launch_overrides, safe for the caller to mutate.
        """
        if not model_key:
            return {}
        spec = cls.get_spec(model_key)
        return dict(spec.launch_overrides) if spec else {}

    @classmethod
    def get_speculative_draft_repo(cls, model_key: str) -> Optional[str]:
        """
        Retrieves the separate draft checkpoint a model's recipe speculates against, if any.

        Two kinds of speculation live in `launch_overrides['speculative_config']` and only one of
        them names a second checkpoint. Self-speculation (MTP, Eagle heads) ships inside the target
        and has no 'model' key; a drafter like DFlash is its own repository that has to be fetched
        and counted against memory alongside the target. Callers that pre-download weights or size
        the pre-flight memory gates need to see the second kind, and it is invisible to them
        otherwise — the `draft_model` argument on the launcher only covers drafters named by the
        caller, not ones the recipe brings with it.

        Args:
            model_key (str): Model alias, HF repo ID, or display name.

        Returns:
            Optional[str]: HuggingFace repo ID of the recipe's draft model, or None when the model
                is unknown, speculates against itself, or does not speculate at all.
        """
        spec_config = cls.get_launch_overrides(model_key).get("speculative_config") or {}
        return spec_config.get("model") or None

    @classmethod
    def declares_own_quantization(cls, model_key: str) -> bool:
        """
        Reports whether the checkpoint carries its quantization format in its own config.

        Callers use this to suppress an inferred `--quantization` flag: an NVFP4 or AWQ checkpoint
        already tells vLLM what it is, and overriding that with a guess derived from the model name
        misconfigures the load.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: True when the model's precisions are all self-declaring.
        """
        spec = cls.get_spec(model_key) if model_key else None
        if not spec or not spec.supported_precisions:
            return False
        return all(p.upper() in SELF_DECLARING_PRECISIONS for p in spec.supported_precisions)
