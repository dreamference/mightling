"""The adapter signing job (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §5.1, §16 "Signing job").

The workflow is read, never run: its `run:` blocks pass `bash -n` and its shape is asserted. The
signer script runs against a stand-in for Hugging Face built from fixture bytes (no request leaves
the machine), and signs with session keys from ssh-keygen, never the release key.
"""

import ast
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from dreamference.hardware.adapter_base_digest import AdapterBaseDigest
from dreamference.hardware.adapter_signature import RELEASE_KEYS, AdapterSignature
from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS, ModelMatrixRegistry

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "adapter-sign.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
SCRIPT = ROOT / "scripts" / "adapters" / "sign_manifest.py"

RECIPE = ModelMatrixRegistry.get_launch_overrides(DEFAULT_MODEL_ALIAS)
BASE_REPO = ModelMatrixRegistry.resolve_hf_repo(DEFAULT_MODEL_ALIAS)
BASE_REVISION = str(RECIPE["revision"])
DRAFTER_REPO = str(RECIPE["speculative_config"]["model"])
DRAFTER_REVISION = str(RECIPE["speculative_config"]["revision"])
ADAPTER_REPO = "dreamference/mightling-rust"
COMMIT = "1" * 40
PUBLISHED = "c" * 40

# name -> (content, kept in LFS)
BASE_FILES = {
    "model-00001-of-00002.safetensors": (b"base weights one", True),
    "model-00002-of-00002.safetensors": (b"base weights two", True),
    "config.json": (b'{"architectures": ["Qwen3_5ForConditionalGeneration"]}', False),
    "tokenizer.json": (b'{"model": "a large tokenizer"}', True),
    "tokenizer_config.json": (b'{"bos_token": null}', False),
    "chat_template.jinja": (b"{% for m in messages %}{{ m.content }}{% endfor %}", False),
    "README.md": (b"not covered by the digest", False),
}
DRAFTER_FILES = {
    "model.safetensors": (b"drafter weights", True),
    "config.json": (b'{"architectures": ["DFlashDraftModel"]}', False),
}
ADAPTER_FILES = {
    "adapter_model.safetensors": (b"adapter A and B", True),
    "adapter_config.json": (b'{"r": 16, "lora_alpha": 32}', False),
    "drafter/adapter_model.safetensors": (b"drafter adapter", True),
    "drafter/adapter_config.json": (b'{"r": 8}', False),
    "README.md": (b"the model card", False),
}


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sign_manifest", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


signer = load_script()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


class FakeHub:
    """Hugging Face from fixture bytes: metadata as model_info records it, downloads, uploads."""

    def __init__(self) -> None:
        self.repos: dict[tuple[str, str], dict[str, tuple[bytes, bool]]] = {
            (BASE_REPO, BASE_REVISION): dict(BASE_FILES),
            (DRAFTER_REPO, DRAFTER_REVISION): dict(DRAFTER_FILES),
            (ADAPTER_REPO, COMMIT): dict(ADAPTER_FILES),
        }
        # What a download returns instead of the recorded content, to stand in for tampering.
        self.served: dict[tuple[str, str], bytes] = {}
        self.uploads: list[tuple[str, str, dict[str, bytes], str]] = []
        self.listed: list[tuple[str, str]] = []

    def files(self, repo: str, revision: str) -> dict[str, object]:
        self.listed.append((repo, revision))
        listing = self.repos[(repo, revision)]
        return {
            path: signer.RemoteFile(
                path=path,
                size=len(data),
                sha256=sha256(data) if lfs else None,
                blob_id=git_blob(data),
            )
            for path, (data, lfs) in listing.items()
        }

    def download(self, repo: str, revision: str, path: str, folder: Path) -> Path:
        data = self.served.get((repo, path), self.repos[(repo, revision)][path][0])
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def upload(self, repo: str, parent: str, files: dict[str, bytes], message: str) -> str:
        self.uploads.append((repo, parent, files, message))
        return PUBLISHED


