"""Signs and publishes an adapter's manifest: the steps of the signing job (.github/workflows/adapter-sign.yml).

specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §5.1. The input is `adapters/<name>/<version>/manifest.json`
in this repository, with `source.json` beside it naming the adapter's Hugging Face repository and the
commit its weights were uploaded at. Four subcommands, each a stage that repeats the checks of the
stages before it, so no stage trusts a file an earlier step left behind:

    check   <manifest>                                  the manifest against the repository's metadata
                                                        and the registry's pins; nothing is signed
    sign    <manifest> --key FILE --out DIR             check, sign with `ssh-keygen -Y sign -n
                                                        mightling-adapter`, verify against the key list
    publish <manifest> --signature FILE --entry-out F   check, verify, upload manifest.json and its
                                                        signature as one new commit; writes the
                                                        catalogue entry
    publish <manifest> --key FILE --dry-run             check, sign and verify; never uploads
    catalog <entry.json>                                adds the entry to ADAPTER_CATALOG

Every failure stops with the reason before anything is signed or uploaded. The manifest is parsed
by `AdapterManifest.parse`, the base digest is `AdapterBaseDigest.compute` over a snapshot staged from
the repository's metadata, and the signature is checked by `AdapterSignature.problem`, the same code
`ling-admin adapter install` runs. `huggingface_hub` is imported only when the hub is reached.
"""

import argparse
import ast
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dreamference.hardware.adapter_base_digest import COVERED, AdapterBaseDigest  # noqa: E402
from dreamference.hardware.adapter_catalog import (  # noqa: E402
    DREAMFERENCE_HF_ORG,
    AdapterCatalog,
)
from dreamference.hardware.adapter_manifest import (  # noqa: E402
    DIGEST,
    MANIFEST_FILE,
    REVISION,
    SIGNATURE_FILE,
    AdapterManifest,
)
from dreamference.hardware.adapter_signature import (  # noqa: E402
    NAMESPACE,
    AdapterSignature,
)
from dreamference.hardware.model_matrix_registry import (  # noqa: E402
    DEFAULT_MODEL_ALIAS,
    ModelMatrixRegistry,
)

TRUSTED_KEYS: Final[Path] = REPO_ROOT / "ling-rs" / "release-signing.pub"
CATALOG_FILE: Final[Path] = REPO_ROOT / "dreamference" / "hardware" / "adapter_catalog.py"
CATALOG_NAME: Final[str] = "ADAPTER_CATALOG"
SOURCE_FILE: Final[str] = "source.json"
SOURCE_KEYS: Final[frozenset[str]] = frozenset({"repo", "commit"})
LAYOUT_DEPTH: Final[int] = 4  # adapters/<name>/<version>/manifest.json
# Hugging Face keeps a file above 10 MB in LFS, with its sha256 in the metadata; anything smaller
# than that is downloaded to be hashed, and a non-LFS file larger than this is refused.
SMALL_FILE_LIMIT: Final[int] = 10 * 1024 * 1024
SIGN_TIMEOUT_S: Final[float] = 60.0
KEY_MODE: Final[int] = 0o600
TOKEN_ENV: Final[str] = "HF_ADAPTER_TOKEN"
NAME_KEY: Final[str] = "name"
IN_ACTIONS: Final[tuple[str, str]] = ("GITHUB_ACTIONS", "true")


@dataclass(frozen=True)
class RemoteFile:
    """One file of a Hugging Face repository at a commit, as its metadata records it.

    Attributes:
        path: The path inside the repository.
        size: Bytes.
        sha256: The LFS object id, for a file kept in LFS; None otherwise.
        blob_id: The git blob id, which is all a small file's metadata carries.
    """

    path: str
    size: int
    sha256: str | None
    blob_id: str | None


