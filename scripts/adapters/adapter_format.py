#!/usr/bin/env python3
"""Which layers the adapter trains, and the adapter file SGLang can load.

**Targets.** Every linear layer of the text model that the pinned SGLang (`lmsysorg/sglang`
v0.5.21, digest in `QWEN38_SGLANG_RECIPE`) can serve with LoRA. Its `Qwen3_5ForCausalLM`
declares `supported_lora_modules = [qkv_proj, o_proj, out_proj, in_proj_qkvz, gate_up_proj,
down_proj, lm_head]`; in Hugging Face names that is:

- full attention (16 layers): `self_attn.{q,k,v,o}_proj`;
- Gated DeltaNet (48 layers): `linear_attn.{in_proj_qkv,in_proj_z,out_proj}`;
- MLP (64 layers): `mlp.{gate,up,down}_proj`.

Left out: `linear_attn.in_proj_a/in_proj_b` (48 outputs each; SGLang's `in_proj_ba` takes no
LoRA), `conv1d`, `lm_head` (the DFlash2 worker unwraps LoRA on `lm_head` and the embeddings, and
the drafter shares them), the embeddings, `mtp.*` and the vision tower.

**Export.** PEFT names the GDN input projection `in_proj_qkv` (one 10,240-row matrix: q 2,048,
k 2,048, v 6,144, split in that order in `Qwen3_5GatedDeltaNet.forward`). SGLang's loader
(`LoRAAdapter._normalize_in_proj_qkvz`) accepts either split `in_proj_q/k/v/z` or one merged
`in_proj_qkvz`, never `in_proj_qkv`. `export` therefore writes the split form: `lora_A` of
`in_proj_qkv` is repeated for q, k and v, `lora_B` is cut into its row slices, and `in_proj_z` is
copied. That is exact: each slice's product `B_slice @ A` is the matching rows of `B @ A`.

`audit` checks the targets against the checkpoint's tensors, against the HF model built on the
meta device (no memory, no GPU), against PEFT, and against SGLang's list and dimensions; and runs
the exporter on a synthetic adapter of the real shapes. Nothing here runs at import.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TARGET_MODULES: Tuple[str, ...] = (
    "q_proj", "k_proj", "v_proj", "o_proj",
    "in_proj_qkv", "in_proj_z", "out_proj",
    "gate_proj", "up_proj", "down_proj",
)

# Read from the pinned image's sglang/srt/models/qwen3_5.py (v0.5.21).
SGLANG_SUPPORTED: Tuple[str, ...] = ("qkv_proj", "o_proj", "out_proj", "in_proj_qkvz", "gate_up_proj",
                                     "down_proj", "lm_head")
# HF name -> the SGLang module its LoRA lands in.
SGLANG_MODULE_OF: Dict[str, str] = {
    "q_proj": "qkv_proj", "k_proj": "qkv_proj", "v_proj": "qkv_proj", "o_proj": "o_proj",
    "in_proj_qkv": "in_proj_qkvz", "in_proj_z": "in_proj_qkvz", "out_proj": "out_proj",
    "gate_proj": "gate_up_proj", "up_proj": "gate_up_proj", "down_proj": "down_proj",
}
NOT_SERVABLE: Tuple[str, ...] = ("in_proj_a", "in_proj_b")

PEFT_PREFIX: str = "base_model.model."


def lora_config_kwargs(rank: int, alpha: Optional[int] = None, dropout: float = 0.0) -> Dict[str, Any]:
    """The PEFT `LoraConfig` arguments of the adapter, one place for training and the audit."""
    return {
        "r": rank,
        "lora_alpha": alpha if alpha is not None else 2 * rank,
        "lora_dropout": dropout,
        "target_modules": list(TARGET_MODULES),
        "bias": "none",
        "task_type": "CAUSAL_LM",
    }


def qkv_split(text_config: Dict[str, Any]) -> Tuple[int, int, int]:
    """Rows of q, k and v inside `in_proj_qkv`."""
    key_dim = text_config["linear_key_head_dim"] * text_config["linear_num_key_heads"]
    value_dim = text_config["linear_value_head_dim"] * text_config["linear_num_value_heads"]
    return key_dim, key_dim, value_dim


def export_for_sglang(tensors: Dict[str, Any], text_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Rewrites a PEFT state dict so SGLang v0.5.21 can load it.

    Args:
        tensors: The adapter's tensors (`adapter_model.safetensors`).
        text_config: The base's `text_config`.

    Returns:
        Dict[str, Any]: The rewritten tensors (`in_proj_qkv` split into `in_proj_q/k/v`).
    """
    q_rows, k_rows, v_rows = qkv_split(text_config)
    out: Dict[str, Any] = {}
    for name, tensor in tensors.items():
        if ".in_proj_qkv." not in name:
            out[name] = tensor
            continue
        if ".lora_A." in name:
            for part in ("q", "k", "v"):
                out[name.replace(".in_proj_qkv.", f".in_proj_{part}.")] = tensor.clone()
        elif ".lora_B." in name:
            if tensor.shape[0] != q_rows + k_rows + v_rows:
                raise ValueError(f"{name}: {tuple(tensor.shape)} is not {q_rows + k_rows + v_rows} rows")
            q, k, v = tensor.split([q_rows, k_rows, v_rows], dim=0)
            for part, piece in (("q", q), ("k", k), ("v", v)):
                out[name.replace(".in_proj_qkv.", f".in_proj_{part}.")] = piece.contiguous()
        else:
            raise ValueError(f"unexpected tensor {name}")
    return out


