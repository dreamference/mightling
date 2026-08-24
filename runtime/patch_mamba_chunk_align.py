#!/usr/bin/env python3
"""
Mamba-aligned chunk splitting for prefix caching on hybrid GDN targets with a DFlash drafter.

Applied at image build time (see `Dockerfile.dense`). Unlike `patch_kv_unify.py` and
`patch_prefix_align.py`, this one is not ported from the Entrpi recipe — it fixes a defect that
the combination of those two patches exposes, found on this machine on 2026-08-24 and written up
in `specs/DREAMFERENCE_PREFIX_CACHE.md`.

The invariant at stake: in mamba cache mode 'align', block *i* of the mamba group must hold the
recurrent state after exactly ``(i + 1) * block_size`` tokens, because that is what a prefix-cache
hit at block *i* restores. The GDN kernel is handed exactly one running-state slot per scheduling
step — ``(seq_len - 1) // block_size`` of a block table gathered down to the last slots — so the
invariant holds only when every prefill chunk ends on a mamba block boundary. The scheduler's
``_mamba_block_aligned_split`` exists to guarantee that, and it aligns chunks to
``cache_config.block_size``.

That is the wrong value once page-size unification has run. With the DFlash drafter, whose
attention page is ~2x the target's, ``unify_kv_cache_spec_page_size`` scales the *target's*
attention and mamba specs from 2240 to 4480 tokens per block while ``cache_config.block_size``
stays 2240. Chunks then end on odd 2240-multiples — mid mamba block — and the kernel's end-of-step
running state lands in a slot whose nominal boundary is up to 2240 tokens later. The block is
subsequently hash-cached under the *boundary's* hash, and a later request that hits it resumes
GDN layers from a state short of where the hash says it is. Live on 2026-08-24: a 12.3k-token
first turn chunked 6720/4480/tail and the follow-up turn hit 8960 — by the slot arithmetic that
restored state@6720 (the kernel's end-of-step write semantics are inferred from the gathered
-table contract, not directly observed; the spec's warm-vs-cold A/B confirms it either way).
Full-attention KV is cached correctly, so a stale state is a silent quality degradation, not a
crash.

The fix is one line: align the split to ``self.block_size``, the scheduler's *resolved* block
size — the LCM across KV cache groups, which the engine core computes with
``resolve_kv_cache_block_sizes`` and passes in. The LCM is a multiple of every group's block size
by construction, so chunk ends land on mamba boundaries in every configuration; it also equals
the coordinator's store-alignment and hit-gate grid, so every checkpoint this produces is both
correct and reachable. Wherever the group sizes agree (every configuration upstream vLLM lets
through today), ``self.block_size == cache_config.block_size`` and the patch is a no-op.

Side effect worth wanting: chunk ends now land on 4480-multiples, so *single-chunk-sized*
prompts checkpoint their last 4480 boundary too — a 7.6k-token prompt goes from caching nothing
in the mamba group (the measured all-zero-hits state) to serving a 4480-token hit on re-send.
"""

import os
import sys

SENTINEL = "dreamference-mamba-chunk-align"

OLD = """            block_size = self.cache_config.block_size
            last_cache_position = request.num_tokens - request.num_tokens % block_size"""

NEW = """            # dreamference-mamba-chunk-align: align chunks to the *resolved* scheduler block
            # size (the LCM across KV cache groups), not cache_config.block_size. Page-size
            # unification with the DFlash drafter scales the target's mamba block 2240 -> 4480
            # while cache_config.block_size stays 2240; chunks aligned to the smaller value end
            # mid mamba block, the kernel's running state lands in a slot whose nominal boundary
            # is later, and prefix caching then serves that stale state as the boundary state.
            # The LCM is a multiple of every group's block size, so this keeps the align-mode
            # invariant in all configurations and is identical to the old value whenever the
            # sizes agree.
            block_size = self.block_size
            last_cache_position = request.num_tokens - request.num_tokens % block_size"""


def main() -> int:
    """
    Rewrites the chunk-alignment granularity in vLLM's scheduler mamba split.

    Returns:
        int: 0 when applied or already present; 1 when the anchor is missing, which means this
            vLLM is not the one the patch was written against and the image must not ship as if
            it were fixed.
    """
    import vllm

    target = os.path.join(
        os.path.dirname(vllm.__file__), "v1", "core", "sched", "scheduler.py"
    )
    with open(target, "r") as handle:
        source = handle.read()

    if SENTINEL in source:
        print(f"[patch_mamba_chunk_align] already applied to {target}")
        return 0

    occurrences = source.count(OLD)
    if occurrences != 1:
        print(
            f"[patch_mamba_chunk_align] ERROR: expected exactly one anchor in {target}, found "
            f"{occurrences}. vLLM {vllm.__version__} does not match what this patch was written "
            f"against; refusing to guess.",
            file=sys.stderr,
        )
        return 1

    with open(target, "w") as handle:
        handle.write(source.replace(OLD, NEW, 1))

    print(f"[patch_mamba_chunk_align] patched {target} (vLLM {vllm.__version__})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