def snapshot_digest(tmp_path: Path, files: dict[str, tuple[bytes, bool]]) -> str:
    """The digest `ling-admin` takes from a downloaded snapshot of the same files."""
    cache = tmp_path / "hub-cache"
    blobs, snapshot = cache / "blobs", cache / "snapshots" / "rev"
    blobs.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(snapshot, ignore_errors=True)
    snapshot.mkdir(parents=True)
    for path, (data, lfs) in files.items():
        blob = blobs / (sha256(data) if lfs else git_blob(data))
        blob.write_bytes(data)
        link = snapshot / path
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(Path("../..") / "blobs" / blob.name)
    return AdapterBaseDigest.compute(snapshot)


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Two session keys: one standing in for the release key, one for a stranger's."""
    folder = tmp_path_factory.mktemp("keys")
    made = {}
    for name in ("release", "stranger"):
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(folder / name)],
            check=True,
        )
        made[name] = folder / name
    trusted = folder / "release-signing.pub"
    trusted.write_text(
        "# The key list, as ling-rs/release-signing.pub is written.\n"
        + made["release"].with_suffix(".pub").read_text()
    )
    made["trusted"] = trusted
    return made


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.delenv("HF_ADAPTER_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    fake = FakeHub()
    monkeypatch.setattr(signer, "HubClient", lambda token=None: fake)
    return fake


def write_manifest(tmp_path: Path, **changes: object) -> Path:
    """`adapters/rust/1.0.0/manifest.json` and `source.json`, correct unless changed."""
    record: dict[str, object] = {
        "schema": 1,
        "name": "rust",
        "display_name": "Rust",
        "version": "1.0.0",
        "description": "Rust specialist.",
        "base": {
            "registry_alias": DEFAULT_MODEL_ALIAS,
            "repo": BASE_REPO,
            "revision": BASE_REVISION,
            "digest": snapshot_digest(tmp_path, BASE_FILES),
        },
        "drafter_base": {
            "repo": DRAFTER_REPO,
            "revision": DRAFTER_REVISION,
            "digest": snapshot_digest(tmp_path, DRAFTER_FILES),
        },
        "lora": {"rank": 16, "alpha": 32, "target_modules": ["q_proj", "v_proj"]},
        "drafter_adapter": {
            "rank": 8,
            "target_modules": ["q_proj"],
            "files": {
                path: f"sha256:{sha256(ADAPTER_FILES[path][0])}"
                for path in ("drafter/adapter_model.safetensors", "drafter/adapter_config.json")
            },
        },
        "files": {
            path: f"sha256:{sha256(ADAPTER_FILES[path][0])}"
            for path in ("adapter_model.safetensors", "adapter_config.json")
        },
        "licence": {"spdx": "Apache-2.0", "base_licence": "Apache-2.0", "training_data": "x"},
        "signer": "mightling-release-2026-10",
    }
    source: dict[str, object] = {"repo": ADAPTER_REPO, "commit": COMMIT}
    for key, value in changes.items():
        if key == "source":
            source = value  # type: ignore[assignment]
        elif value is None:
            record.pop(key, None)
        else:
            record[key] = value
    folder = tmp_path / "adapters" / "rust" / "1.0.0"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source.json").write_text(json.dumps(source))
    manifest = folder / "manifest.json"
    manifest.write_text(json.dumps(record, indent=2) + "\n")
    return manifest


def ssh_sign(
    keys: dict[str, Path], data: bytes, key: str = "release", namespace: str = "mightling-adapter"
) -> Path:
    folder = keys[key].parent / "made"
    folder.mkdir(exist_ok=True)
    message = folder / "manifest.json"
    message.write_bytes(data)
    Path(f"{message}.sig").unlink(missing_ok=True)
    subprocess.run(
        ["ssh-keygen", "-q", "-Y", "sign", "-f", str(keys[key]), "-n", namespace, str(message)],
        check=True,
        capture_output=True,
    )
    return Path(f"{message}.sig")


def publish(manifest: Path, signature: Path, keys: dict[str, Path], tmp_path: Path) -> int:
    return int(
        signer.main(
            [
                "publish",
                str(manifest),
                "--signature",
                str(signature),
                "--trusted-keys",
                str(keys["trusted"]),
                "--entry-out",
                str(tmp_path / "entry.json"),
            ]
        )
    )


def sign(manifest: Path, key: Path, keys: dict[str, Path], out: Path) -> int:
    return int(
        signer.main(
            [
                "sign",
                str(manifest),
                "--key",
                str(key),
                "--out",
                str(out),
                "--trusted-keys",
                str(keys["trusted"]),
            ]
        )
    )


# The workflow


def workflow() -> dict[object, object]:
    data = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(data, dict)
    return data


def steps() -> list[dict[str, object]]:
    jobs = workflow()["jobs"]
    assert isinstance(jobs, dict)
    assert list(jobs) == ["sign"]
    return list(jobs["sign"]["steps"])


def step(name: str) -> dict[str, object]:
    return next(s for s in steps() if s.get("name") == name)


def test_every_run_block_of_the_workflow_parses_with_bash_n(tmp_path: Path) -> None:
    blocks = [str(s["run"]) for s in steps() if "run" in s]
    assert len(blocks) >= 5
    for number, block in enumerate(blocks):
        script = tmp_path / f"run-{number}.sh"
        script.write_text(re.sub(r"\$\{\{[^}]*\}\}", "placeholder", block))
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"block {number}: {result.stderr}"


def test_the_job_runs_in_the_release_environment_with_the_release_jobs_permissions() -> None:
    data = workflow()
    assert data["permissions"] == {"contents": "read"}
    job = data["jobs"]["sign"]  # type: ignore[index]
    assert job["environment"] == "release"
    # release.yml's release job, and the pull request the catalogue entry needs.
    assert job["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
        "id-token": "write",
        "attestations": "write",
    }
    trigger = data.get("on", data.get(True))
    assert isinstance(trigger, dict)
    assert trigger["push"] == {"branches": ["main"], "paths": ["adapters/*/*/manifest.json"]}
    assert "workflow_dispatch" in trigger


def test_the_key_is_written_0600_for_the_signing_step_alone_and_removed() -> None:
    signing = step("Sign and verify")
    run = str(signing["run"])
    assert '( umask 077 && printf \'%s\\n\' "$RELEASE_SIGNING_KEY" > "$key" )' in run
    assert "trap 'rm -f \"$key\"' EXIT" in run
    assert run.index("umask 077") < run.index("sign_manifest.py sign") < run.rindex('rm -f "$key"')
    assert '--key "$key"' in run
    holders = [s.get("name") for s in steps() if "RELEASE_SIGNING_KEY" in json.dumps(s)]
    assert holders == ["Sign and verify"]
    assert "HF_ADAPTER_TOKEN" not in json.dumps(step("Open the catalogue pull request"))


def test_the_steps_run_check_sign_attest_publish_then_the_pull_request() -> None:
    names = [s.get("name") for s in steps()]
    order = [
        "Check",
        "Sign and verify",
        "Attest build provenance",
        "Publish to Hugging Face",
        "Open the catalogue pull request",
    ]
    assert [name for name in names if name in order] == order
    attest = step("Attest build provenance")
    assert attest["with"] == {"subject-checksums": "attested.sha256"}
    assert "manifest.json.sig" in str(step("Sign and verify")["run"])


def test_the_workflow_uses_only_actions_release_yml_pins_and_interpolates_nothing_into_a_shell() -> (
    None
):
    # release.yml's pins and those of release-build.yml, which holds the release's build jobs.
    release_text = (
        RELEASE_WORKFLOW.read_text() + RELEASE_WORKFLOW.with_name("release-build.yml").read_text()
    )
    release_uses = set(re.findall(r"uses: (\S+@[0-9a-f]{40})", release_text))
    used = [str(s["uses"]) for s in steps() if "uses" in s]
    assert used
    assert set(used) <= release_uses
    for s in steps():
        assert "${{" not in str(s.get("run", ""))


# The signer


def test_a_dry_run_with_a_session_key_signs_and_verifies_and_never_uploads(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = write_manifest(tmp_path)
    argv = ["publish", str(manifest), "--key", str(keys["release"]), "--dry-run"]
    assert signer.main([*argv, "--trusted-keys", str(keys["trusted"])]) == 0
    assert "dry run: signed and verified" in capsys.readouterr().out
    assert hub.uploads == []
    out = tmp_path / "signed"
    assert sign(manifest, keys["release"], keys, out) == 0
    assert (out / "manifest.json").read_bytes() == manifest.read_bytes()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            AdapterSignature, "keys", (keys["release"].with_suffix(".pub").read_text().strip(),)
        )
        assert (
            AdapterSignature.problem(
                manifest.read_bytes(), (out / "manifest.json.sig").read_bytes()
            )
            is None
        )
    assert hub.uploads == []


def test_publish_uploads_both_files_on_top_of_the_checked_commit_and_writes_the_entry(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path]
) -> None:
    manifest = write_manifest(tmp_path)
    signature = ssh_sign(keys, manifest.read_bytes())
    assert publish(manifest, signature, keys, tmp_path) == 0
    assert len(hub.uploads) == 1
    repo, parent, files, _ = hub.uploads[0]
    assert (repo, parent) == (ADAPTER_REPO, COMMIT)
    assert files == {
        "manifest.json": manifest.read_bytes(),
        "manifest.json.sig": signature.read_bytes(),
    }
    entry = json.loads((tmp_path / "entry.json").read_text())
    listed = (
        "adapter_model.safetensors",
        "adapter_config.json",
        "drafter/adapter_model.safetensors",
        "drafter/adapter_config.json",
    )
    assert entry == {
        "name": "rust",
        "version": "1.0.0",
        "display_name": "Rust",
        "repo": ADAPTER_REPO,
        "commit": PUBLISHED,
        "manifest_sha256": f"sha256:{sha256(manifest.read_bytes())}",
        "base_alias": DEFAULT_MODEL_ALIAS,
        "size": sum(len(ADAPTER_FILES[path][0]) for path in listed),
    }


