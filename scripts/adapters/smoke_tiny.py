#!/usr/bin/env python3
"""The whole training path on a tiny Qwen3.5-architecture model, on the CPU or one GPU.

    python scripts/adapters/smoke_tiny.py [--device cuda] [--steps 8]

It writes a synthetic checkpoint in the served layout (FP8 attention and GDN projections, NVFP4
MLP and lm_head, BF16 the rest, `hf_quant_config.json`, sharded safetensors with an index) to a
temporary directory, then:

1. loads it with `served_base.load_served_text_model` and checks its logits against a plain HF
   model holding the dequantized weights (the loader puts every tensor where it belongs);
2. wraps it with PEFT on `adapter_format.TARGET_MODULES` and trains a few steps on one fixed
   sequence with `train_lora.chunked_cross_entropy` under gradient checkpointing: the loss must
   fall and only LoRA tensors may change;
3. saves the adapter and exports it for SGLang.

On a GPU it is the first thing to run on a new machine, before the 27B model. Nothing runs at import.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent

TINY_TEXT_CONFIG: Dict[str, Any] = {
    "model_type": "qwen3_5_text", "hidden_size": 64, "intermediate_size": 128, "num_hidden_layers": 4,
    "layer_types": ["linear_attention", "linear_attention", "linear_attention", "full_attention"],
    "full_attention_interval": 4, "num_attention_heads": 4, "num_key_value_heads": 2, "head_dim": 16,
    "linear_key_head_dim": 16, "linear_num_key_heads": 2, "linear_value_head_dim": 16,
    "linear_num_value_heads": 4, "linear_conv_kernel_dim": 4, "vocab_size": 512, "attn_output_gate": True,
    "partial_rotary_factor": 0.25, "rms_norm_eps": 1e-6, "max_position_embeddings": 4096,
    "rope_parameters": {"rope_type": "default", "rope_theta": 10000000, "partial_rotary_factor": 0.25,
                        "mrope_interleaved": True, "mrope_section": [1, 1, 0]},
    "tie_word_embeddings": False, "hidden_act": "silu", "dtype": "bfloat16",
}

FP8_LEAVES = ("q_proj", "k_proj", "v_proj", "o_proj", "in_proj_qkv", "in_proj_z", "out_proj")
NVFP4_LEAVES = ("gate_proj", "up_proj", "down_proj", "lm_head")


def quantize_fp8(weight: Any) -> Dict[str, Any]:
    import torch

    scale = weight.abs().max().float() / 448.0
    return {"weight": (weight.float() / scale).to(torch.float8_e4m3fn), "weight_scale": scale.reshape(()),
            "input_scale": torch.tensor(0.05)}


def quantize_nvfp4(weight: Any) -> Dict[str, Any]:
    import torch
    from served_base import E2M1_VALUES, round_to_e2m1

    out_features, in_features = weight.shape
    w = weight.float()
    global_scale = w.abs().max() / (448.0 * 6.0)
    blocks = w.view(out_features, -1, 16)
    block_scale = (blocks.abs().amax(-1) / 6.0 / global_scale).to(torch.float8_e4m3fn)
    codes = round_to_e2m1(blocks / (block_scale.float() * global_scale).unsqueeze(-1)).view(out_features, in_features)
    grid = torch.tensor(E2M1_VALUES)
    nibbles = ((codes.abs().unsqueeze(-1) == grid).float().argmax(-1) | ((codes < 0).long() << 3)).to(torch.uint8)
    return {"weight": nibbles[:, 0::2] | (nibbles[:, 1::2] << 4), "weight_scale": block_scale,
            "weight_scale_2": global_scale.reshape(()), "input_scale": torch.tensor(0.01)}


def write_snapshot(directory: Path, seed: int = 0) -> Dict[str, Any]:
    """A tiny checkpoint in the served layout. Returns the dequantized dense state dict."""
    import torch
    from safetensors.torch import save_file
    from served_base import dequantize_fp8, dequantize_nvfp4
    from transformers.models.qwen3_5 import configuration_qwen3_5, modeling_qwen3_5

    torch.manual_seed(seed)
    dense = modeling_qwen3_5.Qwen3_5ForCausalLM(configuration_qwen3_5.Qwen3_5TextConfig(**TINY_TEXT_CONFIG))
    tensors: Dict[str, Any] = {}
    reference: Dict[str, Any] = {}
    layers: Dict[str, Dict[str, Any]] = {}
    for name, value in dense.state_dict().items():
        module, _, leaf_kind = name.rpartition(".")
        leaf = module.rsplit(".", 1)[-1]
        source = module if module == "lm_head" else "model.language_model." + module[len("model."):]
        if leaf_kind == "weight" and leaf in FP8_LEAVES + NVFP4_LEAVES:
            quantized = quantize_fp8(value) if leaf in FP8_LEAVES else quantize_nvfp4(value)
            for suffix, tensor in quantized.items():
                tensors[f"{source}.{suffix}"] = tensor
            layers[source] = {"quant_algo": "FP8"} if leaf in FP8_LEAVES else {"quant_algo": "NVFP4", "group_size": 16}
            reference[name] = (dequantize_fp8(quantized["weight"], quantized["weight_scale"], torch.float32)
                               if leaf in FP8_LEAVES else
                               dequantize_nvfp4(quantized["weight"], quantized["weight_scale"],
                                                quantized["weight_scale_2"], torch.float32))
        else:
            target = name if name.startswith("lm_head") else "model.language_model." + name[len("model."):]
            tensors[target] = value.to(torch.bfloat16).contiguous()
            reference[name] = tensors[target].float()
    names = sorted(tensors)
    half = len(names) // 2
    shards = {"model-00001-of-00002.safetensors": names[:half], "model-00002-of-00002.safetensors": names[half:]}
    for file, keys in shards.items():
        save_file({k: tensors[k].contiguous() for k in keys}, str(directory / file))
    (directory / "model.safetensors.index.json").write_text(json.dumps(
        {"weight_map": {k: f for f, keys in shards.items() for k in keys}}))
    (directory / "hf_quant_config.json").write_text(json.dumps(
        {"quantization": {"quant_algo": "MIXED_PRECISION", "quantized_layers": layers}}))
    (directory / "config.json").write_text(json.dumps({"model_type": "qwen3_5", "text_config": TINY_TEXT_CONFIG}))
    return reference


def run(device: str, steps: int) -> List[str]:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers.models.qwen3_5 import configuration_qwen3_5, modeling_qwen3_5

    sys.path.insert(0, str(HERE))
    from adapter_format import TARGET_MODULES, export_dir, lora_config_kwargs
    from served_base import ServedLinear, load_served_text_model
    from train_lora import chunked_cross_entropy

    failures: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "snapshot"
        snapshot.mkdir()
        reference_state = write_snapshot(snapshot)

        # 1. The loader against a dense model holding the same (dequantized) values.
        served = load_served_text_model(snapshot, device, fake_quant_inputs=False, dtype=torch.float32)
        dense = modeling_qwen3_5.Qwen3_5ForCausalLM(configuration_qwen3_5.Qwen3_5TextConfig(**TINY_TEXT_CONFIG))
        dense.load_state_dict(reference_state)
        dense.to(device).eval()
        served.eval()
        ids = torch.randint(0, TINY_TEXT_CONFIG["vocab_size"], (1, 96), generator=torch.Generator().manual_seed(1)).to(device)
        with torch.no_grad():
            a, b = served(input_ids=ids).logits, dense(input_ids=ids).logits
        error = (a - b).abs().max().item()
        print(f"  loader: max |logit difference| against the dense reference {error:.2e}")
        if error > 1e-3:
            failures.append(f"served loader differs from the dense reference by {error}")
        quantized = sum(isinstance(m, ServedLinear) for m in served.modules())
        print(f"  ServedLinear layers: {quantized}")

        # 2. LoRA training through the served layers.
        served.config.use_cache = False
        served.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        head = served.lm_head.dequantized(torch.float32).detach()
        model = get_peft_model(served, LoraConfig(**lora_config_kwargs(4)))
        frozen_before = {n: p.detach().clone() for n, p in model.named_parameters() if not p.requires_grad}
        params = [p for p in model.parameters() if p.requires_grad]
        optimizer = torch.optim.AdamW(params, lr=5e-3)
        labels = ids[0].clone()
        labels[:32] = -100
        shifted = torch.cat([labels[1:], labels.new_full((1,), -100)])
        losses = []
        model.train()
        for _ in range(steps):
            hidden = model.base_model.model.model(input_ids=ids).last_hidden_state[0]
            loss = chunked_cross_entropy(hidden, head, shifted, 16) / int((shifted != -100).sum())
            loss.backward()
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            losses.append(float(loss))
        print(f"  training: loss {losses[0]:.3f} -> {losses[-1]:.3f} over {steps} steps")
        if not losses[-1] < losses[0]:
            failures.append("the loss did not fall")
        changed = [n for n, p in model.named_parameters() if not p.requires_grad and not torch.equal(p, frozen_before[n])]
        if changed:
            failures.append(f"frozen tensors changed: {changed[:3]}")
        trained = {n.split(".lora_")[0].rsplit(".", 1)[-1] for n, p in model.named_parameters() if p.requires_grad}
        if trained != set(TARGET_MODULES):
            failures.append(f"LoRA sits on {sorted(trained)}")

        # 3. Save and export.
        out = Path(tmp) / "adapter"
        model.save_pretrained(str(out))
        export_dir(out, Path(tmp) / "sglang", TINY_TEXT_CONFIG)
        from safetensors.torch import load_file

        exported = load_file(str(Path(tmp) / "sglang" / "adapter_model.safetensors"))
        if any(".in_proj_qkv." in k for k in exported) or not any(".in_proj_q." in k for k in exported):
            failures.append("the export did not split in_proj_qkv")
        print(f"  export: {len(exported)} tensors, e.g. {sorted(exported)[0]}")
    return failures


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--steps", type=int, default=8)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(HERE))
    print(f"🚀 tiny served-layout model on {args.device}")
    failures = run(args.device, args.steps)
    for failure in failures:
        print(f"❌ {failure}")
    if not failures:
        print("✅ loader, LoRA training through the served layers and the SGLang export work end to end")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
