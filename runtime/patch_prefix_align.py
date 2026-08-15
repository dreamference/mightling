#!/usr/bin/env python3
"""
Prefix-caching fix for the DFlash drafter on a hybrid GDN/mamba target.

Applied at image build time by `Dockerfile.dflash`. Ported from `patch_prefix_align.py` in the DGX
Spark DFlash recipe (github.com/Entrpi/qwen3.5-122B-A10B-on-spark), which applies it at container
start, and generalised — see below.

The problem: with the DFlash drafter in front of this target there are two KV cache groups whose
block sizes differ, because the drafter's attention page is about twice the target's. Page-size
unification scales the target's mamba + attention block up to match, which leaves that block
different from `cache_config.block_size`. `resolve_kv_cache_block_sizes` treats that difference as
a signal that mamba is not aligned and backs off to the scheduler block size, forcing
`hash_block_size` to the LCM of the groups. The drafter's group does not divide the LCM, so
`HybridKVCacheCoordinator` aborts at startup. It is a failed launch, not degraded output.

The back-off is only meant to disable fine-grained hashing when mamba is genuinely unaligned. Its
test — `block_size != cache_config.block_size` — is a proxy that also fires when an aligned mamba
block was merely scaled up by unification. Keying on the actual cache mode instead lets the GCD
path run, and the GCD divides every group by construction, which is exactly the finer hash
granularity `hash_block_size` exists to express.

Generalised from upstream in one way that matters. Upstream skips the back-off only for
`mamba_cache_mode == "align"`, but vLLM picks the mode itself when prefix caching is turned on:
`all` when the model supports mamba prefix caching, `align` otherwise (see
`model_executor/models/config.py`). Both are aligned modes; only `none` means unaligned, and `none`
is what vLLM sets when prefix caching is off. Keying on `!= "none"` therefore covers the case
upstream's version would silently fail to patch.
"""

import os
import sys

SENTINEL = "dreamference-prefix-align"

OLD = """    if any(
        isinstance(g.kv_cache_spec, MambaSpec)
        and g.kv_cache_spec.block_size != cache_config.block_size
        for g in groups
    ):
        return scheduler_block_size, scheduler_block_size"""

NEW = """    # dreamference-prefix-align: back off only when mamba is genuinely unaligned. A mamba group
    # whose block_size differs from cache_config.block_size while the cache mode is 'align' or
    # 'all' just means page-size unification scaled it up to match a larger drafter page; the GCD
    # below still divides every group, so fine-grained hashing stays valid. Upstream's proxy test
    # tripped this branch for the hybrid + DFlash + prefix-caching case, forcing
    # hash_block_size to the LCM and breaking HybridKVCacheCoordinator's divisibility assert.
    if getattr(cache_config, "mamba_cache_mode", "none") == "none" and any(
        isinstance(g.kv_cache_spec, MambaSpec)
        and g.kv_cache_spec.block_size != cache_config.block_size
        for g in groups
    ):
        return scheduler_block_size, scheduler_block_size"""


def main() -> int:
    """
    Rewrites the mamba back-off in vLLM's KV cache block-size resolution.

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
        print(f"[patch_prefix_align] already applied to {target}")
        return 0

    occurrences = source.count(OLD)
    if occurrences != 1:
        print(
            f"[patch_prefix_align] ERROR: expected exactly one anchor in {target}, found "
            f"{occurrences}. vLLM {vllm.__version__} does not match what this patch was written "
            f"against; refusing to guess.",
            file=sys.stderr,
        )
        return 1

    with open(target, "w") as handle:
        handle.write(source.replace(OLD, NEW, 1))

    print(f"[patch_prefix_align] patched {target} (vLLM {vllm.__version__})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
