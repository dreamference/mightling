import subprocess
import sys
from pathlib import Path

import pytest

from dreamference.hardware import (
    detect_gb10_hardware,
    get_system_memory,
    check_model_compatibility,
    MODEL_MATRIX
)

def test_system_memory():
    mem = get_system_memory()
    assert "total_gb" in mem
    assert "available_gb" in mem
    assert isinstance(mem["total_gb"], float)

def test_detect_gb10_hardware():
    hw = detect_gb10_hardware()
    assert "is_gb10" in hw
    assert "gpu_name" in hw
    assert "total_unified_memory_gb" in hw

def test_check_model_compatibility():
    # Any registered, GB10-compatible alias serves the purpose; the default is the only one.
    valid, msg = check_model_compatibility("qwen3.8-27b-nvfp4-dflash2")
    assert isinstance(valid, bool)
    assert isinstance(msg, str)

    # Unknown models are permissive by contract: the matrix no longer carries an
    # incompatible-by-design fixture (the deepseek entry left with the 2026-08-24 purge),
    # and an unregistered alias resolves to "unknown, caller beware", not a refusal.
    valid_unknown, msg_unknown = check_model_compatibility("deepseek-v3-671b")
    assert valid_unknown is True
    assert "unknown" in msg_unknown.lower()

def test_check_speculative_compatibility(monkeypatch):
    # The memory budget is checked against the host, so pin it to a GB10's 128 GB: on a 16 GB CI
    # runner the real check correctly refuses the pair, and the test is about the registry entries.
    from types import SimpleNamespace
    from dreamference.hardware import HardwareManager, check_speculative_compatibility
    monkeypatch.setattr(
        HardwareManager, "detect_gb10_hardware",
        classmethod(lambda cls: SimpleNamespace(total_unified_memory_gb=128.0)),
    )
    valid, msg = check_speculative_compatibility(
        "qwen3.8-27b-nvfp4-dflash2", "qwen3.8-27b-dflash2-draft")
    assert valid is True
    assert "Speculative Decoding Qualified" in msg or "Compatible" in msg

def test_default_model_is_registered_and_gb10_compatible():
    from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
    spec = MODEL_MATRIX[DEFAULT_MODEL_ALIAS]
    assert spec.compatible_gb10 is True
    assert spec.hf_repo_id == "RadixArk/Qwen3.8-27B-NVFP4"

def test_default_model_speculates_against_a_downloadable_drafter(vllm_recipes):
    # The DFlash drafter is named inside the recipe rather than passed as an argument, which is
    # the only reason start_server can see it at all: it has to be fetched before the load and
    # counted against memory alongside the target. A recipe that names a drafter the registry
    # cannot size would put a 0 GB drafter into the pre-flight gates.
    from dreamference.hardware import get_speculative_draft_repo, MODEL_MATRIX
    from dreamference.hardware.model_matrix_registry import (
        DEFAULT_MODEL_ALIAS,
        ModelMatrixRegistry,
    )
    draft_repo = get_speculative_draft_repo(DEFAULT_MODEL_ALIAS)
    assert draft_repo == "dreamference/qwen3.8-27b-dflash2-nvfp4-scalesearch"
    draft_spec = ModelMatrixRegistry.get_spec(draft_repo)
    assert draft_spec is not None
    assert draft_spec.min_memory_gb > 0

    spec = MODEL_MATRIX[DEFAULT_MODEL_ALIAS]
    assert spec.launch_overrides["speculative_config"]["method"].lower() == "dflash"  # SGLang spells it DFLASH
    # Self-speculating and non-speculating models must not report a separate drafter.
    assert get_speculative_draft_repo("test-vllm-nvfp4") is None
    assert get_speculative_draft_repo("qwen2.5-coder-32b") is None
    assert get_speculative_draft_repo("some/unknown-repo") is None

def test_default_model_weights_fit_its_own_arena():
    # The recipe's gpu_memory_utilization has a floor the flag itself does not express: vLLM sizes
    # the whole allocation, weights included, as a fraction of total memory, so an arena smaller
    # than the checkpoint is an incoherent budget that start_server aborts on. Guard the two ends
    # rather than the exact value, so retuning does not fail this.
    from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
    spec = MODEL_MATRIX[DEFAULT_MODEL_ALIAS]
    gb10_total_gb = 121.63
    arena_gb = gb10_total_gb * spec.launch_overrides["gpu_memory_utilization"]
    assert arena_gb > spec.min_memory_gb
    # And it has to leave the host its reserve, or the overshoot freezes the machine instead of
    # earning an OOM kill.
    from dreamference.vllm_server.vllm_server_manager import HOST_MEMORY_RESERVE_GB
    assert gb10_total_gb - arena_gb >= HOST_MEMORY_RESERVE_GB

def test_launch_overrides_are_isolated_per_call():
    from dreamference.hardware import get_model_launch_overrides
    from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS
    # Read the expected value from the registry rather than repeating it: this test is about
    # mutation isolation, and hardcoding the recipe's value makes it fail on every retune.
    original = MODEL_MATRIX[DEFAULT_MODEL_ALIAS].launch_overrides["max_model_len"]
    first = get_model_launch_overrides(DEFAULT_MODEL_ALIAS)
    first["max_model_len"] = 1
    assert get_model_launch_overrides(DEFAULT_MODEL_ALIAS)["max_model_len"] == original
    assert get_model_launch_overrides("qwen2.5-coder-32b") == {}
    assert get_model_launch_overrides("some/unknown-repo") == {}

def test_declares_own_quantization():
    from dreamference.hardware import model_declares_own_quantization
    assert model_declares_own_quantization("qwen3.8-27b-nvfp4-dflash2") is True
    assert model_declares_own_quantization("qwen2.5-coder-72b") is False
    assert model_declares_own_quantization("some/unknown-repo") is False

def test_flexible_model_alias_resolution():
    from dreamference.hardware import resolve_model_hf_repo, get_model_launch_overrides, model_declares_own_quantization
    from dreamference.hardware.model_matrix_registry import (
        ENGINE_LING,
        QWEN38_DEFAULT_ENGINE,
        QWEN38_LING_FLAT_CONTEXT,
        ModelMatrixRegistry,
    )

    aliases = [
        "qwen3.8-27b-nvfp4-dflash2",
        "RadixArk/Qwen3.8-27B-NVFP4",
        "Qwen 3.8 27B (NVFP4 + DFlash2, ling-serve)",
        "qwen 3.8 27b (nvfp4 + dflash2, ling-serve)",
    ]
    for name in aliases:
        spec = ModelMatrixRegistry.get_spec(name)
        assert spec is not None, f"Failed to get spec for {name}"
        assert spec.hf_repo_id == "RadixArk/Qwen3.8-27B-NVFP4"
        assert resolve_model_hf_repo(name) == "RadixArk/Qwen3.8-27B-NVFP4"
        # The default engine's context: ling-serve's four flat sessions at 1.8.0, SGLang's 262,144.
        assert get_model_launch_overrides(name).get("max_model_len") == (
            QWEN38_LING_FLAT_CONTEXT if QWEN38_DEFAULT_ENGINE == ENGINE_LING else 262144
        )
        assert model_declares_own_quantization(name) is True

