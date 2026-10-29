"""The desktop app's windows (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md, on Electron per
specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md).

Ask and Work are views of each app window, and every window shares the app's one app-server (the
separate Ask window on `ling web` was folded in on 2026-10-09, specs/DREAMFERENCE_MIGHTLING_ASK.md
§18.6; several windows since 2026-10-22, DESKTOP_ELECTRON §13), the bridge only sends methods the
pinned Codex has, and the committed protocol types are the ones the pinned Codex would generate.
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


def test_ask_is_a_view_of_the_app_windows_not_a_window_on_ling_web():
    # The menu's Ask, the tray's and `ling app` alone show Ask in the most recent app window (or a
    # new one when none is open); nothing in the app starts, signs in to or loads `ling web`, so its
    # app-server is the only one on the home, whatever the number of windows.
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
    assert 'openAsk: () => showView("ask"),' in main and 'openWork: () => showView("work"),' in main
    assert "newWindow: (from: BrowserWindow | null) => newWindow(from)," in main
    assert "installMenu(actions);" in main
    # No argument opens Ask; `--work`, a folder, a thread or a link open Work, in the most recent window.
    assert 'showView(options.work ? "work" : "ask", options.work ?? undefined);' in main
    assert 'view: last ? (options.work ? "work" : "ask") : saved.view' in main
    # A window already open is switched, never doubled; one window, hidden, in the audit's session.
    assert 'send(existing, { channel: "view", payload: view });' in main
    assert "const restored = options.audit === null ? session.restored() : [];" in main
    assert '"work/open-chat"' not in (SRC / "api.ts").read_text(encoding="utf-8")


def test_the_app_window_alone_has_ipc_and_only_for_itself():
    # The one channel is answered only for the app's own windows' webContents (§4.2, §8.1), and their
    # renderers get no Node.
    work = (SRC / "work.ts").read_text(encoding="utf-8")
    assert re.search(r"sandbox: true,\s*contextIsolation: true,\s*nodeIntegration: false", work)
    main = (SRC / "main.ts").read_text(encoding="utf-8")
    # The app's windows are its full windows, pop-outs and detached panes (DESKTOP_ELECTRON §13), all
    # on the one app-server and all made by `openWork` with the same webPreferences; anything else is
    # refused, and every message is routed per window before the policy vets it.
    assert 'const own = windowFor(event.sender.id);' in main
    assert 'if (!own) throw new Error("only the app\'s windows talk to the agent");' in main
    assert "windows().find((entry) => entry.webContents.id === senderId)" in main
    assert "const routed = hub.outgoing(String(own.webContents.id), message.message);" in main
    assert "webviewTag: false" in work
    assert 'contents.on("will-attach-webview", (event) => event.preventDefault());' in work
    preload = (SRC / "preload.ts").read_text(encoding="utf-8")
    assert "contextBridge.exposeInMainWorld" in preload and "require(" not in preload.replace('require("electron")', "")



def test_the_preview_tab_gets_nothing_of_the_app():
    # The preview tab (DESKTOP_PARITY §2.9, Phase 3 item 4) shows pages that are not the app's: its
    # view has no preload, the renderer's sandbox, no Node, a session of its own that grants no
    # permission, and every request goes through the rules of preview-rules.ts before it connects.
    preview = (SRC / "preview.ts").read_text(encoding="utf-8")
    assert re.search(r"sandbox: true,\s*contextIsolation: true,\s*nodeIntegration: false,\s*webSecurity: true", preview)
    assert "preload" not in preview.split("new WebContentsView(")[1].split("});")[0]
    assert "setPermissionRequestHandler((_contents, _permission, callback) => callback(false))" in preview
    assert "setPermissionCheckHandler(() => false)" in preview
    assert 'webRequest.onBeforeRequest({ urls: ["<all_urls>"] }' in preview
    assert 'return { action: "deny" };' in preview
    rules = (SRC / "preview-rules.ts").read_text(encoding="utf-8")
    assert 'if (airgapOn) return { verdict: "block"' in rules

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


def test_work_ships_no_telemetry_sdk_and_no_updater():
    # §8.1: no telemetry SDK in the bundle and no auto-updater; updates come from `ling update`.
    banned = re.compile(
        r"telemetry|analytics|sentry|statsig|posthog|segment|mixpanel|amplitude|updater|update-electron-app"
    )
    for folder in (Path(ELECTRON_DIR), UI_DIR):
        package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        names = {*package.get("dependencies", {}), *package.get("devDependencies", {})}
        assert not [name for name in names if banned.search(name)], folder
    assert "publishers" not in (Path(ELECTRON_DIR) / "forge.config.js").read_text(encoding="utf-8")
    for source in [*SRC.rglob("*.ts"), *(UI_DIR / "src").rglob("*.ts*")]:
        if ".test." in source.name:
            continue
        text = source.read_text(encoding="utf-8")
        assert "autoUpdater" not in text and "electron-updater" not in text, source

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
    # The stamp a real install leaves (desktop_runner.LOCK_STAMP) is not written into the checkout.
    with patch.object(DesktopRunner, "_ensure_toolchain", return_value=True), \
         patch.object(DesktopRunner, "copy_bundled_binaries", return_value=True), \
         patch.object(DesktopRunner, "install_desktop_entry"), \
         patch.object(DesktopRunner, "mark_installed"), \
         patch.object(DesktopRunner, "packages_current", return_value=False), \
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
