"""ReleaseAssetDownloader: what counts as present, and what is refused."""

import hashlib

from dreamference.hardware import ReleaseAssetDownloader
from dreamference.hardware import model_matrix_registry as registry
from dreamference.hardware.model_downloader import ModelDownloader

REPO = "dreamference/test-branch-drafter"
REVISION = "models-2026-01-01"
DATA = b"pinned bytes"
DIGEST = hashlib.sha256(DATA).hexdigest()


def _hosted(monkeypatch):
    pin = {"url": f"https://example.invalid/{REVISION}/w.bin", "sha256": DIGEST}
    monkeypatch.setitem(
        registry.RELEASE_HOSTED_MODELS, REPO, {"revision": REVISION, "files": {"w.bin": pin}}
    )
    return ModelDownloader.get_hf_cache_dir() / ("models--" + REPO.replace("/", "--"))


def test_a_model_not_hosted_as_release_assets_is_never_present():
    assert ReleaseAssetDownloader.is_present("qwen3.8-27b-nvfp4-dflash2") is False


def test_a_snapshot_link_to_a_blob_that_is_gone_is_not_present(monkeypatch):
    folder = _hosted(monkeypatch)
    (folder / "blobs").mkdir(parents=True)
    snapshot = folder / "snapshots" / REVISION
    snapshot.mkdir(parents=True)
    (snapshot / "w.bin").symlink_to(folder / "blobs" / DIGEST)
    assert ReleaseAssetDownloader.is_present(REPO) is False
    (folder / "blobs" / DIGEST).write_bytes(DATA)
    assert ReleaseAssetDownloader.is_present(REPO) is True


def test_a_snapshot_link_to_another_blob_is_not_present(monkeypatch):
    folder = _hosted(monkeypatch)
    (folder / "blobs").mkdir(parents=True)
    (folder / "blobs" / DIGEST).write_bytes(DATA)
    snapshot = folder / "snapshots" / REVISION
    snapshot.mkdir(parents=True)
    (folder / "blobs" / "other").write_bytes(b"x")
    (snapshot / "w.bin").symlink_to(folder / "blobs" / "other")
    assert ReleaseAssetDownloader.is_present(REPO) is False


def test_downloading_a_model_that_is_not_release_hosted_fetches_and_writes_nothing(monkeypatch):
    import sys

    monkeypatch.setattr(
        ReleaseAssetDownloader, "download", sys.modules["conftest"].REAL_RELEASE_DOWNLOAD
    )

    def refuse(*args, **kwargs):
        raise AssertionError("the network was asked")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    assert ReleaseAssetDownloader.download("qwen3.8-27b-nvfp4-dflash2") is False
    assert not ModelDownloader.get_hf_cache_dir().exists()