@pytest.mark.parametrize(
    "path",
    ["adapter_model.safetensors", "adapter_config.json", "drafter/adapter_model.safetensors"],
)
def test_a_file_hash_that_differs_from_the_repositorys_metadata_stops_it_before_signing_and_upload(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path], path: str
) -> None:
    manifest = write_manifest(tmp_path)
    signature = ssh_sign(keys, manifest.read_bytes())
    data, lfs = hub.repos[(ADAPTER_REPO, COMMIT)][path]
    hub.repos[(ADAPTER_REPO, COMMIT)][path] = (data + b" retrained", lfs)
    assert sign(manifest, keys["release"], keys, tmp_path / "out") == 1
    assert not (tmp_path / "out").exists()
    assert publish(manifest, signature, keys, tmp_path) == 1
    assert hub.uploads == []
    assert not (tmp_path / "entry.json").exists()


def test_a_small_file_that_downloads_as_another_blob_stops_it(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = write_manifest(tmp_path)
    hub.served[(ADAPTER_REPO, "adapter_config.json")] = b'{"r": 64}'
    assert publish(manifest, ssh_sign(keys, manifest.read_bytes()), keys, tmp_path) == 1
    assert "not the blob its metadata names" in capsys.readouterr().err
    assert hub.uploads == []


def test_a_file_missing_from_the_repository_stops_it(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path]
) -> None:
    manifest = write_manifest(tmp_path)
    del hub.repos[(ADAPTER_REPO, COMMIT)]["drafter/adapter_config.json"]
    assert publish(manifest, ssh_sign(keys, manifest.read_bytes()), keys, tmp_path) == 1
    assert hub.uploads == []


