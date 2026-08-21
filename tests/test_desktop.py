import json
import os
from unittest.mock import patch

from dreamference.chat import DesktopInstaller, DesktopRunner
from dreamference.chat.desktop_runner import DESKTOP_PROJECT_DIR


def test_window_points_at_the_local_deployment_rather_than_a_bundled_copy():
    # The desktop app and the browser render the same server, which is what keeps every patch
    # `dream onyx configure` applies showing up in both without being ported.
    with open(os.path.join(DESKTOP_PROJECT_DIR, "src-tauri", "tauri.conf.json")) as handle:
        config = json.load(handle)

    window = config["app"]["windows"][0]
    assert window["url"].startswith("http://localhost:3000")
    assert config["productName"] == "Puffin"
    assert config["identifier"] == "dev.dreamference.puffin"


def test_bundle_icons_exist_at_the_sizes_tauri_names():
    # The icon set is rendered from the same mark as the favicon, so a missing size means the
    # launcher entry falls back to a blank square.
    with open(os.path.join(DESKTOP_PROJECT_DIR, "src-tauri", "tauri.conf.json")) as handle:
        config = json.load(handle)

    for relative in config["bundle"]["icon"]:
        assert os.path.exists(os.path.join(DESKTOP_PROJECT_DIR, "src-tauri", relative)), relative


def test_run_refuses_when_the_server_is_down():
    # A window opened against a stopped Onyx shows a connection error with no hint of what to
    # start, so the health check comes before the toolchain.
    with patch.object(DesktopRunner, "onyx_is_up", return_value=False), \
         patch.object(DesktopRunner, "_ensure_toolchain") as toolchain, \
         patch("subprocess.call") as call:
        assert DesktopRunner.run() == 1
        toolchain.assert_not_called()
        call.assert_not_called()


def test_run_stops_when_the_system_packages_cannot_be_installed():
    # Patching `install_system_packages` rather than `has_webview_headers` is deliberate: the
    # latter would let the real method through and shell out to `sudo apt-get` from a unit test.
    with patch.object(DesktopRunner, "onyx_is_up", return_value=True), \
         patch.object(DesktopInstaller, "install_system_packages", return_value=False), \
         patch.object(DesktopInstaller, "install_rust") as rust, \
         patch("subprocess.call") as call:
        assert DesktopRunner.run() == 1
        rust.assert_not_called()
        call.assert_not_called()


def test_the_password_prompt_comes_before_the_large_download():
    # Asking for sudo after a several-hundred-megabyte Rust download would be a poor order to fail
    # in, so the system packages are installed first.
    order = []
    with patch.object(DesktopInstaller, "install_system_packages",
                      side_effect=lambda: order.append("apt") or True), \
         patch.object(DesktopInstaller, "install_rust",
                      side_effect=lambda: order.append("rust") or True), \
         patch.object(DesktopInstaller, "install_tauri_cli",
                      side_effect=lambda: order.append("cli") or True):
        assert DesktopRunner.install() == 0

    assert order == ["apt", "rust", "cli"]


def test_system_package_install_is_announced_and_skipped_when_already_present():
    # It is the one step that changes the machine outside this user's home directory, so the
    # command is printed before it runs -- and not run at all once the headers are there.
    with patch.object(DesktopInstaller, "has_webview_headers", return_value=True), \
         patch("subprocess.run") as run:
        assert DesktopInstaller.install_system_packages() is True
        run.assert_not_called()


def test_build_does_not_require_the_server():
    # The shell holds a URL, not a copy of the UI, so there is nothing to fetch at build time.
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "_tauri_command", return_value=["tauri", "build"]), \
         patch.object(DesktopRunner, "onyx_is_up") as up, \
         patch("subprocess.call", return_value=0) as call:
        assert DesktopRunner.build() == 0
        up.assert_not_called()
        assert call.call_args[1]["cwd"] == DESKTOP_PROJECT_DIR


def test_a_rustup_installed_this_run_is_put_on_path():
    # rustup writes to ~/.cargo/bin without touching the PATH of the shell that ran it, so a build
    # straight after a successful install would otherwise fail to find cargo.
    from dreamference.chat.desktop_installer import CARGO_BIN

    with patch("os.path.isdir", return_value=True), \
         patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False):
        environment = DesktopRunner._environment()

    assert environment["PATH"].startswith(CARGO_BIN)


