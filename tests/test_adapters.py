"""Adapters: the manifest, the signature, install, remove, verify and the copy to a paired node.

specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §16. No Hugging Face request (the fetch is replaced by a
copy from a folder standing in for the repository), signatures by session keys from ssh-keygen,
never the release key.
"""

import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import tarfile
from pathlib import Path

import pytest

from dreamference.cli.adapter_command import AdapterCommand
from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController
from dreamference.hardware.adapter_base_digest import AdapterBaseDigest
from dreamference.hardware.adapter_catalog import AdapterCatalog
from dreamference.hardware.adapter_installer import AdapterInstaller
from dreamference.hardware.adapter_manifest import AdapterManifest
from dreamference.hardware.adapter_signature import RELEASE_KEYS, AdapterSignature
from dreamference.hardware.adapter_store import AdapterStore
from dreamference.hardware.adapter_verifier import AdapterVerifier
from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS, ModelMatrixRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_REPO = "RadixArk/Qwen3.8-27B-NVFP4"
BASE_REVISION = ModelMatrixRegistry.get_launch_overrides(DEFAULT_MODEL_ALIAS)["revision"]
ADAPTER_REPO = "dreamference/mightling-rust"
COMMIT = "1" * 40
LAYER = "base_model.model.model.language_model.layers"


@pytest.fixture
def roomy_disk(monkeypatch):
    """The receiving node has room for the adapter.

    The check reads the real disk, and a CI runner has less free than an adapter's margin.
    """
    import types

    monkeypatch.setattr(
        "dreamference.node.node_adapter_sync.shutil.disk_usage",
        lambda path: types.SimpleNamespace(total=4 << 40, used=2 << 40, free=2 << 40),
    )


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    """Two session keys: one standing in for the release key, one for a stranger's."""
    folder = tmp_path_factory.mktemp("keys")
    made = {}
    for name in ("release", "stranger"):
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(folder / name)],
            check=True,
        )
        made[name] = folder / name
    return made


@pytest.fixture(autouse=True)
def session_keys(keys, monkeypatch):
    monkeypatch.setattr(
        AdapterSignature, "keys", ((keys["release"].with_suffix(".pub")).read_text().strip(),)
    )


def sign(keys, data, key="release", namespace="mightling-adapter"):
    folder = keys[key].parent / "sign"
    folder.mkdir(exist_ok=True)
    message = folder / "manifest.json"
    message.write_bytes(data)
    Path(f"{message}.sig").unlink(missing_ok=True)
    subprocess.run(
        ["ssh-keygen", "-q", "-Y", "sign", "-f", str(keys[key]), "-n", namespace, str(message)],
        check=True,
        capture_output=True,
    )
    return Path(f"{message}.sig").read_bytes()


def safetensors(tensors, dtype="BF16"):
    """A safetensors file of zero tensors, each of shape [2, 2]."""
    header, offset = {}, 0
    for name in tensors:
        header[name] = {"dtype": dtype, "shape": [2, 2], "data_offsets": [offset, offset + 8]}
        offset += 8
    text = json.dumps(header).encode()
    return struct.pack("<Q", len(text)) + text + b"\0" * offset


def base_snapshot():
    """The served base in this test's hub cache: a weight file linked to its blob, and a config."""
    hub = ModelDownloader.get_hf_cache_dir()
    root = hub / f"models--{BASE_REPO.replace('/', '--')}"
    snapshot = root / "snapshots" / BASE_REVISION
    snapshot.mkdir(parents=True, exist_ok=True)
    (root / "blobs").mkdir(exist_ok=True)
    weights = b"weights"
    blob = root / "blobs" / hashlib.sha256(weights).hexdigest()
    blob.write_bytes(weights)
    link = snapshot / "model.safetensors"
    if not link.is_symlink():
        link.symlink_to(os.path.relpath(blob, snapshot))
    (snapshot / "config.json").write_text('{"model_type": "qwen3_5"}')
    return snapshot


def served(snapshot):
    return {
        "key": DEFAULT_MODEL_ALIAS,
        "repo": BASE_REPO,
        "revision": BASE_REVISION,
        "snapshot": str(snapshot),
        "source": "server",
    }


