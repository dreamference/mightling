"""The readers behind check_host_safety(): /proc, sysctls and systemd, each replaced by a stand-in."""

import io
import subprocess

import pytest

from dreamference.vllm_server import vllm_server_manager as vsm
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager


def _fake_files(monkeypatch, files):
    """Makes the module's open() read `files` (path -> text or bytes, or an exception to raise)."""

    def fake_open(path, mode="r", *args, **kwargs):
        content = files.get(path, FileNotFoundError(path))
        if isinstance(content, BaseException):
            raise content
        return io.BytesIO(content) if "b" in mode else io.StringIO(content)

    monkeypatch.setattr(vsm, "open", fake_open, raising=False)


SWAPS_HEADER = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"


@pytest.mark.parametrize(
    ("files", "expected_gb"),
    [
        # Two swap files count; zram does not, because its pages live in the RAM that is short.
        (
            {
                "/proc/swaps": SWAPS_HEADER
                + "/swap.img file 33554432 0 -2\n"
                + "/dev/zram0 partition 16777216 0 100\n"
                + "/swap2.img file 33554432 0 -3\n"
            },
            64.0,
        ),
        ({"/proc/swaps": SWAPS_HEADER + "/dev/zram0 partition 67108864 0 100\n"}, 0.0),
        ({"/proc/meminfo": "MemTotal: 1 kB\nSwapTotal:       67108864 kB\n"}, 64.0),
        # A /proc/swaps the parser cannot read falls back to meminfo.
        (
            {
                "/proc/swaps": SWAPS_HEADER + "/swap.img file lots 0 -2\n",
                "/proc/meminfo": "SwapTotal: 33554432 kB\n",
            },
            32.0,
        ),
        ({"/proc/meminfo": "SwapTotal:\n"}, 0.0),
        ({}, 0.0),
    ],
)
def test_swap_is_the_disk_backed_total(monkeypatch, files, expected_gb):
    _fake_files(monkeypatch, files)
    assert VLLMServerManager._swap_total_gb() == expected_gb


def test_a_sysctl_reads_as_an_integer_or_not_at_all(monkeypatch):
    _fake_files(
        monkeypatch,
        {
            "/proc/sys/vm/min_free_kbytes": "1048576\n",
            "/proc/sys/vm/watermark_scale_factor": "ten\n",
        },
    )
    assert VLLMServerManager._sysctl_int("vm.min_free_kbytes") == 1048576
    assert VLLMServerManager._sysctl_int("vm.watermark_scale_factor") is None
    assert VLLMServerManager._sysctl_int("vm.not_a_knob") is None


def test_a_process_is_found_by_its_executable_name(monkeypatch):
    monkeypatch.setattr(vsm.os, "listdir", lambda path: ["self", "12", "13", "14"])
    _fake_files(
        monkeypatch,
        {
            "/proc/12/cmdline": b"/usr/bin/python3\0earlyoom.py\0",
            "/proc/13/cmdline": PermissionError("another user's process"),
            "/proc/14/cmdline": b"/usr/bin/earlyoom\0-m\0005\0-s\000100\0",
        },
    )
    assert VLLMServerManager._process_argv("earlyoom") == [
        "/usr/bin/earlyoom",
        "-m",
        "5",
        "-s",
        "100",
    ]
    assert VLLMServerManager._process_argv("systemd-oomd") is None


def test_no_proc_means_no_process(monkeypatch):
    def listdir(path):
        raise OSError("no /proc")

    monkeypatch.setattr(vsm.os, "listdir", listdir)
    assert VLLMServerManager._process_argv("earlyoom") is None


@pytest.mark.parametrize(
    ("outcome", "active"),
    [
        (0, True),
        (3, False),
        (FileNotFoundError("systemctl"), False),
        (subprocess.TimeoutExpired(["systemctl"], 5), False),
    ],
)
def test_a_unit_is_active_only_when_systemctl_says_so(monkeypatch, outcome, active):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(cmd, outcome)

    monkeypatch.setattr(vsm.subprocess, "run", run)
    assert VLLMServerManager._unit_is_active("systemd-oomd") is active
    assert calls == [["systemctl", "is-active", "--quiet", "systemd-oomd"]]


def test_earlyoom_with_no_arguments_is_reported_with_none(monkeypatch):
    monkeypatch.setattr(VLLMServerManager, "_unit_is_active", staticmethod(lambda unit: False))
    monkeypatch.setattr(
        VLLMServerManager, "_process_argv", staticmethod(lambda name: ["/usr/bin/earlyoom"])
    )
    problem = VLLMServerManager._oom_handler_problem()
    assert problem is not None and "misconfigured" in problem
    assert "Current arguments: (none)" in problem


@pytest.fixture
def healthy_host(monkeypatch):
    monkeypatch.setattr(vsm.shutil, "which", lambda name: "/usr/bin/sar")
    monkeypatch.setattr(VLLMServerManager, "_oom_handler_problem", classmethod(lambda cls: None))
    monkeypatch.setattr(VLLMServerManager, "_swap_total_gb", staticmethod(lambda: vsm.MIN_SWAP_GB))
    knobs = {
        "vm.min_free_kbytes": vsm.MIN_FREE_KBYTES,
        "vm.watermark_scale_factor": vsm.WATERMARK_SCALE_FACTOR,
    }
    monkeypatch.setattr(VLLMServerManager, "_sysctl_int", staticmethod(lambda name: knobs[name]))
    return knobs


@pytest.mark.parametrize("knob", ["vm.min_free_kbytes", "vm.watermark_scale_factor"])
def test_one_sysctl_below_its_floor_refuses_the_load(healthy_host, capsys, knob):
    healthy_host[knob] -= 1
    with pytest.raises(SystemExit) as exc:
        VLLMServerManager.check_host_safety()
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "1 host safety check failed" in out
    assert f"sudo sysctl -w {knob}=" in out


def test_without_sysstat_the_load_is_refused(healthy_host, monkeypatch, capsys):
    monkeypatch.setattr(vsm.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        VLLMServerManager.check_host_safety()
    assert "sudo apt install sysstat" in capsys.readouterr().out
