#!/usr/bin/env python3
"""
Explicit Opt-In Caching & Dynamic Chunking ("God Mode")

This patch modifies three layers in vLLM:
1. API Layer: Adds `enable_cache` to `ChatCompletionRequest` and passes it via `extra_args`.
2. Scheduler Layer: Disables chunking if `enable_cache` is false.
3. Allocator Layer: Bypasses the LRU Prefix queue if `enable_cache` is false.
"""

import os
import sys

SENTINEL = "dreamference-opt-in-cache"

PROTOCOL_OLD_PROP = """    temperature: float | None = None
    top_p: float | None = None"""

PROTOCOL_NEW_PROP = f"""    temperature: float | None = None
    enable_cache: bool | None = True  # {SENTINEL}
    top_p: float | None = None"""

PROTOCOL_OLD_EXTRA = """        if self.kv_transfer_params:
            # Pass in kv_transfer_params via extra_args
            extra_args["kv_transfer_params"] = self.kv_transfer_params
        return SamplingParams.from_optional("""

PROTOCOL_NEW_EXTRA = f"""        if self.kv_transfer_params:
            # Pass in kv_transfer_params via extra_args
            extra_args["kv_transfer_params"] = self.kv_transfer_params
        _opt = getattr(self, "enable_cache", True)
        extra_args["enable_cache"] = True if _opt is None else _opt  # {SENTINEL}
        return SamplingParams.from_optional("""


SCHED_OLD = """            if (
                self._dreamference_ckpt_chunk_tokens is not None
                and num_computed_tokens < self._dreamference_dense_region_tokens
                and num_new_tokens > self._dreamference_ckpt_chunk_tokens
            ):"""

SCHED_NEW = f"""            # {SENTINEL}: dynamically disable chunking if the client opted out
            _opt_in = True
            if request.sampling_params and request.sampling_params.extra_args:
                _opt_in = request.sampling_params.extra_args.get("enable_cache", True)
            if (
                _opt_in
                and self._dreamference_ckpt_chunk_tokens is not None
                and num_computed_tokens < self._dreamference_dense_region_tokens
                and num_new_tokens > self._dreamference_ckpt_chunk_tokens
            ):"""


ALLOC_OLD = """        if self.enable_caching:
            self.coordinator.cache_blocks(request, num_computed_tokens)"""

ALLOC_NEW = f"""        # {SENTINEL}: instantly reclaim blocks if sequence opted out of caching
        _opt_in = True
        if request.sampling_params and request.sampling_params.extra_args:
            _opt_in = request.sampling_params.extra_args.get("enable_cache", True)
        if self.enable_caching and _opt_in:
            self.coordinator.cache_blocks(request, num_computed_tokens)"""


def apply_patch(target: str, old: str, new: str, name: str) -> bool:
    if not os.path.exists(target):
        print(f"[patch_opt_in_cache] WARNING: {target} not found. Skipping {name}.")
        return False
        
    with open(target, "r") as f:
        source = f.read()
        
    if new in source:
        print(f"[patch_opt_in_cache] {name} already applied to {target}")
        return True
        
    if source.count(old) != 1:
        print(f"[patch_opt_in_cache] ERROR: expected exactly one {name} anchor in {target}. "
              f"Found {source.count(old)}.", file=sys.stderr)
        return False
        
    with open(target, "w") as f:
        f.write(source.replace(old, new, 1))
    print(f"[patch_opt_in_cache] patched {target} ({name})")
    return True


def main() -> int:
    import vllm
    base = os.path.dirname(vllm.__file__)
    
    # Paths in vLLM 0.23.0
    protocol_path = os.path.join(base, "entrypoints", "openai", "chat_completion", "protocol.py")
    if not os.path.exists(protocol_path):
        protocol_path = os.path.join(base, "entrypoints", "openai", "protocol.py")
        
    sched_path = os.path.join(base, "v1", "core", "sched", "scheduler.py")
    alloc_path = os.path.join(base, "v1", "core", "kv_cache_manager.py")
    
    print(f"Applying God Mode opt-in caching patches to vLLM {vllm.__version__}...")
    success = True
    success &= apply_patch(protocol_path, PROTOCOL_OLD_PROP, PROTOCOL_NEW_PROP, "PROTOCOL_PROP")
    success &= apply_patch(protocol_path, PROTOCOL_OLD_EXTRA, PROTOCOL_NEW_EXTRA, "PROTOCOL_EXTRA")
    success &= apply_patch(sched_path, SCHED_OLD, SCHED_NEW, "SCHEDULER")
    success &= apply_patch(alloc_path, ALLOC_OLD, ALLOC_NEW, "ALLOCATOR")
    
    return 0 if success else 1

if __name__ == "__main__":
    raise SystemExit(main())