def test_download_model_functions():
    from dreamference.hardware import is_model_downloaded, download_model, resolve_model_hf_repo
    repo = resolve_model_hf_repo("qwen3.8-27b-nvfp4-dflash2")
    assert repo == "RadixArk/Qwen3.8-27B-NVFP4"
    is_dl = is_model_downloaded("qwen3.8-27b-nvfp4-dflash2")
    assert isinstance(is_dl, bool)

def test_tensorizer_functions(tmp_path, monkeypatch):
    from dreamference.hardware import is_model_tensorized, get_tensorized_path, ModelDownloader
    
    # Test checking non-existent model tensorization
    assert is_model_tensorized("qwen2.5-coder-32b") is False or isinstance(is_model_tensorized("qwen2.5-coder-32b"), bool)

    # Test creating mock tensorized directory and verifying detection
    tdir = ModelDownloader.get_tensorized_dir("qwen2.5-coder-32b")
    assert tdir is not None
    tdir.mkdir(parents=True, exist_ok=True)
    tfile = tdir / "model.tensors"
    tfile.write_text("mock tensors data")

    try:
        assert is_model_tensorized("qwen2.5-coder-32b") is True
        tpath = get_tensorized_path("qwen2.5-coder-32b")
        assert tpath == tfile
    finally:
        # Cleanup mock file
        if tfile.exists():
            tfile.unlink()



def test_the_default_model_is_marked_vision_capable():
    # Clients gate image upload on this, so a wrong value here is the difference between an
    # upload working and the UI refusing it.
    from dreamference.hardware import model_supports_vision

    assert model_supports_vision("qwen3.8-27b-nvfp4-dflash2") is True
    assert model_supports_vision("RadixArk/Qwen3.8-27B-NVFP4") is True
    assert model_supports_vision("llama-3.3-70b") is False
    assert model_supports_vision("qwen2.5-coder-32b") is False
    assert model_supports_vision("") is False


