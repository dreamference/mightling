"""What host setup reads from the machine when a probe or a file fails.

The sudo probe never prompts and reads a failure as "sudo would ask"; a sandbox probe that cannot
start reports nothing; a file is taken for this machine's swap only when fstab or blkid says so;
and a reading that fails is the empty or zero answer that makes setup cautious.
"""

import subprocess

import pytest

from dreamference.vllm_server import HostSafetySetup, host_safety_setup

REAL_PASSWORDLESS_SUDO = HostSafetySetup.passwordless_sudo


@pytest.mark.parametrize(("returncode", "expected"), [(0, True), (1, False)])
def test_sudo_counts_as_passwordless_only_when_it_runs_without_asking(
    monkeypatch, returncode, expected
):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(host_safety_setup.subprocess, "run", run)
    assert REAL_PASSWORDLESS_SUDO() is expected
    ((command, kwargs),) = calls
    assert command == ["sudo", "-n", "true"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == host_safety_setup.SUDO_PROBE_SECONDS


@pytest.mark.parametrize(
    "error", [FileNotFoundError("sudo"), subprocess.TimeoutExpired(["sudo"], 10)]
)
def test_a_sudo_that_cannot_be_asked_is_not_passwordless(monkeypatch, error):
    def run(command, **kwargs):
        raise error

    monkeypatch.setattr(host_safety_setup.subprocess, "run", run)
    assert REAL_PASSWORDLESS_SUDO() is False


@pytest.mark.parametrize("error", [OSError("no systemd"), subprocess.TimeoutExpired(["bwrap"], 30)])
def test_a_sandbox_probe_that_cannot_start_reports_nothing(monkeypatch, error):
    def run(command, **kwargs):
        raise error

    monkeypatch.setattr(host_safety_setup.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(host_safety_setup.subprocess, "run", run)
    assert HostSafetySetup.sandbox_works() is None
