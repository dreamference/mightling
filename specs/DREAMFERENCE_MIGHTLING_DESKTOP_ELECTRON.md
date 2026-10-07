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
| Sandbox | **No `chrome-sandbox` SUID helper is shipped.** Instead `/etc/apparmor.d/chatgpt`: `profile chatgpt "/usr/lib/chatgpt/ChatGPT" flags=(unconfined) { userns, }`. Ubuntu 24.04 sets `kernel.apparmor_restrict_unprivileged_userns=1`, so Chromium's user-namespace sandbox needs this profile — the same lesson CLAUDE.md records for `bwrap` (`puffin-bwrap`). Measured: Electron 44 started from an unconfined shell on this machine aborts with *"The SUID sandbox helper binary was found, but is not configured correctly"*; from a shell under a profile that grants `userns` it starts. |
| Package | A `.deb` with Chromium's dependencies (`libgtk-3-0`, `libnotify4`, `libnss3`, `libdrm2`, `libgbm1`, `libxcb-dri3-0`, `libasound2`, `libxkbcommon0`, `mesa-vulkan-drivers | vulkan-icd`, …) plus ones for its extras (`libtss2*`, `libusb-1.0-0`). Native modules bundled: `node-pty`, `better-sqlite3`, HID and remote-control modules. |

## 3. Decisions

