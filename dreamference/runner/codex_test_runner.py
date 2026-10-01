"""
Codex Test Runner for Dreamference.

This module provides the CodexTestRunner class, which runs Codex's own Rust test suite against the
tree `puffin` is built from: the pinned `codex/` submodule exported with `git archive`, the launcher
crate copied in and the patch series applied, exactly as `CodexBrandedBuilder` prepares it. The
submodule is never touched; the export and its build output live in the builder's cache, beside
(not inside) the directories the product build uses, so a test run never invalidates a build.

Not every Codex test applies to Puffin. Puffin renames Codex, hides OpenAI's account, cloud, voice
and feedback features, switches analytics off and keeps the model on this machine, so a test that
checks one of those upstream behaviours fails by design. `codex-tests/puffin-skips.toml` lists each
such test with the reason, and this runner leaves them out; everything else is expected to pass.

Renaming is the exception that is not skipped. Patch 0001 renames Codex on screen, so ~130 TUI
tests whose expected text says "OpenAI Codex" or "Ask Codex to do anything" fail on it, and they
are the tests that guard layout, wrapping and every popup. Instead of losing them, the test export
gets Puffin's expectations before it is built: `codex-tests/snapshots/` holds Puffin's accepted
versions of upstream's `.snap` files, copied over the originals, and `codex-tests/patches/` holds
test-only diffs for expectations written in Rust (inline snapshots, `contains` checks). Neither
reaches the build `puffin` comes from. `--accept-snapshots` regenerates the first after a Codex
bump and accepts a new snapshot only if it differs from upstream's by the name alone.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from typing import Final, List, Optional

from dreamference.chat.desktop_installer import DesktopInstaller
from dreamference.chat.desktop_runner import DesktopRunner
from dreamference.runner.codex_branded_builder import BUILD_CACHE_DIR, REPO_ROOT, CodexBrandedBuilder

# The skip list: which Codex tests Puffin does not run, and why.
SKIP_FILE: Final[str] = os.path.join(REPO_ROOT, "codex-tests", "puffin-skips.toml")

# Puffin's expectations for tests that check the on-screen name (see the module docstring).
SNAPSHOT_OVERLAY_DIR: Final[str] = os.path.join(REPO_ROOT, "codex-tests", "snapshots")
TEST_PATCHES_DIR: Final[str] = os.path.join(REPO_ROOT, "codex-tests", "patches")

# What patch 0001 puts on screen in place of upstream's names. A snapshot is accepted only if it
# equals upstream's once both are reduced by these (and by the padding the shorter name leaves).
UPSTREAM_NAMES: Final[tuple] = ("OpenAI Codex", "Codex")
PUFFIN_NAME: Final[str] = "Puffin"

# The export and its Cargo output, kept apart from the product build's `src/` and `target/`.
TEST_SOURCE_DIR: Final[str] = os.path.join(BUILD_CACHE_DIR, "test-src")
TEST_TARGET_DIR: Final[str] = os.path.join(BUILD_CACHE_DIR, "test-target")
# The HOME the tests see, emptied before each run: Codex's tests read `~/.codex`, `~/.config` and
# git's global config, and must see neither the user's nor Puffin's.
TEST_HOME_DIR: Final[str] = os.path.join(BUILD_CACHE_DIR, "test-home")
TOOLS_DIR: Final[str] = os.path.join(BUILD_CACHE_DIR, "tools")

# The launcher crate, copied into the export by CodexBrandedBuilder.
LAUNCHER_PACKAGE: Final[str] = "puffin-launcher"

# Upstream's own test profile: the test profile at opt-level 0, which its CI uses to keep test
# binaries small. 20,000 tests across ~280 binaries.
CARGO_PROFILE: Final[str] = "ci-test"

# The nextest release upstream's CI installs (`.github/workflows/rust-ci-full-nextest-platform.yml`),
# pinned by the checksum its release publishes.
NEXTEST_VERSION: Final[str] = "0.9.103"
NEXTEST_URL: Final[str] = (
    "https://github.com/nextest-rs/nextest/releases/download/cargo-nextest-{version}/"
    "cargo-nextest-{version}-{target}.tar.gz"
)
NEXTEST_SHA256: Final[dict] = {
    "aarch64-unknown-linux-gnu": "caf1cbf376a485a30795d08dad21f1d9d2baddc6c102d24b60432e7ae7d9d7a4",
}

# A closed local port. The launcher steps aside under PUFFIN_UPSTREAM_TESTS, so no test should look
# for a model server at all; this makes one that does fail here rather than reach the server
# running on this machine.
CLOSED_MODEL_HOST: Final[str] = "http://127.0.0.1:9"

# Memory the whole run may use. Test binaries link at up to ~4 GB each and eight tests run at once,
# beside a resident model; the cap turns the worst case into an OOM kill of the run, not the host.
DEFAULT_MEMORY_MAX: Final[str] = "24G"

# Files that make a directory a project root to Codex. Found on 2026-10-01 in /tmp itself, left by
# a test run, after which a skills test saw /tmp as the project above its own temporary directory
# and failed: every test temp directory is below it.
PROJECT_MARKERS: Final[tuple] = (".git", ".agents", ".codex")

# Disk the test build needs, in GiB, measured with the debug-free profile below. A run refuses to
# start without it, less what an earlier run's target directory already holds: it is all output
# that Cargo reuses or replaces.
TEST_BUILD_DISK_GIB: Final[int] = 40
DEFAULT_BUILD_JOBS: Final[int] = 6
DEFAULT_TEST_THREADS: Final[int] = 8


class CodexTestRunner:
    """Runs Codex's test suite on Puffin's patched tree, minus the tests that do not apply to it."""

    @classmethod
    def load_skips(cls, path: str = SKIP_FILE) -> dict:
        """
        Reads the skip list.

        Args:
            path (str): The TOML file to read.

        Returns:
            dict: `package`, `target` and `test` lists, each entry carrying its `reason`.

        Raises:
            ValueError: If an entry lacks its key or its reason, so no test is skipped silently.
        """
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        required = {"package": ("name",), "target": ("package", "test"), "test": ("filter",)}
        skips = {}
        for kind, keys in required.items():
            entries = data.get(kind, [])
            for entry in entries:
                for key in keys + ("reason",):
                    if not str(entry.get(key, "")).strip():
                        raise ValueError(f"{path}: a [[{kind}]] entry has no {key}: {entry}")
            skips[kind] = entries
        return skips

    @classmethod
    def filterset(cls, skips: dict, user_filter: Optional[str] = None) -> Optional[str]:
        """
        Builds the nextest filterset that leaves out the skipped tests.

        Args:
            skips (dict): What `load_skips` returned.
            user_filter (Optional[str]): A filterset the caller wants to narrow the run to.

        Returns:
            Optional[str]: The expression for `-E`, or None if nothing narrows the run.
        """
        # A filter may span lines in the TOML, one test to a line; nextest wants one line.
        terms = [f"({' '.join(entry['filter'].split())})" for entry in skips.get("test", [])]
        parts = []
        if user_filter:
            parts.append(f"({user_filter})")
        if terms:
            parts.append(f"not ({' | '.join(terms)})")
        return " & ".join(parts) if parts else None

    @classmethod
    def ensure_nextest(cls) -> Optional[str]:
        """
        Installs the pinned cargo-nextest into the builder's cache, verified against its checksum.

        Returns:
            Optional[str]: The directory holding `cargo-nextest`, or None if it could not be installed.
        """
        import tarfile
        import urllib.request

        binary = os.path.join(TOOLS_DIR, "cargo-nextest")
        stamp = os.path.join(TOOLS_DIR, "cargo-nextest.version")
        if os.path.isfile(binary) and os.path.isfile(stamp):
            with open(stamp) as handle:
                if handle.read().strip() == NEXTEST_VERSION:
                    return TOOLS_DIR

        target = CodexBrandedBuilder.host_target()
        expected = NEXTEST_SHA256.get(target)
        if expected is None:
            print(f"❌ No pinned cargo-nextest {NEXTEST_VERSION} checksum for {target}.")
            return None
        os.makedirs(TOOLS_DIR, exist_ok=True)
        archive = os.path.join(TOOLS_DIR, f"cargo-nextest-{NEXTEST_VERSION}.tar.gz")
        print(f"📥 Fetching cargo-nextest {NEXTEST_VERSION}...")
        try:
            urllib.request.urlretrieve(NEXTEST_URL.format(version=NEXTEST_VERSION, target=target), archive)
        except OSError as exc:
            print(f"❌ Could not download cargo-nextest: {exc}")
            return None
        digest = hashlib.sha256()
        with open(archive, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            os.remove(archive)
            print("❌ cargo-nextest failed its checksum; not using it.")
            return None
        with tarfile.open(archive) as tar:
            member = tar.getmember("cargo-nextest")
            with tar.extractfile(member) as source, open(binary, "wb") as out:
                shutil.copyfileobj(source, out)
        os.chmod(binary, 0o755)
        os.remove(archive)
        with open(stamp, "w") as handle:
            handle.write(f"{NEXTEST_VERSION}\n")
        return TOOLS_DIR

    @classmethod
    def reset_workspace_version(cls, source_dir: str) -> None:
        """
        Puts the exported workspace back at version 0.0.0, the version its tests are written for.

        Upstream's tests run on `main`, where every workspace crate is 0.0.0; its release job bumps
        the version just before building, so the release tag Puffin pins says 0.158.0 and ~30 TUI
        snapshots ("OpenAI Codex (v0.0.0)", "Update available! 0.0.0 -> 9.9.9") fail on an
        unmodified checkout of it. Only the test export is changed; `puffin` keeps its version.

        Args:
            source_dir (str): The exported tree.
        """
        manifest = os.path.join(source_dir, "codex-rs", "Cargo.toml")
        with open(manifest) as handle:
            text = handle.read()
        text = re.sub(r'(\[workspace\.package\]\s*\nversion = )"[^"]+"', r'\1"0.0.0"', text, count=1)
        with open(manifest, "w") as handle:
            handle.write(text)

    @classmethod
    def apply_test_overlay(cls, source_dir: str) -> bool:
        """
        Gives the test export Puffin's expectations for the renamed TUI.

        Copies `codex-tests/snapshots/` over the export's `codex-rs/` and applies the test-only
        diffs in `codex-tests/patches/`, after the product patches. Only test files change.

        Args:
            source_dir (str): The exported, patched tree.

        Returns:
            bool: True if every overlay file had an original to replace and every diff applied.
        """
        workspace_dir = os.path.join(source_dir, "codex-rs")
        if os.path.isdir(SNAPSHOT_OVERLAY_DIR):
            for root, _, files in os.walk(SNAPSHOT_OVERLAY_DIR):
                for name in files:
                    relative = os.path.relpath(os.path.join(root, name), SNAPSHOT_OVERLAY_DIR)
                    target = os.path.join(workspace_dir, relative)
                    if not os.path.isfile(target):
                        # The test it belonged to was renamed or removed upstream.
                        print(f"❌ codex-tests/snapshots/{relative} replaces no upstream snapshot.")
                        print("💡 Regenerate the overlay with `puffin-admin codex test --accept-snapshots`.")
                        return False
                    shutil.copyfile(os.path.join(root, name), target)
        patches = sorted(
            os.path.join(TEST_PATCHES_DIR, name) for name in os.listdir(TEST_PATCHES_DIR)
            if name.endswith(".patch")
        ) if os.path.isdir(TEST_PATCHES_DIR) else []
        for patch in patches:
            if subprocess.run(["git", "apply", "--check", patch], cwd=source_dir, check=False).returncode != 0:
                print(f"❌ codex-tests/patches/{os.path.basename(patch)} does not apply to the test export.")
                return False
            subprocess.run(["git", "apply", patch], cwd=source_dir, check=True)
        return True

    @classmethod
    def snapshot_hashes(cls, workspace_dir: str) -> dict:
        """
        Hashes every insta snapshot file in the export.

        Args:
            workspace_dir (str): The exported `codex-rs` directory.

        Returns:
            dict: Path relative to `codex-rs` → SHA-256 of its content.
        """
        hashes = {}
        for root, dirs, files in os.walk(workspace_dir):
            dirs[:] = [d for d in dirs if d != "target"]
            for name in files:
                if name.endswith(".snap"):
                    path = os.path.join(root, name)
                    with open(path, "rb") as handle:
                        hashes[os.path.relpath(path, workspace_dir)] = hashlib.sha256(handle.read()).hexdigest()
        return hashes

    @classmethod
    def differs_only_by_name(cls, upstream: str, puffin: str) -> bool:
        """
        Tells whether a snapshot differs from upstream's by the on-screen name and nothing else.

        Both are reduced the same way: each name to one token, then whitespace and the box
        rules dropped, because the shorter name leaves padding behind and can shift a wrap.
        Any other change (a word, a symbol, a row of content) survives the reduction.

        Args:
            upstream (str): Upstream's snapshot.
            puffin (str): The snapshot the patched tree produced.

        Returns:
            bool: True if the two reduce to the same text.
        """
        def reduce(text: str) -> str:
            # insta's header (`source:`, `expression:`) is left as it is; only the body matters.
            body = text.split("\n---\n", 1)[-1]
            for name in UPSTREAM_NAMES + (PUFFIN_NAME,):
                body = body.replace(name, "\x00")
            return re.sub(r"[\s│─]+", "", body)

        return reduce(upstream) == reduce(puffin)

    @classmethod
    def accept_snapshots(cls, before: dict) -> int:
        """
        Copies the snapshots a run rewrote into `codex-tests/snapshots/`, if only the name changed.

        Args:
            before (dict): `snapshot_hashes` of the export before the run.

        Returns:
            int: 0 if every rewritten snapshot was accepted, 1 if any needs a person to look at it.
        """
        workspace_dir = os.path.join(TEST_SOURCE_DIR, "codex-rs")
        commit = CodexBrandedBuilder.source_commit()
        after = cls.snapshot_hashes(workspace_dir)
        changed = sorted(path for path, digest in after.items() if before.get(path) != digest)
        refused = []
        for relative in changed:
            with open(os.path.join(workspace_dir, relative)) as handle:
                puffin = handle.read()
            upstream = subprocess.run(
                ["git", "-C", os.path.join(REPO_ROOT, "codex"), "show", f"{commit}:codex-rs/{relative}"],
                capture_output=True, text=True, check=False,
            )
            if upstream.returncode != 0 or not cls.differs_only_by_name(upstream.stdout, puffin):
                refused.append(relative)
                continue
            target = os.path.join(SNAPSHOT_OVERLAY_DIR, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w") as handle:
                handle.write(puffin)
        pending = [os.path.relpath(os.path.join(root, name), workspace_dir)
                   for root, dirs, files in os.walk(workspace_dir)
                   for name in files if name.endswith(".pending-snap")]
        print(f"📸 {len(changed) - len(refused)} snapshot(s) accepted into codex-tests/snapshots/.")
        for relative in refused:
            print(f"⚠️ Not accepted, differs from upstream by more than the name: {relative}")
        for relative in pending:
            print(f"⚠️ Inline snapshot changed; edit it in codex-tests/patches/: {relative}")
        return 1 if refused or pending else 0

    @classmethod
    def has_disk_space(cls) -> bool:
        """
        Checks there is room for the test build before starting it.

        A full disk takes down more than this run: the model server, the web chat's database and
        any other build on the machine fail with it.

        Returns:
            bool: True if the build fits.
        """
        def size_gib(path: str) -> float:
            total = 0
            for root, _, files in os.walk(path):
                for name in files:
                    try:
                        total += os.lstat(os.path.join(root, name)).st_size
                    except OSError:
                        pass
            return total / 2**30

        free = shutil.disk_usage(BUILD_CACHE_DIR).free / 2**30
        needed = max(0.0, TEST_BUILD_DISK_GIB - size_gib(TEST_TARGET_DIR))
        if free < needed:
            print(f"❌ Codex's test build needs about {needed:.0f} GiB more disk; {free:.0f} GiB is free.")
            print(f"💡 Free some space, or remove an old test build: {TEST_TARGET_DIR}")
            return False
        return True

    @classmethod
    def stray_project_markers(cls) -> List[str]:
        """
        Lists project markers in the system temporary directory, above every test's own.

        Returns:
            List[str]: Their paths.
        """
        root = tempfile.gettempdir()
        return [os.path.join(root, name) for name in PROJECT_MARKERS if os.path.lexists(os.path.join(root, name))]

    @classmethod
    def make_temp_dir(cls) -> str:
        """
        Creates the tests' private temporary directory.

        Its name is letters and digits only. The external-agent migration tests encode a project's
        path into a directory name with `-`, `.` and `_` as separators and decode it again, so a
        temporary root containing any of them (`puffin-codex-tests-x_y`) decodes to a different
        path and four of them fail. It is also outside any repository: the skills tests treat an
        ancestor holding `.git` as a project root.

        Its length matters too. A pet-image test base64-encodes the path of a file named
        `frame.png` and asserts the output does not contain `cG5n`, the encoding of `png`, to show
        the image was not sent inline; but the path's own encoding contains `cG5n` whenever `png`
        starts at a multiple of three bytes. `/tmp/pct` plus 11 characters keeps it off one, as
        upstream's `/tmp/.tmpXXXXXX` is; 10 put it on one and failed the test.

        Returns:
            str: The directory, mode 0700.
        """
        import secrets
        import string

        alphabet = string.ascii_lowercase + string.digits
        while True:
            path = os.path.join(tempfile.gettempdir(),
                                "pct" + "".join(secrets.choice(alphabet) for _ in range(11)))
            try:
                os.mkdir(path, 0o700)
                return path
            except FileExistsError:
                continue

    @classmethod
    def environment(cls, rusty_v8: dict, tools_dir: str, jobs: int, temp_dir: str,
                    accept_snapshots: bool = False) -> dict:
        """
        Builds the environment for Cargo and the tests.

        Args:
            rusty_v8 (dict): The V8 variables from `CodexBrandedBuilder.fetch_rusty_v8`.
            tools_dir (str): Where `cargo-nextest` is.
            jobs (int): Parallel compile jobs.
            temp_dir (str): A private, short temporary directory for the tests.
            accept_snapshots (bool): Let insta rewrite snapshot files instead of failing on them.

        Returns:
            dict: The environment.
        """
        environment = DesktopRunner._environment()
        real_home = os.path.expanduser("~")
        # Named before HOME moves, or rustup and Cargo would look for their toolchains in the
        # empty test home.
        environment.setdefault("RUSTUP_HOME", os.path.join(real_home, ".rustup"))
        environment.setdefault("CARGO_HOME", os.path.join(real_home, ".cargo"))
        environment["PATH"] = os.pathsep.join([tools_dir, environment.get("PATH", "")])
        environment.update(rusty_v8)
        linker_dir = cls.lld_dir()
        environment.update({
            "HOME": TEST_HOME_DIR,
            # Private (0700) and short. Tests bind Unix sockets under it, which must stay within
            # SUN_LEN (108 bytes), and refuse a socket directory other users can write, as /tmp is.
            "TMPDIR": temp_dir,
            # Several CLI snapshots record an error's text exactly; with a captured backtrace
            # anyhow appends "Stack backtrace:" to it and they fail. Panics still print theirs.
            "RUST_LIB_BACKTRACE": "0",
            "CARGO_TARGET_DIR": TEST_TARGET_DIR,
            "CARGO_INCREMENTAL": "0",
            "CARGO_BUILD_JOBS": str(jobs),
            # No debug info in the test binaries. With upstream's line tables each of the ~280 is
            # about 1 GB, 88 GB in all, and a second tree (a variant, a stale RUSTFLAGS) doubles it:
            # on 2026-10-01 that filled this machine's disk. Panic messages keep their file and
            # line either way; only backtraces lose them.
            f"CARGO_PROFILE_{CARGO_PROFILE.upper().replace('-', '_')}_DEBUG": "false",
            f"CARGO_PROFILE_{CARGO_PROFILE.upper().replace('-', '_')}_STRIP": "debuginfo",
            # Upstream's `just test` and CI both set this: some tests recurse deeply.
            "RUST_MIN_STACK": "8388608",
            # The launcher leaves the tests' command lines alone (puffin-rs, UPSTREAM_TESTS_ENV).
            "PUFFIN_UPSTREAM_TESTS": "1",
            "DREAMFERENCE_VLLM_HOST": CLOSED_MODEL_HOST,
            # codex-bwrap compiles a vendored bubblewrap that needs libcap's headers. Puffin does
            # not ship it (the sandbox runs the system's /usr/bin/bwrap), and the crate's build
            # script offers this switch for machines without them.
            "CODEX_SKIP_BWRAP_BUILD": "1",
        })
        if accept_snapshots:
            environment.update({"INSTA_UPDATE": "always", "INSTA_FORCE_PASS": "1"})
        else:
            # A developer's INSTA_UPDATE must not turn a failing snapshot into a rewritten one.
            environment.update({"INSTA_UPDATE": "no"})
            environment.pop("INSTA_FORCE_PASS", None)
        if accept_snapshots:
            # insta refuses to write snapshots when it believes it is on CI.
            environment.pop("CI", None)
        environment.pop("RUST_BACKTRACE", None)
        environment.pop("DREAMFERENCE_CONFIG_PATH", None)
        environment.pop("CODEX_HOME", None)
        if linker_dir:
            # GNU ld peaks at ~4 GB linking a test binary, and six links at once overran a 22 GiB
            # cap. The toolchain's own lld needs a fraction of that and nothing installed.
            environment["RUSTFLAGS"] = f"-C link-arg=-fuse-ld=lld -C link-arg=-B{linker_dir}"
        return environment

    @classmethod
    def lld_dir(cls) -> Optional[str]:
        """
        Finds the `gcc-ld` directory of the pinned toolchain, whose `ld.lld` gcc can link with.

        Returns:
            Optional[str]: The directory, or None if this toolchain has none.
        """
        rustup_home = os.environ.get("RUSTUP_HOME", os.path.expanduser("~/.rustup"))
        target = CodexBrandedBuilder.host_target()
        toolchains = os.path.join(rustup_home, "toolchains")
        if not os.path.isdir(toolchains):
            return None
        for name in sorted(os.listdir(toolchains)):
            candidate = os.path.join(toolchains, name, "lib", "rustlib", target, "bin", "gcc-ld")
            if name.startswith("1.95.0") and os.path.isfile(os.path.join(candidate, "ld.lld")):
                return candidate
        return None

    @classmethod
    def test_targets(cls, workspace_dir: str, package: str, environment: dict) -> List[str]:
        """
        Lists a workspace package's integration-test targets.

        Args:
            workspace_dir (str): The exported `codex-rs` directory.
            package (str): The package name.
            environment (dict): The environment Cargo runs in.

        Returns:
            List[str]: The names of its `test` targets.
        """
        metadata = subprocess.run(
            ["cargo", "metadata", "--no-deps", "--format-version", "1"],
            cwd=workspace_dir, env=environment, capture_output=True, text=True, check=True,
        )
        for entry in json.loads(metadata.stdout)["packages"]:
            if entry["name"] == package:
                return [t["name"] for t in entry["targets"] if "test" in t["kind"]]
        raise ValueError(f"no package {package} in the Codex workspace")

    @classmethod
    def commands(cls, skips: dict, workspace_dir: str, environment: dict, user_filter: Optional[str],
                 test_threads: int) -> List[List[str]]:
        """
        Builds the Cargo commands of one run, in order.

        Every workspace binary is built first, because integration tests start other crates'
        binaries (the CLI starts `codex-code-mode-host`, for one) and nextest builds only the
        tested packages' own. A package with a skipped target is tested in a run of its own that
        names its other targets, since Cargo cannot leave one test target out of a workspace build.

        Args:
            skips (dict): What `load_skips` returned.
            workspace_dir (str): The exported `codex-rs` directory.
            environment (dict): The environment Cargo runs in.
            user_filter (Optional[str]): A filterset to narrow the run to.
            test_threads (int): Tests run at once.

        Returns:
            List[List[str]]: The commands.
        """
        excluded = [entry["name"] for entry in skips.get("package", [])]
        skipped_targets: dict = {}
        for entry in skips.get("target", []):
            skipped_targets.setdefault(entry["package"], set()).add(entry["test"])
        filterset = cls.filterset(skips, user_filter)
        nextest = [
            "cargo", "nextest", "run", "--cargo-profile", CARGO_PROFILE, "--no-fail-fast",
            "--test-threads", str(test_threads), "--status-level", "fail",
            "--final-status-level", "fail",
        ]
        if filterset:
            nextest += ["-E", filterset]

        excludes = [arg for name in excluded for arg in ("--exclude", name)]
        commands = [["cargo", "build", "--profile", CARGO_PROFILE, "--workspace", *excludes, "--bins"]]
        commands.append(nextest + ["--workspace", *excludes] +
                        [arg for name in sorted(skipped_targets) for arg in ("--exclude", name)])
        for package in sorted(skipped_targets):
            kept = [name for name in cls.test_targets(workspace_dir, package, environment)
                    if name not in skipped_targets[package]]
            # puffin-launcher is built alongside, never tested here: it is what turns on vendored
            # OpenSSL for the whole build (puffin-rs/Cargo.toml), and Cargo unifies features only
            # across the packages one invocation builds. Without it openssl-sys looks for system
            # headers this machine does not have.
            only_this = cls.filterset(skips, f"package({package})" if not user_filter
                                      else f"package({package}) & ({user_filter})")
            commands.append(nextest[:nextest.index("-E")] if "-E" in nextest else list(nextest))
            commands[-1] += ["-E", only_this, "-p", package, "-p", LAUNCHER_PACKAGE, "--lib", "--bins"]
            commands[-1] += [arg for name in kept for arg in ("--test", name)]
        return commands

    @classmethod
    def scoped(cls, command: List[str], memory_max: str) -> List[str]:
        """
        Wraps a command in a transient systemd scope with a memory cap, when systemd is there.

        Args:
            command (List[str]): The command.
            memory_max (str): The cap, in systemd's syntax.

        Returns:
            List[str]: The command to run.
        """
        if not shutil.which("systemd-run"):
            return command
        wrapped = ["systemd-run", "--user", "--scope", "--quiet",
                   "-p", f"MemoryMax={memory_max}", "-p", "MemorySwapMax=0", "--"]
        # Raises the OOM score so that, if the host runs short anyway, the kernel and earlyoom
        # pick the tests before the model server.
        if shutil.which("choom"):
            wrapped += ["choom", "-n", "1000", "--"]
        return wrapped + command

    @classmethod
    def run(cls, user_filter: Optional[str] = None, test_threads: int = DEFAULT_TEST_THREADS,
            jobs: int = DEFAULT_BUILD_JOBS, memory_max: str = DEFAULT_MEMORY_MAX,
            accept_snapshots: bool = False) -> int:
        """
        Exports and patches Codex, then builds and runs its tests except the skipped ones.

        Args:
            user_filter (Optional[str]): A nextest filterset to narrow the run to.
            test_threads (int): Tests run at once.
            jobs (int): Parallel compile jobs.
            memory_max (str): Memory the whole run may use.
            accept_snapshots (bool): Rewrite the snapshots the run selects and copy the ones that
                differ from upstream by the name alone into `codex-tests/snapshots/`.

        Returns:
            int: 0 if every test that ran passed, non-zero otherwise.
        """
        import fcntl

        if not DesktopInstaller.install_rust():
            return 1
        skips = cls.load_skips()
        os.makedirs(BUILD_CACHE_DIR, exist_ok=True)
        if not cls.has_disk_space():
            return 1
        markers_before = cls.stray_project_markers()
        for marker in markers_before:
            print(f"⚠️ {marker} makes {tempfile.gettempdir()} a project root to Codex's tests; "
                  "the skills tests will fail until it is removed.")
        with open(os.path.join(BUILD_CACHE_DIR, ".test.lock"), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("❌ Another Codex test run is using the test tree.")
                return 1
            if not CodexBrandedBuilder.prepare_source(TEST_SOURCE_DIR):
                return 1
            cls.reset_workspace_version(TEST_SOURCE_DIR)
            if not cls.apply_test_overlay(TEST_SOURCE_DIR):
                return 1
            rusty_v8 = CodexBrandedBuilder.fetch_rusty_v8(TEST_SOURCE_DIR)
            tools_dir = cls.ensure_nextest()
            if rusty_v8 is None or tools_dir is None:
                return 1
            shutil.rmtree(TEST_HOME_DIR, ignore_errors=True)
            os.makedirs(TEST_HOME_DIR)
            temp_dir = cls.make_temp_dir()
            try:
                before = cls.snapshot_hashes(os.path.join(TEST_SOURCE_DIR, "codex-rs"))
                status = cls._run_commands(skips, rusty_v8, tools_dir, jobs, temp_dir, user_filter,
                                           test_threads, memory_max, accept_snapshots)
                status = cls.accept_snapshots(before) or status if accept_snapshots else status
                for marker in sorted(set(cls.stray_project_markers()) - set(markers_before)):
                    print(f"⚠️ A test left {marker} behind; remove it before the next run.")
                return status
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    @classmethod
    def _run_commands(cls, skips: dict, rusty_v8: dict, tools_dir: str, jobs: int, temp_dir: str,
                      user_filter: Optional[str], test_threads: int, memory_max: str,
                      accept_snapshots: bool = False) -> int:
        """
        Runs one test run's commands; `run()` holds the lock and owns the directories.

        Returns:
            int: 0 if every test that ran passed, non-zero otherwise.
        """
        environment = cls.environment(rusty_v8, tools_dir, jobs, temp_dir, accept_snapshots)
        workspace_dir = os.path.join(TEST_SOURCE_DIR, "codex-rs")
        skipped = sum(len(skips[kind]) for kind in skips)
        print(f"🧪 Running Codex's tests on Puffin's tree ({skipped} skip-list entries, "
              f"see {os.path.relpath(SKIP_FILE, REPO_ROOT)})...")
        status = 0
        # Upstream's CI runs under umask 022. Under 002, common on desktop Linux, every temporary
        # directory a test creates is group-writable, and the IDE-context tests rightly refuse a
        # socket in one ("socket directory is writable by other users").
        previous_umask = os.umask(0o022)
        try:
            for command in cls.commands(skips, workspace_dir, environment, user_filter, test_threads):
                code = subprocess.call(cls.scoped(command, memory_max), cwd=workspace_dir, env=environment)
                if command[1] == "build" and code != 0:
                    print("❌ Building Codex's binaries failed; see the cargo output above.")
                    return code
                status = status or code
        finally:
            os.umask(previous_umask)
        print("✅ Every Codex test that applies to Puffin passed." if status == 0
              else "❌ Some Codex tests failed; see the summary above.")
        return status
