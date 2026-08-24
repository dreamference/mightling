# Prefix Caching on the Hybrid GDN + DFlash Stack — Findings and Patch Design

**Status:** v4 — Deployed (`patch_mamba_chunk_align` → dense2, `patch_unify_downscale` →
dense3, `patch_mamba_checkpoint_chunks` → `dreamference-vllm-dflash:0.23.0-aeon-dense4`;
default entry on dense4 since 2026-08-24; the int4-dflash fallback now runs with prefix
caching OFF on its untouched kvfix2 image. Verification results in §4/§4.1)
**Date:** 2026-08-24
**Patch:** `runtime/patch_mamba_chunk_align.py`
**Registry context:** `qwen3.5-122b-a10b-hybrid-dflash` / `qwen3.5-122b-a10b-int4-dflash`, image
`dreamference-vllm-dflash:0.23.0-aeon-dense1` (vLLM `0.23.0+aeon.sm121a.dflash`)

---

## 1. The two defects

Both were found by driving the real `KVCacheManager`/`HybridKVCacheCoordinator` offline inside the
pinned image (pure bookkeeping, no GPU) and then confirming against the live server.

### 1.1 Zero hits by design (upstream `align`-mode sparsity)

In mamba cache mode `align` — forced for Qwen3.5's GDN layers, which lack
`SupportsMambaPrefixCaching` — the GDN kernel receives exactly **one running-state slot per
scheduling step**: `mamba_get_block_table_tensor` gathers the block table down to
`(seq_len - 1) // block_size`. Every earlier position in the mamba group's block table is the
null block, `cache_full_blocks` skips null blocks, and the hybrid coordinator's
`get_cached_block` demands a hit in **every** KV group. Consequence: a prompt that prefills in
one chunk stores *nothing* reusable in the mamba group, and any identical re-send misses in that
group, which zeroes the whole intersection. This is the measured behaviour: 7.6k-token identical
re-sends hit 0 in every configuration ever tested on this stack.

Hits only exist where a prefill chunk *ended* on a reusable boundary. Store and lookup are both
quantized to `scheduler_block_size` — the LCM across groups, **4480** here, because page-size
unification (`patch_kv_unify.py`) scales the target's attention and mamba blocks 2240 → 4480 to
match the DFlash drafter's ~2x page.

### 1.2 Wrong-state hits (introduced by the unify + prefix-align combination)

`Scheduler._mamba_block_aligned_split` exists to make chunk ends land on mamba block boundaries,
but it aligns to `cache_config.block_size` (**2240**) — not the unified mamba block (**4480**).
A chunk ending at an odd 2240-multiple ends *mid* mamba block: the kernel writes its end-of-step
state into slot `(end-1)//4480`, whose nominal boundary is up to 2240 tokens later, and
`cache_blocks` then hashes that block under the *boundary's* token hash.

