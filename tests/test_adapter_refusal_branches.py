"""Adapter refusals and fallbacks the main adapter tests do not reach."""

import io
import json
import types

import pytest

from dreamference.hardware import adapter_verifier
from dreamference.hardware.adapter_catalog import AdapterCatalog
from dreamference.hardware.adapter_installer import AdapterInstaller
from dreamference.hardware.adapter_manifest import AdapterManifest
from dreamference.hardware.adapter_signature import AdapterSignature
from dreamference.hardware.adapter_store import AdapterStore
from dreamference.hardware.adapter_verifier import AdapterVerifier

COMMIT = "3" * 40


def test_a_catalogue_without_the_asked_version_finds_nothing(monkeypatch):
    monkeypatch.setattr(AdapterCatalog, "entries", {"rust": [{"version": "1", "repo": "o/r"}]})
    assert AdapterCatalog.find("rust", "2") is None
    assert AdapterCatalog.find("go", None) is None
    assert AdapterCatalog.find("rust", None)["version"] == "1"


def test_a_repo_without_a_commit_is_refused():
    with pytest.raises(ValueError, match="not <org>/<repo>@<commit>"):
        AdapterCatalog.parse_repo("dreamference/mightling-rust")


def test_a_manifest_that_is_a_json_list_is_refused():
    with pytest.raises(ValueError, match="not a JSON object"):
        AdapterManifest.parse(b"[1, 2]")


def test_a_manifest_section_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError, match="base is not an object"):
        AdapterManifest._section({"base": "x"}, "base", ("repo",))


def test_a_key_with_one_field_is_left_out_of_the_allowed_signers(monkeypatch):
    monkeypatch.setattr(AdapterSignature, "keys", ("ssh-ed25519", "ssh-ed25519 AAAA comment"))
    lines = AdapterSignature.allowed_signers().splitlines()
    assert len(lines) == 1
    assert lines[0].endswith("ssh-ed25519 AAAA")


def test_an_installed_set_whose_adapters_is_not_an_object_reads_as_empty():
    path = AdapterStore.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"adapters": [1]}), encoding="utf-8")
    assert AdapterStore.entries() == {}


def test_dropping_an_adapter_that_is_not_installed_returns_false_and_changes_nothing():
    path = AdapterStore.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"adapters": {"a": {"repo": "o/r"}}})
    path.write_text(text, encoding="utf-8")
    assert AdapterStore.drop("b") is False
    assert path.read_text(encoding="utf-8") == text


def test_an_installed_file_that_is_not_an_installed_set_is_not_changed():
    path = AdapterStore.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="so it was not changed"):
        AdapterStore.drop("a")
    assert path.read_text(encoding="utf-8") == "[]"


def test_a_repo_install_on_a_full_disk_is_refused(monkeypatch):
    monkeypatch.setattr(
        AdapterInstaller, "free_space_problem", classmethod(lambda cls, size: "full")
    )
    with pytest.raises(ValueError, match="full"):
        AdapterInstaller._resolve(None, f"o/r@{COMMIT}", None)


def test_a_repo_install_with_room_has_no_manifest_pin(monkeypatch):
    monkeypatch.setattr(AdapterInstaller, "free_space_problem", classmethod(lambda cls, size: None))
    assert AdapterInstaller._resolve(None, f"o/r@{COMMIT}", None) == ("o/r", COMMIT, None)


def test_a_catalogue_adapter_on_a_full_disk_is_refused(monkeypatch):
    entry = {"repo": "o/r", "commit": COMMIT, "manifest_sha256": "a", "size": 5, "base_alias": "k"}
    monkeypatch.setattr(AdapterCatalog, "find", classmethod(lambda cls, name, version: entry))
    monkeypatch.setattr(
        AdapterInstaller, "free_space_problem", classmethod(lambda cls, size: "full")
    )
    with pytest.raises(ValueError, match="full"):
        AdapterInstaller._resolve("rust", None, {"key": "k"})


