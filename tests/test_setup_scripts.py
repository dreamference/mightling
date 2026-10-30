"""The setup guide's scripts and commands (specs/DREAMFERENCE_SETUP.md §3.4, §4.1).

The GB10 scripts run `ling-admin` in the guide's order with the guide's flags, and every
`ling-admin` command the guide names is one the CLI accepts. Nothing here installs, builds or starts anything: `git`,
`python3` and `ling-admin` are stand-ins that write down how they were called.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "specs" / "DREAMFERENCE_SETUP.md"


def run_script(tmp_path, name, *args, host_setup_fails=False):
    """Runs a copy of scripts/`name` from a scratch checkout, with stand-ins on PATH.

    Returns the result and the calls, one `tool args` line each, in order.
    """
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / name, tmp_path / "scripts" / name)
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("git", "python3", "ling-admin"):
        failing = (
            'case "$*" in "host setup") exit 1;; esac\n'
            if tool == "ling-admin" and host_setup_fails
            else ""
        )
        (fake_bin / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n{failing}')
        (fake_bin / tool).chmod(0o755)
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts" / name), *args],
        capture_output=True,
        text=True,
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)},
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize(
    "args, init", [((), "ling-admin init"), (("some-model",), "ling-admin init --model some-model")]
)
def test_install_gb10_runs_the_steps_in_the_guides_order(tmp_path, args, init):
    result, calls = run_script(tmp_path, "install_gb10.sh", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [
        "git submodule update --init codex",
        "python3 -m pip install -e .",
        init,
        "ling-admin host setup",
        "ling-admin codex build",
    ]
    last = result.stdout.strip().splitlines()[-1]
    assert "ling-admin server start" in last and "'ling'" in last


def test_install_gb10_still_builds_ling_when_host_setup_fails(tmp_path):
    # A refused sudo or a host that needs a reboot is reported, and `ling` is still built.
    result, calls = run_script(tmp_path, "install_gb10.sh", host_setup_fails=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[-2:] == ["ling-admin host setup", "ling-admin codex build"]
    assert "Fix the above" in result.stdout


@pytest.mark.parametrize(
    "args, flags",
    [
        ((), []),
        (("m",), ["--model", "m"]),
        (("m", "8001"), ["--model", "m", "--port", "8001"]),
        (
            ("m", "8001", "d", "3"),
            [
                "--model",
                "m",
                "--port",
                "8001",
                "--draft-model",
                "d",
                "--num-speculative-tokens",
                "3",
            ],
        ),
        (("", "8001"), ["--port", "8001"]),  # an empty argument takes the configured value
    ],
)
def test_run_vllm_gb10_passes_each_argument_given_to_server_start(tmp_path, args, flags):
    result, calls = run_script(tmp_path, "run_vllm_gb10.sh", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [" ".join(["ling-admin", "server", "start", *flags])]
    DreamferenceCLIController.build_parser().parse_args(["server", "start", *flags])


def spec_commands():
    """Every `ling-admin …` the guide names, inline or in a shell block, `a|b` spelled out."""
    text = SPEC.read_text()
    found = re.findall(r"`ling-admin ([^`]+)`", text)
    found += re.findall(r"^ling-admin ([^#\n]+)", text, flags=re.MULTILINE)
    commands = set()
    for command in found:
        words = command.split()
        choices = [word.split("|") for word in words]
        variants = [[]]
        for options in choices:
            variants = [[*variant, option] for variant in variants for option in options]
        commands.update(" ".join(variant) for variant in variants)
    return sorted(commands)


def test_the_guide_names_the_commands_it_should():
    assert {
        "init",
        "host setup",
        "host setup --yes",
        "server start",
        "codex build",
        "status",
    } <= set(spec_commands())


# The guide names `ling-admin run` as a command, without the prompt it takes.
REQUIRED_ARGUMENTS = {"run": ["a prompt"]}


@pytest.mark.parametrize("command", spec_commands())
def test_every_command_the_guide_names_parses(command):
    DreamferenceCLIController.build_parser().parse_args(
        command.split() + REQUIRED_ARGUMENTS.get(command, [])
    )
