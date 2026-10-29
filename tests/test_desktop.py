"""The desktop app's shell (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md): what `ling-admin
desktop` builds and installs, and what the Electron project must keep.
"""

import json
import os
import re
from pathlib import Path
from unittest.mock import patch

from dreamference.chat import DesktopInstaller, DesktopRunner
from dreamference.chat.desktop_runner import DESKTOP_PROJECT_DIR, ELECTRON_DIR, UI_DIR

ELECTRON = Path(ELECTRON_DIR)


def app_config() -> dict:
    with Path(ELECTRON / "app.json").open() as handle:
        return json.load(handle)


def electron_sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((ELECTRON / "src").glob("*.ts"))
    )


def test_the_app_names_one_window_and_no_web_page():
    # Ask (the menu's former Chat) is a view of the app window, served from app:// with the
    # policy layer in the main process (specs/DREAMFERENCE_MIGHTLING_ASK.md §18.6): no window on
    # `ling web` and none on Onyx.
    config = app_config()
    assert "chat" not in config and "work" in config and "csp" in config
    assert config["productName"] == "Mightling"
    assert config["identifier"] == "dev.dreamference.mightling"
    assert (
        config["command"] == "ling-app"
        and config["scheme"] == "ling"
        and config["legacySchemes"] == ["mightling"]
    )


def test_bundle_icons_exist_at_the_sizes_the_package_names():
    # The icon set is rendered from the same mark as the favicon, so a missing size means the
    # launcher entry falls back to a blank square. `icon.png` is the .deb's and the packager's;
    # 32x32 is the tray's.
    for name in ("icon.png", "32x32.png", "128x128.png", "128x128@2x.png"):
        assert (ELECTRON / "icons" / name).is_file(), name


