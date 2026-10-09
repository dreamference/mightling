"""
Toolchain Verification for the Mightling Desktop App.

This module provides the DesktopInstaller class, which reports whether this machine can build the
Electron app in `desktop/electron` (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md) and installs
the parts it is allowed to install.

The app is Electron, so its build needs Node and npm and nothing else: Electron itself comes from
npm as a prebuilt binary, and Work's UI and the main process are built with Vite. There are no
system headers to install, because the engine is bundled rather than linked from the platform.

One thing the *running* app needs that a build cannot give it: on Ubuntu 24.04 Chromium's sandbox
creates a user namespace, which the kernel refuses to programs without an AppArmor profile
(`kernel.apparmor_restrict_unprivileged_userns=1`). The `.deb` writes that profile in its
`postinst`; for a checkout's build and dev run, `install_apparmor_profile()` writes one covering
the binaries here, with sudo, and the escalation is deliberately *visible*: the command is
printed before it runs and sudo prompts on the terminal.

The Rust helpers (`has_rust`, `cargo_path`, `install_rust`) stay here because the Codex build
(`CodexBrandedBuilder`, `CodexTestRunner`) drives rustup through them; the desktop app no longer
needs Rust.
"""

import os
import shutil
import subprocess
from typing import Final, List, Optional, Tuple

# Where rustup puts the toolchain, and the installer that puts it there (used by the Codex build).
CARGO_BIN: Final[str] = os.path.expanduser("~/.cargo/bin")
RUSTUP_URL: Final[str] = "https://sh.rustup.rs"

# The oldest Node the build accepts: Electron Forge 8 declares `node >= 22.13.0` (its packages are
# ESM); the release workflows build with the newest 22.
MIN_NODE_VERSION: Final[Tuple[int, int]] = (22, 13)

# Where a checkout's AppArmor profile goes, and its name.
APPARMOR_PROFILE_PATH: Final[str] = "/etc/apparmor.d/mightling-desktop-dev"
APPARMOR_PROFILE_NAME: Final[str] = "mightling-desktop-dev"