def test_the_minima_candidate_differs_from_production_in_the_checkpoint_alone():
    # Night 2's A/B (specs/DREAMFERENCE_MODELS.md §2.2) is fair only if the candidate is served by
    # production's recipe: same image, flags, drafter and chat-template patches. Its two departures
    # are its own revision and a BF16 KV pool, which SGLang would otherwise make FP8 for a checkpoint
    # that ships KV scales. It is never the default.
    from dreamference.hardware.model_matrix_registry import (
        DEFAULT_MODEL_ALIAS, ModelMatrixRegistry, QWEN38_LORA_RECIPE, QWEN38_SGLANG_RECIPE)
    key = "qwen3.8-27b-minima-nvfp4-dflash2"
    candidate = ModelMatrixRegistry.get_launch_overrides(key)
    # Production's SGLang recipe (ling-serve is the default engine since 1.8.0; Minima runs SGLang).
    production = ModelMatrixRegistry.get_launch_overrides(DEFAULT_MODEL_ALIAS, "sglang")
    assert DEFAULT_MODEL_ALIAS == "qwen3.8-27b-nvfp4-dflash2"
    # Production adds only its adapters' settings, which Minima never takes: every one of its
    # layers is NVFP4, and LoRA on an NVFP4 layer crashed the probe's start under torch.compile.
    assert production == {**QWEN38_SGLANG_RECIPE, "lora": QWEN38_LORA_RECIPE}
    assert "lora" not in candidate
    assert candidate["revision"] == "16e768e7d0461b0b86e565ecedd08a24eca53e9a"
    assert candidate["extra_args"] == production["extra_args"] + ["--kv-cache-dtype", "bfloat16"]
    rest = {k: v for k, v in candidate.items() if k not in ("revision", "extra_args")}
    assert rest == {k: v for k, v in production.items() if k not in ("revision", "extra_args", "lora")}
    assert ModelMatrixRegistry.get_spec("minima-ai/mnma_qwen3.8_27b_nvfp4") is MODEL_MATRIX[key]
    assert ModelMatrixRegistry.key_for_served_id("minima-ai/mnma_qwen3.8_27b_nvfp4") == key
    assert MODEL_MATRIX[key].supports_vision is False
    # `server start` runs its NVFP4 canary only for aliases that say nvfp4.
    assert "nvfp4" in key


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_every_gb10_machine_is_found_by_its_gpu_and_named_by_its_firmware(tmp_path, monkeypatch):
    # The eight GB10 machines share one GPU (PCI 10de:2e12) and differ in DMI and OS release;
    # the GPU decides, and the rest is shown so a report says whose box it came from.
    from dreamference.hardware import hardware_manager
    from dreamference.hardware.hardware_manager import HardwareManager
    monkeypatch.setattr(hardware_manager, "PCI_DEVICES_DIR", str(tmp_path / "pci"))
    monkeypatch.setattr(hardware_manager, "DMI_DIR", str(tmp_path / "dmi"))
    monkeypatch.setattr(hardware_manager, "DGX_RELEASE", str(tmp_path / "dgx-release"))
    monkeypatch.setattr(hardware_manager, "OS_RELEASE", str(tmp_path / "os-release"))
    monkeypatch.setattr(hardware_manager.shutil, "which", lambda name: None)      # no driver yet
    monkeypatch.setattr(HardwareManager, "get_system_memory",
                        classmethod(lambda cls: hardware_manager.MemoryMetrics(total_gb=16.0, available_gb=8.0, used_gb=8.0)))

    (tmp_path / "pci").mkdir()
    assert HardwareManager.gb10_on_pci() is False and HardwareManager.detect_gb10_hardware().is_gb10 is False
    _write(tmp_path / "pci/0000:00:00.0/vendor", "0x10de\n")
    _write(tmp_path / "pci/0000:00:00.0/device", "0x22ce\n")                     # a root port, not the GPU
    assert HardwareManager.gb10_on_pci() is False
    _write(tmp_path / "pci/000f:01:00.0/vendor", "0x10de\n")
    _write(tmp_path / "pci/000f:01:00.0/device", "0x2e12\n")
    hw = HardwareManager.detect_gb10_hardware()
    assert hw.is_gb10 is True and "GB10" in hw.gpu_name

    assert HardwareManager.machine_name() == "" and HardwareManager.os_name() == ""
    _write(tmp_path / "dmi/sys_vendor", "ASUSTeK COMPUTER INC.\n")
    _write(tmp_path / "dmi/product_name", "GX10\n")
    assert HardwareManager.machine_name() == "ASUSTeK COMPUTER INC. GX10"
    _write(tmp_path / "dmi/sys_vendor", "Dell Inc.\n")
    # Not read from a Dell: the shape of a product name that already says whose it is.
    _write(tmp_path / "dmi/product_name", "Dell Pro Max with GB10 FCM1253\n")
    assert HardwareManager.machine_name() == "Dell Pro Max with GB10 FCM1253"

    _write(tmp_path / "os-release", 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.4 LTS"\n')
    assert HardwareManager.os_name() == "Ubuntu 24.04.4 LTS"
    _write(tmp_path / "dgx-release", 'DGX_NAME="DGX Spark"\nDGX_SWBUILD_VERSION="7.2.3"\n\nDGX_OTA_VERSION="7.5.0"\n')
    assert HardwareManager.os_name() == "DGX OS 7.5.0 (Ubuntu 24.04.4 LTS)"
    assert HardwareManager.detect_gb10_hardware().os_name == "DGX OS 7.5.0 (Ubuntu 24.04.4 LTS)"


def test_only_a_gb10_qualifies(monkeypatch, tmp_path):
    # A discrete Blackwell card and a large server are not GB10s: the recipes target SM121's unified memory.
    from dreamference.hardware import hardware_manager
    from dreamference.hardware.hardware_manager import HardwareManager
    monkeypatch.setattr(hardware_manager, "PCI_DEVICES_DIR", str(tmp_path / "pci"))
    (tmp_path / "pci").mkdir()
    monkeypatch.setattr(hardware_manager.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(HardwareManager, "get_system_memory",
                        classmethod(lambda cls: hardware_manager.MemoryMetrics(total_gb=512.0, available_gb=400.0, used_gb=112.0)))
    for name, expected in [("NVIDIA RTX PRO 6000 Blackwell Workstation Edition", False), ("NVIDIA GB10", True)]:
        monkeypatch.setattr(hardware_manager.subprocess, "run", lambda *a, _n=name, **k: subprocess.CompletedProcess(a, 0, f"{_n}, 580.95, 0\n", ""))
        assert HardwareManager.detect_gb10_hardware().is_gb10 is expected, name



def test_the_drafter_and_the_sparse_head_are_pinned_by_sha256():
    # Production serves the release-hosted drafter, and ling-engine's sparse head comes from the same release:
    # every file is pinned by SHA-256 and fetched from one models-* release, never the Hub's moving main.
    import re

    import dreamference.hardware.model_matrix_registry as registry
    for repo, revision in (
        (registry.QWEN38_DRAFTER_REPO, registry.QWEN38_DRAFTER_REVISION),
        (registry.QWEN38_SPARSE_HEAD_ASSET["repo"], registry.QWEN38_SPARSE_HEAD_ASSET["revision"]),
    ):
        source = registry.ModelMatrixRegistry.get_release_source(repo)
        assert source is not None and source["revision"] == revision == registry.MODELS_RELEASE_TAG
        assert re.fullmatch(r"models-\d{4}-\d{2}-\d{2}", revision)
        assert "model.safetensors" in source["files"]
        for pin in source["files"].values():
            assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])
            assert pin["url"].startswith(
                f"https://github.com/dreamference/mightling/releases/download/{revision}/"
            )
    drafter = registry.RELEASE_HOSTED_MODELS[registry.QWEN38_DRAFTER_REPO]["files"]
    # What SGLang's DFLASH loader reads from the drafter's folder.
    assert set(drafter) == {"model.safetensors", "config.json", "hf_quant_config.json"}
    # The target stays on the Hub.
    assert registry.ModelMatrixRegistry.get_release_source(registry.DEFAULT_MODEL_ALIAS) is None


def _release_server(monkeypatch, payloads):
    """Serves each URL of `payloads` from memory in place of the network; records the URLs asked."""
    import io
    import urllib.request

    asked = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        asked.append(url)
        if url not in payloads:
            raise OSError(f"no such asset {url}")
        return Response(payloads[url])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # These tests are of the downloader: put back what conftest stubs.
    conftest = sys.modules["conftest"]
    for name in ("ensure", "download", "_fetch"):
        monkeypatch.setattr(
            f"dreamference.hardware.ReleaseAssetDownloader.{name}",
            getattr(conftest, "REAL_RELEASE_" + name.strip("_").upper()),
        )
    return asked


def _hosted_model(monkeypatch, files):
    """Registers a release-hosted model of `files` (name to bytes); returns its name and the URL payloads."""
    import hashlib

    import dreamference.hardware.model_matrix_registry as registry
    repo = "dreamference/test-drafter"
    payloads, pins = {}, {}
    for name, data in files.items():
        url = f"https://github.com/dreamference/mightling/releases/download/models-2026-01-01/{name}"
        payloads[url] = data
        pins[name] = {"url": url, "sha256": hashlib.sha256(data).hexdigest()}
    monkeypatch.setitem(registry.RELEASE_HOSTED_MODELS, repo, {"revision": "models-2026-01-01", "files": pins})
    return repo, payloads


def test_a_release_hosted_model_lands_in_the_hub_cache_as_pinned_blobs(monkeypatch):
    import hashlib

    from dreamference.hardware import ModelDownloader, ReleaseAssetDownloader
    repo, payloads = _hosted_model(monkeypatch, {"model.safetensors": b"weights" * 1000, "config.json": b"{}"})
    asked = _release_server(monkeypatch, payloads)
    monkeypatch.setattr("huggingface_hub.snapshot_download", lambda **kw: pytest.fail("the Hub was asked"))
    assert not ModelDownloader.is_model_downloaded(repo)
    assert ModelDownloader.download_model(repo)
    assert sorted(asked) == sorted(payloads)
    folder = ModelDownloader.get_hf_cache_dir() / "models--dreamference--test-drafter"
    assert str(folder).startswith(str(Path.home()))
    snapshot = folder / "snapshots" / "models-2026-01-01"
    for name, data in (("model.safetensors", b"weights" * 1000), ("config.json", b"{}")):
        link = snapshot / name
        assert link.is_symlink() and link.read_bytes() == data
        assert link.resolve() == folder / "blobs" / hashlib.sha256(data).hexdigest()
    assert ModelDownloader.is_model_downloaded(repo) and ReleaseAssetDownloader.is_present(repo)
    # Present files are not fetched again.
    asked.clear()
    assert ModelDownloader.download_model(repo)
    assert asked == []


