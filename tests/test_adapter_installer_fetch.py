"""How the adapter installer fetches a snapshot and removes one it no longer needs.

A missing library or a failed download is a refusal that names the repository and commit, never
a half-written path; the removal goes through the hub's cache API and touches only the one
revision of the one repository, and a cache it cannot scan is left alone.
"""

import sys
import types

import pytest

from dreamference.hardware.adapter_installer import AdapterInstaller
from dreamference.hardware.model_downloader import ModelDownloader

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _hub(monkeypatch, **functions):
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(**functions))


def test_without_the_library_the_fetch_is_refused(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(ValueError, match="huggingface_hub is not installed"):
        AdapterInstaller.fetch("org/adapter", COMMIT, ["adapter.safetensors"], None)


@pytest.mark.parametrize("error", [OSError("disk full"), ValueError("revision not found")])
def test_a_failed_download_is_refused_with_the_repository_and_commit(monkeypatch, error):
    def snapshot_download(**kwargs):
        raise error

    _hub(monkeypatch, snapshot_download=snapshot_download)
    with pytest.raises(ValueError) as refused:
        AdapterInstaller.fetch("org/adapter", COMMIT, ["adapter.safetensors"], None)
    assert str(refused.value) == f"the download from org/adapter@0123456789ab failed ({error})"


def test_a_download_fetches_only_the_named_files_at_the_commit(monkeypatch, tmp_path):
    calls = []

    def snapshot_download(**kwargs):
        calls.append(kwargs)
        return str(tmp_path / "snapshot")

    _hub(monkeypatch, snapshot_download=snapshot_download)
    folder = AdapterInstaller.fetch("org/adapter", COMMIT, ["adapter.safetensors"], "secret")
    assert folder == tmp_path / "snapshot"
    assert calls == [
        {
            "repo_id": "org/adapter",
            "revision": COMMIT,
            "allow_patterns": ["adapter.safetensors"],
            "token": "secret",
        }
    ]


class _Cache:
    def __init__(self, repos):
        self.repos = repos
        self.deleted = []

    def delete_revisions(self, *revisions):
        self.deleted.append(revisions)
        return types.SimpleNamespace(execute=lambda: self.deleted.append("executed"))


def _repo(repo_id, *commits):
    revisions = [types.SimpleNamespace(commit_hash=commit) for commit in commits]
    return types.SimpleNamespace(repo_id=repo_id, revisions=revisions)


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(ModelDownloader, "get_hf_cache_dir", classmethod(lambda cls: tmp_path))
    found = _Cache(
        [
            _repo("org/adapter", COMMIT, "f" * 40),
            _repo("org/other", COMMIT),
        ]
    )
    scanned = []

    def scan_cache_dir(path):
        scanned.append(path)
        return found

    _hub(monkeypatch, scan_cache_dir=scan_cache_dir)
    found.scanned = scanned
    return found


def test_only_the_one_revision_of_the_one_repository_is_deleted(cache, tmp_path):
    AdapterInstaller.delete_snapshot("org/adapter", COMMIT)
    assert cache.scanned == [tmp_path]
    assert cache.deleted == [(COMMIT,), "executed"]


@pytest.mark.parametrize(("repo", "commit"), [("org/adapter", "e" * 40), ("org/missing", COMMIT)])
def test_without_a_matching_revision_nothing_is_deleted(cache, repo, commit):
    AdapterInstaller.delete_snapshot(repo, commit)
    assert cache.deleted == []


@pytest.mark.parametrize("error", [OSError("unreadable"), ValueError("not a cache")])
def test_a_cache_that_cannot_be_scanned_is_left_alone(monkeypatch, error):
    def scan_cache_dir(path):
        raise error

    _hub(monkeypatch, scan_cache_dir=scan_cache_dir)
    assert AdapterInstaller.delete_snapshot("org/adapter", COMMIT) is None


def test_without_the_library_nothing_is_deleted(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    assert AdapterInstaller.delete_snapshot("org/adapter", COMMIT) is None