class DesktopInstaller:
    """
    Verifies and provisions what the Mightling desktop app is built and run with.
    """

    @classmethod
    def missing_prerequisites(cls, build: bool = False) -> List[str]:
        """
        Lists the prerequisites that are absent, in the order they should be installed.

        Args:
            build (bool): Whether the `.deb` is to be made, which Forge's maker does with `dpkg`
                and `fakeroot`; a dev run needs neither.

        Returns:
            List[str]: Short identifiers -- `"node"`, `"npm"`, `"fakeroot"` -- for whatever is
                missing. An empty list means the app can be built.
        """
        missing: List[str] = []
        version = cls.node_version()
        if version is None or version < MIN_NODE_VERSION:
            missing.append("node")
        if shutil.which("npm") is None:
            missing.append("npm")
        if build and (shutil.which("fakeroot") is None or shutil.which("dpkg") is None):
            missing.append("fakeroot")
        return missing

    @classmethod
    def node_version(cls) -> Optional[Tuple[int, int]]:
        """
        The major and minor version of the `node` on PATH.

        Returns:
            Optional[Tuple[int, int]]: The version, or None when there is no node or its answer
                cannot be read.
        """
        node = shutil.which("node")
        if node is None:
            return None
        try:
            result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        parts = result.stdout.strip().lstrip("v").split(".")
        try:
            return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            return None

    # -- Rust, for the Codex build -----------------------------------------------------------------

    @classmethod
    def has_rust(cls) -> bool:
        """
        Reports whether a Rust toolchain is on PATH or in the standard rustup location.

        Returns:
            bool: True if `cargo` can be found.
        """
        return cls.cargo_path() is not None

    @classmethod
    def cargo_path(cls) -> Optional[str]:
        """
        Locates the `cargo` binary.

        Checks `~/.cargo/bin` as well as PATH, because a rustup installed in this same session is
        on disk but not yet on the PATH of the shell that installed it.

        Returns:
            Optional[str]: Absolute path to cargo, or None.
        """
        candidate = os.path.join(CARGO_BIN, "cargo")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        return shutil.which("cargo")

    @classmethod
    def install_rust(cls) -> bool:
        """
        Installs a Rust toolchain with rustup, unattended.

        Returns:
            bool: True if cargo is present afterwards.
        """
        if cls.has_rust():
            return True
        if not shutil.which("curl"):
            print("❌ curl is needed to fetch rustup.")
            return False

        print("📦 Installing the Rust toolchain with rustup (this downloads a few hundred MB)...")
        try:
            result = subprocess.run(
                f"curl --proto '=https' --tlsv1.2 -sSf {RUSTUP_URL} | sh -s -- -y --no-modify-path",
                shell=True, timeout=1800, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"❌ rustup failed: {exc}")
            return False
        if result.returncode != 0:
            print("❌ rustup exited non-zero.")
            return False
        return cls.has_rust()

    # -- the sandbox's user namespace ----------------------------------------------------------------

    @classmethod
    def userns_allowed(cls) -> bool:
        """
        Reports whether a program started from this shell may create a user namespace, which is
        what Chromium's sandbox does first. Under a terminal with a permissive AppArmor profile
        (PyCharm's) it may; from a plain terminal or the launcher it may not, until a profile
        names the binary.

        Returns:
            bool: True if `unshare -U true` succeeds.
        """
        if shutil.which("unshare") is None:
            return True
        try:
            # `aa-exec -p unconfined` asks as an unconfined program would, which is how the
            # launcher starts the app; without aa-exec, this shell's own confinement answers.
            command = ["aa-exec", "-p", "unconfined", "--", "unshare", "-U", "true"] if shutil.which("aa-exec") else ["unshare", "-U", "true"]
            return subprocess.run(command, capture_output=True, timeout=10, check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return True

    @classmethod
    def apparmor_profile(cls, binaries: List[str]) -> str:
        """
        The AppArmor profile text that grants `userns` to the given binaries: the shape of the
        `.deb`'s profile (desktop/electron/linux/postinst) and of Ubuntu's own sandbox profiles.

        Args:
            binaries (List[str]): Absolute paths of the Electron binaries a checkout runs.

        Returns:
            str: The profile.
        """
        lines = [
            "# Written by `ling-admin desktop install` (Mightling by Dreamference). It lets the desktop",
            "# app's Chromium create the user namespace its sandbox needs, which Ubuntu otherwise refuses",
            "# to programs without a profile (kernel.apparmor_restrict_unprivileged_userns=1).",
            "abi <abi/4.0>,",
            "include <tunables/global>",
            "",
        ]
        for index, binary in enumerate(binaries):
            name = APPARMOR_PROFILE_NAME if index == 0 else f"{APPARMOR_PROFILE_NAME}-{index}"
            lines += [f'profile {name} "{binary}" flags=(unconfined) {{', "  userns,", "}", ""]
        return "\n".join(lines)

    @classmethod
    def install_apparmor_profile(cls, binaries: List[str]) -> bool:
        """
        Writes and loads the profile with sudo, printing the command first.

        Args:
            binaries (List[str]): The binaries to cover.

        Returns:
            bool: True once a user namespace can be created, or when nothing needed doing.
        """
        binaries = [binary for binary in binaries if os.path.isfile(binary)]
        if not binaries:
            return cls.userns_allowed()
        if not shutil.which("sudo") or not shutil.which("apparmor_parser"):
            print("⚠️  Chromium's sandbox needs a user namespace, and this machine refuses one without an")
            print("   AppArmor profile; sudo or apparmor_parser is missing, so write it yourself:")
            print(cls.apparmor_profile(binaries))
            return False
        print("📦 Installing the AppArmor profile the desktop app's sandbox needs (sudo will ask for your password):")
        print(f"   sudo tee {APPARMOR_PROFILE_PATH} && sudo apparmor_parser -r -T -W {APPARMOR_PROFILE_PATH}")
        try:
            written = subprocess.run(["sudo", "tee", APPARMOR_PROFILE_PATH], input=cls.apparmor_profile(binaries),
                                     text=True, capture_output=True, timeout=300, check=False)
            if written.returncode != 0:
                print("❌ The profile was not written.")
                return False
            loaded = subprocess.run(["sudo", "apparmor_parser", "-r", "-T", "-W", APPARMOR_PROFILE_PATH],
                                    timeout=300, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"❌ Could not install the profile: {exc}")
            return False
        return loaded.returncode == 0

    @classmethod
    def report(cls, build: bool = False) -> Tuple[bool, List[str]]:
        """
        Prints what is missing and how to get it.

        Args:
            build (bool): Whether the `.deb` is to be made (see `missing_prerequisites`).

        Returns:
            Tuple[bool, List[str]]: Whether the toolchain is complete, and what is missing.
        """
        missing = cls.missing_prerequisites(build)
        if not missing:
            print("✅ The desktop build toolchain is complete (Node and npm" + (", dpkg and fakeroot)." if build else ")."))
            return True, missing

        print("⚠️  The Mightling desktop toolchain is incomplete:")
        if "node" in missing:
            print(f"   • Node.js {MIN_NODE_VERSION[0]}.{MIN_NODE_VERSION[1]} or later is needed (Electron, Vite and Forge run on it).")
        if "npm" in missing:
            print("   • npm is needed to install the app's packages.")
        if "fakeroot" in missing:
            print("   • dpkg and fakeroot are needed to make the .deb: sudo apt install fakeroot")
        if "node" in missing or "npm" in missing:
            print("💡 Install Node.js from your distribution or nodejs.org, then `ling-admin desktop install`.")
        return False, missing
