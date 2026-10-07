"""
Mightling Desktop App Runner.

This module provides the DesktopRunner class, which builds and launches the Tauri shell in
`desktop/`.

The shell is deliberately thin: its window points straight at the Onyx deployment on this machine,
so there is no bundled frontend to keep in step with the browser. The desktop app and the browser
render the same server, which means every patch `mling-admin chat configure` applies -- the typography,
the white canvas, the hidden chrome -- shows up in both without being ported. What the desktop app
adds is a window of its own: its own launcher entry and icon, no address bar, and no tab that gets
lost among thirty others.

That is the Chat window. Beside it the app now carries a second, the Work window (the coding agent
on `mling app-server`, specs/DREAMFERENCE_MIGHTLING_DESKTOP.md), whose frontend *is* bundled: it is
built from `desktop/ui` before every Tauri build or dev run (`build_ui`). Chat is unchanged by it.

It follows the same shape as the agent runners: check the service is healthy, provision the tooling
if it is missing, then hand off to a subprocess. The health check is the one that matters -- a
window opened against a stopped Onyx shows a connection error with no hint of what to start, so it
is checked first and the user is told to run `mling-admin chat start` instead.
"""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final, List, Optional

from dreamference.chat.desktop_installer import CARGO_BIN, DesktopInstaller
from dreamference.chat.onyx_runner import DEFAULT_ONYX_WEB_URL

# The Tauri project lives beside the Python package rather than inside it: it is a Rust crate with
# its own build system, and `pip install -e .` has no business copying it around.
DESKTOP_PROJECT_DIR: Final[str] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "desktop"
)

# The Work window's frontend (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §4.5): bundled into the binary
# from `ui/dist`, which `tauri.conf.json` names as `frontendDist`.
UI_DIR: Final[Path] = Path(DESKTOP_PROJECT_DIR) / "ui"

# How long to wait for Onyx to answer before deciding it is not running.
HEALTH_TIMEOUT_SECONDS: Final[int] = 5

# Where a desktop entry and its icon go for the current user. A deb would put these under /usr;
# running from a source checkout, they belong in the XDG user directories instead.
DESKTOP_ENTRY_DIR: Final[str] = os.path.expanduser("~/.local/share/applications")
ICON_DIR: Final[str] = os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps")
DESKTOP_ENTRY_NAME: Final[str] = "mling-app.desktop"
ICON_NAME: Final[str] = "mling-app"

# GNOME matches a running window to its desktop entry by `WM_CLASS`, and shows a generic icon when
# nothing matches -- which is why the app appeared in the dock as an unnamed placeholder. Tao sets
# the class from the binary name, so the window reports instance `mling-app` and class
# `Mightling-app`; naming the file after the instance covers the automatic match and
# `StartupWMClass` covers the explicit one.
WINDOW_CLASS: Final[str] = "Mightling-app"

# Names the desktop binary had before, whose launcher entries and icons are removed on registration
# so the applications grid does not show two of it, one pointing at a binary that no longer builds.
# `puffin-app` is the name it had until the product became Mightling.
LEGACY_ENTRY_NAMES: Final[tuple] = ("puffin-desktop", "puffin-ui", "puffin-app")

# WebKitGTK's HTTP cache, inside the webview's data directory. Onyx serves its stylesheets with
# `immutable` and never changes their filenames, so a patched stylesheet is invisible to anything
# holding a cached copy -- the browser needs a hard refresh, and the app kept showing UI from
# before the last `mling-admin chat configure`. Emptying this on launch costs a few megabytes re-fetched
# over loopback and removes the whole class of bug. The sibling `cookies` file is left alone, which
# is what keeps the session: deleting the data directory wholesale signs the user out.
WEBVIEW_CACHE_DIR_NAME: Final[str] = "WebKitCache"