class HubClient:
    """The Hugging Face calls the job makes, and nothing else; the tests replace it."""

    def __init__(self, token: str | None = None) -> None:
        """Keeps the token for the calls.

        Args:
            token: A Hugging Face token, or None for anonymous reads.
        """
        self.token = token

    def files(self, repo: str, revision: str) -> dict[str, RemoteFile]:
        """Every file of a repository at a commit, with its recorded hashes.

        Args:
            repo: `<org>/<repo>`.
            revision: A commit.

        Returns:
            dict[str, RemoteFile]: Path to file.
        """
        from huggingface_hub import HfApi

        info = HfApi(token=self.token).model_info(repo, revision=revision, files_metadata=True)
        listed: dict[str, RemoteFile] = {}
        for sibling in info.siblings or []:
            lfs = sibling.lfs
            listed[sibling.rfilename] = RemoteFile(
                path=sibling.rfilename,
                size=int(sibling.size or 0),
                sha256=str(lfs.sha256) if lfs is not None else None,
                blob_id=sibling.blob_id,
            )
        return listed

    def download(self, repo: str, revision: str, path: str, folder: Path) -> Path:
        """Downloads one small file into a folder.

        Args:
            repo: `<org>/<repo>`.
            revision: A commit.
            path: The file's path inside the repository.
            folder: Where to put it (at its path inside the folder).

        Returns:
            Path: The downloaded file.
        """
        from huggingface_hub import hf_hub_download

        return Path(
            hf_hub_download(
                repo, path, revision=revision, local_dir=folder, token=self.token or False
            )
        )

    def upload(self, repo: str, parent: str, files: dict[str, bytes], message: str) -> str:
        """Uploads files as one new commit on top of exactly `parent`.

        Args:
            repo: `<org>/<repo>`.
            parent: The commit the new one must follow: the upload fails if the branch moved.
            files: Path inside the repository to content.
            message: The commit message.

        Returns:
            str: The new commit.
        """
        from huggingface_hub import CommitOperationAdd, HfApi

        operations = [
            CommitOperationAdd(path_in_repo=path, path_or_fileobj=data)
            for path, data in files.items()
        ]
        info = HfApi(token=self.token).create_commit(
            repo, operations, commit_message=message, parent_commit=parent
        )
        return str(getattr(info, "oid", "") or "")


@dataclass
class Checked:
    """What `check` established about a manifest.

    Attributes:
        path: The manifest's path in this repository.
        data: Its bytes, exactly as signed.
        manifest: The parsed manifest.
        repo: The adapter's Hugging Face repository.
        commit: The commit its weights were uploaded at.
        base_alias: The model matrix entry it was made for.
        size: Bytes of the files it lists.
        notes: What was checked, for the job's log.
    """

    path: Path
    data: bytes
    manifest: AdapterManifest
    repo: str
    commit: str
    base_alias: str
    size: int
    notes: list[str] = field(default_factory=list)


