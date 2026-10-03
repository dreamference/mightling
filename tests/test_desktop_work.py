"""
The desktop app's Work window (specs/DREAMFERENCE_PUFFIN_DESKTOP.md): Chat stays exactly as it was,
Chat gets no IPC, the bridge only sends methods the pinned Codex has, and the committed protocol
types are the ones the pinned Codex would generate.
"""

import json
import os
import re
from unittest.mock import patch

import pytest

from dreamference.chat import DesktopRunner
from dreamference.chat.desktop_protocol_types import PROTOCOL_DIR, DesktopProtocolTypes
from dreamference.chat.desktop_runner import DESKTOP_PROJECT_DIR, UI_DIR

TAURI_DIR = os.path.join(DESKTOP_PROJECT_DIR, "src-tauri")

# The Chat window as it was before Work existed (tauri.conf.json at 2026-10-03). Any difference is
# a change to what every current user sees.
CHAT_WINDOW = {
    "label": "puffin",
    "title": "Puffin",
    "url": "http://localhost:3000/app",
    "width": 1280,
    "height": 860,
    "minWidth": 720,
    "minHeight": 520,
    "resizable": True,
    "center": True,
    "backgroundColor": "#ffffff",
}


def read(*parts: str) -> str:
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


def test_the_chat_window_is_exactly_as_it_was():
    config = json.loads(read(TAURI_DIR, "tauri.conf.json"))
    assert config["app"]["windows"] == [CHAT_WINDOW]
    main = read(TAURI_DIR, "src", "main.rs")
    # Its sign-in script and the forwarder's port rewrite still reach it, and only it.
    assert 'const CHAT_LABEL: &str = "puffin";' in main
    assert "webview.label() == CHAT_LABEL && payload.event()" in main
    assert ".filter(|w| w.label == CHAT_LABEL)" in main
    assert 'format!("http://localhost:{port}/app")' in main
    # Work opens only when asked for: no argument, no Work window.
    assert "if work.is_some() {\n                bridge::show_work" in main


def test_chat_has_no_ipc_and_work_only_its_own():
    # A remote page gets IPC only from a capability that names it; the bridge also refuses any
    # window but Work. Onyx's page must never reach the agent (§4.2, §8.1).
    capabilities_dir = os.path.join(TAURI_DIR, "capabilities")
    for name in os.listdir(capabilities_dir):
        capability = json.loads(read(capabilities_dir, name))
        assert "puffin" not in capability.get("windows", []), name
        assert "remote" not in capability, name
    bridge = read(TAURI_DIR, "src", "bridge.rs")
    commands = re.findall(r"#\[tauri::command\]\npub fn (\w+)\(([^)]*)\)", bridge)
    assert commands, "no commands found"
    for name, _params in commands:
        body = bridge.split(f"pub fn {name}(", 1)[1].split("\n}\n", 1)[0]
        assert "only_work(&window)?" in body, name


def test_work_loads_nothing_from_the_network():
    csp = json.loads(read(TAURI_DIR, "tauri.conf.json"))["app"]["security"]["csp"]
    assert "default-src 'self'" in csp
    assert "http:" not in csp.replace("http://ipc.localhost", "") and "https:" not in csp
    assert json.loads(read(TAURI_DIR, "tauri.conf.json"))["build"]["frontendDist"] == "../ui/dist"


def test_the_bridge_sends_only_methods_the_pinned_codex_has():
    lib = read(DESKTOP_PROJECT_DIR, "bridge", "src", "lib.rs")
    allowed = re.findall(r'^    "([a-zA-Z/]+)",$', lib.split("pub const ALLOWED_REQUESTS", 1)[1].split("];", 1)[0], re.M)
    known = set(re.findall(r'"method": "([^"]+)"', read(PROTOCOL_DIR, "ClientRequest.ts")))
    assert allowed and set(allowed) <= known, set(allowed) - known
    # The methods §4.3 names as never to be sent are not on the list.
    for prefix in ("feedback/", "account/login", "account/bedrock", "remoteControl/", "thread/realtime/", "userVerification/"):
        assert not [method for method in allowed if method.startswith(prefix)], prefix


def test_the_ui_is_built_before_every_tauri_build():
    real_isdir = os.path.isdir
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "_tauri_command", return_value=["tauri", "build"]), \
         patch("os.path.isdir", side_effect=lambda path: False if path.endswith("node_modules") else real_isdir(path)), \
         patch("shutil.which", return_value="/usr/bin/npm"), \
         patch("subprocess.call", return_value=0) as call:
        assert DesktopRunner.build() == 0
    commands = [(c.args[0], c.kwargs.get("cwd")) for c in call.call_args_list]
    assert commands[:2] == [(["/usr/bin/npm", "ci", "--no-audit", "--no-fund"], UI_DIR), (["/usr/bin/npm", "run", "build"], UI_DIR)]
    assert commands[2] == (["tauri", "build"], DESKTOP_PROJECT_DIR)


def test_no_npm_means_no_build():
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch("shutil.which", return_value=None), \
         patch("subprocess.call") as call:
        assert DesktopRunner.build() == 1
        call.assert_not_called()


@pytest.mark.skipif(not DesktopProtocolTypes.has_source(), reason="needs the Codex submodule and zstd")
def test_the_committed_protocol_types_are_the_pinned_codexs():
    # `puffin app-server generate-ts --experimental` writes exactly these: a Codex bump that moves
    # the protocol must regenerate them (DesktopProtocolTypes.write()) and fix what no longer compiles.
    assert DesktopProtocolTypes.drift() == []