class DesktopRunner:
    """
    Builds and runs the Mightling desktop window.
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
    def build_ui(cls) -> bool:
        """Builds the Work window's bundle, `desktop/ui/dist`, which Tauri embeds in the binary.

        The Chat window needs none of it: it is the Onyx page at its URL. But Tauri embeds
        `frontendDist` at compile time, so every build and `tauri dev` needs it present. npm's
        packages are installed from the committed lock file the first time.

        Returns:
            bool: True when the bundle was built.
        """
        npm = shutil.which("npm")
        if npm is None:
            print("❌ npm is needed to build the Work window (desktop/ui): install Node.js 20 or later.")
            return False
        if not (UI_DIR / "node_modules").is_dir():
            print("📦 Installing the Work window's packages (desktop/ui)...")
            if subprocess.call([npm, "ci", "--no-audit", "--no-fund"], cwd=UI_DIR, env=cls._environment()) != 0:
                return False
        print("🔨 Building the Work window (desktop/ui)...")
        return subprocess.call([npm, "run", "build"], cwd=UI_DIR, env=cls._environment()) == 0

    @classmethod
    def has_source(cls) -> bool:
        """
        Tells a checkout from a release install, which has the package but no `desktop/` project.

        Returns:
            bool: True if the Tauri project is beside the package, i.e. the app can be built here.
        """
        return os.path.isdir(os.path.join(DESKTOP_PROJECT_DIR, "src-tauri"))

    @classmethod
    def _no_source(cls) -> int:
        """
        Says why the desktop app cannot be built on a machine installed from a release.

        Returns:
            int: 1.
        """
        print("❌ The desktop app is built from the repository's desktop/ project, which a release "
              "install does not have.")
        print("💡 Install the app from the release instead: the Mightling .deb or AppImage on the "
              "release page puts `mling-app` in your launcher.")
        return 1

    @classmethod
    def _run_installed(cls) -> int:
        """
        Opens the desktop app a release's `.deb` installed, where there is no project to run.

        Returns:
            int: The app's exit code, or 1 if it is not installed.
        """
        installed = shutil.which("mling-app")
        if installed is None:
            return cls._no_source()
        print("🚀 Opening the Mightling desktop window...")
        return subprocess.call([installed], env=cls._environment())

    @classmethod
    def run(cls, web_url: str = DEFAULT_ONYX_WEB_URL) -> int:
        """
        Opens the Mightling desktop window, building it first if necessary.

        Args:
            web_url (str): Base URL of the Onyx web UI the window points at.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        if not cls.onyx_is_up(web_url):
            print(f"❌ Mightling is not answering at {web_url}.")
            print("💡 Start it first: mling-admin chat start")
            return 1
        if not cls.has_source():
            return cls._run_installed()
        if not cls._ensure_toolchain() or not cls.build_ui():
            return 1

        # Only once something has been built -- on a first run there is no binary to point an
        # Exec line at yet, and `build()` registers it as soon as there is.
        if cls.binary_path():
            cls.install_desktop_entry()

        # Onyx's stylesheets are served `immutable` under filenames that never change, so the
        # webview would otherwise keep showing the UI as it was before the last configure.
        cls.clear_webview_cache()

        command = cls._tauri_command("dev", "--no-watch")
        if command is None:
            print("❌ No Tauri CLI available.")
            return 1

        print("🚀 Opening the Mightling desktop window...")
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
        if not cls.has_source():
            return cls._no_source()
        if not cls._ensure_toolchain() or not cls.build_ui():
            return 1

        command = cls._tauri_command("build")
        if command is None:
            print("❌ No Tauri CLI available.")
            return 1

        print("🔨 Building the Mightling desktop bundle (the first Rust build takes a while)...")
        code = subprocess.call(command, cwd=DESKTOP_PROJECT_DIR, env=cls._environment())
        if code == 0:
            cls.install_desktop_entry()
            bundle = os.path.join(DESKTOP_PROJECT_DIR, "src-tauri", "target", "release", "bundle")
            print(f"✅ Bundles written to {bundle}")
        return code

    @classmethod
    def _app_identifier(cls) -> Optional[str]:
        """
        Reads the bundle identifier out of `tauri.conf.json`.

        Taken from the config rather than repeated here, so the data directory this points at
        cannot drift away from the one the app actually uses.

        Returns:
            Optional[str]: The identifier, or None if the config cannot be read.
        """
        config = os.path.join(DESKTOP_PROJECT_DIR, "src-tauri", "tauri.conf.json")
        try:
            with open(config) as handle:
                return json.load(handle).get("identifier")
        except (OSError, ValueError):
            return None

    @classmethod
    def clear_webview_cache(cls) -> bool:
        """
        Empties the webview's HTTP cache, leaving cookies and local storage in place.

        Returns:
            bool: True if there was a cache and it was removed.
        """
        identifier = cls._app_identifier()
        if not identifier:
            return False
        cache = os.path.join(
            os.path.expanduser("~/.local/share"), identifier, WEBVIEW_CACHE_DIR_NAME
        )
        if not os.path.isdir(cache):
            return False
        shutil.rmtree(cache, ignore_errors=True)
        return True

    @classmethod
    def binary_path(cls) -> Optional[str]:
        """
        Locates the compiled desktop binary, preferring a release build.

        Returns:
            Optional[str]: Path to the binary, or None if it has not been built.
        """
        for profile in ("release", "debug"):
            candidate = os.path.join(
                DESKTOP_PROJECT_DIR, "src-tauri", "target", profile, "mling-app"
            )
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    @classmethod
    def install_desktop_entry(cls) -> bool:
        """
        Registers the app with the desktop environment: an icon and a `.desktop` entry.

        Without this the running window has no entry to match, so the dock shows an unnamed generic
        icon and there is nothing in the applications grid to launch. A deb ships these under
        `/usr`; from a source checkout they go in the user's XDG directories.

        Returns:
            bool: True if the entry was written.
        """
        binary = cls.binary_path()
        if not binary:
            print("❌ The desktop app has not been built yet — nothing to register.")
            return False

        icon = os.path.join(ICON_DIR, f"{ICON_NAME}.png")
        try:
            os.makedirs(DESKTOP_ENTRY_DIR, exist_ok=True)
            os.makedirs(ICON_DIR, exist_ok=True)
        except OSError as exc:
            print(f"⚠️  Could not create the XDG directories: {exc}")
            return False

        from dreamference.chat.onyx_brand_assets import OnyxBrandAssets

        OnyxBrandAssets.render_app_icon(icon, 256)

        # No environment is set on Exec: the webview settings live in the binary itself, so a
        # launcher-started window behaves exactly like one started from the shell.
        entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Mightling\n"
            "Comment=Local AI assistant served from this machine\n"
            f"Exec={binary}\n"
            f"Icon={ICON_NAME}\n"
            "Terminal=false\n"
            "Categories=Utility;Development;\n"
            f"StartupWMClass={WINDOW_CLASS}\n"
        )
        try:
            with open(os.path.join(DESKTOP_ENTRY_DIR, DESKTOP_ENTRY_NAME), "w") as handle:
                handle.write(entry)
        except OSError as exc:
            print(f"⚠️  Could not write the desktop entry: {exc}")
            return False

        for legacy in LEGACY_ENTRY_NAMES:
            for stale in (
                os.path.join(DESKTOP_ENTRY_DIR, f"{legacy}.desktop"),
                os.path.join(ICON_DIR, f"{legacy}.png"),
            ):
                if os.path.exists(stale):
                    os.remove(stale)

        subprocess.run(
            ["update-desktop-database", DESKTOP_ENTRY_DIR],
            capture_output=True, timeout=60, check=False,
        )
        print(f"🖥️  Registered Mightling with the desktop environment ({DESKTOP_ENTRY_NAME}).")
        return True

    @classmethod
    def install(cls) -> int:
        """
        Installs everything the desktop app is built from, without building it.

        Returns:
            int: 0 if the toolchain is complete afterwards.
        """
        if not cls.has_source():
            return cls._no_source()
        if not cls._ensure_toolchain():
            return 1
        if cls.binary_path():
            cls.install_desktop_entry()
        return 0

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
        print(f"{'✅' if serving else '❌'} Mightling server at {web_url}"
              f"{'' if serving else ' — start it with: mling-admin chat start'}")
        complete, _ = DesktopInstaller.report()
        return 0 if serving and complete else 1
