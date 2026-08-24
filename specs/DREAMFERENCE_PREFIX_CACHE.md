# Prefix Caching on the Hybrid GDN + DFlash Stack — Findings and Patch Design

**Status:** Proposed (Code is currently on `dense1` with `enable_prefix_caching: True` for both models)

**Date:** 2026-08-24
**Patch:** `runtime/patch_mamba_chunk_align.py`
**Registry context:** `qwen3.5-122b-a10b-hybrid-dflash` / `qwen3.5-122b-a10b-int4-dflash`, images `dreamference-vllm-dflash:0.23.0-aeon-dense1` and `dreamference-vllm-dflash:0.23.0-aeon-kvfix2`

---

## 1. The two defects

Both were found by driving the real `KVCacheManager`/`HybridKVCacheCoordinator` offline inside the pinned image (pure bookkeeping, no GPU) and then confirming against the live server.

### 1.1 Zero hits by design (upstream `align`-mode sparsity)

In mamba cache mode `align` — forced for Qwen3.5's GDN layers, which lack `SupportsMambaPrefixCaching` — the GDN kernel receives exactly **one running-state slot per scheduling step**: `mamba_get_block_table_tensor` gathers the block table down to `(seq_len - 1) // block_size`. Every earlier position in the mamba group's block table is the null block, `cache_full_blocks` skips null blocks, and the hybrid coordinator's `get_cached_block` demands a hit in **every** KV group. Consequence: a prompt that prefills in one chunk stores *nothing* reusable in the mamba group, and any identical re-send misses in that group, which zeroes the whole intersection. This is the measured behaviour: 7.6k-token identical re-sends hit 0 in every configuration ever tested on this stack.

Hits only exist where a prefill chunk *ended* on a reusable boundary. Store and lookup are both quantized to `scheduler_block_size` — the LCM across groups, **4480** here, because page-size unification (`patch_kv_unify.py`) scales the target's attention and mamba blocks 2240 → 4480 to match the DFlash drafter's ~2x page.

### 1.2 Wrong-state hits (introduced by the unify + prefix-align combination)

`Scheduler._mamba_block_aligned_split` exists to make chunk ends land on mamba block boundaries, but it aligns to `cache_config.block_size` (**2240**) — not the unified mamba block (**4480**). A chunk ending at an odd 2240-multiple ends *mid* mamba block: the kernel writes its end-of-step state into slot `(end-1)//4480`, whose nominal boundary is up to 2240 tokens later, and `cache_blocks` then hashes that block under the *boundary's* token hash.