ADAPTER_DEFAULTS = {
    "name": "rust",
    "modules": ("q_proj", "in_proj_qkvz"),
    "tensors": None,
    "rank": 16,
    "base_digest": None,
    "key": "release",
    "namespace": "mightling-adapter",
}


def make_adapter(folder, keys, **changes):
    """Writes an adapter repository's files into `folder`; returns the manifest's bytes.

    `changes` overrides ADAPTER_DEFAULTS; any other key replaces a top-level key of the manifest.
    """
    options = {**ADAPTER_DEFAULTS, **{k: v for k, v in changes.items() if k in ADAPTER_DEFAULTS}}
    manifest_changes = {k: v for k, v in changes.items() if k not in ADAPTER_DEFAULTS}
    name, modules, tensors, rank = (
        options["name"],
        options["modules"],
        options["tensors"],
        options["rank"],
    )
    base_digest, key, namespace = options["base_digest"], options["key"], options["namespace"]
    folder.mkdir(parents=True, exist_ok=True)
    tensors = tensors or [
        f"{LAYER}.3.self_attn.q_proj.lora_A.weight",
        f"{LAYER}.3.self_attn.q_proj.lora_B.weight",
        f"{LAYER}.0.linear_attn.in_proj_q.lora_A.weight",
    ]
    (folder / "adapter_model.safetensors").write_bytes(safetensors(tensors))
    (folder / "adapter_config.json").write_text(
        json.dumps({"r": rank, "lora_alpha": 32, "target_modules": list(modules)})
    )
    files = {
        path: "sha256:" + hashlib.sha256((folder / path).read_bytes()).hexdigest()
        for path in ("adapter_model.safetensors", "adapter_config.json")
    }
    manifest = {
        "schema": 1,
        "name": name,
        "display_name": "Rust",
        "version": "1.0.0",
        "description": "Rust specialist.",
        "base": {
            "registry_alias": DEFAULT_MODEL_ALIAS,
            "repo": BASE_REPO,
            "revision": BASE_REVISION,
            "digest": base_digest or AdapterBaseDigest.compute(base_snapshot()),
        },
        "lora": {"rank": rank, "alpha": 32, "target_modules": list(modules), "dtype": "bfloat16"},
        "files": files,
        "licence": {"spdx": "Apache-2.0"},
        "signer": "mightling-release-2026-10",
    }
    manifest.update(manifest_changes)
    data = json.dumps(manifest, indent=2).encode()
    (folder / "manifest.json").write_bytes(data)
    (folder / "manifest.json.sig").write_bytes(sign(keys, data, key, namespace))
    return data


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """The Hugging Face repository, as a folder; the fetch copies what it is asked for, and logs it."""
    source = tmp_path / "remote"
    fetched = []

    def fetch(repo, commit, files, token):
        fetched.append(list(files))
        snapshot = (
            ModelDownloader.get_hf_cache_dir()
            / f"models--{repo.replace('/', '--')}"
            / "snapshots"
            / commit
        )
        snapshot.mkdir(parents=True, exist_ok=True)
        for name in files:
            if (source / name).is_file():
                (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, snapshot / name)
        return snapshot

    monkeypatch.setattr(AdapterInstaller, "fetch", classmethod(lambda cls, *a: fetch(*a)))
    monkeypatch.setattr(
        AdapterInstaller, "delete_snapshot", classmethod(lambda cls, repo, commit: None)
    )
    return source, fetched


@pytest.fixture
def catalogued(monkeypatch):
    def entry(data):
        monkeypatch.setattr(
            AdapterCatalog,
            "entries",
            {
                "rust": [
                    {
                        "version": "1.0.0",
                        "display_name": "Rust",
                        "repo": ADAPTER_REPO,
                        "commit": COMMIT,
                        "manifest_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                        "base_alias": DEFAULT_MODEL_ALIAS,
                        "size": 1000,
                    }
                ]
            },
        )

    return entry


# -- the manifest ------------------------------------------------------------------------------------


