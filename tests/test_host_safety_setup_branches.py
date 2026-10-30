"""The decisions of `HostSafetySetup` that its stubbed host (tests/test_release_install.py) never reaches."""
import io
import subprocess

import pytest

from dreamference.vllm_server import host_safety_setup
from dreamference.vllm_server.host_safety_setup import HostSafetySetup
from dreamference.vllm_server.sandbox_prerequisite import SandboxPrerequisite

# tests/conftest.py stubs the probe for every test; these tests are about the real one.
REAL_HELPER_WORKS = HostSafetySetup.__dict__["helper_works"]


def _which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _files(monkeypatch, contents):
    """`open` in the module reads `contents[path]`, or fails as a missing file does."""
    def fake_open(path, *args, **kwargs):
        if path not in contents:
            raise FileNotFoundError(path)
        return io.StringIO(contents[path])
    monkeypatch.setattr(host_safety_setup, "open", fake_open, raising=False)


# -- root_mode and root_argv -----------------------------------------------------------------------


def test_a_root_process_runs_its_commands_without_sudo(monkeypatch):
    monkeypatch.setattr(host_safety_setup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(host_safety_setup.shutil, "which", _which())

    assert HostSafetySetup.root_mode(yes=True) == "root"
    assert HostSafetySetup.root_argv(["swapon", "/swapfile"], "root") == ["swapon", "/swapfile"]


def test_without_sudo_root_cannot_be_had(monkeypatch):
    monkeypatch.setattr(host_safety_setup.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(host_safety_setup.shutil, "which", _which())
    monkeypatch.setattr(HostSafetySetup, "passwordless_sudo", classmethod(lambda cls: True))

    assert HostSafetySetup.root_mode(yes=False, helper=True) is None


def test_the_other_modes_put_sudo_in_front():
    assert HostSafetySetup.root_argv(["swapon", "-a"], "sudo -n") == ["sudo", "-n", "swapon", "-a"]
    assert HostSafetySetup.root_argv(["swapon", "-a"], "sudo") == ["sudo", "swapon", "-a"]


# -- helper_works ----------------------------------------------------------------------------------


@pytest.fixture
def real_helper_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(HostSafetySetup, "helper_works", REAL_HELPER_WORKS)
    path = tmp_path / "mightling-node-root"
    monkeypatch.setattr(host_safety_setup, "HELPER_PATH", str(path))
    return path


def test_no_helper_installed_is_no_helper_and_runs_no_sudo(monkeypatch, real_helper_probe):
    monkeypatch.setattr(host_safety_setup.subprocess, "run",
                        lambda *a, **k: pytest.fail("sudo ran for a helper that is not there"))

    assert HostSafetySetup.helper_works() is False


def test_the_helper_works_only_when_sudo_runs_its_probe_without_asking(monkeypatch, real_helper_probe):
    real_helper_probe.write_text("#!/bin/sh\n")
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, run.code)

    monkeypatch.setattr(host_safety_setup.subprocess, "run", run)
    run.code = 0
    assert HostSafetySetup.helper_works() is True
    assert seen == [["sudo", "-n", str(real_helper_probe), "probe"]]
    run.code = 1
    assert HostSafetySetup.helper_works() is False


def test_a_sudo_that_cannot_run_is_no_helper(monkeypatch, real_helper_probe):
    real_helper_probe.write_text("#!/bin/sh\n")

    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(host_safety_setup.subprocess, "run", run)

    assert HostSafetySetup.helper_works() is False


# -- is_swap_file: whether `mkswap` may overwrite the file ---------------------------------------------


def test_a_file_whose_header_says_swap_is_the_swap_file(monkeypatch):
    _files(monkeypatch, {host_safety_setup.FSTAB: "UUID=1 / ext4 defaults 0 1\n"})
    monkeypatch.setattr(host_safety_setup.shutil, "which", _which("blkid"))
    monkeypatch.setattr(host_safety_setup.subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 0, stdout="swap\n"))

    assert HostSafetySetup.is_swap_file("/swapfile") is True


def test_a_file_whose_header_says_something_else_is_not_overwritten(monkeypatch):
    _files(monkeypatch, {})
    monkeypatch.setattr(host_safety_setup.shutil, "which", _which("blkid"))
    monkeypatch.setattr(host_safety_setup.subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 0, stdout="ext4\n"))

    assert HostSafetySetup.is_swap_file("/swapfile") is False


def test_a_header_that_cannot_be_read_is_not_a_swap_file(monkeypatch):
    _files(monkeypatch, {})
    monkeypatch.setattr(host_safety_setup.shutil, "which", _which("blkid"))

    def run(argv, **kwargs):
        raise OSError("blkid")

    monkeypatch.setattr(host_safety_setup.subprocess, "run", run)

    assert HostSafetySetup.is_swap_file("/swapfile") is False


def test_a_commented_out_fstab_line_does_not_make_a_swap_file(monkeypatch):
    _files(monkeypatch, {host_safety_setup.FSTAB: "# /swapfile none swap sw 0 0\n"})
    monkeypatch.setattr(host_safety_setup.shutil, "which", _which())

    assert HostSafetySetup.is_swap_file("/swapfile") is False


# -- what the checks read --------------------------------------------------------------------------


def test_the_sandbox_is_not_judged_without_bubblewrap_or_user_systemd(monkeypatch):
    monkeypatch.setattr(host_safety_setup.subprocess, "run",
                        lambda *a, **k: pytest.fail("nothing can be tried"))
    for present in [("systemd-run",), ("bwrap",)]:
        monkeypatch.setattr(host_safety_setup.shutil, "which", _which(*present))
        assert HostSafetySetup.sandbox_works() is None


def test_a_swaps_line_that_is_short_or_not_numbers_is_skipped(monkeypatch):
    _files(monkeypatch, {"/proc/swaps": (
        "Filename Type Size Used Priority\n"
        "/swapfile file\n"
        "/dev/sda2 partition big 0 -2\n"
        "/swapfile file 16777216 1048576 -3\n")})

    assert HostSafetySetup.swap_areas() == [
        {"name": "/swapfile", "type": "file", "size_gb": 16.0, "used_gb": 1.0}]