def test_a_release_asset_that_does_not_match_its_pin_is_not_kept(monkeypatch):
    from dreamference.hardware import ModelDownloader
    repo, payloads = _hosted_model(monkeypatch, {"model.safetensors": b"the pinned weights"})
    _release_server(monkeypatch, dict.fromkeys(payloads, b"other weights"))
    assert not ModelDownloader.download_model(repo)
    folder = ModelDownloader.get_hf_cache_dir() / "models--dreamference--test-drafter"
    assert list((folder / "blobs").iterdir()) == []
    assert not ModelDownloader.is_model_downloaded(repo)


def test_a_failed_release_download_leaves_nothing_half_written(monkeypatch):
    from dreamference.hardware import ModelDownloader
    repo, _ = _hosted_model(monkeypatch, {"model.safetensors": b"weights"})
    _release_server(monkeypatch, {})
    assert not ModelDownloader.download_model(repo)
    folder = ModelDownloader.get_hf_cache_dir() / "models--dreamference--test-drafter"
    assert list((folder / "blobs").iterdir()) == []


def test_a_connection_that_drops_mid_asset_is_a_failed_download_not_a_crash(monkeypatch):
    # A body shorter than its Content-Length raises http.client.IncompleteRead, an HTTPException
    # and not an OSError: `server start` crashed with a traceback and the `.incomplete` file stayed.
    import http.client
    import urllib.request

    from dreamference.hardware import ModelDownloader
    repo, payloads = _hosted_model(monkeypatch, {"model.safetensors": b"weights"})
    _release_server(monkeypatch, payloads)

    class Dropped:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            raise http.client.IncompleteRead(b"wei", 4)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: Dropped())
    assert not ModelDownloader.download_model(repo)
    folder = ModelDownloader.get_hf_cache_dir() / "models--dreamference--test-drafter"
    assert list((folder / "blobs").iterdir()) == []


def test_a_snapshot_link_another_download_makes_at_the_same_time_does_not_fail_this_one(monkeypatch):
    # The link was unlinked, then made: a second run that made it in between failed this one
    # with FileExistsError, and a reader in that gap found the file missing.
    from dreamference.hardware import ModelDownloader, ReleaseAssetDownloader
    repo, payloads = _hosted_model(monkeypatch, {"model.safetensors": b"weights"})
    _release_server(monkeypatch, payloads)
    assert ReleaseAssetDownloader.download(repo)
    folder = ModelDownloader.get_hf_cache_dir() / "models--dreamference--test-drafter"
    snapshot = folder / "snapshots" / "models-2026-01-01"
    final = snapshot / "model.safetensors"
    real = Path.symlink_to

    def other_run_links_first(self, target, *args, **kwargs):
        if not final.is_symlink():
            real(final, target)
        real(self, target, *args, **kwargs)
    monkeypatch.setattr(Path, "symlink_to", other_run_links_first)
    assert ReleaseAssetDownloader.download(repo)
    assert ReleaseAssetDownloader.is_present(repo)
    assert [path.name for path in snapshot.iterdir()] == ["model.safetensors"]


def test_node_sync_sends_a_release_hosted_drafter_as_a_hub_folder(monkeypatch):
    # The receiving node checks every 64-hex blob against its name: a release-hosted drafter's blobs are named so.
    from dreamference.hardware import ModelDownloader
    from dreamference.node.node_model_sync import LFS_BLOB, NodeModelSync
    repo, payloads = _hosted_model(monkeypatch, {"model.safetensors": b"weights"})
    _release_server(monkeypatch, payloads)
    assert ModelDownloader.download_model(repo)
    folder = NodeModelSync.hub() / NodeModelSync.folder_name(repo)
    assert (folder / "snapshots").is_dir()
    assert all(LFS_BLOB.fullmatch(blob.name) for blob in (folder / "blobs").iterdir())
    assert NodeModelSync._verify(NodeModelSync.hub()) is None
    # The registry's drafter is sent as its tag's snapshot and the blobs it links to.
    import hashlib

    import dreamference.hardware.model_matrix_registry as registry
    source = registry.ModelMatrixRegistry.get_release_source(registry.QWEN38_DRAFTER_REPO)
    hub, drafter = NodeModelSync.hub(), NodeModelSync.folder_name(registry.QWEN38_DRAFTER_REPO)
    snapshot = hub / drafter / "snapshots" / source["revision"]
    snapshot.mkdir(parents=True)
    for name, pin in source["files"].items():
        (hub / drafter / "blobs").mkdir(exist_ok=True)
        (hub / drafter / "blobs" / pin["sha256"]).write_bytes(hashlib.sha256(b"").digest())
        (snapshot / name).symlink_to(Path("../..") / "blobs" / pin["sha256"])
    members = NodeModelSync.members(hub, registry.QWEN38_DRAFTER_REPO)
    assert members is not None and members[0] == snapshot
    assert sorted(path.name for path in members[1:]) == sorted(pin["sha256"] for pin in source["files"].values())


def test_an_engine_asset_applies_only_to_its_engine_and_feature(monkeypatch):
    # The sparse head is ling-engine's, and only a build that reads it (its recipe lists the feature) fetches
    # it: SGLang's recipe, or a ling-engine recipe without the feature (79c38c7, 1.7.2), gets nothing.
    import dreamference.hardware.model_matrix_registry as registry
    from dreamference.node.node_model_sync import NodeModelSync
    key, head = registry.DEFAULT_MODEL_ALIAS, registry.QWEN38_SPARSE_HEAD_ASSET["repo"]
    # The shipped default since 1.8.0 is ling-engine at a commit that reads the head; SGLang fetches nothing.
    assert [a["repo"] for a in registry.ModelMatrixRegistry.get_engine_assets(key)] == [head]
    assert registry.ModelMatrixRegistry.get_engine_assets(key, "sglang") == []
    assert head in NodeModelSync.repos(key)
    recipe = dict(registry.ModelMatrixRegistry.get_launch_overrides(key, "ling"))
    cases = (("ling", [], False), ("ling", ["sparse-head"], True), ("sglang", ["sparse-head"], False))
    for engine, features, applies in cases:
        overrides = {**recipe, "engine": engine, "engine_features": features}
        monkeypatch.setattr(
            registry.ModelMatrixRegistry,
            "get_launch_overrides",
            classmethod(lambda cls, k, engine=None, o=overrides: o),
        )
        assets = registry.ModelMatrixRegistry.get_engine_assets(key)
        assert [a["repo"] for a in assets] == ([head] if applies else [])
        assert (head in NodeModelSync.repos(key)) == applies


