"""
Puffin-Branded Codex Builder for Dreamference.

This module provides the CodexBrandedBuilder class, which turns the pinned `codex/` submodule (a
fork of openai/codex at a stable release tag) into `puffin`, the terminal agent. It is Codex with
Puffin's branding and with the launcher in `puffin-rs/` compiled in, so it finds the local model
server and configures itself with no Python involved.

The submodule is never modified. Each build exports the pinned commit with `git archive` into a
scratch tree, applies the patch series in `codex-patches/` there with `git apply`, and compiles that
tree, after copying `puffin-rs/` in beside the workspace crates as `codex-rs/puffin` -- so the fork
stays byte-identical to upstream and moving to a newer release is a submodule
bump plus whatever patch hunks stop applying. Only `codex-rs/` is exported: it is the whole Rust
workspace, and the rest of the repository (the npm wrapper, Bazel files, SDKs) plays no part in a
Cargo build.

The compiled output is keyed by the source commit, the patch contents and the launcher's source, so an unchanged tree is
not rebuilt, and Cargo's target directory is kept across builds so a patch edit recompiles only the
crates it touches rather than the several hundred dependencies beneath them.
"""

import hashlib
import os
import shutil
import subprocess
from typing import Final, List, Optional

from dreamference.chat.desktop_installer import DesktopInstaller
from dreamference.chat.desktop_runner import DesktopRunner

REPO_ROOT: Final[str] = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
CODEX_SUBMODULE_DIR: Final[str] = os.path.join(REPO_ROOT, "codex")
CODEX_PATCH_DIR: Final[str] = os.path.join(REPO_ROOT, "codex-patches")

# The launcher crate. It is Dreamference's own Rust, so it lives here as source rather than inside a
# patch, and is copied into the exported tree where patch 0002's dependency line expects it.
PUFFIN_CRATE_DIR: Final[str] = os.path.join(REPO_ROOT, "puffin-rs")
PUFFIN_CRATE_DEST: Final[str] = os.path.join("codex-rs", "puffin")

# The release the patches are written against. The submodule is pinned to this tag's commit; the
# constant exists so a mismatch can be reported by name rather than as a hunk that fails to apply.
CODEX_RELEASE_TAG: Final[str] = "rust-v0.158.0"

# The cache keeps its original name: Cargo's target directory inside it holds several hundred
# compiled dependencies, and renaming it would throw them away.
BUILD_CACHE_DIR: Final[str] = os.path.expanduser("~/.cache/dreamference/puffin-codex")
INSTALL_DIR: Final[str] = os.path.expanduser("~/.local/share/dreamference/puffin")

# Cargo still builds the binary as `codex` -- renaming the [[bin]] and `default-run` would be two
# more patch hunks for a name the builder can simply give the file when it installs it. Codex's
# own help and --version already say `puffin` (patch 0001 sets clap's name and bin_name).
CARGO_BIN_NAME: Final[str] = "codex"
BRANDED_EXECUTABLE_NAME: Final[str] = "puffin"

# Where the user types `puffin`. A symlink rather than a copy, because Codex finds
# codex-code-mode-host next to its own executable, and it resolves that through the link.
PATH_LINK: Final[str] = os.path.expanduser("~/.local/bin/puffin")

# Code Mode runs its JavaScript in a separate host process that Codex looks for next to its own
# executable, so the two binaries are built and installed together.
CODE_MODE_HOST_NAME: Final[str] = "codex-code-mode-host"

# Upstream's release profile keeps line tables (`debug = "line-tables-only"`, `strip = false`) so
# its CI can archive symbols, and strips only when it packages. Built as-is, puffin-codex is 1.4 GB
# rather than ~315 MB -- and the runner reads the whole file on every launch to recover its system
# prompt. Overridden through Cargo's environment rather than a patch, so it touches no Codex source,
# and applied at compile time, which also spares generating debug info that would be thrown away.
RELEASE_PROFILE_OVERRIDES: Final[dict] = {
    "CARGO_PROFILE_RELEASE_DEBUG": "none",
    "CARGO_PROFILE_RELEASE_STRIP": "debuginfo",
}

# Records which source commit and patch set the installed binaries were built from.
BUILD_STAMP_NAME: Final[str] = "build-key"