Live demonstration (2026-08-24, exact production config): a 12,323-token first turn chunked as 6720 / 4480 / tail (the splitter's 2240 grid), and the follow-up turn hit 8960 tokens — the first nonzero hit ever observed on this stack. By the slot-write arithmetic, that hit restored **state@6720 from the slot labelled 8960** — the kernel's end-of-step write semantics are inferred from the gathered-table contract (`mamba_get_block_table_tensor`'s docstring and the state-migration comment in `remove_skipped_blocks`), not directly observed. The hit's existence alone does not discriminate, and a fact-recall probe was inconclusive because full-attention KV (cached correctly) covers the gap — so if the state is stale, the damage is a silent degradation of the GDN layers' contribution in long-context multi-turn sessions, not a visible failure. The patch is correct under either resolution: aligning chunks to the LCM is required for the invariant and also unlocks the §2 hit-rate gains. Upstream vLLM never reaches this state: `resolve_kv_cache_block_sizes` refuses mamba blocks that diverge from `cache_config.block_size`, and it is our `patch_prefix_align.py` (ported from the Entrpi recipe) that lets the configuration through. The splitter bug is latent upstream; the enablement made it live.

---

## 2. The patch

**One line, scheduler-side** (`runtime/patch_mamba_chunk_align.py`, sentinel `dreamference-mamba-chunk-align`): `_mamba_block_aligned_split` aligns to `self.block_size` — the scheduler's *resolved* block size, which the engine core computes as the LCM across KV groups and passes in — instead of `cache_config.block_size`.

Why the LCM is the right value in every configuration:

- It is a multiple of every group's block size by construction, so chunk ends always land on mamba block boundaries → the align-mode invariant (slot *i* holds the state at `(i+1)*block_size`) holds, fixing §1.2.
- It equals the coordinator's store-alignment and hit-gate grid, so every checkpoint the splitter forces is also *cacheable and hittable* — with the 2240 splitter, states at odd 2240-multiples (e.g. 6720, 11200) were unreachable even when correct.
- Wherever group sizes agree — every configuration upstream lets through today — `self.block_size == cache_config.block_size` and the patch is a no-op.

### 2.1 Chunk-size economics (no registry change required)

The current budgets (`max_num_batched_tokens` 8248 for the hybrid, 8213 for int4) floor to one 4480-token chunk per step under the patch. That doubles the step count of a long prefill but maximizes checkpoint density (every 4480 boundary). The alternative — raising the budget so chunks land at exactly 8960 (two blocks) — restores per-step efficiency at half the checkpoint density, moves the torch.compile range endpoint (one cold compile), and grows activation reserve slightly against the 0.68 gpu-mem headroom. **Recommendation: ship the patch with the budget unchanged, measure prefill throughput, and only then decide** — prefill on this box is compute-bound MoE work where a 4480-token batch is still large.

---

## 3. Deployment Plan

1. Append to `Dockerfile.dense` (after the existing three patch RUNs):
   `COPY runtime/patch_mamba_chunk_align.py /tmp/` + `RUN python3 /tmp/patch_mamba_chunk_align.py`
   (the script exits 1 and fails the build if the anchor is missing).
2. Build as `dreamference-vllm-dflash:0.23.0-aeon-dense2` (base layers cached; the build is patch-layers only).
3. Point the default (hybrid) entry's `docker_image` at `dense2`, restart via `dream server`.
   The torch.compile cache is keyed off traced sources of the *model*, not the scheduler, and the scheduler is host-process Python — expect a warm compile cache.
4. **Housekeeping:** The `int4-dflash` fallback currently runs on `kvfix2` with `enable_prefix_caching` set to `True` in the registry. Because it lacks the new alignment patches, it ships the §1.2 stale-hit hazard. We must either bake a `kvfix3` with `patch_mamba_chunk_align` or set its `enable_prefix_caching` to `False` in the registry.

---

## 4. What this does not fix (design floor)

- No hits below 4480 shared tokens, and hits remain quantized to the 4480 grid — both follow from one-state-slot-per-step `align` mode plus the LCM. Finer grids are not reachable by configuration: shrinking the grid to 2240 would require un-scaling the target's blocks, which either re-triggers the startup assert `patch_kv_unify.py` exists to fix or doubles attention KV bytes via padding.
- The real fix is upstream: `SupportsMambaPrefixCaching` ('all' mode) for GDN, which materializes every block's state at real memory cost. Out of scope here.

---

## 5. Further patch options (roadmap)

Ranked by value-per-risk on this stack. Geometry fact underpinning #1: the drafter's per-token KV is exactly 2x the target's (8 kv-heads x 128 head-dim = 4096 B vs 2 x 256 = 2048 B, both bf16) — that integer ratio is the whole reason the grid is 4480.

1. **Halve the grid by scaling the drafter's block DOWN (2240 → 1120).** `unify_kv_cache_spec_page_size` only scales smaller-page specs *up* to the max page; with the platform block at 2240 the drafter's page (9.2 MB) becomes the max and the target's attn+mamba scale to 4480. A patch could instead scale the *larger*-page spec's block down when the ratio divides evenly: drafter 2240 → 1120 puts every group at ~4.59 MB pages with blocks {2240, 2240, 1120} → LCM = **2240**. Hit floor and grid halve, checkpoint density doubles, and there is **no capacity cost**.
2. **Stop freeing hash-cached checkpoint blocks mid-request.** `MambaManager.remove_skipped_blocks` frees the previous state block as the sequence advances; freed blocks keep their hash only until the free queue recycles them, which is what makes hits opportunistic. Skipping the free when `block.block_hash is not None` keeps checkpoints alive until normal request teardown.
3. **Recycle-event logging** (one debug line where the pool evicts a hashed block on reuse) — near-zero risk.
4. **Drafter-only fp8 KV** — the other route to a 2240 grid (4096 → 2048 B/token), with a capacity *gain*.
5. **`all`-mode mamba caching for GDN** (the complete fix: every block's state materialised, hits guaranteed rather than opportunistic, no dependence on chunk history). Requires the FLA GDN kernel to write per-block states — it has no `all`-mode machinery today.

---

## 6. Upstream reporting

Two reports worth filing, both with the offline repro:

- **vLLM:** `_mamba_block_aligned_split` uses `cache_config.block_size` where the resolved scheduler block size is meant; latent today (resolve refuses divergent mamba blocks) but wrong the moment that guard is relaxed — as two published DFlash recipes already do.
- **Entrpi (`qwen3.5-122B-A10B-on-spark`):** their `patch_prefix_align.py` + `patch_unify2.py` combination ships the stale-state hit on every deployment; `patch_mamba_chunk_align.py` is the companion fix.