def test_model_download_fetches_the_recipe_drafter_at_its_pin(monkeypatch):
    # `ling-admin model download` used to fetch the checkpoint alone; the drafter came only with `server start`.
    import dreamference.hardware as hardware
    import dreamference.hardware.model_matrix_registry as registry
    fetched = []
    monkeypatch.setattr(
        hardware.ModelDownloader,
        "download_model",
        classmethod(lambda cls, repo, **k: fetched.append((repo, k.get("revision"))) or True),
    )
    assert hardware.download_recipe_companions(registry.DEFAULT_MODEL_ALIAS)
    head = registry.QWEN38_SPARSE_HEAD_ASSET
    # The sparse head comes too: ling-engine reads it, the shipped default since 1.8.0.
    assert fetched == [
        (registry.QWEN38_DRAFTER_REPO, registry.QWEN38_DRAFTER_REVISION or None),
        (head["repo"], head["revision"]),
    ]


def test_a_ling_engine_recipe_lists_the_sparse_head_exactly_when_its_commit_reads_it():
    # The asset's condition is a feature list, because an install cannot run git; this keeps the list honest: for
    # every ling-engine recipe, "sparse-head" is listed if and only if its engine commit descends from the first
    # commit that reads the file. Needs the ling-engine submodule with both commits (a checkout); skipped otherwise.
    from pathlib import Path

    import pytest

    import dreamference.hardware.model_matrix_registry as registry
    engine = Path(__file__).resolve().parent.parent / "ling-engine"
    since = registry.LING_ENGINE_SPARSE_HEAD_SINCE
    recipes = [
        registry.ModelMatrixRegistry.get_launch_overrides(key)
        for key in registry.ModelMatrixRegistry.MATRIX
        if registry.ModelMatrixRegistry.get_launch_overrides(key).get("engine") == "ling"
    ]
    if not recipes:
        pytest.skip("no ling-engine recipe in the registry")
    for recipe in recipes:
        commit = recipe.get("engine_commit", "")
        known = [
            subprocess.run(["git", "-C", str(engine), "cat-file", "-e", c + "^{commit}"], capture_output=True)
            for c in (since, commit)
        ]
        if not commit or any(r.returncode != 0 for r in known):
            pytest.skip("the ling-engine submodule lacks the commits (fetch it)")
        descends = subprocess.run(
            ["git", "-C", str(engine), "merge-base", "--is-ancestor", since, commit], capture_output=True
        ).returncode == 0
        assert ("sparse-head" in (recipe.get("engine_features") or [])) == descends, commit


def test_a_ling_engine_recipe_lists_kv_pages_exactly_when_its_commit_has_the_page_pool():
    # Same rule as the sparse head's: the feature list is for an install that cannot run git, and a checkout keeps
    # it honest against the ling-engine submodule. Only then does the launch pass --sessions/--batch/--kv-pages.
    from pathlib import Path

    import pytest

    import dreamference.hardware.model_matrix_registry as registry
    engine = Path(__file__).resolve().parent.parent / "ling-engine"
    since = registry.LING_ENGINE_KV_PAGES_SINCE
    recipe = registry.ModelMatrixRegistry.get_launch_overrides(registry.DEFAULT_MODEL_ALIAS, "ling")
    commit = recipe.get("engine_commit", "")
    known = [
        subprocess.run(["git", "-C", str(engine), "cat-file", "-e", c + "^{commit}"], capture_output=True)
        for c in (since, commit)
    ]
    if not commit or any(r.returncode != 0 for r in known):
        pytest.skip("the ling-engine submodule lacks the commits (fetch it)")
    descends = subprocess.run(
        ["git", "-C", str(engine), "merge-base", "--is-ancestor", since, commit], capture_output=True
    ).returncode == 0
    assert ("kv-pages" in (recipe.get("engine_features") or [])) == descends, commit


def test_a_ling_engine_recipe_lists_the_switch_features_exactly_when_its_commit_has_them():
    from pathlib import Path

    import pytest

    import dreamference.hardware.model_matrix_registry as registry

    engine = Path(__file__).resolve().parent.parent / "ling-engine"
    recipe = registry.ModelMatrixRegistry.get_launch_overrides(registry.DEFAULT_MODEL_ALIAS, "ling")
    commit = recipe.get("engine_commit", "")
    for feature, since in (
        ("paging-switch", registry.LING_ENGINE_PAGING_SWITCH_SINCE),
        ("fair-pairing", registry.LING_ENGINE_FAIR_PAIRING_SINCE),
    ):
        known = [
            subprocess.run(["git", "-C", str(engine), "cat-file", "-e", c + "^{commit}"], capture_output=True)
            for c in (since, commit)
        ]
        if not commit or any(r.returncode != 0 for r in known):
            pytest.skip("the ling-engine submodule lacks the commits (fetch it)")
        descends = subprocess.run(
            ["git", "-C", str(engine), "merge-base", "--is-ancestor", since, commit], capture_output=True
        ).returncode == 0
        assert (feature in (recipe.get("engine_features") or [])) == descends, feature


def test_a_model_refused_for_memory_is_told_the_budget_it_was_compared_against(monkeypatch):
    # The check allows 95% of the total; the message used to call the total "available", so a
    # model needing less than that total was refused as if it needed more.
    from types import SimpleNamespace

    from dreamference.hardware import HardwareManager
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    spec = ModelMatrixRegistry.get_spec("qwen3.8-27b-nvfp4-dflash2")
    total = spec.min_memory_gb / 0.97
    monkeypatch.setattr(
        HardwareManager, "detect_gb10_hardware",
        classmethod(lambda cls: SimpleNamespace(total_unified_memory_gb=total)),
    )
    valid, msg = check_model_compatibility("qwen3.8-27b-nvfp4-dflash2")
    assert valid is False
    assert f"{total * 0.95:.1f} GB" in msg and "available" not in msg


