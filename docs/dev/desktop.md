# The desktop app

Developer notes behind the desktop line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md`, whose §9 records what was built and measured.

The desktop app is Electron, built the way the Codex desktop app is built, with `ling` inside (`chat/desktop_runner.py`, project in `desktop/electron/`, Work's UI in `desktop/ui/`).

## Why Electron

Until 2026-10-07 it was a Tauri shell on the system's WebKitGTK, and typing into it on this GB10 trailed the keys: WebKitGTK's one GPU path asks the DRM device for a dumb buffer, which the proprietary NVIDIA driver refuses, so the app drew on the CPU at 40–50 ms per keystroke frame in a near-full-screen window (measured in an offscreen view, ELECTRON §1). Chromium renders through ANGLE on NVIDIA's own GL and never asks.

## Shape

- Electron is **stock 42.x, pinned to a patch release** (the Codex app runs the upstream vendor's own build of the same major). Forge 7 packages it (`maker-deb`); Vite builds the main process (`src/early-bootstrap.ts` → `bootstrap.ts` → `main.ts`), the preload and Work's React UI.
- `app.json` holds the product name, identifier, scheme and the two windows' settings, read by the app and by the Python side alike.
- **Chat** is a `BrowserWindow` on `http://localhost:3000/app` with **no preload and no IPC**.
- **Work** loads `app://-/index.html` from the asar (`app-protocol.ts`: a privileged scheme serving the bundle with a strict CSP, cancelling requests from any other origin) and talks to the main process over **one IPC channel** behind `window.electronBridge.sendMessageFromView`, answered only for Work's own `webContents`.
- The bridge's guarantees moved from Rust to `bridge.ts` unchanged: the method allow-list (a test checks it against the committed protocol types), the thread fields never sent, answers only to pending server requests, the busy marker Night Shift reads (`server.ts` writes it).
- `server.ts` spawns `<resources>/ling -c features.code_mode_host=true app-server` with `LOG_FORMAT=json`, `RUST_LOG=warn` and the resources folder on PATH, as the Codex app spawns its agent. `ling` and `codex-code-mode-host` are copied from the installed build into `desktop/electron/resources/` by `desktop build` (the release workflow takes them from the `ling` job's artifact) and ride along as extra resources.
- On a client, Chat keeps loading `http://localhost:3000/app` through a TCP forwarder (`desktop/electron/src/forwarder.ts`), and `desktop/electron/src/node_locator.ts` ports the node-locator crate; see [node.md](node.md).

## Six things to keep

1. **No GPU switches, no `WEBKIT_*`, no `GTK_THEME`, no `--no-sandbox`**: Electron's defaults, as the Codex app runs. A test greps the sources for each.
2. **Chromium's sandbox needs a user namespace, and Ubuntu refuses one to programs without an AppArmor profile** (the same `kernel.apparmor_restrict_unprivileged_userns` rule as `bwrap`, see [host-safety.md](host-safety.md#bubblewrap-needs-an-apparmor-profile-and-this-machine-now-has-one)). The `.deb`'s `postinst` writes `/etc/apparmor.d/mightling` (`profile mightling "/usr/lib/mightling/Mightling" flags=(unconfined) { userns, }`, the Codex app's shape) and loads it; `prerm` removes it; **no `chrome-sandbox` SUID helper is shipped** (the packager's `afterCopy` deletes it). For a checkout, `desktop install` writes `mightling-desktop-dev` for npm's `electron` and the packaged binary, with sudo, announced first. From a terminal under PyCharm's permissive profile the app starts without any of this, which is why the problem only shows from the launcher or a plain terminal: `DesktopInstaller.userns_allowed()` asks as an unconfined program would (`aa-exec -p unconfined`).
3. **Egress is closed in the main process and checked by `ling-admin audit egress --app`.** `egress.ts` always passes `disable-background-networking`, `disable-component-update` and `disable-features=AimEnabled,AutofillServerCommunication,NetworkTimeServiceQuerying,MediaRouter`; permission requests are denied except the microphone for Chat's origin; the spellchecker's dictionary download is pointed at `app://` (so it fails locally) and `window.open` goes to the system browser. Fuses: `RunAsNode`, `EnableNodeOptionsEnvironmentVariable`, `EnableNodeCliInspectArguments`, `GrantFileProtocolExtraPrivileges` off; `EnableCookieEncryption`, `EnableEmbeddedAsarIntegrityValidation`, `OnlyLoadAppFromAsar`, `WasmTrapHandlers` on; flipped by a `postPackage` hook with `@electron/fuses` 2, because Forge 7's own fuses plugin pins the 1.x line, which has no `WasmTrapHandlers`. The audit's app mode starts the packaged (or installed) app with `MIGHTLING_APP_AUDIT=<seconds>` (both windows hidden, Chat on the web UI, Work's app-server started, quit by itself), in a scratch `HOME` and `CODEX_HOME`, and adds the web UI's port 3000 to the allowlist for that session alone; it needs a display (`DISPLAY` or `xvfb-run`), because Electron's `--ozone-platform=headless` SIGSEGVs on this machine.
4. **The data folder stays `~/.local/share/dev.dreamference.mightling`** (`app.setPath("userData", …)` after `app.setName`), so `ling app` keeps emptying the HTTP cache there (now Chromium's `Cache`, never its `Cookies`) and the rename migration's move still lands. Work's bounds are persisted beside it (`window-state.ts`, clamped to a display's work area).
5. **Work's window is frameless with native controls** (`titleBarStyle: "hidden"`, a transparent `titleBarOverlay`, 36 px), so Work's own header bar is the title bar: `-webkit-app-region: drag` on `.bar`, `no-drag` on its controls, and 150 px of right padding under the overlay's buttons. Chat keeps a normal frame.
6. **Chat signs in from the main process** (`sign-in.ts`): the web UI's admin password is per install (`~/.config/dreamference/chat-admin.json`, 0600, from the `security/chat-password` branch), read only by the main process and posted to `/api/auth/login` over the Chat window's own session, once per window and only when `/api/me` answers 401/403; the page never sees it (no injected script, no preload), a client without the file gets the login page, and `credentials.test.ts` checks the preload and renderer bundles carry no credential string.

## Also

- **`ling airgapped --thread <id>`** is how Work learns a thread's air-gap level (`airgapped.ts` parses the first line): the launcher's own resolver, not a fourth copy of the crate.
- `ling-admin desktop {install,run,build,status}` needs **Node 20+ and npm only** (and `fakeroot` for the `.deb`); `desktop run` packages the app and runs the packaged binary, never `electron-forge start`, whose dev server Work's `app://` page does not use.
- Night Shift recognises the `ling` bundled inside the app as an app-server by name and `app-server` in its command line, not only the installed binary (`NightShiftHost._is_app_server`).
- `DesktopInstaller`'s rustup helpers stay because the Codex build drives them.
- `DesktopRunner.has_source()` tells a checkout from a release install ([install-modes.md](install-modes.md)).

## Tests

- `npm test` in `desktop/electron` (vitest, the main-process modules; the forwarder tests run real loopback sockets) and in `desktop/ui`.
- `npm run e2e` (Playwright-Electron, needs a display and `npm run package` first).
- The Python tests read `app.json`, `forge.config.js`, `linux/postinst` and the TypeScript sources as text.
