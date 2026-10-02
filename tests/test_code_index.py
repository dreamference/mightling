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


def test_the_pins_name_every_tool_with_a_sha256():
    pins = {tool.name: tool for tool in CodeIndexSetup.pins()}
    assert set(pins) == {"codebase-memory-mcp", "scip", "scip-go", "scip-java", "scip-dotnet"}
    for tool in pins.values():
        assert len(tool.sha256) == 64
        assert tool.url.startswith(("https://github.com/", "https://api.nuget.org/")), tool.url
    # linux-arm64 builds, the platform Puffin targets; scip-clang has none and is not pinned.
    assert "arm64" in pins["scip-go"].url and "scip-clang" not in pins
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


def test_the_npm_lockfile_pins_both_npm_indexers_by_integrity():
    import json

    with open(os.path.join(code_setup.INDEXERS_SOURCE_DIR, "package-lock.json")) as handle:
        lock = json.load(handle)
    for name, version in [("scip-python", "0.6.6"), ("scip-typescript", "0.4.0")]:
        entry = lock["packages"][f"node_modules/@sourcegraph/{name}"]
        assert entry["version"] == version and entry["integrity"].startswith("sha512-")
    # `npm ci --ignore-scripts` runs no package script; none of these packages declares one.
    assert not [k for k, v in lock["packages"].items() if v.get("hasInstallScript")]


def test_a_raw_download_installs_as_the_program_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(code_setup, "INSTALL_DIR", str(tmp_path / "install"))
    launcher = b"#!/usr/bin/env sh\nexec java -jar \"$0\"\nPK..."
    tool = PinnedTool(hashlib.sha256(launcher).hexdigest(), "scip-java", "0.13.1", "https://github.com/x/scip-java", "-")
    monkeypatch.setattr(CodeIndexSetup, "_download", classmethod(lambda cls, url: launcher))
    assert CodeIndexSetup.install_tool(tool) is True
    installed = tmp_path / "install" / "bin" / "scip-java"
    assert installed.read_bytes() == launcher and os.access(installed, os.X_OK)
    assert CodeIndexSetup.install_tool(tool._replace(sha256="0" * 64)) is False


def _nupkg(frameworks) -> bytes:
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for framework in frameworks:
            package.writestr(f"tools/net{framework}.0/any/scip-dotnet.dll", f"net{framework}")
            package.writestr(f"tools/net{framework}.0/any/Google.Protobuf.dll", "x")
        package.writestr("scip-dotnet.nuspec", "<package/>")
    return buffer.getvalue()