def test_a_serialization_that_fails_midway_leaves_no_tensors_file(tmp_path, monkeypatch):
    # A truncated model.tensors is non-empty, so is_model_tensorized() took it for a finished one.
    import types

    from dreamference.hardware import ModelDownloader
    from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

    model_key = "qwen3.8-27b-nvfp4-dflash2"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    # Fixed at import from the real home: point it at the test's folder.
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", tmp_path / "tensorizer")
    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(lambda cls: None))
    monkeypatch.setattr(ModelDownloader, "is_model_downloaded", classmethod(lambda cls, *a, **k: True))
    monkeypatch.setattr(
        ModelDownloader, "get_model_snapshot_dir", classmethod(lambda cls, *a, **k: snapshot)
    )
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="")
    )

    written = []

    class Serializer:
        def __init__(self, path):
            self.path = path

        def write_module(self, model):
            written.append(Path(self.path).name)
            Path(self.path).write_bytes(b"half a checkpoint")
            raise RuntimeError("out of memory")

    torch = types.ModuleType("torch")
    torch.tensor = lambda *a, **k: None
    nn = types.ModuleType("torch.nn")
    nn.Parameter = lambda *a, **k: None
    torch.nn = nn
    model = types.SimpleNamespace(register_parameter=lambda *a: None)
    transformers = types.ModuleType("transformers")
    transformers.AutoModelForCausalLM = types.SimpleNamespace(from_pretrained=lambda *a, **k: model)
    tensorizer = types.ModuleType("tensorizer")
    tensorizer.TensorSerializer = Serializer
    for name, module in {
        "torch": torch, "torch.nn": nn, "transformers": transformers, "tensorizer": tensorizer
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    ModelDownloader.tensorize_model(model_key, force=True)
    assert ModelDownloader.is_model_tensorized(model_key) is False
    assert not (ModelDownloader.get_tensorized_dir(model_key) / "model.tensors").exists()
    # A kill midway skips every except, so the file being written must not have the final name.
    assert written and "model.tensors" not in written
    assert not list(ModelDownloader.get_tensorized_dir(model_key).iterdir())


def test_a_tensorization_whose_every_attempt_fails_reports_failure(tmp_path, monkeypatch, capsys):
    # It returned True, the docstring's "present or successfully created", with nothing created.
    import types

    from dreamference.hardware import ModelDownloader
    from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

    model_key = "qwen3.8-27b-nvfp4-dflash2"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", tmp_path / "tensorizer")
    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(lambda cls: None))
    monkeypatch.setattr(ModelDownloader, "is_model_downloaded", classmethod(lambda cls, *a, **k: True))
    monkeypatch.setattr(
        ModelDownloader, "get_model_snapshot_dir", classmethod(lambda cls, *a, **k: snapshot)
    )
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="")
    )
    # None in sys.modules makes the import raise ImportError: no local tensorizer.
    monkeypatch.setitem(sys.modules, "tensorizer", None)

    assert ModelDownloader.tensorize_model(model_key, force=True) is False
    assert ModelDownloader.is_model_tensorized(model_key) is False
    assert "normal download will be used" in capsys.readouterr().out


def test_a_cached_model_is_sized_by_its_blobs_not_also_by_its_snapshot_links(monkeypatch):
    from dreamference.hardware import ModelDownloader
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    model_key = "qwen3.8-27b-nvfp4-dflash2"
    repo = ModelMatrixRegistry.resolve_hf_repo(model_key)
    root = ModelDownloader.get_hf_cache_dir() / ("models--" + repo.replace("/", "--"))
    (root / "blobs").mkdir(parents=True)
    (root / "blobs" / "abc").write_bytes(b"\0" * 2048)
    (root / "snapshots" / "rev").mkdir(parents=True)
    (root / "snapshots" / "rev" / "model.safetensors").symlink_to("../../blobs/abc")
    assert ModelDownloader._get_cache_size(model_key) == "2.0 KB"


def test_an_interrupted_download_is_not_taken_for_a_downloaded_model():
    # download_model() skips the fetch for a model it finds, so a partial snapshot never resumed.
    import json

    from dreamference.hardware import ModelDownloader
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    model_key = "qwen3.8-27b-nvfp4-dflash2"
    repo = ModelMatrixRegistry.resolve_hf_repo(model_key)
    revision = ModelMatrixRegistry.pinned_revision(model_key) or "main-rev"
    root = ModelDownloader.get_hf_cache_dir() / ("models--" + repo.replace("/", "--"))
    snapshot = root / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (root / "blobs").mkdir()
    shards = ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]
    index = {"weight_map": {"a.weight": shards[0], "b.weight": shards[1]}}
    (snapshot / "config.json").write_text("{}")
    (snapshot / "model.safetensors.index.json").write_text(json.dumps(index))
    (snapshot / shards[0]).write_bytes(b"\0")
    assert ModelDownloader.is_model_downloaded(model_key, revision) is False
    (snapshot / shards[1]).write_bytes(b"\0")
    assert ModelDownloader.is_model_downloaded(model_key, revision) is True
    # A blob still being written is a download that has not finished.
    (root / "blobs" / "0123abcd.8d80c8fe.incomplete").write_bytes(b"\0")
    assert ModelDownloader.is_model_downloaded(model_key, revision) is False
    # One left behind by a download of the same blob that then finished is not.
    (root / "blobs" / "0123abcd").write_bytes(b"\0")
    assert ModelDownloader.is_model_downloaded(model_key, revision) is True


def test_an_unpinned_model_uses_the_complete_snapshot_its_download_check_accepted():
    # is_model_downloaded() accepts any complete snapshot; the newest one, then taken for the
    # model, could be an interrupted fetch, so tensorization and sizing read half a checkpoint.
    import json
    import os

    from dreamference.hardware import ModelDownloader
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    repo = "someone/unpinned-model"
    assert ModelMatrixRegistry.pinned_revision(repo) is None
    snapshots = ModelDownloader.get_hf_cache_dir() / ("models--" + repo.replace("/", "--")) / "snapshots"
    complete, partial = snapshots / "aaa", snapshots / "bbb"
    for snapshot in (complete, partial):
        snapshot.mkdir(parents=True)
        (snapshot / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"w": "model-00001-of-00001.safetensors"}})
        )
    (complete / "model-00001-of-00001.safetensors").write_bytes(b"\0")
    os.utime(complete, (1, 1))
    assert ModelDownloader.is_model_downloaded(repo) is True
    assert ModelDownloader.get_model_snapshot_dir(repo) == complete
    # With no complete snapshot, the newest still answers (eviction and sizing read partial ones).
    (complete / "model-00001-of-00001.safetensors").unlink()
    os.utime(complete, (1, 1))
    assert ModelDownloader.get_model_snapshot_dir(repo) == partial


