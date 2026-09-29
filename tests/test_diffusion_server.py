"""Tests for the diffusion model sidecar: registry entry, manager, and serving script."""

from dreamference.hardware import (
    get_model_launch_overrides,
    model_is_diffusion,
    resolve_model_hf_repo,
)
from dreamference.hardware.model_matrix_registry import (
    DEFAULT_DIFFUSION_MODEL_ALIAS,
    DEFAULT_MODEL_ALIAS,
    ModelMatrixRegistry,
)
from dreamference.vllm_server import DiffusionServerManager, DEFAULT_VLLM_IMAGE
from dreamference.vllm_server.diffusion_server_manager import CONTAINER_SERVICE_PATH


def test_default_diffusion_model_is_registered_and_flagged():
    spec = ModelMatrixRegistry.get_spec(DEFAULT_DIFFUSION_MODEL_ALIAS)
    assert spec is not None
    assert spec.is_diffusion
    assert resolve_model_hf_repo(DEFAULT_DIFFUSION_MODEL_ALIAS) == (
        "dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1"
    )


def test_diffusion_flag_separates_the_two_model_roles():
    # The cross-refusals in `main-model set` / `diffusion-model set` ride on this predicate, so
    # both directions are pinned: the diffusion default is diffusion, the main default is not,
    # and an unknown key reports False (sent down the vLLM path, which can explain a bad load).
    assert model_is_diffusion(DEFAULT_DIFFUSION_MODEL_ALIAS)
    assert not model_is_diffusion(DEFAULT_MODEL_ALIAS)
    assert not model_is_diffusion("no-such-model")


def test_launch_command_hardens_the_container_and_names_the_model():
    cmd = DiffusionServerManager().build_launch_command(
        DEFAULT_DIFFUSION_MODEL_ALIAS, DEFAULT_MODEL_ALIAS, port=8001, hf_token="tok"
    )
    joined = " ".join(cmd)
    assert "--name dreamference-diffusion-8001" in joined
    assert "--gpus all" in joined
    assert "--network host" in joined
    # A fixed memory cap stands in for the PSI watchdog at this scale: swap equals memory so the
    # worst case is a contained OOM kill, never host memory pressure.
    assert "--memory=8g" in joined and "--memory-swap=8g" in joined
    assert "--oom-score-adj=800" in joined
    assert "/root/.cache/huggingface" in joined
    # The serving script is bind-mounted read-only and run as the entrypoint.
    assert f"diffusion_openai_service.py:{CONTAINER_SERVICE_PATH}:ro" in joined
    assert cmd[-1] == CONTAINER_SERVICE_PATH
    assert cmd[cmd.index("--entrypoint") + 1] == "python3"
    assert (
        "DREAMFERENCE_DIFFUSION_MODEL_ID="
        "dllm-collection/Qwen2.5-Coder-0.5B-Instruct-diffusion-bd3lm-v0.1" in joined
    )
    assert "DREAMFERENCE_DIFFUSION_PORT=8001" in joined
    assert "HF_TOKEN=tok" in joined


def test_sidecar_rides_in_the_main_models_image_not_the_buildable_default():
    # DEFAULT_VLLM_IMAGE is a bare tag that ensure_docker_image would *build*; on a machine whose
    # main model pins its own image, that default has plausibly never been built, and falling
    # back to it would trigger a surprise multi-gigabyte build to serve a 0.6B model. The main
    # model's image is present by construction wherever the main model serves.
    main_image = get_model_launch_overrides(DEFAULT_MODEL_ALIAS)["docker_image"]
    resolved = DiffusionServerManager.resolve_docker_image(
        DEFAULT_DIFFUSION_MODEL_ALIAS, DEFAULT_MODEL_ALIAS
    )
    assert resolved == main_image


def test_a_diffusion_model_may_pin_its_own_image(monkeypatch):
    spec = ModelMatrixRegistry.get_spec(DEFAULT_DIFFUSION_MODEL_ALIAS)
    monkeypatch.setattr(spec, "launch_overrides", {"docker_image": "custom-diffusion:1"})
    assert DiffusionServerManager.resolve_docker_image(
        DEFAULT_DIFFUSION_MODEL_ALIAS, DEFAULT_MODEL_ALIAS
    ) == "custom-diffusion:1"


def test_unpinned_main_model_falls_back_to_the_project_default_image():
    assert DiffusionServerManager.resolve_docker_image(
        DEFAULT_DIFFUSION_MODEL_ALIAS, "qwen2.5-coder-32b"
    ) == DEFAULT_VLLM_IMAGE


def test_service_module_imports_without_torch():
    # The serving script runs inside the container, where torch and transformers live, but it is
    # imported on the host too (this suite, and the manager resolving its path). Heavy imports
    # must stay inside methods — a module-level `import torch` would break every host import.
    import sys
    import importlib

    saved = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "torch" or name.startswith("torch.")
        or name == "transformers" or name.startswith("transformers.")
    }
    sys.modules["torch"] = None  # any lazy import attempt now fails loudly
    sys.modules["transformers"] = None
    try:
        sys.modules.pop("dreamference.vllm_server.diffusion_openai_service", None)
        module = importlib.import_module("dreamference.vllm_server.diffusion_openai_service")
        assert hasattr(module, "DiffusionModelRunner")
    finally:
        del sys.modules["torch"]
        del sys.modules["transformers"]
        sys.modules.update(saved)


