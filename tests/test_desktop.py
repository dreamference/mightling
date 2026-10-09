"""The desktop app's shell (specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md): what `ling-admin
desktop` builds and installs, and what the Electron project must keep."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from dreamference.chat import DesktopInstaller, DesktopRunner
from dreamference.chat.desktop_runner import DESKTOP_PROJECT_DIR, ELECTRON_DIR, UI_DIR

ELECTRON = Path(ELECTRON_DIR)


def app_config() -> dict:
    with open(ELECTRON / "app.json") as handle:
        return json.load(handle)


def electron_sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted((ELECTRON / "src").glob("*.ts")))


def test_the_app_names_one_window_and_no_web_page():
    # Ask (the menu's former Chat) is a view of the app window, served from app:// with the
    # policy layer in the main process (specs/DREAMFERENCE_MIGHTLING_ASK.md §18.6): no window on
    # `ling web` and none on Onyx.
    config = app_config()
    assert "chat" not in config and "work" in config and "csp" in config
    assert config["productName"] == "Mightling"
    assert config["identifier"] == "dev.dreamference.mightling"
    assert config["command"] == "ling-app" and config["scheme"] == "mightling"


def test_bundle_icons_exist_at_the_sizes_the_package_names():
    # The icon set is rendered from the same mark as the favicon, so a missing size means the
    # launcher entry falls back to a blank square. `icon.png` is the .deb's and the packager's;
    # 32x32 is the tray's.
    for name in ("icon.png", "32x32.png", "128x128.png", "128x128@2x.png"):
        assert (ELECTRON / "icons" / name).is_file(), name


def test_electron_is_pinned_exactly_and_stock():
    # The Codex app runs on the upstream vendor's own Electron build; Mightling runs the published
    # one, pinned to a patch release and bumped deliberately (§3, decision 2).
    with open(ELECTRON / "package.json") as handle:
        package = json.load(handle)
    assert package["devDependencies"]["electron"] == "42.11.11"
    assert package["main"] == ".vite/build/early-bootstrap.js"
    with open(UI_DIR / "package.json") as handle:
        assert not any(name.startswith("@tauri-apps") for name in json.load(handle)["devDependencies"])


def test_no_gpu_flags_no_toolkit_environment_no_sandbox_switch():
    # Electron's defaults, as the Codex app runs (§3, decision 1): the WebKitGTK workarounds died
    # with the Tauri shell, and Chromium's sandbox stays on.
    source = electron_sources()
    for forbidden in ("WEBKIT_", "GTK_THEME", "disable-gpu", "ignore-gpu-blocklist", "no-sandbox", "analytics-default-enabled"):
        assert forbidden not in source, forbidden
    assert "sandbox: true" in source and "nodeIntegration: false" in source and "contextIsolation: true" in source


def test_the_egress_switches_are_always_applied():
    # What keeps Chromium quiet on the network (§3, decision 6).
    egress = (ELECTRON / "src" / "egress.ts").read_text(encoding="utf-8")
    for switch in ("disable-background-networking", "disable-component-update", "AutofillServerCommunication", "NetworkTimeServiceQuerying", "MediaRouter"):
        assert switch in egress, switch
    bootstrap = (ELECTRON / "src" / "early-bootstrap.ts").read_text(encoding="utf-8")
    assert "applySwitches()" in bootstrap


def test_the_fuses_are_the_codex_apps():
    forge = (ELECTRON / "forge.config.js").read_text(encoding="utf-8")
    for fuse, state in (("RunAsNode", "false"), ("EnableNodeOptionsEnvironmentVariable", "false"), ("EnableNodeCliInspectArguments", "false"),
                        ("GrantFileProtocolExtraPrivileges", "false"), ("EnableCookieEncryption", "true"),
                        ("EnableEmbeddedAsarIntegrityValidation", "true"), ("OnlyLoadAppFromAsar", "true"), ("WasmTrapHandlers", "true")):
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
    assert "cat > /usr/bin/ling-app" in postinst and 'exec /usr/lib/mightling/Mightling "$@"' in postinst
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
    source = (Path(DESKTOP_PROJECT_DIR).parent / "dreamference" / "chat" / "desktop_runner.py").read_text(encoding="utf-8")
    assert "onyx_is_up" not in source and "3000" not in source and "chat start" not in source


def test_run_stops_when_the_packages_cannot_be_installed():
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "install_packages", return_value=False), \
         patch("subprocess.call") as call:
        assert DesktopRunner.run() == 1
        call.assert_not_called()


def test_install_writes_the_profile_only_where_the_namespace_is_refused():
    # The one step that changes the machine outside this user's home, and only when needed.
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "install_packages", return_value=True), \
         patch.object(DesktopRunner, "binary_path", return_value=None), \
         patch.object(DesktopInstaller, "userns_allowed", return_value=True), \
         patch.object(DesktopInstaller, "install_apparmor_profile") as profile:
        assert DesktopRunner.install() == 0
        profile.assert_not_called()
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "install_packages", return_value=True), \
         patch.object(DesktopRunner, "binary_path", return_value=None), \
         patch.object(DesktopRunner, "dev_binaries", return_value=["/opt/electron"]), \
         patch.object(DesktopInstaller, "userns_allowed", return_value=False), \
         patch.object(DesktopInstaller, "install_apparmor_profile", return_value=True) as profile:
        assert DesktopRunner.install() == 0
        profile.assert_called_once_with(["/opt/electron"])


def test_the_profile_install_is_announced_before_sudo_runs(capsys):
    with patch("shutil.which", return_value="/usr/bin/x"), \
         patch("subprocess.run") as run:
        run.return_value.returncode = 0
        assert DesktopInstaller.install_apparmor_profile([os.path.abspath(__file__)]) is True
    out = capsys.readouterr().out
    assert "sudo tee /etc/apparmor.d/mightling-desktop-dev" in out
    assert run.call_args_list[0].args[0][:2] == ["sudo", "tee"]
    assert run.call_args_list[1].args[0][:2] == ["sudo", "apparmor_parser"]


def test_build_does_not_require_the_server():
    # Ask is served by the bundled `ling` itself, so there is nothing to fetch at build time.
    commands = []
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "install_packages", return_value=True), \
         patch.object(DesktopRunner, "copy_bundled_binaries", return_value=True), \
         patch.object(DesktopRunner, "install_desktop_entry"), \
         patch.object(DesktopRunner, "_npm", side_effect=lambda args, cwd: commands.append((args, cwd)) or True):
        assert DesktopRunner.build() == 0
    assert commands == [(["run", "make"], ELECTRON_DIR)]


def test_build_needs_the_agent_it_bundles():
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "install_packages", return_value=True), \
         patch.object(DesktopRunner, "copy_bundled_binaries", return_value=False), \
         patch.object(DesktopRunner, "_npm") as npm:
        assert DesktopRunner.build() == 1
        npm.assert_not_called()


def test_the_bundled_binaries_come_from_the_installed_build(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ling", "codex-code-mode-host"):
        (bin_dir / name).write_bytes(b"#!/bin/sh\n")
    resources = tmp_path / "resources"
    with patch("dreamference.runner.codex_branded_builder.CodexBrandedBuilder.executable_path", return_value=str(bin_dir / "ling")), \
         patch("dreamference.chat.desktop_runner.RESOURCES_DIR", resources), \
         patch("shutil.which", return_value=None):
        assert DesktopRunner.copy_bundled_binaries() is True
    assert sorted(p.name for p in resources.iterdir()) == ["codex-code-mode-host", "ling"]
    (bin_dir / "ling").unlink()
    with patch("dreamference.runner.codex_branded_builder.CodexBrandedBuilder.executable_path", return_value=str(bin_dir / "ling")), \
         patch("dreamference.chat.desktop_runner.RESOURCES_DIR", resources):
        assert DesktopRunner.copy_bundled_binaries() is False


def test_a_rustup_installed_this_run_is_put_on_path():
    # The Codex build shares this helper: rustup writes to ~/.cargo/bin without touching the PATH
    # of the shell that ran it.
    from dreamference.chat.desktop_installer import CARGO_BIN

    with patch("os.path.isdir", return_value=True), \
         patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False):
        environment = DesktopRunner._environment()

    assert environment["PATH"].startswith(CARGO_BIN)


def test_cargo_is_found_in_the_rustup_location_as_well_as_on_path():
    from dreamference.chat.desktop_installer import CARGO_BIN

    with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True):
        assert DesktopInstaller.cargo_path() == os.path.join(CARGO_BIN, "cargo")


def test_the_toolchain_is_node_only():
    with patch.object(DesktopInstaller, "node_major", return_value=22), patch("shutil.which", return_value="/usr/bin/npm"):
        assert DesktopInstaller.missing_prerequisites() == []
    with patch.object(DesktopInstaller, "node_major", return_value=18), patch("shutil.which", return_value=None):
        assert DesktopInstaller.missing_prerequisites() == ["node", "npm"]


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
    with patch.object(desktop_runner, "DESKTOP_ENTRY_DIR", str(entries)), \
         patch.object(desktop_runner, "ICON_DIR", str(icons)), \
         patch.object(DesktopRunner, "binary_path", return_value="/opt/Mightling"), \
         patch("dreamference.chat.onyx_brand_assets.OnyxBrandAssets.render_app_icon"), \
         patch("subprocess.run"):
        assert DesktopRunner.install_desktop_entry() is True

    assert sorted(p.name for p in entries.iterdir()) == ["ling-app.desktop"]
    assert list(icons.iterdir()) == []
    entry = (entries / "ling-app.desktop").read_text()
    assert "Exec=/opt/Mightling %U\n" in entry and "MimeType=x-scheme-handler/mightling;" in entry


def test_desktop_entry_is_not_written_before_anything_is_built():
    # The Exec line has to point at a binary; on a first run there is not one yet.
    with patch.object(DesktopRunner, "binary_path", return_value=None), \
         patch("builtins.open") as opened:
        assert DesktopRunner.install_desktop_entry() is False
        opened.assert_not_called()


def test_entry_exec_carries_no_environment():
    # A launcher-started window behaves exactly like one started from a shell.
    import inspect

    source = inspect.getsource(DesktopRunner.install_desktop_entry)
    assert "Exec={binary} %U" in source
    assert "WEBKIT_DISABLE_DMABUF_RENDERER" not in source


def test_cache_is_cleared_without_signing_the_user_out():
    # Only Chromium's HTTP cache goes -- `Cookies` sits beside it.
    from dreamference.chat.desktop_runner import WEBVIEW_CACHE_DIR_NAME

    assert WEBVIEW_CACHE_DIR_NAME == "Cache"
    with patch.object(DesktopRunner, "_app_identifier", return_value="dev.dreamference.mightling"), \
         patch("os.path.isdir", return_value=True), \
         patch("shutil.rmtree") as rmtree:
        assert DesktopRunner.clear_webview_cache() is True

    removed = rmtree.call_args[0][0]
    assert removed.endswith(os.path.join("dev.dreamference.mightling", "Cache"))
    assert "Cookies" not in removed
    # The launcher's `ling app` empties the same folder.
    app_rs = (Path(DESKTOP_PROJECT_DIR).parent / "ling-rs" / "src" / "app.rs").read_text(encoding="utf-8")
    assert 'const WEBVIEW_CACHE_DIR: &str = "Cache";' in app_rs


def test_identifier_comes_from_the_app_config():
    # Repeating it here would let the data directory drift from the one the app really uses.
    assert DesktopRunner._app_identifier() == "dev.dreamference.mightling"
    bootstrap = (ELECTRON / "src" / "early-bootstrap.ts").read_text(encoding="utf-8")
    assert 'app.setPath("userData", path.join(home, ".local", "share", appConfig.identifier))' in bootstrap


def test_window_background_is_painted_rather_than_left_black():
    # A repaint gap shows the window's own background. The app window's is transparent (its title
    # bar overlay needs that), so the page paints the white itself.
    styles = (ELECTRON.parent / "ui" / "src" / "styles.css").read_text(encoding="utf-8")
    assert "body { background: #fff; }" in styles


def test_run_packages_the_app_and_opens_the_packaged_binary():
    # Not `electron-forge start`: Work's page is served by app:// from the built renderer, which a
    # dev server run does not produce; the window that opens is the one the .deb ships.
    commands = []
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "install_packages", return_value=True), \
         patch.object(DesktopInstaller, "userns_allowed", return_value=True), \
         patch.object(DesktopRunner, "copy_bundled_binaries", return_value=True), \
         patch.object(DesktopRunner, "_npm", side_effect=lambda args, cwd: commands.append(args) or True), \
         patch.object(DesktopRunner, "binary_path", return_value="/opt/out/Mightling-linux-arm64/Mightling"), \
         patch.object(DesktopRunner, "install_desktop_entry"), \
         patch.object(DesktopRunner, "clear_webview_cache"), \
         patch("subprocess.call", return_value=0) as call:
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