def test_a_model_tensorized_at_one_pin_is_not_loaded_once_the_pin_moves(tmp_path, monkeypatch):
    from dreamference.hardware import ModelDownloader
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    model_key = "qwen3.8-27b-nvfp4-dflash2"
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", tmp_path / "tensorizer")
    monkeypatch.setattr(ModelMatrixRegistry, "pinned_revision", classmethod(lambda cls, k: "a" * 40))
    tdir = ModelDownloader.get_tensorized_dir(model_key)
    tdir.mkdir(parents=True)
    (tdir / "model.tensors").write_bytes(b"weights of the old pin")
    assert ModelDownloader.is_model_tensorized(model_key) is True
    monkeypatch.setattr(ModelMatrixRegistry, "pinned_revision", classmethod(lambda cls, k: "b" * 40))
    assert ModelDownloader.is_model_tensorized(model_key) is False


def test_the_container_mounts_a_relative_hf_home_by_its_absolute_path(tmp_path, monkeypatch):
    # Docker reads a bare name before the colon as a named volume, not the host folder.
    from dreamference.hardware import ModelDownloader

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HF_HOME", "hf")
    args = ModelDownloader.container_volume_args()
    assert args == ["-v", f"{tmp_path / 'hf'}:{ModelDownloader.CONTAINER_HF_HOME}"]


def test_a_cache_path_docker_cannot_mount_is_refused_by_name(tmp_path, monkeypatch):
    from dreamference.hardware import ModelDownloader

    monkeypatch.setenv("HF_HOME", str(tmp_path / "a:b"))
    with pytest.raises(ValueError, match="cannot be mounted"):
        ModelDownloader.container_volume_args()


def test_the_hub_cache_follows_the_legacy_variable_huggingface_hub_still_honours(tmp_path, monkeypatch):
    # snapshot_download writes to HUGGINGFACE_HUB_CACHE when HF_HUB_CACHE is unset; the host
    # looked under HF_HOME instead and never found what it had downloaded.
    from dreamference.hardware import ModelDownloader

    monkeypatch.setenv("HF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(tmp_path / "legacy"))
    assert ModelDownloader.get_hf_cache_dir() == tmp_path / "legacy"
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "current"))
    assert ModelDownloader.get_hf_cache_dir() == tmp_path / "current"


def test_a_recipe_s_nested_values_are_isolated_per_call():
    import copy

    from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS, ModelMatrixRegistry

    for engine in [None, *MODEL_MATRIX[DEFAULT_MODEL_ALIAS].engine_recipes]:
        first = ModelMatrixRegistry.get_launch_overrides(DEFAULT_MODEL_ALIAS, engine)
        original = copy.deepcopy(first)
        first["extra_args"].append("--mutated")
        first["speculative_config"]["mutated"] = True
        assert ModelMatrixRegistry.get_launch_overrides(DEFAULT_MODEL_ALIAS, engine) == original

