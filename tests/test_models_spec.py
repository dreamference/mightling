"""specs/DREAMFERENCE_MODELS.md, checked against the registry, the launch manager and detection."""

import re
import subprocess
from pathlib import Path

import jinja2
import pytest

from dreamference.hardware import hardware_manager
from dreamference.hardware.hardware_manager import HardwareManager
from dreamference.hardware.model_matrix_registry import (
    QWEN38_SGLANG_IMAGE,
    QWEN38_SGLANG_RECIPE,
    ModelMatrixRegistry,
)
from dreamference.vllm_server import VLLMServerManager

REPO = Path(__file__).resolve().parent.parent


def test_the_recipe_runs_the_spec_s_extra_args_in_order():
    # §1 "The recipe": every flag the spec lists, and no other.
    assert QWEN38_SGLANG_RECIPE["extra_args"] == [
        "--attention-backend", "flashinfer",
        "--sampling-backend", "pytorch",
        "--chunked-prefill-size", "8192",
        "--disable-prefill-cuda-graph",
        "--cuda-graph-max-bs-decode", "8",
        "--disable-flashinfer-autotune",
        "--mamba-radix-cache-strategy", "extra_buffer",
        "--mamba-ssm-dtype", "bfloat16",
        "--max-mamba-cache-size", "96",
        "--max-running-requests", "8",
        "--enable-torch-compile",
        "--torch-compile-max-bs", "4",
        "--num-continuous-decode-steps", "2",
        "--sleep-on-idle",
        "--enable-metrics",
    ]  # fmt: skip


def test_the_recipe_s_engine_context_memory_parsers_and_drafter_are_the_spec_s():
    recipe = ModelMatrixRegistry.get_launch_overrides("qwen3.8-27b-nvfp4-dflash2")
    assert recipe["engine"] == "sglang"
    assert re.fullmatch(r"lmsysorg/sglang@sha256:[0-9a-f]{64}", QWEN38_SGLANG_IMAGE)
    assert recipe["docker_image"] == QWEN38_SGLANG_IMAGE
    assert recipe["revision"].startswith("52d1adc5")
    assert recipe["max_model_len"] == 262144
    assert recipe["gpu_memory_utilization"] == 0.50
    assert recipe["container_headroom_gb"] == 24
    assert (recipe["tool_call_parser"], recipe["reasoning_parser"]) == ("qwen3_coder", "qwen3")
    spec = recipe["speculative_config"]
    assert (spec["method"], spec["quantization"], spec["num_speculative_tokens"]) == (
        "DFLASH",
        "modelopt_fp4",
        12,
    )
    assert spec["model"] == "dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch"
    assert len(recipe["runtime_patches"]) == 2 and len(recipe["chat_template_patches"]) == 2


@pytest.mark.parametrize(
    ("asked", "resolved"),
    [(None, "medium"), ("max", "xhigh"), ("high", "xhigh"), ("xhigh", "xhigh"), ("medium", "medium"),
     ("minimal", "low"), ("low", "low"), ("none", "rejected")],
)  # fmt: skip
def test_the_effort_patch_defaults_to_medium_and_maps_codex_s_efforts(asked, resolved):
    # §1: the default drops from xhigh to medium, max/high map to xhigh and minimal to low.
    anchor, replacement = QWEN38_SGLANG_RECIPE["chat_template_patches"][0]
    assert "default('xhigh')" in anchor
    template = jinja2.Environment().from_string(
        replacement + "rejected{%- else %}{{ resolved_reasoning_effort }}{%- endif %}"
    )
    variables = {} if asked is None else {"reasoning_effort": asked}
    assert template.render(**variables).strip() == resolved


def test_an_alias_outside_the_matrix_has_no_recipe_but_a_parser_by_name():
    # §1 "Aliases outside the matrix": no recipe, a parser guessed from the name.
    assert ModelMatrixRegistry.get_spec("qwen2.5-coder-32b") is None
    assert ModelMatrixRegistry.removed_in("qwen2.5-coder-32b") is None
    assert ModelMatrixRegistry.get_launch_overrides("qwen2.5-coder-32b") == {}
    mgr = VLLMServerManager()
    assert mgr.resolve_tool_call_parser("qwen2.5-coder-32b") == "hermes"
    assert mgr.resolve_tool_call_parser("mistralai/Mistral-Small-24B-Instruct") == "mistral"


@pytest.fixture
def no_driver(tmp_path, monkeypatch):
    """A machine with no nvidia-smi, an empty PCI bus and DMI under tmp_path."""
    monkeypatch.setattr(hardware_manager, "PCI_DEVICES_DIR", str(tmp_path / "pci"))
    monkeypatch.setattr(hardware_manager, "DMI_DIR", str(tmp_path / "dmi"))
    monkeypatch.setattr(hardware_manager, "DGX_RELEASE", str(tmp_path / "dgx-release"))
    monkeypatch.setattr(hardware_manager, "OS_RELEASE", str(tmp_path / "os-release"))
    monkeypatch.setattr(hardware_manager.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        HardwareManager,
        "get_system_memory",
        classmethod(
            lambda cls: hardware_manager.MemoryMetrics(
                total_gb=128.0, available_gb=100.0, used_gb=28.0
            )
        ),
    )
    (tmp_path / "pci").mkdir()
    return tmp_path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_a_gb10_on_the_bus_without_a_driver_is_named_as_the_spec_says(no_driver):
    # §4: the GPU is then reported as "NVIDIA GB10 (no driver answering: ...)".
    _write(no_driver / "pci/000f:01:00.0/vendor", "0x10de\n")
    _write(no_driver / "pci/000f:01:00.0/device", "0x2e12\n")
    hw = HardwareManager.detect_gb10_hardware()
    assert hw.is_gb10 is True
    assert hw.gpu_name == "NVIDIA GB10 (no driver answering: nvidia-smi is missing or failed)"


@pytest.mark.parametrize("product", ["GX10", "Dell Pro Max with GB10 FCM1253", "DGX Spark"])
def test_the_vendor_s_dmi_name_never_qualifies(no_driver, monkeypatch, product):
    # §4: DMI strings are never used, and there is no environment override.
    _write(no_driver / "dmi/product_name", product + "\n")
    _write(no_driver / "dgx-release", 'DGX_NAME="DGX Spark"\nDGX_OTA_VERSION="7.5.0"\n')
    monkeypatch.setenv("DREAMFERENCE_GB10_OVERRIDE", "1")
    hw = HardwareManager.detect_gb10_hardware()
    assert hw.machine == product
    assert hw.is_gb10 is False


def test_nothing_reads_the_gb10_override_variable():
    # §4: "The DREAMFERENCE_GB10_OVERRIDE variable ... does not exist in the code."
    found = subprocess.run(
        ["git", "grep", "-l", "GB10_OVERRIDE", "--", "dreamference", "scripts"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert found.returncode == 1, found.stdout
