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