def test_the_windows_icon_holds_every_size_windows_draws_and_the_installer_is_dpi_aware():
    # forge.config.js writes resources/icon.ico from ICO_IMAGES: each entry must be a PNG of the size
    # its directory entry declares, and together they cover 16 to 48 (small icons at 100-300 %
    # scaling), 64 and 256, so Windows never scales a 32- or 128-pixel image to fit.
    forge = (ELECTRON / "forge.config.js").read_text()
    listed = re.search(r"const ICO_IMAGES = \[(.*?)\n\];", forge, re.S).group(1)
    entries = [(name, int(size)) for name, size in re.findall(r'\["([^"]+)", (\d+)\]', listed)]
    assert {size for _name, size in entries} >= {16, 20, 24, 32, 40, 48, 64, 256}
    for name, size in entries:
        data = (ELECTRON / "icons" / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        assert int.from_bytes(data[16:20], "big") == int.from_bytes(data[20:24], "big") == size, (
            name
        )
    # The installer's own window is drawn at the display's scaling, not stretched from 96 DPI.
    assert "\nManifestDPIAware true\n" in (ELECTRON / "windows" / "installer.nsi").read_text()


def test_electron_is_pinned_exactly_and_stock():
    # The Codex app runs on the upstream vendor's own Electron build; Mightling runs the published
    # one, pinned to a patch release and bumped deliberately (§3, decision 2).
    with Path(ELECTRON / "package.json").open() as handle:
        package = json.load(handle)
    assert package["devDependencies"]["electron"] == "42.11.11"
    assert package["main"] == ".vite/build/early-bootstrap.cjs"
    with Path(UI_DIR / "package.json").open() as handle:
        assert not any(
            name.startswith("@tauri-apps") for name in json.load(handle)["devDependencies"]
        )


def test_no_gpu_flags_no_toolkit_environment_no_sandbox_switch():
    # Electron's defaults, as the Codex app runs (§3, decision 1): the WebKitGTK workarounds died
    # with the Tauri shell, and Chromium's sandbox stays on.
    source = electron_sources()
    for forbidden in (
        "WEBKIT_",
        "GTK_THEME",
        "disable-gpu",
        "ignore-gpu-blocklist",
        "no-sandbox",
        "analytics-default-enabled",
    ):
        assert forbidden not in source, forbidden
    assert (
        "sandbox: true" in source
        and "nodeIntegration: false" in source
        and "contextIsolation: true" in source
    )


def test_the_egress_switches_are_always_applied():
    # What keeps Chromium quiet on the network (§3, decision 6).
    egress = (ELECTRON / "src" / "egress.ts").read_text(encoding="utf-8")
    for switch in (
        "disable-background-networking",
        "disable-component-update",
        "AutofillServerCommunication",
        "NetworkTimeServiceQuerying",
        "MediaRouter",
    ):
        assert switch in egress, switch
    bootstrap = (ELECTRON / "src" / "early-bootstrap.ts").read_text(encoding="utf-8")
    assert "applySwitches()" in bootstrap


def test_the_fuses_are_the_codex_apps():
    forge = (ELECTRON / "forge.config.js").read_text(encoding="utf-8")
    for fuse, state in (
        ("RunAsNode", "false"),
        ("EnableNodeOptionsEnvironmentVariable", "false"),
        ("EnableNodeCliInspectArguments", "false"),
        ("GrantFileProtocolExtraPrivileges", "false"),
        ("EnableCookieEncryption", "true"),
        ("EnableEmbeddedAsarIntegrityValidation", "true"),
        ("OnlyLoadAppFromAsar", "true"),
        ("WasmTrapHandlers", "true"),
    ):
        assert f"[FuseV1Options.{fuse}]: {state}" in forge, fuse
    # No SUID sandbox helper is shipped and none of the Codex app's extras are depended on.
    assert '"chrome-sandbox"' in forge
    assert "libtss2" not in forge and "libusb" not in forge


def test_the_package_writes_the_apparmor_profile_and_the_command():
    # Ubuntu refuses the user namespace Chromium's sandbox needs to programs without a profile;
    # the .deb's postinst writes one for the binary, the Codex app's shape, and `ling-app`.
    postinst = (ELECTRON / "linux" / "postinst").read_text(encoding="utf-8")
    assert 'profile mightling "/usr/lib/mightling/Mightling" flags=(unconfined) {' in postinst
    assert "userns," in postinst
    assert (
        "cat > /usr/bin/ling-app" in postinst
        and 'exec /usr/lib/mightling/Mightling "$@"' in postinst
    )
    prerm = (ELECTRON / "linux" / "prerm").read_text(encoding="utf-8")
    assert "apparmor_parser -R" in prerm and "rm -f /usr/bin/ling-app" in prerm


def test_the_checkouts_profile_has_the_same_shape():
    profile = DesktopInstaller.apparmor_profile(["/opt/a/electron", "/opt/b/Mightling"])
    assert 'profile mightling-desktop-dev "/opt/a/electron" flags=(unconfined) {' in profile
    assert 'profile mightling-desktop-dev-1 "/opt/b/Mightling" flags=(unconfined) {' in profile
    assert profile.count("userns,") == 2


def test_the_app_no_longer_waits_for_onyx():
    # Ask (the former Chat) and Work run in the app window on its own app-server; neither needs
    # Onyx. Nothing checks port 3000 before the window opens.
    assert not hasattr(DesktopRunner, "onyx_is_up")
    source = (
        Path(DESKTOP_PROJECT_DIR).parent / "dreamference" / "chat" / "desktop_runner.py"
    ).read_text(encoding="utf-8")
    assert "onyx_is_up" not in source and "3000" not in source and "chat start" not in source


def test_run_stops_when_the_packages_cannot_be_installed():
    with (
        patch.object(DesktopRunner, "_ensure_toolchain", return_value=True),
        patch.object(DesktopRunner, "install_packages", return_value=False),
        patch("subprocess.call") as call,
    ):
        assert DesktopRunner.run() == 1
        call.assert_not_called()


def test_install_writes_the_profile_only_where_a_binary_has_none():
    # The one step that changes the machine outside this user's home, and only when needed.
    with (
        patch.object(DesktopRunner, "_ensure_toolchain", return_value=True),
        patch.object(DesktopRunner, "install_packages", return_value=True),
        patch.object(DesktopRunner, "binary_path", return_value=None),
        patch.object(DesktopRunner, "dev_binaries", return_value=["/opt/electron"]),
        patch.object(DesktopInstaller, "uncovered", return_value=[]),
        patch.object(DesktopInstaller, "install_apparmor_profile") as profile,
    ):
        assert DesktopRunner.install() == 0
        profile.assert_not_called()
    with (
        patch.object(DesktopRunner, "_ensure_toolchain", return_value=True),
        patch.object(DesktopRunner, "install_packages", return_value=True),
        patch.object(DesktopRunner, "binary_path", return_value=None),
        patch.object(
            DesktopRunner,
            "dev_binaries",
            return_value=["/opt/electron", "/opt/out/Mightling-linux-arm64/Mightling"],
        ),
        patch.object(
            DesktopInstaller, "uncovered", return_value=["/opt/out/Mightling-linux-arm64/Mightling"]
        ),
        patch.object(
            DesktopInstaller,
            "covering_profile",
            side_effect=lambda binary: (
                "/etc/apparmor.d/mightling-desktop-dev" if binary == "/opt/electron" else None
            ),
        ),
        patch.object(DesktopInstaller, "install_apparmor_profile", return_value=True) as profile,
    ):
        assert DesktopRunner.install() == 0
        # The checkout's profile is rewritten whole: what it named before, and what it lacked.
        profile.assert_called_once_with(
            ["/opt/electron", "/opt/out/Mightling-linux-arm64/Mightling"]
        )


def test_install_leaves_a_binary_another_profile_names():
    # An unpacked build's own profile (or the .deb's) already attaches to it; a second profile
    # with the same attachment would conflict.
    with (
        patch.object(DesktopRunner, "_ensure_toolchain", return_value=True),
        patch.object(DesktopRunner, "install_packages", return_value=True),
        patch.object(DesktopRunner, "binary_path", return_value=None),
        patch.object(
            DesktopRunner,
            "dev_binaries",
            return_value=["/opt/electron", "/opt/out/Mightling-linux-arm64/Mightling"],
        ),
        patch.object(DesktopInstaller, "uncovered", return_value=["/opt/electron"]),
        patch.object(
            DesktopInstaller,
            "covering_profile",
            side_effect=lambda binary: (
                None if binary == "/opt/electron" else "/etc/apparmor.d/mightling-unpacked"
            ),
        ),
        patch.object(DesktopInstaller, "install_apparmor_profile", return_value=False) as profile,
    ):
        # A profile that could not be written fails the install.
        assert DesktopRunner.install() == 1
        profile.assert_called_once_with(["/opt/electron"])


def test_the_packaged_binary_is_covered_before_it_is_built(tmp_path):
    # `desktop install` runs before the first `desktop run` packages the app; the profile it
    # wrote named npm's electron only, so the binary the launcher starts died with "No usable
    # sandbox!" (2026-10-19). The packaged path is predicted, by the packager's arch names.
    electron = tmp_path / "node_modules" / "electron" / "dist" / "electron"
    electron.parent.mkdir(parents=True)
    electron.write_bytes(b"")
    with (
        patch("dreamference.chat.desktop_runner.ELECTRON_DIR", tmp_path),
        patch("platform.machine", return_value="aarch64"),
    ):
        assert DesktopRunner.packaged_binary_path() == str(
            tmp_path / "out" / "Mightling-linux-arm64" / "Mightling"
        )
        assert DesktopRunner.dev_binaries() == [
            str(electron),
            str(tmp_path / "out" / "Mightling-linux-arm64" / "Mightling"),
        ]
    with (
        patch("dreamference.chat.desktop_runner.ELECTRON_DIR", tmp_path),
        patch("platform.machine", return_value="x86_64"),
    ):
        assert DesktopRunner.packaged_binary_path().endswith("/out/Mightling-linux-x64/Mightling")
    # The profile is written for a path that does not exist yet: AppArmor attaches it at exec.
    with patch("shutil.which", return_value="/usr/bin/x"), patch("subprocess.run") as run:
        run.return_value.returncode = 0
        assert (
            DesktopInstaller.install_apparmor_profile(
                [str(tmp_path / "out" / "Mightling-linux-arm64" / "Mightling")]
            )
            is True
        )
    assert (
        f'"{tmp_path}/out/Mightling-linux-arm64/Mightling"' in run.call_args_list[0].kwargs["input"]
    )


def test_a_profile_is_found_by_the_binary_it_names(tmp_path):
    profiles = tmp_path / "apparmor.d"
    (profiles / "abstractions").mkdir(parents=True)
    (profiles / "mightling").write_text(
        'profile mightling "/usr/lib/mightling/Mightling" flags=(unconfined) {\n  userns,\n}\n'
    )
    (profiles / "other").write_text('profile other "/opt/Other/Mightling" {\n}\n')
    sysctl = tmp_path / "restrict"
    sysctl.write_text("1\n")
    with (
        patch("dreamference.chat.desktop_installer.APPARMOR_DIR", str(profiles)),
        patch("dreamference.chat.desktop_installer.USERNS_SYSCTL", str(sysctl)),
    ):
        assert DesktopInstaller.covering_profile("/usr/lib/mightling/Mightling") == str(
            profiles / "mightling"
        )
        # A profile without `userns,` grants nothing; a path that is only a prefix is not a match.
        assert DesktopInstaller.covering_profile("/opt/Other/Mightling") is None
        assert DesktopInstaller.covering_profile("/usr/lib/mightling/Might") is None
        assert DesktopInstaller.uncovered(["/usr/lib/mightling/Mightling", "/opt/x/Mightling"]) == [
            "/opt/x/Mightling"
        ]
        sysctl.write_text("0\n")
        # Where the kernel does not restrict user namespaces, nothing needs a profile.
        assert DesktopInstaller.userns_restricted() is False
        assert DesktopInstaller.uncovered(["/opt/x/Mightling"]) == []
    with patch("dreamference.chat.desktop_installer.APPARMOR_DIR", str(tmp_path / "missing")):
        assert DesktopInstaller.covering_profile("/usr/lib/mightling/Mightling") is None


def test_run_says_when_the_packaged_binary_has_no_profile(capsys):
    with (
        patch.object(DesktopRunner, "_ensure_toolchain", return_value=True),
        patch.object(DesktopRunner, "install_packages", return_value=True),
        patch.object(DesktopRunner, "copy_bundled_binaries", return_value=True),
        patch.object(DesktopRunner, "_npm", return_value=True),
        patch.object(
            DesktopRunner, "binary_path", return_value="/opt/out/Mightling-linux-arm64/Mightling"
        ),
        patch.object(DesktopRunner, "install_desktop_entry"),
        patch.object(DesktopRunner, "clear_webview_cache"),
        patch.object(DesktopInstaller, "uncovered", side_effect=lambda binaries: list(binaries)),
        patch("subprocess.call", return_value=0) as call,
    ):
        # Still opened: from PyCharm's terminal (a permissive profile) it starts anyway.
        assert DesktopRunner.run() == 0
    out = capsys.readouterr().out
    assert "/opt/out/Mightling-linux-arm64/Mightling" in out and "No usable sandbox!" in out
    assert "`ling-admin desktop install`" in out
    assert call.call_args[0][0] == ["/opt/out/Mightling-linux-arm64/Mightling"]


def test_the_profile_install_is_announced_before_sudo_runs(capsys):
    with patch("shutil.which", return_value="/usr/bin/x"), patch("subprocess.run") as run:
        run.return_value.returncode = 0
        assert DesktopInstaller.install_apparmor_profile([os.path.abspath(__file__)]) is True
    out = capsys.readouterr().out
    assert "sudo tee /etc/apparmor.d/mightling-desktop-dev" in out
    assert run.call_args_list[0].args[0][:2] == ["sudo", "tee"]
    assert run.call_args_list[1].args[0][:2] == ["sudo", "apparmor_parser"]


def test_build_does_not_require_the_server():
    # Ask is served by the bundled `ling` itself, so there is nothing to fetch at build time.
    commands = []
    with (
        patch.object(DesktopRunner, "_ensure_toolchain", return_value=True),
        patch.object(DesktopRunner, "install_packages", return_value=True),
        patch.object(DesktopRunner, "copy_bundled_binaries", return_value=True),
        patch.object(DesktopRunner, "install_desktop_entry"),
        patch.object(
            DesktopRunner,
            "_npm",
            side_effect=lambda args, cwd: commands.append((args, cwd)) or True,
        ),
    ):
        assert DesktopRunner.build() == 0
    assert commands == [(["run", "make"], ELECTRON_DIR)]


def test_build_needs_the_agent_it_bundles():
    with (
        patch.object(DesktopRunner, "_ensure_toolchain", return_value=True),
        patch.object(DesktopRunner, "install_packages", return_value=True),
        patch.object(DesktopRunner, "copy_bundled_binaries", return_value=False),
        patch.object(DesktopRunner, "_npm") as npm,
    ):
        assert DesktopRunner.build() == 1
        npm.assert_not_called()


def test_the_bundled_binaries_come_from_the_installed_build(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ling", "codex-code-mode-host"):
        (bin_dir / name).write_bytes(b"#!/bin/sh\n")
    resources = tmp_path / "resources"
    with (
        patch(
            "dreamference.runner.codex_branded_builder.CodexBrandedBuilder.executable_path",
            return_value=str(bin_dir / "ling"),
        ),
        patch("dreamference.chat.desktop_runner.RESOURCES_DIR", resources),
        patch("shutil.which", return_value=None),
    ):
        assert DesktopRunner.copy_bundled_binaries() is True
    assert sorted(p.name for p in resources.iterdir()) == ["codex-code-mode-host", "ling"]
    (bin_dir / "ling").unlink()
    with (
        patch(
            "dreamference.runner.codex_branded_builder.CodexBrandedBuilder.executable_path",
            return_value=str(bin_dir / "ling"),
        ),
        patch("dreamference.chat.desktop_runner.RESOURCES_DIR", resources),
    ):
        assert DesktopRunner.copy_bundled_binaries() is False


def test_a_rustup_installed_this_run_is_put_on_path():
    # The Codex build shares this helper: rustup writes to ~/.cargo/bin without touching the PATH
    # of the shell that ran it.
    from dreamference.chat.desktop_installer import CARGO_BIN

    with (
        patch("os.path.isdir", return_value=True),
        patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False),
    ):
        environment = DesktopRunner._environment()

    assert environment["PATH"].startswith(CARGO_BIN)


def test_cargo_is_found_in_the_rustup_location_as_well_as_on_path():
    from dreamference.chat.desktop_installer import CARGO_BIN

    with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True):
        assert DesktopInstaller.cargo_path() == os.path.join(CARGO_BIN, "cargo")