Live demonstration (2026-08-24, exact production config): a 12,323-token first turn chunked as
6720 / 4480 / tail (the splitter's 2240 grid), and the follow-up turn hit 8960 tokens — the
first nonzero hit ever observed on this stack. By the slot-write arithmetic, that hit restored
**state@6720 from the slot labelled 8960** — the kernel's end-of-step write semantics are
inferred from the gathered-table contract (`mamba_get_block_table_tensor`'s docstring and the
state-migration comment in `remove_skipped_blocks`), not directly observed; the §4 warm-vs-cold
A/B is what confirms it either way. The hit's existence alone does not discriminate, and a
fact-recall probe was inconclusive because full-attention KV (cached correctly) covers the gap —
so if the state is stale, the damage is a silent degradation of the GDN layers' contribution in
long-context multi-turn sessions, not a visible failure. The patch is correct under either
resolution: aligning chunks to the LCM is required for the invariant and also unlocks the §2
hit-rate gains. Upstream vLLM never reaches this state:
`resolve_kv_cache_block_sizes` refuses mamba blocks that diverge from `cache_config.block_size`,
and it is our `patch_prefix_align.py` (ported from the Entrpi recipe) that lets the
configuration through. The splitter bug is latent upstream; the enablement made it live.

---

## 2. The patch

**One line, scheduler-side** (`runtime/patch_mamba_chunk_align.py`, sentinel
`dreamference-mamba-chunk-align`): `_mamba_block_aligned_split` aligns to `self.block_size` — the
scheduler's *resolved* block size, which the engine core computes as the LCM across KV groups and
passes in — instead of `cache_config.block_size`.

Why the LCM is the right value in every configuration:

- It is a multiple of every group's block size by construction, so chunk ends always land on
  mamba block boundaries → the align-mode invariant (slot *i* holds the state at
  `(i+1)*block_size`) holds, fixing §1.2.
- It equals the coordinator's store-alignment and hit-gate grid, so every checkpoint the splitter
  forces is also *cacheable and hittable* — with the 2240 splitter, states at odd 2240-multiples
  (e.g. 6720, 11200) were unreachable even when correct.
- Wherever group sizes agree — every configuration upstream lets through today —
  `self.block_size == cache_config.block_size` and the patch is a no-op.

Expected behaviour change on this stack (derived from the split logic, to be verified per §4):

| Traffic shape | Before | After |
| --- | --- | --- |
| Identical re-send, 7.6k prompt | 0 | **4480** (chunk forced to end at 4480; state cached, correct) |
| Second turn over 12.3k first turn | 8960 with **stale** GDN state | 8960 with **correct** state |
| Anything sharing < 4480 tokens | 0 | 0 (design floor, see §5) |

### 2.1 Chunk-size economics (no registry change required)

The current budget (`max_num_batched_tokens` 8280, minus 88 draft-token slots → 8192 for prefill)
floors to one 4480-token chunk per step under the patch. That doubles the step count of a long
prefill but maximizes checkpoint density (every 4480 boundary). The alternative — raising the
budget to 9048 so chunks land at exactly 8960 (two blocks) — restores per-step efficiency at half
the checkpoint density, moves the torch.compile range endpoint (one cold compile), and grows
activation reserve slightly against the 0.72 gpu-mem headroom. **Recommendation: ship the patch
with the budget unchanged, measure prefill throughput, and only then decide** — prefill on this
box is compute-bound MoE work where a 4480-token batch is still large.

---

## 3. Deployment

1. Append to `Dockerfile.dense` (after the existing three patch RUNs):
   `COPY runtime/patch_mamba_chunk_align.py /tmp/` + `RUN python3 /tmp/patch_mamba_chunk_align.py`
   (the script exits 1 and fails the build if the anchor is missing).
2. Build as `dreamference-vllm-dflash:0.23.0-aeon-dense2` (base layers cached; the build is
   patch-layers only).
3. Point the default (hybrid) entry's `docker_image` at `dense2`, restart via `dream server`.
   The torch.compile cache is keyed off traced sources of the *model*, not the scheduler, and the
   scheduler is host-process Python — expect a warm compile cache. **Deviation from v1, which
   said "both entries":** the `int4-dflash` fallback deliberately stays on `kvfix2` — its whole
   value is being the untouched known-good configuration — and therefore retains the §1.2
   stale-hit hazard; its registry comment says so.
4. Done 2026-08-24: `dense2` built (patch layer only), default entry repointed, server
   relaunched.

## 4. Verification — results (2026-08-24; dense2 then dense3 live)

| Probe | dense1 (pre-patch) | dense2 (chunk-align) | dense3 (+ downscale, grid 2240) |
| --- | --- | --- | --- |
| Identical 7.4k re-send | 0, every trial | **4480** | **6720** (warm wall 1.65s) |
| Two-turn over 12.2k first turn | 8960 with stale state | **8960**, correct state | **11200** (warm 4.2s vs cold 10.0s) |
| Warm vs cold, greedy, identical input | — | byte-identical | factually identical; see note |
| Cold prefill, 10.5k prompt | 1,973 tok/s | 1,929 (−2.2%) | 1,812 (−8% vs dense1) |
| KV pool (boot-dependent memory) | 12.9 GiB / 71.8k tok | 13.3 GiB / 73.5k | **12.6 GiB / 111.7k** (+52% tokens on fewer bytes — the mamba 2x padding reclaimed) |

The downscale engaged as designed — server log: `dreamference-unify-downscale: 6 layer(s)
block 2240 -> 1120 (unified page 4587520 bytes)`, the six drafter attention layers, page equal
to the offline prediction to the byte.

**Warm/cold note (dense3):** answers diverge at the token level but are factually identical —
on a varied 11.6k-token ledger, warm (hit 11200) and cold both recalled Record 0 verbatim and
the correct decade span, differing only in phrasing ("1950 to 1999" vs "the 1950s through the
1990s"). Different prefill chunkings produce ulp-level logit differences that flip greedy
near-ties; dense2's byte-identical result was the lucky case, not the guarantee. The
structural argument is the strong one: with splitter grid, mamba block, and LCM all equal
(2240), a chunk can no longer end mid mamba block, so the §1.2 staleness mechanism cannot
arise by construction.

**Prefill cost:** the 8192-token budget floors to 6720-token chunks on the 2240 grid (was 8192
whole on dense1). Recoverable by raising `max_num_batched_tokens` so the post-draft-slot
budget lands on a 2240 multiple (e.g. 8960 + 88 slots = 9048), at the cost of one cold
compile; not taken — 8% is tolerable and the KV-pool and hit-rate wins dominate.

**Launch note:** the dense3 relaunch was refused twice by the host-safety pre-flight (desktop
~2 GB fatter than at the morning launches; at 0.72 the arena + 10.9 GB load peak missed
available memory by 1.3 GB, at 0.71 by 0.13 GB while the desktop drifted between checks).
`gpu_memory_utilization` is now 0.70 in the registry — ~6.5 GiB KV floor headroom retained,
worth raising back when the desktop is lighter; the registry comment records the arithmetic.

**Caveat — hits are opportunistic, not guaranteed.** The first post-boot matrix run's two-turn
probe hit 0, **unexplained**: two identical follow-up sequences (including one reproducing the
full matrix shape) both hit 8960. Candidate accounts, neither confirmed: (a) recycling of a
freed checkpoint block — align mode frees the previous state block mid-request
(`remove_skipped_blocks`), and a freed block keeps its hash only until the free queue reuses
it — though the LRU queue consumes never-allocated blocks first and the pool was ~80% untouched
at the time, which makes this strained; (b) some first-minute-post-boot engine state (the run
started ~1 minute after health, right behind the 43s lazy-warmup request). Rare,
boot-adjacent, and the fix direction would not change either way; noted for re-runs rather
than chased. Structurally, checkpoints *are* freeable mid-request, so under genuine pool
pressure hits can be lost — inherent to `align` mode's one-live-state design, not introduced
by the patch, which only created checkpoints where there were none.

### 4.1 dense4 — checkpoint-dense chunking (`patch_mamba_checkpoint_chunks`)

The last structural gap: a checkpoint existed only where a budget-sized chunk ended, so a
prefix shared up to e.g. 5k tokens missed even though its full-attention KV was cached. dense4
caps aligned prefill chunks at `DREAMFERENCE_CHECKPOINT_CHUNK_BLOCKS` blocks (default 1), so
**every 2240 boundary of every prefill gets an exact mamba state** — 'all'-mode hit behaviour
at align-mode memory cost, because checkpoints remain *evictable* freed-but-hashed blocks
(a held mamba block is ~157 MB × 36 GDN layers — the reason real 'all' mode is unaffordable).

Measured (2026-08-24, dense4 live, util 0.70):

| Probe | dense3 | dense4 |
| --- | --- | --- |
| Identical 7.4k re-send | 6720 | **6720** ✓ |
| Divergent prompt sharing ~4.9k tokens | **0** (no checkpoint below 6720) | **4480** ✓ |
| Two-turn over 12.2k | 11200 | **11200** ✓ |
| Cold prefill, 10.5k (2240-token chunks) | 1,812 tok/s | 1,789 (−1.3% — the density is nearly free) |
| Prose decode | 23.5 tok/s | 23.8 ✓ |

Pack efficiency held: 8,845 tokens/GiB vs dense2's 5,536 (+60%). This boot's pool is small in
absolute terms (5.84 GiB / 51.7k tokens) only because util went 0.72 → 0.70 while the desktop
sat ~2 GB heavier; raising util back when the desktop lightens restores ~9k tokens per 0.01.

**Boot-adjacent anomaly, second occurrence.** The first post-boot matrix run again produced
one impossible miss (the divergent probe read 0; the identical logic re-run minutes later hit
4480 twice). Same signature as the dense2-era zero: within ~90s of engine health, and this
time correlated with an add→abort request storm every ~2s — Onyx re-establishing its provider
connection after the restart. Steady-state behaviour is verified correct; treat first-minute
measurements after a restart as unreliable, and take any production zero-hit report with a
restart timestamp check before reopening this file.

### Original plan (retained for re-runs)

- **Re-send probe** (exists: `scratchpad` probes from the investigation): 7.6k identical re-send —
  expect `prefix_cache_hits` delta 4480, was 0.
- **Correctness probe:** two-turn 12.3k conversation, warm vs. cold (cold = fresh server or
  distinct leading token), greedy. With the patch, the 8960 hit restores the state the same
  prefill wrote at a true boundary; warm and cold answers should agree modulo chunking numerics.
  Sharper variant if needed: compare `prompt_logprobs` over a fixed continuation.
- **No-regression:** full test suite (patch is in-image; repo tests unaffected), plus a
  single-stream benchmark to confirm prefill throughput with 4480-token chunks (§2.1).
- **Offline invariant check:** the investigation's offline harness can assert, post-patch, that
  every chunk end the splitter produces is `% 4480 == 0` for a sweep of prompt lengths.

## 5. What this does not fix (design floor)

- No hits below 4480 shared tokens, and hits remain quantized to the 4480 grid — both follow from
  one-state-slot-per-step `align` mode plus the LCM. Finer grids are not reachable by
  configuration: shrinking the grid to 2240 would require un-scaling the target's blocks, which
  either re-triggers the startup assert `patch_kv_unify.py` exists to fix or doubles attention
  KV bytes via padding.
- The real fix is upstream: `SupportsMambaPrefixCaching` ('all' mode) for GDN, which materializes
  every block's state at real memory cost. Out of scope here.

## 6. Further patch options (roadmap, 2026-08-24)

Ranked by value-per-risk on this stack. Geometry fact underpinning #1: the drafter's per-token
KV is exactly 2x the target's (8 kv-heads x 128 head-dim = 4096 B vs 2 x 256 = 2048 B, both
bf16) — that integer ratio is the whole reason the grid is 4480.

1. **Halve the grid by scaling the drafter's block DOWN (2240 → 1120).** *Implemented
   2026-08-24 as `runtime/patch_unify_downscale.py`, baked into dense3 — §4 has the measured
   results, including the unplanned +52% KV pool from reclaiming the mamba padding.*
   `unify_kv_cache_spec_page_size` only scales smaller-page specs *up* to the max page; with
   the platform block at 2240 the drafter's page (9.2 MB) becomes the max and the target's
   attn+mamba scale to 4480. A patch in the same function `patch_kv_unify.py` already rewrites
   could instead scale the *larger*-page spec's block down when the ratio divides evenly:
   drafter 2240 → 1120 puts every group at ~4.59 MB pages with blocks {2240, 2240, 1120} →
   LCM = **2240**. Hit floor and grid halve, checkpoint density doubles, and — unlike padding —
   there is **no capacity cost**: scaling is byte-exact (the 2.0 ratio makes the pages equal to
   the byte). FA has no problem with block 1120 (multiple of 16), and stride consistency is
   preserved (the acceptance collapse `patch_kv_unify.py` warns about came from padding
   *without* scaling). Verification: the §4 matrix, expecting hits at 2240·k.
2. **Stop freeing hash-cached checkpoint blocks mid-request.** `MambaManager.
   remove_skipped_blocks` frees the previous state block as the sequence advances; freed
   blocks keep their hash only until the free queue recycles them, which is what makes hits
   opportunistic. Skipping the free when `block.block_hash is not None` keeps checkpoints
   alive until normal request teardown. Trade: a 32k sequence holds up to ~7 extra mamba
   blocks until it finishes, and under real pool pressure holding memory can cause the
   preemption the free was avoiding — a wash there, a win in the mid-pressure band. Pair with:
3. **Recycle-event logging** (one debug line where the pool evicts a hashed block on reuse) —
   would have settled the §4 unexplained zero in one glance; near-zero risk.
4. **Drafter-only fp8 KV** — the other route to a 2240 grid (4096 → 2048 B/token), with a
   capacity *gain*; the FA backend has the fp8-KV plumbing, but sm121 kernel support needs a
   live probe and quantized drafter KV risks DFlash acceptance. Superseded by #1 unless the
   capacity gain is wanted for its own sake.
5. **`all`-mode mamba caching for GDN** (the complete fix: every block's state materialised,
   hits guaranteed rather than opportunistic, no dependence on chunk history). Requires the
   FLA GDN kernel to write per-block states — it has no `all`-mode machinery today (verified)
   — plus `SupportsMambaPrefixCaching` on the model class, at roughly −30% effective KV
   capacity (mamba blocks become prompt-proportional). Upstream-grade work.

Rejected on the numbers: padding target pages up to the drafter's at block 2240 (halves the
token pool — scaling is waste-free, padding is not); partial-group hits with GDN-state replay
(engine surgery upstream doesn't have either). Housekeeping regardless of the above: the
`int4-dflash` fallback still ships the §1.2 stale-hit hazard on kvfix2 — either bake a
`kvfix3` with `patch_mamba_chunk_align` or set its `enable_prefix_caching` to False.

## 7. Upstream reporting

Two reports worth filing, both with the offline repro:

- **vLLM:** `_mamba_block_aligned_split` uses `cache_config.block_size` where the resolved
  scheduler block size is meant; latent today (resolve refuses divergent mamba blocks) but wrong
  the moment that guard is relaxed — as two published DFlash recipes already do.
- **Entrpi (`qwen3.5-122B-A10B-on-spark`):** their `patch_prefix_align.py` + `patch_unify2.py`
  combination ships the stale-state hit on every deployment; `patch_mamba_chunk_align.py` is the
  companion fix.
