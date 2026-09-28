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

# The model card's sampler recipe for the Tiny-A2D bd3lm checkpoints. Applied on the first
# attempt; a checkpoint whose remote-code generate() has a different signature gets a second
# attempt with bare max_new_tokens (see DiffusionModelRunner.generate).
DIFFUSION_SAMPLER_DEFAULTS: Final[Dict[str, Any]] = {
    "steps": 128,
    "block_size": 32,
    "temperature": 0.0,
    "cfg_scale": 0.0,
    "remasking": "low_confidence",
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

        trust_remote_code is required: the bd3lm checkpoints ship their own modeling and
        generation code, and AutoModelForMaskedLM is the auto-class their config declares.
        """
        try:
            import torch
            from transformers import AutoModelForMaskedLM, AutoTokenizer

            self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True)
            model = AutoModelForMaskedLM.from_pretrained(
                self.model_id, dtype=torch.bfloat16, trust_remote_code=True
            )
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
        Runs one diffusion generation and returns the completion text.

        The first attempt passes the model card's sampler kwargs; remote code that does not
        recognise them raises TypeError, and the retry passes only max_new_tokens so a
        differently-shaped generate() still produces text.

        Args:
            prompt (str): Fully templated prompt text.
            max_new_tokens (int): Completion length cap.
            temperature (float): Sampling temperature; 0.0 is deterministic.

        Returns:
            str: The generated completion, without the prompt.
        """
        import torch

        inputs = self.tokenizer(prompt, return_tensors="pt")
        input_ids = inputs["input_ids"].to(self.device)
        sampler = dict(DIFFUSION_SAMPLER_DEFAULTS)
        sampler["temperature"] = temperature
        # Serialised: generation holds the GPU and the model is not re-entrant.
        with self._lock, torch.no_grad():
            try:
                output = self.model.generate(
                    input_ids, max_new_tokens=max_new_tokens, **sampler
                )
            except TypeError:
                output = self.model.generate(input_ids, max_new_tokens=max_new_tokens)
        # generate() may hand back a batched tensor or a ModelOutput carrying one.
        sequences = getattr(output, "sequences", output)
        sequence = sequences[0] if getattr(sequences, "dim", lambda: 2)() > 1 else sequences
        completion_ids = sequence[input_ids.shape[-1]:]
        return self.tokenizer.decode(completion_ids, skip_special_tokens=True)

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
        server_version = "DreamferenceDiffusion/1.0"

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

    server = ThreadingHTTPServer(("0.0.0.0", port), build_handler(runner))
    print(f"[diffusion] serving {model_id} on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
