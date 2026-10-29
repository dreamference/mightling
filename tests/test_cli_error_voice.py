"""Ordinary failures reach the user as one ❌ line and a non-zero exit, never a traceback."""

import errno
import json
import subprocess
import urllib.error

import pytest

from dreamference.cli.dreamference_cli_controller import DreamferenceCLIController


def _fail_with(monkeypatch, error):
    def raise_it(cls, argv=None):
        raise error

    monkeypatch.setattr(DreamferenceCLIController, "dispatch_cli", classmethod(raise_it))


def _run(capsys):
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["status"])
    assert exit_info.value.code == 1
    return capsys.readouterr().out.strip().splitlines()


def test_no_docker_says_so_and_how_to_fix_it(monkeypatch, capsys):
    _fail_with(monkeypatch, FileNotFoundError(errno.ENOENT, "No such file or directory", "docker"))
    lines = _run(capsys)
    assert len(lines) == 1
    assert lines[0].startswith("❌ `docker` is not installed") and "Install Docker" in lines[0]


def test_a_missing_file_names_it(monkeypatch, capsys):
    _fail_with(
        monkeypatch, FileNotFoundError(errno.ENOENT, "No such file or directory", "/nope/tasks.md")
    )
    assert _run(capsys) == ["❌ No such file or directory: /nope/tasks.md"]


def test_permission_denied_names_the_path(monkeypatch, capsys):
    _fail_with(monkeypatch, PermissionError(errno.EACCES, "Permission denied", "/etc/x"))
    assert _run(capsys) == ["❌ Permission denied: /etc/x"]


def test_a_failed_subprocess_names_the_command_and_status(monkeypatch, capsys):
    _fail_with(monkeypatch, subprocess.CalledProcessError(3, ["docker", "pull", "img"]))
    assert _run(capsys) == ["❌ `docker pull img` failed (exit 3)."]


def test_a_timeout_names_the_command(monkeypatch, capsys):
    _fail_with(monkeypatch, subprocess.TimeoutExpired(["docker", "info"], 5))
    assert _run(capsys) == ["❌ `docker info` timed out after 5 s."]


@pytest.mark.parametrize("error", [ConnectionRefusedError(), urllib.error.URLError("refused")])
def test_a_server_that_is_not_running_points_at_server_start(monkeypatch, capsys, error):
    _fail_with(monkeypatch, error)
    lines = _run(capsys)
    assert len(lines) == 1 and "ling-admin server start" in lines[0]


def test_bad_json_is_one_line(monkeypatch, capsys):
    _fail_with(monkeypatch, json.JSONDecodeError("Expecting value", "{bad", 0))
    lines = _run(capsys)
    assert len(lines) == 1 and lines[0].startswith("❌ A file or reply that should be JSON is not")


def test_other_os_errors_use_the_system_text(monkeypatch, capsys):
    _fail_with(monkeypatch, OSError(errno.ENOSPC, "No space left on device", "/data/x"))
    assert _run(capsys) == ["❌ No space left on device: /data/x"]


def test_a_bug_keeps_its_traceback(monkeypatch):
    _fail_with(monkeypatch, TypeError("bug"))
    with pytest.raises(TypeError):
        DreamferenceCLIController.run_cli(["status"])


def test_server_stop_without_docker_is_one_line(monkeypatch, capsys):
    def no_docker(*args, **kwargs):
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", "docker")

    monkeypatch.setattr(subprocess, "run", no_docker)
    monkeypatch.setattr(subprocess, "check_output", no_docker)
    with pytest.raises(SystemExit) as exit_info:
        DreamferenceCLIController.run_cli(["server", "stop"])
    assert exit_info.value.code == 1
    assert "❌ `docker` is not installed" in capsys.readouterr().out
