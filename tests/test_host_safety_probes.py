"""What host setup reads from the machine when a probe or a file fails.

The sudo probe never prompts and reads a failure as "sudo would ask"; a sandbox probe that cannot
start reports nothing; a file is taken for this machine's swap only when fstab or blkid says so;
and a reading that fails is the empty or zero answer that makes setup cautious.
"""

import io
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


@pytest.fixture
def no_fstab(monkeypatch, tmp_path):
    monkeypatch.setattr(host_safety_setup, "FSTAB", str(tmp_path / "missing"))
    monkeypatch.setattr(host_safety_setup.shutil, "which", lambda name: "/usr/sbin/" + name)


@pytest.mark.parametrize(("blkid", "expected"), [("swap\n", True), ("ext4\n", False), ("", False)])
def test_without_fstab_only_blkid_naming_swap_makes_a_file_the_swap_file(
    monkeypatch, no_fstab, blkid, expected
):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=blkid)

    monkeypatch.setattr(host_safety_setup.subprocess, "run", run)
    assert HostSafetySetup.is_swap_file("/swap.img") is expected
    assert calls == [["blkid", "-p", "-o", "value", "-s", "TYPE", "/swap.img"]]


@pytest.mark.parametrize("error", [OSError("no blkid"), subprocess.TimeoutExpired(["blkid"], 10)])
def test_a_blkid_that_fails_does_not_make_a_file_the_swap_file(monkeypatch, no_fstab, error):
    def run(command, **kwargs):
        raise error

    monkeypatch.setattr(host_safety_setup.subprocess, "run", run)
    assert HostSafetySetup.is_swap_file("/swap.img") is False


def _shadow_open(monkeypatch, content):
    def fake(path, *args, **kwargs):
        if isinstance(content, Exception):
            raise content
        return io.StringIO(content)

    monkeypatch.setattr(host_safety_setup, "open", fake, raising=False)


def test_unreadable_proc_swaps_means_no_swap_areas(monkeypatch):
    _shadow_open(monkeypatch, PermissionError("/proc/swaps"))
    assert HostSafetySetup.swap_areas() == []


@pytest.mark.parametrize(
    ("meminfo", "expected"),
    [
        ("MemTotal: 134217728 kB\nMemAvailable: 8388608 kB\n", 8.0),
        ("MemTotal: 134217728 kB\n", 0.0),
        ("MemAvailable: plenty kB\n", 0.0),
        ("MemAvailable:\n", 0.0),
        (OSError("/proc/meminfo"), 0.0),
    ],
)
def test_available_memory_is_zero_unless_meminfo_states_it(monkeypatch, meminfo, expected):
    _shadow_open(monkeypatch, meminfo)
    assert HostSafetySetup._mem_available_gb() == expected


def test_a_missing_file_or_disk_reads_as_zero_gigabytes(tmp_path):
    sized = tmp_path / "swap.img"
    with sized.open("wb") as handle:
        handle.truncate(2 * 1024**3)
    assert HostSafetySetup._file_gb(str(sized)) == 2.0
    assert HostSafetySetup._file_gb(str(tmp_path / "missing")) == 0.0
    assert HostSafetySetup._disk_free_gb(str(tmp_path / "missing")) == 0.0