def test_a_manifest_name_with_a_slash_is_refused_before_the_store_is_read():
    manifest = types.SimpleNamespace(name="../evil")
    with pytest.raises(ValueError, match="not a name an adapter may have"):
        AdapterInstaller._check_name(manifest, "o/r")


def test_deleting_a_snapshot_the_cache_does_not_hold_deletes_nothing(monkeypatch):
    deleted = []
    cache = types.SimpleNamespace(
        repos=[
            types.SimpleNamespace(
                repo_id="o/r", revisions=[types.SimpleNamespace(commit_hash="other")]
            )
        ],
        delete_revisions=lambda *hashes: deleted.append(hashes),
    )
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda path=None: cache)
    AdapterInstaller.delete_snapshot("o/r", COMMIT)
    assert deleted == []


def test_a_served_base_that_is_not_in_the_matrix_is_none(monkeypatch):
    monkeypatch.setattr(AdapterVerifier, "served_id", classmethod(lambda cls, host: None))
    monkeypatch.setattr(AdapterVerifier, "base_for", classmethod(lambda cls, key: None))
    assert AdapterVerifier.served_base("http://x", "unknown-model") is None


def _answer(monkeypatch, record):
    body = io.BytesIO(json.dumps(record).encode())
    monkeypatch.setattr(adapter_verifier.urllib.request, "urlopen", lambda *a, **k: body)
    # a context manager over a BytesIO closes it, which json.load has read by then


def test_a_server_that_lists_only_adapters_has_no_served_base_id(monkeypatch):
    _answer(monkeypatch, {"data": [{"id": "base:rust", "parent": "base"}, "text", {"id": 3}]})
    assert AdapterVerifier.served_id("http://x") is None


def test_a_server_answer_that_is_not_a_list_has_no_served_id(monkeypatch):
    _answer(monkeypatch, {"data": "nope"})
    assert AdapterVerifier.served_id("http://x") is None


def test_a_json_file_that_is_a_list_is_refused(tmp_path):
    path = tmp_path / "adapter_config.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a JSON object"):
        AdapterVerifier._json(path)


def test_a_name_missing_from_the_catalogue_is_refused_with_the_repo_hint(monkeypatch):
    monkeypatch.setattr(AdapterCatalog, "find", classmethod(lambda cls, name, version: None))
    with pytest.raises(ValueError, match="not in this release's catalogue"):
        AdapterInstaller._resolve("rust@2", None, None)


def test_an_adapter_made_for_another_base_is_refused(monkeypatch):
    entry = {
        "repo": "o/r",
        "commit": COMMIT,
        "manifest_sha256": "a",
        "size": 5,
        "base_alias": "other",
    }
    monkeypatch.setattr(AdapterCatalog, "find", classmethod(lambda cls, name, version: entry))
    with pytest.raises(ValueError, match="base mismatch"):
        AdapterInstaller._resolve("rust", None, {"key": "served"})


def test_a_catalogue_adapter_with_room_resolves_to_its_pin(monkeypatch):
    entry = {
        "repo": "o/r",
        "commit": COMMIT,
        "manifest_sha256": "abc",
        "size": 5,
        "base_alias": "k",
    }
    monkeypatch.setattr(AdapterCatalog, "find", classmethod(lambda cls, name, version: entry))
    monkeypatch.setattr(AdapterInstaller, "free_space_problem", classmethod(lambda cls, size: None))
    assert AdapterInstaller._resolve("rust", None, {"key": "k"}) == ("o/r", COMMIT, "abc")


def test_a_name_another_repository_holds_is_refused(monkeypatch):
    monkeypatch.setattr(AdapterStore, "get", classmethod(lambda cls, name: {"repo": "o/first"}))
    with pytest.raises(ValueError, match="installed from o/first"):
        AdapterInstaller._check_name(types.SimpleNamespace(name="rust"), "o/second")
