"""The Qwen3.8-Flash-Next trial entry (docs/dev/flash-next-feasibility-2026-10-19.md).

Its recipe, the launch it builds, and the per-model load-transient figure that replaces the global
estimate in the start-up memory gate for this model only, with the PSI watchdog still guarding the load.
"""

import json
from pathlib import Path
from typing import ClassVar

import pytest

from dreamference.hardware import ModelMatrixRegistry, get_model_launch_overrides
from dreamference.hardware.hardware_telemetry import HardwareTelemetry
from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS, QWEN38_SGLANG_RECIPE
from dreamference.vllm_server import VLLMServerManager, vllm_server_manager
from dreamference.vllm_server.vllm_server_manager import (
    HOST_MEMORY_RESERVE_GB,
    LOAD_TRANSIENT_FRACTION,
    MIN_LOAD_TRANSIENT_GB,
)

FLASH_NEXT = "qwen3.8-flash-next-nvfp4-mtp"
REPO = "local-inference-lab/Qwen3.8-Flash-Next-NVFP4"
REVISION = "6c01ac378d22f6652c9f392c87700496eca8e5dc"
IMAGE = "vllm/vllm-openai@sha256:1ff6716fe240a922d0d518b8884261e25b0c02eda8e2ce90888e364978249cad"
# MemTotal of a GB10 in GiB, and the snapshot's size (106.4 GB).
GB10_TOTAL = 121.6
WEIGHTS = 99.1


def gb10(available: float):
    return classmethod(
        lambda cls: HardwareTelemetry(
            is_gb10=True,
            gpu_name="NVIDIA GB10",
            driver_version="0",
            arch="aarch64",
            total_unified_memory_gb=GB10_TOTAL,
            available_memory_gb=available,
            used_memory_gb=GB10_TOTAL - available,
            vram_gb=0.0,
        )
    )


# -- the entry -------------------------------------------------------------------------------------


def test_flash_next_is_a_vllm_trial_entry_and_not_the_default():
    assert DEFAULT_MODEL_ALIAS == "qwen3.8-27b-nvfp4-dflash2"
    spec = ModelMatrixRegistry.get_spec(FLASH_NEXT)
    recipe = get_model_launch_overrides(FLASH_NEXT)
    assert spec.hf_repo_id == REPO and spec.compatible_gb10 and spec.supports_vision
    assert recipe["engine"] == "vllm" and recipe["docker_image"] == IMAGE
    assert recipe["revision"] == REVISION
    # The served id maps back to the entry, which is how the server is asked what runs.
    assert ModelMatrixRegistry.key_for_served_id(REPO) == FLASH_NEXT
    # Self-declaring NVFP4: no --quantization; and "nvfp4" in the alias keeps the NVFP4 canary.
    assert ModelMatrixRegistry.declares_own_quantization(FLASH_NEXT) and "nvfp4" in FLASH_NEXT
    # Production's template patches, unchanged: Qwen's template is byte-identical.
    assert recipe["chat_template_patches"] == QWEN38_SGLANG_RECIPE["chat_template_patches"]
    assert ModelMatrixRegistry.swe_bench_max_parallel(FLASH_NEXT) == 1
    assert ModelMatrixRegistry.swe_bench_max_parallel(REPO) == 1
    assert ModelMatrixRegistry.swe_bench_max_parallel(DEFAULT_MODEL_ALIAS) is None


def test_the_entrys_memory_figures_are_coherent_with_the_gates():
    # Before the download the gate sizes the weights from min_memory_gb, so it has to fit the
    # arena; and the arena plus the container's own headroom has to stay under the host reserve,
    # or the cgroup cap (clamped there) leaves the process nothing outside its arena.
    spec = ModelMatrixRegistry.get_spec(FLASH_NEXT)
    recipe = get_model_launch_overrides(FLASH_NEXT)
    arena = GB10_TOTAL * recipe["gpu_memory_utilization"]
    assert spec.min_memory_gb < arena
    assert GB10_TOTAL - arena >= HOST_MEMORY_RESERVE_GB
    assert arena + 4.0 <= GB10_TOTAL - HOST_MEMORY_RESERVE_GB
    assert spec.min_memory_gb <= 0.95 * GB10_TOTAL  # HardwareManager's compatibility check


