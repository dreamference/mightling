#!/usr/bin/env python3
"""LoRA training of a per-language adapter on one GB10, against the served base.

    python scripts/adapters/train_lora.py plan  scripts/adapters/configs/rust-r16-48k.toml
    python scripts/adapters/train_lora.py train scripts/adapters/configs/rust-r16-48k.toml

`plan` reads the config and the samples and prints the memory and time estimates; it needs no GPU
and imports neither torch nor transformers. `train` runs the loop:

- the base is `served_base.load_served_text_model` (`base = "served"`: the served FP8/NVFP4 bytes,
  dequantized inside each matmul, about 21 GB; optional served activation rounding), or
  `base = "nf4"` (bitsandbytes QLoRA of the original BF16 checkpoint, the A/B arm);
- LoRA through PEFT on `adapter_format.TARGET_MODULES` (the layers SGLang can serve);
- one trajectory per micro-step (no padding, no packing: the GDN layers of transformers' model do
  not support packed sequences), `grad_accum` trajectories per optimizer step, the loss summed over
  assistant tokens and divided by the step's assistant-token count;
- gradient checkpointing on every decoder layer, and the cross-entropy over the 248,320-token
  vocabulary in recomputed chunks, so no full logits tensor exists;
- a memory-pressure guard: it refuses to start beside a resident model server or with too little
  available memory, and stops itself when the kernel's memory PSI says the host is stalling
  (unified memory freezes the host rather than raising an out-of-memory error).

The adapter is saved in PEFT format every `save_every` steps and at the end, and exported for SGLang
(`adapter_format.export_dir`). Nothing runs at import.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent

# Layer and vocabulary sizes of Qwen3.8-27B's text model (config.json's text_config).
HIDDEN: int = 5120
LAYERS: int = 64
FULL_ATTENTION_LAYERS: int = 16
INTERMEDIATE: int = 17408
VOCAB: int = 248320
PARAMS: float = 26.9e9
ATTENTION_WIDTH: int = 24 * 256  # query heads x head dim of the 16 full-attention layers


# -- config and estimates ---------------------------------------------------------------------------

def load_config(path: Path) -> Dict[str, Any]:
    config = tomllib.loads(path.read_text())
    config["_path"] = str(path)
    return config


def sample_lengths(path: Path, limit: Optional[int] = None) -> List[int]:
    """Token counts of the samples (reads only the `tokens` field)."""
    lengths: List[int] = []
    with open(path) as handle:
        for line in handle:
            lengths.append(json.loads(line)["tokens"])
            if limit and len(lengths) >= limit:
                break
    return lengths


def memory_estimate_gb(seq: int, rank: int, base: str, loss_chunk: int = 2048) -> Dict[str, float]:
    """
    A bottom-up estimate of peak memory for one micro-step at `seq` tokens, batch 1.

    Base: served bytes 21.0 GB (FP8 attention + NVFP4 MLP + BF16 rest) plus a BF16 lm_head for
    the loss; nf4 about 16 GB plus the BF16 embeddings and head. Activations: one BF16 hidden
    state per layer kept by checkpointing, plus one layer's recompute (the MLP's four
    `seq x intermediate` tensors and their gradients dominate). LoRA: FP32 weights, gradients and
    two Adam moments.
    """
    gb = 1e9
    base_gb = {"served": 21.0 + 2.5, "nf4": 16.0 + 5.1, "bf16": 53.8}[base]
    lora_params = rank * 6.8e6
    checkpoints = seq * HIDDEN * 2 * LAYERS / gb
    recompute = seq * INTERMEDIATE * 2 * 6 / gb + seq * HIDDEN * 2 * 8 / gb
    loss = loss_chunk * VOCAB * 4 * 2 / gb
    lora = lora_params * 16 / gb
    runtime = 6.0
    total = base_gb + checkpoints + recompute + loss + lora + runtime
    return {"base": base_gb, "checkpoints": checkpoints, "recompute": recompute, "loss_chunk": loss,
            "lora_and_adam": lora, "runtime": runtime, "total": total}


def flops_per_token(seq: int) -> float:
    """
    Training FLOPs per token of a frozen-base LoRA step with full recomputation.

    Matmuls: forward 2N, recompute 2N, input gradients 2N (frozen weights take no weight
    gradient), so 6N; the LoRA branches add about 1%. Full attention: 4 x position x width per
    layer forward, three passes, averaged over positions (seq / 2).
    """
    attention = FULL_ATTENTION_LAYERS * 4 * (seq / 2) * ATTENTION_WIDTH * 3
    return 6 * PARAMS * 1.01 + attention


def plan(config: Dict[str, Any]) -> None:
    train = config["train"]
    seq, rank, base = train["max_seq_len"], config["lora"]["rank"], train["base"]
    print(f"🚀 {config['name']}: base {base}, rank {rank}, max_seq_len {seq}")
    memory = memory_estimate_gb(seq, rank, base, train["loss_chunk"])
    print("  memory at max_seq_len: " + ", ".join(f"{k} {v:.1f}" for k, v in memory.items()) + " GB")
    samples = Path(os.path.expanduser(train["samples"]))
    if samples.exists():
        lengths = sample_lengths(samples)
    else:
        lengths = [int(x) for x in config["estimate"]["assumed_lengths"]] * int(config["estimate"]["assumed_samples"])
        print(f"  ({samples} does not exist yet: assuming {config['estimate']['assumed_samples']} samples "
              f"x {config['estimate']['assumed_lengths']} tokens)")
    lengths = [min(n, seq) for n in lengths]
    tokens = sum(lengths) * train["epochs"]
    flops = sum(flops_per_token(n) * n for n in lengths) * train["epochs"]
    for tflops in config["estimate"]["effective_tflops"]:
        hours = flops / (tflops * 1e12) / 3600
        print(f"  {len(lengths)} samples x {train['epochs']} epochs = {tokens / 1e6:.1f}M tokens; at "
              f"{tflops} effective TFLOPS: {tokens / (flops / (tflops * 1e12)):.0f} tok/s, {hours:.1f} h")
    steps = math.ceil(len(lengths) * train["epochs"] / train["grad_accum"])
    print(f"  optimizer steps: {steps} ({train['grad_accum']} trajectories each)")


# -- host safety -------------------------------------------------------------------------------------

def refuse_unsafe_host(min_available_gb: float) -> None:
    """Refuses to start beside a model server, or with too little memory available."""
    try:
        names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True,
                               timeout=20).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        names = []
    servers = [n for n in names if n.startswith("dreamference-vllm") or n.startswith("m0-prod")]
    if servers:
        raise SystemExit(f"❌ a model server is running ({', '.join(servers)}): training needs the whole "
                         "machine; stop it first (ling-admin server stop)")
    available = 0.0
    with open("/proc/meminfo") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                available = int(line.split()[1]) / 1e6
    if available < min_available_gb:
        raise SystemExit(f"❌ {available:.0f} GB available, the run needs {min_available_gb:.0f} GB")


def start_psi_guard(limit_full_avg10: float) -> None:
    """Exits the process when the host's memory `full` pressure (avg10) passes the limit."""
    path = "/proc/pressure/memory"
    if not os.path.exists(path):
        print("⚠️  no /proc/pressure/memory: the PSI guard is off")
        return

    def watch() -> None:
        while True:
            with open(path) as handle:
                for line in handle:
                    if line.startswith("full"):
                        avg10 = float(line.split()[1].split("=")[1])
                        if avg10 > limit_full_avg10:
                            print(f"❌ memory pressure full avg10={avg10} > {limit_full_avg10}: stopping",
                                  flush=True)
                            os._exit(3)
            time.sleep(1.0)

    threading.Thread(target=watch, daemon=True).start()