@pytest.mark.parametrize("section", ["base", "drafter_base"])
def test_a_wrong_base_digest_stops_it_before_signing_and_upload(
    tmp_path: Path,
    hub: FakeHub,
    keys: dict[str, Path],
    section: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = json.loads(write_manifest(tmp_path).read_text())
    manifest = write_manifest(
        tmp_path, **{section: {**record[section], "digest": "sha256:" + "0" * 64}}
    )
    assert sign(manifest, keys["release"], keys, tmp_path / "out") == 1
    assert f"{section}.digest" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
    assert publish(manifest, ssh_sign(keys, manifest.read_bytes()), keys, tmp_path) == 1
    assert hub.uploads == []


def test_a_base_file_changed_on_the_hub_changes_the_digest(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path]
) -> None:
    manifest = write_manifest(tmp_path)
    hub.repos[(BASE_REPO, BASE_REVISION)]["chat_template.jinja"] = (b"another template", False)
    assert publish(manifest, ssh_sign(keys, manifest.read_bytes()), keys, tmp_path) == 1
    assert hub.uploads == []


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("base", "revision", "2" * 40),
        ("base", "repo", "someone/Qwen3.8-27B-NVFP4"),
        ("base", "registry_alias", "qwen3.8-27b-dflash2-draft"),
        ("drafter_base", "revision", "3" * 40),
    ],
)
def test_a_base_that_is_not_the_registrys_pin_stops_it_before_the_hub_is_asked(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path], section: str, key: str, value: str
) -> None:
    record = json.loads(write_manifest(tmp_path).read_text())
    manifest = write_manifest(tmp_path, **{section: {**record[section], key: value}})
    assert sign(manifest, keys["release"], keys, tmp_path / "out") == 1
    assert hub.listed == []
    assert not (tmp_path / "out").exists()
    assert hub.uploads == []