def _tensorize_in_docker(tmp_path, monkeypatch, image_ready=True, returncode=0):
    # Runs tensorize_model with docker present and every outside call faked; the fake `docker run`
    # writes what the container would, at the host path of the file it was told to write.
    import re
    import types

    from dreamference.hardware import ModelDownloader
    from dreamference.hardware.hardware_manager import HardwareManager
    from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

    model_key = "qwen3.8-27b-nvfp4-dflash2"
    hub = tmp_path / "hub"
    snapshot = hub / "models--org--name" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    dgx = tmp_path / "home" / ".cache" / "dreamference"
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", dgx / "tensorizer")
    monkeypatch.setattr(ModelDownloader, "get_hf_cache_dir", classmethod(lambda cls: hub))
    monkeypatch.setattr(ModelDownloader, "container_volume_args", classmethod(lambda cls: []))
    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(lambda cls: None))
    monkeypatch.setattr(VLLMServerManager, "ensure_docker_image", lambda self, image=None: image_ready)
    monkeypatch.setattr(VLLMServerManager, "_estimate_model_weights_gb", staticmethod(lambda model: 20.0))
    monkeypatch.setattr(
        HardwareManager,
        "detect_gb10_hardware",
        classmethod(lambda cls: types.SimpleNamespace(total_unified_memory_gb=128.0)),
    )
    monkeypatch.setattr(ModelDownloader, "is_model_downloaded", classmethod(lambda cls, *a, **k: True))
    monkeypatch.setattr(
        ModelDownloader, "get_model_snapshot_dir", classmethod(lambda cls, *a, **k: snapshot)
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    # No local tensorizer: attempt 2 fails on its import.
    monkeypatch.setitem(sys.modules, "tensorizer", None)
    docker_runs, written = [], []

    def run(argv, *args, **kwargs):
        if argv[0] == "docker":
            docker_runs.append(argv)
            mounts = dict(argv[i + 1].split(":", 1)[::-1] for i, a in enumerate(argv) if a == "-v")
            target = re.search(r"TensorSerializer\('([^']+)'\)", argv[-1]).group(1)
            for inside, outside in mounts.items():
                if target.startswith(inside + "/"):
                    host = Path(outside + target[len(inside):])
                    written.append(host)
                    host.parent.mkdir(parents=True, exist_ok=True)
                    host.write_bytes(b"tensors")
        return types.SimpleNamespace(returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    ModelDownloader.tensorize_model(model_key, force=True)
    return ModelDownloader.get_tensorized_dir(model_key), docker_runs, written, dgx


def test_tensorize_runs_the_container_with_the_dreamference_cache_mounted(tmp_path, monkeypatch):
    # `get_dgx_cache_dir` did not exist: the AttributeError was swallowed and the container never ran.
    tdir, docker_runs, written, dgx = _tensorize_in_docker(tmp_path, monkeypatch)
    assert len(docker_runs) == 1
    assert f"{dgx}:/root/.cache/dreamference" in docker_runs[0]
    assert written and all(path.is_relative_to(tdir) for path in written)
    assert (tdir / "model.tensors").read_bytes() == b"tensors"


def test_a_failed_container_leaves_no_tensors_file(tmp_path, monkeypatch):
    tdir, docker_runs, _, _ = _tensorize_in_docker(tmp_path, monkeypatch, returncode=1)
    assert docker_runs
    assert not (tdir / "model.tensors").exists() and not (tdir / "model.tensors.partial").exists()


def test_the_container_writes_the_tensors_under_another_name_and_the_host_renames_it(
    tmp_path, monkeypatch
):
    # A container killed midway left a non-empty model.tensors, which reads as tensorized.
    tdir, _, written, _ = _tensorize_in_docker(tmp_path, monkeypatch)
    assert [path.name for path in written] == ["model.tensors.partial"]
    assert (tdir / "model.tensors").exists() and not (tdir / "model.tensors.partial").exists()


def test_tensorize_does_not_run_a_container_whose_image_is_missing(tmp_path, monkeypatch):
    _, docker_runs, _, _ = _tensorize_in_docker(tmp_path, monkeypatch, image_ready=False)
    assert docker_runs == []


def test_tensorize_on_the_host_runs_no_code_and_unpickles_nothing_from_the_repository(
    tmp_path, monkeypatch
):
    # The host attempt loaded any named repository with trust_remote_code=True and no
    # use_safetensors: its modeling .py ran, and .bin weights were unpickled, outside any container.
    import types

    from dreamference.hardware import ModelDownloader
    from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

    model_key = "qwen3.8-27b-nvfp4-dflash2"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", tmp_path / "tensorizer")
    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(lambda cls: None))
    monkeypatch.setattr(ModelDownloader, "is_model_downloaded", classmethod(lambda cls, *a, **k: True))
    monkeypatch.setattr(
        ModelDownloader, "get_model_snapshot_dir", classmethod(lambda cls, *a, **k: snapshot)
    )
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="")
    )
    loads = []

    def from_pretrained(path, **kwargs):
        loads.append(kwargs)
        return types.SimpleNamespace(register_parameter=lambda *a: None)

    class Serializer:
        def __init__(self, path):
            self.path = path

        def write_module(self, model):
            Path(self.path).write_bytes(b"tensors")

        def close(self):
            pass

    torch = types.ModuleType("torch")
    torch.tensor = lambda *a, **k: None
    nn = types.ModuleType("torch.nn")
    nn.Parameter = lambda *a, **k: None
    torch.nn = nn
    transformers = types.ModuleType("transformers")
    transformers.AutoModelForCausalLM = types.SimpleNamespace(from_pretrained=from_pretrained)
    tensorizer = types.ModuleType("tensorizer")
    tensorizer.TensorSerializer = Serializer
    for name, module in {
        "torch": torch, "torch.nn": nn, "transformers": transformers, "tensorizer": tensorizer
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    ModelDownloader.tensorize_model(model_key, force=True)
    assert len(loads) == 1
    assert loads[0]["trust_remote_code"] is False
    assert loads[0]["use_safetensors"] is True


def test_the_sparse_head_comes_with_the_ling_engine_recipe_once_it_has_the_feature(monkeypatch):
    # The asset sat in SGLang's recipe, which the engine filter always skips, so the ling-engine
    # recipe, the only one it is for, never listed it whatever its engine_features said.
    import dreamference.hardware.model_matrix_registry as registry

    key, head = registry.DEFAULT_MODEL_ALIAS, registry.QWEN38_SPARSE_HEAD_ASSET["repo"]
    monkeypatch.setitem(registry.QWEN38_LING_RECIPE, "engine_features", ["sparse-head"])
    assets = registry.ModelMatrixRegistry.get_engine_assets(key, "ling")
    assert [asset["repo"] for asset in assets] == [head]
    assert registry.ModelMatrixRegistry.get_engine_assets(key, "sglang") == []


def test_an_engine_asset_reads_the_recipe_s_engine_as_engine_of_does(monkeypatch):
    # A recipe without `engine` runs on vLLM (engine_of); its vLLM assets were dropped, since
    # the filter compared the asset's engine with the missing key, and `SGLang` with `sglang`.
    import dreamference.hardware.model_matrix_registry as registry

    asset = {"repo": "someone/asset", "engine": "vllm", "feature": "f"}
    for engine in (None, "VLLM"):
        overrides = {"engine_features": ["f"], "engine_assets": [asset]}
        if engine:
            overrides["engine"] = engine
        monkeypatch.setattr(
            registry.ModelMatrixRegistry,
            "get_launch_overrides",
            classmethod(lambda cls, k, engine=None, o=overrides: o),
        )
        assets = registry.ModelMatrixRegistry.get_engine_assets(registry.DEFAULT_MODEL_ALIAS)
        assert [a["repo"] for a in assets] == ["someone/asset"]


def test_two_fetches_of_one_release_asset_at_once_both_land_it(monkeypatch):
    # Both streamed into the one `<sha256>.incomplete`: the second truncated what the first was
    # writing, moved it into place, and the first's own move then failed on a file that was gone.
    import hashlib
    import io
    import stat
    import urllib.request

    from dreamference.hardware import ModelDownloader, ReleaseAssetDownloader

    data = b"weights" * 1000
    repo, payloads = _hosted_model(monkeypatch, {"model.safetensors": data})
    _release_server(monkeypatch, payloads)
    (url,) = payloads
    pin = {"url": url, "sha256": hashlib.sha256(data).hexdigest()}
    blob = ReleaseAssetDownloader.folder(repo) / "blobs" / pin["sha256"]
    blob.parent.mkdir(parents=True)
    nested = []

    class Response(io.BytesIO):
        def __init__(self, payload, first):
            super().__init__(payload)
            self.first = first

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            if self.first and not nested:
                # Another download of the same model starts while this one is streaming.
                nested.append(ReleaseAssetDownloader._fetch(pin, blob))
            return super().read(size)

    opened = []

    def fake_urlopen(request, timeout=None):
        opened.append(request.full_url)
        return Response(payloads[request.full_url], first=len(opened) == 1)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert ReleaseAssetDownloader._fetch(pin, blob) is True
    assert nested == [True]
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == pin["sha256"]
    assert stat.S_IMODE(blob.stat().st_mode) == 0o644
    assert not list(blob.parent.glob("*.incomplete"))
    assert ModelDownloader.get_hf_cache_dir() in blob.parents


def test_node_sync_carries_the_assets_of_every_engine_once(monkeypatch):
    import dreamference.hardware.model_matrix_registry as registry
    from dreamference.node.node_model_sync import NodeModelSync

    assets = {
        "sglang": [{"repo": "o/shared"}, {"repo": "o/sglang-head"}],
        "ling": [{"repo": "o/shared"}, {"repo": "o/ling-head"}],
    }
    reg = registry.ModelMatrixRegistry
    monkeypatch.setattr(reg, "resolve_hf_repo", classmethod(lambda cls, key: "o/model"))
    monkeypatch.setattr(reg, "get_speculative_draft_repo", classmethod(lambda cls, key: None))
    monkeypatch.setattr(reg, "engines", classmethod(lambda cls, key: ["sglang", "ling"]))
    monkeypatch.setattr(
        reg, "get_engine_assets", classmethod(lambda cls, key, engine=None: assets[engine])
    )
    assert NodeModelSync.repos("m") == ["o/model", "o/shared", "o/sglang-head", "o/ling-head"]