def test_scip_dotnet_unpacks_the_build_for_the_recorded_sdk(tmp_path, monkeypatch):
    monkeypatch.setattr(code_setup, "INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(code_setup, "INDEXERS_DIR", str(tmp_path / "indexers"))
    package = _nupkg([6, 8, 10])
    tool = PinnedTool(hashlib.sha256(package).hexdigest(), "scip-dotnet", "0.2.14", "https://api.nuget.org/x.nupkg", "tools/")
    monkeypatch.setattr(CodeIndexSetup, "_download", classmethod(lambda cls, url: package))
    # An SDK 9: the newest build it runs is net8.0.
    monkeypatch.setattr(CodeIndexSetup, "dotnet_major", classmethod(lambda cls, dotnet: 9))
    assert CodeIndexSetup.install_tool(tool) is True
    dll = tmp_path / "indexers" / "scip-dotnet" / "scip-dotnet.dll"
    assert dll.read_text() == "net8"
    assert (tmp_path / "indexers" / "scip-dotnet" / "Google.Protobuf.dll").is_file()
    assert CodeIndexSetup.is_installed(tool)
    # An SDK older than every build in the package installs nothing.
    monkeypatch.setattr(CodeIndexSetup, "dotnet_major", classmethod(lambda cls, dotnet: 5))
    (tmp_path / "install" / "bin" / ".scip-dotnet.sha256").unlink()
    assert CodeIndexSetup.install_tool(tool) is False


def test_toolchains_are_found_once_and_only_when_new_enough(tmp_path, monkeypatch):
    goroot = tmp_path / "go"
    (goroot / "bin").mkdir(parents=True)
    (goroot / "bin" / "go").write_text("")
    jdk = tmp_path / "jdk"
    (jdk / "bin").mkdir(parents=True)
    (jdk / "bin" / "javac").write_text("")
    sdk = tmp_path / "dotnet"
    sdk.mkdir()
    (sdk / "dotnet").write_text("")
    which = {"go": str(goroot / "bin" / "go"), "dotnet": str(sdk / "dotnet")}
    monkeypatch.setattr(code_setup.shutil, "which", lambda name: which.get(name))
    monkeypatch.setenv("JAVA_HOME", str(jdk))
    answers = {"env": f"{goroot}\n", "-version": "javac 21.0.4\n", "--list-sdks": "8.0.404 [/x]\n9.0.101 [/x]\n"}
    monkeypatch.setattr(CodeIndexSetup, "_version_of", classmethod(lambda cls, command: answers.get(command[1])))
    found = CodeIndexSetup.find_toolchains()
    assert found == {"go": str(goroot), "java": str(jdk), "dotnet": str(sdk)}
    # A Java 8 compiler cannot run scip-java (Java 17 bytecode); a .NET 7 SDK is unsupported.
    answers.update({"-version": "javac 1.8.0_492\n", "--list-sdks": "7.0.410 [/x]\n"})
    assert CodeIndexSetup.find_toolchains() == {"go": str(goroot)}

    monkeypatch.setattr(code_setup, "INDEXERS_DIR", str(tmp_path / "indexers"))
    CodeIndexSetup.record_toolchains({"go": str(goroot), "java": str(jdk)})
    assert os.readlink(tmp_path / "indexers" / "go") == str(goroot)
    # A toolchain no longer found loses its link, so puffin-code never runs a stale path.
    CodeIndexSetup.record_toolchains({"go": str(goroot)})
    assert not os.path.lexists(tmp_path / "indexers" / "java")


def test_maven_and_gradle_are_recorded_by_their_homes(tmp_path, monkeypatch):
    # scip-java runs `mvn` or `gradle`. One installed under the home directory is neither on the
    # indexing sandbox's PATH nor visible in it, so setup records where it really lives.
    maven = tmp_path / "sdk" / "maven-3.9.9"
    (maven / "bin").mkdir(parents=True)
    (maven / "bin" / "mvn").write_text("")
    links = tmp_path / "links"
    links.mkdir()
    os.symlink(maven / "bin" / "mvn", links / "mvn")
    # A `gradle` that is a lone script, with no home around it, is not recorded.
    (links / "gradle").write_text("")
    which = {"mvn": str(links / "mvn"), "gradle": str(links / "gradle")}
    monkeypatch.setattr(code_setup.shutil, "which", lambda name: which.get(name))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    assert CodeIndexSetup.find_toolchains() == {"maven": str(maven)}

    monkeypatch.setattr(code_setup, "INDEXERS_DIR", str(tmp_path / "indexers"))
    CodeIndexSetup.record_toolchains({"maven": str(maven)})
    assert os.readlink(tmp_path / "indexers" / "maven") == str(maven)
    CodeIndexSetup.record_toolchains({})
    assert not os.path.lexists(tmp_path / "indexers" / "maven")


def test_indexers_without_their_toolchain_are_skipped_not_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(code_setup, "INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(code_setup, "INDEXERS_DIR", str(tmp_path / "indexers"))
    monkeypatch.setattr(CodeIndexSetup, "find_toolchains", classmethod(lambda cls: {}))
    installed = []
    monkeypatch.setattr(CodeIndexSetup, "install_tool", classmethod(lambda cls, tool: installed.append(tool.name) or True))
    monkeypatch.setattr(CodeIndexSetup, "install_npm_indexers", classmethod(lambda cls: True))
    assert CodeIndexSetup.install() is True
    assert installed == ["codebase-memory-mcp", "scip"]


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
