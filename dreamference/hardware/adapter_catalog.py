"""The adapters this release knows (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §6.1).

`list --available` works without the network and `install <name>` knows exactly which commit to
fetch because the catalogue ships in the package, like `ModelMatrixRegistry.MATRIX`: the wheel is
listed in the release's signed `SHA256SUMS`, so what `install <name>` fetches is pinned by a signed
release and checked again by the manifest's own signature. New adapters, new versions and
re-signed manifests arrive with a release; the signing job (§5.1) opens the pull request that adds
an entry. An adapter published between releases is installed with `--repo <org>/<repo>@<commit>`.
"""

import re
from typing import Final

# The Dreamference organisation on Hugging Face (the slug is §17's question 9: assumed until the
# organisation is created). One repository per adapter, `<org>/mightling-<name>`.
DREAMFERENCE_HF_ORG: Final[str] = "dreamference"

# name -> entries, newest last. Each entry: version, display_name, repo, commit (40 hex),
# manifest_sha256 (of the signed manifest.json at that commit), base_alias (a model matrix key)
# and size (bytes of the files the manifest lists). Empty until the first adapter meets the
# publishing bar (§7.3).
ADAPTER_CATALOG: Final[dict[str, list[dict[str, object]]]] = {}

REPO: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}"
)
COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")


class AdapterCatalog:
    """Lookups in the catalogue the release ships."""

    # The catalogue read; the tests replace it.
    entries: dict[str, list[dict[str, object]]] = ADAPTER_CATALOG

    @classmethod
    def find(cls, name: str, version: str | None = None) -> dict[str, object] | None:
        """The catalogue's entry for a name, at a version or the newest.

        Args:
            name: The adapter's name.
            version: A version, or None for the newest.

        Returns:
            dict[str, object] | None: The entry with its `name` added, or None.
        """
        versions = cls.entries.get(name) or []
        chosen = [entry for entry in versions if version is None or entry.get("version") == version]
        if not chosen:
            return None
        return {**chosen[-1], "name": name}

    @classmethod
    def available(cls) -> list[dict[str, object]]:
        """Every catalogued adapter at its newest version.

        Returns:
            list[dict[str, object]]: The entries, sorted by name.
        """
        found = (cls.find(name) for name in sorted(cls.entries))
        return [entry for entry in found if entry is not None]

    @classmethod
    def parse_repo(cls, text: str) -> tuple[str, str]:
        """Reads `<org>/<repo>@<commit>`, refusing a branch or tag in place of a commit.

        Args:
            text: What `--repo` was given.

        Returns:
            tuple[str, str]: The repository and the commit.

        Raises:
            ValueError: When it is not that shape.
        """
        repo, separator, commit = text.partition("@")
        if not separator or not REPO.fullmatch(repo):
            raise ValueError(f"{text!r} is not <org>/<repo>@<commit>")
        if not COMMIT.fullmatch(commit):
            raise ValueError(
                f"{commit!r} is not a commit: name the full 40-character commit, never a branch or a tag"
            )
        return repo, commit