def test_the_toolchain_is_node_only():
    with (
        patch.object(DesktopInstaller, "node_version", return_value=(22, 13)),
        patch("shutil.which", return_value="/usr/bin/npm"),
    ):
        assert DesktopInstaller.missing_prerequisites() == []
    with (
        patch.object(DesktopInstaller, "node_version", return_value=(24, 0)),
        patch("shutil.which", return_value="/usr/bin/npm"),
    ):
        assert DesktopInstaller.missing_prerequisites() == []
    # Electron Forge 8 declares node >= 22.13.0: an earlier 22 is not enough.
    with (
        patch.object(DesktopInstaller, "node_version", return_value=(22, 12)),
        patch("shutil.which", return_value="/usr/bin/npm"),
    ):
        assert DesktopInstaller.missing_prerequisites() == ["node"]
    with (
        patch.object(DesktopInstaller, "node_version", return_value=(18, 20)),
        patch("shutil.which", return_value=None),
    ):
        assert DesktopInstaller.missing_prerequisites() == ["node", "npm"]


def test_the_deb_needs_dpkg_and_no_fakeroot():
    # Forge 8's deb maker runs `dpkg-deb --root-owner-group` itself: fakeroot is not asked for.
    present = {"npm", "dpkg", "dpkg-deb"}
    which = lambda name: f"/usr/bin/{name}" if name in present else None
    with (
        patch.object(DesktopInstaller, "node_version", return_value=(22, 13)),
        patch("shutil.which", side_effect=which),
    ):
        assert DesktopInstaller.missing_prerequisites(build=True) == []
    present.discard("dpkg-deb")
    with (
        patch.object(DesktopInstaller, "node_version", return_value=(22, 13)),
        patch("shutil.which", side_effect=which),
    ):
        assert DesktopInstaller.missing_prerequisites(build=True) == ["dpkg"]
        assert DesktopInstaller.missing_prerequisites() == []