def valid_manifest():
    return {
        "schema": 1,
        "name": "rust",
        "display_name": "Rust",
        "version": "1.0.0",
        "description": "Rust.",
        "base": {"repo": BASE_REPO, "revision": "a" * 40, "digest": "sha256:" + "b" * 64},
        "lora": {"rank": 16, "target_modules": ["q_proj"]},
        "files": {
            "adapter_model.safetensors": "sha256:" + "c" * 64,
            "adapter_config.json": "sha256:" + "d" * 64,
        },
        "licence": {"spdx": "Apache-2.0"},
    }


def test_a_valid_manifest_parses():
    manifest = AdapterManifest.parse(json.dumps(valid_manifest()).encode())
    assert (manifest.name, manifest.rank, manifest.target_modules) == ("rust", 16, ("q_proj",))


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"schema": 2}, "schema"),
        ({"surprise": 1}, "does not know"),
        ({"licence": None}, "licence"),
        (
            {
                "files": {
                    "adapter_model.safetensors": "sha256:" + "c" * 64,
                    "adapter_config.json": "sha256:" + "d" * 64,
                    "adapter_model.bin": "sha256:" + "e" * 64,
                }
            },
            "neither safetensors",
        ),
        ({"files": {"../adapter_model.safetensors": "sha256:" + "c" * 64}}, "leaves"),
        (
            {
                "files": {
                    "adapter_model.safetensors": "md5:x",
                    "adapter_config.json": "sha256:" + "d" * 64,
                }
            },
            "digest",
        ),
        ({"name": "Rust Adapter"}, "name"),
        ({"lora": {"rank": 0, "target_modules": ["q_proj"]}}, "rank"),
    ],
)
def test_a_manifest_this_release_does_not_accept_is_refused(change, reason):
    record = valid_manifest()
    record.update(change)
    if change.get("licence", 0) is None:
        del record["licence"]
    with pytest.raises(ValueError, match=reason):
        AdapterManifest.parse(json.dumps(record).encode())


def test_a_branch_in_place_of_a_commit_is_refused():
    assert AdapterCatalog.parse_repo(f"{ADAPTER_REPO}@{COMMIT}") == (ADAPTER_REPO, COMMIT)
    with pytest.raises(ValueError, match="never a branch"):
        AdapterCatalog.parse_repo(f"{ADAPTER_REPO}@main")


# -- the signature -----------------------------------------------------------------------------------


def test_the_package_carries_the_release_keys():
    lines = [
        line
        for line in (REPO_ROOT / "ling-rs" / "release-signing.pub").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert list(RELEASE_KEYS) == lines


def test_a_signed_manifest_verifies_and_anything_else_does_not(keys):
    data = b'{"schema": 1}'
    assert AdapterSignature.problem(data, sign(keys, data)) is None
    assert AdapterSignature.problem(data + b" ", sign(keys, data)) is not None
    assert AdapterSignature.problem(data, sign(keys, data, key="stranger")) is not None
    assert (
        AdapterSignature.problem(data, sign(keys, data, namespace="mightling-release")) is not None
    )
    assert AdapterSignature.problem(data, b"") is not None


# -- install -----------------------------------------------------------------------------------------


def test_a_catalogued_adapter_installs_at_its_pinned_commit_with_exactly_its_files(
    keys, remote, catalogued
):
    source, fetched = remote
    catalogued(make_adapter(source, keys))
    entry = AdapterInstaller.install("rust", None, served(base_snapshot()))
    assert fetched[0] == ["manifest.json", "manifest.json.sig"]
    assert sorted(fetched[1]) == sorted(
        ["manifest.json", "manifest.json.sig", "adapter_model.safetensors", "adapter_config.json"]
    )
    assert (entry["commit"], entry["trust"], entry["snapshot"]) == (
        COMMIT,
        "dreamference",
        f"models--dreamference--mightling-rust/snapshots/{COMMIT}",
    )
    assert AdapterStore.get("rust")["repo"] == ADAPTER_REPO
    assert AdapterStore.path().stat().st_mode & 0o777 == 0o600
    assert AdapterInstaller.verify(AdapterStore.get("rust"), served(base_snapshot())) is None


def test_a_file_that_differs_from_its_hash_stops_the_install_and_records_nothing(
    keys, remote, catalogued
):
    source, _ = remote
    catalogued(make_adapter(source, keys))
    (source / "adapter_config.json").write_text('{"r": 16, "target_modules": ["q_proj"], "x": 1}')
    with pytest.raises(ValueError, match="does not match"):
        AdapterInstaller.install("rust", None, served(base_snapshot()))
    assert AdapterStore.entries() == {}


def test_a_manifest_the_catalogue_does_not_pin_is_refused(keys, remote, catalogued):
    source, _ = remote
    catalogued(b"another manifest")
    make_adapter(source, keys)
    with pytest.raises(ValueError, match="pins"):
        AdapterInstaller.install("rust", None, served(base_snapshot()))


def test_a_base_mismatch_stops_the_install_before_any_adapter_file_is_fetched(
    keys, remote, catalogued
):
    source, fetched = remote
    catalogued(make_adapter(source, keys, base_digest="sha256:" + "0" * 64))
    with pytest.raises(ValueError, match="base mismatch"):
        AdapterInstaller.install("rust", None, served(base_snapshot()))
    assert fetched == [["manifest.json", "manifest.json.sig"]]
    other = dict(served(base_snapshot()), key="qwen3.8-27b-minima-nvfp4-dflash2")
    fetched.clear()
    with pytest.raises(ValueError, match="base mismatch"):
        AdapterInstaller.install("rust", None, other)
    assert fetched == []


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("unsigned", "not signed"),
        ("stranger", "not a Dreamference"),
        ("release-namespace", "not a Dreamference"),
    ],
)
def test_only_a_dreamference_signature_installs_without_third_party(keys, remote, kind, reason):
    source, _ = remote
    if kind == "stranger":
        make_adapter(source, keys, key="stranger")
    elif kind == "release-namespace":
        make_adapter(source, keys, namespace="mightling-release")
    else:
        make_adapter(source, keys)
        (source / "manifest.json.sig").unlink()
    with pytest.raises(ValueError, match=reason):
        AdapterInstaller.install(None, f"{ADAPTER_REPO}@{COMMIT}", served(base_snapshot()))
    assert AdapterStore.entries() == {}