def export_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """The adapter_config.json SGLang reads: the split names in place of `in_proj_qkv`."""
    out = dict(config)
    modules = [m for m in config["target_modules"] if m != "in_proj_qkv"]
    if "in_proj_qkv" in config["target_modules"]:
        modules += ["in_proj_q", "in_proj_k", "in_proj_v"]
    out["target_modules"] = sorted(set(modules))
    return out


def export_dir(source: Path, target: Path, text_config: Dict[str, Any]) -> None:
    """Exports a saved PEFT adapter directory into a directory SGLang loads with --lora-paths."""
    from safetensors.torch import load_file, save_file

    target.mkdir(parents=True, exist_ok=True)
    tensors = load_file(str(source / "adapter_model.safetensors"))
    save_file(export_for_sglang(tensors, text_config), str(target / "adapter_model.safetensors"))
    config = json.loads((source / "adapter_config.json").read_text())
    (target / "adapter_config.json").write_text(json.dumps(export_config(config), indent=2) + "\n")


# -- the audit ------------------------------------------------------------------------------------

def safetensors_header_names(snapshot: Path) -> Dict[str, Dict[str, Any]]:
    """Every tensor's dtype and shape, from the shards' headers (reads a few MB, no weights)."""
    names: Dict[str, Dict[str, Any]] = {}
    for shard in sorted(snapshot.glob("*.safetensors")):
        with open(shard, "rb") as handle:
            length = struct.unpack("<Q", handle.read(8))[0]
            header = json.loads(handle.read(length))
        header.pop("__metadata__", None)
        names.update(header)
    return names


def sglang_hidden_dims(module: str, tc: Dict[str, Any]) -> Tuple[int, int]:
    """SGLang's `Qwen3_5ForCausalLM.get_hidden_dim` (input, output) for a fused module."""
    head_dim = tc["head_dim"]
    if module == "qkv_proj":
        q_heads = tc["num_attention_heads"] * (2 if tc.get("attn_output_gate", True) else 1)
        return tc["hidden_size"], head_dim * (q_heads + tc["num_key_value_heads"] * 2)
    if module == "o_proj":
        return tc["num_attention_heads"] * head_dim, tc["hidden_size"]
    if module == "out_proj":
        return tc["linear_value_head_dim"] * tc["linear_num_value_heads"], tc["hidden_size"]
    if module == "in_proj_qkvz":
        key_dim = tc["linear_key_head_dim"] * tc["linear_num_key_heads"]
        value_dim = tc["linear_value_head_dim"] * tc["linear_num_value_heads"]
        return tc["hidden_size"], key_dim * 2 + value_dim * 2
    if module == "gate_up_proj":
        return tc["hidden_size"], tc["intermediate_size"] * 2
    if module == "down_proj":
        return tc["intermediate_size"], tc["hidden_size"]
    raise KeyError(module)


