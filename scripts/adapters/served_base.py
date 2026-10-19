#!/usr/bin/env python3
"""The served checkpoint as a frozen training base: exact weights, optional served activations.

`RadixArk/Qwen3.8-27B-NVFP4` is ModelOpt `MIXED_PRECISION`, not plain NVFP4:

- FP8 E4M3, per-tensor scale (W8A8, static input scale): `self_attn.{q,k,v,o}_proj` (16 layers)
  and `linear_attn.{in_proj_qkv,in_proj_z,out_proj}` (48 layers);
- NVFP4, E2M1 values with an E4M3 scale per 16 inputs and an FP32 global scale (W4A4):
  `mlp.{gate,up,down}_proj` (64 layers) and `lm_head`;
- BF16: embeddings, norms, `linear_attn.{in_proj_a,in_proj_b,conv1d,A_log,dt_bias}`, `mtp.*`,
  the vision tower.

`ServedLinear` keeps a quantized layer in its served bytes (about 21 GB for the text model, against
about 54 GB in BF16) and dequantizes it inside the matmul, forward and backward, never keeping the
BF16 copy: the frozen base the adapter trains against has exactly the served weight values. With
`fake_quant_inputs` it also rounds the base path's input the way the served kernels do (FP8 with the
static input scale; NVFP4 with dynamic per-16 block scales under the static global scale), with a
straight-through gradient. SGLang's LoRA path reads the unquantized input, so the adapter's own
branch never sees that rounding, here or at serving.

`python scripts/adapters/served_base.py check` runs on the CPU against the local snapshot: it
decodes one FP8 and one NVFP4 tensor of layer 0 and compares the decoder with SGLang's reference
layout (low nibble first), checks the ModelOpt block-scale invariant, and checks `ServedLinear`'s
gradients and PEFT's wrapping of it on tiny synthetic layers. Nothing here runs at import.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import torch
import torch.nn.functional as F

E2M1_VALUES: Tuple[float, ...] = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
NVFP4_BLOCK: int = 16
FP8_E4M3_MAX: float = 448.0
FP4_E2M1_MAX: float = 6.0

SNAPSHOT_GLOB: str = "~/.cache/huggingface/hub/models--RadixArk--Qwen3.8-27B-NVFP4/snapshots/*/"

# Checkpoint prefix of the text model, and the prefix the text-only HF class uses. Training on
# `Qwen3_5ForCausalLM` gives adapter keys under `model.layers.*`, which is what SGLang's
# `should_apply_lora` accepts.
CHECKPOINT_TEXT_PREFIX: str = "model.language_model."
CAUSAL_LM_PREFIX: str = "model."


# -- decoding -------------------------------------------------------------------------------------

def e2m1_lut(device: Any = "cpu") -> torch.Tensor:
    """The 16 E2M1 codes as floats: bit 3 is the sign, bits 0-2 index `E2M1_VALUES`."""
    positive = torch.tensor(E2M1_VALUES, dtype=torch.float32, device=device)
    return torch.cat([positive, -positive])


def unpack_e2m1(packed: torch.Tensor) -> torch.Tensor:
    """
    Unpacks ModelOpt's FP4 bytes: two codes per byte, the low nibble is the even column.

    Args:
        packed: uint8 [out, in / 2].

    Returns:
        torch.Tensor: float32 [out, in], the E2M1 values before scaling.
    """
    lut = e2m1_lut(packed.device)
    low = (packed & 0x0F).long()
    high = (packed >> 4).long()
    out = torch.empty(packed.shape[0], packed.shape[1] * 2, dtype=torch.float32, device=packed.device)
    out[:, 0::2] = lut[low]
    out[:, 1::2] = lut[high]
    return out


def dequantize_nvfp4(packed: torch.Tensor, block_scale: torch.Tensor, global_scale: torch.Tensor,
                     dtype: torch.dtype = torch.bfloat16) -> torch.Tensor:
    """
    Dequantizes an NVFP4 weight: value x E4M3 block scale (per 16 inputs) x FP32 global scale.

    Args:
        packed: uint8 [out, in / 2] (`weight`).
        block_scale: float8_e4m3fn [out, in / 16] (`weight_scale`).
        global_scale: float32 scalar (`weight_scale_2`).
        dtype: The result's dtype.

    Returns:
        torch.Tensor: [out, in].
    """
    values = unpack_e2m1(packed)
    scale = block_scale.to(torch.float32) * global_scale.to(torch.float32)
    values = values.view(values.shape[0], -1, NVFP4_BLOCK) * scale.unsqueeze(-1)
    return values.view(packed.shape[0], -1).to(dtype)


def dequantize_fp8(weight: torch.Tensor, scale: torch.Tensor, dtype: torch.dtype = torch.bfloat16) -> torch.Tensor:
    """
    Dequantizes a per-tensor FP8 weight.

    Args:
        weight: float8_e4m3fn [out, in].
        scale: float32 scalar (`weight_scale`).
        dtype: The result's dtype.

    Returns:
        torch.Tensor: [out, in].
    """
    return (weight.to(torch.float32) * scale.to(torch.float32)).to(dtype)


def round_to_e2m1(x: torch.Tensor) -> torch.Tensor:
    """Rounds float32 values (already scaled into [-6, 6]) to the nearest E2M1 value."""
    grid = torch.tensor(E2M1_VALUES, dtype=x.dtype, device=x.device)
    magnitude = x.abs().clamp(max=FP4_E2M1_MAX)
    index = torch.bucketize(magnitude, (grid[1:] + grid[:-1]) / 2)
    return grid[index] * x.sign()


class _StraightThrough(torch.autograd.Function):
    """Forward: the rounded tensor. Backward: the gradient unchanged."""

    @staticmethod
    def forward(ctx: Any, x: torch.Tensor, rounded: torch.Tensor) -> torch.Tensor:  # type: ignore[override]
        return rounded

    @staticmethod
    def backward(ctx: Any, grad: torch.Tensor) -> Tuple[torch.Tensor, None]:  # type: ignore[override]
        return grad, None


def fake_quant_fp8_input(x: torch.Tensor, input_scale: torch.Tensor) -> torch.Tensor:
    """FP8 E4M3 with the static per-tensor input scale, as the W8A8 kernels see it."""
    with torch.no_grad():
        scale = input_scale.to(torch.float32)
        q = (x.float() / scale).clamp(-FP8_E4M3_MAX, FP8_E4M3_MAX).to(torch.float8_e4m3fn)
        rounded = (q.to(torch.float32) * scale).to(x.dtype)
    return _StraightThrough.apply(x, rounded)


def fake_quant_nvfp4_input(x: torch.Tensor, input_scale: torch.Tensor) -> torch.Tensor:
    """NVFP4 with a dynamic E4M3 scale per 16 values under the static global input scale (W4A4)."""
    with torch.no_grad():
        glob_scale = input_scale.to(torch.float32)
        shape = x.shape
        blocks = x.float().reshape(-1, NVFP4_BLOCK)
        block_scale = (blocks.abs().amax(-1, keepdim=True) / FP4_E2M1_MAX / glob_scale)
        block_scale = block_scale.clamp(max=FP8_E4M3_MAX).to(torch.float8_e4m3fn).to(torch.float32)
        denominator = (block_scale * glob_scale).clamp(min=torch.finfo(torch.float32).tiny)
        rounded = round_to_e2m1(blocks / denominator) * denominator
        rounded = rounded.reshape(shape).to(x.dtype)
    return _StraightThrough.apply(x, rounded)


# -- the frozen layer -----------------------------------------------------------------------------

class _DequantMatmul(torch.autograd.Function):
    """`x @ W^T` with W rebuilt from its served bytes in forward and again in backward."""

    @staticmethod
    def forward(ctx: Any, x: torch.Tensor, layer: "ServedLinear") -> torch.Tensor:  # type: ignore[override]
        ctx.layer = layer
        weight = layer.dequantized(x.dtype)
        return F.linear(x, weight)

    @staticmethod
    def backward(ctx: Any, grad: torch.Tensor) -> Tuple[torch.Tensor, None]:  # type: ignore[override]
        weight = ctx.layer.dequantized(grad.dtype)
        return grad @ weight, None


class ServedLinear(torch.nn.Linear):
    """
    A frozen linear layer held in its served format.

    It subclasses `nn.Linear` so PEFT's default LoRA dispatch wraps it (`in_features`,
    `out_features` and a `weight` attribute are what PEFT reads); the `weight` parameter holds the
    served bytes (uint8 or float8) and never takes a gradient.
    """

    def __init__(self, in_features: int, out_features: int, fmt: str, tensors: Dict[str, torch.Tensor],
                 fake_quant_inputs: bool = False) -> None:
        torch.nn.Module.__init__(self)
        self.in_features = in_features
        self.out_features = out_features
        self.fmt = fmt
        self.fake_quant_inputs = fake_quant_inputs
        self.weight = torch.nn.Parameter(tensors["weight"], requires_grad=False)
        self.register_parameter("bias", None)
        for name in ("weight_scale", "weight_scale_2", "input_scale"):
            if name in tensors:
                self.register_buffer(name, tensors[name], persistent=False)
        if fmt not in ("fp8", "nvfp4", "bf16"):
            raise ValueError(f"unknown format {fmt}")

    def dequantized(self, dtype: torch.dtype) -> torch.Tensor:
        """The served weight values as a dense matrix of `dtype`."""
        if self.fmt == "nvfp4":
            return dequantize_nvfp4(self.weight, self.weight_scale, self.weight_scale_2, dtype)
        if self.fmt == "fp8":
            return dequantize_fp8(self.weight, self.weight_scale, dtype)
        return self.weight.to(dtype)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # type: ignore[override]
        if self.fake_quant_inputs and hasattr(self, "input_scale"):
            x = fake_quant_nvfp4_input(x, self.input_scale) if self.fmt == "nvfp4" \
                else fake_quant_fp8_input(x, self.input_scale)
        return _DequantMatmul.apply(x, self)

    def extra_repr(self) -> str:
        return f"in_features={self.in_features}, out_features={self.out_features}, fmt={self.fmt}"


# -- the checkpoint -------------------------------------------------------------------------------

def find_snapshot(path: Optional[str] = None) -> Path:
    """The local snapshot directory of the served checkpoint."""
    if path:
        return Path(os.path.expanduser(path))
    matches = sorted(glob.glob(os.path.expanduser(SNAPSHOT_GLOB)))
    if not matches:
        raise SystemExit("❌ no local snapshot of RadixArk/Qwen3.8-27B-NVFP4; pass --snapshot")
    return Path(matches[-1])


def layer_formats(snapshot: Path) -> Dict[str, str]:
    """Maps each quantized module of the checkpoint to `fp8` or `nvfp4` (hf_quant_config.json)."""
    config = json.loads((snapshot / "hf_quant_config.json").read_text())
    layers = config["quantization"]["quantized_layers"]
    return {name: {"FP8": "fp8", "NVFP4": "nvfp4"}[entry["quant_algo"]] for name, entry in layers.items()}


def tensor_index(snapshot: Path) -> Dict[str, str]:
    """Maps each tensor name to its shard file."""
    return json.loads((snapshot / "model.safetensors.index.json").read_text())["weight_map"]


def read_tensors(snapshot: Path, names: Iterable[str], device: str = "cpu") -> Dict[str, torch.Tensor]:
    """Reads named tensors from the shards, one shard opened at a time."""
    from safetensors import safe_open

    index = tensor_index(snapshot)
    by_file: Dict[str, List[str]] = {}
    for name in names:
        by_file.setdefault(index[name], []).append(name)
    out: Dict[str, torch.Tensor] = {}
    for file, file_names in by_file.items():
        with safe_open(str(snapshot / file), framework="pt", device=device) as handle:
            for name in file_names:
                out[name] = handle.get_tensor(name)
    return out


def checkpoint_name(hf_module: str) -> str:
    """`model.layers.N.x` (Qwen3_5ForCausalLM) -> `model.language_model.layers.N.x` (checkpoint)."""
    if hf_module == "lm_head":
        return hf_module
    if hf_module.startswith(CAUSAL_LM_PREFIX):
        return CHECKPOINT_TEXT_PREFIX + hf_module[len(CAUSAL_LM_PREFIX):]
    return hf_module


def load_served_text_model(snapshot: Path, device: str = "cuda", fake_quant_inputs: bool = True,
                           dtype: torch.dtype = torch.bfloat16) -> torch.nn.Module:
    """
    Builds `Qwen3_5ForCausalLM` from the served checkpoint: quantized layers as `ServedLinear`,
    everything else (embeddings, norms, the GDN's conv1d, A_log, dt_bias, in_proj_a/b) in BF16.
    The vision tower and the MTP head are not loaded.

    Args:
        snapshot: The checkpoint directory.
        device: Where the weights go.
        fake_quant_inputs: Round the base path's inputs as the served kernels do.
        dtype: Dtype of the unquantized tensors and of the compute.

    Returns:
        torch.nn.Module: The model, every parameter frozen.
    """
    from transformers.models.qwen3_5 import configuration_qwen3_5, modeling_qwen3_5

    config = json.loads((snapshot / "config.json").read_text())
    text_config = configuration_qwen3_5.Qwen3_5TextConfig(**config["text_config"])
    with torch.device("meta"):
        model = modeling_qwen3_5.Qwen3_5ForCausalLM(text_config)
    formats = layer_formats(snapshot)
    index = tensor_index(snapshot)

    # Swap every quantized nn.Linear for a ServedLinear holding its bytes.
    for name, module in list(model.named_modules()):
        if not isinstance(module, torch.nn.Linear):
            continue
        source = checkpoint_name(name)
        fmt = formats.get(source)
        if fmt is None:
            continue
        keys = [f"{source}.{suffix}" for suffix in ("weight", "weight_scale", "weight_scale_2", "input_scale")
                if f"{source}.{suffix}" in index]
        raw = read_tensors(snapshot, keys, device)
        tensors = {key[len(source) + 1:]: value for key, value in raw.items()}
        served = ServedLinear(module.in_features, module.out_features, fmt, tensors, fake_quant_inputs)
        parent_name, _, child = name.rpartition(".")
        setattr(model.get_submodule(parent_name) if parent_name else model, child, served)

    # Everything left on the meta device is a plain BF16 tensor of the checkpoint.
    state: Dict[str, torch.Tensor] = {}
    wanted = [name for name, tensor in list(model.named_parameters()) + list(model.named_buffers())
              if tensor.is_meta]
    raw = read_tensors(snapshot, [checkpoint_name(name) for name in wanted if checkpoint_name(name) in index], device)
    for name in wanted:
        source = checkpoint_name(name)
        if source in raw:
            state[name] = raw[source].to(dtype) if raw[source].is_floating_point() else raw[source]
    missing = [name for name in wanted if name not in state]
    model.load_state_dict(state, strict=False, assign=True)
    # Buffers computed at init (rotary inverse frequencies) are rebuilt on the device.
    for name in missing:
        if "rotary_emb" not in name:
            raise RuntimeError(f"no checkpoint tensor for {name}")
    if missing:
        model.model.rotary_emb = type(model.model.rotary_emb)(text_config, device=device)
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return model


# -- the CPU check --------------------------------------------------------------------------------

def _reference_unpack(packed: torch.Tensor) -> torch.Tensor:
    """SGLang's test reference (`quant_ref_utils.break_fp4_bytes`): stack(low, high) per byte."""
    flat = packed.flatten()
    combined = torch.stack((flat & 0x0F, (flat & 0xF0) >> 4), dim=1).flatten()
    signs = (combined & 0x08).to(torch.bool)
    values = torch.tensor(E2M1_VALUES)[(combined & 0x07).long()] * torch.where(signs, -1.0, 1.0)
    return values.reshape(packed.shape[0], packed.shape[1] * 2)


def check_checkpoint(snapshot: Path) -> List[str]:
    """Decodes layer 0's `linear_attn.in_proj_z` (FP8) and `mlp.down_proj` (NVFP4) on the CPU."""
    failures: List[str] = []
    fp8 = "model.language_model.layers.0.linear_attn.in_proj_z"
    nv = "model.language_model.layers.0.mlp.down_proj"
    tensors = read_tensors(snapshot, [f"{fp8}.weight", f"{fp8}.weight_scale",
                                      f"{nv}.weight", f"{nv}.weight_scale", f"{nv}.weight_scale_2"])
    w8 = dequantize_fp8(tensors[f"{fp8}.weight"], tensors[f"{fp8}.weight_scale"], torch.float32)
    print(f"  {fp8}: {tuple(w8.shape)} FP8, std {w8.std():.5f}, max |w| {w8.abs().max():.4f}")

    packed, block, glob_scale = tensors[f"{nv}.weight"], tensors[f"{nv}.weight_scale"], tensors[f"{nv}.weight_scale_2"]
    ours = unpack_e2m1(packed)
    if not torch.equal(ours, _reference_unpack(packed)):
        failures.append("the E2M1 decoder disagrees with SGLang's reference layout")
    w4 = dequantize_nvfp4(packed, block, glob_scale, torch.float32)
    # ModelOpt's max calibration sets each block scale to amax/6 (rounded to E4M3), so the largest
    # code of almost every block is +-6 or +-4 (when the E4M3 rounding of the scale went up).
    block_max = ours.abs().view(ours.shape[0], -1, NVFP4_BLOCK).amax(-1)
    share_top = (block_max >= 4.0).float().mean().item()
    print(f"  {nv}: {tuple(w4.shape)} NVFP4, std {w4.std():.5f}, max |w| {w4.abs().max():.4f}, "
          f"blocks whose largest code is >= 4: {share_top:.4f}")
    if share_top < 0.95:
        failures.append(f"only {share_top:.3f} of NVFP4 blocks reach code 4 or 6: scale layout looks wrong")
    if not (1e-3 < w8.std().item() < 0.2 and 1e-3 < w4.std().item() < 0.2):
        failures.append("dequantized weight scale is implausible")
    return failures


def check_layer() -> List[str]:
    """`ServedLinear` against a dense layer: forward, input gradient, no weight gradient, PEFT."""
    failures: List[str] = []
    torch.manual_seed(0)
    dense = torch.randn(64, 32) * 0.05
    # Quantize `dense` to NVFP4 the ModelOpt way, then check that ServedLinear computes with exactly
    # the dequantized values.
    glob_scale = dense.abs().max() / (FP8_E4M3_MAX * FP4_E2M1_MAX)
    blocks = dense.view(64, -1, NVFP4_BLOCK)
    block_scale = (blocks.abs().amax(-1) / FP4_E2M1_MAX / glob_scale).to(torch.float8_e4m3fn)
    codes = round_to_e2m1(blocks / (block_scale.float() * glob_scale).unsqueeze(-1)).view(64, 32)
    grid = torch.tensor(E2M1_VALUES)
    magnitude_code = (codes.abs().unsqueeze(-1) == grid).float().argmax(-1)
    nibbles = (magnitude_code | ((codes < 0).long() << 3)).to(torch.uint8)
    packed = nibbles[:, 0::2] | (nibbles[:, 1::2] << 4)
    layer = ServedLinear(32, 64, "nvfp4", {"weight": packed, "weight_scale": block_scale,
                                          "weight_scale_2": glob_scale.reshape(())})
    reference = dequantize_nvfp4(packed, block_scale, glob_scale, torch.float32)
    if (reference - dense).abs().max() > dense.abs().max() * 0.2:
        failures.append("synthetic NVFP4 round trip is far from the dense weights")
    x = torch.randn(3, 5, 32, requires_grad=True)
    y = layer(x)
    x_ref = x.detach().clone().requires_grad_(True)
    y_ref = F.linear(x_ref, reference)
    if not torch.allclose(y, y_ref, atol=1e-5):
        failures.append("ServedLinear forward differs from the dequantized dense layer")
    y.square().sum().backward()
    y_ref.square().sum().backward()
    if not torch.allclose(x.grad, x_ref.grad, atol=1e-5):
        failures.append("ServedLinear input gradient differs")
    if layer.weight.grad is not None:
        failures.append("the frozen served weight took a gradient")

    try:
        from peft import LoraConfig, get_peft_model
    except ImportError:
        print("  (peft not installed: the PEFT wrapping check is skipped)")
        return failures

    class Tiny(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.proj = layer

        def forward(self, inputs: torch.Tensor) -> torch.Tensor:
            return self.proj(inputs)

    wrapped = get_peft_model(Tiny(), LoraConfig(r=4, lora_alpha=8, target_modules=["proj"], lora_dropout=0.0))
    trainable = [name for name, p in wrapped.named_parameters() if p.requires_grad]
    if sorted(trainable) != ["base_model.model.proj.lora_A.default.weight", "base_model.model.proj.lora_B.default.weight"]:
        failures.append(f"PEFT trainable parameters are {trainable}")
    with torch.no_grad():
        wrapped.base_model.model.proj.lora_B.default.weight.normal_()
    out = wrapped(x.detach())
    out.sum().backward()
    if wrapped.base_model.model.proj.lora_A.default.weight.grad is None:
        failures.append("PEFT's LoRA A took no gradient through ServedLinear")
    print(f"  PEFT wraps ServedLinear: {type(wrapped.base_model.model.proj).__name__}, trainable {trainable}")
    return failures


def check_activation_rounding() -> List[str]:
    """The NVFP4 input rounding keeps every 16-block on the E2M1 grid of its own scale."""
    failures: List[str] = []
    x = torch.randn(4, 64) * 3
    scale = x.abs().max() / (FP8_E4M3_MAX * FP4_E2M1_MAX)
    y = fake_quant_nvfp4_input(x, scale)
    if (y - x).abs().max() > x.abs().max() * 0.25:
        failures.append("NVFP4 input rounding error is too large")
    y8 = fake_quant_fp8_input(x, x.abs().max() / FP8_E4M3_MAX)
    if (y8 - x).abs().max() > x.abs().max() * 0.07:
        failures.append("FP8 input rounding error is too large")
    return failures


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="decode served tensors on the CPU and check ServedLinear")
    check.add_argument("--snapshot", help="checkpoint directory (default: the local HF cache)")
    check.add_argument("--no-checkpoint", action="store_true", help="only the synthetic checks")
    args = parser.parse_args(argv)

    failures: List[str] = []
    if not args.no_checkpoint:
        snapshot = find_snapshot(args.snapshot)
        print(f"🚀 checkpoint {snapshot.parent.parent.name.replace('models--', '').replace('--', '/')}")
        failures += check_checkpoint(snapshot)
    print("🚀 ServedLinear on synthetic layers")
    failures += check_layer()
    failures += check_activation_rounding()
    for failure in failures:
        print(f"❌ {failure}")
    if not failures:
        print("✅ served-format decoding and the frozen layer check out")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
