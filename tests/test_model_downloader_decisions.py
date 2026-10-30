"""The downloader's decisions on a made-up model, with the registry, the Hub and the CLI faked.

Nothing reaches the network: `huggingface_hub` is a recorder, `huggingface-cli` a fake process,
and the hub cache is the test's own folder.
"""

import os
import sys
import types
from pathlib import Path

import pytest

from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

REPO = "org/name"


@pytest.fixture
def model(tmp_path, monkeypatch):
    # "fake-model" resolves to org/name, pinned at `pin`, with the hub cache under tmp_path.
    state = types.SimpleNamespace(pin=None, repo=REPO, release=None, hub=tmp_path / "hub")
    monkeypatch.setenv("HF_HUB_CACHE", str(state.hub))
    monkeypatch.setattr(
        ModelMatrixRegistry, "resolve_hf_repo", classmethod(lambda cls, key: state.repo)
    )
    monkeypatch.setattr(
        ModelMatrixRegistry, "pinned_revision", classmethod(lambda cls, key: state.pin)
    )
    monkeypatch.setattr(
        ModelMatrixRegistry, "get_release_source", classmethod(lambda cls, key: state.release)
    )
    state.snapshots = state.hub / ("models--" + REPO.replace("/", "--")) / "snapshots"
    return state


def _snapshot(model, name, files=("config.json",)):
    folder = model.snapshots / name
    folder.mkdir(parents=True, exist_ok=True)
    for file in files:
        (folder / file).write_text("{}")
    return folder


def test_the_hf_home_follows_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert ModelDownloader.get_hf_home() == tmp_path / "xdg" / "huggingface"
    assert ModelDownloader.get_hf_cache_dir() == tmp_path / "xdg" / "huggingface" / "hub"


def test_a_hub_folder_docker_would_split_is_refused(tmp_path, monkeypatch):
    # `-v a:b:/root/...` mounts `a` at `b`: a colon in the hub path would mount the wrong folder.
    monkeypatch.setenv("HF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub:2"))
    with pytest.raises(ValueError):
        ModelDownloader.container_volume_args()


def test_a_pin_missing_from_disk_falls_back_to_a_snapshot_that_is_there(model):
    model.pin = "pinned"
    other = _snapshot(model, "other")
    assert ModelDownloader.get_model_snapshot_dir("fake-model") == other


def test_a_stray_file_among_the_snapshots_is_never_taken_for_one(model):
    snapshot = _snapshot(model, "abc", files=())
    stray = model.snapshots / "stray"
    stray.write_text("")
    os.utime(snapshot, (1_000_000, 1_000_000))
    os.utime(stray, (2_000_000, 2_000_000))
    assert ModelDownloader.get_model_snapshot_dir("fake-model") == snapshot


def test_an_empty_tensors_file_is_not_tensorized(tmp_path, monkeypatch, model):
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", tmp_path / "tensorizer")
    tdir = ModelDownloader.get_tensorized_dir("fake-model")
    tdir.mkdir(parents=True)
    (tdir / "model.tensors").write_bytes(b"")
    assert ModelDownloader.is_model_tensorized("fake-model") is False
    (tdir / "model.tensors").write_bytes(b"tensors")
    assert ModelDownloader.is_model_tensorized("fake-model") is True


@pytest.mark.parametrize(
    "files, downloaded",
    [
        ((), False),  # an empty snapshot holds nothing
        (("config.json", "model.safetensors.index.json"), False),  # an index that does not parse
        (("config.json",), True),
    ],
)
def test_a_snapshot_counts_only_when_it_holds_something_readable(model, files, downloaded):
    folder = _snapshot(model, "abc", files=files)
    if "model.safetensors.index.json" in files:
        (folder / "model.safetensors.index.json").write_text("{not json")
    assert ModelDownloader.is_model_downloaded("fake-model") is downloaded


def test_a_repository_id_without_an_owner_is_never_downloaded(model):
    model.repo = "name"
    folder = model.hub / "models--name" / "snapshots" / "abc"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}")
    assert ModelDownloader.is_model_downloaded("fake-model") is False


def test_a_release_hosted_model_asks_the_release_downloader(model, monkeypatch):
    from dreamference.hardware.release_asset_downloader import ReleaseAssetDownloader

    model.release = {"files": {}}
    monkeypatch.setattr(ReleaseAssetDownloader, "is_present", classmethod(lambda cls, key: True))
    assert ModelDownloader.is_model_downloaded("fake-model") is True


