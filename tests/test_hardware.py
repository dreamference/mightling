import subprocess
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
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    aliases = [
        "qwen3.8-27b-nvfp4-dflash2",
        "RadixArk/Qwen3.8-27B-NVFP4",
        "Qwen 3.8 27B (NVFP4 + DFlash2, SGLang)",
        "qwen 3.8 27b (nvfp4 + dflash2, sglang)",
    ]
    for name in aliases:
        spec = ModelMatrixRegistry.get_spec(name)
        assert spec is not None, f"Failed to get spec for {name}"
        assert spec.hf_repo_id == "RadixArk/Qwen3.8-27B-NVFP4"
        assert resolve_model_hf_repo(name) == "RadixArk/Qwen3.8-27B-NVFP4"
        assert get_model_launch_overrides(name).get("max_model_len") == 262144
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
    production = ModelMatrixRegistry.get_launch_overrides(DEFAULT_MODEL_ALIAS)
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
    assert registry.ModelMatrixRegistry.get_engine_assets(key) == []
    assert head not in NodeModelSync.repos(key)
    recipe = dict(registry.ModelMatrixRegistry.get_launch_overrides(key))
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
    assert fetched == [(registry.QWEN38_DRAFTER_REPO, registry.QWEN38_DRAFTER_REVISION or None)]


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
