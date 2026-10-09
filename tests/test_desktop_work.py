"""The desktop app's window (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md, on Electron per
specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md).

Ask and Work share the one app window and its one app-server (the separate Ask window on
`ling web` was folded into it on 2026-10-09, specs/DREAMFERENCE_MIGHTLING_ASK.md §18.6), the
bridge only sends methods the pinned Codex has, and the committed protocol types are the ones the
pinned Codex would generate.
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

# §4.3: method families the window must never send.
NEVER_SENT = (
    "feedback/", "account/login", "account/bedrock", "remoteControl/", "thread/realtime/",
    "userVerification/",
)


def app_config() -> dict:
    return json.loads((Path(ELECTRON_DIR) / "app.json").read_text(encoding="utf-8"))


def test_ask_is_a_view_of_the_one_app_window_not_a_window_on_ling_web():
    # The menu's Ask, the tray's and `ling app` alone show Ask in the app window; nothing in the
    # app starts, signs in to or loads `ling web`, so its app-server is the only one on the home.
    config = app_config()
    assert "chat" not in config and config["work"]["title"] == "Mightling"
    for retired in ("chat.ts", "web.ts", "sign-in.ts", "forwarder.ts", "discover.ts"):
        assert not (SRC / retired).exists(), retired
    for source in SRC.glob("*.ts"):
        if source.name == "credentials.test.ts":  # names what must be absent from the bundles
            continue
        text = source.read_text(encoding="utf-8")
        assert '"web", "serve"' not in text and "--print-url" not in text and "127.0.0.1:3100" not in text, source.name
    main = (SRC / "main.ts").read_text(encoding="utf-8")
    assert "openWork(" in main and main.count("new BrowserWindow") == 0
    assert 'const actions = { openAsk: () => showView("ask"), openWork: () => showView("work") };' in main
    assert "installMenu(actions);" in main
    # No argument opens Ask; `--work`, a folder, a thread or a link open Work.
    assert 'showView(options.work ? "work" : "ask");' in main
    # A window already open is switched, never doubled.
    assert 'deliver({ channel: "view", payload: view });' in main
    assert '"work/open-chat"' not in (SRC / "api.ts").read_text(encoding="utf-8")


def test_the_app_window_alone_has_ipc_and_only_for_itself():
    # The one channel is answered only for the app window's own webContents (§4.2, §8.1), and the
    # window's renderer gets no Node.
    work = (SRC / "work.ts").read_text(encoding="utf-8")
    assert re.search(r"sandbox: true,\s*contextIsolation: true,\s*nodeIntegration: false", work)
    main = (SRC / "main.ts").read_text(encoding="utf-8")
    assert 'if (!window || event.sender.id !== window.webContents.id) throw new Error("only the app window talks to the agent");' in main
    preload = (SRC / "preload.ts").read_text(encoding="utf-8")
    assert "contextBridge.exposeInMainWorld" in preload and "require(" not in preload.replace('require("electron")', "")


def test_work_loads_nothing_from_the_network():
    csp = app_config()["csp"]
    assert csp.startswith("default-src 'none'")
    assert "https:" not in csp
    assert "http:" not in csp and "frame-src" not in csp
    # The page carries the same policy as a meta tag, and the scheme sends it as a header.
    index = (UI_DIR / "index.html").read_text(encoding="utf-8")
    assert f'content="{csp}"' in index
    protocol = (SRC / "app-protocol.ts").read_text(encoding="utf-8")
    assert 'headers["Content-Security-Policy"] = appConfig.csp' in protocol


def test_the_bridge_sends_only_methods_the_pinned_codex_has():
    # One policy for both hosts (ASK §2.3): the app's main process compiles in the file `ling web`
    # compiles in, and runs its conformance cases (policy.test.ts).
    policy_ts = (SRC / "policy.ts").read_text(encoding="utf-8")
    assert 'import policyFile from "../../../ling-rs/web/policy.json";' in policy_ts
    assert "ALLOWED_REQUESTS" not in (SRC / "bridge.ts").read_text(encoding="utf-8")
    policy = json.loads((Path(ELECTRON_DIR).parent.parent / "ling-rs" / "web" / "policy.json").read_text(encoding="utf-8"))
    allowed = set(policy["allowedRequests"])
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