def test_the_node_floor_is_forge_s_own():
    # MIN_NODE_VERSION repeats the `engines` field of the Forge CLI the lockfile pins.
    from dreamference.chat.desktop_installer import MIN_NODE_VERSION

    lock = json.loads((ELECTRON / "package-lock.json").read_text(encoding="utf-8"))
    engines = lock["packages"]["node_modules/@electron-forge/cli"]["engines"]["node"]
    assert engines == ">= {}.{}.0".format(*MIN_NODE_VERSION)


def test_node_version_reads_major_and_minor():
    from types import SimpleNamespace

    with (
        patch("shutil.which", return_value="/usr/bin/node"),
        patch("subprocess.run", return_value=SimpleNamespace(stdout="v22.13.1\n")),
    ):
        assert DesktopInstaller.node_version() == (22, 13)
    with (
        patch("shutil.which", return_value="/usr/bin/node"),
        patch("subprocess.run", return_value=SimpleNamespace(stdout="garbage\n")),
    ):
        assert DesktopInstaller.node_version() is None


def test_desktop_entry_matches_the_window_class_gnome_sees():
    # GNOME resolves a running window to its entry through WM_CLASS; with no match it shows an
    # unnamed generic icon in the dock. Electron takes the class from `app.setName`.
    from dreamference.chat.desktop_runner import DESKTOP_ENTRY_NAME, WINDOW_CLASS

    assert WINDOW_CLASS == "Mightling"
    assert DESKTOP_ENTRY_NAME == "ling-app.desktop"
    bootstrap = (ELECTRON / "src" / "early-bootstrap.ts").read_text(encoding="utf-8")
    assert "app.setName(appConfig.productName)" in bootstrap


