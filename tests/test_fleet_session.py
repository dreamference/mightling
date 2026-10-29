"""Tests for the fleet's SSH session: every ssh, ssh-keygen and local command is a stand-in."""

import subprocess
from types import SimpleNamespace
from typing import ClassVar

import pytest

from dreamference.node.fleet_session import FleetSession


@pytest.fixture
def session(tmp_path):
    """A session to `node-b` that logs to the run folder."""
    return FleetSession("node-b", "owner", tmp_path / "run", log_path=tmp_path / "fleet.log")


def test_a_master_that_times_out_is_not_open_and_its_error_is_kept(session, monkeypatch):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired("ssh", 80)

    monkeypatch.setattr(subprocess, "run", hang)
    assert session.open() is False
    assert "timed out" in session.last_error
    assert "-> exit 255" in session.log_path.read_text()


def test_a_master_that_cannot_be_started_is_not_open(session, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(subprocess, "run", missing)
    assert session.open() is False
    assert "ssh" in session.last_error


def test_the_master_is_opened_without_stdin_when_the_run_answers_the_password(session, monkeypatch):
    seen = {}

    def run(command, **kwargs):
        seen.update(kwargs, command=command)
        kwargs["stderr"].write("Warning: added host key\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    session.askpass_env = {"SSH_ASKPASS": "/bin/askpass", "SSH_ASKPASS_REQUIRE": "force"}
    assert session.open() is True
    assert seen["stdin"] == subprocess.DEVNULL and seen["env"]["SSH_ASKPASS_REQUIRE"] == "force"
    assert seen["command"][-3:] == ["-f", "-N", "owner@node-b"]
    assert session.last_error == "Warning: added host key\n"
    assert (session.run_dir.stat().st_mode & 0o777) == 0o700


def test_a_key_login_without_a_key_is_never_tried(session, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ssh was run"))
    assert session.key_login_works() is False


def test_a_key_login_offers_only_the_key_and_never_a_password(session, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **k: (
            calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    session.identity = tmp_path / "id_ed25519"
    assert session.key_login_works() is True
    command = calls[0]
    assert "BatchMode=yes" in command and "PreferredAuthentications=publickey" in command
    assert "StrictHostKeyChecking=yes" in command and "ControlPath=none" in command
    assert command[-2:] == ["owner@node-b", "true"]


@pytest.mark.parametrize(
    ("error", "code"), [(subprocess.TimeoutExpired("ssh", 1), 124), (OSError("no ssh"), 255)]
)
def test_a_command_that_times_out_or_cannot_start_is_a_failed_result(monkeypatch, error, code):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)
    assert FleetSession._spawn(["ssh", "x"], capture=True).returncode == code


@pytest.mark.parametrize(("code", "expected"), [(0, True), (255, False)])
def test_the_master_is_open_when_ssh_check_answers(session, monkeypatch, code, expected):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **k: calls.append(command) or SimpleNamespace(returncode=code),
    )
    assert session.is_open() is expected
    session.close()
    assert [c[3:5] for c in calls] == [["-O", "check"], ["-O", "exit"]]


def test_no_host_key_fingerprint_without_a_known_hosts_file(session, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ssh-keygen was run"))
    assert session.host_key_fingerprint() is None


def test_the_host_key_fingerprint_is_read_from_ssh_keygen(session, monkeypatch):
    session.run_dir.mkdir()
    session.known_hosts.write_text("node-b ssh-ed25519 AAAA\n")
    answer = "# Host node-b found: line 1\nnode-b ED25519 SHA256:abcDEF123 no comment\n"
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=answer, stderr="")
    )
    assert session.host_key_fingerprint() == "abcDEF123"
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="")
    )
    assert session.host_key_fingerprint() is None


def test_host_key_lines_drop_the_host_comments_and_short_lines(session, monkeypatch):
    answer = "# Host node-b found: line 1\nnode-b ssh-ed25519 AAAAkey\nnode-b ssh-rsa\nnode-b ecdsa-sha2 BBBB extra\n"
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=answer, stderr="")
    )
    assert session.host_key_lines() == ["ssh-ed25519 AAAAkey", "ecdsa-sha2 BBBB"]


class Process:
    """Stands in for `subprocess.Popen`, exiting with the code its command's first word names."""

    codes: ClassVar[dict[str, int]] = {}
    started: ClassVar[list] = []

    def __init__(self, command, stdin=None, stdout=None) -> None:
        self.command = command
        self.stdin = stdin
        self.stdout = SimpleNamespace(closed=False)
        self.stdout.close = lambda: setattr(self.stdout, "closed", True)
        Process.started.append(self)

    def wait(self) -> int:
        """Returns the code set for this command."""
        return Process.codes.get(self.command[0], 0)


@pytest.fixture
def popen(monkeypatch):
    """Stands every Popen in with `Process`."""
    Process.codes, Process.started = {}, []
    monkeypatch.setattr(subprocess, "Popen", Process)
    return Process


def test_a_pipeline_is_streamed_into_the_remote_command(session, popen):
    assert session.pipe_into([["docker", "save", "img"], ["zstd"]], "zstd -d | docker load") == 0
    save, zstd, ssh = popen.started
    assert zstd.stdin is save.stdout and ssh.stdin is zstd.stdout
    assert save.stdout.closed and zstd.stdout.closed
    assert ssh.command[-3:] == ["-T", "owner@node-b", "zstd -d | docker load"]
    assert "(input: docker save img | zstd)" in session.log_path.read_text()


def test_a_failing_local_command_fails_a_pipeline_the_remote_end_accepted(session, popen):
    popen.codes = {"docker": 1}
    assert session.pipe_into([["docker", "save", "img"], ["zstd"]], "docker load") == 1


def test_the_remote_failure_is_the_pipelines_exit_code(session, popen):
    popen.codes = {"ssh": 3, "docker": 1}
    assert session.pipe_into([["docker", "save", "img"]], "docker load") == 3


def test_a_session_without_a_log_writes_none(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    FleetSession("node-b", "owner", tmp_path / "run").open()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run"]


def test_a_new_run_dir_is_private_and_under_the_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    run_dir = FleetSession.new_run_dir()
    assert run_dir.parent == tmp_path and run_dir.name.startswith("mightling-fleet-")
    assert (run_dir.stat().st_mode & 0o777) == 0o700