class ManifestSigner:
    """The stages of the signing job."""

    @classmethod
    def check(cls, path: Path, hub: HubClient) -> Checked:
        """Checks a manifest before anything is signed.

        Args:
            path: `adapters/<name>/<version>/manifest.json`.
            hub: The Hugging Face client.

        Returns:
            Checked: The manifest and what was established.

        Raises:
            ValueError: With the first reason it may not be signed.
        """
        try:
            data = path.read_bytes()
        except OSError as error:
            raise ValueError(f"{path} cannot be read ({error})") from error
        manifest = AdapterManifest.parse(data)
        cls.check_layout(path, manifest)
        repo, commit = cls.read_source(path.parent / SOURCE_FILE, manifest.name)
        base_alias = cls.check_pins(manifest)
        checked = Checked(path, data, manifest, repo, commit, base_alias, 0)
        checked.size = cls.check_files(manifest, repo, commit, hub)
        checked.notes.append(
            f"{len(manifest.all_files())} files match {repo}@{commit[:12]} ({checked.size} bytes)"
        )
        cls.check_digest(
            "base", manifest.base_repo, manifest.base_revision, manifest.base_digest, hub
        )
        checked.notes.append(f"base {manifest.base_repo}@{manifest.base_revision[:12]} matches")
        drafter = cls.drafter_base(manifest)
        if drafter is not None:
            cls.check_digest("drafter_base", *drafter, hub)
            checked.notes.append(f"drafter_base {drafter[0]}@{drafter[1][:12]} matches")
        return checked

    @classmethod
    def check_layout(cls, path: Path, manifest: AdapterManifest) -> None:
        """Requires the manifest to sit at `adapters/<name>/<version>/manifest.json`.

        Args:
            path: The manifest's path.
            manifest: The parsed manifest.

        Raises:
            ValueError: When the folders do not name its name and version.
        """
        parts = path.resolve().parts
        if len(parts) < LAYOUT_DEPTH or path.name != MANIFEST_FILE:
            raise ValueError(f"{path} is not adapters/<name>/<version>/{MANIFEST_FILE}")
        top, name, version = parts[-4], parts[-3], parts[-2]
        if (top, name, version) != ("adapters", manifest.name, manifest.version):
            raise ValueError(
                f"{path} must be adapters/{manifest.name}/{manifest.version}/{MANIFEST_FILE}, "
                "the manifest's own name and version"
            )

    @classmethod
    def read_source(cls, path: Path, name: str) -> tuple[str, str]:
        """Reads `source.json`: the adapter's repository and the commit of its weights.

        Args:
            path: `source.json` beside the manifest.
            name: The adapter's name, which names its repository.

        Returns:
            tuple[str, str]: The repository and the commit.

        Raises:
            ValueError: When it is missing, malformed, or names another repository.
        """
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"{path} is missing or not JSON ({error})") from error
        if not isinstance(record, dict) or set(record) != SOURCE_KEYS:
            raise ValueError(f"{path} must hold exactly {sorted(SOURCE_KEYS)}")
        repo, commit = AdapterCatalog.parse_repo(f"{record['repo']}@{record['commit']}")
        expected = f"{DREAMFERENCE_HF_ORG}/mightling-{name}"
        if repo != expected:
            raise ValueError(
                f"{path} names {repo}; the adapter {name!r} is published at {expected}"
            )
        return repo, commit

    @classmethod
    def check_pins(cls, manifest: AdapterManifest) -> str:
        """Requires the base (and the drafter base) to be the registry's pinned checkpoints.

        Args:
            manifest: The parsed manifest.

        Returns:
            str: The model matrix key of the base.

        Raises:
            ValueError: When the base is not an entry that serves adapters at its pinned revision.
        """
        base = manifest.raw.get("base")
        alias = base.get("registry_alias") if isinstance(base, dict) else None
        key = (
            str(alias)
            if alias is not None
            else ModelMatrixRegistry.key_for_served_id(manifest.base_repo, DEFAULT_MODEL_ALIAS)
        )
        spec = ModelMatrixRegistry.MATRIX.get(key) if key else None
        if spec is None or spec.hf_repo_id != manifest.base_repo:
            raise ValueError(
                f"base {manifest.base_repo} ({alias or 'no alias'}) is not the registry's"
            )
        recipe = ModelMatrixRegistry.get_launch_overrides(key or "")
        if ModelMatrixRegistry.get_lora_recipe(key or "") is None:
            raise ValueError(f"the registry serves {key} without adapters")
        if recipe.get("revision") != manifest.base_revision:
            raise ValueError(
                f"base.revision {manifest.base_revision[:12]} is not the registry's pin of {key} "
                f"({str(recipe.get('revision'))[:12]})"
            )
        cls.check_drafter_pin(manifest, recipe.get("speculative_config") or {})
        return str(key)

    @classmethod
    def check_drafter_pin(cls, manifest: AdapterManifest, speculative: dict[str, object]) -> None:
        """Requires `drafter_base`, when given or needed, to be the recipe's pinned drafter.

        Args:
            manifest: The parsed manifest.
            speculative: The base recipe's `speculative_config`.

        Raises:
            ValueError: When the drafter base is not the recipe's, or a drafter adapter has none.
        """
        drafter = cls.drafter_base(manifest)
        if drafter is None:
            if manifest.drafter_files:
                raise ValueError("drafter_adapter is given without drafter_base")
            return
        pinned = (speculative.get("model"), speculative.get("revision"))
        if (drafter[0], drafter[1]) != pinned:
            raise ValueError(
                f"drafter_base {drafter[0]}@{drafter[1][:12]} is not the recipe's drafter "
                f"{pinned[0]}@{str(pinned[1])[:12]}"
            )

    @classmethod
    def drafter_base(cls, manifest: AdapterManifest) -> tuple[str, str, str] | None:
        """Reads `drafter_base`, which the manifest parser keeps only in `raw`.

        Args:
            manifest: The parsed manifest.

        Returns:
            tuple[str, str, str] | None: Repository, revision and digest; None when absent.

        Raises:
            ValueError: When it is present but not of that shape.
        """
        section = manifest.raw.get("drafter_base")
        if section is None:
            return None
        if not isinstance(section, dict):
            raise ValueError("drafter_base is not an object")
        repo, revision, digest = (section.get(key) for key in ("repo", "revision", "digest"))
        if not isinstance(repo, str) or not repo:
            raise ValueError("drafter_base.repo is not a repository")
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise ValueError("drafter_base.revision is not a commit")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("drafter_base.digest is not a sha256 digest")
        return repo, revision, digest

    @classmethod
    def check_files(cls, manifest: AdapterManifest, repo: str, commit: str, hub: HubClient) -> int:
        """Requires every file the manifest lists to have its sha256 in the repository at the commit.

        Args:
            manifest: The parsed manifest.
            repo: The adapter's repository.
            commit: The commit of its weights.
            hub: The Hugging Face client.

        Returns:
            int: Bytes of the files listed.

        Raises:
            ValueError: With the first file that is missing or differs.
        """
        remote = hub.files(repo, commit)
        size = 0
        with tempfile.TemporaryDirectory(prefix="adapter-files-") as work:
            for path, digest in sorted(manifest.all_files().items()):
                found = remote.get(path)
                if found is None:
                    raise ValueError(f"{path} is not in {repo} at {commit[:12]}")
                recorded = cls.remote_sha256(found, repo, commit, hub, Path(work))
                if f"sha256:{recorded}" != digest:
                    raise ValueError(
                        f"{path}: the manifest says {digest[:19]}…, {repo}@{commit[:12]} has "
                        f"sha256:{recorded[:12]}…"
                    )
                size += found.size
        return size

    @classmethod
    def remote_sha256(
        cls, found: RemoteFile, repo: str, revision: str, hub: HubClient, folder: Path
    ) -> str:
        """A remote file's sha256: from LFS metadata, or by downloading a small file.

        A downloaded file must also have the git blob id the metadata records, so what was hashed
        is what the repository holds at that commit.

        Args:
            found: The file's metadata.
            repo: Its repository.
            revision: The commit.
            hub: The Hugging Face client.
            folder: Where a small file is downloaded.

        Returns:
            str: The hex sha256.

        Raises:
            ValueError: When a small file is too large or is not the blob the metadata names.
        """
        if found.sha256 is not None:
            return found.sha256
        if found.size > SMALL_FILE_LIMIT:
            raise ValueError(f"{found.path} is {found.size} bytes and not in LFS")
        data = hub.download(repo, revision, found.path, folder).read_bytes()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()
        if found.blob_id is None or blob != found.blob_id:
            raise ValueError(
                f"{found.path} downloaded from {repo} is not the blob its metadata names"
            )
        return hashlib.sha256(data).hexdigest()

    @classmethod
    def check_digest(
        cls, label: str, repo: str, revision: str, digest: str, hub: HubClient
    ) -> None:
        """Requires a base's digest, computed from its repository's metadata, to equal the manifest's.

        Args:
            label: `base` or `drafter_base`, for the refusal.
            repo: The base's repository.
            revision: Its pinned commit.
            digest: The manifest's digest.
            hub: The Hugging Face client.

        Raises:
            ValueError: When the digests differ.
        """
        computed = cls.remote_base_digest(repo, revision, hub)
        if computed != digest:
            raise ValueError(
                f"{label}.digest {digest[:19]}… is not the digest of {repo}@{revision[:12]} "
                f"({computed[:19]}…)"
            )

    @classmethod
    def remote_base_digest(cls, repo: str, revision: str, hub: HubClient) -> str:
        """`AdapterBaseDigest.compute` over a snapshot staged from a repository's metadata.

        A weight file is staged as a link to a blob named by its LFS sha256, which is how the hub
        cache stores it and what `AdapterBaseDigest.file_hash` reads; a small file is downloaded.
        So the digest is the one `ling-admin` computes from the downloaded snapshot, by the same code.

        Args:
            repo: The base's repository.
            revision: Its pinned commit.
            hub: The Hugging Face client.

        Returns:
            str: `sha256:<hex>`.
        """
        remote = hub.files(repo, revision)
        with tempfile.TemporaryDirectory(prefix="adapter-base-") as work:
            staged, downloads = Path(work) / "snapshot", Path(work) / "downloads"
            staged.mkdir()
            for path, found in sorted(remote.items()):
                if not any(fnmatch(Path(path).name, pattern) for pattern in COVERED):
                    continue
                target = staged / path
                target.parent.mkdir(parents=True, exist_ok=True)
                if found.sha256 is not None:
                    target.symlink_to(Path("blobs") / found.sha256)
                else:
                    sha = cls.remote_sha256(found, repo, revision, hub, downloads)
                    target.write_bytes(cls._downloaded(downloads / path, sha))
            return AdapterBaseDigest.compute(staged)

    @classmethod
    def _downloaded(cls, path: Path, sha: str) -> bytes:
        """Reads a downloaded small file, checked against the hash just taken.

        Args:
            path: The downloaded file.
            sha: Its sha256.

        Returns:
            bytes: Its content.

        Raises:
            ValueError: When it changed since.
        """
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f"{path.name} changed while it was being checked")
        return data

    @classmethod
    def trusted_keys(cls, path: Path) -> tuple[str, ...]:
        """The public keys of a key list: one per line, `#` comments and blank lines dropped.

        Args:
            path: `ling-rs/release-signing.pub` or a stand-in.

        Returns:
            tuple[str, ...]: The keys.

        Raises:
            ValueError: When the file cannot be read or lists no key.
        """
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise ValueError(f"the key list {path} cannot be read ({error})") from error
        keys = tuple(line.strip() for line in lines if line.strip() and not line.startswith("#"))
        if not keys:
            raise ValueError(f"the key list {path} names no key")
        return keys

    @classmethod
    def verify(cls, data: bytes, signature: bytes, keys_file: Path) -> None:
        """Checks a signature the way `ling-admin adapter install` will.

        Args:
            data: The manifest's bytes.
            signature: `manifest.json.sig`.
            keys_file: The key list to check against.

        Raises:
            ValueError: When no key of the list signed exactly these bytes under the adapter namespace.
        """
        with cls._keys(cls.trusted_keys(keys_file)):
            problem = AdapterSignature.problem(data, signature)
        if problem:
            raise ValueError(
                f"{problem}: the signing key is not one of {keys_file.name}'s, nothing was published"
            )

    @classmethod
    @contextmanager
    def _keys(cls, keys: tuple[str, ...]) -> Iterator[None]:
        """Points `AdapterSignature` at a key list for one check.

        Args:
            keys: The keys.

        Yields:
            None: While the keys are in place.
        """
        saved = AdapterSignature.keys
        AdapterSignature.keys = keys
        try:
            yield
        finally:
            AdapterSignature.keys = saved

    @classmethod
    def sign(cls, data: bytes, key: Path, keys_file: Path) -> bytes:
        """Signs a manifest's bytes with `ssh-keygen -Y sign` and verifies the result at once.

        Args:
            data: The manifest's bytes.
            key: The private key, a file readable by its owner alone.
            keys_file: The key list the signature must verify against.

        Returns:
            bytes: The SSHSIG signature.

        Raises:
            ValueError: When the key file is open to others, signing fails, or the signature
                does not verify against the key list.
        """
        try:
            mode = stat.S_IMODE(key.stat().st_mode)
        except OSError as error:
            raise ValueError(f"the key file cannot be read ({error})") from error
        if mode & 0o077:
            raise ValueError(f"the key file is mode {mode:o}; it must be {KEY_MODE:o}")
        with tempfile.TemporaryDirectory(prefix="adapter-sign-") as work:
            message = Path(work) / MANIFEST_FILE
            message.write_bytes(data)
            try:
                result = subprocess.run(
                    [
                        "ssh-keygen",
                        "-q",
                        "-Y",
                        "sign",
                        "-f",
                        str(key),
                        "-n",
                        NAMESPACE,
                        str(message),
                    ],
                    capture_output=True,
                    timeout=SIGN_TIMEOUT_S,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ValueError(f"ssh-keygen could not sign ({error})") from error
            if result.returncode != 0:
                detail = result.stderr.decode("utf-8", "replace").strip()
                raise ValueError(f"ssh-keygen -Y sign failed ({detail or result.returncode})")
            signature = (Path(work) / SIGNATURE_FILE).read_bytes()
        cls.verify(data, signature, keys_file)
        return signature

    @classmethod
    def publish(
        cls, checked: Checked, signature: bytes, keys_file: Path, hub: HubClient
    ) -> dict[str, object]:
        """Verifies the signature again and uploads the manifest and its signature as one commit.

        Args:
            checked: What `check` established.
            signature: `manifest.json.sig`.
            keys_file: The key list the signature must verify against.
            hub: The Hugging Face client (with the write token).

        Returns:
            dict[str, object]: The catalogue entry, its `name` included.

        Raises:
            ValueError: When the signature does not verify (nothing is uploaded) or the upload
                returns no commit.
        """
        cls.verify(checked.data, signature, keys_file)
        manifest = checked.manifest
        commit = hub.upload(
            checked.repo,
            checked.commit,
            {MANIFEST_FILE: checked.data, SIGNATURE_FILE: signature},
            f"Signed manifest of {manifest.name} {manifest.version}",
        )
        if not REVISION.fullmatch(commit):
            raise ValueError(f"the upload to {checked.repo} returned no commit ({commit!r})")
        return cls.entry(checked, commit)

    @classmethod
    def entry(cls, checked: Checked, commit: str) -> dict[str, object]:
        """The catalogue entry for a published manifest (adapter_catalog.py's fields).

        Args:
            checked: What `check` established.
            commit: The commit that carries the signed manifest.

        Returns:
            dict[str, object]: The entry, its `name` included.
        """
        return {
            "name": checked.manifest.name,
            "version": checked.manifest.version,
            "display_name": checked.manifest.display_name,
            "repo": checked.repo,
            "commit": commit,
            "manifest_sha256": "sha256:" + hashlib.sha256(checked.data).hexdigest(),
            "base_alias": checked.base_alias,
            "size": checked.size,
        }


class CatalogWriter:
    """Adds an entry to `ADAPTER_CATALOG` in adapter_catalog.py, for the catalogue's pull request."""

    @classmethod
    def add(cls, source: str, entry: dict[str, object]) -> str:
        """Returns the module's text with the entry added (a version already listed is replaced).

        Args:
            source: adapter_catalog.py's text.
            entry: The entry, its `name` included.

        Returns:
            str: The new text.

        Raises:
            ValueError: When the catalogue's assignment is not found or not a literal.
        """
        node = cls._assignment(source)
        try:
            catalog = ast.literal_eval(node.value) if node.value is not None else None
        except ValueError as error:
            raise ValueError(f"{CATALOG_NAME} is not a literal ({error})") from error
        if not isinstance(catalog, dict):
            raise ValueError(f"{CATALOG_NAME} is not a dict")
        record = {key: value for key, value in entry.items() if key != NAME_KEY}
        name = str(entry["name"])
        versions = [
            item for item in catalog.get(name, []) if item.get("version") != record["version"]
        ]
        versions.append(record)
        versions.sort(key=lambda item: tuple(int(part) for part in str(item["version"]).split(".")))
        catalog[name] = versions
        lines = source.splitlines(keepends=True)
        annotation = ast.get_source_segment(source, node.annotation) or ""
        text = f"{CATALOG_NAME}: {annotation} = {cls.render(catalog)}\n"
        start, end = node.lineno - 1, node.end_lineno or node.lineno
        return "".join([*lines[:start], text, *lines[end:]])

    @classmethod
    def render(cls, catalog: dict[str, list[dict[str, object]]]) -> str:
        """A catalogue as Python source, one key per line, names sorted.

        Args:
            catalog: The catalogue.

        Returns:
            str: Its literal.
        """
        if not catalog:
            return "{}"
        out = ["{"]
        for name in sorted(catalog):
            out.append(f"    {json.dumps(name)}: [")
            for item in catalog[name]:
                out.append("        {")
                out.extend(
                    f"            {json.dumps(key)}: {cls.literal(value)},"
                    for key, value in item.items()
                )
                out.append("        },")
            out.append("    ],")
        out.append("}")
        return "\n".join(out)

    @classmethod
    def literal(cls, value: object) -> str:
        """One value of an entry as Python source.

        Args:
            value: A string or an integer (an entry holds nothing else).

        Returns:
            str: Its literal; a JSON string is a Python string literal.

        Raises:
            ValueError: For any other type.
        """
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ValueError(f"a catalogue entry holds strings and integers, not {value!r}")
        return json.dumps(value, ensure_ascii=False)

    @classmethod
    def _assignment(cls, source: str) -> ast.AnnAssign:
        """Finds `ADAPTER_CATALOG: ... = ...` at the module's top level.

        Args:
            source: adapter_catalog.py's text.

        Returns:
            ast.AnnAssign: The assignment.

        Raises:
            ValueError: When there is none.
        """
        for node in ast.parse(source).body:
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == CATALOG_NAME
            ):
                return node
        raise ValueError(f"{CATALOG_NAME} is not assigned in the catalogue module")