def test_toolchain_targets_the_webkit_series_tauri_v2_links_against():
    # v1 used webkit2gtk-4.0. Installing the wrong series produces a linker error that reads like a
    # missing library rather than a wrong version.
    from dreamference.chat.desktop_installer import LINUX_BUILD_PACKAGES

    assert "libwebkit2gtk-4.1-dev" in LINUX_BUILD_PACKAGES
    assert "libwebkit2gtk-4.0-dev" not in LINUX_BUILD_PACKAGES
    assert DesktopInstaller.header_install_command().startswith("sudo apt install -y ")


def test_cargo_is_found_in_the_rustup_location_as_well_as_on_path():
    from dreamference.chat.desktop_installer import CARGO_BIN

    with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True):
        assert DesktopInstaller.cargo_path() == os.path.join(CARGO_BIN, "cargo")


def test_webview_environment_lives_in_the_binary_not_the_launcher():
    # A .desktop entry, an AppImage AppRun or a direct execution all bypass the Python launcher, so
    # both settings are applied in main.rs before Tauri starts the webview. Without them the window
    # either never appears (DMABUF/DRM refusal) or comes up dark, showing none of the styling --
    # every rule in onyx_ui_overrides is scoped `html:not(.dark)`.
    import dreamference.chat.desktop_runner as runner

    assert not hasattr(runner, "WEBVIEW_ENV")

    source = open(os.path.join(DESKTOP_PROJECT_DIR, "src-tauri", "src", "main.rs")).read()
    assert "WEBKIT_DISABLE_DMABUF_RENDERER" in source
    assert "GTK_THEME" in source
    # Only filled in when unset, so either can still be overridden from the shell.
    assert "var_os(key).is_none()" in source


def test_desktop_entry_matches_the_window_class_gnome_sees():
    # GNOME resolves a running window to its entry through WM_CLASS; with no match it shows an
    # unnamed generic icon in the dock, which is what it was doing.
    from dreamference.chat.desktop_runner import DESKTOP_ENTRY_NAME, WINDOW_CLASS

    assert WINDOW_CLASS == "Puffin-desktop"
    # Tao derives the class from the binary name, so the file is named after the instance too.
    assert DESKTOP_ENTRY_NAME == "puffin-desktop.desktop"


def test_desktop_entry_is_not_written_before_anything_is_built():
    # The Exec line has to point at a binary; on a first run there is not one yet.
    with patch.object(DesktopRunner, "binary_path", return_value=None), \
         patch("builtins.open") as opened:
        assert DesktopRunner.install_desktop_entry() is False
        opened.assert_not_called()


def test_entry_exec_carries_no_environment():
    # The webview settings live in the binary, so a launcher-started window behaves exactly like
    # one started from a shell. An Exec line prefixed with env vars would mean two places to fix.
    import inspect

    source = inspect.getsource(DesktopRunner.install_desktop_entry)
    assert "Exec={binary}" in source
    assert "WEBKIT_DISABLE_DMABUF_RENDERER" not in source


def test_cache_is_cleared_without_signing_the_user_out():
    # Onyx serves stylesheets `immutable` under filenames that never change, so a patched sheet is
    # invisible to a cached copy. Only the HTTP cache goes -- `cookies` sits beside it, and taking
    # the whole data directory (which is what clears it by hand) logs the user out.
    from dreamference.chat.desktop_runner import WEBVIEW_CACHE_DIR_NAME

    assert WEBVIEW_CACHE_DIR_NAME == "WebKitCache"
    with patch.object(DesktopRunner, "_app_identifier", return_value="dev.dreamference.puffin"), \
         patch("os.path.isdir", return_value=True), \
         patch("shutil.rmtree") as rmtree:
        assert DesktopRunner.clear_webview_cache() is True

    removed = rmtree.call_args[0][0]
    assert removed.endswith(os.path.join("dev.dreamference.puffin", "WebKitCache"))
    assert "cookies" not in removed


def test_identifier_comes_from_the_tauri_config():
    # Repeating it here would let the data directory drift from the one the app really uses.
    assert DesktopRunner._app_identifier() == "dev.dreamference.puffin"