# Code Mode embeds V8. The `v8` crate's own build script downloads a prebuilt library from
# denoland/rusty_v8, but Codex builds V8 with pointer compression and the sandbox on, a flavour
# denoland does not publish for aarch64 Linux -- the download 404s. OpenAI publishes that flavour on
# its own releases, and the submodule carries a manifest of their checksums, which is what
# `.github/actions/setup-rusty-v8` in the submodule verifies against before a release build.
RUSTY_V8_RELEASE_URL: Final[str] = "https://github.com/openai/codex/releases/download/rusty-v8-v{version}"
RUSTY_V8_PROFILE: Final[str] = "ptrcomp_sandbox_release"


class CodexBrandedBuilder:
    """
    Builds and installs the Puffin-branded Codex from the submodule plus the patch series.
    """

    @classmethod
    def executable_path(cls) -> str:
        """
        Returns where the branded executable is installed.

        Returns:
            str: Absolute path to `puffin-codex`, whether or not it has been built yet.
        """
        return os.path.join(INSTALL_DIR, "bin", BRANDED_EXECUTABLE_NAME)

    @classmethod
    def patches(cls) -> List[str]:
        """
        Lists the patch series in the order it is applied.

        Returns:
            List[str]: Absolute paths of the `.patch` files, sorted by their numeric prefix.
        """
        if not os.path.isdir(CODEX_PATCH_DIR):
            return []
        return [
            os.path.join(CODEX_PATCH_DIR, name)
            for name in sorted(os.listdir(CODEX_PATCH_DIR))
            if name.endswith(".patch")
        ]

    @classmethod
    def source_commit(cls) -> Optional[str]:
        """
        Resolves the commit the submodule is checked out at.

        Returns:
            Optional[str]: The full commit hash, or None if the submodule is not checked out.
        """
        result = subprocess.run(
            ["git", "-C", CODEX_SUBMODULE_DIR, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not os.path.isdir(os.path.join(CODEX_SUBMODULE_DIR, "codex-rs")):
            return None
        return result.stdout.strip()

    @classmethod
    def build_key(cls) -> Optional[str]:
        """
        Identifies a build by its inputs: the source commit, the exact bytes of every patch and of
        every launcher source file, and the profile overrides, since changing any of those changes
        the binary as surely as a patch does.

        Returns:
            Optional[str]: A short key, or None if the submodule is not checked out.
        """
        commit = cls.source_commit()
        if commit is None:
            return None
        digest = hashlib.sha256()
        digest.update(repr(sorted(RELEASE_PROFILE_OVERRIDES.items())).encode())
        for patch in cls.patches():
            digest.update(os.path.basename(patch).encode())
            with open(patch, "rb") as handle:
                digest.update(handle.read())
        for path in cls.launcher_files():
            digest.update(os.path.relpath(path, PUFFIN_CRATE_DIR).encode())
            with open(path, "rb") as handle:
                digest.update(handle.read())
        return f"{commit[:12]}-{digest.hexdigest()[:12]}"

    @classmethod
    def launcher_files(cls) -> List[str]:
        """
        Lists the launcher crate's source files, in a stable order.

        Returns:
            List[str]: Absolute paths under `puffin-rs/`, excluding any local build output.
        """
        found: List[str] = []
        for root, dirs, files in os.walk(PUFFIN_CRATE_DIR):
            dirs[:] = sorted(d for d in dirs if d != "target")
            found.extend(os.path.join(root, name) for name in sorted(files))
        return found

    @classmethod
    def is_current(cls) -> bool:
        """
        Checks that both binaries are installed and were built from the current inputs.

        Returns:
            bool: True if no rebuild is needed.
        """
        key = cls.build_key()
        stamp = os.path.join(INSTALL_DIR, BUILD_STAMP_NAME)
        if key is None or not os.path.isfile(stamp):
            return False
        with open(stamp) as handle:
            if handle.read().strip() != key:
                return False
        host = os.path.join(INSTALL_DIR, "bin", CODE_MODE_HOST_NAME)
        return all(
            os.path.isfile(path) and os.access(path, os.X_OK)
            for path in (cls.executable_path(), host)
        )

    @classmethod
    def prepare_source(cls, source_dir: str) -> bool:
        """
        Exports the pinned commit into `source_dir`, adds the launcher crate, and applies the patch
        series to that copy.

        Args:
            source_dir (str): Scratch directory to create; any previous contents are removed.

        Returns:
            bool: True if the export succeeded and every patch applied cleanly.
        """
        commit = cls.source_commit()
        if commit is None:
            print("❌ The codex submodule is not checked out.")
            print("💡 Fetch it with: git submodule update --init codex")
            return False

        shutil.rmtree(source_dir, ignore_errors=True)
        os.makedirs(source_dir)
        archive = subprocess.Popen(
            ["git", "-C", CODEX_SUBMODULE_DIR, "archive", commit, "codex-rs"],
            stdout=subprocess.PIPE,
        )
        extracted = subprocess.run(["tar", "-x", "-C", source_dir], stdin=archive.stdout, check=False)
        archive.stdout.close()
        if archive.wait() != 0 or extracted.returncode != 0:
            print(f"❌ Could not export codex {commit[:12]} from the submodule.")
            return False
        # Before the patches, because 0002 makes the CLI depend on it.
        shutil.copytree(
            PUFFIN_CRATE_DIR,
            os.path.join(source_dir, PUFFIN_CRATE_DEST),
            ignore=shutil.ignore_patterns("target"),
        )

        for patch in cls.patches():
            # Checked first so a patch that does not fit leaves nothing half-applied behind it.
            check = subprocess.run(["git", "apply", "--check", patch], cwd=source_dir, check=False)
            if check.returncode != 0:
                print(f"❌ {os.path.basename(patch)} does not apply to codex {commit[:12]}.")
                print(f"💡 The patches are written against {CODEX_RELEASE_TAG}; refresh them after a submodule bump.")
                return False
            subprocess.run(["git", "apply", patch], cwd=source_dir, check=True)
        return True

    @classmethod
    def host_target(cls) -> str:
        """
        Returns the Rust target triple of this machine, which is the one Codex is built for.

        Returns:
            str: For example `aarch64-unknown-linux-gnu` on GB10.
        """
        import platform

        return f"{platform.machine()}-unknown-linux-gnu"

    @classmethod
    def fetch_rusty_v8(cls, source_dir: str) -> Optional[dict]:
        """
        Downloads OpenAI's prebuilt V8 for this target and verifies it against the pinned checksums.

        Trust runs in one direction: the per-target checksum file is checked against the manifest
        committed in the submodule, and the archive and its Rust bindings are then checked against
        that file. Nothing downloaded is used unverified, and verified files are kept, so later
        builds do not fetch them again.

        Args:
            source_dir (str): The exported tree, read for the `v8` version Codex pins.

        Returns:
            Optional[dict]: The `RUSTY_V8_ARCHIVE` and `RUSTY_V8_SRC_BINDING_PATH` variables for
                Cargo, or None if the files could not be fetched or failed verification.
        """
        import re
        import urllib.request

        with open(os.path.join(source_dir, "codex-rs", "Cargo.toml")) as handle:
            pinned = re.search(r'^v8 = "=([\d.]+)"', handle.read(), re.M)
        if pinned is None:
            print("❌ Could not find the v8 version pinned in codex-rs/Cargo.toml.")
            return None
        version = pinned.group(1)
        target = cls.host_target()

        manifest = subprocess.run(
            ["git", "-C", CODEX_SUBMODULE_DIR, "show",
             f"HEAD:third_party/v8/rusty_v8_{version.replace('.', '_')}_release_manifests.sha256"],
            capture_output=True, text=True, check=False,
        )
        checksums_name = f"rusty_v8_{RUSTY_V8_PROFILE}_{target}.sha256"
        trusted = {
            name: digest
            for digest, name in (line.split() for line in manifest.stdout.splitlines() if line.strip())
        }.get(checksums_name)
        if manifest.returncode != 0 or trusted is None:
            print(f"❌ The submodule pins no prebuilt V8 {version} for {target}.")
            return None

        v8_dir = os.path.join(BUILD_CACHE_DIR, "rusty_v8", version)
        os.makedirs(v8_dir, exist_ok=True)
        base_url = RUSTY_V8_RELEASE_URL.format(version=version)

        def sha256(path: str) -> str:
            digest = hashlib.sha256()
            with open(path, "rb") as blob:
                for chunk in iter(lambda: blob.read(1 << 20), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        def fetch(name: str, expected: str) -> Optional[str]:
            path = os.path.join(v8_dir, name)
            if os.path.isfile(path) and sha256(path) == expected:
                return path
            print(f"📥 Fetching {name}...")
            try:
                urllib.request.urlretrieve(f"{base_url}/{name}", path)
            except OSError as error:
                print(f"❌ Could not download {name}: {error}")
                return None
            if sha256(path) != expected:
                os.remove(path)
                print(f"❌ {name} does not match its pinned checksum; discarded.")
                return None
            return path

        checksums_path = fetch(checksums_name, trusted)
        if checksums_path is None:
            return None
        with open(checksums_path) as handle:
            # Some of OpenAI's manifests are written with CRLF line endings.
            listed = {
                name.lstrip("*"): digest
                for digest, name in (line.split() for line in handle.read().replace("\r", "").splitlines() if line.strip())
            }
        archive_name = f"librusty_v8_{RUSTY_V8_PROFILE}_{target}.a.gz"
        binding_name = f"src_binding_{RUSTY_V8_PROFILE}_{target}.rs"
        if archive_name not in listed or binding_name not in listed:
            print(f"❌ {checksums_name} does not list the V8 archive and bindings for {target}.")
            return None
        archive = fetch(archive_name, listed[archive_name])
        binding = fetch(binding_name, listed[binding_name])
        if archive is None or binding is None:
            return None
        return {"RUSTY_V8_ARCHIVE": archive, "RUSTY_V8_SRC_BINDING_PATH": binding}

    @classmethod
    def build(cls, force: bool = False) -> bool:
        """
        Builds the branded Codex and installs it, unless the installed copy is already current.

        Args:
            force (bool): Rebuild even if the installed binaries match the current inputs.

        Returns:
            bool: True if an up-to-date `puffin-codex` is installed afterwards.
        """
        if not force and cls.is_current():
            return True
        # The toolchain version itself is pinned by codex-rs/rust-toolchain.toml; rustup fetches it
        # on the first cargo invocation, so only rustup has to exist beforehand.
        if not DesktopInstaller.install_rust():
            return False

        key = cls.build_key()
        source_dir = os.path.join(BUILD_CACHE_DIR, "src")
        if key is None or not cls.prepare_source(source_dir):
            return False

        rusty_v8 = cls.fetch_rusty_v8(source_dir)
        if rusty_v8 is None:
            return False

        environment = DesktopRunner._environment()
        environment.update(rusty_v8)
        environment["CARGO_TARGET_DIR"] = os.path.join(BUILD_CACHE_DIR, "target")
        environment.update(RELEASE_PROFILE_OVERRIDES)
        # No --locked: upstream's Cargo.lock records the workspace crates at version 0.0.0, which
        # its release job bumps just before building, so Cargo rewrites those 158 entries. Every
        # third-party version stays exactly as the lockfile pins it.
        command = [
            "cargo", "build", "--release",
            "-p", "codex-cli", "--bin", CARGO_BIN_NAME,
            "-p", "codex-code-mode-host", "--bin", CODE_MODE_HOST_NAME,
        ]
        print(f"🔨 Building Puffin-branded Codex ({CODEX_RELEASE_TAG}, {len(cls.patches())} patches)...")
        if subprocess.call(command, cwd=os.path.join(source_dir, "codex-rs"), env=environment) != 0:
            print("❌ The Codex build failed; see the cargo output above.")
            return False

        release_dir = os.path.join(BUILD_CACHE_DIR, "target", "release")
        bin_dir = os.path.join(INSTALL_DIR, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        for built, installed in ((CARGO_BIN_NAME, BRANDED_EXECUTABLE_NAME), (CODE_MODE_HOST_NAME, CODE_MODE_HOST_NAME)):
            # Copied to a temporary name and renamed over the old one, so a running session keeps
            # its binary and a new one never sees a half-written file.
            staging = os.path.join(bin_dir, f".{installed}.new")
            shutil.copy2(os.path.join(release_dir, built), staging)
            os.replace(staging, os.path.join(bin_dir, installed))
        with open(os.path.join(INSTALL_DIR, BUILD_STAMP_NAME), "w") as handle:
            handle.write(f"{key}\n")

        cls.link_onto_path()
        print(f"✅ Installed {cls.executable_path()}")
        return True

    @classmethod
    def link_onto_path(cls) -> None:
        """
        Points `~/.local/bin/puffin` at the installed executable, so `puffin` works from any shell.

        Only a missing file or an existing symlink is replaced; a real file of that name belongs to
        something else and is reported instead of overwritten.
        """
        os.makedirs(os.path.dirname(PATH_LINK), exist_ok=True)
        if os.path.lexists(PATH_LINK) and not os.path.islink(PATH_LINK):
            print(f"⚠️ {PATH_LINK} exists and is not a link; leaving it. Run {cls.executable_path()} directly.")
            return
        staging = f"{PATH_LINK}.new"
        if os.path.lexists(staging):
            os.remove(staging)
        os.symlink(cls.executable_path(), staging)
        os.replace(staging, PATH_LINK)