@pytest.fixture
def hub(model, monkeypatch):
    # huggingface_hub as a recorder; the CLI fallback as a fake process that fails unless told.
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("DREAMFERENCE_HF_TOKEN", raising=False)
    model.fetched, model.cli, model.cli_returncode = [], [], 1
    model.tensorized = []

    def snapshot_download(repo_id, token=None, revision=None):
        model.fetched.append((repo_id, token, revision))
        _snapshot(model, revision or "main")

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download)
    )
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/" + name if name == "huggingface-cli" else None
    )

    def run(argv, env=None, **kwargs):
        model.cli.append((argv, env))
        return types.SimpleNamespace(returncode=model.cli_returncode, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr(
        ModelDownloader,
        "tensorize_model",
        classmethod(lambda cls, key, **kwargs: model.tensorized.append(key) or True),
    )
    return model


def test_the_cache_is_checked_for_the_requested_revision_not_any(hub):
    _snapshot(hub, "def")
    assert ModelDownloader.download_model("fake-model", revision="abc") is True
    assert hub.fetched == [(REPO, None, "abc")]
    # Now on disk: a second download fetches nothing.
    assert ModelDownloader.download_model("fake-model", revision="abc") is True
    assert len(hub.fetched) == 1


@pytest.mark.parametrize(
    "argument, env, token",
    [
        (None, {"DREAMFERENCE_HF_TOKEN": "product-token"}, "product-token"),
        ("argument-token", {"HF_TOKEN": "env-token"}, "argument-token"),  # the caller's wins
    ],
)
def test_the_download_token_comes_from_the_caller_then_the_environment(
    hub, monkeypatch, argument, env, token
):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert ModelDownloader.download_model("fake-model", hf_token=argument, revision="abc") is True
    assert hub.fetched == [(REPO, token, "abc")]


def test_the_cli_fallback_fetches_the_revision_and_fails_with_the_cli(hub, monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    assert (
        ModelDownloader.download_model("fake-model", revision="abc", auto_tensorize=True) is False
    )
    argv, _ = hub.cli[0]
    assert argv[:3] == ["huggingface-cli", "download", REPO]
    assert argv[argv.index("--revision") + 1] == "abc"
    # A failed download is not tensorized.
    assert hub.tensorized == []
    hub.cli_returncode = 0
    assert ModelDownloader.download_model("fake-model", revision="abc") is True


def test_only_a_download_asked_to_tensorize_does(hub):
    assert ModelDownloader.download_model("fake-model", revision="abc") is True
    assert hub.tensorized == []
    assert ModelDownloader.download_model("fake-model", revision="abc", auto_tensorize=True) is True
    assert hub.tensorized == ["fake-model"]


class _StopError(Exception):
    pass


@pytest.fixture
def tensorize(model, tmp_path, monkeypatch):
    # tensorize_model up to the host safety check, which records that it was reached and stops.
    from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", tmp_path / "tensorizer")
    model.checked, model.downloads = [], []

    def check_host_safety(cls):
        model.checked.append(True)

    monkeypatch.setattr(VLLMServerManager, "check_host_safety", classmethod(check_host_safety))
    model.download_ok = False

    def download_model(cls, key, **kwargs):
        model.downloads.append(key)
        return model.download_ok

    monkeypatch.setattr(ModelDownloader, "download_model", classmethod(download_model))

    def snapshot_dir(cls, key):
        raise _StopError

    monkeypatch.setattr(ModelDownloader, "get_model_snapshot_dir", classmethod(snapshot_dir))
    return model


def _tensorized(model):
    tdir = ModelDownloader.get_tensorized_dir("fake-model")
    tdir.mkdir(parents=True)
    (tdir / "model.tensors").write_bytes(b"tensors")


def test_an_already_tensorized_model_is_redone_only_when_forced(tensorize):
    _snapshot(tensorize, "abc")
    _tensorized(tensorize)
    assert ModelDownloader.tensorize_model("fake-model") is True
    assert tensorize.checked == []
    with pytest.raises(_StopError):
        ModelDownloader.tensorize_model("fake-model", force=True)
    assert tensorize.checked == [True]


def test_a_model_not_yet_tensorized_is_tensorized_without_force(tensorize):
    _snapshot(tensorize, "abc")
    with pytest.raises(_StopError):
        ModelDownloader.tensorize_model("fake-model")
    assert tensorize.checked == [True] and tensorize.downloads == []


def test_tensorize_downloads_first_and_stops_when_the_download_fails(tensorize):
    assert ModelDownloader.tensorize_model("fake-model") is False
    assert tensorize.downloads == ["fake-model"]
    tensorize.download_ok = True
    with pytest.raises(_StopError):
        ModelDownloader.tensorize_model("fake-model")


def test_an_empty_key_is_never_tensorized(tensorize):
    assert ModelDownloader.tensorize_model("") is False
    assert tensorize.checked == []


def test_tensorize_stops_when_no_snapshot_is_found_after_the_download(tensorize, monkeypatch):
    tensorize.download_ok = True
    monkeypatch.setattr(
        ModelDownloader, "get_model_snapshot_dir", classmethod(lambda cls, key: None)
    )
    # Neither conversion may start: no docker, no local tensorizer.
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setitem(sys.modules, "tensorizer", None)
    assert ModelDownloader.tensorize_model("fake-model") is False
    assert not ModelDownloader.get_tensorized_dir("fake-model").exists()


@pytest.fixture
def companions(monkeypatch):
    # A recipe naming a drafter and two engine assets; download_model a recorder that fails on `failing`.
    calls = types.SimpleNamespace(engines=[], downloads=[], failing=set())

    def get_launch_overrides(cls, key, engine=None):
        calls.engines.append(("overrides", engine))
        return {"speculative_config": {"model": "org/drafter", "revision": "drafter-pin"}}

    def get_engine_assets(cls, key, engine=None):
        calls.engines.append(("assets", engine))
        return [
            {"repo": "org/asset", "revision": "asset-pin"},
            {"repo": "org/unpinned", "revision": ""},
        ]

    def download_model(cls, key, hf_token=None, auto_tensorize=False, revision=None):
        calls.downloads.append((key, auto_tensorize, revision))
        return key not in calls.failing

    monkeypatch.setattr(
        ModelMatrixRegistry, "get_launch_overrides", classmethod(get_launch_overrides)
    )
    monkeypatch.setattr(ModelMatrixRegistry, "get_engine_assets", classmethod(get_engine_assets))
    monkeypatch.setattr(ModelDownloader, "download_model", classmethod(download_model))
    return calls


def test_companions_come_at_their_pins_for_the_engine_served_and_are_never_tensorized(companions):
    assert ModelDownloader.download_companions("fake-model", engine="sglang") is True
    assert companions.engines == [("overrides", "sglang"), ("assets", "sglang")]
    assert companions.downloads == [
        ("org/drafter", False, "drafter-pin"),
        ("org/asset", False, "asset-pin"),
        ("org/unpinned", False, None),
    ]


def test_a_failed_download_does_not_stop_the_ones_after_it(companions):
    companions.failing = {"org/drafter"}
    assert ModelDownloader.download_companions("fake-model") is False
    assert [key for key, _, _ in companions.downloads] == [
        "org/drafter",
        "org/asset",
        "org/unpinned",
    ]
    companions.downloads.clear()
    companions.failing = {"fake-model"}
    assert ModelDownloader.download_with_drafter("fake-model") is False
    assert [key for key, _, _ in companions.downloads] == [
        "fake-model",
        "org/drafter",
        "org/asset",
        "org/unpinned",
    ]


def test_the_cache_size_reads_zero_when_nothing_is_there_and_counts_a_kilobyte(model):
    assert ModelDownloader._get_cache_size("fake-model") == "0 B"
    blobs = model.hub / ("models--" + REPO.replace("/", "--")) / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "a").write_bytes(b"x" * 1023)
    assert ModelDownloader._get_cache_size("fake-model") == "1023.0 B"
    (blobs / "a").write_bytes(b"x" * 1024)
    assert ModelDownloader._get_cache_size("fake-model") == "1.0 KB"


def test_download_all_fetches_only_models_offered_and_fit_for_a_gb10(monkeypatch):
    entries = {
        "fits": types.SimpleNamespace(compatible_gb10=True),
        "unfit": types.SimpleNamespace(compatible_gb10=False),
        "withdrawn": types.SimpleNamespace(compatible_gb10=True),
    }
    monkeypatch.setattr(ModelMatrixRegistry, "MATRIX", entries)
    monkeypatch.setattr(
        ModelMatrixRegistry, "is_offered", classmethod(lambda cls, key: key != "withdrawn")
    )
    monkeypatch.setattr(
        ModelDownloader, "download_model", classmethod(lambda cls, key, **kwargs: True)
    )
    assert ModelDownloader.download_all_models() == {"fits": True}


def test_a_cache_already_gone_counts_as_cleared(tmp_path):
    assert ModelDownloader._remove_cache_dir(tmp_path / "missing") is True


def test_a_linked_cache_is_emptied_without_following_a_link_inside_it(tmp_path):
    # The cache on another disk through a link; inside it, a link to a folder that is not the cache's.
    disk, keep = tmp_path / "disk", tmp_path / "keep"
    disk.mkdir()
    keep.mkdir()
    (keep / "file").write_text("kept")
    (disk / "blob").write_text("")
    (disk / "elsewhere").symlink_to(keep)
    cache = tmp_path / "cache"
    cache.symlink_to(disk)
    assert ModelDownloader._remove_cache_dir(cache) is True
    assert cache.is_symlink() and list(disk.iterdir()) == []
    assert (keep / "file").read_text() == "kept"


def test_a_linked_cache_that_could_not_be_emptied_is_not_reported_cleared(tmp_path, monkeypatch):
    disk = tmp_path / "disk"
    disk.mkdir()
    (disk / "blob").write_text("")
    cache = tmp_path / "cache"
    cache.symlink_to(disk)

    def unlink(self, missing_ok=False):
        raise PermissionError(self)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert ModelDownloader._remove_cache_dir(cache) is False
