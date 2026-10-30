"""What the container diagnostics line calls healthy, and what it says when it cannot know.

Host RAM, I/O wait and CPU each get a verdict from their thresholds; a figure that cannot be read
is "Unknown", never a reassuring "Healthy"; and with no Docker, a failed `docker stats` or a probe
that raises there is no line at all rather than a made-up one.
"""

import io
import subprocess

import pytest

from dreamference.vllm_server import diagnostics
from dreamference.vllm_server.diagnostics import ContainerDiagnostics

STATS = "1.5GiB / 100GiB|50.00%|1MB / 2MB"


def _host(monkeypatch, files, stats=STATS, stats_rc=0, cgroup=None, cpus=4):
    """Fakes Docker and /proc: `files` maps a /proc path to its text, or to an exception."""
    ran = []

    def run(cmd, **kwargs):
        ran.append(cmd)
        if cmd[:2] == ["docker", "stats"]:
            return subprocess.CompletedProcess(cmd, stats_rc, stdout=stats, stderr="")
        if isinstance(cgroup, BaseException):
            raise cgroup
        rc = 1 if cgroup is None else 0
        return subprocess.CompletedProcess(cmd, rc, stdout=cgroup or "", stderr="")

    def fake_open(path, mode="r"):
        text = files.get(path, FileNotFoundError(path))
        if isinstance(text, BaseException):
            raise text
        return io.StringIO(text)

    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(diagnostics.subprocess, "run", run)
    monkeypatch.setattr(diagnostics, "open", fake_open, raising=False)
    monkeypatch.setattr(diagnostics.os, "cpu_count", lambda: cpus)
    return ran


def _meminfo(gb):
    return {"/proc/meminfo": f"MemTotal: 1 kB\nMemAvailable: {int(gb * 1024 * 1024)} kB\n"}


def _cpu(total_busy, iowait):
    # user nice system idle iowait: the total is their sum.
    return f"cpu  {total_busy} 0 0 0 {iowait} 0 0\ncpu0 1 0 0 0 0\n"


@pytest.mark.parametrize(
    ("gb", "verdict"),
    [(6.0, "6.0GB (Healthy)"), (3.0, "3.0GB (Unhealthy)"), (1.0, "1.0GB (Dangerous)")],
)
def test_free_host_ram_gets_its_verdict_from_the_thresholds(monkeypatch, gb, verdict):
    _host(monkeypatch, _meminfo(gb))
    line = ContainerDiagnostics("http://localhost:8000").get_diagnostics_line()
    assert f"Host RAM Free: {verdict}" in line


@pytest.mark.parametrize("meminfo", [None, "MemTotal: 1 kB\n", "MemAvailable: lots kB\n"])
def test_host_ram_that_cannot_be_read_is_unknown_not_healthy(monkeypatch, meminfo):
    files = {} if meminfo is None else {"/proc/meminfo": meminfo}
    _host(monkeypatch, files)
    line = ContainerDiagnostics("http://localhost:8000").get_diagnostics_line()
    assert "Host RAM Free: Unknown" in line and "Healthy" not in line.split("Host RAM")[1]


def test_io_wait_is_unknown_until_a_second_reading_and_then_gets_its_verdict(monkeypatch):
    files = _meminfo(8.0)
    _host(monkeypatch, files)
    diag = ContainerDiagnostics("http://localhost:8000")
    files["/proc/stat"] = _cpu(100, 0)
    assert "IOWait: Unknown" in diag.get_diagnostics_line()
    for busy, iowait, verdict in [
        (195, 5, "5.0% (Healthy)"),
        (275, 25, "20.0% (Unhealthy)"),
        (325, 75, "50.0% (Dangerous)"),
    ]:
        files["/proc/stat"] = _cpu(busy, iowait)
        assert f"IOWait: {verdict}" in diag.get_diagnostics_line()


def test_io_wait_over_no_elapsed_ticks_or_from_a_short_line_is_unknown(monkeypatch):
    files = {**_meminfo(8.0), "/proc/stat": _cpu(100, 0)}
    _host(monkeypatch, files)
    diag = ContainerDiagnostics("http://localhost:8000")
    diag.get_diagnostics_line()
    assert "IOWait: Unknown" in diag.get_diagnostics_line(), "no ticks passed"
    files["/proc/stat"] = "cpu 1 2 3\n"
    assert "IOWait: Unknown" in diag.get_diagnostics_line()
    assert diag._last_total_ticks == 100, "a short line does not move the baseline"


@pytest.mark.parametrize(
    ("cpu", "verdict"),
    [("280.00%", "Healthy"), ("280.20%", "Dangerous"), ("--", "Unknown")],
)
def test_container_cpu_is_judged_against_seventy_percent_of_the_host(monkeypatch, cpu, verdict):
    _host(monkeypatch, _meminfo(8.0), stats=f"1GiB / 2GiB|{cpu}|0B / 0B", cpus=4)
    line = ContainerDiagnostics("http://localhost:8000").get_diagnostics_line()
    assert f"CPU: {cpu} / 280.0% ({verdict})" in line


def test_a_host_without_a_cpu_count_is_judged_against_one_cpu(monkeypatch):
    _host(monkeypatch, _meminfo(8.0), stats="1GiB / 2GiB|90%|0B / 0B", cpus=None)
    line = ContainerDiagnostics("http://localhost:8000").get_diagnostics_line()
    assert "CPU: 90% / 100.0% (Healthy)" in line


def test_the_containers_swap_and_major_faults_are_read_from_its_cgroup(monkeypatch):
    cgroup = "anon 1\npgmajfault 7\npswpin 3\npswpout 4\n"
    ran = _host(monkeypatch, _meminfo(8.0), cgroup=cgroup)
    line = ContainerDiagnostics("http://gb10:18000").get_diagnostics_line()
    assert line.endswith(" | SwapIO: 3in 4out | MajFaults: 7")
    assert ran[0][-1] == "dreamference-vllm-18000"
    assert ran[1] == [
        "docker",
        "exec",
        "dreamference-vllm-18000",
        "cat",
        "/sys/fs/cgroup/memory.stat",
    ]


@pytest.mark.parametrize("cgroup", [None, subprocess.TimeoutExpired(["docker"], 1)])
def test_a_cgroup_that_cannot_be_read_leaves_the_swap_figures_out(monkeypatch, cgroup):
    ran = _host(monkeypatch, _meminfo(8.0), cgroup=cgroup)
    line = ContainerDiagnostics("http://localhost").get_diagnostics_line()
    assert "SwapIO" not in line and line.startswith("1.5GiB / 100GiB | CPU: 50.00%")
    assert ran[0][-1] == "dreamference-vllm-8000"


def test_stats_without_their_three_fields_are_passed_on_as_they_are(monkeypatch):
    _host(monkeypatch, _meminfo(8.0), stats="  unexpected output \n", cgroup="pswpin 1\n")
    line = ContainerDiagnostics("http://localhost:8000").get_diagnostics_line()
    assert line == "unexpected output | SwapIO: 1in ?out | MajFaults: ?"


def test_no_line_without_docker_or_when_docker_stats_fails_or_raises(monkeypatch):
    _host(monkeypatch, _meminfo(8.0), stats_rc=1)
    diag = ContainerDiagnostics("http://localhost:8000")
    assert diag.get_diagnostics_line() is None

    def broken(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 2)

    monkeypatch.setattr(diagnostics.subprocess, "run", broken)
    assert diag.get_diagnostics_line() is None

    ran = _host(monkeypatch, _meminfo(8.0))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
    assert diag.get_diagnostics_line() is None
    assert ran == []