1. **Electron, bundled Chromium, Electron's defaults.** `desktop/electron/` replaces `desktop/src-tauri/`; `desktop/ui/` (Work's React code and the generated protocol types) stays. No GPU flags, no `WEBKIT_*`, no `GTK_THEME`; the Chat window no longer forces light mode through the toolkit (it follows the system, as a browser does; Onyx's overrides are `html:not(.dark)`-scoped, so a dark desktop shows plain Onyx, as the browser would).
2. **Electron 44.6.0** (Chromium 152.0.7977.130, Node 24.21.0), the latest stable release on 2026-10-07, with prebuilt `linux-arm64`, `linux-x64`, `darwin-arm64`, `darwin-x64`, `win32-arm64` and `win32-x64`. The Codex app's Chromium 154 is not a published Electron; the newest stable is as close as a published build gets. Pinned exactly; bumped deliberately.
3. **`ling` inside.** `resources/ling` and `resources/codex-code-mode-host` (Codex looks for the host next to its own executable, resolving symlinks) are bundled, copied from the installed `ling` build at package time; the main process spawns the **bundled** `ling app-server`. A dev run (`ling-admin desktop run`, unpackaged) falls back to `$MIGHTLING_BIN`, `ling` on `PATH`, then `~/.local/bin/ling`, as the Tauri bridge did. `ling-search`, `ling-fetch` and `ling-code` are not bundled: the app-server reaches them through the launcher's prompt and the install's links, as the TUI does.
4. **The bridge keeps its guarantees** in TypeScript: the method allow-list, the thread fields never sent, answers only to pending server requests, the busy marker Night Shift reads; the renderer never gets Node (preload + IPC, Work's window only; Chat has no preload and no IPC).
5. **`app://` serves Work's UI** (`ui/dist`) with a Content-Security-Policy of its own origin; Chat loads `http://localhost:<port>/app` through the client forwarder as before.
6. **Egress hardening in the main process:** no crash reporter, no auto-updater, spellcheck off (Electron on Linux would download Hunspell dictionaries from a Google CDN), `MediaRouter` disabled (Chromium's Cast discovery sends mDNS/SSDP), permission requests denied except the microphone for Chat's origin, window-open requests sent to the system browser. Fuses: `RunAsNode` off, `EnableNodeOptionsEnvironmentVariable` off, `EnableNodeCliInspectArguments` off, `EnableCookieEncryption` on, `EnableEmbeddedAsarIntegrityValidation` on, `OnlyLoadAppFromAsar` on. Verified by `ling-admin audit egress --app` (§6).
7. **Packaging: Electron Forge with `maker-deb`,** as the Codex app is packaged (`out/Mightling-linux-arm64`). The `.deb` installs `/usr/lib/ling-app/ling-app` and a `/usr/bin/ling-app` link; its `postinst` writes `/etc/apparmor.d/ling-app` (`userns` for that binary, the Codex app's shape) and loads it; `postrm` removes it. **No `chrome-sandbox` SUID helper** is shipped, as in the Codex app. **No AppImage:** an AppImage cannot carry an AppArmor profile and Chromium's sandbox then needs the SUID helper, which an AppImage cannot install either.
8. **Toolchain:** Node and npm only (the repository's Node 22+). No rustup, no Tauri CLI, no GTK/WebKit headers. `DesktopInstaller.install_rust` and `cargo_path` stay because the Codex build uses them.
9. **Single instance:** a second `ling-app` hands its arguments to the running one, which shows the window asked for. (Tauri's version ran two processes; the Codex app is single-instance.)
10. **The air-gap level shown in Work** comes from `ling airgapped --thread <id>`, the launcher's own resolver, parsed from its first line — not a fourth copy of the resolver crate.

## 4. What is kept from the Tauri app, file by file

| Tauri (`desktop/src-tauri/src/`) | Electron (`desktop/electron/src/`) |
|---|---|
| `main.rs`: two windows, `--work`/`--cwd`/`--thread`, the sign-in script after each page load, the forwarder's port rewrite | `main.ts`, `args.ts`, `chat.ts`, `work.ts` |
| `bridge.rs` + `desktop/bridge` (`ling-desktop-bridge`) | `bridge.ts` (the pure half: `vetOutgoing`, `classify`, `BusyTracker`, `codexHome`, `busyMarker`, `servedModel`, `findLing`) and `server.ts` (the process, stdout/stderr framing, IPC) |
| `forwarder.rs` (TCP pass-through, the 503 page) | `forwarder.ts` |
| `discover.rs` (`_mightling-node._tcp` browse, `decide`) | `discover.ts` (`multicast-dns`, MIT, bundled) |
| `node_locator.rs` (byte-identical copy of the Rust crate) | `node_locator.ts`, a port tested against the same cases as the crate's tests (`node_locator.test.ts`); the byte-identical contract now covers the two Rust copies only |
| `auto_sign_in.js` | the same file, unchanged, run by `webContents.executeJavaScript` after each Chat load |
| `tauri.conf.json` (windows, CSP, identifier, icons) | `app.json` (read by the app and by the Python tests) |
| `capabilities/work.json` | the preload is attached to Work's window only; every IPC handler checks the sender is Work |

## 5. What the user does, unchanged

`ling app`, `ling app --work`, `ling app <folder>`, `ling app --thread <id>`, the launcher entry, `ling-admin desktop install|run|build|status`. `ling app` still checks the web UI on a node and empties the HTTP cache before opening Chat (now Chromium's `Cache` under the same data directory, `~/.local/share/dev.dreamference.mightling`, which the rename migration already moves).

## 6. The egress audit's app mode

`ling-admin audit egress --app` traces `ling-app` under `strace -f` on the display `DISPLAY` names: the app starts in audit mode (`MIGHTLING_APP_AUDIT=<seconds>`: both windows hidden, Chat on the web UI, Work's app-server started, then quit), and the trace is judged by the same rules as `exec` and `tui`, with the web UI's loopback port added to the allowlist for this kind of session. Electron's `--ozone-platform=headless` crashes (SIGSEGV) on this machine with 44.6.0, so the audit needs a real display or `xvfb-run`.

## 7. Size

Installed: Electron's Chromium (~220 MB for `electron` itself plus its data files) + `ling` (249 MB) + `codex-code-mode-host` (67 MB) + Work's UI (<1 MB) + `multicast-dns`. Against today's ~15 MB Tauri binary (which linked the system WebKitGTK), the price of bundling the engine. §9 records the measured `.deb`.

## 8. Checks that need a person at the machine

- Typing at full screen on the 5120×2160 display, the reason for the change (the probe of §9.1 first, then the built app).
- Chromium's GPU status on this driver: in a hidden window Electron 44 reports software compositing (`gpu_compositing: disabled_software`, `opengl: disabled_off`) with the NVIDIA device detected, and `--ignore-gpu-blocklist` changes nothing. Whether a visible window is accelerated, and whether software Chromium is smooth enough at that size, is what the probe answers.
- The microphone in Chat (a permission prompt now comes from Chromium).
- The launcher entry, the dock icon, the window class.
- Installing the `.deb` (the AppArmor profile, the `/usr/bin/ling-app` link, the replaced Tauri entry).

## 9. As built (2026-10-07, branch `desktop/electron`)

Filled in at the end of the build: see the report in the commit messages and §9.1–§9.4 below.

### 9.1 The probe

`~/.local/share/dreamference/electron-probe/` holds Electron 44.6.0 and a one-file window on `http://localhost:3000/app` with the Codex app's window settings and no GPU flags. From a terminal under a profile that grants user namespaces (PyCharm's), `npx --prefix ~/.local/share/dreamference/electron-probe electron ~/.local/share/dreamference/electron-probe/main.js`; from a plain terminal the SUID helper message appears first (§2, Sandbox), fixed for the probe with `sudo chown root:root …/chrome-sandbox && sudo chmod 4755 …/chrome-sandbox`.
