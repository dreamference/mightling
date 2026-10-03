"""
The bundle a provisioning run installs from (specs/DREAMFERENCE_PUFFIN_FLEET.md §7.2).

`install.sh --from <dir>` takes a folder holding the same asset names and the same
`puffin-<target>.sha256sums` as a release, so a new node installs what this machine runs with no
GitHub token. Two sources:

- `this` (the default): the binaries installed here, gzipped under the release names, and a wheel
  of this checkout's `dreamference` package. A new node then runs this machine's own build, even
  one never released. On a release install there is no wheel to copy (pip keeps none), so `this`
  there means the installed version's release.
- `release[=X.Y.Z]`: a release's own assets, downloaded once here with this machine's `gh` login.
"""

import gzip
import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final, List, Optional, Tuple

BUNDLE_ROOT: Final[str] = os.path.expanduser("~/.cache/dreamference/fleet")

# The binaries a node needs, as the release names them; the first two are required.
REQUIRED_BINARIES: Final[Tuple[str, ...]] = ("puffin", "codex-code-mode-host")
OPTIONAL_BINARIES: Final[Tuple[str, ...]] = ("puffin-search", "puffin-fetch", "puffin-code")

RELEASE_REPO: Final[str] = os.environ.get("PUFFIN_RELEASE_REPO", "dgxcoder/dgxcoder")


class FleetBundle:
    """Builds the folder `install.sh --from` installs."""

    @classmethod
    def target(cls) -> str:
        """
        Returns:
            str: This machine's release target, `aarch64-unknown-linux-gnu` on a GB10.
        """
        machine = {"arm64": "aarch64", "amd64": "x86_64"}.get(
            platform.machine(), platform.machine()
        )
        return f"{machine}-unknown-linux-gnu"

    @classmethod
    def build(cls, source: str = "this") -> Optional[Path]:
        """
        Builds (or reuses) the bundle for one run.

        Args:
            source: `this`, `release` or `release=X.Y.Z`.

        Returns:
            Optional[Path]: The bundle folder, or None with the reason printed.
        """
        from dreamference.runner.codex_branded_builder import CodexBrandedBuilder

        if source == "this" and CodexBrandedBuilder.has_source():
            return cls.from_this()
        version = source.partition("=")[2] if source.startswith("release=") else None
        if source == "this":
            from dreamference import __version__

            version = __version__
        if source not in ("this", "release") and not source.startswith("release="):
            print(f"❌ --from is `this` or `release[=X.Y.Z]`, not `{source}`.")
            return None
        return cls.from_release(version)

    @classmethod
    def from_this(cls) -> Optional[Path]:
        """
        Returns:
            Optional[Path]: A bundle of the binaries installed here and a wheel of this checkout,
            or None if a required binary is missing or the wheel could not be built.
        """
        from dreamference.runner.codex_branded_builder import INSTALL_DIR, REPO_ROOT

        target = cls.target()
        binaries = Path(INSTALL_DIR) / "bin"
        missing = [name for name in REQUIRED_BINARIES if not (binaries / name).is_file()]
        if missing:
            print(
                f"❌ {', '.join(missing)} not installed here; run `puffin-admin codex build` first."
            )
            return None
        digest = cls._digest(
            [
                binaries / name
                for name in (*REQUIRED_BINARIES, *OPTIONAL_BINARIES)
                if (binaries / name).is_file()
            ]
        )
        bundle = Path(BUNDLE_ROOT) / f"bundle-this-{digest[:12]}"
        if (
            (bundle / f"puffin-{target}.sha256sums").is_file()
            and any(bundle.glob("dreamference-*.whl"))
            and (bundle / "install.sh").is_file()
        ):
            return bundle
        staging = bundle.with_name(bundle.name + ".partial")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        sums = []
        for name in (*REQUIRED_BINARIES, *OPTIONAL_BINARIES):
            if not (binaries / name).is_file():
                continue
            asset = staging / f"{name}-{target}.gz"
            with (
                open(binaries / name, "rb") as raw,
                gzip.open(asset, "wb", compresslevel=6) as packed,
            ):
                shutil.copyfileobj(raw, packed)
            sums.append(f"{cls._sha256(asset)}  {asset.name}")
        (staging / f"puffin-{target}.sha256sums").write_text("\n".join(sums) + "\n")
        shutil.copy2(Path(REPO_ROOT) / "install.sh", staging / "install.sh")
        wheel = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--quiet",
                "-w",
                str(staging),
                REPO_ROOT,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if wheel.returncode != 0 or not any(staging.glob("dreamference-*.whl")):
            print(f"❌ Could not build a wheel of this checkout: {wheel.stderr.strip()[-300:]}")
            return None
        (staging / "VERSION").write_text(f"this-{digest[:12]}\n")
        shutil.rmtree(bundle, ignore_errors=True)
        staging.rename(bundle)
        return bundle

    @classmethod
    def from_release(cls, version: Optional[str]) -> Optional[Path]:
        """
        Args:
            version: `X.Y.Z`, or None for the latest release.

        Returns:
            Optional[Path]: A bundle of the release's assets for this target, downloaded with
            this machine's `gh` login, or None.
        """
        if shutil.which("gh") is None:
            print("❌ --from release downloads with `gh`, which is not installed here.")
            return None
        tag = f"v{version.lstrip('v')}" if version else None
        bundle = Path(BUNDLE_ROOT) / f"bundle-{tag or 'latest'}"
        target = cls.target()
        if (bundle / f"puffin-{target}.sha256sums").is_file() and tag:
            return bundle
        bundle.mkdir(parents=True, exist_ok=True)
        command = [
            "gh",
            "release",
            "download",
            *([tag] if tag else []),
            "-R",
            RELEASE_REPO,
            "-D",
            str(bundle),
            "--clobber",
            "-p",
            f"*-{target}.gz",
            "-p",
            f"puffin-{target}.sha256sums",
            "-p",
            "dreamference-*.whl",
            "-p",
            "install.sh",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"❌ Could not download the release: {result.stderr.strip()[-300:]}")
            return None
        (bundle / "VERSION").write_text(f"{tag or 'latest'}\n")
        return bundle

    @classmethod
    def version(cls, bundle: Path) -> str:
        """
        Args:
            bundle: A bundle folder.

        Returns:
            str: What it records as its version (`this-<digest>` or a release tag).
        """
        try:
            return (bundle / "VERSION").read_text().strip()
        except OSError:
            return "bundle"

    @classmethod
    def files(cls, bundle: Path) -> List[Path]:
        """
        Args:
            bundle: A bundle folder.

        Returns:
            List[Path]: Every file in it, which is what is copied to a node.
        """
        return sorted(path for path in bundle.iterdir() if path.is_file())

    @classmethod
    def _sha256(cls, path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _digest(cls, paths: List[Path]) -> str:
        """The bundle's name: a digest of the binaries' sizes and times, cheap to recompute."""
        digest = hashlib.sha256()
        for path in paths:
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}".encode())
        try:
            from dreamference.runner.codex_branded_builder import REPO_ROOT

            head = subprocess.run(
                ["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", REPO_ROOT, "status", "--porcelain", "dreamference"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            digest.update(f"{head}:{hashlib.sha256(dirty.encode()).hexdigest()}".encode())
        except OSError:
            pass
        return digest.hexdigest()
