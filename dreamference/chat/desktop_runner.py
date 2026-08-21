"""
Puffin Desktop App Runner.

This module provides the DesktopRunner class, which builds and launches the Tauri shell in
`desktop/`.

The shell is deliberately thin: its window points straight at the Onyx deployment on this machine,
so there is no bundled frontend to keep in step with the browser. The desktop app and the browser
render the same server, which means every patch `dream onyx configure` applies -- the typography,
the white canvas, the hidden chrome -- shows up in both without being ported. What the desktop app
adds is a window of its own: its own launcher entry and icon, no address bar, and no tab that gets
lost among thirty others.

It follows the same shape as the agent runners: check the service is healthy, provision the tooling
if it is missing, then hand off to a subprocess. The health check is the one that matters -- a
window opened against a stopped Onyx shows a connection error with no hint of what to start, so it
is checked first and the user is told to run `dream onyx start` instead.
"""

import os
import subprocess
import urllib.error
import urllib.request
from typing import Final, List, Optional

from dreamference.chat.desktop_installer import CARGO_BIN, DesktopInstaller
from dreamference.chat.onyx_runner import DEFAULT_ONYX_WEB_URL

# The Tauri project lives beside the Python package rather than inside it: it is a Rust crate with
# its own build system, and `pip install -e .` has no business copying it around.
DESKTOP_PROJECT_DIR: Final[str] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "desktop"
)

# How long to wait for Onyx to answer before deciding it is not running.
HEALTH_TIMEOUT_SECONDS: Final[int] = 5



class DesktopRunner:
    """
    Builds and runs the Puffin desktop window.
    """

    @classmethod
    def onyx_is_up(cls, web_url: str = DEFAULT_ONYX_WEB_URL) -> bool:
        """
        Reports whether the Onyx deployment is answering.

        Args:
            web_url (str): Base URL of the Onyx web UI.

        Returns:
            bool: True if the server responded.
        """
        try:
            with urllib.request.urlopen(
                f"{web_url.rstrip('/')}/api/health", timeout=HEALTH_TIMEOUT_SECONDS
            ) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    @classmethod
    def _tauri_command(cls, *args: str) -> Optional[List[str]]:
        """
        Builds the argument vector that invokes the Tauri CLI.

        Args:
            *args: Tauri subcommand and flags.

        Returns:
            Optional[List[str]]: The command, or None if no CLI is available.
        """
        import shutil

        if shutil.which("cargo-tauri"):
            return ["cargo-tauri", *args]
        if shutil.which("npx"):
            return ["npx", "--no-install", "tauri", *args]
        return None

    @classmethod
    def _environment(cls) -> dict:
        """
        Returns the environment the Tauri CLI and the built binary run with.

        A rustup installed during this same run is on disk but absent from the inherited PATH, so a
        build straight after a successful install would fail to find cargo.

        The webview's own environment -- the DMABUF renderer and the GTK theme -- is deliberately
        *not* set here. It lives in `src-tauri/src/main.rs` instead, because a `.desktop` entry, an
        AppImage's AppRun or someone running the binary directly all bypass this launcher, and both
        settings are load-bearing: without them the window either never appears or comes up in dark
        mode showing none of Dreamference's styling.

        Returns:
            dict: A copy of the current environment with `~/.cargo/bin` on PATH.
        """
        environment = dict(os.environ)
        if os.path.isdir(CARGO_BIN) and CARGO_BIN not in environment.get("PATH", ""):
            environment["PATH"] = f"{CARGO_BIN}{os.pathsep}{environment.get('PATH', '')}"
        return environment

    @classmethod
    def _ensure_toolchain(cls) -> bool:
        """
        Installs what can be installed without root, and reports what cannot.

        Returns:
            bool: True if the toolchain is complete afterwards.
        """
        # System packages first: they are the step that needs a password, and asking for it after
        # a several-hundred-megabyte Rust download would be a poor order to fail in.
        if not DesktopInstaller.install_system_packages():
            return False
        if not DesktopInstaller.install_rust():
            return False
        if not DesktopInstaller.install_tauri_cli():
            return False
        return True

    @classmethod
    def run(cls, web_url: str = DEFAULT_ONYX_WEB_URL) -> int:
        """
        Opens the Puffin desktop window, building it first if necessary.

        Args:
            web_url (str): Base URL of the Onyx web UI the window points at.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        if not cls.onyx_is_up(web_url):
            print(f"❌ Puffin is not answering at {web_url}.")
            print("💡 Start it first: dream onyx start")
            return 1
        if not cls._ensure_toolchain():
            return 1

        command = cls._tauri_command("dev", "--no-watch")
        if command is None:
            print("❌ No Tauri CLI available.")
            return 1

        print("🚀 Opening the Puffin desktop window...")
        return subprocess.call(command, cwd=DESKTOP_PROJECT_DIR, env=cls._environment())

    @classmethod
    def build(cls) -> int:
        """
        Builds a distributable desktop bundle.

        Unlike `run()`, this does not require Onyx to be up: the shell holds a URL, not a copy of
        the UI, so there is nothing to fetch at build time.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        if not cls._ensure_toolchain():
            return 1

        command = cls._tauri_command("build")
        if command is None:
            print("❌ No Tauri CLI available.")
            return 1

        print("🔨 Building the Puffin desktop bundle (the first Rust build takes a while)...")
        code = subprocess.call(command, cwd=DESKTOP_PROJECT_DIR, env=cls._environment())
        if code == 0:
            bundle = os.path.join(DESKTOP_PROJECT_DIR, "src-tauri", "target", "release", "bundle")
            print(f"✅ Bundles written to {bundle}")
        return code

    @classmethod
    def install(cls) -> int:
        """
        Installs everything the desktop app is built from, without building it.

        Returns:
            int: 0 if the toolchain is complete afterwards.
        """
        return 0 if cls._ensure_toolchain() else 1

    @classmethod
    def status(cls, web_url: str = DEFAULT_ONYX_WEB_URL) -> int:
        """
        Reports whether the desktop app can be built and whether Onyx is up.

        Args:
            web_url (str): Base URL of the Onyx web UI.

        Returns:
            int: 0 if the app could be launched right now, 1 otherwise.
        """
        serving = cls.onyx_is_up(web_url)
        print(f"{'✅' if serving else '❌'} Puffin server at {web_url}"
              f"{'' if serving else ' — start it with: dream onyx start'}")
        complete, _ = DesktopInstaller.report()
        return 0 if serving and complete else 1