# -- training ----------------------------------------------------------------------------------------

def chunked_cross_entropy(hidden: Any, weight: Any, labels: Any, chunk: int) -> Any:
    """Summed next-token cross-entropy, logits built and freed one recomputed chunk at a time."""
    import torch
    import torch.nn.functional as F
    from torch.utils.checkpoint import checkpoint

    def chunk_loss(h: Any, y: Any) -> Any:
        logits = (h @ weight.t()).float()
        return F.cross_entropy(logits, y, ignore_index=-100, reduction="sum")

    total = hidden.new_zeros((), dtype=torch.float32)
    for start in range(0, hidden.shape[0], chunk):
        y = labels[start:start + chunk]
        if bool((y != -100).any()):
            total = total + checkpoint(chunk_loss, hidden[start:start + chunk], y, use_reentrant=False)
    return total


def build_model(config: Dict[str, Any]) -> Any:
    import torch
    from peft import LoraConfig, get_peft_model

    sys.path.insert(0, str(HERE))
    from adapter_format import lora_config_kwargs
    from served_base import find_snapshot, load_served_text_model

    train = config["train"]
    if train["base"] == "served":
        snapshot = find_snapshot(train.get("snapshot"))
        model = load_served_text_model(snapshot, "cuda", fake_quant_inputs=train.get("fake_quant_inputs", True))
        head = model.lm_head.dequantized(torch.bfloat16)
    elif train["base"] == "nf4":
        from transformers import AutoModelForCausalLM, BitsAndBytesConfig

        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                                   bnb_4bit_compute_dtype=torch.bfloat16)
        model = AutoModelForCausalLM.from_pretrained(train["original"], quantization_config=quant,
                                                     dtype=torch.bfloat16, device_map={"": 0})
        head = model.lm_head.weight
    else:
        raise SystemExit(f"❌ unknown base {train['base']}")
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    lora = config["lora"]
    model = get_peft_model(model, LoraConfig(**lora_config_kwargs(lora["rank"], lora.get("alpha"),
                                                                  lora.get("dropout", 0.0))))
    model.print_trainable_parameters()
    return model, head.detach()