def test_registration_removes_launchers_left_by_earlier_binary_names(tmp_path):
    # The binary was puffin-desktop, then puffin-ui, then puffin-app; their entries would otherwise sit
    # beside the new one in the applications grid, launching a binary that no longer builds.
    from dreamference.chat import desktop_runner

    entries, icons = tmp_path / "applications", tmp_path / "icons"
    entries.mkdir()
    icons.mkdir()
    for legacy in ("puffin-desktop", "puffin-ui", "puffin-app"):
        (entries / f"{legacy}.desktop").write_text("[Desktop Entry]\n")
        (icons / f"{legacy}.png").write_bytes(b"")
    with (
        patch.object(desktop_runner, "DESKTOP_ENTRY_DIR", str(entries)),
        patch.object(desktop_runner, "ICON_DIR", str(icons)),
        patch.object(DesktopRunner, "binary_path", return_value="/opt/Mightling"),
        patch("subprocess.run"),
    ):
        assert DesktopRunner.install_desktop_entry() is True

    assert sorted(p.name for p in entries.iterdir()) == ["ling-app.desktop"]
    # The legacy icons are gone and the app's own mark is the one left.
    assert [p.name for p in icons.iterdir()] == ["ling-app.png"]
    assert (icons / "ling-app.png").read_bytes() == desktop_runner.APP_ICON_SOURCE.read_bytes()
    entry = (entries / "ling-app.desktop").read_text()
    assert (
        "Exec=/opt/Mightling %U\n" in entry
        and "MimeType=x-scheme-handler/ling;x-scheme-handler/mightling;" in entry
    )


def test_a_checkout_path_with_spaces_is_quoted_in_the_launcher_entry(tmp_path):
    # The Exec key splits on spaces: unquoted, `/home/u/My Projects/…` launched `/home/u/My`.
    from dreamference.chat import desktop_runner

    entries, icons = tmp_path / "applications", tmp_path / "icons"
    with (
        patch.object(desktop_runner, "DESKTOP_ENTRY_DIR", str(entries)),
        patch.object(desktop_runner, "ICON_DIR", str(icons)),
        patch.object(
            DesktopRunner, "binary_path", return_value="/home/u/My Projects/100%/Mightling"
        ),
        patch("subprocess.run"),
    ):
        assert DesktopRunner.install_desktop_entry() is True
    entry = (entries / "ling-app.desktop").read_text()
    assert 'Exec="/home/u/My Projects/100%%/Mightling" %U\n' in entry
    # Quotes, dollars, backticks and backslashes are escaped inside the quotes, then the
    # backslashes once more as the key is a string (Desktop Entry Specification, "Exec key").
    assert DesktopRunner.exec_argument('/a b/$x"`\\y') == '"/a b/\\\\$x\\\\"\\\\`\\\\\\\\y"'
    assert DesktopRunner.exec_argument("/opt/Mightling") == "/opt/Mightling"


def test_desktop_entry_is_not_written_before_anything_is_built():
    # The Exec line has to point at a binary; on a first run there is not one yet.
    with (
        patch.object(DesktopRunner, "binary_path", return_value=None),
        patch("builtins.open") as opened,
    ):
        assert DesktopRunner.install_desktop_entry() is False
        opened.assert_not_called()


