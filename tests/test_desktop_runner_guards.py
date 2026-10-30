"""The desktop runner removes no cache it cannot place, and stamps no install that did not happen."""

from pathlib import Path
from unittest.mock import patch

import pytest

from dreamference.chat.desktop_runner import WEBVIEW_CACHE_DIR_NAME, DesktopRunner


@pytest.mark.parametrize("identifier", [None, ""])
def test_without_the_apps_identifier_no_cache_is_removed(identifier):
    # Without it the path would be ~/.local/share/Cache, which is not the app's.
    shared = Path("~/.local/share").expanduser() / WEBVIEW_CACHE_DIR_NAME
    shared.mkdir(parents=True)
    with (
        patch.object(DesktopRunner, "_app_identifier", return_value=identifier),
        patch("shutil.rmtree", side_effect=AssertionError("removed a cache")),
    ):
        assert DesktopRunner.clear_webview_cache() is False
    assert shared.is_dir()


def test_an_app_without_a_cache_folder_has_nothing_removed():
    app = Path("~/.local/share").expanduser() / "dev.dreamference.mightling"
    app.mkdir(parents=True)
    (app / "Cookies").write_text("session")
    with (
        patch.object(DesktopRunner, "_app_identifier", return_value="dev.dreamference.mightling"),
        patch("shutil.rmtree", side_effect=AssertionError("removed a cache")),
    ):
        assert DesktopRunner.clear_webview_cache() is False
    assert (app / "Cookies").read_text() == "session"


def test_an_install_that_left_no_node_modules_is_not_stamped(tmp_path):
    (tmp_path / "package-lock.json").write_text("{}")
    DesktopRunner.mark_installed(tmp_path)
    assert not (tmp_path / "node_modules").exists()
    assert not DesktopRunner.packages_current(tmp_path)