def test_a_third_party_adapter_installs_only_with_its_name_typed(keys, remote):
    source, _ = remote
    make_adapter(source, keys, key="stranger")
    with pytest.raises(ValueError, match="not confirmed"):
        AdapterInstaller.install(
            None, f"{ADAPTER_REPO}@{COMMIT}", served(base_snapshot()), confirm=lambda m: False
        )
    assert AdapterStore.entries() == {}
    entry = AdapterInstaller.install(
        None,
        f"{ADAPTER_REPO}@{COMMIT}",
        served(base_snapshot()),
        confirm=lambda m: m.name == "rust",
    )
    assert entry["trust"] == "third-party"
    assert AdapterInstaller.verify(entry, served(base_snapshot())) is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"modules": ("gate_up_proj",)}, "does not apply adapters"),
        (
            {"modules": ("q_proj",), "tensors": [f"{LAYER}.0.mlp.down_proj.lora_A.weight"]},
            "not a LoRA tensor",
        ),
        (
            {
                "modules": ("in_proj_qkvz",),
                "tensors": [f"{LAYER}.0.linear_attn.in_proj_qkv.lora_A.weight"],
            },
            "not a LoRA tensor",
        ),
        ({"rank": 32}, "ceiling"),
    ],
)
def test_an_adapter_the_recipe_cannot_serve_is_refused(keys, remote, changes, reason):
    source, _ = remote
    make_adapter(source, keys, **changes)
    with pytest.raises(ValueError, match=reason):
        AdapterInstaller.install(None, f"{ADAPTER_REPO}@{COMMIT}", served(base_snapshot()))


def test_remove_drops_the_entry_and_the_snapshot_through_the_cache_api(
    keys, remote, catalogued, monkeypatch
):
    source, _ = remote
    catalogued(make_adapter(source, keys))
    AdapterInstaller.install("rust", None, served(base_snapshot()))
    deleted = []
    monkeypatch.setattr(
        AdapterInstaller, "delete_snapshot", classmethod(lambda cls, r, c: deleted.append((r, c)))
    )
    assert AdapterInstaller.remove("rust") is True
    assert AdapterStore.entries() == {} and deleted == [(ADAPTER_REPO, COMMIT)]
    assert AdapterInstaller.remove("rust") is False


