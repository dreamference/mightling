"""
OpenAI-compatible HTTP service for diffusion language models.

This file is executed *inside* the diffusion sidecar container, not on the host: vLLM cannot
serve a block-diffusion checkpoint, so `DiffusionServerManager` bind-mounts this module into the
vLLM image (which carries the torch + transformers build for this architecture) and runs it as
the container entrypoint. Like `image_search_service`, it lives in two worlds — imported by
Dreamference on the host (where torch may be absent, so nothing heavy is imported at module
level) and run as ``__main__`` in the container.

The HTTP surface is the subset of the OpenAI API the agents and Onyx actually call:
``/health``, ``/v1/models``, ``/v1/completions`` and ``/v1/chat/completions``, non-streaming.
The model loads on a background thread so ``/health`` can report progress (503 with the error
text until the load finishes — the transformers version in the image vs. what the checkpoint's
remote code expects is not verifiable from the host, and this endpoint is where a mismatch
surfaces).
"""

import ast
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Final, List, Optional

SERVICE_PORT: Final[int] = 8001

MODEL_ID_ENV: Final[str] = "DREAMFERENCE_DIFFUSION_MODEL_ID"
PORT_ENV: Final[str] = "DREAMFERENCE_DIFFUSION_PORT"

# The container shares the host's network, so the address bound here is the host's. It used to be
# 0.0.0.0, which offered the model to every machine on the LAN; nothing outside this machine uses
# it, and nothing inside a container does either.
BIND_ADDRESS: Final[str] = "127.0.0.1"

# The model card's sampler recipe for the Tiny-A2D bd3lm checkpoints: `steps` denoising steps
# spread over `max_new_tokens`, in blocks of `block_size`, each step committing the masked
# positions the model is most confident about ("low_confidence" remasking). The recipe's
# `cfg_scale` is 0, i.e. no classifier-free guidance, so the sampler has none.
DIFFUSION_SAMPLER_DEFAULTS: Final[Dict[str, Any]] = {
    "steps": 128,
    "block_size": 32,
    "temperature": 0.0,
}

DEFAULT_MAX_NEW_TOKENS: Final[int] = 256
MAX_MAX_NEW_TOKENS: Final[int] = 2048


class DiffusionModelRunner:
    """
    Loads a diffusion checkpoint through transformers and generates from it.

    One instance per process, created by ``main()``. The load happens in :meth:`load` on a
    background thread; until it finishes, :attr:`ready` is False and :attr:`error` carries the
    failure text if it failed.
    """

    def __init__(self, model_id: str):
        """
        Args:
            model_id (str): HuggingFace repository ID of the diffusion checkpoint.
        """
        self.model_id = model_id
        self.model: Any = None
        self.tokenizer: Any = None
        self.ready: bool = False
        self.error: Optional[str] = None
        self.device: str = "cpu"
        self._lock = threading.Lock()

    def load(self) -> None:
        """
        Loads tokenizer and model, preferring CUDA and falling back to CPU.

        trust_remote_code is required: the bd3lm checkpoints ship their own modeling code, and
        AutoModelForMaskedLM is the auto-class their config declares. Two things stood between
        that code and this image, and until 2026-09-29 the sidecar never produced a token:

        * transformers refuses remote code that imports a package it cannot find, scanning the
          file as text. The Tiny-A2D modeling file imports `dllm` only under
          ``if __name__ == "__main__":`` -- never at load -- and was refused for it.
        * Its forward reads ``decoder_layer.attention_type``, which this transformers keeps in
          ``config.layer_types`` instead.
        """
        try:
            import torch
            from transformers import AutoModelForMaskedLM, AutoTokenizer, dynamic_module_utils

            scan = dynamic_module_utils.get_imports

            def runtime_imports(filename: str) -> List[str]:
                needed = _imports_outside_main_guard(filename)
                return [name for name in scan(filename) if name in needed]

            dynamic_module_utils.get_imports = runtime_imports
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True)
                model = AutoModelForMaskedLM.from_pretrained(
                    self.model_id, dtype=torch.bfloat16, trust_remote_code=True
                )
            finally:
                dynamic_module_utils.get_imports = scan
            _restore_layer_attention_types(model)
            # CUDA is preferred but not assumed: a 0.6B model is serviceable on GB10's cores,
            # and a driver/arch mismatch should degrade the sidecar, not kill it.
            if torch.cuda.is_available():
                try:
                    model = model.to("cuda")
                    self.device = "cuda"
                except Exception:
                    self.device = "cpu"
            model.eval()
            self.model = model
            self.ready = True
        except Exception as exc:  # pragma: no cover - exercised only in the container
            self.error = f"{type(exc).__name__}: {exc}"

    def generate(self, prompt: str, max_new_tokens: int, temperature: float) -> str:
        """
        Runs one block-diffusion generation and returns the completion text.

        Not ``model.generate``: that is transformers' left-to-right decoder, which is the wrong
        algorithm for this checkpoint and fails inside its remote code besides.

        Args:
            prompt (str): Fully templated prompt text.
            max_new_tokens (int): Completion length cap.
            temperature (float): Sampling temperature; 0.0 is deterministic.

        Returns:
            str: The generated completion, without the prompt, cut at end-of-sequence.
        """
        inputs = self.tokenizer(prompt, return_tensors="pt")
        input_ids = inputs["input_ids"].to(self.device)
        # Serialised: generation holds the device and the model is not re-entrant.
        with self._lock:
            completion = block_diffusion_sample(
                self.model, input_ids,
                mask_id=self.tokenizer.mask_token_id, eos_id=self.tokenizer.eos_token_id,
                max_new_tokens=max_new_tokens, block_size=DIFFUSION_SAMPLER_DEFAULTS["block_size"],
                steps=DIFFUSION_SAMPLER_DEFAULTS["steps"], temperature=temperature,
            )
        return self.tokenizer.decode(completion, skip_special_tokens=True)

    def chat(self, messages: List[Dict[str, Any]], max_new_tokens: int, temperature: float) -> str:
        """
        Applies the checkpoint's chat template and generates.

        Args:
            messages (List[Dict[str, Any]]): OpenAI-style message dicts.
            max_new_tokens (int): Completion length cap.
            temperature (float): Sampling temperature.

        Returns:
            str: The assistant reply text.
        """
        prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        return self.generate(prompt, max_new_tokens, temperature)