def test_max_tokens_are_clamped_to_the_service_cap():
    from dreamference.vllm_server.diffusion_openai_service import (
        DEFAULT_MAX_NEW_TOKENS,
        MAX_MAX_NEW_TOKENS,
        _clamp_max_tokens,
    )

    assert _clamp_max_tokens(64) == 64
    assert _clamp_max_tokens(10 ** 9) == MAX_MAX_NEW_TOKENS
    assert _clamp_max_tokens(0) == 1
    assert _clamp_max_tokens(None) == DEFAULT_MAX_NEW_TOKENS
    assert _clamp_max_tokens("not-a-number") == DEFAULT_MAX_NEW_TOKENS


def _serve(runner):
    """Starts the service's real handler on an ephemeral port; returns (server, base_url)."""
    from http.server import ThreadingHTTPServer
    import threading

    from dreamference.vllm_server.diffusion_openai_service import build_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(runner))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class _StubRunner:
    """Stands in for DiffusionModelRunner without loading anything."""

    def __init__(self, ready=True, error=None):
        self.model_id = "stub/diffusion"
        self.device = "cpu"
        self.ready = ready
        self.error = error

    def generate(self, prompt, max_new_tokens, temperature):
        return f"gen:{prompt}"

    def chat(self, messages, max_new_tokens, temperature):
        return f"chat:{messages[-1]['content']}"


def test_health_reports_loading_then_error_then_ok():
    import requests

    loading = _StubRunner(ready=False)
    server, base = _serve(loading)
    try:
        resp = requests.get(f"{base}/health", timeout=5)
        assert resp.status_code == 503 and resp.json()["status"] == "loading"

        loading.error = "OSError: incompatible remote code"
        resp = requests.get(f"{base}/health", timeout=5)
        assert resp.status_code == 503 and "remote code" in resp.json()["error"]

        loading.ready = True
        assert requests.get(f"{base}/health", timeout=5).status_code == 200
    finally:
        server.shutdown()


def test_openai_routes_answer_completions_and_chat():
    import requests

    server, base = _serve(_StubRunner())
    try:
        models = requests.get(f"{base}/v1/models", timeout=5).json()
        assert models["data"][0]["id"] == "stub/diffusion"

        completion = requests.post(
            f"{base}/v1/completions", json={"prompt": "def add", "max_tokens": 8}, timeout=5
        ).json()
        assert completion["choices"][0]["text"] == "gen:def add"

        chat = requests.post(
            f"{base}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            timeout=5,
        ).json()
        assert chat["choices"][0]["message"]["content"] == "chat:hi"
    finally:
        server.shutdown()


def test_requests_are_refused_while_the_model_loads():
    import requests

    server, base = _serve(_StubRunner(ready=False))
    try:
        resp = requests.post(f"{base}/v1/completions", json={"prompt": "x"}, timeout=5)
        assert resp.status_code == 503
    finally:
        server.shutdown()


def test_block_diffusion_fills_blocks_most_confident_first_and_stops_at_eos():
    # The sidecar used to call transformers' left-to-right generate(), which is the wrong
    # algorithm for a block-diffusion checkpoint and failed inside its remote code: it had never
    # produced a token. This toy model "wants" the sequence 5 6 7 EOS and is surest of the
    # latest position, so the order of commitment is visible.
    import torch
    from dreamference.vllm_server.diffusion_openai_service import block_diffusion_sample

    MASK, EOS, VOCAB = 99, 9, 100
    target = [5, 6, 7, EOS, 1, 1, 1, 1]
    masks_seen = []

    class Toy(torch.nn.Module):
        dtype = torch.float32

        def forward(self, input_ids, attention_mask):
            masks_seen.append(attention_mask)
            n = input_ids.shape[1]
            logits = torch.zeros(1, n, VOCAB)
            for position in range(2, n):
                logits[0, position, target[position - 2]] = 1.0 + position  # later = surer
            return type("Out", (), {"logits": logits})()

    tokens = block_diffusion_sample(Toy(), torch.tensor([[3, 4]]), mask_id=MASK, eos_id=EOS,
                                    max_new_tokens=8, block_size=4, steps=8)
    assert tokens == [5, 6, 7]  # cut at EOS; the second block was never started
    mask = masks_seen[0][0, 0]
    assert mask.shape == (6, 6)
    assert (mask[:2, 2:] < 0).all()  # the prompt does not see the block being generated
    assert (mask[2:, :] == 0).all()  # the block sees the prompt and itself


def test_imports_under_the_main_guard_are_not_load_requirements(tmp_path):
    # The Tiny-A2D modeling file imports `dllm` only under `if __name__ == "__main__":`, and
    # transformers' text scan refused to load it for that.
    from dreamference.vllm_server.diffusion_openai_service import _imports_outside_main_guard

    module = tmp_path / "modeling.py"
    module.write_text(
        "import torch\nfrom transformers.utils import x\n"
        "def f():\n    import numpy\n"
        "if __name__ == '__main__':\n    import dllm\n    from dllm import utils\n"
    )
    assert _imports_outside_main_guard(str(module)) == {"torch", "transformers", "numpy"}
