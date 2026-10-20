"""What remote access downloads, pinned by version and SHA-256 (REMOTE_ACCESS §14.1).

Every value was read from a published checksum file or registry digest on 2026-10-20: NetBird's
`netbird_0.81.3_checksums.txt` and Docker Hub's records of the `0.81.3` tags, and go-gost's
`checksums.txt` for v3.3.0. A pin changes only with a value read the same way.
"""

from typing import Final

NETBIRD_VERSION: Final[str] = "0.81.3"
NETBIRD_RELEASES: Final[str] = "https://github.com/netbirdio/netbird/releases/download"

# The client's release archives, by (system, architecture) as `uname` reports them.
NETBIRD_CLIENT_SHA256: Final[dict[tuple[str, str], str]] = {
    ("linux", "x86_64"): "cbdea310068e35449f034544d5137fefee8f6d948fffd020883ffc8b3bab63b1",
    ("linux", "aarch64"): "980eef938177108bdfa7d802566b6309123592a58631349a78f11cd97c76c22d",
    ("darwin", "x86_64"): "95ef6a3f3c56d7d7d96e66fdf2523a6ee8c719b8beb959e9f15c6a101e4cac1f",
    ("darwin", "arm64"): "bc37108ee88cbc0a998031231570a2c62f03c0937867a0114b8d137e7840fe71",
}
# `uname -m` → the architecture word in NetBird's and gost's archive names.
ARCHIVE_ARCH: Final[dict[str, str]] = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}

# The combined server (management and signal; its relay is switched off), the multi-architecture
# index, pinned by digest like the Matrix homeserver's image.
NETBIRD_SERVER_IMAGE: Final[str] = (
    "netbirdio/netbird-server:0.81.3"
    "@sha256:541ecef7e98a66b17411d44e9d4dcfdb65913415f39b3b944b8e7e748f997f1d"
)

# The relay ships only as an image; the box gets its static binary, taken out of the image pinned
# by the box architecture's own manifest digest (§14.2, item 3).
NETBIRD_RELAY_REPOSITORY: Final[str] = "netbirdio/relay"
NETBIRD_RELAY_INDEX_DIGEST: Final[str] = (
    "sha256:721a15a9de4817b142134b5e039bfc3559b0c80db8054408d3723bc52ccba4ec"
)
NETBIRD_RELAY_MANIFEST_DIGEST: Final[dict[str, str]] = {
    "amd64": "sha256:de516151c3d90f8bdc8e55af34eb14a727dac6b167308cb6d771232a9a970324",
    "arm64": "sha256:f5f7bba04394051a67e5c6ed57c4ce5d4ee0b4fb77804ce317cb0b894cba41c7",
}
NETBIRD_RELAY_BINARY: Final[str] = "/go/bin/netbird-relay"

# The SNI router on the box (§14.3, item 1).
GOST_VERSION: Final[str] = "3.3.0"
GOST_SHA256: Final[dict[str, str]] = {
    "amd64": "676fb7f78d267b6ae73df719c0c7f2b565dde7147da935cfafbc1e1da558b6d5",
    "arm64": "d03699e3f385d4ff5dad68046712adfcc7515325a064d2ab046e0bece30f8f8f",
}


class NetBirdPins:
    """The URLs and digests of what remote access fetches."""

    @classmethod
    def client_archive(cls, system: str, machine: str) -> tuple[str, str] | None:
        """The NetBird client's release archive for a machine.

        Args:
            system: `uname -s`, any case (`Linux`, `Darwin`).
            machine: `uname -m` (`x86_64`, `aarch64`, `arm64`).

        Returns:
            tuple[str, str] | None: The URL and its pinned SHA-256, or None where none is pinned.
        """
        key = (system.lower(), machine)
        sha = NETBIRD_CLIENT_SHA256.get(key)
        arch = ARCHIVE_ARCH.get(machine)
        if sha is None or arch is None:
            return None
        name = f"netbird_{NETBIRD_VERSION}_{key[0]}_{arch}.tar.gz"
        return f"{NETBIRD_RELEASES}/v{NETBIRD_VERSION}/{name}", sha

    @classmethod
    def relay_image(cls, arch: str) -> str | None:
        """The relay's image for one architecture, pinned by that architecture's manifest.

        Args:
            arch: `amd64` or `arm64`.

        Returns:
            str | None: `netbirdio/relay@sha256:…`, or None for an architecture not pinned.
        """
        digest = NETBIRD_RELAY_MANIFEST_DIGEST.get(arch)
        return f"{NETBIRD_RELAY_REPOSITORY}@{digest}" if digest else None

    @classmethod
    def gost_archive(cls, arch: str) -> tuple[str, str] | None:
        """Gost's release archive for one architecture.

        Args:
            arch: `amd64` or `arm64`.

        Returns:
            tuple[str, str] | None: The URL and its pinned SHA-256, or None.
        """
        sha = GOST_SHA256.get(arch)
        if sha is None:
            return None
        name = f"gost_{GOST_VERSION}_linux_{arch}.tar.gz"
        return f"https://github.com/go-gost/gost/releases/download/v{GOST_VERSION}/{name}", sha
