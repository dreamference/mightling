"""Hidden acceptance tests for the click task of scripts/turn_end_compaction_bench.py.

Copied into the scratch clone of click 8.1.7 after the session ends, run from its root (pytest
imports `src/`), and removed. They accept any reasonable implementation of the four requests: the
parser moved behind a deprecated `click.parser`, `Option(deprecated=...)`, and `CLICK_HELP_WIDTH`.
Not named `test_*` so that this repository's own suite never collects it.
"""
import os
import subprocess
import sys
from pathlib import Path

import click
from click.testing import CliRunner

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _python(*args):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env, timeout=60)


def test_hidden_import_click_does_not_warn():
    done = _python("-W", "error::DeprecationWarning", "-c", "import click")
    assert done.returncode == 0, done.stderr


def test_hidden_click_parser_warns_and_still_works():
    warned = _python("-W", "error::DeprecationWarning", "-c", "import click.parser")
    assert warned.returncode != 0 and "DeprecationWarning" in warned.stderr
    names = _python("-W", "ignore", "-c", "from click.parser import OptionParser, split_arg_string")
    assert names.returncode == 0, names.stderr
    assert (Path(SRC) / "click" / "_optparse.py").is_file()


def test_hidden_deprecated_option_warns_and_marks_help():
    @click.command()
    @click.option("--old", deprecated=True, help="The old way.")
    @click.option("--older", deprecated="Use --new.", help="Older still.")
    def cli(old, older):
        click.echo(f"old={old} older={older}")

    result = CliRunner(mix_stderr=False).invoke(cli, ["--old", "x", "--older", "y"])
    assert result.exit_code == 0, result.output
    assert "old=x older=y" in result.stdout
    assert "deprecated" in result.stderr.lower() and "Use --new." in result.stderr
    quiet = CliRunner(mix_stderr=False).invoke(cli, [])
    assert "deprecated" not in quiet.stderr.lower()
    help_text = CliRunner().invoke(cli, ["--help"]).output
    assert "(DEPRECATED)" in help_text


def test_hidden_help_width_from_environment(monkeypatch):
    @click.command(help="word " * 60)
    def cli():
        pass

    monkeypatch.setenv("CLICK_HELP_WIDTH", "40")
    narrow = CliRunner().invoke(cli, ["--help"]).output
    assert max(len(line) for line in narrow.splitlines()) <= 40
    monkeypatch.setenv("CLICK_HELP_WIDTH", "120")
    wide = CliRunner().invoke(cli, ["--help"]).output
    assert max(len(line) for line in wide.splitlines()) > 40
    explicit = CliRunner().invoke(cli, ["--help"], terminal_width=60).output
    assert max(len(line) for line in explicit.splitlines()) <= 60
