#!/usr/bin/env python3
"""
Checkpoint-dense prefill chunking: materialise a mamba state at every block boundary.

Applied at image build time (see `Dockerfile.dense`), after `patch_mamba_chunk_align.py`, whose
rewritten splitter this extends — the anchor is that patch's output, so applying these out of
order fails loudly. The full story is `specs/DREAMFERENCE_PREFIX_CACHE.md`.

What remains broken after chunk-align + unify-downscale: a mamba 'align'-mode checkpoint exists
only where a prefill chunk *ends*. The kernel gets one running-state slot per scheduling step —
the block containing the chunk's last token — so with an 8192-token budget flooring to 6720, a
12k prompt checkpoints at 6720 and 11200 and nothing else. A later request sharing only the
first 5000 tokens finds no state at 2240 or 4480 and misses entirely, even though the
full-attention KV for those blocks is cached. Checkpoint density equals chunk size, and chunk
size was budget-driven.

The fix: cap aligned prefill chunks at N blocks (`DREAMFERENCE_CHECKPOINT_CHUNK_BLOCKS`,
default 1; 0 disables) — but only inside a **dense region** of the first
`DREAMFERENCE_CHECKPOINT_DENSE_BLOCKS` blocks (default 2, i.e. 4480 tokens; 0 disables the
region and with it the cap). Inside the region every block boundary becomes a chunk end and
receives an exact state — that is where shared prefixes (system prompts, personas, repo-map
heads) live. Beyond it, chunks run at the full grid-floored budget for prefill speed, and the
splitter's force-to-last_cache branch still checkpoints every prompt's final boundary, which
is all a same-conversation continuation needs. Measured on GB10 (same boot): capping every
chunk cost ~14% prefill (1,837 vs 2,137 tok/s on 10.5k); the dense-region form keeps the
short-prefix hits at ~full speed. Decode, the sub-block tail, and non-mamba models untouched.

Why this is affordable where 'all' mode is not: a mamba checkpoint block on this stack is
~157 MB (36 GDN layers x 4.59 MB page). 'all' mode would hold one per block per *running*
request — tripling per-token KV cost. Here each checkpoint is freed by
`remove_skipped_blocks` as the sequence advances and survives only as an *evictable*
hash-cached block in the LRU pool: dense checkpoints cost cache pressure, not live capacity.
Functionally this gives 'all'-mode hit behaviour (a state at every boundary of every prefix)
with align-mode memory economics — the difference is that checkpoints can be evicted under
pool pressure, which LRU resolves in recency order.

Known cost: smaller chunks pay more per-step overhead (the budget floor already cost −8% at
6720 vs 8192; N=1's 2240 chunks cost more — measured after deployment, and tunable back to
N=2 or N=4 through the env var with no rebuild). One step per block also means one ~157 MB
state-copy per step (previous slot -> new slot), ~0.6 ms at GB10 bandwidth — noise.
"""

import os
import sys

SENTINEL = "dreamference-checkpoint-chunks"

INIT_OLD = """        self.need_mamba_block_aligned_split = (
            self.has_mamba_layers and self.cache_config.mamba_cache_mode == "align"
        )"""

INIT_NEW = """        self.need_mamba_block_aligned_split = (
            self.has_mamba_layers and self.cache_config.mamba_cache_mode == "align"
        )
        # dreamference-checkpoint-chunks: cap aligned prefill chunks at N blocks so every N-th
        # block boundary ends a chunk and gets a mamba state checkpoint (the kernel writes one
        # running state per step, into the block containing the chunk's last token). 0 = no cap.
        import os as _dreamference_os

        _dreamference_ckpt_blocks = int(
            _dreamference_os.environ.get("DREAMFERENCE_CHECKPOINT_CHUNK_BLOCKS", "1")
        )
        self._dreamference_ckpt_chunk_tokens = (
            _dreamference_ckpt_blocks * self.block_size
            if _dreamference_ckpt_blocks > 0
            else None
        )
        # Dense region: the cap applies only below this many tokens; beyond it chunks run at
        # the full (grid-floored) budget, and the force-to-last_cache branch still guarantees
        # a checkpoint at every prompt's final boundary. Shared prefixes (system prompts,
        # personas) live early; deep continuations only need the last boundary. Measured on
        # GB10: capping everywhere costs ~14% prefill; dense-2-blocks + sparse recovers it.
        _dreamference_dense_blocks = int(
            _dreamference_os.environ.get("DREAMFERENCE_CHECKPOINT_DENSE_BLOCKS", "2")
        )
        self._dreamference_dense_region_tokens = (
            _dreamference_dense_blocks * self.block_size
            if _dreamference_dense_blocks > 0
            else 0
        )"""

SPLIT_OLD = """            num_computed_tokens_after_sched = num_computed_tokens + num_new_tokens"""

SPLIT_NEW = """            # dreamference-checkpoint-chunks: inside the dense region, end every chunk at
            # the next checkpoint boundary regardless of which branch below fires — the
            # remainder reschedules next step, checkpointing as it goes. Applied BEFORE the
            # branch arithmetic so the equality case (num_new_tokens == last_cache_position ==
            # budget; prompts in [8960, 11200) at the 8960 budget) cannot slip through
            # uncapped. In-region chunks are grid multiples, so the branches below are no-ops
            # on them; out-of-region scheduling is untouched.
            if (
                self._dreamference_ckpt_chunk_tokens is not None
                and num_computed_tokens < self._dreamference_dense_region_tokens
                and num_new_tokens > self._dreamference_ckpt_chunk_tokens
            ):
                num_new_tokens = self._dreamference_ckpt_chunk_tokens
            num_computed_tokens_after_sched = num_computed_tokens + num_new_tokens"""


def main() -> int:
    """
    Adds the checkpoint-density cap to the (already chunk-aligned) scheduler splitter.

    Returns:
        int: 0 when applied or already present; 1 when an anchor is missing — either this vLLM
            is not the one the patch was written against, or `patch_mamba_chunk_align.py` has
            not been applied first. The image must not ship as if it were fixed.
    """
    import vllm

    target = os.path.join(
        os.path.dirname(vllm.__file__), "v1", "core", "sched", "scheduler.py"
    )
    with open(target, "r") as handle:
        source = handle.read()

    if SENTINEL in source:
        print(f"[patch_mamba_checkpoint_chunks] already applied to {target}")
        return 0

    if "dreamference-mamba-chunk-align" not in source:
        print(
            "[patch_mamba_checkpoint_chunks] ERROR: patch_mamba_chunk_align.py must be "
            "applied first; refusing to patch an unaligned splitter.",
            file=sys.stderr,
        )
        return 1

    for name, anchor in (("init", INIT_OLD), ("split", SPLIT_OLD)):
        if source.count(anchor) != 1:
            print(
                f"[patch_mamba_checkpoint_chunks] ERROR: expected exactly one {name} anchor "
                f"in {target}, found {source.count(anchor)}. vLLM {vllm.__version__} does not "
                f"match what this patch was written against; refusing to guess.",
                file=sys.stderr,
            )
            return 1

    source = source.replace(INIT_OLD, INIT_NEW, 1).replace(SPLIT_OLD, SPLIT_NEW, 1)
    with open(target, "w") as handle:
        handle.write(source)

    print(f"[patch_mamba_checkpoint_chunks] patched {target} (vLLM {vllm.__version__})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