def test_the_launch_is_vllm_on_the_pinned_image_at_the_pinned_revision_with_mtp(monkeypatch):
    from dreamference.hardware.hardware_manager import HardwareManager

    monkeypatch.setattr(HardwareManager, "detect_gb10_hardware", gb10(115.0))
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "detect_image_vllm_version", lambda image: None)
    cmd = mgr.build_launch_command(model=FLASH_NEXT)

    assert cmd[cmd.index("--entrypoint") + 1 : cmd.index("--entrypoint") + 5] == [
        "vllm",
        IMAGE,
        "serve",
        REPO,
    ]
    assert cmd[cmd.index("--revision") + 1] == REVISION
    assert cmd[cmd.index("--tokenizer-revision") + 1] == REVISION
    assert json.loads(cmd[cmd.index("--speculative-config") + 1]) == {
        "method": "mtp",
        "num_speculative_tokens": 3,
    }
    assert cmd[cmd.index("--max-model-len") + 1] == "131072"
    assert cmd[cmd.index("--gpu-memory-utilization") + 1] == "0.86"
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "fp8"
    assert cmd[cmd.index("--max-num-batched-tokens") + 1] == "4096"
    assert cmd[cmd.index("--tool-call-parser") + 1] == "qwen3_coder"
    assert cmd[cmd.index("--reasoning-parser") + 1] == "qwen3"
    assert cmd[cmd.index("--mamba-cache-mode") + 1] == "align"
    assert "--quantization" not in cmd
    template = cmd[cmd.index("--chat-template") + 1]
    assert template.startswith("/root/.cache/dreamference/chat-templates/")
    # The cgroup cap leaves the process room outside its arena.
    cap = float(next(a for a in cmd if a.startswith("--memory=")).split("=")[1].rstrip("g"))
    assert cap >= GB10_TOTAL * 0.86 + 4.0


def test_a_vllm_recipe_without_a_revision_gets_no_revision_flags(monkeypatch):
    mgr = VLLMServerManager()
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "detect_image_vllm_version", lambda image: None)
    cmd = mgr.build_launch_command(model="qwen2.5-coder-32b")
    assert "--revision" not in cmd and "--chat-template" not in cmd


# -- the load-transient figure ---------------------------------------------------------------------


def test_a_recipes_measured_transient_replaces_the_global_estimate_for_that_model_only():
    flash, source = VLLMServerManager.load_transient_gb(FLASH_NEXT, WEIGHTS)
    assert flash == get_model_launch_overrides(FLASH_NEXT)["load_transient_gb"]
    assert "load_transient_gb" in source
    production, source = VLLMServerManager.load_transient_gb(DEFAULT_MODEL_ALIAS, 20.0)
    assert production == max(MIN_LOAD_TRANSIENT_GB, 20.0 * LOAD_TRANSIENT_FRACTION)
    assert "x weights" in source
    big, _ = VLLMServerManager.load_transient_gb("qwen2.5-coder-32b", 80.0)
    assert big == pytest.approx(80.0 * LOAD_TRANSIENT_FRACTION)


def test_a_measured_transient_is_still_floored(monkeypatch):
    recipe = {**get_model_launch_overrides(FLASH_NEXT), "load_transient_gb": 0.5}
    monkeypatch.setattr(
        ModelMatrixRegistry,
        "get_launch_overrides",
        classmethod(lambda cls, key, engine=None: dict(recipe)),
    )
    assert VLLMServerManager.load_transient_gb(FLASH_NEXT, WEIGHTS)[0] == MIN_LOAD_TRANSIENT_GB


class FakeWatchdog:
    started: ClassVar[list] = []

    def __init__(self, container_name, on_trip=None):
        self.container_name = container_name

    def start(self):
        FakeWatchdog.started.append(self.container_name)
        return True

    def stop(self):
        pass


@pytest.fixture
def start(monkeypatch):
    """`start_server` for Flash-Next up to the launch, on a GB10 with a given MemAvailable."""
    from dreamference.hardware.hardware_manager import HardwareManager

    mgr = VLLMServerManager()
    seen = {"host_safety": 0, "downloads": [], "launched": False}
    monkeypatch.setattr(mgr, "is_docker_available", lambda: True)
    monkeypatch.setattr(mgr, "ensure_docker_image", lambda image: True)
    monkeypatch.setattr(mgr, "_stop_index_scopes", lambda: None)
    monkeypatch.setattr(
        mgr, "check_host_safety", lambda: seen.__setitem__("host_safety", seen["host_safety"] + 1)
    )
    monkeypatch.setattr(mgr, "_evict_model_page_cache", lambda model: 0.0)
    monkeypatch.setattr(mgr, "_estimate_model_weights_gb", lambda model: WEIGHTS)
    monkeypatch.setattr(mgr, "_reset_stale_compile_cache", lambda *a, **k: False)
    monkeypatch.setattr(
        "dreamference.hardware.download_model",
        lambda *a, **k: seen["downloads"].append((a[0], k.get("revision"))) or True,
    )
    monkeypatch.setattr(
        "dreamference.vllm_server.chat_template_patcher.ChatTemplatePatcher.prepare",
        classmethod(lambda cls, *a: Path("/nonexistent/chat_template.jinja")),
    )
    monkeypatch.setattr(mgr, "build_launch_command", lambda **kwargs: ["docker", "run", IMAGE])
    monkeypatch.setattr(
        vllm_server_manager.subprocess, "run", lambda *a, **k: seen.__setitem__("launched", True)
    )
    monkeypatch.setattr(vllm_server_manager, "MemoryPressureWatchdog", FakeWatchdog)
    FakeWatchdog.started = []

    def go(available: float) -> dict:
        monkeypatch.setattr(HardwareManager, "detect_gb10_hardware", gb10(available))
        mgr.start_server(model=FLASH_NEXT, background=False, gate=False)
        return seen

    return go