def test_entry_exec_carries_no_environment():
    # A launcher-started window behaves exactly like one started from a shell.
    import inspect

    source = inspect.getsource(DesktopRunner.install_desktop_entry)
    assert "Exec={cls.exec_argument(binary)} %U" in source
    assert "WEBKIT_DISABLE_DMABUF_RENDERER" not in source


def test_cache_is_cleared_without_signing_the_user_out():
    # Only Chromium's HTTP cache goes -- `Cookies` sits beside it.
    from dreamference.chat.desktop_runner import WEBVIEW_CACHE_DIR_NAME

    assert WEBVIEW_CACHE_DIR_NAME == "Cache"
    with (
        patch.object(DesktopRunner, "_app_identifier", return_value="dev.dreamference.mightling"),
        patch("os.path.isdir", return_value=True),
        patch("shutil.rmtree") as rmtree,
    ):
        assert DesktopRunner.clear_webview_cache() is True

    removed = rmtree.call_args[0][0]
    assert removed.endswith(os.path.join("dev.dreamference.mightling", "Cache"))
    assert "Cookies" not in removed
    # The launcher's `ling app` empties the same folder.
    app_rs = (Path(DESKTOP_PROJECT_DIR).parent / "ling-rs" / "src" / "app.rs").read_text(
        encoding="utf-8"
    )
    assert 'const WEBVIEW_CACHE_DIR: &str = "Cache";' in app_rs


def test_identifier_comes_from_the_app_config():
    # Repeating it here would let the data directory drift from the one the app really uses.
    assert DesktopRunner._app_identifier() == "dev.dreamference.mightling"
    bootstrap = (ELECTRON / "src" / "early-bootstrap.ts").read_text(encoding="utf-8")
    assert "dataFolder(process.platform, process.env, appConfig.identifier)" in bootstrap
    assert 'app.setPath("userData", settleDataFolder(legacy, data))' in bootstrap
    # Off Windows the folder is still `~/.local/share/<identifier>`, where this runner empties the
    # cache (data-folder.ts; the cases both it and `ling app` are tested against).
    rule = (ELECTRON / "src" / "data-folder.ts").read_text(encoding="utf-8")
    assert 'joined(home, false, [".local", "share", identifier])' in rule
    vectors = json.loads(
        (ELECTRON.parent.parent / "ling-rs" / "vectors" / "app-data-folder.json").read_text(
            encoding="utf-8"
        )
    )
    assert vectors["identifier"] == DesktopRunner._app_identifier()
    linux = [case for case in vectors["cases"] if case["platform"] == "linux" and case["expected"]]
    assert any(
        case["expected"]
        == case["env"]["HOME"].rstrip("/") + "/.local/share/" + vectors["identifier"]
        for case in linux
    )


def test_window_background_is_painted_rather_than_left_black():
    # A repaint gap shows the window's own background: the page paints its canvas itself, and the
    # window's background is the theme's canvas (theme.ts, DESKTOP_PARITY item 11), never left black
    # or white in the other theme.
    styles = (ELECTRON.parent / "ui" / "src" / "styles.css").read_text(encoding="utf-8")
    assert "body { background: var(--canvas); }" in styles
    assert "--canvas: #ffffff;" in styles
    assert ':root[data-theme="dark"] {' in styles
    theme = (ELECTRON / "src" / "theme.ts").read_text(encoding="utf-8")
    assert 'CANVAS: Record<Theme, string> = { light: "#ffffff", dark: "#212121" }' in theme
    assert "--canvas: #212121;" in styles
    work = (ELECTRON / "src" / "work.ts").read_text(encoding="utf-8")
    assert "...windowColours(options.theme)," in work


def test_ling_web_paints_the_stored_theme_before_the_page_runs():
    # In a browser `ling web`'s bridge.js sets html[data-theme] before the first paint (DESKTOP_PARITY
    # item 11): the choice the Appearance settings kept in localStorage, under the key prefs.ts uses.
    bridge = (ELECTRON.parent.parent / "ling-rs" / "web" / "static" / "bridge.js").read_text(
        encoding="utf-8"
    )
    prefs = (ELECTRON.parent / "ui" / "src" / "prefs.ts").read_text(encoding="utf-8")
    assert 'export const STORAGE_KEY = "mightling.prefs";' in prefs
    assert 'localStorage.getItem("mightling.prefs")' in bridge
    assert "document.documentElement.dataset.theme = chosen ||" in bridge


def test_run_packages_the_app_and_opens_the_packaged_binary():
    # Not `electron-forge start`: Work's page is served by app:// from the built renderer, which a
    # dev server run does not produce; the window that opens is the one the .deb ships.
    commands = []
    with (
        patch.object(DesktopRunner, "_ensure_toolchain", return_value=True),
        patch.object(DesktopRunner, "install_packages", return_value=True),
        patch.object(DesktopInstaller, "uncovered", return_value=[]),
        patch.object(DesktopRunner, "copy_bundled_binaries", return_value=True),
        patch.object(
            DesktopRunner, "_npm", side_effect=lambda args, cwd: commands.append(args) or True
        ),
        patch.object(
            DesktopRunner, "binary_path", return_value="/opt/out/Mightling-linux-arm64/Mightling"
        ),
        patch.object(DesktopRunner, "install_desktop_entry"),
        patch.object(DesktopRunner, "clear_webview_cache"),
        patch("subprocess.call", return_value=0) as call,
    ):
        assert DesktopRunner.run() == 0
    assert commands == [["run", "package"]]
    assert call.call_args[0][0] == ["/opt/out/Mightling-linux-arm64/Mightling"]


