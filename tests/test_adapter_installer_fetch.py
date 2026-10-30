"""How the adapter installer fetches a snapshot and removes one it no longer needs.

A missing library or a failed download is a refusal that names the repository and commit, never
a half-written path; the removal goes through the hub's cache API and touches only the one
revision of the one repository, and a cache it cannot scan is left alone.
"""

import sys
import types

import pytest

from dreamference.hardware.adapter_installer import AdapterInstaller

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
