"""The agent's web commands are Rust binaries (mling-web-rs/), not console scripts of this virtualenv."""

import os

import pytest

from dreamference.cli import DreamferenceCLIController
from dreamference.runner import codex_branded_builder as builder_module

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("retired", [["search", "q"], ["fetch", "https://example.com"]])
def test_mightling_admin_no_longer_has_the_web_subcommands(retired):
    with pytest.raises(SystemExit):
        DreamferenceCLIController.build_parser().parse_args(retired)


def test_the_web_commands_are_not_console_scripts():
    # A console script would put the Python implementation back on PATH: in a shell with this
    # virtualenv active, `.venv/bin` comes before `~/.local/bin`, so the model would run it
    # instead of the binary the builder installs.
    with open(os.path.join(REPO_ROOT, "setup.py")) as handle:
        setup = handle.read()
    assert "mling-search=" not in setup and "mling-fetch=" not in setup


def test_the_builder_installs_both_web_commands():
    assert builder_module.WEB_BIN_NAMES == ("mling-search", "mling-fetch")
    with open(os.path.join(builder_module.WEB_CRATE_DIR, "Cargo.toml")) as handle:
        manifest = handle.read()
    for name in builder_module.WEB_BIN_NAMES:
        assert f'name = "{name}"' in manifest