def test_verify_catches_a_file_changed_after_the_install(keys, remote, catalogued):
    source, _ = remote
    catalogued(make_adapter(source, keys))
    entry = AdapterInstaller.install("rust", None, served(base_snapshot()))
    weights = AdapterInstaller.folder_of(entry) / "adapter_model.safetensors"
    weights.write_bytes(weights.read_bytes() + b"\0")
    assert "does not match" in AdapterInstaller.verify(entry, served(base_snapshot()))


# -- the base digest and the served id ---------------------------------------------------------------


def test_the_base_digest_reads_weight_hashes_from_their_blobs_and_changes_with_a_config():
    snapshot = base_snapshot()
    listing = AdapterBaseDigest.listing(snapshot)
    assert listing == [
        f"{hashlib.sha256(b'{"model_type": "qwen3_5"}').hexdigest()}  config.json",
        f"{hashlib.sha256(b'weights').hexdigest()}  model.safetensors",
    ]
    before = AdapterBaseDigest.compute(snapshot)
    (snapshot / "config.json").write_text("{}")
    assert AdapterBaseDigest.compute(snapshot) != before


def test_a_digest_cached_while_the_base_was_still_downloading_is_not_kept_once_it_is_complete():
    # A digest taken over a snapshot that lacked a file was cached for the revision forever, so
    # the complete base read as a permanent base mismatch.
    snapshot = base_snapshot()
    shard = snapshot / "model.safetensors"
    target = shard.readlink()
    shard.unlink()
    partial = AdapterBaseDigest.of(BASE_REPO, BASE_REVISION, snapshot)
    shard.symlink_to(target)
    complete = AdapterBaseDigest.compute(snapshot)
    assert AdapterBaseDigest.of(BASE_REPO, BASE_REVISION, snapshot) == complete != partial


def test_an_adapter_id_names_its_base_entry():
    assert ModelMatrixRegistry.key_for_served_id(f"{BASE_REPO}+rust") == DEFAULT_MODEL_ALIAS


def test_only_the_default_model_serves_adapters_never_minima():
    assert ModelMatrixRegistry.get_lora_recipe(DEFAULT_MODEL_ALIAS)["max_lora_rank"] == 16
    assert ModelMatrixRegistry.get_lora_recipe("qwen3.8-27b-minima-nvfp4-dflash2") is None


# -- the command -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["adapter", "list", "--bogus"],
        ["adapter", "install", "rust", "--yes"],
        ["adapter", "remove"],
        ["adapter", "verify", "a", "b"],
        ["adapter", "install", "rust", "extra"],
    ],
)
def test_every_adapter_subcommand_rejects_unknown_arguments(argv):
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_on_a_client_the_command_names_the_node(capsys):
    args = DreamferenceCLIController.build_parser().parse_args(["adapter", "list"])

    class Config:
        vllm_host = "http://node.local:8000"
        model = DEFAULT_MODEL_ALIAS

    assert AdapterCommand.run(args, Config()) == 1
    assert "installed on the node" in capsys.readouterr().out


# -- the copy to a paired node -----------------------------------------------------------------------


def adapter_tar(snapshot_root):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        archive.add(snapshot_root, arcname=snapshot_root.name)
    stream.seek(0)
    return stream


class Stdin:
    def __init__(self, stream):
        self.buffer = stream


def staged_repo(tmp_path, keys, **changes):
    folder = tmp_path / "sent" / "models--dreamference--mightling-rust"
    make_adapter(folder / "snapshots" / COMMIT, keys, **changes)
    return folder


def test_a_node_installs_a_signed_adapter_it_receives(tmp_path, keys, monkeypatch, roomy_disk):
    from dreamference.node.node_adapter_sync import NodeAdapterSync

    monkeypatch.setattr(
        AdapterVerifier, "served_base", classmethod(lambda cls, h, m: served(base_snapshot()))
    )
    monkeypatch.setattr("sys.stdin", Stdin(adapter_tar(staged_repo(tmp_path, keys))))
    assert NodeAdapterSync.receive(["rust", ADAPTER_REPO, COMMIT, "100000"]) == 0
    assert AdapterStore.get("rust")["trust"] == "dreamference"


