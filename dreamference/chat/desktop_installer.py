"""
Toolchain Verification for the Puffin Desktop App.

This module provides the DesktopInstaller class, which reports whether this machine can build the
Tauri shell in `desktop/` and installs the parts it is allowed to install.

Tauri compiles a native binary, so its prerequisites are in three groups, and they differ in who
can install them:

* **Rust** -- installed per user under `~/.cargo`, so `rustup` can be driven from here and undone
  with `rustup self uninstall`.
* **The Tauri CLI** -- taken from npm rather than `cargo install tauri-cli`, because npm ships a
  prebuilt binary for this architecture while cargo would compile it, which is minutes of build for
  a tool that is not the product.
* **The GTK and WebKit development headers** -- system packages, so installing them needs root.
  `install_system_packages()` runs `sudo apt-get install`, and the escalation is deliberately
  *visible*: the command is printed before it runs and sudo prompts on the terminal, so nothing
  happens to the machine without the operator typing a password. Where sudo cannot prompt -- a
  non-interactive shell, no tty -- it falls back to printing the line rather than failing obscurely.

The webview itself is *not* bundled. Tauri renders through the platform webview, which is already
present here as `libwebkit2gtk-4.1` -- the `-dev` package supplies only the headers needed to link
against it.
"""

import os
import shutil
import subprocess
from typing import Final, List, Optional, Tuple

# The Debian/Ubuntu packages Tauri v2 needs to link. `webkit2gtk-4.1` is the v2 series; v1 used 4.0,
# and installing the wrong one produces a linker error that reads like a missing library.
LINUX_BUILD_PACKAGES: Final[List[str]] = [
    "libwebkit2gtk-4.1-dev",
    "libgtk-3-dev",
    "librsvg2-dev",
    "libayatana-appindicator3-dev",
    "build-essential",
    "pkg-config",
]

# Where rustup puts the toolchain, and the installer that puts it there.
CARGO_BIN: Final[str] = os.path.expanduser("~/.cargo/bin")
RUSTUP_URL: Final[str] = "https://sh.rustup.rs"

# The npm package that carries the prebuilt Tauri CLI.
TAURI_CLI_PACKAGE: Final[str] = "@tauri-apps/cli@^2"


class DesktopInstaller:
    """
    Verifies and provisions the toolchain the Puffin desktop app is built with.
    """

    @classmethod
    def missing_prerequisites(cls) -> List[str]:
        """
        Lists the prerequisites that are absent, in the order they should be installed.

        Returns:
            List[str]: Short identifiers -- `"headers"`, `"rust"`, `"tauri-cli"` -- for whatever is
                missing. An empty list means the app can be built.
        """
        missing: List[str] = []
        if not cls.has_webview_headers():
            missing.append("headers")
        if not cls.has_rust():
            missing.append("rust")
        if not cls.has_tauri_cli():
            missing.append("tauri-cli")
        return missing

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
    def has_tauri_cli(cls) -> bool:
        """
        Reports whether the Tauri CLI can be run.

        Returns:
            bool: True if a Tauri CLI is available through npm or on PATH.
        """
        if shutil.which("cargo-tauri"):
            return True
        if not shutil.which("npx"):
            return False
        result = subprocess.run(
            ["npx", "--no-install", "tauri", "--version"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        return result.returncode == 0

    @classmethod
    def has_webview_headers(cls) -> bool:
        """
        Reports whether the WebKitGTK development headers are installed.

        Returns:
            bool: True if pkg-config can resolve `webkit2gtk-4.1`.
        """
        if not shutil.which("pkg-config"):
            return False
        result = subprocess.run(
            ["pkg-config", "--exists", "webkit2gtk-4.1"],
            capture_output=True, timeout=30, check=False,
        )
        return result.returncode == 0

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

    @classmethod
    def install_tauri_cli(cls) -> bool:
        """
        Installs the prebuilt Tauri CLI from npm.

        Returns:
            bool: True if the CLI can be run afterwards.
        """
        if cls.has_tauri_cli():
            return True
        if not shutil.which("npm"):
            print("❌ npm is needed to install the Tauri CLI.")
            print(f"💡 Or install it with cargo: cargo install tauri-cli --version '^2'")
            return False

        print(f"📦 Installing the Tauri CLI ({TAURI_CLI_PACKAGE})...")
        result = subprocess.run(
            ["npm", "install", "--global", TAURI_CLI_PACKAGE],
            capture_output=True, text=True, timeout=900, check=False,
        )
        if result.returncode != 0:
            print(f"❌ npm install failed: {result.stderr.strip()[:200]}")
            return False
        return cls.has_tauri_cli()

    @classmethod
    def apt_available(cls) -> bool:
        """
        Reports whether this is a Debian-family system with apt.

        Returns:
            bool: True if `apt-get` is on PATH.
        """
        return shutil.which("apt-get") is not None

    @classmethod
    def install_system_packages(cls) -> bool:
        """
        Installs the GTK and WebKit development headers with apt, prompting for sudo.

        Returns:
            bool: True if the headers are present afterwards.
        """
        if cls.has_webview_headers():
            return True
        if not cls.apt_available() or not shutil.which("sudo"):
            print("❌ The WebKitGTK development headers are missing and apt is not available here.")
            print(f"💡 Install the equivalent of: {' '.join(LINUX_BUILD_PACKAGES)}")
            return False

        # Printed before it runs, because this is the one step that changes the machine outside
        # this user's home directory.
        print("📦 Installing the desktop build dependencies (sudo will ask for your password):")
        print(f"   {cls.header_install_command()}")
        try:
            result = subprocess.run(
                ["sudo", "apt-get", "install", "-y", *LINUX_BUILD_PACKAGES],
                timeout=1800, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"❌ apt failed: {exc}")
            return False
        if result.returncode != 0:
            print("❌ apt exited non-zero — the headers were not installed.")
            print(f"💡 Run it yourself, then try again:\n   {cls.header_install_command()}")
            return False
        return cls.has_webview_headers()

    @classmethod
    def header_install_command(cls) -> str:
        """
        Returns the command that installs the system build dependencies.

        Returns:
            str: An `apt install` line, for the operator to run themselves.
        """
        return "sudo apt install -y " + " ".join(LINUX_BUILD_PACKAGES)

    @classmethod
    def report(cls) -> Tuple[bool, List[str]]:
        """
        Prints what is missing and how to get it.

        Returns:
            Tuple[bool, List[str]]: Whether the toolchain is complete, and what is missing.
        """
        missing = cls.missing_prerequisites()
        if not missing:
            print("✅ The desktop build toolchain is complete.")
            return True, missing

        print("⚠️  The Puffin desktop toolchain is incomplete:")
        if "headers" in missing:
            print("   • WebKitGTK/GTK development headers — installed with sudo apt on first build.")
        if "rust" in missing:
            print("   • No Rust toolchain — installed with rustup on first build.")
        if "tauri-cli" in missing:
            print("   • No Tauri CLI — installed from npm on first build.")
        print("💡 `dream desktop install` fetches all of it.")
        return False, missing