def check_kernels(config: Dict[str, Any]) -> None:
    """Refuses the pure-PyTorch Gated DeltaNet fallback, which is slow and silent."""
    import importlib.util

    if importlib.util.find_spec("fla") is None and not config["train"].get("allow_torch_gdn", False):
        raise SystemExit("❌ flash-linear-attention (`fla`) is not installed: transformers would run the GDN "
                         "layers in its PyTorch loop (about 6x slower on GB10). Install it or set allow_torch_gdn")
    if importlib.util.find_spec("causal_conv1d") is None:
        print("⚠️  causal-conv1d not installed: the conv falls back to PyTorch (a few % of step time)")


def train(config: Dict[str, Any]) -> None:
    import torch

    t = config["train"]
    refuse_unsafe_host(memory_estimate_gb(t["max_seq_len"], config["lora"]["rank"], t["base"], t["loss_chunk"])["total"] + 8)
    start_psi_guard(t.get("psi_full_avg10_limit", 20.0))
    check_kernels(config)
    torch.manual_seed(t["seed"])
    random.seed(t["seed"])

    samples_path = Path(os.path.expanduser(t["samples"]))
    samples = [json.loads(line) for line in open(samples_path)]
    samples = [s for s in samples if s["trained"] > 0]
    random.shuffle(samples)
    n_val = max(1, int(len(samples) * t["validation_share"]))
    validation, training = samples[:n_val], samples[n_val:]
    out = Path(os.path.expanduser(t["output_dir"]))
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(config, indent=2))
    (out / "validation.json").write_text(json.dumps([s.get("attempt") for s in validation]))

    model, head = build_model(config)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=t["learning_rate"], betas=(0.9, 0.999),
                                  weight_decay=t.get("weight_decay", 0.0))
    total_steps = math.ceil(len(training) * t["epochs"] / t["grad_accum"])
    warmup = max(1, int(total_steps * t["warmup_share"]))

    def lr_at(step: int) -> float:
        if step < warmup:
            return t["learning_rate"] * (step + 1) / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        return t["learning_rate"] * (t["min_lr_share"] + (1 - t["min_lr_share"]) * 0.5 * (1 + math.cos(math.pi * progress)))

    def loss_of(sample: Dict[str, Any]) -> Any:
        ids = torch.tensor(sample["input_ids"][:t["max_seq_len"]], device="cuda").unsqueeze(0)
        labels = torch.tensor(sample["labels"][:t["max_seq_len"]], device="cuda")
        shifted = torch.cat([labels[1:], labels.new_full((1,), -100)])
        with torch.autocast("cuda", dtype=torch.bfloat16):
            hidden = model.base_model.model.model(input_ids=ids).last_hidden_state[0]
        return chunked_cross_entropy(hidden, head, shifted, t["loss_chunk"]), int((shifted != -100).sum())

    order = [s for _ in range(t["epochs"]) for s in random.sample(training, len(training))]
    log = open(out / "metrics.jsonl", "a")
    step = 0
    for group_start in range(0, len(order), t["grad_accum"]):
        group = order[group_start:group_start + t["grad_accum"]]
        trained = sum(min(s["trained"], t["max_seq_len"]) for s in group)
        began, loss_sum, tokens = time.time(), 0.0, 0
        model.train()
        for sample in group:
            loss, count = loss_of(sample)
            (loss / max(1, trained)).backward()
            loss_sum += float(loss)
            tokens += min(sample["tokens"], t["max_seq_len"])
        norm = torch.nn.utils.clip_grad_norm_(params, t["max_grad_norm"])
        for g in optimizer.param_groups:
            g["lr"] = lr_at(step)
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        step += 1
        elapsed = time.time() - began
        record = {"step": step, "of": total_steps, "loss": loss_sum / max(1, trained), "lr": lr_at(step - 1),
                  "grad_norm": float(norm), "tokens": tokens, "tok_s": tokens / elapsed,
                  "peak_gb": torch.cuda.max_memory_allocated() / 1e9}
        if step % t["eval_every"] == 0 or step == total_steps:
            model.eval()
            with torch.no_grad():
                pairs = [loss_of(s) for s in validation]
            record["val_loss"] = sum(float(l) for l, _ in pairs) / max(1, sum(c for _, c in pairs))
        print(json.dumps(record), flush=True)
        log.write(json.dumps(record) + "\n")
        log.flush()
        if step % t["save_every"] == 0 or step == total_steps:
            model.save_pretrained(str(out / f"step-{step}"))

    sys.path.insert(0, str(HERE))
    from adapter_format import export_dir
    from served_base import find_snapshot

    final = out / f"step-{step}"
    text_config = json.loads((find_snapshot(t.get("snapshot")) / "config.json").read_text())["text_config"]
    export_dir(final, out / "sglang", text_config)
    print(f"✅ adapter {final}, SGLang export {out / 'sglang'}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["plan", "train"])
    parser.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "plan":
        plan(config)
    else:
        train(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