def test_a_node_refuses_a_tampered_or_unsigned_adapter_and_adds_nothing(
    tmp_path, keys, monkeypatch, roomy_disk
):
    from dreamference.node.node_adapter_sync import NodeAdapterSync

    folder = staged_repo(tmp_path, keys, key="stranger")
    monkeypatch.setattr("sys.stdin", Stdin(adapter_tar(folder)))
    assert NodeAdapterSync.receive(["rust", ADAPTER_REPO, COMMIT, "100"]) == 1
    assert AdapterStore.entries() == {}
    assert not (ModelDownloader.get_hf_cache_dir() / folder.name).exists()
    assert NodeAdapterSync.receive(["../x", ADAPTER_REPO, COMMIT, "100"]) == 2


def test_a_node_serving_another_base_keeps_the_adapter_as_a_base_mismatch(
    tmp_path, keys, monkeypatch, capsys, roomy_disk
):
    from dreamference.node.node_adapter_sync import NodeAdapterSync

    monkeypatch.setattr(AdapterVerifier, "served_base", classmethod(lambda cls, h, m: None))
    monkeypatch.setattr("sys.stdin", Stdin(adapter_tar(staged_repo(tmp_path, keys))))
    assert NodeAdapterSync.receive(["rust", ADAPTER_REPO, COMMIT, "100000"]) == 0
    assert "base mismatch" in capsys.readouterr().out


def test_the_sender_copies_signed_adapters_of_the_model_only(monkeypatch):
    from dreamference.node.node_adapter_sync import NodeAdapterSync

    for name, trust, base in (
        ("rust", "dreamference", BASE_REPO),
        ("evil", "third-party", BASE_REPO),
        ("other", "dreamference", "minima-ai/mnma_qwen3.8_27b_nvfp4"),
    ):
        AdapterStore.put(
            {"name": name, "trust": trust, "base_repo": base, "repo": f"x/{name}", "commit": COMMIT}
        )
    assert [e["name"] for e in NodeAdapterSync.signed_for(DEFAULT_MODEL_ALIAS)] == ["rust"]
    sent = []

    class Process:
        returncode = 0
        stdout = None

        def __init__(self, command, **kwargs):
            sent.append(command)

        def wait(self):
            return 0

    monkeypatch.setattr("dreamference.node.node_adapter_sync.subprocess.Popen", Process)
    monkeypatch.setattr(
        "dreamference.node.node_pairing.NodePairing.ssh_command",
        classmethod(lambda cls, record, request: ["ssh", "node", request]),
    )
    code, lines = NodeAdapterSync.send({"name": "second"}, DEFAULT_MODEL_ALIAS)
    assert code == 0 and len(lines) == 1
    assert [c[-1] for c in sent if c[0] == "ssh"] == [f"adapter-receive rust x/rust {COMMIT} 0"]
    assert NodeAdapterSync.after(1, "second", DEFAULT_MODEL_ALIAS, None) == (1, [])


def test_an_installed_set_that_cannot_be_read_is_not_replaced_by_one_entry():
    # Read as empty, the file used to be rewritten with the new entry alone: every adapter installed
    # before it was forgotten (and its snapshot left behind, unremovable by `adapter remove`).
    path = AdapterStore.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = '{"schema": 1, "adapters": {"rust": {"name": "rust"}},\n'
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"installed\.json"):
        AdapterStore.put({"name": "go", "repo": "x/go", "commit": COMMIT})
    with pytest.raises(ValueError, match=r"installed\.json"):
        AdapterStore.drop("rust")
    assert path.read_text(encoding="utf-8") == text
    assert AdapterStore.entries() == {}


def test_an_entry_that_cannot_be_written_leaves_the_installed_set_and_no_staging_file():
    AdapterStore.put({"name": "rust", "repo": "x/rust", "commit": COMMIT})
    before = AdapterStore.path().read_text(encoding="utf-8")
    with pytest.raises(TypeError):
        AdapterStore.put({"name": "go", "repo": object()})
    assert AdapterStore.path().read_text(encoding="utf-8") == before
    assert sorted(p.name for p in AdapterStore.folder().iterdir()) == ["installed.json"]