def test_a_drafter_adapter_without_its_drafter_base_is_refused(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path]
) -> None:
    manifest = write_manifest(tmp_path, drafter_base=None)
    assert sign(manifest, keys["release"], keys, tmp_path / "out") == 1
    assert hub.uploads == []


def test_a_manifest_without_a_drafter_needs_no_drafter_base(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path]
) -> None:
    manifest = write_manifest(tmp_path, drafter_base=None, drafter_adapter=None)
    assert sign(manifest, keys["release"], keys, tmp_path / "out") == 0


@pytest.mark.parametrize("namespace_or_key", ["stranger", "mightling-release"])
def test_a_signature_that_does_not_verify_against_the_key_list_stops_it_before_upload(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path], namespace_or_key: str
) -> None:
    manifest = write_manifest(tmp_path)
    if namespace_or_key == "stranger":
        signature = ssh_sign(keys, manifest.read_bytes(), key="stranger")
    else:
        signature = ssh_sign(keys, manifest.read_bytes(), namespace="mightling-release")
    assert publish(manifest, signature, keys, tmp_path) == 1
    assert hub.uploads == []
    assert not (tmp_path / "entry.json").exists()


def test_a_signing_key_that_is_not_a_release_key_stops_the_signing_step(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = write_manifest(tmp_path)
    assert sign(manifest, keys["stranger"], keys, tmp_path / "out") == 1
    assert "is not one of release-signing.pub's" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
    argv = ["publish", str(manifest), "--key", str(keys["stranger"]), "--dry-run"]
    assert signer.main([*argv, "--trusted-keys", str(keys["trusted"])]) == 1
    assert hub.uploads == []


def test_a_key_file_others_can_read_is_refused(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path]
) -> None:
    manifest = write_manifest(tmp_path)
    key = tmp_path / "open-key"
    shutil.copy(keys["release"], key)
    key.chmod(0o644)
    assert sign(manifest, key, keys, tmp_path / "out") == 1
    assert not (tmp_path / "out").exists()


def test_a_changed_manifest_does_not_verify_with_the_old_signature(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path]
) -> None:
    manifest = write_manifest(tmp_path)
    signature = ssh_sign(keys, manifest.read_bytes())
    manifest = write_manifest(tmp_path, description="Rust specialist, retold.")
    assert publish(manifest, signature, keys, tmp_path) == 1
    assert hub.uploads == []


def test_publish_takes_the_signature_sign_made_and_a_key_only_for_a_dry_run(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path]
) -> None:
    manifest = write_manifest(tmp_path)
    assert signer.main(["publish", str(manifest)]) == 1
    assert signer.main(["publish", str(manifest), "--key", str(keys["release"])]) == 1
    assert hub.uploads == []
    assert hub.listed == []


