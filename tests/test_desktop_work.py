"""The desktop app's Work window (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md, on Electron per
specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md).

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
from dreamference.chat.desktop_runner import ELECTRON_DIR, UI_DIR

SRC = Path(ELECTRON_DIR) / "src"
NPM = "/usr/bin/npm"

# The Chat window as it was before Work existed (tauri.conf.json at 2026-10-03), now in app.json.
# Any difference is a change to what every current user sees.
CHAT_WINDOW = {
    "label": "ling",
    "title": "Mightling",
    "url": "http://localhost:3000/app",
    "width": 1280,
    "height": 860,
    "minWidth": 720,
    "minHeight": 520,
    "backgroundColor": "#ffffff",
}

# §4.3: method families the window must never send.
NEVER_SENT = (
    "feedback/", "account/login", "account/bedrock", "remoteControl/", "thread/realtime/",
    "userVerification/",
)


def app_config() -> dict:
    return json.loads((Path(ELECTRON_DIR) / "app.json").read_text(encoding="utf-8"))


def test_the_chat_window_is_exactly_as_it_was():
    assert app_config()["chat"] == CHAT_WINDOW
    chat = (SRC / "chat.ts").read_text(encoding="utf-8")
    # Its sign-in (now from the main process, on its own session) and the forwarder's port
    # rewrite still reach it, and only it.
    assert "new WindowSignIn(origin" in chat and "sessionHttp(window.webContents.session)" in chat
    assert "executeJavaScript" not in chat
    assert "url.port = String(port)" in chat
    # Work opens only when asked for: no argument, no Work window.
    main = (SRC / "main.ts").read_text(encoding="utf-8")
    assert "if (options.work) showWork();\n  else showChat();" in main


def test_chat_has_no_ipc_and_work_only_its_own():
    # Chat has no preload, so nothing in Onyx's page can reach the main process; Work's one
    # channel is answered only for Work's own window (§4.2, §8.1).
    chat = (SRC / "chat.ts").read_text(encoding="utf-8")
    assert "preload:" not in chat
    assert "sandbox: true, contextIsolation: true, nodeIntegration: false" in chat
    main = (SRC / "main.ts").read_text(encoding="utf-8")
    assert 'if (!work || event.sender.id !== work.webContents.id) throw new Error("only the Work window talks to the agent");' in main
    preload = (SRC / "preload.ts").read_text(encoding="utf-8")
    assert "contextBridge.exposeInMainWorld" in preload and "require(" not in preload.replace('require("electron")', "")


def test_work_loads_nothing_from_the_network():
    csp = app_config()["csp"]
    assert csp.startswith("default-src 'none'")
    assert "https:" not in csp
    assert "http:" not in csp.replace("http://localhost:3000", "").replace("http://localhost:33000", "")
    # The page carries the same policy as a meta tag, and the scheme sends it as a header.
    index = (UI_DIR / "index.html").read_text(encoding="utf-8")
    assert f'content="{csp}"' in index
    protocol = (SRC / "app-protocol.ts").read_text(encoding="utf-8")
    assert 'headers["Content-Security-Policy"] = appConfig.csp' in protocol


def test_the_bridge_sends_only_methods_the_pinned_codex_has():
    bridge = (SRC / "bridge.ts").read_text(encoding="utf-8")
    listed = bridge.split("export const ALLOWED_REQUESTS", 1)[1].split("];", 1)[0]
    allowed = set(re.findall(r'"([a-zA-Z/]+)"', listed))
    client_requests = (PROTOCOL_DIR / "ClientRequest.ts").read_text(encoding="utf-8")
    known = set(re.findall(r'"method": "([^"]+)"', client_requests))
    assert allowed
    assert allowed <= known, allowed - known
    for family in NEVER_SENT:
        assert not [method for method in allowed if method.startswith(family)], family


def test_the_agent_is_spawned_as_the_codex_app_spawns_its_own():
    server = (SRC / "server.ts").read_text(encoding="utf-8")
    assert '["-c", "features.code_mode_host=true", "app-server"]' in server
    assert 'LOG_FORMAT: "json"' in server and 'RUST_LOG: process.env.RUST_LOG ?? "warn"' in server
    bridge = (SRC / "bridge.ts").read_text(encoding="utf-8")
    assert "app.asar.unpacked" in bridge and "MIGHTLING_BIN" in bridge


def test_the_packages_are_installed_before_every_make():
    real_is_dir = Path.is_dir

    def no_node_modules(path: Path) -> bool:
        return path.name != "node_modules" and real_is_dir(path)

    commands = []
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "copy_bundled_binaries", return_value=True), \
         patch.object(DesktopRunner, "install_desktop_entry"), \
         patch.object(Path, "is_dir", autospec=True, side_effect=no_node_modules), \
         patch("shutil.which", return_value=NPM), \
         patch("subprocess.call", side_effect=lambda command, **kw: commands.append((command, kw.get("cwd"))) or 0):
        assert DesktopRunner.build() == 0
    assert commands == [
        ([NPM, "ci", "--no-audit", "--no-fund"], UI_DIR),
        ([NPM, "ci", "--no-audit", "--no-fund"], Path(ELECTRON_DIR)),
        ([NPM, "run", "make"], Path(ELECTRON_DIR)),
    ]


def test_no_npm_means_no_build():
    real_is_dir = Path.is_dir
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(Path, "is_dir", autospec=True, side_effect=lambda path: path.name != "node_modules" and real_is_dir(path)), \
         patch("shutil.which", return_value=None), \
         patch("subprocess.call") as call:
        assert DesktopRunner.build() == 1
        call.assert_not_called()


@pytest.mark.skipif(not DesktopProtocolTypes.has_source(), reason="needs the Codex submodule and zstd")
def test_the_committed_protocol_types_are_the_pinned_codexs():
    # `ling app-server generate-ts --experimental` writes exactly these: a Codex bump that moves
    # the protocol must regenerate them (DesktopProtocolTypes.write()) and fix what stops compiling.
    assert DesktopProtocolTypes.drift() == []