def test_works_page_is_served_from_the_built_renderer_in_every_mode():
    main = (ELECTRON / "src" / "main.ts").read_text(encoding="utf-8")
    assert 'serve(path.join(__dirname, "..", "renderer", "main_window"));' in main
    assert "MAIN_WINDOW_VITE_DEV_SERVER_URL" not in main
    # The tray's icon is read from the asar, where the icons are packed.
    assert 'installTray(path.join(__dirname, "..", "..", "icons")' in main
    assert "app.asar.unpacked/icons" not in main


def test_packages_are_installed_again_when_the_lock_file_changes(tmp_path):
    # `ling-admin desktop build` on 2026-10-18: desktop/electron's node_modules predated smol-toml,
    # which package-lock.json had gained, and Rolldown could not resolve it. The install now
    # leaves the lock file's hash in node_modules and runs `npm ci` again when the hash differs.
    from unittest.mock import patch

    import dreamference.chat.desktop_runner as runner_module

    ui, electron = tmp_path / "ui", tmp_path / "electron"
    for project in (ui, electron):
        project.mkdir()
        (project / "package-lock.json").write_text('{"packages": {}}')
    calls = []

    def fake_npm(args, cwd):
        calls.append(Path(cwd).name)
        (Path(cwd) / "node_modules").mkdir(exist_ok=True)
        return True

    with (
        patch.object(runner_module, "UI_DIR", ui),
        patch.object(runner_module, "ELECTRON_DIR", electron),
        patch.object(DesktopRunner, "_npm", side_effect=fake_npm),
    ):
        assert DesktopRunner.install_packages() is True
        assert calls == ["ui", "electron"]
        assert DesktopRunner.packages_current(ui) and DesktopRunner.packages_current(electron)
        # Nothing changed: no install.
        assert DesktopRunner.install_packages() is True
        assert calls == ["ui", "electron"]
        # A dependency added to the app's lock file: that project alone is installed again.
        (electron / "package-lock.json").write_text('{"packages": {"node_modules/smol-toml": {}}}')
        assert not DesktopRunner.packages_current(electron)
        assert DesktopRunner.install_packages() is True
        assert calls == ["ui", "electron", "electron"]
    # A node_modules from before the stamp counts as stale once.
    old = tmp_path / "old"
    (old / "node_modules").mkdir(parents=True)
    (old / "package-lock.json").write_text("{}")
    assert not DesktopRunner.packages_current(old)


SANDBOX_SCRIPT = ELECTRON / "linux" / "install-sandbox-profile.sh"


def _unpacked(tmp_path: Path) -> Path:
    """An unpacked build: the binary and, beside it, the script the postPackage hook puts there."""
    folder = tmp_path / "Mightling-linux-arm64"
    folder.mkdir()
    (folder / "Mightling").write_bytes(b"#!/bin/sh\n")
    (folder / "Mightling").chmod(0o755)
    script = folder / "install-sandbox-profile.sh"
    script.write_bytes(SANDBOX_SCRIPT.read_bytes())
    script.chmod(0o755)
    return folder


def _script_env(tmp_path: Path, restricted: bool) -> dict:
    """The script's environment: scratch profiles and sysctl, and stand-ins for sudo and apparmor_parser."""
    profiles = tmp_path / "apparmor.d"
    profiles.mkdir(exist_ok=True)
    sysctl = tmp_path / "restrict"
    sysctl.write_text("1\n" if restricted else "0\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "root.log"
    # The stand-in sudo runs nothing as root: it records its arguments and what it was fed.
    (bin_dir / "sudo").write_text(
        f'#!/bin/sh\necho "$@" >> "{log}"\n[ "$1" = tee ] && cat > "{tmp_path}/written" || true\n'
    )
    (bin_dir / "apparmor_parser").write_text("#!/bin/sh\nexit 0\n")
    for name in ("sudo", "apparmor_parser"):
        (bin_dir / name).chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "MIGHTLING_APPARMOR_DIR": str(profiles),
        "MIGHTLING_USERNS_SYSCTL": str(sysctl),
        # No setuid helper unless a test names one (this machine's Google Chrome has one).
        "MIGHTLING_SANDBOX_HELPERS": "",
    }


