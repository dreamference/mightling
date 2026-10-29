"""The node's root helper: the one command a role node's sudoers lets its user run without a password.

`ling-admin node provision --role` used to allow `systemctl`, `apt-get`, `apt` and `docker` with
any arguments, which is root to anyone with the node user's shell. The helper does host setup's
automated fixes and nothing else; these tests run the rendered script as the test's own user, so
every refusal is checked before it would ask for root, and nothing here runs sudo or writes /etc.
"""

import os
import subprocess

import pytest

from dreamference.vllm_server import host_safety_setup
from dreamference.vllm_server.host_root_helper import HELPER_PATH, VERBS, HostRootHelper
from dreamference.vllm_server.host_safety_setup import HostSafetySetup
from tests.test_release_install import host as release_host  # noqa: F401

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="the helper would really run as root")


@pytest.fixture
def machine(request):
    """The host stubs of the release install tests: what host setup reads, set per test."""
    return request.getfixturevalue("release_host")


@pytest.fixture
def helper(tmp_path):
    path = tmp_path / "mightling-node-root"
    path.write_text(HostRootHelper.render())

    def run(*argv):
        return subprocess.run(
            ["sh", str(path), *argv], capture_output=True, text=True, timeout=10, check=False
        )

    return run


def test_the_helper_is_a_plain_shell_script_with_its_own_path(tmp_path):
    text = HostRootHelper.render()
    assert text.startswith("#!/bin/sh\n")
    assert "\nset -eu\n" in text and "\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\n" in text
    path = tmp_path / "helper"
    path.write_text(text)
    assert subprocess.run(["sh", "-n", str(path)], check=False).returncode == 0
    assert HELPER_PATH == "/usr/local/sbin/mightling-node-root"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown"],
        ["sh"],
        ["sysstat", "extra"],
        ["swap", "/etc/passwd"],
        ["sysctl"],
        ["sysctl", "kernel.core_pattern"],
        ["sysctl", "vm.min_free_kbytes", "1"],
        ["sysctl", "vm.min_free_kbytes=1"],
        ["docker-group", "root"],
        ["bwrap-profile", "/tmp/x"],
    ],
)
def test_the_helper_refuses_what_it_does_not_know_before_it_asks_for_root(helper, argv):
    result = helper(*argv)
    assert result.returncode == 2, result.stderr
    assert "usage:" in result.stderr and "must run as root" not in result.stderr


@pytest.mark.parametrize("verb", VERBS)
def test_every_verb_it_knows_needs_root(helper, verb):
    argv = [verb, "vm.min_free_kbytes"] if verb == "sysctl" else [verb]
    result = helper(*argv)
    assert result.returncode == 1 and "must run as root" in result.stderr, result.stderr


def test_every_fix_host_setup_hands_the_helper_is_a_verb_it_knows(machine, helper, monkeypatch):
    machine.update(
        sar=False,
        oom_problem="no handler",
        earlyoom=None,
        swap_gb=8.0,
        areas=[{"name": "/swap.img", "type": "file", "size_gb": 8.0, "used_gb": 0.5}],
        sysctl={"vm.min_free_kbytes": 45_056, "vm.watermark_scale_factor": 10},
        docker_access=False,
    )
    steps = HostSafetySetup.steps()
    handed = [argv for step in steps for argv in step.get("helper", [])]
    assert ["sysstat"] in handed and ["earlyoom"] in handed and ["swap"] in handed
    assert ["sysctl", "vm.min_free_kbytes"] in handed and ["docker-group"] in handed
    for argv in handed:
        result = helper(*argv)
        assert result.returncode == 1 and "must run as root" in result.stderr, (argv, result.stderr)


def test_host_setup_runs_its_fixes_through_the_helper_when_that_is_all_sudo_allows(
    machine, monkeypatch, capsys
):
    machine["sysctl"]["vm.watermark_scale_factor"] = 10
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(HostSafetySetup, "helper_works", classmethod(lambda cls: True))
    ran = []

    def fake_run(command, **kwargs):
        ran.append(command)
        machine["sysctl"]["vm.watermark_scale_factor"] = 200
        return subprocess.CompletedProcess(command, 0, "done\n")

    monkeypatch.setattr(host_safety_setup.subprocess, "run", fake_run)
    assert HostSafetySetup.setup(yes=True) is True
    assert ran == [["sudo", "-n", HELPER_PATH, "sysctl", "vm.watermark_scale_factor"]]


def test_a_fix_the_helper_does_not_do_is_left_for_a_terminal(machine, monkeypatch, capsys):
    monkeypatch.setattr(
        HostSafetySetup,
        "_wifi_step",
        classmethod(lambda cls: {"name": "wifi", "why": "it sleeps", "commands": [["nmcli", "x"]]}),
    )
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(HostSafetySetup, "helper_works", classmethod(lambda cls: True))
    monkeypatch.setattr(
        host_safety_setup.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run")
    )
    assert HostSafetySetup.setup(yes=True) is False
    assert "ling-admin host setup` in a terminal" in capsys.readouterr().out


def test_only_host_setup_falls_back_to_the_helper(monkeypatch):
    monkeypatch.setattr(host_safety_setup.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(HostSafetySetup, "helper_works", classmethod(lambda cls: True))
    assert HostSafetySetup.root_mode(yes=True) is None
    assert HostSafetySetup.root_mode(yes=True, helper=True) == "helper"
    monkeypatch.setattr(HostSafetySetup, "passwordless_sudo", classmethod(lambda cls: True))
    assert HostSafetySetup.root_mode(yes=True, helper=True) == "sudo -n"
