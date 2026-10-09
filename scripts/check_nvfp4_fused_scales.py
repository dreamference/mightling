"""Checks that an NVFP4 checkpoint's fused projection groups share one global scale.

SGLang (and vLLM) load a Qwen3.5/3.8 layer's ``in_proj_qkv`` and ``in_proj_z``, ``in_proj_b`` and
``in_proj_a``, ``q/k/v_proj`` and ``gate/up_proj`` into one fused GEMM each, and a fused NVFP4 GEMM
has one global weight scale: SGLang's ``CompressedTensorsW4A4Fp4`` takes the largest of the group's
scales and does not rescale the block scales. A checkpoint calibrated per module therefore serves
with mis-scaled weights, silently: arXiv 2609.04098 found the DeltaNet decay and write gates off by
1.82x and 2.75x, with long-context perplexity looking *better* for it. A checkpoint is safe to serve
only when every group's scales are equal, which this script checks from the safetensors headers and
the scale tensors alone (a few kilobytes read; no torch, no GPU).

    python3 scripts/check_nvfp4_fused_scales.py <snapshot directory>

Exit 0 when every group is harmonized, 1 when one is not, 2 when the checkpoint cannot be read or
has no compressed-tensors NVFP4 scales. specs/DREAMFERENCE_MODELS.md §2.2.
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path
from typing import Dict, Final, List, Tuple

# The fused groups of SGLang's Qwen3_5ForCausalLM (packed_modules_mapping in models/qwen3_5.py).
GROUPS: Final[Tuple[Tuple[str, ...], ...]] = (
    ("linear_attn.in_proj_qkv", "linear_attn.in_proj_z"),
    ("linear_attn.in_proj_b", "linear_attn.in_proj_a"),
    ("self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj"),
    ("mlp.gate_proj", "mlp.up_proj"),
)
SCALES: Final[Tuple[str, ...]] = ("weight_global_scale", "input_global_scale")
# Equal means equal as stored; a relative tolerance only absorbs a float32 round trip.
TOLERANCE: Final[float] = 1e-6


def read_scalars(snapshot: Path) -> Dict[str, float]:
    """Every scalar F32 tensor named ``*_global_scale`` in the snapshot's safetensors files."""
    values: Dict[str, float] = {}
    for shard in sorted(snapshot.glob("*.safetensors")):
        with shard.open("rb") as handle:
            size = struct.unpack("<Q", handle.read(8))[0]
            header = json.loads(handle.read(size))
            base = 8 + size
            for name, meta in header.items():
                if name == "__metadata__" or not name.endswith(SCALES):
                    continue
                start, end = meta["data_offsets"]
                if meta["dtype"] != "F32" or end - start != 4:
                    continue
                handle.seek(base + start)
                values[name] = struct.unpack("<f", handle.read(4))[0]
    return values


def check(values: Dict[str, float]) -> Tuple[int, int, List[str]]:
    """(groups checked, groups that differ, a line per difference)."""
    layers = sorted({m.group(1) for m in (re.match(r"(.*\.layers\.\d+)\.", n) for n in values) if m},
                    key=lambda p: int(p.rsplit(".", 1)[1]))
    checked, bad, lines = 0, 0, []
    for layer in layers:
        for group in GROUPS:
            for scale in SCALES:
                found = [values.get(f"{layer}.{member}.{scale}") for member in group]
                if all(v is None for v in found):
                    continue
                checked += 1
                if any(v is None for v in found):
                    bad += 1
                    lines.append(f"{layer} {'+'.join(group)} {scale}: missing for part of the group {found}")
                    continue
                low, high = min(found), max(found)
                if high - low > TOLERANCE * max(abs(high), 1e-30):
                    bad += 1
                    lines.append(f"{layer} {'+'.join(m.split('.')[-1] for m in group)} {scale}: "
                                 f"{', '.join(f'{v:.6g}' for v in found)} (ratio {high / low:.3f})")
    return checked, bad, lines


def main(argv: List[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().split("\n\n")[1] if __doc__ else "usage: check_nvfp4_fused_scales.py <snapshot>")
        return 2
    snapshot = Path(argv[0])
    try:
        values = read_scalars(snapshot)
    except (OSError, ValueError, KeyError, struct.error) as exc:
        print(f"❌ Cannot read {snapshot}: {exc}")
        return 2
    if not values:
        print(f"❌ No compressed-tensors NVFP4 global scales in {snapshot}.")
        return 2
    checked, bad, lines = check(values)
    if not checked:
        print(f"❌ {len(values)} global scales, but none in a fused group this script knows.")
        return 2
    for line in lines[:40]:
        print(f"   {line}")
    if bad:
        print(f"❌ {bad} of {checked} fused-group scales differ: SGLang would serve those GEMMs mis-scaled.")
        return 1
    print(f"✅ All {checked} fused-group scales are harmonized ({len(values)} global scales read).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