def test_the_gate_admits_flash_next_on_its_own_figure_with_the_watchdog_on(start):
    # 0.86 x 121.6 = 104.6 GiB of arena plus the measured 8 GiB: 112.6 needed, 113.5 available.
    # The global estimate (0.15 x 99.1 = 14.9) would need 119.5, more than any GB10 has free.
    seen = start(113.5)
    assert seen["host_safety"] == 1
    assert (FLASH_NEXT, REVISION) in seen["downloads"]
    assert seen["launched"]
    assert FakeWatchdog.started == ["dreamference-vllm-8000"]


def test_the_gate_still_refuses_a_host_that_cannot_meet_the_models_own_figure(start, capsys):
    with pytest.raises(SystemExit):
        start(112.0)
    out = capsys.readouterr().out
    assert (
        "Not enough memory to start vLLM." in out
        and "this model's own figure, load_transient_gb" in out
    )


def test_without_the_override_the_global_estimate_refuses_the_same_host(start, monkeypatch, capsys):
    recipe = {
        k: v for k, v in get_model_launch_overrides(FLASH_NEXT).items() if k != "load_transient_gb"
    }
    real = ModelMatrixRegistry.get_launch_overrides.__func__
    monkeypatch.setattr(
        ModelMatrixRegistry,
        "get_launch_overrides",
        classmethod(
            lambda cls, key, engine=None: (
                dict(recipe)
                if cls.get_spec(key) is cls.MATRIX[FLASH_NEXT]
                else real(cls, key, engine)
            )
        ),
    )
    with pytest.raises(SystemExit):
        start(113.5)
    assert f"{LOAD_TRANSIENT_FRACTION:.2f} x weights" in capsys.readouterr().out


# -- the smoke boot's long-prompt probe -------------------------------------------------------------


def load_probe():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "long_prompt_probe.py"
    spec = importlib.util.spec_from_file_location("long_prompt_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_server():
    """A model server stand-in: 4 characters a token, a needle it finds, and a switch for drift."""
    import http.server
    import re
    import threading

    state = {"calls": 0, "drift_over": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply({"data": [{"id": REPO, "max_model_len": 131072}]})

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            content = request["messages"][0]["content"]
            tokens = len(content) // 4
            state["calls"] += 1
            needle = re.search(r"vault code is (\d{7})", content)
            if needle:
                text = needle.group(1)
            elif state["drift_over"] and tokens > state["drift_over"]:
                text = f"record 999999: drift {state['calls']}"
            else:
                text = "record 000001: the brisk barrel moved to bay 037."
            self.reply(
                {
                    "choices": [{"message": {"content": text}}],
                    "usage": {"prompt_tokens": tokens, "completion_tokens": 12},
                }
            )

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", state
    server.shutdown()


def test_the_probe_passes_identical_answers_and_finds_the_needle(fake_server, tmp_path):
    host, _ = fake_server
    probe = load_probe()
    assert (
        probe.main(
            ["determinism", "--host", host, "--runs", "3", "--out", str(tmp_path / "d.json")]
        )
        == 0
    )
    result = json.loads((tmp_path / "d.json").read_text())
    assert (
        result["cases"]["long"]["prompt_tokens"]
        > 2048
        > result["cases"]["control"]["prompt_tokens"]
    )
    assert probe.main(["needle", "--host", host, "--tokens", "20000", "--depths", "0.5"]) == 0
    # Beyond the served context is a skip, which does not pass.
    assert probe.main(["needle", "--host", host, "--tokens", "200000"]) == 1


def test_the_probe_fails_when_long_prompts_drift(fake_server, tmp_path):
    host, state = fake_server
    state["drift_over"] = 2048
    probe = load_probe()
    assert (
        probe.main(
            ["determinism", "--host", host, "--runs", "3", "--out", str(tmp_path / "d.json")]
        )
        == 1
    )
    result = json.loads((tmp_path / "d.json").read_text())
    assert result["cases"]["control"]["passed"] and not result["cases"]["long"]["identical"]
    assert (
        probe.corrupted("!" * 50) and probe.corrupted("") and probe.corrupted("fine text") is None
    )