def test_an_unpacked_build_carries_its_own_profile_script():
    # The zip (and `out/Mightling-linux-<arch>/`) has no postinst; the script beside the binary
    # writes the profile for the folder it is in. It names the binary app.json names.
    forge = (ELECTRON / "forge.config.js").read_text(encoding="utf-8")
    assert 'path.join(__dirname, "linux", "install-sandbox-profile.sh")' in forge
    assert 'if (process.platform === "linux")' in forge
    script = SANDBOX_SCRIPT.read_text(encoding="utf-8")
    assert f'BINARY="$DIR/{app_config()["executable"]}"' in script
    assert os.access(SANDBOX_SCRIPT, os.X_OK)
    # Its own name: neither the .deb's (`mightling`) nor a checkout's (`mightling-desktop-dev`).
    assert "PROFILE_NAME=mightling-unpacked" in script
    assert 'profile $PROFILE_NAME "$BINARY" flags=(unconfined) {' in script and "userns," in script
    # No SUID helper and no --no-sandbox as a way out.
    assert "--no-sandbox" not in script and "chmod 4755" not in script
    # Without an administrator it names a Chromium browser's own root-owned helper, if one is there.
    assert (
        "/opt/google/chrome/chrome-sandbox" in script and "CHROME_DEVEL_SANDBOX=$HELPER" in script
    )
    assert '[ -u "$HELPER" ]' in script and 'stat -c %u "$HELPER"' in script


def test_the_script_prints_the_commands_before_sudo_runs(tmp_path):
    import subprocess

    folder = _unpacked(tmp_path)
    env = _script_env(tmp_path, restricted=True)
    # A file that names the binary but grants no `userns,` does not count.
    (tmp_path / "apparmor.d" / "stale").write_text(
        f'profile stale "{folder.resolve()}/Mightling" {{\n}}\n'
    )
    result = subprocess.run(
        [str(folder / "install-sandbox-profile.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    binary = str(folder.resolve() / "Mightling")
    assert "sudo tee /etc/apparmor.d/mightling-unpacked" in result.stdout
    assert "sudo apparmor_parser -r -T -W /etc/apparmor.d/mightling-unpacked" in result.stdout
    assert "Without an administrator" not in result.stdout
    assert (tmp_path / "root.log").read_text().splitlines() == [
        "tee /etc/apparmor.d/mightling-unpacked",
        "apparmor_parser -r -T -W /etc/apparmor.d/mightling-unpacked",
    ]
    written = (tmp_path / "written").read_text()
    assert (
        f'profile mightling-unpacked "{binary}" flags=(unconfined) {{' in written
        and "userns," in written
    )


def test_the_script_changes_nothing_when_nothing_is_needed(tmp_path):
    import subprocess

    folder = _unpacked(tmp_path)
    script = str(folder / "install-sandbox-profile.sh")
    # --print: the commands and the profile, and no sudo.
    env = _script_env(tmp_path, restricted=True)
    printed = subprocess.run(
        [script, "--print"], env=env, capture_output=True, text=True, timeout=30
    )
    assert printed.returncode == 0 and "profile mightling-unpacked" in printed.stdout
    # Already covered (the .deb's profile, say): a second attachment would conflict.
    (tmp_path / "apparmor.d" / "mightling").write_text(
        f'profile mightling "{folder.resolve()}/Mightling" flags=(unconfined) {{\n  userns,\n}}\n'
    )
    covered = subprocess.run([script], env=env, capture_output=True, text=True, timeout=30)
    assert covered.returncode == 0 and "Nothing to do" in covered.stdout
    # A kernel that does not restrict user namespaces needs no profile.
    unrestricted = subprocess.run(
        [script],
        env=_script_env(tmp_path, restricted=False),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert unrestricted.returncode == 0 and "does not restrict" in unrestricted.stdout
    assert not (tmp_path / "root.log").exists()
    # No binary beside it: refused.
    (folder / "Mightling").unlink()
    assert (
        subprocess.run([script], env=env, capture_output=True, text=True, timeout=30).returncode
        == 1
    )


def test_no_two_modules_differ_only_in_case():
    # Windows and macOS file systems ignore case, so `./SideChat` there resolves to sidechat.ts
    # before SideChat.tsx and the UI does not build (windows.yml, 2026-10-27).
    suffixes = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    stems: dict[tuple[Path, str], set[str]] = {}
    for root in (Path(UI_DIR) / "src", ELECTRON / "src"):
        for path in root.rglob("*"):
            stem = path.name.split(".")[0]
            # A test file is never imported, so only modules can be mistaken for one another.
            if (
                path.suffix in suffixes
                and "node_modules" not in path.parts
                and ".test." not in path.name
            ):
                stems.setdefault((path.parent, stem.lower()), set()).add(stem)
    clashes = {str(folder / key): names for (folder, key), names in stems.items() if len(names) > 1}
    assert not clashes


def test_a_launcher_left_by_an_earlier_name_that_cannot_be_removed_does_not_fail_registration(
    tmp_path,
):
    # An entry an install under sudo left root-owned must not undo the registration just written.
    from dreamference.chat import desktop_runner

    entries, icons = tmp_path / "applications", tmp_path / "icons"
    entries.mkdir()
    icons.mkdir()
    (entries / "puffin-app.desktop").write_text("[Desktop Entry]\n")
    with (
        patch.object(desktop_runner, "DESKTOP_ENTRY_DIR", str(entries)),
        patch.object(desktop_runner, "ICON_DIR", str(icons)),
        patch.object(DesktopRunner, "binary_path", return_value="/opt/Mightling"),
        patch("subprocess.run"),
        patch.object(desktop_runner.os, "remove", side_effect=PermissionError(13, "denied")),
    ):
        assert DesktopRunner.install_desktop_entry() is True
