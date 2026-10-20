"""An adapter's signed manifest, parsed strictly (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §4.2).

`manifest.json` is the one file the signature covers, and it names every other file of the
adapter by sha256. Anything this parser does not know is refused rather than ignored: an unknown
schema, an unknown top-level key, a missing required one, a file name that leaves the adapter's
folder, a file that is not safetensors or JSON (no pickle ever reaches the disk), a hash that is
not a sha256.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Final

SCHEMA: Final[int] = 1

REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"schema", "name", "display_name", "version", "description", "base", "lora", "files", "licence"}
)
OPTIONAL_KEYS: Final[frozenset[str]] = frozenset(
    {"drafter_base", "drafter_adapter", "evals", "signer"}
)

# An installed name is also the engine's name for the adapter and part of a model id
# (`<base>+<name>`), so it is kept to what every one of those places accepts.
NAME: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
VERSION: Final[re.Pattern[str]] = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
DIGEST: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}")
REVISION: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
# A path inside the adapter's folder: at most one folder, segments that start with a letter, a
# digit or `_` (so never `.` or `..`), no leading slash.
FILE_PATH: Final[re.Pattern[str]] = re.compile(
    r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)?[A-Za-z0-9_][A-Za-z0-9_.-]*"
)
FILE_SUFFIXES: Final[tuple[str, ...]] = (".safetensors", ".json")
WEIGHTS_FILE: Final[str] = "adapter_model.safetensors"
CONFIG_FILE: Final[str] = "adapter_config.json"
MANIFEST_FILE: Final[str] = "manifest.json"
SIGNATURE_FILE: Final[str] = "manifest.json.sig"


@dataclass(frozen=True)
class AdapterManifest:
    """The fields of a manifest that installing, verifying and serving read.

    Attributes:
        name: The adapter's name, which is also its engine-side name.
        display_name: What `list`, `/adapter` and the model chip show.
        version: `major.minor.patch`.
        description: One line about what the adapter is for.
        base_repo: The base checkpoint's Hugging Face repository.
        base_revision: The commit of the base it was trained on.
        base_digest: `sha256:` of the base's canonical file listing (§5.2).
        rank: The LoRA rank.
        target_modules: The modules the adapter changes, in SGLang's names.
        files: Path inside the adapter to `sha256:<hex>`, the weights and the config included.
        drafter_files: The drafter adapter's files (§7.2), fetched and hashed like the rest; the
            pinned engine runs the drafter unadapted, so nothing serves them yet.
        signer: The signing key's comment, for people (the verifier never trusts it).
        raw: The manifest as parsed, for the record.
    """

    name: str
    display_name: str
    version: str
    description: str
    base_repo: str
    base_revision: str
    base_digest: str
    rank: int
    target_modules: tuple[str, ...]
    files: dict[str, str] = field(default_factory=dict)
    drafter_files: dict[str, str] = field(default_factory=dict)
    signer: str = ""
    raw: dict[str, object] = field(default_factory=dict)

    @classmethod
    def parse(cls, data: bytes) -> "AdapterManifest":
        """Parses a manifest's bytes.

        Args:
            data: The bytes of `manifest.json`.

        Returns:
            AdapterManifest: The manifest.

        Raises:
            ValueError: With the reason, when the manifest is not one this release accepts.
        """
        try:
            record = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError(f"manifest.json is not JSON ({error})") from error
        if not isinstance(record, dict):
            raise ValueError("manifest.json is not a JSON object")
        cls._check_keys(record)
        base = cls._section(record, "base", ("repo", "revision", "digest"))
        lora = cls._section(record, "lora", ("rank", "target_modules"))
        files = cls._files(record.get("files"), (WEIGHTS_FILE, CONFIG_FILE))
        drafter_files = cls._drafter_files(record.get("drafter_adapter"), files)
        name = cls._matching(record, "name", NAME, "lowercase letters, digits and dashes")
        version = cls._matching(record, "version", VERSION, "major.minor.patch")
        cls._matching(base, "revision", REVISION, "a commit")
        cls._matching(base, "digest", DIGEST, "a sha256 digest")
        rank, modules = lora["rank"], lora["target_modules"]
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise ValueError("lora.rank is not a positive integer")
        if (
            not isinstance(modules, list)
            or not modules
            or not all(isinstance(m, str) and m for m in modules)
        ):
            raise ValueError("lora.target_modules is not a list of module names")
        return cls(
            name=name,
            display_name=cls._text(record, "display_name"),
            version=version,
            description=cls._text(record, "description"),
            base_repo=cls._text(base, "repo"),
            base_revision=str(base["revision"]),
            base_digest=str(base["digest"]),
            rank=rank,
            target_modules=tuple(modules),
            files=files,
            drafter_files=drafter_files,
            signer=str(record.get("signer") or ""),
            raw=record,
        )

    def all_files(self) -> dict[str, str]:
        """Every file the manifest names, the drafter adapter's included.

        Returns:
            dict[str, str]: Path inside the adapter to `sha256:<hex>`.
        """
        return {**self.files, **self.drafter_files}

    @classmethod
    def _check_keys(cls, record: dict[str, object]) -> None:
        """Refuses an unknown schema, unknown keys and missing ones.

        Args:
            record: The parsed manifest.

        Raises:
            ValueError: With the reason.
        """
        schema = record.get("schema")
        if schema != SCHEMA:
            raise ValueError(f"manifest schema {schema!r} is not one this release reads ({SCHEMA})")
        unknown = sorted(set(record) - REQUIRED_KEYS - OPTIONAL_KEYS)
        if unknown:
            raise ValueError(
                f"manifest.json has keys this release does not know: {', '.join(unknown)}"
            )
        missing = sorted(REQUIRED_KEYS - set(record))
        if missing:
            raise ValueError(f"manifest.json lacks {', '.join(missing)}")

    @classmethod
    def _text(cls, record: dict[str, object], key: str) -> str:
        """Reads a required non-empty string.

        Args:
            record: The parsed manifest.
            key: Its key.

        Returns:
            str: The value.

        Raises:
            ValueError: When it is not a non-empty string.
        """
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is not a non-empty string")
        return value

    @classmethod
    def _matching(
        cls, record: dict[str, object], key: str, pattern: re.Pattern[str], shape: str
    ) -> str:
        """Reads a required string of a given shape.

        Args:
            record: The manifest or one of its sections.
            key: The key.
            pattern: What the whole value must match.
            shape: The shape in words, for the refusal.

        Returns:
            str: The value.

        Raises:
            ValueError: When it is missing or of another shape.
        """
        value = cls._text(record, key)
        if not pattern.fullmatch(value):
            raise ValueError(f"{key} {value!r} is not {shape}")
        return value

    @classmethod
    def _drafter_files(cls, drafter: object, files: dict[str, str]) -> dict[str, str]:
        """Reads the optional drafter adapter's file list.

        Args:
            drafter: The manifest's `drafter_adapter`, or None.
            files: The target adapter's files, which it may not repeat.

        Returns:
            dict[str, str]: Path to digest; empty without a drafter adapter.

        Raises:
            ValueError: When the section or one of its files is refused.
        """
        if drafter is None:
            return {}
        if not isinstance(drafter, dict):
            raise ValueError("drafter_adapter is not an object")
        drafter_files = cls._files(drafter.get("files"), ())
        overlap = sorted(set(drafter_files) & set(files))
        if overlap:
            raise ValueError(f"{', '.join(overlap)} is listed twice")
        return drafter_files

    @classmethod
    def _section(
        cls, record: dict[str, object], key: str, required: tuple[str, ...]
    ) -> dict[str, object]:
        """Reads a required object with required keys.

        Args:
            record: The parsed manifest.
            key: The section's key.
            required: The keys it must have.

        Returns:
            dict[str, object]: The section.

        Raises:
            ValueError: When it is not an object or lacks a key.
        """
        section = record.get(key)
        if not isinstance(section, dict):
            raise ValueError(f"{key} is not an object")
        missing = [name for name in required if name not in section]
        if missing:
            raise ValueError(f"{key} lacks {', '.join(missing)}")
        return section

    @classmethod
    def _files(cls, files: object, needed: tuple[str, ...]) -> dict[str, str]:
        """Checks a file list: names inside the folder, safetensors or JSON, sha256 each.

        Args:
            files: The manifest's `files` (or the drafter adapter's).
            needed: The files the list must name.

        Returns:
            dict[str, str]: Path to digest.

        Raises:
            ValueError: With the first file that is refused.
        """
        if not isinstance(files, dict) or not files:
            raise ValueError("a files list is not an object naming the adapter's files")
        checked: dict[str, str] = {}
        for path, digest in files.items():
            if not FILE_PATH.fullmatch(path):
                raise ValueError(f"the file name {path!r} leaves the adapter's folder")
            if not path.endswith(FILE_SUFFIXES):
                raise ValueError(
                    f"{path} is neither safetensors nor JSON; nothing else is installed"
                )
            if path in (MANIFEST_FILE, SIGNATURE_FILE):
                raise ValueError(f"{path} cannot be one of the files the manifest lists")
            if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                raise ValueError(f"the digest of {path} is not sha256:<64 hex>")
            checked[path] = digest
        for name in needed:
            if name not in checked:
                raise ValueError(f"files does not name {name}")
        return checked
