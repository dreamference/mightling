#!/usr/bin/env python3
"""
Page-size unification by down-scaling: halve the prefix-cache grid on the DFlash stack.

Applied at image build time (see `Dockerfile.dense`), after `patch_kv_unify.py` (baked into the
kvfix2 base) whose up-scaling branch this supersedes when a cleaner option exists. Companion to
`patch_mamba_chunk_align.py`; the full story is `specs/DREAMFERENCE_PREFIX_CACHE.md` §6.1.

`unify_kv_cache_spec_page_size` reconciles unequal per-block page sizes across KV cache groups by
scaling the *smaller*-page specs' `block_size` up to the max page. On this stack the drafter's
per-token KV is exactly 2x the target's (8 kv-heads x 128 head-dim vs 2 x 256, both bf16), so the
drafter's page becomes the max and the target's attention and mamba blocks scale 2240 -> 4480 —
making 4480 the LCM that quantizes every prefix-cache store, hit, and mamba checkpoint. It also
leaves the mamba page padded to twice its natural size (its page is block-size-independent, so
the up-scale branch can only pad it), wasting half of every mamba block.

This patch adds a preferred path: when every larger-page spec is an attention spec whose block
size divides down *byte-exactly* to the minimum page (and stays a multiple of 16, the attention
backends' block granularity), scale those blocks DOWN instead. Here that means drafter
2240 -> 1120 with every group at the same ~4.59 MB page and blocks {2240, 2240, 1120}:

  * LCM (= scheduler_block_size) drops 4480 -> 2240: prefix-cache hit floor and grid halve,
    and mamba 'align' checkpoints double in density (with `patch_mamba_chunk_align` aligning
    chunks to the same value).
  * No capacity cost — scaling is byte-exact where padding is not, and the mamba page's 2x
    padding disappears, returning that waste to the pool.
  * Stride consistency is preserved (block_size x per-token bytes == page exactly), which is
    the invariant whose violation `patch_kv_unify.py` documents as collapsing DFlash acceptance.

Any spec that does not divide down cleanly (non-attention, already padded, non-integer ratio,
sub-16 result) falls back to the existing up-scale-and-pad behaviour unchanged. A runtime log
line marks each down-scaled layer group so a server log shows whether the path engaged.
"""

import os
import sys

SENTINEL = "dreamference-unify-downscale"

OLD = """    max_page_size = max(page_sizes)
    new_kv_cache_spec = {}"""

NEW = """    max_page_size = max(page_sizes)
    # dreamference-unify-downscale: prefer shrinking the larger-page attention specs to the
    # MINIMUM page over inflating everyone else to the maximum. Up-scaling multiplies the
    # LCM block size that quantizes prefix caching (4480 on the DFlash stack) and can only
    # *pad* block-size-independent mamba pages (wasting half of each). Down-scaling is taken
    # only when byte-exact for every layer; anything else falls through to the original path.
    _downscaled = _dreamference_try_downscale(kv_cache_spec, min(page_sizes))
    if _downscaled is not None:
        return _downscaled
    new_kv_cache_spec = {}"""

HELPER = '''

# ===================== dreamference-unify-downscale =====================
def _dreamference_try_downscale(kv_cache_spec, min_page_size):
    """Scale larger-page attention specs' block_size DOWN to the minimum page.

    Returns the new spec dict, or None when any layer cannot be down-scaled
    byte-exactly (non-attention spec, padded page, non-integer ratio, block not
    divisible, or a result that is not a multiple of 16 — the attention
    backends' block-size granularity).
    """
    from vllm.v1.kv_cache_interface import AttentionSpec
    from dataclasses import replace
    import logging

    new_spec = {}
    scaled = []
    for layer_name, layer in kv_cache_spec.items():
        if layer.page_size_bytes == min_page_size:
            new_spec[layer_name] = layer
            continue
        if not isinstance(layer, AttentionSpec) or layer.page_size_padded is not None:
            return None
        ratio, rem = divmod(layer.page_size_bytes, min_page_size)
        if rem or layer.block_size % ratio:
            return None
        new_block_size = layer.block_size // ratio
        if new_block_size % 16:
            return None
        candidate = replace(layer, block_size=new_block_size)
        if candidate.page_size_bytes != min_page_size:
            return None
        new_spec[layer_name] = candidate
        scaled.append((layer_name, layer.block_size, new_block_size))
    if scaled:
        logging.getLogger("vllm").info(
            "dreamference-unify-downscale: %d layer(s) block %d -> %d "
            "(unified page %d bytes; e.g. %s)",
            len(scaled), scaled[0][1], scaled[0][2], min_page_size, scaled[0][0],
        )
    return new_spec
# =================== end dreamference-unify-downscale ===================
'''


def main() -> int:
    """
    Adds the down-scaling path to vLLM's KV page-size unification.

    Returns:
        int: 0 when applied or already present; 1 when the anchor is missing, which means this
            vLLM is not the one the patch was written against and the image must not ship as if
            it were fixed.
    """
    import vllm

    target = os.path.join(os.path.dirname(vllm.__file__), "v1", "core", "kv_cache_utils.py")
    with open(target, "r") as handle:
        source = handle.read()

    if SENTINEL in source:
        print(f"[patch_unify_downscale] already applied to {target}")
        return 0

    occurrences = source.count(OLD)
    if occurrences != 1:
        print(
            f"[patch_unify_downscale] ERROR: expected exactly one anchor in {target}, found "
            f"{occurrences}. vLLM {vllm.__version__} does not match what this patch was written "
            f"against; refusing to guess.",
            file=sys.stderr,
        )
        return 1

    with open(target, "w") as handle:
        handle.write(source.replace(OLD, NEW, 1) + HELPER)

    print(f"[patch_unify_downscale] patched {target} (vLLM {vllm.__version__})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