def _imports_outside_main_guard(filename: str) -> set:
    """
    Names the top-level packages a module imports anywhere but its ``__main__`` block.

    Args:
        filename (str): Path of a Python source file.

    Returns:
        set: Root package names, e.g. {"torch", "transformers"}.
    """
    with open(filename, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    def is_main_guard(node: ast.AST) -> bool:
        return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")

    names: set = set()
    pending = [node for node in tree.body if not is_main_guard(node)]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
        pending.extend(child for child in ast.iter_child_nodes(node) if not is_main_guard(child))
    return names


def _restore_layer_attention_types(model: Any) -> None:
    """
    Gives each decoder layer the ``attention_type`` older transformers set on it.

    Args:
        model (Any): A loaded model whose ``model.layers`` are decoder layers.
    """
    layers = getattr(getattr(model, "model", None), "layers", None) or []
    kinds = getattr(model.config, "layer_types", None) or ["full_attention"] * len(layers)
    for layer, kind in zip(layers, kinds):
        if not hasattr(layer, "attention_type"):
            layer.attention_type = kind


def block_diffusion_sample(
    model: Any, input_ids: Any, mask_id: int, eos_id: Optional[int], max_new_tokens: int,
    block_size: int, steps: int, temperature: float = 0.0,
) -> List[int]:
    """
    Generates by block diffusion: one block of mask tokens at a time, filled in over several steps.

    Each block starts as ``block_size`` mask tokens after everything so far. Every step predicts
    all of its masked positions at once and commits the ones the model is most sure of; the rest
    stay masked for the next step. The attention is **block-causal** -- the prompt is one block,
    each generated block sees itself and every block before it, never one after -- which is how
    BD3LM checkpoints are trained. (Full bidirectional attention also produced working code on
    the Tiny-A2D checkpoint, but slower and further from the training setup.) Generation stops
    at the first block containing end-of-sequence.

    Args:
        model (Any): A masked LM taking ``input_ids`` and a 4-D additive ``attention_mask``.
        input_ids (Any): Prompt token ids, shape (1, prompt_length).
        mask_id (int): The tokenizer's mask token id.
        eos_id (Optional[int]): End-of-sequence id; generation is cut there.
        max_new_tokens (int): Upper bound on generated tokens.
        block_size (int): Tokens per block.
        steps (int): Denoising steps across ``max_new_tokens``; each block gets its share.
        temperature (float): 0.0 takes the most likely token; above it, samples.

    Returns:
        List[int]: The generated token ids, without the prompt, cut before end-of-sequence.
    """
    import torch

    prompt_length = input_ids.shape[1]
    blocks = max(1, -(-max_new_tokens // block_size))
    steps_per_block = max(1, min(block_size, steps * block_size // max(max_new_tokens, 1)))
    x = input_ids
    with torch.no_grad():
        for _ in range(blocks):
            block = torch.full((1, block_size), mask_id, dtype=x.dtype, device=x.device)
            x = torch.cat([x, block], dim=1)
            attention = _block_causal_mask(x.shape[1], prompt_length, block_size, model.dtype, x.device)
            for step in range(steps_per_block):
                masked = x[0] == mask_id
                remaining = int(masked.sum())
                if remaining == 0:
                    break
                logits = model(input_ids=x, attention_mask=attention).logits[0].float()
                logits[:, mask_id] = float("-inf")  # never "predict" the mask itself
                if temperature > 0:
                    probabilities = (logits / temperature).softmax(-1)
                    predicted = torch.multinomial(probabilities, 1).squeeze(-1)
                    confidence = probabilities.gather(-1, predicted[:, None]).squeeze(-1)
                else:
                    confidence, predicted = logits.softmax(-1).max(-1)
                confidence[~masked] = -1.0
                # Commit an even share of what is left, so the block is complete on the last step.
                commit = -(-remaining // (steps_per_block - step))
                chosen = confidence.topk(commit).indices
                x[0, chosen] = predicted[chosen]
            if eos_id is not None and eos_id in x[0, -block_size:].tolist():
                break
    generated = x[0, prompt_length:].tolist()
    if eos_id is not None and eos_id in generated:
        generated = generated[:generated.index(eos_id)]
    return generated[:max_new_tokens]


def _block_causal_mask(length: int, prompt_length: int, block_size: int, dtype: Any, device: Any) -> Any:
    """
    Builds the additive attention mask for block-causal attention.

    Args:
        length (int): Sequence length.
        prompt_length (int): Tokens in the prompt, which form block 0.
        block_size (int): Tokens per generated block.
        dtype (Any): The model's dtype, for the mask's fill value.
        device (Any): Where the mask lives.

    Returns:
        Any: A (1, 1, length, length) tensor, 0 where attention is allowed.
    """
    import torch

    block_of = torch.zeros(length, dtype=torch.long, device=device)
    block_of[prompt_length:] = 1 + torch.arange(length - prompt_length, device=device) // block_size
    allowed = block_of[None, :] <= block_of[:, None]
    mask = torch.zeros(length, length, dtype=dtype, device=device)
    mask[~allowed] = torch.finfo(dtype).min
    return mask[None, None]


def _clamp_max_tokens(value: Any) -> int:
    """Coerces a request's max_tokens to a sane int within the service cap."""
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_NEW_TOKENS
    return max(1, min(tokens, MAX_MAX_NEW_TOKENS))


def build_handler(runner: "DiffusionModelRunner") -> type:
    """
    Builds the request handler class bound to a runner.

    Args:
        runner (DiffusionModelRunner): The loaded (or loading) model runner.

    Returns:
        type: A BaseHTTPRequestHandler subclass.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "PuffinDiffusion/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
            print(f"[diffusion] {self.address_string()} {fmt % args}", flush=True)

        def _json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                if runner.ready:
                    self._json(200, {"status": "ok", "model": runner.model_id, "device": runner.device})
                elif runner.error:
                    self._json(503, {"status": "error", "error": runner.error})
                else:
                    self._json(503, {"status": "loading", "model": runner.model_id})
            elif self.path == "/v1/models":
                self._json(200, {
                    "object": "list",
                    "data": [{"id": runner.model_id, "object": "model", "owned_by": "dreamference"}],
                })
            else:
                self._json(404, {"error": {"message": f"unknown path {self.path}"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/v1/completions", "/v1/chat/completions"):
                self._json(404, {"error": {"message": f"unknown path {self.path}"}})
                return
            if not runner.ready:
                message = runner.error or "model is still loading"
                self._json(503, {"error": {"message": message}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": {"message": "invalid JSON body"}})
                return

            max_tokens = _clamp_max_tokens(request.get("max_tokens", DEFAULT_MAX_NEW_TOKENS))
            try:
                temperature = float(request.get("temperature", 0.0))
            except (TypeError, ValueError):
                temperature = 0.0

            created = int(time.time())
            response_id = f"dfsn-{uuid.uuid4().hex}"
            try:
                if self.path == "/v1/chat/completions":
                    messages = request.get("messages") or []
                    text = runner.chat(messages, max_tokens, temperature)
                    self._json(200, {
                        "id": response_id,
                        "object": "chat.completion",
                        "created": created,
                        "model": runner.model_id,
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }],
                    })
                else:
                    prompt = request.get("prompt") or ""
                    if isinstance(prompt, list):
                        prompt = prompt[0] if prompt else ""
                    text = runner.generate(str(prompt), max_tokens, temperature)
                    self._json(200, {
                        "id": response_id,
                        "object": "text_completion",
                        "created": created,
                        "model": runner.model_id,
                        "choices": [{
                            "index": 0,
                            "text": text,
                            "finish_reason": "stop",
                        }],
                    })
            except Exception as exc:
                self._json(500, {"error": {"message": f"{type(exc).__name__}: {exc}"}})

    return Handler


def main() -> None:  # pragma: no cover - the container entrypoint
    """Loads the model on a background thread and serves until killed."""
    model_id = os.environ.get(MODEL_ID_ENV, "")
    if not model_id:
        raise SystemExit(f"{MODEL_ID_ENV} is not set")
    port = int(os.environ.get(PORT_ENV, str(SERVICE_PORT)))

    runner = DiffusionModelRunner(model_id)
    threading.Thread(target=runner.load, name="diffusion-load", daemon=True).start()

    server = ThreadingHTTPServer((BIND_ADDRESS, port), build_handler(runner))
    print(f"[diffusion] serving {model_id} on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
