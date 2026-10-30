"""The PSI watchdog's reading, cgroup resolution and kill paths, driven one method at a time.

None of these start the watchdog's thread: each calls the method under test directly, with the
kernel files, `docker`, `os.kill` and Docker's socket replaced by stand-ins under tmp.
"""

import io
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from dreamference.vllm_server import psi_watchdog
from dreamference.vllm_server.psi_watchdog import MemoryPressureWatchdog


@pytest.fixture
def pressure_file(tmp_path, monkeypatch):
    path = tmp_path / "memory"
    monkeypatch.setattr(psi_watchdog, "PSI_MEMORY_PATH", str(path))
    return path


@pytest.fixture
def no_docker_cli(monkeypatch):
    """Every `docker` command fails; the commands asked for are recorded."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(psi_watchdog.subprocess, "run", run)
    return calls


def _eperm(pid, sig):
    raise PermissionError(1, "Operation not permitted")


def _gone(pid, sig):
    raise ProcessLookupError(3, "No such process")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            "some avg10=1.00 avg60=2.00 avg300=0.00 total=1\n"
            "full avg10=3.50 avg60=4.25 avg300=0.00 total=1\n",
            (3.5, 4.25),
        ),
        # Only the `some` line: the kernel always writes both, so this is not PSI as it should be.
        ("some avg10=1.00 avg60=2.00 avg300=0.00 total=1\n", None),
        ("full avg10=3.50 avg300=0.00 total=1\n", None),
        ("full avg10=high avg60=4.25 avg300=0.00 total=1\n", None),
    ],
)
def test_only_a_full_line_with_both_averages_is_a_reading(pressure_file, content, expected):
    pressure_file.write_text(content)
    assert psi_watchdog.read_memory_pressure_full() == expected


def test_a_kernel_without_psi_gives_no_reading_and_no_sample(pressure_file):
    assert psi_watchdog.read_memory_pressure_full() is None
    wd = MemoryPressureWatchdog("test-container")
    assert wd._sample(123.0) is None
    assert wd.tripped is False


def test_avg10_at_the_default_limit_starts_the_spike_clock(pressure_file, monkeypatch, no_docker_cli):
    # The limit is where danger begins, so a reading at it counts as over.
    pressure_file.write_text("full avg10=60.00 avg60=0.00 avg300=0.00 total=1\n")
    monkeypatch.setattr(psi_watchdog, "time", SimpleNamespace(monotonic=lambda: 100.0))
    wd = MemoryPressureWatchdog("test-container")
    assert wd._sample(None) == 100.0
    assert no_docker_cli == []


def test_avg60_at_the_default_floor_trips_at_once(pressure_file, monkeypatch, no_docker_cli):
    pressure_file.write_text("full avg10=0.00 avg60=25.00 avg300=0.00 total=1\n")
    monkeypatch.setattr(MemoryPressureWatchdog, "_kill", lambda self: True)
    wd = MemoryPressureWatchdog("test-container")
    assert wd._sample(None) is None
    assert wd.tripped is True


def test_the_spike_rule_trips_once_the_hold_reaches_its_duration(pressure_file, monkeypatch):
    pressure_file.write_text("full avg10=75.00 avg60=0.00 avg300=0.00 total=1\n")
    monkeypatch.setattr(psi_watchdog, "time", SimpleNamespace(monotonic=lambda: 105.0))
    monkeypatch.setattr(MemoryPressureWatchdog, "_kill", lambda self: True)
    wd = MemoryPressureWatchdog("test-container", trip_duration_s=5.0)
    assert wd._sample(100.0) == 100.0
    assert wd.tripped is True


def test_the_sustained_rule_reports_avg60_over_its_minute(pressure_file, monkeypatch):
    pressure_file.write_text("full avg10=10.00 avg60=30.00 avg300=0.00 total=1\n")
    monkeypatch.setattr(MemoryPressureWatchdog, "_kill", lambda self: True)
    seen = []
    wd = MemoryPressureWatchdog("test-container", on_trip=lambda *report: seen.append(report))
    wd._sample(None)
    assert seen == [(psi_watchdog.TRIP_REASON_SUSTAINED, 30.0, 60.0)]


def test_the_cgroup_is_found_from_the_container_id_and_probed(tmp_path, monkeypatch):
    procs = tmp_path / "docker" / "abc123" / "cgroup.procs"
    procs.parent.mkdir(parents=True)
    procs.write_text("4242\n")
    monkeypatch.setattr(
        psi_watchdog,
        "CGROUP_PROCS_CANDIDATES",
        (str(tmp_path / "missing-{id}"), str(tmp_path / "docker" / "{id}" / "cgroup.procs")),
    )
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(psi_watchdog.subprocess, "run", run)
    monkeypatch.setattr(psi_watchdog.os, "kill", _eperm)
    wd = MemoryPressureWatchdog("test-container")
    wd._ensure_cgroup_resolved()
    assert calls == [["docker", "inspect", "--format", "{{.Id}}", "test-container"]]
    assert wd._container_id == "abc123"
    assert wd._cgroup_procs == str(procs)
    assert wd._direct_kill_ok is False
    # Resolved once: later healthy samples do not fork again.
    wd._ensure_cgroup_resolved()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        OSError("docker not installed"),
        subprocess.TimeoutExpired(["docker"], 10),
        subprocess.CompletedProcess([], 0, stdout="  \n", stderr=""),
    ],
)
def test_an_inspect_that_names_no_container_leaves_the_cgroup_unknown(monkeypatch, outcome):
    def run(cmd, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(psi_watchdog.subprocess, "run", run)
    wd = MemoryPressureWatchdog("test-container")
    wd._ensure_cgroup_resolved()
    assert wd._cgroup_procs is None and wd._container_id is None
    assert wd._resolve_attempts == 1


def test_an_id_with_no_known_cgroup_layout_is_kept_for_the_socket(tmp_path, monkeypatch):
    monkeypatch.setattr(psi_watchdog, "CGROUP_PROCS_CANDIDATES", (str(tmp_path / "{id}"),))
    monkeypatch.setattr(
        psi_watchdog.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr=""),
    )
    wd = MemoryPressureWatchdog("test-container")
    wd._ensure_cgroup_resolved()
    assert wd._container_id == "abc123"
    assert wd._cgroup_procs is None
    assert wd._direct_kill_ok is None


@pytest.mark.parametrize(
    "layout",
    [
        "/sys/fs/cgroup/system.slice/docker-abc123.scope/cgroup.procs",  # v2, systemd driver
        "/sys/fs/cgroup/docker/abc123/cgroup.procs",  # v2, cgroupfs driver
        "/sys/fs/cgroup/memory/docker/abc123/cgroup.procs",  # v1
    ],
)
def test_each_known_cgroup_layout_is_found(monkeypatch, layout):
    exists = psi_watchdog.os.path.exists
    monkeypatch.setattr(
        psi_watchdog.os.path,
        "exists",
        lambda path: path == layout if path.startswith("/sys/fs/cgroup/") else exists(path),
    )
    monkeypatch.setattr(
        psi_watchdog.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr=""),
    )
    monkeypatch.setattr(MemoryPressureWatchdog, "_read_cgroup_pids", lambda self: [])
    wd = MemoryPressureWatchdog("test-container")
    wd._ensure_cgroup_resolved()
    assert wd._cgroup_procs == layout


def test_a_failed_inspect_is_not_read_as_an_id(monkeypatch):
    # Whatever a failing `docker inspect` printed, it is not the container's ID.
    monkeypatch.setattr(
        psi_watchdog.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, 1, stdout="[]\n", stderr="Error: No such object: test-container\n"
        ),
    )
    wd = MemoryPressureWatchdog("test-container")
    wd._ensure_cgroup_resolved()
    assert wd._container_id is None and wd._cgroup_procs is None


def test_the_probe_skips_a_pid_that_has_exited(monkeypatch):
    probed = []

    def kill(pid, sig):
        probed.append(pid)
        if pid == 1:
            raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(psi_watchdog.os, "kill", kill)
    wd = MemoryPressureWatchdog("test-container")
    wd._probe_direct_kill([1, 2])
    assert probed == [1, 2]
    assert wd._direct_kill_ok is True
    # Every PID gone: the question stays open rather than being answered either way.
    monkeypatch.setattr(psi_watchdog.os, "kill", _gone)
    wd._direct_kill_ok = None
    wd._probe_direct_kill([1, 2])
    assert wd._direct_kill_ok is None


def test_an_unknown_or_unreadable_cgroup_lists_no_pids(tmp_path):
    wd = MemoryPressureWatchdog("test-container")
    assert wd._read_cgroup_pids() == []
    wd._cgroup_procs = str(tmp_path / "gone" / "cgroup.procs")
    assert wd._read_cgroup_pids() == []
    procs = tmp_path / "cgroup.procs"
    procs.write_text("4242\nnot-a-pid\n4243\n")
    wd._cgroup_procs = str(procs)
    assert wd._read_cgroup_pids() == [4242, 4243]


def test_a_cgroup_file_that_does_not_decode_lists_no_pids(monkeypatch):
    class Undecodable(io.StringIO):
        def read(self, *args):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(psi_watchdog, "open", lambda *args, **kwargs: Undecodable(), raising=False)
    wd = MemoryPressureWatchdog("test-container")
    wd._cgroup_procs = "/sys/fs/cgroup/docker/abc123/cgroup.procs"
    assert wd._read_cgroup_pids() == []


def test_permission_lost_mid_kill_falls_through_to_the_socket(tmp_path, monkeypatch, no_docker_cli):
    procs = tmp_path / "cgroup.procs"
    procs.write_text("4242\n4243\n")
    signalled = []

    def kill(pid, sig):
        if pid == 4243:
            raise PermissionError(1, "Operation not permitted")
        signalled.append(pid)

    monkeypatch.setattr(psi_watchdog.os, "kill", kill)
    monkeypatch.setattr(MemoryPressureWatchdog, "_kill_via_docker_socket", lambda self: True)
    wd = MemoryPressureWatchdog("test-container")
    wd._cgroup_procs = str(procs)
    wd._direct_kill_ok = True
    # One PID signalled is not the container killed: the rest of it still holds memory.
    assert wd._kill() is True
    assert signalled == [4242]
    assert wd._direct_kill_ok is False
    assert no_docker_cli == []


def test_pids_that_are_all_gone_are_not_a_kill(tmp_path, monkeypatch, no_docker_cli):
    procs = tmp_path / "cgroup.procs"
    procs.write_text("4242\n")
    monkeypatch.setattr(psi_watchdog.os, "kill", _gone)
    monkeypatch.setattr(MemoryPressureWatchdog, "_kill_via_docker_socket", lambda self: False)
    wd = MemoryPressureWatchdog("test-container")
    wd._cgroup_procs = str(procs)
    wd._direct_kill_ok = True
    assert wd._kill() is False
    assert no_docker_cli == [["docker", "kill", "test-container"]]


def test_a_pid_gone_mid_kill_does_not_spare_the_rest(tmp_path, monkeypatch, no_docker_cli):
    procs = tmp_path / "cgroup.procs"
    procs.write_text("4242\n4243\n")
    signalled = []

    def kill(pid, sig):
        if pid == 4242:
            raise ProcessLookupError(3, "No such process")
        signalled.append(pid)

    monkeypatch.setattr(psi_watchdog.os, "kill", kill)
    monkeypatch.setattr(MemoryPressureWatchdog, "_docker_api", lambda self, action, body=b"": True)
    wd = MemoryPressureWatchdog("test-container")
    wd._cgroup_procs = str(procs)
    wd._direct_kill_ok = True
    assert wd._kill() is True
    assert signalled == [4243]


@pytest.mark.parametrize("probed", [False, None])
def test_no_pid_is_signalled_without_a_probe_that_allowed_it(tmp_path, monkeypatch, probed):
    # The probe said no, or never got an answer: the socket kills the container, not kill(2).
    procs = tmp_path / "cgroup.procs"
    procs.write_text("4242\n")
    signalled = []
    monkeypatch.setattr(psi_watchdog.os, "kill", lambda pid, sig: signalled.append(pid))
    monkeypatch.setattr(MemoryPressureWatchdog, "_docker_api", lambda self, action, body=b"": True)
    wd = MemoryPressureWatchdog("test-container")
    wd._cgroup_procs = str(procs)
    wd._direct_kill_ok = probed
    assert wd._kill() is True
    assert signalled == []


def test_a_docker_cli_that_cannot_run_is_not_a_kill(monkeypatch):
    def run(cmd, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(psi_watchdog.subprocess, "run", run)
    monkeypatch.setattr(MemoryPressureWatchdog, "_kill_via_docker_socket", lambda self: False)
    assert MemoryPressureWatchdog("test-container")._kill() is False


def test_a_raising_on_trip_does_not_undo_the_kill(monkeypatch):
    monkeypatch.setattr(MemoryPressureWatchdog, "_kill", lambda self: True)

    def on_trip(reason, pressure, held_for_s):
        raise RuntimeError("the caller's report failed")

    wd = MemoryPressureWatchdog("test-container", on_trip=on_trip)
    wd._trip(psi_watchdog.TRIP_REASON_SUSTAINED, 30.0, 60.0)
    assert wd.tripped is True and wd.killed is True


def test_a_failed_kill_tells_the_operator_to_kill_it_by_hand(monkeypatch, capsys):
    monkeypatch.setattr(MemoryPressureWatchdog, "_kill", lambda self: False)
    wd = MemoryPressureWatchdog("test-container")
    wd._trip(psi_watchdog.TRIP_REASON_SPIKE, 75.0, 4.0)
    out = capsys.readouterr().out
    assert wd.tripped is True and wd.killed is False
    assert "every path to kill 'test-container' failed" in out
    assert "docker kill test-container" in out


def test_a_remote_docker_host_is_not_the_socket_path(monkeypatch, fake_socket):
    # Nor is it a reason to fall back to the local socket, which names some other dockerd.
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:2375")
    assert MemoryPressureWatchdog("test-container")._kill_via_docker_socket() is False
    assert fake_socket == []


@pytest.fixture
def docker_socket(monkeypatch):
    """A unix socket under a short tmp path, named by DOCKER_HOST; never the real dockerd's."""
    directory = tempfile.mkdtemp(prefix="psi-")
    path = str(Path(directory) / "docker.sock")
    monkeypatch.setenv("DOCKER_HOST", f"unix://{path}")
    yield path
    shutil.rmtree(directory, ignore_errors=True)


