"""The Python side of the code index: `puffin-admin code setup`, and the model server stopping
puffin-code's index runs before a load (specs/DREAMFERENCE_PUFFIN_CODE_INDEX.md §5, §9.2).
The router itself is Rust, tested with `cargo test` in puffin-code-rs/."""

import hashlib
import inspect
import io
import os
import subprocess
import sys
import tarfile
from unittest.mock import patch

import pytest

# Not `setup_module`: pytest would take that name for its module-level setup hook.
from dreamference.cli import code_index_setup as code_setup
from dreamference.cli.code_index_setup import CodeIndexSetup, PinnedTool
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager


def _archive(member: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_the_pins_name_both_tools_with_a_sha256():
    pins = {tool.name: tool for tool in CodeIndexSetup.pins()}
    assert set(pins) == {"codebase-memory-mcp", "scip"}
    for tool in pins.values():
        assert len(tool.sha256) == 64 and tool.url.startswith("https://github.com/")
    # The scip CLI's pin is the one the spec records (§3).
    assert pins["scip"].sha256 == "6ab677dc2c4bf2955975d0530766152e45daaa988f9404068d8adecacd0bb24c"


def test_a_tool_installs_only_from_its_pinned_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(code_setup, "INSTALL_DIR", str(tmp_path / "install"))
    archive = _archive("scip", b"#!/bin/sh\necho scip\n")
    good = PinnedTool(hashlib.sha256(archive).hexdigest(), "scip", "0.10.0", "https://github.com/x/scip.tar.gz", "scip")
    bad = good._replace(sha256="0" * 64)
    downloads = []
    monkeypatch.setattr(CodeIndexSetup, "_download", classmethod(lambda cls, url: downloads.append(url) or archive))

    assert CodeIndexSetup.install_tool(bad) is False
    assert not (tmp_path / "install" / "bin" / "scip").exists(), "a mismatched archive was installed"

    assert CodeIndexSetup.install_tool(good) is True
    installed = tmp_path / "install" / "bin" / "scip"
    assert installed.read_bytes() == b"#!/bin/sh\necho scip\n"
    assert os.access(installed, os.X_OK)
    assert CodeIndexSetup.is_installed(good)
    # Installed and stamped: nothing is downloaded again.
    assert CodeIndexSetup.install_tool(good) is True
    assert len(downloads) == 2


def test_scip_python_installs_from_the_lockfile_without_scripts(tmp_path, monkeypatch):
    monkeypatch.setattr(code_setup, "INDEXERS_DIR", str(tmp_path / "indexers"))
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\n")
    calls = []
    monkeypatch.setattr(code_setup.shutil, "which", lambda name: str(node) if name == "node" else "/usr/bin/npm")
    monkeypatch.setattr(
        code_setup.subprocess, "run", lambda command, cwd=None, check=False: calls.append((command, cwd)) or subprocess.CompletedProcess(command, 0)
    )
    assert CodeIndexSetup.install_npm_indexers() is True
    (command, cwd), = calls
    assert command[1:3] == ["ci", "--ignore-scripts"]
    assert cwd == str(tmp_path / "indexers")
    assert (tmp_path / "indexers" / "package-lock.json").is_file()
    assert os.readlink(tmp_path / "indexers" / "node") == str(node)


def test_no_node_keeps_python_on_the_universal_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(code_setup, "INDEXERS_DIR", str(tmp_path / "indexers"))
    monkeypatch.setattr(code_setup.shutil, "which", lambda name: None)
    assert CodeIndexSetup.install_npm_indexers() is False
    assert not (tmp_path / "indexers").exists()


def test_the_npm_lockfile_pins_scip_python_by_integrity():
    import json

    with open(os.path.join(code_setup.INDEXERS_SOURCE_DIR, "package-lock.json")) as handle:
        lock = json.load(handle)
    entry = lock["packages"]["node_modules/@sourcegraph/scip-python"]
    assert entry["version"] == "0.6.6" and entry["integrity"].startswith("sha512-")


def test_the_admin_command_dispatches_setup(monkeypatch):
    from dreamference.cli.dreamference_cli_controller import main

    monkeypatch.setattr(CodeIndexSetup, "install", classmethod(lambda cls: True))
    monkeypatch.setattr(sys, "argv", ["puffin-admin", "code", "setup"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0


def test_server_start_stops_index_runs_before_its_pre_flight():
    # An indexer holding memory would make check_host_safety()'s view of free memory wrong.
    source = inspect.getsource(VLLMServerManager.start_server)
    assert source.index("self._stop_index_scopes()") < source.index("self.check_host_safety()")


def test_stopping_index_runs_asks_the_user_systemd(monkeypatch):
    from conftest import REAL_STOP_INDEX_SCOPES

    monkeypatch.setattr(VLLMServerManager, "_stop_index_scopes", REAL_STOP_INDEX_SCOPES)
    calls = []
    monkeypatch.setattr(
        "dreamference.vllm_server.vllm_server_manager.subprocess.run",
        lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )
    VLLMServerManager._stop_index_scopes()
    assert calls == [["systemctl", "--user", "stop", "puffin-index-*"]]
    # No systemd at all (a container): nothing to stop, and no error.
    monkeypatch.setattr(
        "dreamference.vllm_server.vllm_server_manager.subprocess.run",
        lambda command, **kwargs: (_ for _ in ()).throw(FileNotFoundError("systemctl")),
    )
    VLLMServerManager._stop_index_scopes()


def test_tests_cannot_reach_the_user_systemd():
    with pytest.raises(AssertionError, match="real systemd command"):
        subprocess.run(["systemctl", "--user", "stop", "puffin-index-*"])
    with pytest.raises(AssertionError, match="real systemd command"):
        subprocess.run(["systemd-run", "--user", "--scope", "true"])
