#!/usr/bin/env python3
"""
KV page-size unification fix for the DFlash drafter on GB10.

Applied at image build time to the vLLM inside `Dockerfile.dflash`. Ported from the
`patch_unify2.py` published with the DGX Spark DFlash recipe
(github.com/Entrpi/qwen3.5-122B-A10B-on-spark), which applies the same change at container start.

Why it is needed at all: serving Qwen3.5-122B-A10B with the z-lab DFlash drafter puts two KV cache
specs in front of vLLM at once — the target's hybrid GDN/mamba + attention pages, and the drafter's
own attention pages, which are about twice as large. `unify_kv_cache_spec_page_size` reconciles them
by scaling the smaller spec's `block_size` up by `max_page_size // layer_page_size`. That arithmetic
assumes `max_page_size` is a whole number of the layer's pages, and with a hybrid target it is not:
vLLM has already padded the mamba page to match the attention page, so the padded value becomes
`max_page_size`, the scaled page lands just under it, and the assert on the next line fires.

Measured on this machine on 2026-08-15, on both the project image (vLLM 0.24.0) and the recipe's own
`ghcr.io/aeon-7/aeon-vllm-ultimate` build (0.23.0+aeon.sm121a.dflash): all 72 GB of weights load,
then `determine_available_memory` -> `profile_cudagraph_memory` ->
`_init_minimal_kv_cache_for_profiling` -> `get_kv_cache_groups` dies on that assert. The 0.24 tree
grew an `elif` that pads instead of scaling, but it guards only the *non-divisible* case, while the
assert covers both branches — so the failure survives the upgrade.

The fix keeps the scaled `block_size` and pads the sub-1% remainder, mirroring what
`get_kv_cache_groups` already does for `HiddenStateCacheSpec` layers. Keeping the scaling is the
whole point: an earlier upstream attempt padded without it, which left the drafter reading its KV at
the wrong stride and dropped mean acceptance to ~1.47 — DFlash then costs more than it saves, and
nothing errors to say so.
"""

import os
import sys

SENTINEL = "dreamference-kv-unify"

OLD = """            new_spec = replace(layer_spec, block_size=new_block_size)
            assert new_spec.page_size_bytes == max_page_size"""

NEW = """            new_spec = replace(layer_spec, block_size=new_block_size)
            if new_spec.page_size_bytes != max_page_size:
                # dreamference-kv-unify: max_page_size is a *padded* hybrid page, so the scaled
                # page lands just under it. Pad the remainder while KEEPING the scaled block_size
                # -- dropping the scaling mis-strides the drafter's KV and collapses acceptance.
                new_spec = replace(
                    layer_spec,
                    block_size=new_block_size,
                    page_size_padded=max_page_size,
                )
            assert new_spec.page_size_bytes == max_page_size"""


def main() -> int:
    """
    Rewrites vLLM's page-size unification in place.

    Returns:
        int: 0 when the patch is applied or was already present; 1 when the anchor is missing,
            which means the vLLM in this image is not the one this patch was written against and
            the result must not be shipped as if it were fixed.
    """
    import vllm

    target = os.path.join(os.path.dirname(vllm.__file__), "v1", "core", "kv_cache_utils.py")
    with open(target, "r") as handle:
        source = handle.read()

    if SENTINEL in source:
        print(f"[patch_kv_unify] already applied to {target}")
        return 0

    occurrences = source.count(OLD)
    if occurrences != 1:
        print(
            f"[patch_kv_unify] ERROR: expected exactly one anchor in {target}, found "
            f"{occurrences}. vLLM {vllm.__version__} does not match what this patch was written "
            f"against; refusing to guess.",
            file=sys.stderr,
        )
        return 1

    with open(target, "w") as handle:
        handle.write(source.replace(OLD, NEW, 1))

    print(f"[patch_kv_unify] patched {target} (vLLM {vllm.__version__})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