def say(text: str) -> None:
    """Writes a line of the job's log.

    Args:
        text: The line.
    """
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def refuse(text: str) -> int:
    """Reports why the job stops, as a GitHub annotation when it runs in Actions.

    Args:
        text: The reason.

    Returns:
        int: The exit status, 1.
    """
    prefix = "::error::" if os.environ.get(IN_ACTIONS[0]) == IN_ACTIONS[1] else "❌ "
    sys.stderr.write(f"{prefix}{text}\n")
    return 1


def parser() -> argparse.ArgumentParser:
    """The command line.

    Returns:
        argparse.ArgumentParser: The parser.
    """
    top = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = top.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="check a manifest; nothing is signed")
    sign = commands.add_parser("sign", help="check, sign and verify")
    publish = commands.add_parser("publish", help="check, verify and upload")
    for command in (check, sign, publish):
        command.add_argument("manifest", type=Path)
    for command in (sign, publish):
        command.add_argument("--trusted-keys", type=Path, default=TRUSTED_KEYS)
    sign.add_argument("--key", type=Path, required=True, help="the private key, mode 0600")
    sign.add_argument("--out", type=Path, required=True, help="where manifest.json and its .sig go")
    publish.add_argument("--signature", type=Path, help="manifest.json.sig from `sign`")
    publish.add_argument("--key", type=Path, help="with --dry-run: sign with this key instead")
    publish.add_argument("--dry-run", action="store_true", help="check and sign; never upload")
    publish.add_argument("--entry-out", type=Path, help="where the catalogue entry is written")
    catalog = commands.add_parser("catalog", help="add a published entry to ADAPTER_CATALOG")
    catalog.add_argument("entry", type=Path)
    catalog.add_argument("--catalog-file", type=Path, default=CATALOG_FILE)
    return top