def audit(snapshot: Path, ranks: List[int]) -> List[str]:
    import torch
    from transformers.models.qwen3_5 import configuration_qwen3_5, modeling_qwen3_5

    failures: List[str] = []
    config = json.loads((snapshot / "config.json").read_text())
    tc = config["text_config"]
    header = safetensors_header_names(snapshot)

    # 1. The HF text model on the meta device: names, shapes, layer types.
    with torch.device("meta"):
        model = modeling_qwen3_5.Qwen3_5ForCausalLM(configuration_qwen3_5.Qwen3_5TextConfig(**tc))
    linears = {name: module for name, module in model.named_modules() if isinstance(module, torch.nn.Linear)}
    by_leaf: Dict[str, List[str]] = {}
    for name in linears:
        by_leaf.setdefault(name.rsplit(".", 1)[-1], []).append(name)
    print("🚀 linear layers of Qwen3_5ForCausalLM (meta device)")
    for leaf, names in sorted(by_leaf.items()):
        module = linears[names[0]]
        mark = "train" if leaf in TARGET_MODULES else "frozen"
        print(f"  {leaf:12s} x{len(names):3d}  {module.in_features:6d} -> {module.out_features:6d}  {mark}")
    for leaf in TARGET_MODULES:
        if leaf not in by_leaf:
            failures.append(f"target {leaf} is not a linear layer of the HF model")

    # 2. Every targeted module exists in the served checkpoint under the text prefix.
    for leaf in TARGET_MODULES:
        for name in by_leaf.get(leaf, []):
            source = "model.language_model." + name[len("model."):] + ".weight"
            if source not in header:
                failures.append(f"{source} is not in the checkpoint")
    layer_types = tc["layer_types"]
    attention_layers = {i for i, kind in enumerate(layer_types) if kind == "full_attention"}
    for name in by_leaf.get("q_proj", []):
        if int(re.search(r"layers\.(\d+)\.", name).group(1)) not in attention_layers:
            failures.append(f"{name} sits in a linear-attention layer")
    print(f"  layers: {len(layer_types)}, full attention at {sorted(attention_layers)[:4]}... "
          f"({len(attention_layers)}), Gated DeltaNet {len(layer_types) - len(attention_layers)}")

    # 3. SGLang: every target maps into a supported module whose dims match the HF layers.
    for leaf in TARGET_MODULES:
        fused = SGLANG_MODULE_OF[leaf]
        if fused not in SGLANG_SUPPORTED:
            failures.append(f"{leaf} -> {fused} is not servable")
    groups: Dict[str, List[str]] = {}
    for leaf in TARGET_MODULES:
        groups.setdefault(SGLANG_MODULE_OF[leaf], []).append(leaf)
    for fused, leaves in groups.items():
        expected = sglang_hidden_dims(fused, tc)
        first = [linears[by_leaf[leaf][0]] for leaf in leaves]
        got = (first[0].in_features, sum(m.out_features for m in first))
        status = "ok" if got == expected else "MISMATCH"
        print(f"  SGLang {fused:13s} <- {'+'.join(leaves):30s} {got} expected {expected} {status}")
        if got != expected:
            failures.append(f"{fused}: HF gives {got}, SGLang expects {expected}")

    # 4. PEFT on the meta model: what it would train, and how much.
    try:
        from peft import LoraConfig, get_peft_model
    except ImportError:
        print("  (peft not installed: the PEFT checks are skipped)")
        peft_ok = False
    else:
        peft_ok = True
    if peft_ok:
        for rank in ranks:
            with torch.device("meta"):
                fresh = modeling_qwen3_5.Qwen3_5ForCausalLM(configuration_qwen3_5.Qwen3_5TextConfig(**tc))
                wrapped = get_peft_model(fresh, LoraConfig(**lora_config_kwargs(rank)))
            trainable = [(n, p) for n, p in wrapped.named_parameters() if p.requires_grad]
            count = sum(p.numel() for _, p in trainable)
            leaves = {re.search(r"\.([a-z_]+)\.lora_[AB]\.", n).group(1) for n, _ in trainable}
            print(f"  PEFT r={rank}: {len(trainable)} tensors, {count / 1e6:.1f}M parameters "
                  f"({count * 2 / 1e6:.0f} MB in BF16), modules {sorted(leaves)}")
            if leaves != set(TARGET_MODULES):
                failures.append(f"PEFT r={rank} trains {sorted(leaves)}")
            if any(not n.startswith(PEFT_PREFIX + "model.layers.") for n, _ in trainable):
                failures.append("a LoRA tensor sits outside model.layers.*")

    # 5. The exporter on a synthetic adapter of the real shapes (one GDN layer).
    rank = ranks[0]
    gdn = next(i for i, kind in enumerate(layer_types) if kind == "linear_attention")
    base = f"{PEFT_PREFIX}model.layers.{gdn}.linear_attn"
    hidden = tc["hidden_size"]
    rows = sum(qkv_split(tc))
    generator = torch.Generator().manual_seed(0)
    synthetic = {
        f"{base}.in_proj_qkv.lora_A.weight": torch.randn(rank, hidden, generator=generator),
        f"{base}.in_proj_qkv.lora_B.weight": torch.randn(rows, rank, generator=generator),
        f"{base}.in_proj_z.lora_A.weight": torch.randn(rank, hidden, generator=generator),
        f"{base}.in_proj_z.lora_B.weight": torch.randn(tc["linear_value_head_dim"] * tc["linear_num_value_heads"], rank,
                                                       generator=generator),
    }
    exported = export_for_sglang(synthetic, tc)
    delta = synthetic[f"{base}.in_proj_qkv.lora_B.weight"] @ synthetic[f"{base}.in_proj_qkv.lora_A.weight"]
    rebuilt = torch.cat([exported[f"{base}.in_proj_{p}.lora_B.weight"] @ exported[f"{base}.in_proj_{p}.lora_A.weight"]
                         for p in ("q", "k", "v")])
    if not torch.allclose(delta, rebuilt):
        failures.append("the in_proj_qkv split changes the LoRA product")
    # SGLang's split branch needs q, k, v and z all present to stack into in_proj_qkvz.
    stacked_rows = sum(exported[f"{base}.in_proj_{p}.lora_B.weight"].shape[0] for p in ("q", "k", "v", "z"))
    if stacked_rows != sglang_hidden_dims("in_proj_qkvz", tc)[1]:
        failures.append(f"split adapter stacks to {stacked_rows} rows")
    print(f"  export: in_proj_qkv -> in_proj_q/k/v {[exported[f'{base}.in_proj_{p}.lora_B.weight'].shape[0] for p in 'qkv']}"
          f" + in_proj_z, stacks to {stacked_rows} rows, product exact")
    return failures


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    audit_cmd = sub.add_parser("audit", help="check the target layers without a GPU")
    audit_cmd.add_argument("--snapshot", help="checkpoint directory (default: the local HF cache)")
    audit_cmd.add_argument("--ranks", default="16,32", help="ranks to count (default 16,32)")
    export_cmd = sub.add_parser("export", help="rewrite a PEFT adapter directory for SGLang")
    export_cmd.add_argument("source", type=Path)
    export_cmd.add_argument("target", type=Path)
    export_cmd.add_argument("--snapshot", help="checkpoint directory, for text_config")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from served_base import find_snapshot

    snapshot = find_snapshot(args.snapshot)
    if args.command == "export":
        text_config = json.loads((snapshot / "config.json").read_text())["text_config"]
        export_dir(args.source, args.target, text_config)
        print(f"✅ wrote {args.target}")
        return 0
    failures = audit(snapshot, [int(r) for r in args.ranks.split(",")])
    for failure in failures:
        print(f"❌ {failure}")
    if not failures:
        print("✅ targets exist in the checkpoint and the HF model, PEFT trains exactly them, SGLang can serve them")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
