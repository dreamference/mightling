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
#
# hybrid-dflash since 2026-08-23: the Intel int4-dflash recipe plus the dense-bandwidth stack,
# promoted after serving and benchmarking on this machine (prose 23.8 / code 49.9 / JSON 53.1
# tok/s single-stream at 32k context and eight slots). int4-dflash stays in the matrix as the
# tested fallback.
DEFAULT_MODEL_ALIAS: Final[str] = "qwen3.5-122b-a10b-hybrid-dflash"

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
                "Fallback (was the default until 2026-08-23). Same 122B-A10B weights as the "
                "NVFP4 entry below, but served as Intel's "
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
                "and generation defaults, neither used by a coding agent); and --ulimit memlock=-1:-1 on the "
                "container, which upstream needs because fastsafetensors stages shards through "
                "pinned host buffers — this recipe does not use fastsafetensors, and the container "
                "here runs at Docker's default 8 MB memlock, which the NVFP4 entry has loaded under "
                "without trouble.\n\n"
                "--override-generation-config IS carried, contrary to an earlier note here that "
                "dismissed it as a default the agent would override per request. Codex does not "
                "override it — it has no sampling setting to override it with — so every one of its "
                "turns sampled at the checkpoint's temperature 0.6 / top_p 0.95 / top_k 20. It is "
                "now applied for every model this project serves; see DEFAULT_GENERATION_OVERRIDES.\n\n"
                "NOT yet verified: a real load. Every number here is either measured upstream on "
                "another GB10 or derived from this machine's own memory arithmetic. Treat the "
                "first launch as supervised — this is the checkpoint class that froze this host "
                "six times on 2026-08-14, and the pre-flight gates in start_server are what stand "
                "between a bad recipe and the power button."
            ),
            hf_repo_id="Intel/Qwen3.5-122B-A10B-int4-AutoRound",
            # Qwen3_5MoeForConditionalGeneration with a vision_config: this checkpoint takes
            # images. Verified against the checkpoint's own config.json, not inferred from the name.
            supports_vision=True,
            launch_overrides={
                # This model brings its own vLLM. The project's pinned image cannot run it: the
                # load on 2026-08-15 reached 100% of the weights and then died in KV-cache
                # profiling on the page-size unification assert described at attention_backend
                # below, which needs a source patch upstream ships as runtime/patch_unify2.py.
                #
                # The base is the image the source thread publishes and measures on (reply #53:
                # ghcr.io/aeon-7/aeon-vllm-ultimate:2026-06-18-v0.23.0-dflashfix, digest
                # sha256:be9e05a11da6e72607ab6f3e960993b253b673af0727005122a3266129a518e3,
                # vLLM 0.23.0+aeon.sm121a.dflash) — but that image alone does not start either.
                # It failed here on 2026-08-15 at the same assert the project image did, because
                # upstream's serve.sh patches the image at container start rather than shipping it
                # patched. Dockerfile.dflash bakes in both patches it needs — see runtime/patch_kv_unify.py
                # (decides whether it starts) and runtime/patch_prefix_align.py (decides whether it can
                # start with prefix caching).
                #
                # Third-party base, run with --gpus all and the host network like every image this
                # project launches — worth knowing before adopting it. Every other model keeps
                # DEFAULT_VLLM_IMAGE.
                "docker_image": "dreamference-vllm-dflash:0.23.0-aeon-kvfix2",
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
                # 0.68, arrived at by being wrong once. 0.70 was set from a reading of 98.45 GB
                # available and cleared the load-peak gate by 2.37 GB; an hour later the desktop
                # had grown ~2.5 GB and the same recipe was refused by 0.16 GB — the gate did its
                # job, but a margin that thin is a coin toss against a browser.
                #
                # At 0.68 the arena is 82.7 GB against 71.4 GiB of target shards plus 1.4 GiB of
                # drafter: 9.8 GiB above the weights for KV, activations and CUDA graphs, which at
                # ~24 KiB/token covers this entry's 131072 context (~3.1 GiB) with room over. The
                # gate then wants 93.7 GB against ~95.9 GB available — 2.2 GB of slack that a few
                # more browser tabs cannot eat.
                #
                # The direction of error is deliberate. Erring low costs KV cache and fails
                # loudly; erring high costs the power button. Raise this only when loading
                # headless, where the desktop's ~17 GB comes back.
                "gpu_memory_utilization": 0.68,
                # Unset (bf16 KV), matching the recipe the upstream throughput numbers were taken
                # on. The NVFP4 entry runs fp8 KV; that would halve KV per token here, but it is
                # untested against DFlash's drafter KV geometry and the arena above does not need
                # the space. Left explicit so the choice reads as a choice.
                "kv_cache_dtype": "auto",
                # FA2, not FlashInfer. The DFlash drafter is non-causal and needs FLASH_ATTN; the
                # target is put on the same backend so both KV layouts come from one implementation.
                # FLASH_ATTN also opts into indexes_kv_by_block_stride, which vLLM 0.24 uses to pad
                # an attention page that does not divide the target's hybrid GDN/mamba page.
                #
                # That padding is NOT enough for this pair, and an earlier version of this comment
                # claimed otherwise. The load on 2026-08-15 disproved it: weights reached 100%, then
                # profiling died in kv_cache_utils.unify_kv_cache_spec_page_size at
                # `assert new_spec.page_size_bytes == max_page_size`. The padding branch is an
                # `elif` guarding only the *non-divisible* case, while the assert sits after both
                # branches — so a drafter page that divides a padded max_page_size takes the
                # scaling branch, lands just under it, and trips the assert. This is the failure
                # upstream's runtime/patch_unify2.py exists to fix, and it is still live in 0.24.
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
                # 2026-08-24 ROOT CAUSE of the zero-hit isolation result (found by driving the
                # real KVCacheManager offline inside the image, no GPU needed): vLLM's mamba
                # 'align' mode never materialises intermediate GDN state blocks during prefill.
                # The mamba group's block table is the null block everywhere except the live
                # tail state, cache_full_blocks skips null blocks, and the hybrid coordinator's
                # get_cached_block demands a hit in EVERY KV group — so nothing can hit below
                # the first boundary at which a real mamba state block exists, on any
                # checkpoint, image, or flag set. Store and lookup are both quantized to the
                # scheduler_block_size grid — the LCM across groups, 4480 here because the
                # DFlash drafter's block is 2x the target's 2240 — and align mode only
                # materialises mamba states when prefill runs long enough to checkpoint one
                # (multi-chunk, >8280-token prompts). Live-confirmed 2026-08-24 on this exact
                # config: a 7.6k identical re-send hits 0 (single chunk, no state below 4480's
                # first reachable boundary), while a second turn over a 12.5k-token first turn
                # hit exactly 8960 = floor(12519/4480)*4480 — the first nonzero hit ever
                # observed on this stack, and it skipped 11200 (odd 2240-multiple), pinning the
                # 4480 grid. The historical 1.4-3.6% logged rates were these long-history
                # multi-turn hits. Follow-up analysis indicates those rare hits are also WRONG:
                # the scheduler's chunk splitter aligns to cache_config.block_size (2240)
                # while page unification scaled the mamba block to 4480, so the checkpointed
                # state can be up to 2240 tokens short of the boundary its hash claims — by the
                # slot-write arithmetic the live 8960 hit restored state@6720 (kernel write
                # semantics inferred, not directly observed; the spec's A/B confirms). Fix
                # designed in
                # specs/DREAMFERENCE_PREFIX_CACHE.md; patch in
                # runtime/patch_mamba_chunk_align.py, baked into the dense2 image the hybrid
                # entry pins. THIS entry's kvfix2 image does not carry it (untouched fallback).
                # 'all' mode would cache every block, but vLLM
                # forces 'align' for models lacking SupportsMambaPrefixCaching — mamba1/mamba2
                # families only; Qwen3.5's GDN is not among them — so no flag reaches it.
                # Upstream design limitation, not aeon/DFlash/dense-stack specific. The flag
                # stays on: boundary-crossing continuations do hit, and the full-attention
                # groups' caching works; only the 13x warm-prefix figure below is upstream's
                # (non-hybrid), not this stack's.
                # Prefix caching ON, which is worth roughly 13x on warm-prefix TTFT upstream
                # (2.30s -> 0.18s on a 4k prefix) and matters more here than any other single
                # setting: an agent re-reads the same files every turn, and without this every turn
                # re-prefills them from scratch.
                #
                # It took two things to enable. Two KV cache groups exist with DFlash — the
                # drafter's attention page is ~2x the target's — so page unification scales the
                # target's mamba+attention block up while the drafter's group stays put.
                # resolve_kv_cache_block_sizes then sees a MambaSpec whose block_size differs from
                # cache_config.block_size, reads that as unaligned mamba, and forces
                # hash_block_size to the LCM, which the drafter's group does not divide —
                # HybridKVCacheCoordinator aborts at startup. runtime/patch_prefix_align.py keys
                # that back-off on the actual cache mode instead, so the GCD path runs.
                #
                # The second thing is that vLLM's own default would still have turned this off:
                # ModelConfig.is_prefix_caching_supported() returns False for hybrid attention.
                # That value is only consulted when enable_prefix_caching is None, so stating True
                # explicitly overrides it — which is what the launcher now does.
                #
                # No --mamba-cache-mode flag is needed. vLLM sets the mode itself once prefix
                # caching is on ('all' when the model supports mamba prefix caching, 'align'
                # otherwise) and requires chunked prefill for align, which this recipe already
                # enables.
                "enable_prefix_caching": True,
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
        "qwen3.5-122b-a10b-hybrid-dflash": ModelSpec(
            name="Qwen 3.5 122B-A10B (INT4+FP8 hybrid + DFlash + dense-bandwidth stack)",
            params_b=122.0,
            supported_precisions=["INT4+FP8-HYBRID"],
            min_memory_gb=71.5,
            max_memory_gb=120.0,
            compatible_gb10=True,
            notes=(
                "The int4-dflash entry above plus the dense-bandwidth stack from "
                "github.com/Entrpi/qwen3.5-122B-A10B-on-spark -- the same lineage the kvfix "
                "patches came from. Three additions, all baked into the pinned image "
                "(Dockerfile.dense): FP8 dispatch for the hybrid checkpoint's dense layers, an "
                "int8 w8a16 Triton GEMV lm-head (which also frees the dead bf16 head, ~1.4 GiB "
                "back to KV), and FLA sm121 shared-memory tuning. Upstream measures the stack "
                "at +28% base throughput and ~81 tok/s on real agent turns with DFlash on this "
                "hardware. Two of upstream's defaults are deliberately NOT carried over, for "
                "reasons the int4-dflash entry documents at length: gpu_memory_utilization "
                "stays 0.68 (their 0.82 is headless math; this box runs a desktop and froze at "
                "0.80), and load_format stays mmap (their fastsafetensors is a double-residency "
                "load peak without GDS, which is what freezes this host). The default since "
                "2026-08-23. Repointed from the bleysg hybrid checkpoint back to Intel's INT4 "
                "on 2026-08-24, per upstream's amortization law (dense FP8-experts gains fall "
                "to ~0% at agent-level speculative acceptance). The zero-prefix-cache-hit "
                "behaviour first blamed on the bleysg checkpoint turned out to be "
                "architectural — every hybrid-GDN checkpoint has it; see the int4-dflash "
                "entry's enable_prefix_caching comment for the root cause. The int8 lm-head and FLA "
                "patches still apply; the FP8-experts patch detects no FP8 dense layers and "
                "stands down. int4-dflash remains the untouched fallback."
            ),
            hf_repo_id="Intel/Qwen3.5-122B-A10B-int4-AutoRound",
            # Same Qwen3_5MoeForConditionalGeneration architecture and vision_config as the
            # Intel export it is derived from; verified against the checkpoint's config.json,
            # per this field's rule, not inferred from the alias.
            supports_vision=True,
            launch_overrides={
                # Values mirror the int4-dflash entry above verbatim, comments included by
                # reference -- one recipe, one place to reason about it. Only the image differs.
                # dense2 = dense1 + patch_mamba_chunk_align (2026-08-24): prefill chunks align
                # to the LCM grid so mamba 'align'-mode checkpoints are correct and reachable.
                # dense3 = dense2 + patch_unify_downscale (2026-08-24): page unification scales
                # the drafter's block DOWN (2240 -> 1120) instead of the target's up, so the
                # LCM — the prefix-cache hit floor/grid — halves 4480 -> 2240 at zero capacity
                # cost, and the mamba page's 2x padding waste returns to the pool. Full story:
                # specs/DREAMFERENCE_PREFIX_CACHE.md. The int4-dflash fallback deliberately
                # stays on kvfix2 (untouched-fallback principle) and so retains the stale-hit
                # hazard that spec documents.
                "docker_image": "dreamference-vllm-dflash:0.23.0-aeon-dense3",
                # 32k, not the int4-dflash entry's 131k, for two stacked reasons. The hard one:
                # this stack's non-KV residency is larger (the int8 lm-head holds both copies
                # until its lazy first-forward build), and the first load refused loudly --
                # "one request at max seq len needs 9.03 GiB KV, available 5.78 GiB". The soft
                # one: this recipe exists for concurrent research/agent traffic, where eight
                # bounded streams beat one unbounded, and 32k bounds each stream's KV claim.
                "max_model_len": 32768,
                "env": {
                    "VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS": "0",
                    "VLLM_MARLIN_USE_ATOMIC_ADD": "1",
                },
                # 0.72, not the int4-dflash entry's 0.68: the Intel shards are ~5 GiB larger than
                # the bleysg hybrid this entry briefly served, and at 0.68 the engine refused to
                # start -- 5.9 GiB KV needed for one 32k request against 5.4 available
                # (measured 2026-08-24). 0.72 is the NVFP4 entry's own operating point on an
                # even larger checkpoint; the pre-flight gate still referees against the live
                # desktop before any load, which is the designed loud failure.
                # Lowered 0.72 -> 0.70 on 2026-08-24 at that gate's own instruction: with the
                # desktop ~2 GB fatter than at the morning launch, 0.72's arena (87.57) plus the
                # 10.94 GB load peak needed 98.51 GB against 97.19 available, and a retry at
                # 0.71 still missed by 0.13 GB while the desktop drifted between checks. 0.70
                # leaves ~6.5 GiB for KV against the 5.9 floor above -- tight but above it --
                # and buys the launch a standing margin instead of gating on the day's tabs.
                # Worth raising back toward 0.72 when the desktop is lighter.
                "gpu_memory_utilization": 0.70,
                "kv_cache_dtype": "auto",
                "attention_backend": "flash_attn",
                "tool_call_parser": "qwen3_xml",
                "reasoning_parser": "qwen3",
                # 8280 = 8192 + max_num_seqs * (num_speculative_tokens - 1) = 8192 + 8*11; the
                # draft-slot arithmetic the int4-dflash entry documents, retuned for 8 seqs
                # and the n=12 draft window below.
                "max_num_batched_tokens": 8280,
                "enable_prefix_caching": True,
                "speculative_config": {
                    "method": "dflash",
                    "model": "z-lab/Qwen3.5-122B-A10B-DFlash",
                    # 12, up from 8 on 2026-08-24. The registry's own rule was "raise to 12
                    # when the traffic proves tool-call-heavy", and the evidence arrived from
                    # two directions at once: upstream's Hermes bench (73% tool calls) measures
                    # median acceptance ~8.3 -- above the entire n=8 window -- at 121.8 tok/s,
                    # and this box's own metrics still accepted 17% at position 7, the last
                    # slot n=8 offers.
                    "num_speculative_tokens": 12,
                    "attention_backend": "FLASH_ATTN",
                },
                "extra_args": [
                    # 8, not the int4-dflash entry's 3: with per-stream KV bounded at 32k the
                    # pool fits eight, and the measured Deep Research livelock (capacity queue
                    # -> 60s first-chunk timeout -> abort/retry churn) is a queueing problem
                    # that slots solve. Decode is bandwidth-bound and batches nearly free.
                    "--max-num-seqs", "8",
                    "--tensor-parallel-size", "1",
                    "--dtype", "auto",
                    "--default-chat-template-kwargs", '{"enable_thinking": false}',
                ],
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
            # Qwen3_5MoeForConditionalGeneration with a vision_config: this checkpoint takes
            # images. Verified against the checkpoint's own config.json, not inferred from the name.
            supports_vision=True,
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
    def supports_vision(cls, model_key: str) -> bool:
        """
        Reports whether the model accepts image input alongside text.

        vLLM needs no telling -- it reads the modality off the checkpoint -- but API clients do.
        Onyx, for one, refuses an upload with "The current model does not support image input"
        unless the model entry it holds advertises the capability, so the fact has to travel from
        this registry into that client's configuration.

        Args:
            model_key (str): Short model alias, HF repo ID, or display name.

        Returns:
            bool: True when the checkpoint is vision-capable.
        """
        spec = cls.get_spec(model_key) if model_key else None
        return bool(spec and spec.supports_vision)

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