def _serve_once(path, reply):
    """Answers one request on `path` with `reply`; returns the thread and what it received."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    received = []

    def answer():
        with server:
            conn, _ = server.accept()
            with conn:
                received.append(conn.recv(4096))
                conn.sendall(reply)

    thread = threading.Thread(target=answer, name="fake-dockerd", daemon=True)
    thread.start()
    return thread, received


@pytest.mark.parametrize(
    ("reply", "killed"),
    [
        (b"HTTP/1.1 204 No Content\r\n\r\n", True),
        # Already exited: not this watchdog's failure, but not a kill either.
        (b"HTTP/1.1 404 Not Found\r\n\r\n", False),
        (b"garbage", False),
    ],
)
def test_the_socket_kill_posts_sigkill_for_the_container_id(docker_socket, reply, killed):
    thread, received = _serve_once(docker_socket, reply)
    wd = MemoryPressureWatchdog("test-container")
    wd._container_id = "abc123"
    assert wd._kill_via_docker_socket() is killed
    thread.join(timeout=5)
    assert received[0].startswith(b"POST /containers/abc123/kill?signal=SIGKILL HTTP/1.1\r\n")


def test_no_listener_on_the_socket_is_not_a_kill(docker_socket):
    assert MemoryPressureWatchdog("test-container")._kill_via_docker_socket() is False


@pytest.fixture
def fake_socket(monkeypatch):
    """Stands in for the socket module, so the default path can be named without reaching it."""
    log = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def settimeout(self, seconds):
            log.append(("timeout", seconds))

        def connect(self, path):
            log.append(("connect", path))

        def sendall(self, data):
            log.append(("send", data))

        def recv(self, size):
            return b"HTTP/1.1 204 No Content\r\n\r\n"

    monkeypatch.setattr(
        psi_watchdog,
        "socket",
        SimpleNamespace(
            AF_UNIX=socket.AF_UNIX,
            SOCK_STREAM=socket.SOCK_STREAM,
            timeout=socket.timeout,
            socket=lambda *args: Conn(),
        ),
    )
    return log


def test_without_docker_host_the_kill_goes_to_dockerds_socket_with_a_timeout(
    monkeypatch, fake_socket
):
    # A stalled dockerd must not hold the watchdog forever.
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    assert MemoryPressureWatchdog("test-container")._kill_via_docker_socket() is True
    assert fake_socket[:2] == [
        ("timeout", psi_watchdog.DOCKER_KILL_TIMEOUT_S),
        ("connect", "/var/run/docker.sock"),
    ]


def _killed_directly(tmp_path, monkeypatch):
    """A root watchdog whose direct SIGKILL reaches every PID of the container."""
    procs = tmp_path / "cgroup.procs"
    procs.write_text("4242\n")
    monkeypatch.setattr(psi_watchdog.os, "kill", lambda pid, sig: None)
    wd = MemoryPressureWatchdog("test-container")
    wd._cgroup_procs = str(procs)
    wd._direct_kill_ok = True
    wd._container_id = "abc123"
    return wd


def test_a_direct_kill_turns_off_the_restart_policy_over_the_socket(
    tmp_path, monkeypatch, docker_socket, no_docker_cli
):
    # dockerd saw the PIDs die, not a stop: `--restart unless-stopped` would bring the container
    # back and reload the model on the host that just ran out of memory.
    thread, received = _serve_once(docker_socket, b"HTTP/1.1 200 OK\r\n\r\n")
    assert _killed_directly(tmp_path, monkeypatch)._kill() is True
    thread.join(timeout=5)
    head, _, body = received[0].partition(b"\r\n\r\n")
    assert head.startswith(b"POST /containers/abc123/update HTTP/1.1\r\n")
    assert body == b'{"RestartPolicy": {"Name": "no"}}'
    # dockerd reads exactly Content-Length bytes of JSON; without them the update is refused.
    assert b"\r\nContent-Type: application/json\r\n" in head
    assert b"\r\nContent-Length: %d\r\n" % len(body) in head
    assert no_docker_cli == []


def test_without_the_socket_the_restart_policy_goes_through_the_cli(
    tmp_path, monkeypatch, docker_socket, no_docker_cli
):
    assert _killed_directly(tmp_path, monkeypatch)._kill() is True
    assert no_docker_cli == [["docker", "update", "--restart=no", "test-container"]]
