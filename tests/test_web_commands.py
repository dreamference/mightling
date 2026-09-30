"""The agent's web commands are Rust binaries (puffin-web-rs/), not console scripts of this virtualenv."""

import os

import pytest

from dreamference.cli import DreamferenceCLIController
from dreamference.runner import codex_branded_builder as builder_module

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_puffin_admin_no_longer_has_a_search_subcommand():
    with pytest.raises(SystemExit):
        DreamferenceCLIController.build_parser().parse_args(["search", "q"])


def test_the_web_commands_are_not_console_scripts():
    # A console script would put the Python implementation back on PATH: in a shell with this
    # virtualenv active, `.venv/bin` comes before `~/.local/bin`, so the model would run it
    # instead of the binary the builder installs.
    with open(os.path.join(REPO_ROOT, "setup.py")) as handle:
        setup = handle.read()
    assert "puffin-search=" not in setup and "puffin-fetch=" not in setup


def test_the_builder_installs_both_web_commands():
    assert builder_module.WEB_BIN_NAMES == ("puffin-search", "puffin-fetch")
    with open(os.path.join(builder_module.WEB_CRATE_DIR, "Cargo.toml")) as handle:
        manifest = handle.read()
    for name in builder_module.WEB_BIN_NAMES:
        assert f'name = "{name}"' in manifest
