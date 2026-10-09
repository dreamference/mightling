"""
Mightling Desktop App Runner.

This module provides the DesktopRunner class, which builds and launches the Electron app in
`desktop/electron` (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md), built the way the Codex
desktop app is built: Electron Forge, with `ling` bundled inside as `resources/ling`.

The app has one window, the Mightling UI built from `desktop/ui` and bundled by the same Vite
build as the main process. It shows Ask (the menu's former Chat, which showed the Onyx web UI until
2026-10-08 and was a window of its own on `ling web` until 2026-10-09) and Work, both on the one
`ling app-server` the app starts (specs/DREAMFERENCE_MIGHTLING_ASK.md §18.6). It needs neither
Onyx nor `ling web`, so nothing is checked before the window opens: it says on its own start-up
screen what it is waiting for.

It follows the same shape as the agent runners: provision the tooling if it is missing, then hand
off to a subprocess.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Final, List, Optional

from dreamference.chat.desktop_installer import CARGO_BIN, DesktopInstaller

# The project lives beside the Python package rather than inside it: it has its own build system,
# and `pip install -e .` has no business copying it around.
DESKTOP_PROJECT_DIR: Final[str] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "desktop"
)

# The Electron project and Work's UI.
ELECTRON_DIR: Final[Path] = Path(DESKTOP_PROJECT_DIR) / "electron"
UI_DIR: Final[Path] = Path(DESKTOP_PROJECT_DIR) / "ui"

# `app.json`: the product name, identifier, scheme and window settings the app and these tests read.
APP_CONFIG: Final[Path] = ELECTRON_DIR / "app.json"

# The binaries bundled into the app, copied here from the installed `ling` build before packaging.
RESOURCES_DIR: Final[Path] = ELECTRON_DIR / "resources"
BUNDLED_BINARIES: Final[tuple] = ("ling", "codex-code-mode-host")

# Where a desktop entry and its icon go for the current user. A deb puts these under /usr;
# running from a source checkout, they belong in the XDG user directories instead.
DESKTOP_ENTRY_DIR: Final[str] = os.path.expanduser("~/.local/share/applications")
ICON_DIR: Final[str] = os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps")
DESKTOP_ENTRY_NAME: Final[str] = "ling-app.desktop"
ICON_NAME: Final[str] = "ling-app"

# GNOME matches a running window to its desktop entry by `WM_CLASS`, and shows a generic icon when
# nothing matches. Electron sets the class from the app's name (`app.setName`), `Mightling`.
WINDOW_CLASS: Final[str] = "Mightling"

# Names the desktop binary had before, whose launcher entries and icons are removed on registration
# so the applications grid does not show two of it, one pointing at a binary that no longer builds.
# `puffin-app` is the name it had until the product became Mightling.
LEGACY_ENTRY_NAMES: Final[tuple] = ("puffin-desktop", "puffin-ui", "puffin-app")

# Chromium's HTTP cache, inside the app's data directory, emptied on launch so a window never
# shows a page from before an upgrade. Emptying it costs little: the UI is bundled in the app.
# The `Cookies` file beside it is left alone.
WEBVIEW_CACHE_DIR_NAME: Final[str] = "Cache"


class DesktopRunner:
    """
    Builds and runs the Mightling desktop app.
    """

    @classmethod
    def _environment(cls) -> dict:
        """
        Returns the environment the build tools and the app run with.

        `~/.cargo/bin` is put on PATH for the Codex build, which shares this helper; a rustup
        installed during the same run is on disk but absent from the inherited PATH.

        Returns:
            dict: A copy of the current environment.
        """
        environment = dict(os.environ)
        if os.path.isdir(CARGO_BIN) and CARGO_BIN not in environment.get("PATH", ""):
            environment["PATH"] = f"{CARGO_BIN}{os.pathsep}{environment.get('PATH', '')}"
        return environment

    @classmethod
    def _ensure_toolchain(cls, build: bool = False) -> bool:
        """
        Checks Node and npm are present (and, for the `.deb`, dpkg); nothing is
        installed, since all of them come from the system.

        Args:
            build (bool): Whether the `.deb` is to be made.

        Returns:
            bool: True if the toolchain is complete.
        """
        complete, _ = DesktopInstaller.report(build)
        return complete

    @classmethod
    def _npm(cls, args: List[str], cwd: Path) -> bool:
        npm = shutil.which("npm")
        if npm is None:
            print("❌ npm is needed to build the desktop app: install Node.js 22.13 or later.")
            return False
        return subprocess.call([npm, *args], cwd=cwd, env=cls._environment()) == 0

    @classmethod
    def install_packages(cls) -> bool:
        """Installs the two projects' npm packages from their lock files, once.

        Returns:
            bool: True when both have their `node_modules`.
        """
        for directory, what in ((UI_DIR, "the app window's packages (desktop/ui)"), (ELECTRON_DIR, "the app's packages (desktop/electron: Electron, Forge, Vite)")):
            if (directory / "node_modules").is_dir():
                continue
            print(f"📦 Installing {what}...")
            if not cls._npm(["ci", "--no-audit", "--no-fund"], directory):
                return False
        return True

    @classmethod
    def has_source(cls) -> bool:
        """
        Tells a checkout from a release install, which has the package but no `desktop/` project.

        Returns:
            bool: True if the Electron project is beside the package, i.e. the app can be built here.
        """
        return ELECTRON_DIR.is_dir() and (ELECTRON_DIR / "package.json").is_file()

    @classmethod
    def _no_source(cls) -> int:
        """
        Says why the desktop app cannot be built on a machine installed from a release.

        Returns:
            int: 1.
        """
        print("❌ The desktop app is built from the repository's desktop/ project, which a release "
              "install does not have.")
        print("💡 Install the app from the release instead: the Mightling .deb on the release page "
              "puts `ling-app` in your launcher.")
        return 1

    @classmethod
    def _run_installed(cls) -> int:
        """
        Opens the desktop app a release's `.deb` installed, where there is no project to run.

        Returns:
            int: The app's exit code, or 1 if it is not installed.
        """
        installed = shutil.which("ling-app")
        if installed is None:
            return cls._no_source()
        print("🚀 Opening the Mightling desktop window...")
        return subprocess.call([installed], env=cls._environment())

    @classmethod
    def copy_bundled_binaries(cls) -> bool:
        """
        Copies `ling` and `codex-code-mode-host` from the installed build into `resources/`, where
        Forge packages them beside the app (the Codex app bundles its agent the same way). A
        `rg` on PATH rides along too, for the agent's fuzzy file search.

        Returns:
            bool: True when `ling` was copied.
        """
        from dreamference.runner.codex_branded_builder import CodexBrandedBuilder
        bin_dir = Path(CodexBrandedBuilder.executable_path()).parent
        RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
        for name in BUNDLED_BINARIES:
            source = bin_dir / name
            if not source.is_file():
                print(f"❌ {source} is not built: run `ling-admin codex build` first.")
                return False
            shutil.copy2(source, RESOURCES_DIR / name)
        rg = shutil.which("rg")
        if rg:
            shutil.copy2(rg, RESOURCES_DIR / "rg")
        return True

    @classmethod
    def run(cls) -> int:
        """
        Opens the Mightling desktop window from the checkout: packages the app (`electron-forge
        package`: Vite builds the main process, the preload and Work's page; `ling` is bundled)
        and runs the packaged binary, so what opens is what the `.deb` ships. Not
        `electron-forge start`, whose dev server Work's `app://` page does not use.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        if not cls.has_source():
            return cls._run_installed()
        if not cls._ensure_toolchain() or not cls.install_packages():
            return 1
        if not DesktopInstaller.userns_allowed():
            print("⚠️  Chromium's sandbox needs a user namespace, which this machine refuses without an AppArmor")
            print("   profile; from a plain terminal or the launcher the app would abort at start.")
            print("💡 `ling-admin desktop install` writes the profile (sudo once).")

        if not cls.copy_bundled_binaries():
            return 1
        print("🔨 Building the desktop app (Vite, then Forge's package)...")
        if not cls._npm(["run", "package"], ELECTRON_DIR):
            return 1
        binary = cls.binary_path()
        if binary is None:
            print(f"❌ The package step wrote no app under {ELECTRON_DIR / 'out'}.")
            return 1
        cls.install_desktop_entry()

        cls.clear_webview_cache()
        print("🚀 Opening the Mightling desktop window...")
        return subprocess.call([binary], env=cls._environment())

    @classmethod
    def build(cls) -> int:
        """
        Builds the distributable `.deb` (and a zip) with Electron Forge.

        `ling` must be built, because it is bundled into the app: the app-server Ask and Work
        share is that binary.

        Returns:
            int: 0 on success, non-zero on failure.
        """
        if not cls.has_source():
            return cls._no_source()
        if not cls._ensure_toolchain(build=True) or not cls.install_packages():
            return 1
        if not cls.copy_bundled_binaries():
            return 1

        print("🔨 Building the Mightling desktop app (Vite, then Forge's .deb)...")
        if not cls._npm(["run", "make"], ELECTRON_DIR):
            return 1
        cls.install_desktop_entry()
        bundle = ELECTRON_DIR / "out" / "make"
        print(f"✅ Bundles written to {bundle}")
        return 0

    @classmethod
    def _app_config(cls) -> dict:
        try:
            with open(APP_CONFIG) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    @classmethod
    def _app_identifier(cls) -> Optional[str]:
        """
        Reads the app's identifier out of `app.json`.

        Taken from the config rather than repeated here, so the data directory this points at
        cannot drift away from the one the app actually uses.

        Returns:
            Optional[str]: The identifier, or None if the config cannot be read.
        """
        return cls._app_config().get("identifier")

    @classmethod
    def clear_webview_cache(cls) -> bool:
        """
        Empties Chromium's HTTP cache, leaving cookies and local storage in place.

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
        Locates the packaged desktop binary (`electron-forge package` or `make` output).

        Returns:
            Optional[str]: Path to the binary, or None if it has not been built.
        """
        config = cls._app_config()
        name = config.get("productName", "Mightling")
        executable = config.get("executable", "Mightling")
        out = ELECTRON_DIR / "out"
        if not out.is_dir():
            return None
        for folder in sorted(out.glob(f"{name}-*")):
            candidate = folder / executable
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    @classmethod
    def dev_binaries(cls) -> List[str]:
        """
        The Electron binaries a checkout runs: npm's prebuilt `electron` (for `desktop run`) and
        the packaged app (for `desktop build`'s output). Both need the AppArmor profile.

        Returns:
            List[str]: Absolute paths that exist.
        """
        candidates = [str(ELECTRON_DIR / "node_modules" / "electron" / "dist" / "electron")]
        packaged = cls.binary_path()
        if packaged:
            candidates.append(packaged)
        return [path for path in candidates if os.path.isfile(path)]

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

        scheme = cls._app_config().get("scheme", "mightling")
        entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Mightling\n"
            "Comment=Local AI assistant served from this machine\n"
            f"Exec={binary} %U\n"
            f"Icon={ICON_NAME}\n"
            "Terminal=false\n"
            "StartupNotify=true\n"
            "Categories=Utility;Development;\n"
            f"MimeType=x-scheme-handler/{scheme};\n"
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
        Installs what the desktop app is built and run from, without building it: npm's packages
        (Electron among them), and the AppArmor profile Chromium's sandbox needs on this machine.

        Returns:
            int: 0 if the toolchain is complete afterwards.
        """
        if not cls.has_source():
            return cls._no_source()
        if not cls._ensure_toolchain() or not cls.install_packages():
            return 1
        if not DesktopInstaller.userns_allowed():
            DesktopInstaller.install_apparmor_profile(cls.dev_binaries())
        if cls.binary_path():
            cls.install_desktop_entry()
        return 0

    @classmethod
    def status(cls) -> int:
        """
        Reports whether the desktop app can be built, and whether the `ling` it bundles is built.

        Returns:
            int: 0 if the app could be built and launched right now, 1 otherwise.
        """
        from dreamference.runner.codex_branded_builder import CodexBrandedBuilder
        ling = CodexBrandedBuilder.executable_path()
        built = os.path.isfile(ling)
        print(f"{'✅' if built else '❌'} ling, which the app bundles for Work and Ask"
              f"{f' ({ling})' if built else ' — build it with: ling-admin codex build'}")
        complete, _ = DesktopInstaller.report()
        if not DesktopInstaller.userns_allowed():
            print("⚠️  Chromium's sandbox cannot create a user namespace from an unconfined program here: "
                  "`ling-admin desktop install` writes the AppArmor profile.")
        return 0 if built and complete else 1
