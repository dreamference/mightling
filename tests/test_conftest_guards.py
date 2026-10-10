"""The suite is offline by construction (AGENTS.md): the guards of conftest.py, tested as rules."""

import os
import socket
import subprocess

import pytest

from conftest import INSTALLED_BINARY_DIRS, MODEL_SERVER_PORTS, REAL_HOME, _installed_binary


def test_the_installed_binaries_are_recognised_by_path_link_and_name(tmp_path, monkeypatch):
    assert _installed_binary(os.path.join(REAL_HOME, ".local/bin/ling")) == os.path.join(REAL_HOME, ".local/bin/ling")
    assert _installed_binary("~/.local/share/dreamference/mightling/bin/ling-code").startswith(INSTALLED_BINARY_DIRS[1])
    # A stand-in under the test's folder is not the install, nor is a program elsewhere on PATH.
    stand_in = tmp_path / "ling"
    stand_in.write_text("#!/bin/sh\n")
    assert _installed_binary(str(stand_in)) == "" and _installed_binary("/opt/ling") == "" and _installed_binary("") == ""
    # A bare name is looked up on PATH: `ling` on PATH is the link in ~/.local/bin.
    monkeypatch.setenv("PATH", os.path.join(REAL_HOME, ".local/bin"))
    if os.path.exists(os.path.join(REAL_HOME, ".local/bin/ling")):
        assert _installed_binary("ling")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert _installed_binary("ling") == ""


def test_running_the_installed_agent_fails_the_test(monkeypatch):
    path = os.path.join(REAL_HOME, ".local/bin/ling")
    with pytest.raises(AssertionError, match="installed .*ling"):
        subprocess.run([path, "--version"], capture_output=True)
    with pytest.raises(AssertionError, match="installed"):
        subprocess.Popen([os.path.join(REAL_HOME, ".local/share/dreamference/mightling/bin/ling"), "exec", "hi"])


@pytest.fixture
def listener():
    """A socket this test process listens on, standing in for the model server at its port."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        yield server.getsockname()


@pytest.mark.installed_binary
def test_a_test_of_the_built_binary_opts_out_by_its_marker(listener):
    # The spawn goes through (and fails on the missing file, not on the guard) ...
    with pytest.raises(FileNotFoundError):
        subprocess.run([os.path.join(REAL_HOME, ".local/share/dreamference/mightling/bin/no-such-binary")])
    # ... and a connection to the model server's port is not refused by the guard either. The port
    # is this test's own listener: the guard itself is what keeps the suite off the live server,
    # so even its tests never reach it.
    with socket.create_connection(listener, timeout=2):
        pass


def test_connecting_to_the_model_server_fails_the_test(listener, monkeypatch):
    # The rule covers the live ports by name ...
    assert MODEL_SERVER_PORTS == (8000, 18000)
    # ... and is shown on a listener of this test's own, named as the model server's port.
    monkeypatch.setattr("conftest.MODEL_SERVER_PORTS", (listener[1],))
    with pytest.raises(AssertionError, match="model server"):
        socket.create_connection(listener, timeout=1)
    with pytest.raises(AssertionError, match="model server"):
        import urllib.request
        urllib.request.urlopen(f"http://127.0.0.1:{listener[1]}/v1/models", timeout=1)
    # Any other free port is reachable as before.
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen(1)
        with socket.create_connection(other.getsockname(), timeout=2):
            pass