def run_check(args: argparse.Namespace, hub: HubClient) -> Checked:
    """Runs `check` and logs what it established.

    Args:
        args: The parsed command line.
        hub: The Hugging Face client.

    Returns:
        Checked: The result.
    """
    checked = ManifestSigner.check(args.manifest, hub)
    for note in checked.notes:
        say(f"✅ {checked.manifest.name} {checked.manifest.version}: {note}")
    return checked


def run_publish(args: argparse.Namespace, hub: HubClient) -> None:
    """Runs `publish`, or its dry run.

    Args:
        args: The parsed command line.
        hub: The Hugging Face client.

    Raises:
        ValueError: With the reason it stopped.
    """
    if args.dry_run:
        if args.key is None and args.signature is None:
            raise ValueError("the dry run needs --key (or --signature)")
    elif args.signature is None or args.key is not None:
        raise ValueError(
            "publish uploads the signature `sign` made (--signature); --key is for --dry-run only"
        )
    checked = run_check(args, hub)
    if args.key is not None:
        signature = ManifestSigner.sign(checked.data, args.key, args.trusted_keys)
    else:
        signature = args.signature.read_bytes()
    if args.dry_run:
        ManifestSigner.verify(checked.data, signature, args.trusted_keys)
        say(
            f"✅ dry run: signed and verified; would upload {MANIFEST_FILE} and {SIGNATURE_FILE} to {checked.repo}"
        )
        return
    entry = ManifestSigner.publish(checked, signature, args.trusted_keys, hub)
    say(f"✅ published {checked.repo}@{entry['commit']}, manifest {entry['manifest_sha256']}")
    if args.entry_out is not None:
        args.entry_out.parent.mkdir(parents=True, exist_ok=True)
        args.entry_out.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    say(json.dumps(entry))


