# Mightling Desktop on Electron — the shell rebuilt the way the Codex app is built, with `ling` inside

**Status:** decided by the user on 2026-10-07 ("do everything exactly the way the Codex app does it; instead of codex, ling needs to be inside"), written before the code on the same day, then updated with what was built (§9). It replaces §9 (the webview) and §10 (packaging) of [DREAMFERENCE_MIGHTLING_DESKTOP.md](./DREAMFERENCE_MIGHTLING_DESKTOP.md); everything else in that spec (the two windows, the bridge, Work's UI, the protocol types, the air-gap rule in the server) stands and is carried over unchanged.

**Why:** typing into the Tauri app on this GB10 was slow — letters trailed the keys — while the same page in the browser was not. The cause is the rendering engine, not the page (§1).

---

## 1. Measured: WebKitGTK draws on the CPU here, and the window is large

Measured on 2026-10-07 in an offscreen WebKitGTK 2.52.3 view (`python3-gi`, a textarea with one keystroke per animation frame, 60 frames), with the app's own settings (`WEBKIT_DISABLE_DMABUF_RENDERER=1`):

| Window | Time per keystroke frame | What it feels like |
|---|---|---|
| 1280×860, the app's default size | 16 ms | smooth |
| 1280×860 at 2× scale | 30 ms | laggy |
| 4096×1700, a near-full-screen window on the user's 5120×2160 monitor at 1.25× | **40–50 ms** | letters trail the keys (20–25 fps) |

Every setting that avoids the GPU lands at 40–50 ms for the large window (DMABUF off; compositing off; compositing forced; GLX forced). The GPU path is what the app disables, and it cannot be re-enabled on this driver:

- With the DMABUF renderer on, WebKitGTK asks the kernel for a DRM "dumb buffer" and the NVIDIA driver refuses: `DRM_IOCTL_MODE_CREATE_DUMB` returns **"Function not implemented"** on the NVIDIA card node and **"Permission denied"** on its render node (the user can open both nodes; group membership is not the problem). Only the display-less `card0` allows it. Pointing WebKit at the render node (`WEBKIT_DMABUF_RENDERER_DEVICE`) changes nothing. The app then shows no window at all, which is why `main.rs` disabled the renderer.
- The proprietary NVIDIA driver implements its own buffer allocation and only part of the kernel's generic interface, which WebKitGTK's one GPU path depends on. Chromium renders through ANGLE on NVIDIA's own GL/EGL library and composites into an X11 window through GLX/EGL, never asking for a dumb buffer, and has fallbacks for every driver (SwiftShader last). That is why the browser was fine.

So the slowness is WebKitGTK's GPU design meeting NVIDIA's X11 driver, and the app falling back to CPU drawing on a very large window. Smaller windows are smooth; a Wayland session would likely let WebKitGTK take its buffers from the compositor (untested); neither is the fix the user chose.

## 2. What the Codex desktop app is (read from the installed package, 2026-10-07)

`chatgpt` 26.930.31730 (arm64), `/usr/lib/chatgpt/`, 1.5 GB installed:

| What | Found |
|---|---|
| Shell | **Electron**, with Chromium **154.0.8037.98** bundled: `ChatGPT` (310 MB), `app.asar` (519 MB), `chrome_100_percent.pak`, `chrome_200_percent.pak`, `v8_context_snapshot.bin`, `snapshot_blob.bin`, `icudtl.dat`, `resources.pak`, `browser_crashpad_handler`, `LICENSES.chromium.html`, ANGLE (`libEGL.so`, `libGLESv2.so`), `libvulkan.so.1`, `libvk_swiftshader.so` (the software renderer for when no GPU works), `libqt5_shim.so`, `libqt6_shim.so` |
| Runtime | The upstream vendor's own Electron build, "owl" (`owl-electron-app.json`: `runtimeName: owl`, packaged from `codex/codex-apps/electron/out/ChatGPT-linux-arm64`). No published Electron release carries Chrome 154.0.8037.98: Chrome 154 exists only in Electron 45/46 nightlies (checked against `releases.electronjs.org/releases.json`). |
| Display | Linked to X11, GTK 3 **and** Wayland (`libwayland-client`, `libwayland-egl`), `libgbm`, `libdrm`. **No custom GPU flags**: the only `disable-gpu` string is a settings path. Electron's defaults. |
| The agent | `resources/codex` (249 MB) and `resources/codex-code-mode-host` (67 MB) beside the app; `codex-launcher` execs the same `ChatGPT` binary; the main process spawns the bundled `codex app-server` over stdio (DESKTOP §1). |
| Windows | `protocol.registerSchemesAsPrivileged([{scheme: "app", privileges: {standard, secure, stream, supportFetchAPI}}])`; `sandbox: true`, `contextIsolation: true`, `nodeIntegration: false`, a preload. |
| Sandbox | **No `chrome-sandbox` SUID helper is shipped.** Instead `/etc/apparmor.d/chatgpt`: `profile chatgpt "/usr/lib/chatgpt/ChatGPT" flags=(unconfined) { userns, }`. Ubuntu 24.04 sets `kernel.apparmor_restrict_unprivileged_userns=1`, so Chromium's user-namespace sandbox needs this profile — the same lesson `docs/dev/host-safety.md` records for `bwrap` (`puffin-bwrap`). Measured: Electron 44 started from an unconfined shell on this machine aborts with *"The SUID sandbox helper binary was found, but is not configured correctly"*; from a shell under a profile that grants `userns` it starts. |
| Package | A `.deb` with Chromium's dependencies (`libgtk-3-0`, `libnotify4`, `libnss3`, `libdrm2`, `libgbm1`, `libxcb-dri3-0`, `libasound2`, `libxkbcommon0`, `mesa-vulkan-drivers | vulkan-icd`, …) plus ones for its extras (`libtss2*`, `libusb-1.0-0`). Native modules bundled: `node-pty`, `better-sqlite3`, HID and remote-control modules. |

## 3. Decisions

1. **Electron, bundled Chromium, Electron's defaults.** `desktop/electron/` replaces `desktop/src-tauri/`; `desktop/ui/` (Work's React code and the generated protocol types) stays. No GPU flags, no `WEBKIT_*`, no `GTK_THEME`; the Chat window no longer forces light mode through the toolkit (it follows the system, as a browser does; Onyx's overrides are `html:not(.dark)`-scoped, so a dark desktop shows plain Onyx, as the browser would).
2. **Electron 42.11.11: stock Electron 42, the latest 42.x patch with `linux-arm64` prebuilts** (decided by the coordinator on 2026-10-07, superseding "look it up by Chrome version"). The Codex app runs Electron 42.3.0 on the upstream vendor's own "owl" runtime; its Chromium 154 is not a published Electron (Chrome 154 is only in Electron 45/46 nightlies). Same major as the Codex app, published build, pinned exactly in `package.json`; bumped deliberately. Forge 7.11.2 (`maker-deb`, `maker-zip`, `plugin-vite`, `plugin-auto-unpack-natives`), Vite 8, TypeScript 6, vitest 5, Playwright 1.63. `package.json`: `main: .vite/build/early-bootstrap.js`, `desktopName: mightling.desktop`.
3. **`ling` inside.** `resources/ling` and `resources/codex-code-mode-host` (Codex looks for the host next to its own executable, resolving symlinks) are bundled as Forge extra resources, copied from the installed `ling` build at package time (`DesktopRunner.copy_bundled_binaries`; the release workflow takes them from the `ling` job's artifact); a `rg` on PATH rides along. The main process spawns `<resources>/ling -c features.code_mode_host=true app-server` with `LOG_FORMAT=json`, `RUST_LOG=warn` and the resources folder on PATH, as the Codex app spawns its agent; lookup order `MIGHTLING_BIN` → `resources/ling` → `app.asar.unpacked/ling` → `ling` on PATH → `~/.local/bin/ling`. Never the Codex app's analytics flag. `ling-search`, `ling-fetch` and `ling-code` are not bundled: the app-server reaches them through the launcher's prompt and the install's links, as the TUI does.
4. **The bridge keeps its guarantees** in TypeScript (`bridge.ts`, pure: `vetOutgoing`, `classify`, `BusyTracker`, `findLing`; `server.ts`, the process): the method allow-list (a Python test checks it against the committed protocol types), the thread fields never sent, answers only to pending server requests, the busy marker Night Shift reads. The renderer never gets Node: `sandbox: true`, `contextIsolation: true`, `nodeIntegration: false` on both windows; Work has a preload (`preload.ts`) that exposes one object, `window.electronBridge.sendMessageFromView(message)` → `ipcRenderer.invoke("mightling_desktop:message-from-view")`, and re-dispatches `mightling_desktop:message-for-view` as window `MessageEvent`s (large payloads chunked with acks; the theme read at preload into `documentElement.dataset.theme`), plus `window.mightlingWindowType = "electron"`; `main.ts` answers that channel only for Work's own `webContents`. Chat has no preload and no IPC.
5. **`app://` serves Work's UI** (`app-protocol.ts`): registered privileged (`standard`, `secure`, `stream`, `supportFetchAPI`), `protocol.handle("app")` streams `app://-/<path>` from the bundle with its MIME type (404 otherwise), sends the CSP of `app.json` on HTML (`default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' app: blob: data:; connect-src 'self'; frame-src 'self' http://localhost:3000 http://localhost:33000; worker-src 'self' blob:; base-uri 'none'; form-action 'none'`, also as the page's meta tag), and a session rule cancels `app://` requests from any other origin. Chat loads `http://localhost:<port>/app` through the client forwarder as before; a model server named outright (`DREAMFERENCE_VLLM_HOST`, `MIGHTLING_NODE`) is the launcher's first tier here too, so an unattended run never browses.
6. **Egress hardening in the main process** (`egress.ts`, `early-bootstrap.ts`): always `disable-background-networking`, `disable-component-update`, `disable-features=AimEnabled,AutofillServerCommunication,NetworkTimeServiceQuerying,MediaRouter`; no crash reporter, no auto-updater; permission requests denied except the microphone for Chat's origin; the spellchecker's dictionary download pointed at `app://-/dictionaries/` so it fails locally (`spellcheck: true` stays, as the Codex app has it); `window.open` and foreign navigation sent to the system browser. Fuses, flipped by a `postPackage` hook with `@electron/fuses` 2 (Forge 7's own fuses plugin pins the 1.x line, which has no `WasmTrapHandlers`): on `EnableCookieEncryption`, `EnableEmbeddedAsarIntegrityValidation`, `OnlyLoadAppFromAsar`, `WasmTrapHandlers`; off `RunAsNode`, `EnableNodeOptionsEnvironmentVariable`, `EnableNodeCliInspectArguments`, `GrantFileProtocolExtraPrivileges`. None of the Codex app's Sentry, OTel, Statsig, crash upload, HID, remote-control, `libtss2` or `libusb` is mirrored. Verified by `ling-admin audit egress --app` (§6, §9.3).
7. **Packaging: Electron Forge with `maker-deb`,** as the Codex app is packaged (`out/Mightling-linux-arm64/Mightling`). The `.deb` (`mightling_<version>_arm64.deb`, package `mightling`, conflicts with and replaces the Tauri-era `puffin`/`mightling-app`) installs `/usr/lib/mightling/Mightling`, the maker's `/usr/bin/mightling` link, a `.desktop` entry (`Exec=… %U`, `StartupNotify`, `Categories=Utility;Development;`, `MimeType=x-scheme-handler/mightling`) and a pixmap icon; its `postinst` writes `/etc/apparmor.d/mightling` (`profile mightling "/usr/lib/mightling/Mightling" flags=(unconfined) { userns, include if exists <local/mightling> }`, the Codex app's shape), loads it, and writes `/usr/bin/ling-app` (`exec /usr/lib/mightling/Mightling "$@"`); `prerm` removes both. **No `chrome-sandbox` SUID helper is shipped** (the hook deletes it), as in the Codex app. **No AppImage:** an AppImage cannot carry an AppArmor profile and Chromium's sandbox then needs the SUID helper, which an AppImage cannot install either. No apt repository for now (a later option: the Codex app ships one). Deep links `mightling://thread/<id>` and `mightling://work?cwd=<folder>` open Work; a second instance hands its arguments to the running one.
8. **Toolchain:** Node 20+ and npm only (the release workflow uses 22); `fakeroot` and `dpkg` for the `.deb`. No rustup, no Tauri CLI, no GTK/WebKit headers. `DesktopInstaller.install_rust` and `cargo_path` stay because the Codex build uses them. For a checkout, `desktop install` writes `/etc/apparmor.d/mightling-desktop-dev` for npm's `electron` and the packaged binary, with sudo, announced first, only where `aa-exec -p unconfined unshare -U` fails.
9. **Single instance:** `requestSingleInstanceLock` after `whenReady` → `app.setName` → `app.setPath("userData", ~/.local/share/dev.dreamference.mightling)`; a second `ling-app` hands its arguments to the running one, which shows the window asked for. (Tauri's version ran two processes; the Codex app is single-instance.)
10. **The air-gap level shown in Work** comes from `ling airgapped --thread <id>` (added to the launcher's CLI), parsed from its first line — not a fourth copy of the resolver crate.
11. **Windows.** Chat: `BrowserWindow` 1280×860, min 720×520, white, `autoHideMenuBar`, the settings of `app.json` (unchanged from the Tauri window). Work: frameless with native controls on Linux (`titleBarStyle: "hidden"`, `titleBarOverlay: {color: "#00000000", symbolColor: dark ? "#ffffff" : "#1f1f1f", height: 36}`, `backgroundColor: "#00000000"`, min 480×600), bounds persisted and restored clamped to a display's work area with the maximised state (`window-state.ts`); Work's header bar is the title bar (`-webkit-app-region: drag`, 150 px right padding under the overlay's buttons). Both: `spellcheck: true`, devtools in dev only. Native application menu (File: Chat, Work; Edit; View; Window), a context menu Work asks for over IPC, a tray icon, `powerSaveBlocker` while a turn runs, a notification when a turn ends in an unfocused window, the system theme sent to Work on change.
12. **Chat signs in from the main process, with the per-install credential** (coordinator, 2026-10-07, after `security/chat-password`, which merges after `rename/mightling`: the web UI's admin password is random per install, kept in `~/.config/dreamference/chat-admin.json`, mode 0600, `{email, password}`). `sign-in.ts` reads that file in the main process only, asks `/api/me` over the Chat window's own session (`net.request` with that `session`, `credentials: "include"`, redirects not followed) and, when it answers 401 or 403, posts `/api/auth/login` (form-encoded `username`/`password`) once; the `fastapiusersauth` cookie lands in the session's jar (and is set through `session.cookies.set` if Chromium did not store it). Once per window: the attempt is counted before the login, so a password the owner changed gets the login page, not a second try; an unreachable server does not spend it. No file (a client) means the login page. The password is read for the one request and never kept, never injected into the page (no `executeJavaScript`, no preload for Chat). The Tauri-era in-page script with the published default is gone. `credentials.test.ts` checks that no credential string (the file's name, the login path, the cookie name, the old defaults) is in the preload's or Work's sources, or in the built preload and renderer bundles; the release workflow runs it again after `make`.

## 4. What is kept from the Tauri app, file by file

| Tauri (`desktop/src-tauri/src/`) | Electron (`desktop/electron/src/`) |
|---|---|
| `main.rs`: two windows, `--work`/`--cwd`/`--thread`, the sign-in script after each page load, the forwarder's port rewrite | `main.ts`, `args.ts`, `chat.ts`, `work.ts` |
| `bridge.rs` + `desktop/bridge` (`ling-desktop-bridge`) | `bridge.ts` (the pure half: `vetOutgoing`, `classify`, `BusyTracker`, `codexHome`, `busyMarker`, `servedModel`, `findLing`), `server.ts` (the process, stdout/stderr framing), `api.ts` (the channel's message types and chunking), `main.ts` (the IPC handler) |
| `forwarder.rs` (TCP pass-through, the 503 page) | `forwarder.ts` |
| `discover.rs` (`_mightling-node._tcp` browse, `decide`) | `discover.ts` (`multicast-dns`, MIT, bundled) |
| `node_locator.rs` (byte-identical copy of the Rust crate) | `node_locator.ts`, a port tested against the same cases as the crate's tests (`node_locator.test.ts`); the byte-identical contract now covers the two Rust copies only |
| `auto_sign_in.js` (an in-page script with the published default password filled in) | **dropped**: `sign-in.ts` signs Chat in from the main process with the per-install credential (§3, decision 12) |
| `tauri.conf.json` (windows, CSP, identifier, icons) | `app.json` (read by the app and by the Python tests) |
| `capabilities/work.json` | the preload is attached to Work's window only; the one IPC handler checks the sender is Work |
| — | `early-bootstrap.ts`, `bootstrap.ts` (the process split of the Codex app), `app-protocol.ts`, `egress.ts`, `window-state.ts`, `shell.ts` (menu, context menu, tray, power, notifications), `airgapped.ts`; `linux/postinst`, `linux/prerm`; `e2e/` (Playwright) |

## 5. What the user does, unchanged

`ling app`, `ling app --work`, `ling app <folder>`, `ling app --thread <id>`, the launcher entry, `ling-admin desktop install|run|build|status`. `ling app` still checks the web UI on a node and empties the HTTP cache before opening Chat (now Chromium's `Cache` under the same data directory, `~/.local/share/dev.dreamference.mightling`, which the rename migration already moves).

## 6. The egress audit's app mode

`ling-admin audit egress --app` traces `ling-app` under `strace -f` on the display `DISPLAY` names: the app starts in audit mode (`MIGHTLING_APP_AUDIT=<seconds>`: both windows hidden, Chat on the web UI, Work's app-server started, then quit), and the trace is judged by the same rules as `exec` and `tui`, with the web UI's loopback port added to the allowlist for this kind of session. Electron's `--ozone-platform=headless` crashes (SIGSEGV) on this machine (measured with 44.6.0 in the probe), so the audit needs a real display or `xvfb-run`. The app's session runs in a scratch `HOME` (Chromium's profile and the data folder) and `CODEX_HOME`, with `DREAMFERENCE_VLLM_HOST` naming the server so nothing browses.

## 7. Size

Measured on 2026-10-07 (arm64, `npm run make`): **`mightling_1.5.0_arm64.deb` is 204 MB to download and 727 MB installed** (`Installed-Size: 744704` KB), against the ~15 MB Tauri binary that linked the system WebKitGTK. Of the installed size, 412 MB is the agent (`ling` 318 MB and `codex-code-mode-host` 94 MB, bundled as the Codex app bundles its own), 316 MB Electron's Chromium with its data files, and under 1 MB Work's UI and the main process (`app.asar` 456 KB). The price of bundling the engine is the ~316 MB; the rest was already on every machine that had `ling`.

## 8. Checks that need a person at the machine

- Typing at full screen on the 5120×2160 display, the reason for the change (the probe of §9.1 first, then the built app).
- Chromium's GPU status on this driver: in a hidden window Electron 44 reports software compositing (`gpu_compositing: disabled_software`, `opengl: disabled_off`) with the NVIDIA device detected, and `--ignore-gpu-blocklist` changes nothing. Whether a visible window is accelerated, and whether software Chromium is smooth enough at that size, is what the probe answers.
- The microphone in Chat (a permission prompt now comes from Chromium).
- The launcher entry, the dock icon, the window class.
- Installing the `.deb` (the AppArmor profile, the `/usr/bin/ling-app` link, the replaced Tauri entry).

## 9. As built (2026-10-07, branch `desktop/electron`)

Everything in §3 is built, with these records:

- **Tests.** `desktop/electron`: 44 vitest tests (the forwarder's over real loopback sockets, the bridge's vetting, chunking, the locator and discovery rules, the window-state clamp, the audit/link arguments, the air-gap line, the sign-in's once-per-window rule, and the bundle check of decision 12) and `tsc --noEmit` clean; `desktop/ui`: the 11 existing tests; Playwright-Electron (`npm run e2e`, §9.2); the Python suite with the desktop, node, release-install and audit tests rewritten to read `app.json`, `forge.config.js`, `linux/postinst` and the TypeScript sources (§9.4 for the count).
- **What a person at the machine still has to check:** §8, unchanged; none of it was done here, since the app's windows are never opened by an unattended run.
- **Not built:** AppImage (§3.7), an apt repository, review/worktrees/settings in Work (DESKTOP §15), the tool glossary. `ling-rs`'s `airgapped.rs` gained `--thread <id>` (three lines); its unit tests were not re-run here, because they need the Codex export directory, which this branch's build names differently from the one on this machine.
- **The `fakeroot` the `.deb` maker insists on** was absent here; the local `.deb` was made through a stand-in that calls `dpkg-deb --root-owner-group` (what fakeroot is for), so its files are root-owned as a real one's are. `desktop build` names `fakeroot` as a prerequisite; the release runners have it.
- **`chrome-sandbox` is deleted in the `postPackage` hook,** not in the packager's `afterCopy`: that hook's path is the app folder inside `resources/`, not Electron's root, so the first package still shipped the helper.
- **Found in review, fixed in the second commit:** `desktop run` used `electron-forge start`, where plugin-vite defines a dev-server URL and `main.ts` then served Work's `app://` page from an empty path (a 404); it now packages the app and runs the packaged binary, and `main.ts` always serves the built renderer. The packaged tray looked for its icon in `app.asar.unpacked/icons`, which the auto-unpack plugin (native modules only) never creates; it reads `icons/` from the asar. The release workflow's `dist/ling-*.gz` also matched `ling-search`, `ling-fetch` and `ling-code`, which `gunzip -c` would have concatenated into one file; the names are now exact. And **Night Shift did not recognise the bundled `ling`**: its busy-marker check accepted only the installed binary as `/proc/<pid>/exe`, so a turn in Work would have had its marker deleted and the night run would have started anyway; a program of the same name running `app-server` now counts too (`NightShiftHost._is_app_server`, with a test that runs a copy under another path).
- **`rg` is not bundled.** `copy_bundled_binaries` adds it when `rg` is on PATH, which it is not on this machine, and the release workflow does not fetch it; the agent's fuzzy file search then falls back to its own. Bundling a pinned `rg` in the workflow is a later step.
- **The probe of §9.1 is still Electron 44.6.0;** the app is 42.11.11.
- **Two `vitest` traps:** a socket explicitly paused does not resume when a `data` listener is added, so a test reader that pauses to `unshift` a leftover must `resume()`; and `new URL("mightling://thread/../etc")` normalises the path, so a traversal-shaped id is not a test of anything (the id is only ever sent to the server as a thread id).

### 9.1 The probe

`~/.local/share/dreamference/electron-probe/` holds Electron 44.6.0 (the probe predates the 42.11.11 decision and was left as measured; the built app is on 42.11.11) and a one-file window on `http://localhost:3000/app` with the Codex app's window settings and no GPU flags. From a terminal under a profile that grants user namespaces (PyCharm's), `npx --prefix ~/.local/share/dreamference/electron-probe electron ~/.local/share/dreamference/electron-probe/main.js`; from a plain terminal the SUID helper message appears first (§2, Sandbox), fixed for the probe with `sudo chown root:root …/chrome-sandbox && sudo chmod 4755 …/chrome-sandbox`.

### 9.2 Playwright

`e2e/app.e2e.ts` starts the Vite build with a scripted stand-in for `ling` (answers `initialize`, lists no threads, reports `Airgapped: off`) in a scratch `HOME`, with `MIGHTLING_HIDDEN=1` so no window takes the screen: Work's page is at `app://-/index.html`, `window.mightlingWindowType` is `electron`, the page reaches the agent through `electronBridge`, and a `feedback/upload` request is refused by the bridge before it is sent. Needs a display (`DISPLAY`, or `xvfb-run` in CI) and `npm run package` first; §9.4 says whether it passed here.

### 9.3 The egress audit

`ling-admin audit egress --app` on this machine (display `:1`, the packaged app, the bundled `ling`). The first run **failed** on `[2001:4860:4860::8888]:443`: Chromium's IPv6 reachability check, a UDP `connect()` that sends nothing, which no switch turns off (EGRESS §10.7 has the evidence). The audit now records a UDP connect with nothing sent on it as a route lookup and still judges any payload; the second run **passed**: destinations `127.0.0.1:3000` and `[::1]:3000` (web UI), `127.0.0.1:8000` (model server), `127.0.0.1:8767` and `[::1]:8767` (the page's own Gmail status requests), no DNS query, no networked git, 11 route lookups to the probe address. The `exec` audit with the same flags also passed, unchanged.

### 9.4 Results on this machine

Measured on 2026-10-07, arm64, from the final `make`:

| What | Result |
|---|---|
| `.deb` | `mightling_1.5.0_arm64.deb`, 203 MB download, **727 MB installed** (`Installed-Size: 744610` KB) vs ~15 MB for the Tauri binary; 412 MB of it is `ling` + `codex-code-mode-host` |
| `app.asar` | 364 KB: the Vite build (main, preload, Work's page, `multicast-dns` bundled in), the icons, `package.json`; nothing else (an `ignore` function; a regex list had replaced the Vite plugin's default and let configs and stray `node_modules` in) |
| Simulated install (`apt-get -s install ./…deb`, installs nothing) | resolves on this Ubuntu 24.04: `1 newly installed`, no unmet dependency |
| `chrome-sandbox` in the package | none |
| Fuses (`@electron/fuses read`) | RunAsNode, NODE_OPTIONS, `--inspect`, file:// privileges **off**; cookie encryption, asar integrity, only-from-asar, Wasm trap handlers **on** |
| `desktop/electron` vitest | 44 passed (bundle check included, bundles present) |
| `desktop/ui` vitest | 11 passed; `tsc` clean |
| Playwright-Electron | 1 passed (hidden window, scripted `ling`) |
| Chat sign-in against the live web UI | signed in from the main process with the per-install credential; `/api/me` 200; the cookie HttpOnly; nothing of the credential in the page |
| `ling-admin audit egress --app` | **pass** (§9.3) |
| Python suite | all passed but one: `test_mightling_admin_and_the_web_commands_are_linked_onto_path_for_the_models_shell`, which looks for `ling-admin` in the running virtualenv; the suite ran with the main checkout's virtualenv, which predates the rename and has only `puffin-admin`. Inherited: neither that test nor `dreamference/runner/` differs from the base commit (`git diff 03b5cc5` is empty); passes once the package is reinstalled from `rename/mightling` |

Not run here: the `tui` egress audit with the new `-yy`/`write` flags (`exec` and `--app` were); `DesktopInstaller.userns_allowed()` (the `aa-exec -p unconfined` probe) and `install_apparmor_profile` against the real system (both covered by mocked tests only); `cargo test` for `ling-rs` (the three-line `--thread` branch was checked against `command`'s signature and the launcher's actual output format, not compiled), a real install of the `.deb` (it would replace the live app), and everything in §8.

## 10. The Mac app, an unsigned preview (as built 2026-10-08, branch `desktop/mac-preview`)

The same app for macOS, built on GitHub's Mac runners, published as `Mightling-<version>-arm64-preview.dmg` (Apple silicon) and `Mightling-<version>-x64-preview.dmg` (Intel). On a Mac the app is a client of a GB10 node: nothing in it runs a model. The maintainer's decision (2026-10-08): **unsigned preview first**, no Apple Developer ID and no notarization.

**How it is built.** `.github/workflows/mac-preview.yml`, a reusable workflow in the shape of `windows.yml` and `build-clients.yml`: on pushes to `desktop/mac**` (CI), by hand, and from `release.yml` behind the input `build_mac_preview` (default on since the standalone build went green; it runs only when `build_clients` is on too, whose macOS `ling` it bundles). It builds no `ling`. It takes `ling` and `codex-code-mode-host` from the `mightling-client-<target>` artifacts of `build-clients.yml`: from the calling run in a release (a reusable workflow shares its caller's run and artifacts), otherwise from the run named by `client_run_id`, otherwise from the newest run that has them. They are checked against the build's own `ling-<target>.sha256sums` before they are unpacked. Each architecture builds on its own runner (`macos-15`, `macos-15-intel`, the runners `build-clients.yml` uses), so each run of the app below is native. Forge `package` (not `make`) writes `out/Mightling-darwin-<arch>/Mightling.app`; the dmg is made with `hdiutil` (HFS+, UDZO) with an `/Applications` link beside the app, so no dmg maker joins the lockfile and the Linux job's `npm ci` is unchanged. The release job needs no change to sign it: it sums every file of every artifact into `SHA256SUMS`, so the dmgs are covered by the signed `SHA256SUMS` and by the build-provenance attestation, and the release notes gain a paragraph on opening an unsigned app whenever a `-preview.dmg` is among the files (`gh release create --notes-file`, prepended to the generated notes).

**What changed in the app for macOS** (`forge.config.js`, `src/shell.ts`, `src/preload.ts`):
- **Fuses on the `.app`.** On macOS the fuses are in `Electron Framework`, not the main executable; `@electron/fuses` finds it from the `.app` path. Flipping them breaks the bundle's signature, which Apple silicon will not run with, so `resetAdHocDarwinSignature` is on for darwin: fuses re-signs the bundle ad hoc (`codesign --sign - --force --deep`).
- **Ad-hoc signature, nothing more.** The workflow also signs the bundled `ling` and `codex-code-mode-host` ad hoc before packaging (the arm64 build had only the linker's signature, the x86-64 one none). `codesign --verify --deep --strict` must pass, on the built app and again on the dmg's copy; `spctl` (Gatekeeper) rejects the app, as it must without a Developer ID.
- **The icon**: an `.icns` made in Forge's `generateAssets` hook from `icons/icon.png` with the system's `sips` and `iconutil`, into the git-ignored `resources/` (so it stays out of the asar).
- **Info.plist**: the `mightling://` scheme (`protocols`, without which `open-url` never fires on macOS), the developer-tools category, and `NSLocalNetworkUsageDescription` with `NSBonjourServices: _mightling-node._tcp`, the text macOS 15 shows before the app (or the `ling` it starts, which macOS attributes to the app) may browse for a node or connect to one on the LAN.
- **The application menu** (`appMenu` first on darwin) and the tray icon at menu-bar height (18 points instead of 32).
- **The preload** set `document.documentElement.dataset.theme` when the document element did not exist yet (seen in the macOS run: "Unable to load preload script … Cannot read properties of null (reading 'dataset')"; the bridge itself was exposed before the throw). It now waits for `DOMContentLoaded` when there is no element yet. Whether Linux hit the same is not known; the Linux e2e test checks the bridge, not the theme.

**What the CI run checks on each architecture**: the `.app` layout, `Info.plist`, `file` on the main executable, the framework and the two bundled binaries, their minimum macOS, `codesign -dv`, `codesign --verify --deep --strict`, `spctl`, the fuses (`@electron/fuses read`), the sizes; then **the app is started** in the audit's session (`MIGHTLING_APP_AUDIT=8`: Chat and Work opened hidden, Work's app-server started, quit) with a scratch `CODEX_HOME`, the model server named (so nothing browses) and Chromium's `--use-mock-keychain` (the first attempt, with a scratch `HOME`, hung: the cookie-encryption key wants a keychain, and a home with none brings up a dialog). The run must exit 0, and the bundled `ling app-server` must be seen running as the app's child. Then `hdiutil create`, `hdiutil verify`, and the signature checked again on the mounted image.

### 10.1 Results

Run [37772687306](https://github.com/dreamference/mightling/actions/runs/37772687306) (both jobs green), bundling `ling 1.5.1` from the macOS client artifacts of release run 37761824506; the dmgs were then downloaded and unpacked here with 7-Zip (the reason for HFS+):

| What | arm64 (`macos-15`) | x64 (`macos-15-intel`) |
|---|---|---|
| dmg | `Mightling-1.5.1-arm64-preview.dmg`, 281 MB (268 MiB); `hdiutil verify` VALID | `Mightling-1.5.1-x64-preview.dmg`, 300 MB (286 MiB); VALID |
| In it | `Mightling.app` and an `Applications` link | the same |
| App installed | 644 MB: `ling` 289 MB, `codex-code-mode-host` 82 MB, `Frameworks` (Electron) 276 MB, `app.asar` 370 KB | 682 MB: `ling` 307 MB, `codex-code-mode-host` 86 MB, `Frameworks` 281 MB |
| `file` | main executable, `Electron Framework`, `ling`, `codex-code-mode-host`: Mach-O 64-bit **arm64** | all Mach-O 64-bit **x86_64** |
| Minimum macOS | app 12.0 (`LSMinimumSystemVersion`, Electron's); `ling` 11.0 | app 12.0; `ling` 10.12 |
| Signature | ad hoc on the bundle, `ling` and `codex-code-mode-host` (CodeDirectory flags `0x2`, an empty CMS blob, no Team ID); `codesign --verify --deep --strict`: valid on disk, also from the mounted dmg | the same |
| Gatekeeper (`spctl -a -t exec`) | **rejected**, as expected without a Developer ID | rejected |
| Fuses | as on Linux (§9.4): RunAsNode, NODE_OPTIONS, `--inspect`, file:// privileges off; cookie encryption, asar integrity, only-from-asar, Wasm trap handlers on | the same |
| `Info.plist` | `dev.dreamference.mightling` 1.5.1, `mightling://` scheme, Local Network text and `_mightling-node._tcp`, our icon (identical pixels to `icons/icon.png`) | the same |
| Audit session on the runner | exit 0; `Resources/ling -c features.code_mode_host=true app-server` running as the app's child; Chat's `localhost:3000` refused (no node there), as expected | the same |

The ad-hoc signatures are what Apple silicon needs to run the app at all; they say nothing about who built it, and macOS's quarantine check treats the app as unsigned. That is why `docs/desktop.md` (On a Mac) and the release notes give the three ways past it: Open Anyway in Privacy & Security (the only one in the Finder on macOS 15, which dropped the Control-click → Open bypass), Control-click → Open on macOS 14 and earlier, or `xattr -dr com.apple.quarantine /Applications/Mightling.app`.

### 10.2 Not verified (needs a person at a Mac)

- **Opening the downloaded dmg** with its quarantine mark: the Gatekeeper dialog, Open Anyway, Control-click → Open on 14, and the `xattr` route. The runner ran the app it had just built, which carries no quarantine mark.
- **The Local Network prompt** on macOS 15 and whether, once allowed, the browse (`multicast-dns` binds UDP 5353 beside `mDNSResponder` with `reuseAddr`) finds a real node; whether the `ling` the app starts is covered by the app's permission. Whether an update, whose ad-hoc signature differs, asks again. In CI the model server was named, so nothing browsed.
- **Chat and Work against a real node**: the forwarder to the node's web UI, sign-in, a Work turn on the node's model.
- **App Translocation**: run from the dmg or from Downloads without moving it, macOS starts it from a random read-only path; the docs say to drag it to Applications first.
- **The keychain**: the cookie-encryption key is stored in the login keychain (CI used Chromium's mock keychain). macOS may ask once whether Mightling may use it.
- **Gaps known from the code:** `ling app` (`ling-rs/src/app.rs`) looks for `ling-app` on PATH or a `.desktop` entry, neither of which exists on a Mac, so it does not open the app there (`open -b dev.dreamference.mightling` would); closing the last window quits the app (Linux behaviour, not the Mac convention); the data folder is `~/.local/share/dev.dreamference.mightling`, not `~/Library/Application Support`; no auto-update; the tray icon is the colour icon scaled down, not a template image; the `.icns` tops out at 512 px (no 1024 px entry, as `icons/` has none).

## 11. Chat becomes Ask (2026-10-08, `3febddb`)

The app's first window no longer shows Onyx. It is **Ask**: the Mightling UI loaded from `ling web` on this machine (`http://127.0.0.1:3100`, [MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md) §10, Phase B), signed in with a one-time link from `ling web open --print-url`, which the window trades for a session cookie as a browser does; the app reads no credential file itself (`desktop/electron/src/web.ts`). When nothing answers on the port the app starts `ling web serve` (loopback only) as its child and stops it on quit; a server the user runs as a unit (`ling web start`) is used as it is.

- **Removed with it:** the Onyx sign-in, the loopback forwarder and the node discovery that fed it, and the `multicast-dns` dependency. No permission but clipboard writes is granted to the window.
- **`ling app`** opens Ask without checking for Onyx (`--ask`; `--chat` is kept as an alias); `ling app --work`, a folder or `--thread <id>` open Work. `ling-admin desktop run` and `status` no longer need Onyx.
- **The app's egress audit** (`ling-admin audit egress --app`) allows `ling web`'s 3100 instead of Onyx's 3000.
- **On Windows**, which has no `ling web` yet, Ask opens Work.
- **Ask inside the Work window** is the next step (MIGHTLING_ASK's list of what is not built), not part of this change.

