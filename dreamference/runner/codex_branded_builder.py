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

The agent's web commands, `puffin-search` and `puffin-fetch`, are built here too, from the
standalone crate `puffin-web-rs/`: a separate Cargo build with its own lockfile, target directory
and stamp, installed beside `puffin`, so either can be rebuilt without the other.

All of that needs a checkout. A machine installed from a release (`install.sh`) has the package
from a wheel and the binaries from the release's assets, with no `codex/`, `codex-patches/` or
crate directories beside it; `has_source()` tells the two apart, and without source the installed
binaries count as current, since there is nothing here they could be rebuilt from.
"""

import hashlib
import os
import re
import sys
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

# Where `puffin-admin` and the web commands become reachable from any shell -- including the one
# `puffin` runs the model's commands in. The prompt tells the model to use `puffin-search`,
# `puffin-fetch` and `puffin-admin gmail` for web and mail access, but the commands only existed
# inside the repository's virtualenv, so every such call ended in "command not found" (exit 127).
ADMIN_PATH_LINK: Final[str] = os.path.expanduser("~/.local/bin/puffin-admin")
SEARCH_PATH_LINK: Final[str] = os.path.expanduser("~/.local/bin/puffin-search")
FETCH_PATH_LINK: Final[str] = os.path.expanduser("~/.local/bin/puffin-fetch")

# The agent's web commands, `puffin-search` and `puffin-fetch`: a small Rust crate of its own rather
# than part of the launcher, so changing them never relinks Codex, and a static binary rather than
# a console script, so they do not depend on this virtualenv. Built with its own lockfile into its
# own target directory, installed beside `puffin`, and stamped separately from the Codex build.
WEB_CRATE_DIR: Final[str] = os.path.join(REPO_ROOT, "puffin-web-rs")
WEB_BUILD_CACHE_DIR: Final[str] = os.path.expanduser("~/.cache/dreamference/puffin-web")
WEB_BUILD_STAMP_NAME: Final[str] = "web-build-key"
WEB_BIN_NAMES: Final[tuple] = ("puffin-search", "puffin-fetch")

# The code index router, `puffin-code` (specs/DREAMFERENCE_PUFFIN_CODE_INDEX.md §4.2): a crate of
# its own for the same reasons as the web commands, built the same way. The prompt tells the model
# to run it, so it is linked onto PATH beside the others.
CODE_CRATE_DIR: Final[str] = os.path.join(REPO_ROOT, "puffin-code-rs")
CODE_BUILD_CACHE_DIR: Final[str] = os.path.expanduser("~/.cache/dreamference/puffin-code-build")
CODE_BUILD_STAMP_NAME: Final[str] = "code-build-key"
CODE_BIN_NAMES: Final[tuple] = ("puffin-code",)
CODE_PATH_LINK: Final[str] = os.path.expanduser("~/.local/bin/puffin-code")

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
    def has_source(cls) -> bool:
        """
        Tells a checkout from a release install.

        Until 2026-10-02 a release install was treated as a stale build: `puffin-admin run` and
        `codex build` installed rustup and then died with FileNotFoundError on the missing
        `puffin-web-rs/` (measured with the v1.3.0 wheel in a scratch home).

        Returns:
            bool: True if the patch series and the launcher crate are beside the package, i.e.
            `puffin` can be built here.
        """
        return os.path.isdir(CODEX_PATCH_DIR) and os.path.isdir(PUFFIN_CRATE_DIR)

    @classmethod
    def binaries_installed(cls, names: tuple) -> bool:
        """
        Checks that binaries are present in the install directory.

        Args:
            names (tuple): File names under `bin/`.

        Returns:
            bool: True if every one is an executable file.
        """
        return all(
            os.path.isfile(path) and os.access(path, os.X_OK)
            for path in (os.path.join(INSTALL_DIR, "bin", name) for name in names)
        )

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
        digest.update(f"version={cls.puffin_version()}".encode())
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
        return cls.crate_files(PUFFIN_CRATE_DIR)

    @classmethod
    def crate_files(cls, crate_dir: str) -> List[str]:
        """
        Lists a crate's source files, in a stable order.

        Args:
            crate_dir (str): The crate's directory.

        Returns:
            List[str]: Absolute paths under `crate_dir`, excluding any local build output.
        """
        found: List[str] = []
        for root, dirs, files in os.walk(crate_dir):
            dirs[:] = sorted(d for d in dirs if d != "target")
            found.extend(os.path.join(root, name) for name in sorted(files))
        return found

    @classmethod
    def is_current(cls) -> bool:
        """
        Checks that both binaries are installed and were built from the current inputs.

        Returns:
            bool: True if no rebuild is needed. Without a checkout, True if they are installed.
        """
        if not cls.has_source():
            return cls.binaries_installed((BRANDED_EXECUTABLE_NAME, CODE_MODE_HOST_NAME))
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
        # Plain copy, not copy2: Cargo decides freshness by comparing source mtimes with its last
        # build, so a file carrying its original, older mtime can be taken as already compiled even
        # though its content changed -- and the stale launcher gets linked in.
        shutil.copytree(
            PUFFIN_CRATE_DIR,
            os.path.join(source_dir, PUFFIN_CRATE_DEST),
            ignore=shutil.ignore_patterns("target"),
            copy_function=shutil.copy,
        )

        for patch in cls.patches():
            # Checked first so a patch that does not fit leaves nothing half-applied behind it.
            check = subprocess.run(["git", "apply", "--check", patch], cwd=source_dir, check=False)
            if check.returncode != 0:
                print(f"❌ {os.path.basename(patch)} does not apply to codex {commit[:12]}.")
                print(f"💡 The patches are written against {CODEX_RELEASE_TAG}; refresh them after a submodule bump.")
                return False
            subprocess.run(["git", "apply", patch], cwd=source_dir, check=True)
        return cls.stamp_version(os.path.join(source_dir, "codex-rs", "Cargo.toml"), cls.puffin_version())

    @classmethod
    def puffin_version(cls) -> str:
        """
        The version `puffin` reports: the release being built (`PUFFIN_VERSION`, which the release
        workflow sets), else this package's own version.

        Returns:
            str: A version such as `1.4.1`.
        """
        from dreamference import __version__
        return os.environ.get("PUFFIN_VERSION") or __version__

    @classmethod
    def stamp_version(cls, manifest: str, version: str) -> bool:
        """
        Sets the exported workspace's version, so that every place the binaries read
        `CARGO_PKG_VERSION` -- `--version`, the session header, the status card, `exec`'s banner,
        `doctor` -- reports Puffin's release rather than the upstream tag it was forked from. The
        export is edited, never the submodule, and no patch is needed.

        Args:
            manifest (str): The exported `codex-rs/Cargo.toml`.
            version (str): The version to stamp.

        Returns:
            bool: True if the manifest's `[workspace.package]` version was set.
        """
        if not re.fullmatch(r"\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?", version):
            print(f"❌ {version!r} is not a version Cargo accepts (for example 1.4.1).")
            return False
        with open(manifest) as handle:
            text = handle.read()
        section = re.search(r"^\[workspace\.package\]\n(?:(?!\[).*\n)*?version = \"[^\"]*\"", text, re.M)
        if section is None:
            print(f"❌ No [workspace.package] version in {manifest}.")
            return False
        stamped = section.group(0)[:section.group(0).rindex("version = ")] + f'version = "{version}"'
        with open(manifest, "w") as handle:
            handle.write(text[:section.start()] + stamped + text[section.end():])
        return True

    @classmethod
    def host_target(cls) -> str:
        """
        Returns the Rust target triple of this machine, which is the one Codex is built for.

        Returns:
            str: For example `aarch64-unknown-linux-gnu` on GB10, `aarch64-apple-darwin` on an Apple
            silicon Mac (whose `uname -m` says `arm64`), `x86_64-apple-darwin` on an Intel one.
        """
        import platform

        machine = {"arm64": "aarch64", "amd64": "x86_64"}.get(platform.machine().lower(), platform.machine())
        if platform.system() == "Darwin":
            return f"{machine}-apple-darwin"
        return f"{machine}-unknown-linux-gnu"

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
            bool: True if an up-to-date `puffin` and its web commands are installed afterwards.
        """
        if not cls.has_source():
            return cls._release_install_report()
        # First and independently: the web commands and the code index take seconds, and a stale
        # Codex must not keep them from updating, nor they it.
        web_ok = cls.build_web_tools(force=force)
        code_ok = cls.build_code_index(force=force)
        return cls._build_codex(force=force) and web_ok and code_ok

    @classmethod
    def _release_install_report(cls) -> bool:
        """
        What `build()` does where there is nothing to build from: says so, and refreshes the links.

        Returns:
            bool: True if the release's `puffin` is installed.
        """
        if cls.is_current():
            cls.link_onto_path()
            print(f"✅ puffin is installed from a release ({cls.executable_path()}); there is no "
                  "source here to build it from. `puffin update` installs a newer release.")
            return True
        print("❌ puffin is not installed, and this is not a checkout, so it cannot be built here.")
        print("💡 Install the release's binaries with install.sh (see the README), or clone the "
              "repository and run `puffin-admin codex build` there.")
        return False

    @classmethod
    def _build_codex(cls, force: bool = False) -> bool:
        """
        Builds and installs the branded Codex unless the installed copy is already current.

        Args:
            force (bool): Rebuild even if the installed binaries match the current inputs.

        Returns:
            bool: True if an up-to-date `puffin` is installed afterwards.
        """
        if not force and cls.is_current():
            # Cheap and idempotent, so an install that predates a link still gets it.
            cls.link_onto_path()
            return True
        # Every build starts by wiping the shared source tree, so two at once destroy each other
        # mid-compile ("Could not locate working directory"). An exclusive lock makes a second
        # build wait, and the re-check after it lets that one finish at once if the first build
        # already produced what it needed.
        import fcntl

        os.makedirs(BUILD_CACHE_DIR, exist_ok=True)
        with open(os.path.join(BUILD_CACHE_DIR, ".build.lock"), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("⏳ Another puffin build is running; waiting for it to finish...")
                fcntl.flock(lock, fcntl.LOCK_EX)
            if not force and cls.is_current():
                return True
            return cls._build_locked()

    @classmethod
    def _build_locked(cls) -> bool:
        """
        The build itself; `build()` holds the lock around it.

        Returns:
            bool: True if an up-to-date `puffin` is installed afterwards.
        """
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
        print(f"🔨 Building puffin (upstream {CODEX_RELEASE_TAG}, {len(cls.patches())} patches)...")
        if subprocess.call(command, cwd=os.path.join(source_dir, "codex-rs"), env=environment) != 0:
            print("❌ The puffin build failed; see the cargo output above.")
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
    def crate_key(cls, crate_dir: str) -> str:
        """
        Identifies a standalone crate's build by the exact bytes of its source files.

        Args:
            crate_dir (str): The crate's directory.

        Returns:
            str: A short key.
        """
        digest = hashlib.sha256()
        for path in cls.crate_files(crate_dir):
            digest.update(os.path.relpath(path, crate_dir).encode())
            with open(path, "rb") as handle:
                digest.update(handle.read())
        return digest.hexdigest()[:12]

    @classmethod
    def crate_is_current(cls, crate_dir: str, stamp_name: str, bin_names: tuple) -> bool:
        """
        Checks that a standalone crate's binaries are installed and built from its current source.

        Args:
            crate_dir (str): The crate's directory.
            stamp_name (str): The stamp file under the install directory that records its key.
            bin_names (tuple): The binaries it installs.

        Returns:
            bool: True if no rebuild is needed. Without the crate's source, True if they are
            installed.
        """
        if not os.path.isdir(crate_dir):
            return cls.binaries_installed(bin_names)
        stamp = os.path.join(INSTALL_DIR, stamp_name)
        if not os.path.isfile(stamp):
            return False
        with open(stamp) as handle:
            if handle.read().strip() != cls.crate_key(crate_dir):
                return False
        return all(
            os.access(os.path.join(INSTALL_DIR, "bin", name), os.X_OK) for name in bin_names
        )

    @classmethod
    def build_crate(
        cls, crate_dir: str, cache_dir: str, stamp_name: str, bin_names: tuple, force: bool = False
    ) -> bool:
        """
        Builds a standalone crate of Puffin's commands and installs its binaries beside `puffin`.

        Unlike the Codex build this compiles the crate in place, with `--locked` against its own
        committed lockfile, into its own target directory: it is Dreamference's source, not an
        export, and sharing Codex's target directory would make each rebuild wait on Codex's lock.

        Args:
            crate_dir (str): The crate's directory.
            cache_dir (str): Where its Cargo target directory lives.
            stamp_name (str): The stamp file under the install directory that records its key.
            bin_names (tuple): The binaries to install.
            force (bool): Rebuild even if the installed binaries match the source.

        Returns:
            bool: True if its current binaries are installed afterwards.
        """
        if not force and cls.crate_is_current(crate_dir, stamp_name, bin_names):
            cls.link_onto_path()
            return True
        if not DesktopInstaller.install_rust():
            return False
        environment = DesktopRunner._environment()
        environment["CARGO_TARGET_DIR"] = os.path.join(cache_dir, "target")
        command = ["cargo", "build", "--release", "--locked"]
        for name in bin_names:
            command += ["--bin", name]
        print(f"🔨 Building {', '.join(bin_names)}...")
        if subprocess.call(command, cwd=crate_dir, env=environment) != 0:
            print(f"❌ The {os.path.basename(crate_dir)} build failed; see the cargo output above.")
            return False
        bin_dir = os.path.join(INSTALL_DIR, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        for name in bin_names:
            # Renamed over the old file, so a command running now keeps its binary.
            staging = os.path.join(bin_dir, f".{name}.new")
            shutil.copy2(os.path.join(cache_dir, "target", "release", name), staging)
            os.replace(staging, os.path.join(bin_dir, name))
        with open(os.path.join(INSTALL_DIR, stamp_name), "w") as handle:
            handle.write(f"{cls.crate_key(crate_dir)}\n")
        cls.link_onto_path()
        print(f"✅ Installed {', '.join(bin_names)} in {bin_dir}")
        return True

    @classmethod
    def web_tools_are_current(cls) -> bool:
        """
        Checks that `puffin-search` and `puffin-fetch` are installed and built from current source.

        Returns:
            bool: True if no rebuild is needed.
        """
        return cls.crate_is_current(WEB_CRATE_DIR, WEB_BUILD_STAMP_NAME, WEB_BIN_NAMES)

    @classmethod
    def build_web_tools(cls, force: bool = False) -> bool:
        """
        Builds `puffin-search` and `puffin-fetch` from `puffin-web-rs/` unless they are current.

        Args:
            force (bool): Rebuild even if the installed binaries match the source.

        Returns:
            bool: True if both are installed and current afterwards.
        """
        return cls.build_crate(
            WEB_CRATE_DIR, WEB_BUILD_CACHE_DIR, WEB_BUILD_STAMP_NAME, WEB_BIN_NAMES, force=force
        )

    @classmethod
    def build_code_index(cls, force: bool = False) -> bool:
        """
        Builds `puffin-code` from `puffin-code-rs/` unless it is current.

        Args:
            force (bool): Rebuild even if the installed binary matches the source.

        Returns:
            bool: True if it is installed and current afterwards.
        """
        return cls.build_crate(
            CODE_CRATE_DIR, CODE_BUILD_CACHE_DIR, CODE_BUILD_STAMP_NAME, CODE_BIN_NAMES, force=force
        )

    @classmethod
    def console_script_path(cls, name: str) -> Optional[str]:
        """
        Returns a console script of the Python environment running this code, if it has one.

        Args:
            name (str): The script's name, e.g. `puffin-admin`.

        Returns:
            Optional[str]: Absolute path of the console script beside this interpreter, or None.
        """
        candidate = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), name)
        return candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else None

    @classmethod
    def link_onto_path(cls) -> None:
        """
        Points `~/.local/bin/puffin`, `puffin-admin`, `puffin-search`, `puffin-fetch` and
        `puffin-code` at their executables.

        `puffin` so it works from any shell; the others so the model can run the web and mail
        commands its prompt names from the shell `puffin` gives it. A web command is linked only
        once its binary is installed, so a link never dangles.
        """
        cls._link(cls.executable_path(), PATH_LINK)
        script = cls.console_script_path("puffin-admin")
        if script:
            cls._link(script, ADMIN_PATH_LINK)
        for name, link in (
            ("puffin-search", SEARCH_PATH_LINK),
            ("puffin-fetch", FETCH_PATH_LINK),
            ("puffin-code", CODE_PATH_LINK),
        ):
            binary = os.path.join(INSTALL_DIR, "bin", name)
            if os.access(binary, os.X_OK):
                cls._link(binary, link)

    @classmethod
    def _link(cls, target: str, link: str) -> None:
        """
        Makes `link` a symlink to `target`.

        Only a missing file or an existing symlink is replaced; a real file of that name belongs to
        something else and is reported instead of overwritten.

        Args:
            target (str): What the link points at.
            link (str): Path of the link.
        """
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if os.path.lexists(link) and not os.path.islink(link):
            print(f"⚠️ {link} exists and is not a link; leaving it. Run {target} directly.")
            return
        staging = f"{link}.new"
        if os.path.lexists(staging):
            os.remove(staging)
        os.symlink(target, staging)
        os.replace(staging, link)
