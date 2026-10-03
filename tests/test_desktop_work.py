"""The desktop app's Work window (specs/DREAMFERENCE_PUFFIN_DESKTOP.md).

Chat stays exactly as it was, Chat gets no IPC, the bridge only sends methods the pinned Codex
has, and the committed protocol types are the ones the pinned Codex would generate.
"""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from dreamference.chat import DesktopRunner
from dreamference.chat.desktop_protocol_types import PROTOCOL_DIR, DesktopProtocolTypes
from dreamference.chat.desktop_runner import DESKTOP_PROJECT_DIR, UI_DIR

DESKTOP = Path(DESKTOP_PROJECT_DIR)
TAURI = DESKTOP / "src-tauri"
NPM = "/usr/bin/npm"

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

# §4.3: method families the window must never send.
NEVER_SENT = (
    "feedback/", "account/login", "account/bedrock", "remoteControl/", "thread/realtime/",
    "userVerification/",
)


def tauri_config() -> dict:
    return json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))


def test_the_chat_window_is_exactly_as_it_was():
    assert tauri_config()["app"]["windows"] == [CHAT_WINDOW]
    main = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
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
    for path in (TAURI / "capabilities").iterdir():
        capability = json.loads(path.read_text(encoding="utf-8"))
        assert "puffin" not in capability.get("windows", []), path.name
        assert "remote" not in capability, path.name
    bridge = (TAURI / "src" / "bridge.rs").read_text(encoding="utf-8")
    commands = re.findall(r"#\[tauri::command\]\npub fn (\w+)\(", bridge)
    assert commands, "no commands found"
    for name in commands:
        body = bridge.split(f"pub fn {name}(", 1)[1].split("\n}\n", 1)[0]
        assert "only_work(&window)?" in body, name


def test_work_loads_nothing_from_the_network():
    config = tauri_config()
    csp = config["app"]["security"]["csp"]
    assert "default-src 'self'" in csp
    assert "https:" not in csp
    assert "http:" not in csp.replace("http://ipc.localhost", "")
    assert config["build"]["frontendDist"] == "../ui/dist"


def test_the_bridge_sends_only_methods_the_pinned_codex_has():
    lib = (DESKTOP / "bridge" / "src" / "lib.rs").read_text(encoding="utf-8")
    listed = lib.split("pub const ALLOWED_REQUESTS", 1)[1].split("];", 1)[0]
    allowed = set(re.findall(r'^ {4}"([a-zA-Z/]+)",$', listed, re.MULTILINE))
    client_requests = (PROTOCOL_DIR / "ClientRequest.ts").read_text(encoding="utf-8")
    known = set(re.findall(r'"method": "([^"]+)"', client_requests))
    assert allowed
    assert allowed <= known, allowed - known
    for family in NEVER_SENT:
        assert not [method for method in allowed if method.startswith(family)], family


def test_the_ui_is_built_before_every_tauri_build():
    real_is_dir = Path.is_dir

    def no_node_modules(path: Path) -> bool:
        return path.name != "node_modules" and real_is_dir(path)

    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "_tauri_command", return_value=["tauri", "build"]), \
         patch.object(Path, "is_dir", autospec=True, side_effect=no_node_modules), \
         patch("shutil.which", return_value=NPM), \
         patch("subprocess.call", return_value=0) as call:
        assert DesktopRunner.build() == 0
    commands = [(c.args[0], c.kwargs.get("cwd")) for c in call.call_args_list]
    assert commands == [
        ([NPM, "ci", "--no-audit", "--no-fund"], UI_DIR),
        ([NPM, "run", "build"], UI_DIR),
        (["tauri", "build"], DESKTOP_PROJECT_DIR),
    ]


def test_no_npm_means_no_build():
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch("shutil.which", return_value=None), \
         patch("subprocess.call") as call:
        assert DesktopRunner.build() == 1
        call.assert_not_called()


@pytest.mark.skipif(not DesktopProtocolTypes.has_source(), reason="needs the Codex submodule and zstd")
def test_the_committed_protocol_types_are_the_pinned_codexs():
    # `puffin app-server generate-ts --experimental` writes exactly these: a Codex bump that moves
    # the protocol must regenerate them (DesktopProtocolTypes.write()) and fix what stops compiling.
    assert DesktopProtocolTypes.drift() == []
