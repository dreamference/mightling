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


def test_run_stops_at_the_headers_because_installing_them_needs_root():
    # Rust and the CLI are per-user and installed automatically; the system headers are not, and a
    # tool that silently sudo's is worse than one that says what it needs.
    with patch.object(DesktopRunner, "onyx_is_up", return_value=True), \
         patch.object(DesktopInstaller, "has_webview_headers", return_value=False), \
         patch.object(DesktopInstaller, "install_rust") as rust, \
         patch("subprocess.call") as call:
        assert DesktopRunner.run() == 1
        rust.assert_not_called()
        call.assert_not_called()


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