@pytest.mark.parametrize(
    "change",
    [
        {"source": {"repo": "dreamference/mightling-cpp", "commit": COMMIT}},
        {"source": {"repo": ADAPTER_REPO, "commit": "main"}},
        {"source": {"repo": ADAPTER_REPO, "commit": COMMIT, "branch": "main"}},
        {"version": "1.0.1"},
        {"lora": {"rank": 16}},
        {"evaluation": []},
    ],
)
def test_a_manifest_out_of_its_layout_or_malformed_is_refused(
    tmp_path: Path, hub: FakeHub, keys: dict[str, Path], change: dict[str, object]
) -> None:
    manifest = write_manifest(tmp_path, **change)
    assert sign(manifest, keys["release"], keys, tmp_path / "out") == 1
    assert hub.listed == []
    assert hub.uploads == []


def test_the_digest_from_metadata_is_the_digest_ling_admin_takes_from_the_snapshot(
    tmp_path: Path, hub: FakeHub
) -> None:
    hub.repos[(BASE_REPO, BASE_REVISION)]["original/model.safetensors"] = (b"a nested weight", True)
    expected = snapshot_digest(tmp_path, hub.repos[(BASE_REPO, BASE_REVISION)])
    assert signer.ManifestSigner.remote_base_digest(BASE_REPO, BASE_REVISION, hub) == expected


def test_the_default_key_list_is_ling_rs_release_signing_pub() -> None:
    assert signer.TRUSTED_KEYS == ROOT / "ling-rs" / "release-signing.pub"
    assert signer.ManifestSigner.trusted_keys(signer.TRUSTED_KEYS) == RELEASE_KEYS


# The catalogue's pull request


def catalogue(text: str) -> dict[str, list[dict[str, object]]]:
    for node in ast.parse(text).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "ADAPTER_CATALOG":
            assert node.value is not None
            value = ast.literal_eval(node.value)
            assert isinstance(value, dict)
            return value
    raise AssertionError("no ADAPTER_CATALOG")


def entry(version: str, commit: str) -> dict[str, object]:
    return {
        "name": "rust",
        "version": version,
        "display_name": 'Rust (the "borrow" checker\'s)',
        "repo": ADAPTER_REPO,
        "commit": commit,
        "manifest_sha256": "sha256:" + "a" * 64,
        "base_alias": DEFAULT_MODEL_ALIAS,
        "size": 1234,
    }


def test_the_entry_is_added_to_the_catalogue_and_a_version_resigned_replaces_its_entry(
    tmp_path: Path,
) -> None:
    module = ROOT / "dreamference" / "hardware" / "adapter_catalog.py"
    original = module.read_text()
    first = signer.CatalogWriter.add(original, entry("1.0.0", PUBLISHED))
    assert catalogue(first) == {
        "rust": [{k: v for k, v in entry("1.0.0", PUBLISHED).items() if k != "name"}]
    }
    second = signer.CatalogWriter.add(first, entry("1.1.0", "d" * 40))
    resigned = signer.CatalogWriter.add(second, entry("1.0.0", "e" * 40))
    versions = catalogue(resigned)["rust"]
    assert [(v["version"], v["commit"]) for v in versions] == [
        ("1.0.0", "e" * 40),
        ("1.1.0", "d" * 40),
    ]
    # The rest of the module is untouched and it still imports.
    head = original[: original.index("ADAPTER_CATALOG:")]
    tail = original[original.index("\nREPO: Final") :]
    assert resigned.startswith(head)
    assert resigned.endswith(tail)
    namespace: dict[str, object] = {}
    exec(compile(resigned, str(module), "exec"), namespace)
    assert namespace["ADAPTER_CATALOG"] == catalogue(resigned)
    # The catalog subcommand writes the same into the file it is given.
    copy = tmp_path / "adapter_catalog.py"
    copy.write_text(original)
    (tmp_path / "entry.json").write_text(json.dumps(entry("1.0.0", PUBLISHED)))
    assert signer.main(["catalog", str(tmp_path / "entry.json"), "--catalog-file", str(copy)]) == 0
    assert copy.read_text() == first