def run_sign(args: argparse.Namespace, hub: HubClient) -> None:
    """Runs `sign`: check, sign, verify, and write the pair to `--out`.

    Args:
        args: The parsed command line.
        hub: The Hugging Face client.
    """
    checked = run_check(args, hub)
    signature = ManifestSigner.sign(checked.data, args.key, args.trusted_keys)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / MANIFEST_FILE).write_bytes(checked.data)
    (args.out / SIGNATURE_FILE).write_bytes(signature)
    say(f"✅ signed and verified against {args.trusted_keys.name}: {args.out / SIGNATURE_FILE}")


def run_catalog(args: argparse.Namespace) -> None:
    """Runs `catalog`: adds the entry `publish` wrote to the catalogue module.

    Args:
        args: The parsed command line.

    Raises:
        ValueError: When the entry is not one `publish` writes.
    """
    entry = json.loads(args.entry.read_text(encoding="utf-8"))
    if not isinstance(entry, dict) or not {"name", "version", "commit"} <= set(entry):
        raise ValueError(f"{args.entry} is not a catalogue entry")
    source = args.catalog_file.read_text(encoding="utf-8")
    args.catalog_file.write_text(CatalogWriter.add(source, entry), encoding="utf-8")
    say(f"✅ {CATALOG_NAME} lists {entry['name']} {entry['version']} at {entry['commit']}")


def main(argv: list[str] | None = None) -> int:
    """Runs one stage.

    Args:
        argv: The arguments, or None for the process's own.

    Returns:
        int: 0 when the stage passed, 1 when it stopped (with the reason on stderr).
    """
    args = parser().parse_args(argv)
    hub = HubClient(os.environ.get(TOKEN_ENV) or None)
    stages = {
        "check": lambda: run_check(args, hub),
        "sign": lambda: run_sign(args, hub),
        "publish": lambda: run_publish(args, hub),
        "catalog": lambda: run_catalog(args),
    }
    try:
        stages[args.command]()
    except ValueError as error:
        return refuse(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
