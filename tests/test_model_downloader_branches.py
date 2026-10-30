"""ModelDownloader refusals and fallbacks the other downloader tests do not reach."""

import types

import pytest

from dreamference.hardware import ModelDownloader
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

KEY = "some-org/some-model"


def test_an_empty_model_key_has_no_snapshot_tensorized_dir_or_tensors_file():
    assert ModelDownloader.get_model_snapshot_dir("") is None
    assert ModelDownloader.get_tensorized_dir("") is None
    assert ModelDownloader.get_tensorized_path("") is None


def test_a_cache_folder_with_no_snapshot_has_no_snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ModelDownloader, "get_hf_cache_dir", classmethod(lambda cls: tmp_path))
    (tmp_path / "models--some-org--some-model" / "snapshots").mkdir(parents=True)
    assert ModelDownloader.get_model_snapshot_dir(KEY) is None


def test_an_empty_model_key_is_never_taken_for_downloaded():
    assert ModelDownloader.is_model_downloaded("") is False


def test_downloading_without_a_model_key_is_refused_and_fetches_nothing(monkeypatch):
    fetched = []
    monkeypatch.setattr("huggingface_hub.snapshot_download", lambda *a, **k: fetched.append(a))
    with pytest.raises(ValueError, match="model_key cannot be None or empty"):
        ModelDownloader.download_model("")
    assert fetched == []


def test_companions_come_from_the_engine_assets_when_the_recipe_has_no_drafter(monkeypatch):
    monkeypatch.setattr(
        ModelMatrixRegistry, "get_launch_overrides", classmethod(lambda cls, key, engine=None: {})
    )
    monkeypatch.setattr(
        ModelMatrixRegistry,
        "get_engine_assets",
        classmethod(lambda cls, key, engine=None: [{"repo": "o/head", "revision": "r1"}]),
    )
    seen = []

    def download(cls, repo, hf_token=None, auto_tensorize=True, revision=None):
        seen.append((repo, revision, auto_tensorize))
        return True

    monkeypatch.setattr(ModelDownloader, "download_model", classmethod(download))
    assert ModelDownloader.download_companions("m") is True
    assert seen == [("o/head", "r1", False)]


def test_one_companion_that_fails_makes_the_result_false_but_the_rest_are_fetched(monkeypatch):
    monkeypatch.setattr(
        ModelMatrixRegistry, "get_launch_overrides", classmethod(lambda cls, key, engine=None: {})
    )
    monkeypatch.setattr(
        ModelMatrixRegistry,
        "get_engine_assets",
        classmethod(lambda cls, key, engine=None: [{"repo": "o/a"}, {"repo": "o/b"}]),
    )
    seen = []

    def download(cls, repo, hf_token=None, auto_tensorize=True, revision=None):
        seen.append(repo)
        return repo != "o/a"

    monkeypatch.setattr(ModelDownloader, "download_model", classmethod(download))
    assert ModelDownloader.download_companions("m") is False
    assert seen == ["o/a", "o/b"]


def test_the_size_of_a_cached_model_in_terabytes(tmp_path, monkeypatch):
    monkeypatch.setattr(ModelDownloader, "get_hf_cache_dir", classmethod(lambda cls: tmp_path))
    blob = tmp_path / "models--some-org--some-model" / "blobs" / "x"
    blob.parent.mkdir(parents=True)
    with blob.open("wb") as handle:
        handle.truncate(3 << 39)  # a sparse 1.5 TB file
    assert ModelDownloader._get_cache_size(KEY) == "1.5 TB"


def test_the_size_of_a_model_that_resolves_to_nothing_is_unknown(monkeypatch):
    monkeypatch.setattr(ModelMatrixRegistry, "resolve_hf_repo", classmethod(lambda cls, key: ""))
    assert ModelDownloader._get_cache_size(KEY) == "unknown size"


def test_a_cache_that_cannot_be_removed_is_not_reported_cleared(monkeypatch, capsys):
    monkeypatch.setattr(
        ModelDownloader, "_remove_cache_dir", classmethod(lambda cls, path: path.name != "hub")
    )
    monkeypatch.setattr(
        ModelDownloader,
        "get_hf_cache_dir",
        classmethod(lambda cls: ModelDownloader.TENSORIZER_CACHE_HOME.parent / "hub"),
    )
    assert ModelDownloader.clear_cache() is False
    assert "Model cache cleared" not in capsys.readouterr().out


def _prepared(tmp_path, monkeypatch, *, docker, snapshot):
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", tmp_path / "tensorizer")
    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(lambda cls: None))
    monkeypatch.setattr(
        ModelDownloader, "is_model_downloaded", classmethod(lambda cls, *a, **k: True)
    )
    monkeypatch.setattr(
        ModelDownloader, "get_model_snapshot_dir", classmethod(lambda cls, *a, **k: snapshot)
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker" if docker else None)
    monkeypatch.setitem(__import__("sys").modules, "tensorizer", None)


def test_a_model_without_a_tensorized_dir_is_not_tensorized(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    _prepared(tmp_path, monkeypatch, docker=False, snapshot=snapshot)
    monkeypatch.setattr(ModelDownloader, "get_tensorized_dir", classmethod(lambda cls, key: None))
    assert ModelDownloader.tensorize_model(KEY, force=True) is False


def test_a_snapshot_outside_the_hub_cache_is_never_given_to_the_container(tmp_path, monkeypatch):
    snapshot = tmp_path / "elsewhere"
    snapshot.mkdir()
    _prepared(tmp_path, monkeypatch, docker=True, snapshot=snapshot)
    commands = []

    def run(cmd, *args, **kwargs):
        commands.append(cmd)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    assert ModelDownloader.tensorize_model(KEY, force=True) is False
    assert not any(cmd and cmd[0] == "docker" for cmd in commands)


def test_the_vllm_script_fallback_keeps_the_tensors_and_copies_only_metadata(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"w")
    _prepared(tmp_path, monkeypatch, docker=False, snapshot=snapshot)

    def run(cmd, *args, **kwargs):
        tdir = ModelDownloader.get_tensorized_dir(KEY)
        (tdir / "model.tensors").write_bytes(b"t")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    assert ModelDownloader.tensorize_model(KEY, force=True) is True
    tdir = ModelDownloader.get_tensorized_dir(KEY)
    assert (tdir / "model.tensors").read_bytes() == b"t"
    assert (tdir / "config.json").exists()
    assert not (tdir / "model.safetensors").exists()
