"""The Docker route of `tensorize_model` runs: it once asked for a cache directory that did not exist.

`ModelDownloader.get_dgx_cache_dir` was never defined, so the `AttributeError` was swallowed by the
route's `except Exception` and every serialization silently used the local library instead.
"""

import subprocess
from types import SimpleNamespace

from dreamference.hardware import model_downloader
from dreamference.hardware.hardware_manager import HardwareManager
from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager


def test_tensorize_model_runs_the_docker_container_for_a_cached_snapshot(tmp_path, monkeypatch):
    home = tmp_path / "home"
    hf_hub = tmp_path / "hub"
    snapshot = hf_hub / "models--x--y" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    tensorizer = home / ".cache" / "dreamference" / "tensorizer"
    target = tensorizer / "y"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", tensorizer)
    monkeypatch.setattr(ModelDownloader, "is_model_tensorized", classmethod(lambda cls, key: False))
    monkeypatch.setattr(ModelDownloader, "is_model_downloaded", classmethod(lambda cls, key: True))
    monkeypatch.setattr(
        ModelDownloader, "get_model_snapshot_dir", classmethod(lambda cls, key: snapshot)
    )
    monkeypatch.setattr(ModelDownloader, "get_tensorized_dir", classmethod(lambda cls, key: target))
    monkeypatch.setattr(ModelDownloader, "get_hf_cache_dir", classmethod(lambda cls: hf_hub))
    monkeypatch.setattr(ModelDownloader, "container_volume_args", classmethod(lambda cls: []))
    monkeypatch.setattr(VLLMServerManager, "check_host_safety", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(VLLMServerManager, "ensure_docker_image", lambda self, image: True)
    monkeypatch.setattr(
        VLLMServerManager, "_estimate_model_weights_gb", staticmethod(lambda key: 1.0)
    )
    monkeypatch.setattr(
        HardwareManager,
        "detect_gb10_hardware",
        staticmethod(lambda: SimpleNamespace(total_unified_memory_gb=128)),
    )
    monkeypatch.setattr(model_downloader.shutil, "which", lambda name: "/usr/bin/docker")
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        (target / "model.tensors").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(model_downloader.subprocess, "run", fake_run)

    assert ModelDownloader.tensorize_model("x/y") is True
    assert [cmd[:2] for cmd in commands] == [["docker", "run"]]
    assert f"{home / '.cache' / 'dreamference'}:/root/.cache/dreamference" in commands[0]
    assert "/root/.cache/dreamference/tensorizer/y/model.tensors" in commands[0][-1]
