# Mightling — Changes Made to Codex

**Status:** implemented. The patch series was cut down from about 406 KB to about 9 KB; the later patches `0005`–`0025` bring it to **23 patches and 42,741 bytes** (checked 2026-10-09) touching 43 upstream files, under the 43,000-byte limit `test_the_patches_stay_small` enforces. The limit was 20,000 until 2026-09-30 and has been raised explicitly, each time only by what a hook needed: 25,000 for `0017` (`/cavemode`), 27,500 for `0018` (`/night`), 31,500 for `0019` (`/airgapped`), 32,500 for `0020` (MCP tools as plain functions), 33,750 for `0019`'s Full Access hooks, 34,750 for `0021` (observation masking), 36,250 for `0022` (`/apps`), 37,500 for `0023` (the app server's air-gap rule), and 43,000 on 2026-10-08 when `0024` (`/airgapped on` on Windows) and `0025` (`/node`) were merged together. Everything larger than a one-line hook or a renamed string lives in `ling-rs/`. The agent's web commands, `ling-search` and `ling-fetch`, are Rust binaries from the standalone crate `ling-web-rs/` since 2026-09-30 (§4.1).
**Supersedes:** `DREAMFERENCE_CODEX.md`, which describes the older setup where an upstream `codex` on PATH was launched from Python.
**Upstream:** [`openai/codex`](https://github.com/openai/codex), the open-source coding agent this fork began from, release `rust-v0.158.0`.

`ling` is Mightling's terminal coding agent (Mightling by Dreamference). It is Codex CLI with the name changed, the system prompt's identity changed, and a small launcher compiled in that points each session at the model this machine serves. Everything else, including the TUI, `exec`, `resume`, `fork`, sandboxing, Code Mode and every flag, is Codex's own code, unchanged. Someone who knows `codex` can use `ling` the same way.

This document lists every change and where it lives.

---

## 1. Principle: the Codex source is never edited

| Piece | Location | Role |
| --- | --- | --- |
| Fork | `github.com/dgxcoder/codex` | A fork of `openai/codex` that carries upstream's history and tags. |
| Submodule | `codex/` (shallow) | Pinned to the `rust-v0.158.0` tag commit (`064c6b8`). **Never modified.** |
| Patches | `codex-patches/00NN-*.patch` | Small unified diffs with one line of context, applied in file-name order: renamed strings, the launcher hook, and one-line switches that hide or reroute commands (§3). Numbers `0003`–`0004` are unused; they belonged to the pre-minimisation series. |
| Launcher | `ling-rs/` | Mightling's own Rust crate. It is kept as source, not as a patch. |
| Web commands | `ling-web-rs/` | `ling-search` and `ling-fetch`, the commands the prompt gives the model for the web. A standalone crate, built beside Codex rather than inside it (§4.1). |
| Builder | `dreamference/runner/codex_branded_builder.py` | Turns the inputs above into the installed binaries. |

The patches are applied to an **exported copy** at build time, never to the submodule. The fork therefore stays byte-identical to upstream. Moving to a newer Codex means bumping the submodule and refreshing whichever hunks no longer apply (§6).

Running `cargo` inside `codex/` is forbidden: even `cargo tree` rewrites the submodule's `Cargo.lock`.

---

## 2. Build pipeline (`ling-admin codex build [--force]`)

0. **Lock.** `build()` takes an exclusive `flock` on `~/.cache/dreamference/puffin-codex/.build.lock`. Every build begins by wiping the shared source tree, so two concurrent builds used to destroy each other mid-compile ("Could not locate working directory"). A second build now waits, then re-checks the build key and returns at once if the first build already produced it.
1. **Export.** `git archive <pinned commit> codex-rs` into `~/.cache/dreamference/puffin-codex/src`. Only the Rust workspace is exported; the npm wrapper, Bazel files and SDKs play no part.
2. **Add the launcher.** Copy `ling-rs/` to `codex-rs/ling`. This has to happen before the patches, because `0002` makes the CLI depend on it. A path dependency inside the workspace root becomes a workspace member automatically, so the workspace manifest is not patched.
   - The copy uses `shutil.copy`, not `copy2`. That gives the files fresh modification times, which matters because Cargo decides freshness by mtime. A copied file with its original, older mtime was once taken as already compiled, and a stale launcher got linked in.
3. **Patch.** For each file in `codex-patches/`, run `git apply --check` and then `git apply`. A patch that does not fit stops the build before anything is half-applied, and the error names the release tag the patches were written for.
4. **Fetch V8.** Code Mode embeds V8 built with pointer compression and the sandbox enabled. denoland does not publish that build for aarch64 Linux, so the `v8` crate's own download returns 404. `fetch_rusty_v8()` does what upstream's `.github/actions/setup-rusty-v8` does:
   - it downloads the upstream vendor's `rusty-v8-v<version>` release assets;
   - it verifies them against `third_party/v8/rusty_v8_*_release_manifests.sha256` from the submodule;
   - it passes the archive and bindings to Cargo as `RUSTY_V8_ARCHIVE` and `RUSTY_V8_SRC_BINDING_PATH`.

   Verified files are cached, so later builds don't download them again.
5. **Compile.** Run `cargo build --release -p codex-cli --bin codex -p codex-code-mode-host --bin codex-code-mode-host` with these settings. Cargo still calls the binary `codex`; step 6 names the file.
   - `CARGO_TARGET_DIR=~/.cache/dreamference/puffin-codex/target`. This directory persists, so a patch edit recompiles only the crates it touches.
   - `CARGO_PROFILE_RELEASE_DEBUG=none` and `CARGO_PROFILE_RELEASE_STRIP=debuginfo`. Upstream keeps line tables and strips only when it packages; built as-is, the binary is 1.4 GB instead of about 315 MB. These are set through the environment, not a patch, so they touch no Codex source.
   - No `--locked`. Upstream's `Cargo.lock` records the workspace crates at `0.0.0`, and its release job bumps them just before building, so Cargo rewrites those 158 entries. Every third-party version stays as pinned.
   - In release builds, the `ling` job of `.github/workflows/release.yml` sets `MIGHTLING_VERSION=<release version>`. `update.rs` reads it with `option_env!`; a local build has none and reports itself as `source`.
6. **Install.** Copy the binaries to `~/.local/share/dreamference/mightling/bin/`:
   - `ling`, which is Cargo's `codex` binary under its new name;
   - `codex-code-mode-host`, which keeps its upstream name because Codex looks for that exact name next to its own executable.

   Each is written to a temporary name and renamed into place. `~/.local/bin/ling` is then symlinked to the installed binary. The helper is still found through the link, because Codex resolves its own executable path. The link only replaces a missing file or an existing symlink.
7. **Stamp.** A `build-key` file records a hash of every input: the source commit, the patch bytes, the launcher source and the profile overrides. An unchanged tree is not rebuilt; any change to an input triggers a rebuild.
8. **Web commands** (`build_web_tools()`, run first and independently of steps 0–7). `cargo build --release --locked --bin ling-search --bin ling-fetch` in `ling-web-rs/` itself, against its own committed `Cargo.lock`, with `CARGO_TARGET_DIR=~/.cache/dreamference/puffin-web/target` (the cache folders keep their pre-rename names so the compiled dependencies survive). The two binaries are installed beside `ling` the same way (temporary name, then rename), linked as `~/.local/bin/ling-search` and `~/.local/bin/ling-fetch`, and stamped in their own `web-build-key` (a hash of the crate's source). A change to the crate never relinks Codex, and a Codex rebuild never recompiles the crate. `CodexInstaller.is_installed()` requires both builds to be current. The helper behind it, `build_crate()`, takes any standalone crate of Mightling commands, so a later one (`ling-code-rs/`, `DREAMFERENCE_MIGHTLING_CODE_INDEX.md` §4.2) can use it as it is.
9. **The other standalone commands**, built the same way and also before Codex: `ling-code` from `ling-code-rs/` (`build_code_index()`, target `~/.cache/dreamference/puffin-code-build`) and, on Linux, `ling-docs` from `ling-docs-rs/` (`build_docs_index()`, target `~/.cache/dreamference/ling-docs-build`), each `--locked` against its own lockfile and stamped separately. The Mightling UI that `ling web` embeds (`desktop/ui/`) is built with `npm ci` and Vite (`build_web_ui()`) and handed to the Codex build as `LING_WEB_UI_DIST`.
10. **The egress audit.** After installing a new `ling`, `codex build` runs `ling-admin audit egress` (and the `--tui` one) unless `--no-audit`; without a model server it says so and does not wait ([MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md)).

**Host requirements:** Rust through rustup (the toolchain version is pinned by `codex-rs/rust-toolchain.toml` and fetched on first use), `git`, `tar`, `perl` and a C compiler. No `libssl-dev` (the launcher crate enables `openssl-sys/vendored`, §4) and no `libcap-dev`: the bundled `bwrap` binary is not built, and Codex falls back to the system's `/usr/bin/bwrap`.

---

## 3. The patches

Only changes that cannot be made from outside are patches, and each is a one-line edit. Anything larger goes in `ling-rs/`, which Cargo compiles into the same binary. A test (`test_the_patches_stay_small`) keeps the series under a cap, 43,000 bytes since 2026-10-08, raised explicitly and only by what a hook needs.

### `0001-brand-mightling-name.patch` (visible name)

| File | Change |
| --- | --- |
| `tui/src/history_cell/session.rs` | Session header `>_ Codex (vX)` → `>_ Mightling (vX)`, in the compact, boxed and raw renderings |
| `tui/src/status/card.rs` | `/status` card title → `Mightling` |
| `exec/src/event_processor_with_human_output.rs` | `exec` banner `Codex vX` → `Mightling vX` |
| `cli/src/main.rs` | clap `name`, `bin_name`, `override_usage` and the shell-completion name → `ling`, so `--version` prints `ling 0.158.0` |
| `cli/src/plugin_cmd.rs`, `marketplace_cmd.rs`, `mcp_cmd.rs` | Subcommand usage lines `codex plugin …`, `codex mcp add …` → `ling …` |
| `exec/src/cli.rs` | `exec`'s hard-coded usage `codex exec …` → `ling exec …` (clap exposes no getter for `override_usage`, so the parse-time rebrand cannot reach it) |

### `0002-ling-launcher-hook.patch` (the hook)

| File | Change |
| --- | --- |
| `cli/Cargo.toml` | `ling-launcher = { path = "../ling" }` |
| `cli/src/main.rs` | `MultitoolCli::parse()` → `ling_launcher::help::parse(ling_launcher::args().await?)`, which rebrands the clap help tree at parse time (`ling-rs/src/help.rs`) |

The call sits in `cli_main`. That is after `arg0` dispatch, so the `codex-linux-sandbox`, `apply_patch` and `codex-execve-wrapper` aliases never reach it, and before Codex parses its command line.

### `0005`–`0025` (switches: hide, reroute, replace, closing network channels, cave mode, `/night`, `/airgapped`, MCP tools, masking, `/apps`, `/node`)

Hiding a subcommand only removes it from `--help`, so each hidden CLI subcommand that must not run is *also* refused by the launcher (§4, step 1). A hidden slash command (`is_visible() == false`) is gone from the popup, and typing it is not recognised either, because the command lookup only matches visible commands. In every case the code behind the command stays compiled.

| Patch | File(s) | Change |
| --- | --- | --- |
| `0005-hide-vendor-login` | `cli/src/main.rs`, `tui/src/slash_command.rs` | `login` / `logout` get `hide = true`; `/logout` is not visible. There is no a cloud account to sign in to. |
| `0006-hide-cloud` | `cli/src/main.rs` | `cloud` (alias `cloud-tasks`) gets `hide = true`. It is kept for a future private cloud. |
| `0007-hide-remote-control` | `cli/src/main.rs` | `remote-control` gets `hide = true`. It relays through the upstream vendor's servers today and is kept for a private relay later. It is hidden only, **not** refused: `ling remote-control` still runs, and fails without the vendor's sign-in. |
| `0008-mightling-update` | `cli/src/main.rs` | `update` calls `ling_launcher::update::run()` instead of Codex's installer logic, and its help reads "Update Mightling to the latest release." |
| `0009-remove-feedback` | `tui/src/slash_command.rs`, `tui/src/chatwidget/turn_runtime.rs` | `/feedback`, which uploads session logs to the upstream vendor, is not visible. The interrupted-turn hint no longer says "Hit `/feedback`…". |
| `0010-hide-voice` | `tui/src/slash_command.rs` | `/voice`, the upstream vendor's realtime voice API, is not visible. It is kept for a future local voice. |
| `0011-usage-token-stats` | `tui/Cargo.toml`, `tui/src/bottom_pane/slash_commands.rs`, `tui/src/chatwidget/slash_dispatch.rs`, `tui/src/slash_command.rs` | `/usage` is always listed, and shows this session's token statistics from `ling_launcher::usage::report()`, fed by the counters `/status` already uses, instead of the hosted service's plan limits. It drops upstream's `/usage daily\|weekly\|cumulative` form. The TUI crate gains a path dependency on the launcher. |
| `0017-cave-mode` | `tui/src/slash_command.rs`, `tui/src/chatwidget/slash_dispatch.rs`, `app-server/Cargo.toml`, `app-server/src/extensions.rs`, `cli/src/main.rs` | Adds `/cavemode` (after `/model`, inline arguments, available during a task), whose two dispatch arms print `ling_launcher::cave::command()`; and registers `ling_launcher::cave::install` beside `codex_git_attribution::install` in the app server's extension registry (the TUI, `exec` and app-server clients) and in the one `debug prompt-input` builds. The app server gains a path dependency on the launcher. See [MIGHTLING_CAVE_MODE](./DREAMFERENCE_MIGHTLING_CAVE_MODE.md). |
| `0012-hide-auto-review` | `tui/src/slash_command.rs` | `/approve` (`SlashCommand::AutoReview`) is not visible. It defaults to the upstream vendor's `codex-auto-review` model, which the local catalog lacks. |
| `0013-disable-usage-analytics` | `analytics/src/client.rs` | The analytics client is constructed disabled, whatever `[analytics]` or the login say. It posted usage events to `chatgpt.com/backend-api/codex/analytics-events/events` whenever a vendor login was present. |
| `0014-mightling-home` | `cli/src/main.rs` | First statement of `main()`: `ling_launcher::home::use_mightling_home()` sets `CODEX_HOME` to `~/.mightling` (unless already set), before `arg0` reads `.env` from the home folder. First run copies an allow-list from `~/.codex`, never `auth.json`. |
| `0015-no-vendor-network` | `otel/src/config.rs`, `core-plugins/src/manager.rs`, `core-plugins/src/remote_legacy.rs`, `tui/src/tooltips.rs` | Found by tracing sessions with no login: the Statsig OTEL metrics exporter (`ab.chatgpt.com`, default-on in release builds) resolves to none; the curated-plugin startup sync (`git ls-remote https://github.com/openai/plugins.git`) never starts; the featured-plugins request returns an empty list; the TUI's announcement fetch from `raw.githubusercontent.com` records "none" without fetching. With these and the launcher's `chatgpt_base_url` pointed at `127.0.0.1:9`, traced `exec` and TUI sessions contact only loopback services. |
| `0016-doctor-offline` | `cli/src/doctor.rs`, `cli/src/doctor/updates.rs` | `ling doctor` checked the vendor's sign-in, looked for updates on `api.github.com`, Homebrew and the upstream vendor's desktop feed (`persistent.oaistatic.com`, a hard-coded `chatgpt.com/backend-api` URL the `chatgpt_base_url` redirect does not cover), and probed the vendor's websocket and the provider (`api.openai.com` when no config loads). Those four checks are **switched off, not removed**: each returns "disabled in Mightling (no network)" while `MIGHTLING_DOCTOR_OFFLINE` is true, and setting it to `false` restores them. The constant is `!cfg!(test)`, so upstream's own unit tests still run the real checks. The local checks (config, MCP, sandbox, state, terminal, git) are untouched. |
| `0018-night-slash-command` | `tui/src/slash_command.rs`, `tui/src/chatwidget/slash_dispatch.rs` | Adds `/night`, Night Shift's queue command; the logic is `ling_launcher::night` ([MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md)). |
| `0019-airgapped` | `linux-sandbox/Cargo.toml`, `linux-sandbox/src/linux_run_main.rs`, `tui/src/slash_command.rs`, `tui/src/chatwidget/slash_dispatch.rs`, `tui/src/chatwidget/permissions_menu.rs`, `tui/src/chatwidget/permission_popups.rs`, `app-server/src/extensions.rs`, `cli/src/main.rs` | Adds `/airgapped`; the sandbox helper takes the network from every command at level `on` (`ling-airgapped`); since 2026-10-03 the Full Access row is disabled in both permission pickers at `on` and `/airgapped on` is refused in a Full Access session ([MIGHTLING_AIRGAPPED §14](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md)). |
| `0020-flat-mcp-tools` | `core/Cargo.toml`, `core/src/client.rs`, `core/src/tools/router.rs` | SGLang and vLLM drop the Responses API's `namespace` tool type, so no MCP tool ever reached the local model; two hooks send MCP tools as plain functions and map a call back (`ling-rs/tools`, [MIGHTLING_CODE_INDEX §15](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md)). |
| `0021-observation-masking` | `core/Cargo.toml`, `core/src/context_manager/history.rs` | Old tool outputs are replaced in the request by a placeholder naming a saved copy; the rule is the leaf crate `ling-rs/masking`, off by default ([MIGHTLING_CONTEXT_BUDGET](./DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md)). 1,001 bytes. |
| `0022-ling-apps` | `app-server/src/request_processors/apps_processor.rs`, `tui/src/chatwidget/connectors.rs` | `/apps` without a vendor sign-in: the gate and the list, which name Mightling's own Gmail, Google Drive and Google Calendar, served by `ling apps serve` over MCP (`ling-rs/apps`, [MIGHTLING_APPS](./DREAMFERENCE_MIGHTLING_APPS.md)). 1,367 bytes. |
| `0023-airgapped-app-server` | `core/Cargo.toml`, `core/src/config/mod.rs` | The app server refuses Full Access at `/airgapped on` for every client, as the TUI does (the desktop app's Work window included). 1,220 bytes. |
| `0024-airgapped-windows` | `windows-sandbox-rs/Cargo.toml`, `src/resolved_permissions.rs`, `src/elevated_impl.rs`, `src/unified_exec/backends/elevated.rs` | `/airgapped on` on Windows: the elevated sandbox clears a command's network when its session is at `on`, through its offline account ([MIGHTLING_WINDOWS_ARM](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md)). Compiled only for Windows; nothing has run on Windows here. 2,048 bytes. |
| `0025-node-slash-command` | `tui/src/app/event_dispatch.rs`, `tui/src/app_event.rs`, `tui/src/chatwidget/slash_dispatch.rs`, `tui/src/slash_command.rs` | `/node provision|add|list|status|remove`: suspends the TUI and runs `ling-admin node …` on the terminal (`ling-rs/src/node_command.rs`, [MIGHTLING_NODE §18.9](./DREAMFERENCE_MIGHTLING_NODE.md)). 3,382 bytes. |

### What used to be patches

| Former patch | Now |
| --- | --- |
| Vendored OpenSSL in `core/Cargo.toml` | `ling-rs/Cargo.toml` depends on `openssl-sys` with `vendored` for glibc Linux. Cargo unifies features across the build, so every crate that links OpenSSL gets the vendored build. |
| `[[bin]] name` and `default-run` in `cli/Cargo.toml` | Not renamed: Cargo builds `codex` and the builder installs it as `ling`. |
| Workspace `members` and `[workspace.dependencies]` | Not needed: the launcher is a path dependency inside the workspace root. |
| The identity in `models-manager/models.json` (~390 KB, because each template is one JSON line) | `rebrand()` in the launcher renames the prompt when it writes the model catalog. |

---

## 4. The launcher (`ling-rs/`, crate `ling-launcher`)

This is the Rust port of what the Python `ling` entry point used to do before exec'ing Codex. That entry point is gone. `CodexRunner` now only builds `ling` and runs it, so there is no second copy of the setup to drift.

`args()`, the function the hook calls, runs `prepare_args` on the process's argv:

1. **Refuses switched-off commands** (`REMOVED_COMMANDS`): `login`, `logout`, `cloud` and `cloud-tasks` exit with `` `ling <name>` is not available: <reason>. `` before Codex parses anything. The subcommand is the first positional argument, found by `first_positional()` from Codex's own clap definitions of which options take a value, so `ling -c key=value cloud` is refused too, and the scan cannot drift from Codex. The same index decides whether the command needs a model at all. Until 2026-09-29 only the first argument was checked, which let that form through.
2. **Handles `app` itself** (`app.rs`): `ling app` opens Mightling's desktop app, `ling-app` (Electron, [MIGHTLING_DESKTOP_ELECTRON](./DREAMFERENCE_MIGHTLING_DESKTOP_ELECTRON.md)), instead of the upstream vendor's closed-source app.
   - It finds the app on PATH, or through the `Exec=` line of the `ling-app` desktop entry.
   - `ling app` alone opens **Ask**, the Mightling UI on `ling web`, which the app starts itself when nothing answers on port 3100; `ling app --work`, `ling app <folder>` and `ling app --thread <id>` open **Work** on `ling app-server`. Neither needs Onyx (until 2026-10-08 the first window was the Onyx web UI on `localhost:3000`), and both wait for the model server on their own start-up screen.
   - It empties the app's HTTP cache before opening Ask, keeping Chromium's `Cookies`, so a window never shows a page from before an upgrade.
   - It starts the app detached, so closing the terminal does not close it.
3. **Skips setup for commands that never reach a model.** These are `--help`/`-h`, `--version`/`-V`, and the subcommands `help completion apply a features doctor mcp plugin archive unarchive delete sandbox update`. They answer at once instead of waiting for a server. (`update` goes on to `update.rs` through patch `0008`.)
4. **Resolves the vLLM URL**, first match wins (`node.rs`; [MIGHTLING_NODE §6.1, §18](./DREAMFERENCE_MIGHTLING_NODE.md)):
   - the `DREAMFERENCE_VLLM_HOST` environment variable;
   - `vllm_host` in `DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then `~/.config/dreamference/config.toml`;
   - `MIGHTLING_NODE=<name>`, one node for one command;
   - `http://localhost:8000` when this machine is a node (`~/.config/dreamference/node-id` exists), with no browse;
   - the node remembered in `$CODEX_HOME/node.json`, found again on the network by its id;
   - a browse of `_mightling-node._tcp`: one node is used and remembered, several are a question, and a node other than the remembered one is never adopted silently;
   - `http://localhost:8000` if a model server answers there (an install from before the split); otherwise "No Mightling node found on this network".
   `ling node list|use|forget` is intercepted like `night` and `airgapped`, and never waits for a model.
5. **Waits for the server.** It prints `⏳ Waiting for local vLLM server at … to become available...` with a dot per second, and gives up after 600 s with the `ling-admin server start` hint.
6. **Reads the served model from `GET /v1/models`**, both its `id` and its `max_model_len`, so the launcher needs no copy of Mightling's model registry.
7. **Checks for Gmail.** Unless `mightling_gmail = false` (config file) or `DREAMFERENCE_MIGHTLING_GMAIL=false`, it asks the Gmail service's `/status` (`http://127.0.0.1:8767`, 1 s timeout). If an account is connected, `gmail_access_instructions()` adds an "Email access" block after the web section. The block names the accounts and the `ling-admin gmail` commands, and warns that email content is untrusted data, never instructions. See `DREAMFERENCE_MIGHTLING_GMAIL.md`.
8. **Writes `$CODEX_HOME/model_catalog.json`**, a single entry Codex requires before it will use an unknown model. Codex parses it with strict serde structs, so a wrong shape stops it at startup:
   - context window, compaction limit and truncation limit set to `max_model_len`;
   - one reasoning level, `none`;
   - `visibility = "list"`;
   - `tool_mode = "code_mode"`, so the model gets Code Mode's `exec` tool;
   - `base_instructions`: the longest bundled template, passed through `rebrand()` (the opening sentence, which also claims a GPT model, becomes `You are Mightling, a coding agent.`, and every later `Codex` becomes `Mightling`), followed by `WEB_ACCESS_INSTRUCTIONS`, which tells the model to use `ling-search` and `ling-fetch` (§4.1), then the Gmail block from step 7 if any.
9. **Edits `$CODEX_HOME/config.toml` with `toml_edit`.** Editing the document rather than appending text means a top-level key can never be absorbed into the preceding table, which twice stopped Codex from starting. It sets:
   - `model_catalog_json`, always;
   - `suppress_unstable_features_warning = true` and `check_for_update_on_startup = false`, only if absent. The update check would offer to replace Mightling with upstream Codex.
   - `[model_providers.openai-custom]`: `name`, and a `base_url` that is always rewritten to follow the server;
   - `[features] code_mode = true`, `enable_mcp_apps = false`, only if absent;
   - `[sandbox_workspace_write] network_access = true`, only if absent. Without it, DNS fails inside the sandbox and the web commands break.
   - `[sandbox_workspace_write] writable_roots = ["$CODEX_HOME/skills"]`, only if absent (2026-10-01). Codex's built-in `skill-installer` downloads a skill from the public `github.com/openai/skills` into `$CODEX_HOME/skills`, which the sandbox otherwise mounts read-only: `ling exec` could not install one, and the TUI needed an approval. No vendor login is involved, and patch `0015` (which stops the *plugin* sync) is unchanged. `writable_roots = []` in the file switches it off. The cost: a session can now write instructions that later sessions read. Checked live: `ling exec -s workspace-write` installed the curated `pdf` skill into `~/.mightling/skills/pdf`, and a skill placed there by hand was found and followed.
10. **Prepends `--oss --local-provider openai-custom -c model_provider="openai-custom" --model <id>`**, skipping any of these the user already gave (the `-c` follows a user's `--local-provider`). They are root options, so `exec`, `resume` and `fork` inherit them. The `-c` is what keeps a fresh `CODEX_HOME` off the upstream sign-in screen: the TUI's startup account check reads the configured `model_provider`, not `--local-provider`, and it is passed rather than written to `config.toml`. Until 2026-09-29 a first run opened on that screen (`test_a_fresh_home_opens_on_the_composer`).

The hook then parses the result with `help::parse` (`help.rs`), not `MultitoolCli::parse_from`. It walks the whole clap command tree once and replaces the product name in every about, help and usage string: "Codex" and "Codex CLI" become "Mightling", `codex` as the typed command becomes `ling`. Paths and identifiers that merely contain the name (`~/.codex`, `$CODEX_HOME`, `codex-code-mode-host`, `openai/codex`) are left alone. This is how dozens of help strings across several crates are rebranded without a patch per string.

**Other launcher modules:**

| Module | Reached from | What it does |
| --- | --- | --- |
| `update.rs` | `ling update` (patch `0008`) | Asks the GitHub API for the latest *published* release of `dreamference/mightling` (`MIGHTLING_RELEASE_REPO` names a fork); drafts and pre-releases are not offered. The repository is public, so no token is needed; `GH_TOKEN` or `GITHUB_TOKEN` is sent when set (a higher rate limit), a logged-in `gh`'s token only for a fork, and a token only ever goes to `api.github.com`, never to a download URL. It compares the release with `MIGHTLING_VERSION`; a source build has none and always installs. It downloads the gzipped `ling` and `codex-code-mode-host` and the per-target `sha256sums` file, and the gzipped `ling-search`, `ling-fetch`, `ling-code` and, on Linux, `ling-docs` and `ling-signal` when the release carries them (older releases do not; the installed ones are then kept). From 1.5.0 on it first checks the release-wide `SHA256SUMS` against its Ed25519 signature by Mightling's release key (`release_signature.rs`, [RELEASE_SIGNING](./DREAMFERENCE_RELEASE_SIGNING.md)), which lists the per-target file; then it verifies every archive before replacing any, swaps them in next to the running executable, and links the commands into `~/.local/bin` (`ling-signal` is installed beside `ling` but never linked). The release carries the `ling-code` router only; the indexers it runs still come from `ling-admin code setup`. |
| `usage.rs` | `/usage` (patch `0011`) | Formats the session's input tokens (cached / new), output tokens (plus reasoning tokens), total, and the last request's share of the context window, or "No tokens used yet in this session." |
| `cave.rs` | `/cavemode` and every model request (patch `0017`) | Cave mode: the four levels and their texts (`ling-rs/cave/`, byte-for-byte the benchmark's), the level's resolution (this session's file `$CODEX_HOME/cave_mode/<thread-id>`, `DREAMFERENCE_MIGHTLING_CAVE_MODE`, `mightling_cave_mode` in the TOML file, then `ultra`), the `/cavemode` command, and the World State section that sends a level's full text once, a one-line reminder at each later turn, and the full text again after a switch or a compaction. |
| `app.rs` | `ling app` (step 2) | Opens `ling-app`. |
| `compaction.rs` | every launch that reaches the model | Reads the server's KV pool from `/metrics` and passes `-c model_auto_compact_token_limit=<60% of it>` unless the command line or `config.toml` sets that key; registers the ledger hook in `config.toml` (`[[hooks.SessionStart]]`, matcher `compact`) with the `trusted_hash` Codex requires before it runs a hook, or removes it, by `mightling_compaction_ledger` ([MIGHTLING_COMPACTION §11](./DREAMFERENCE_MIGHTLING_COMPACTION.md)). |
| `ledger.rs` | `ling ledger` (the hook), `ling ledger show <rollout>` | Rebuilds from the session's rollout and `git status`, by rule, what a compaction drops: files changed, other files read, commands that failed since the previous compaction, the last test result. Standard library and `serde_json` only. |
| `help.rs` | every parse | Rebrands the help tree (above). |
| `home.rs` | first statement of `main()` (patch `0014`) | `CODEX_HOME` is `~/.mightling` (below). |
| `node.rs`, `node_command.rs` | every launch that needs a model; `ling node …`; `/node` (patch `0025`) | Where the model server is (step 4), and `/node` running `ling-admin node …` on the real terminal ([MIGHTLING_NODE §18.9](./DREAMFERENCE_MIGHTLING_NODE.md)). |
| `night.rs` | `/night` (patch `0018`), `ling night …` | Night Shift's queue ([MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md)). |
| `airgapped.rs` | `/airgapped` (patch `0019`), `ling airgapped` | The command and the World State fragments; the resolution is the leaf crate `ling-airgapped` ([MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md)). |
| `prompt.rs` | every new session; `ling prompt …` | Named system prompts: `default`, `high-swe`, `ask`, `refine` or a custom file ([MIGHTLING_PROMPT](./DREAMFERENCE_MIGHTLING_PROMPT.md)). |
| `refine.rs` | a new task when refine mode is on | Study first (a session that changes nothing and writes a refined description), then do, in a fresh session ([MIGHTLING_REFINE](./DREAMFERENCE_MIGHTLING_REFINE.md)); off by default. |
| `mask.rs` | every launch that reaches the model | Whether old tool outputs are masked, and at what sizes; the rule is `ling-rs/masking`, called through patch `0021` ([MIGHTLING_CONTEXT_BUDGET §4.1](./DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md)). |
| `skills.rs` | every launch; `ling skill …` | The launcher's side of `ling-skills`: the `from-<agent>` links, the preflight and the catalogue budget ([MIGHTLING_SKILLS](./DREAMFERENCE_MIGHTLING_SKILLS.md)). |
| `code_index.rs`, `docs_index.rs` | every launch; `ling docs …` | Start `ling-code session` and `ling-docs session` outside the sandbox and offer their tools over MCP ([MIGHTLING_CODE_INDEX](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md), [MIGHTLING_LOCAL_INDEX](./DREAMFERENCE_MIGHTLING_LOCAL_INDEX.md)). |
| `apps.rs` | `/apps` (patch `0022`), `ling apps serve` | Gmail, Google Drive and Google Calendar as read-only MCP tools; the logic is the `ling-apps` crate ([MIGHTLING_APPS](./DREAMFERENCE_MIGHTLING_APPS.md)). |
| `web.rs`, `chat.rs`, `signal.rs` | `ling web …`, `ling chat …`, `ling signal …` | Tell the `ling-web-server`, `ling-chat` and `ling-signal` crates where things are; the servers and bridges are those crates ([MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md), [MIGHTLING_CHAT](./DREAMFERENCE_MIGHTLING_CHAT.md), [MIGHTLING_SIGNAL](./DREAMFERENCE_MIGHTLING_SIGNAL.md)). |
| `release_signature.rs` | `ling update` | Refuses a release whose `SHA256SUMS` is not signed by Mightling's release key ([RELEASE_SIGNING](./DREAMFERENCE_RELEASE_SIGNING.md)). |
| `rename.rs` | the first run of a binary installed by `puffin update` | The launcher's half of the Puffin → Mightling migration ([RENAME_MIGHTLING §4.2](./DREAMFERENCE_RENAME_MIGHTLING.md)). |
| `notice.rs` | every interactive launch | What the launcher says at start (the air-gap level at `on`, skipped or quarantined skills, what a night run finished, a non-default prompt), also shown inside the TUI. |
| `audit.rs` | `ling audit egress` on Windows | One real `ling exec` traced over ETW and judged as the node's strace audit is ([MIGHTLING_WINDOWS_ARM §14](./DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md)); nothing has run on Windows here. |

`CODEX_HOME` is `~/.mightling` (`home.rs`, patch `0014`), unless already set. On first run the launcher copies an allow-list from `~/.codex` — sessions, history, `config.toml`, rules, skills, prompts, plugins and the session-state databases — so existing sessions carry over, while `auth.json` and debug logs stay behind. `ling-admin logs mcp` reads `~/.mightling/logs_2.sqlite` (`CodexInstaller.home_dir()`).

The crate's unit tests cover:
- argument injection and the no-model commands;
- the refused commands;
- `/v1/models` parsing;
- TOML scoping and preservation of the user's settings;
- the prompt's identity, web section and Gmail block;
- host resolution;
- the help rebrand, the update decision and the `/usage` report;
- cave mode's levels, tiers, `/cavemode` forms and World State render table.

Run them in the **export** directory, never in `codex/`: `cargo test --release -p ling-launcher`. `tests/test_mightling_slash_commands.py` drives every slash command against the installed binary on a pseudo-terminal. It skips its live cases when no model server answers.

### 4.1 The web commands (`ling-web-rs/`)

`ling-search` and `ling-fetch` are the two shell commands `WEB_ACCESS_INSTRUCTIONS` gives the model: search through the SearXNG instance on `127.0.0.1:8888`, then fetch a promising result as readable text. Until 2026-09-30 they were Python, `ling-search` a console script of the repository's virtualenv and fetching a `ling-admin` subcommand, so the model's web access depended on that virtualenv staying on the `PATH` of the shell `ling` gives it. When it was not, every call ended in exit 127 and the model concluded it had no web. They are now static Rust binaries installed beside `ling` (§2, step 8), with no Python involved.

**Where the crate lives.** A standalone crate with its own lockfile, not a module of the launcher and not a member of `ling-code-rs/`:
- inside the launcher, every change would relink the 330 MB Codex binary, a link step that has to be scheduled around the model server's memory; the crate alone builds in seconds while the model serves;
- `ling-code-rs/` was being created at the same time. Both crates follow the same shape (own lockfile, own target directory, `build_crate()`), so they can become members of one Cargo workspace later without changing how either is built or installed.

**Behaviour carried over from `WebTools`** (`dreamference/mcp_server/web_tools.py`):
- **Search:** `GET /search?q=…&format=json&categories=general&language=en` on `DREAMFERENCE_SEARXNG_URL` (default `http://127.0.0.1:8888`); `-n N` results (default 5, at least 1), up to three direct answers, snippets cut at 200 characters in text mode; `--json` prints `{query, result_count, answers, results: [{title, url, snippet, engine}]}`.
- **Its errors:** an empty query; an unreachable instance, with the command that starts it (`ling-admin searxng start`); a response that is not JSON, with the `search.formats` hint; and a search in which *every* engine failed, which names each engine and its reason and suggests restarting the container, because an empty result there once read as "the web has nothing on this".
- **Fetch:** http and https only (a `file://` argument is refused before any request, with `urlparse`'s wording); 25 s to connect and per read, not in total; up to 10 redirects; a 5 MiB cap enforced while the body streams; `--max-chars` (default 8,000, at most 100,000) counted in characters; `--json` prints `{url, final_url, status, content_type, title, text, truncated}`.
- **Text extraction:** `script`, `style`, `noscript`, `svg`, `canvas`, `template`, `iframe` and `form` are dropped with their subtrees; every other text node is joined with a newline; runs of spaces and tabs become one space and blank-line runs become one blank line. The title is the first `<title>`. Non-HTML responses (plain text, JSON, source) are returned as they are. The tree comes from html5ever, walked iteratively (20,000 nested elements parse without overflowing the stack).
- **Output and exit codes:** the same text layout, `❌`/`💡` lines on stdout, exit 1 on a reported error and 2 on a usage error.

**Deliberate differences, each found in the comparison below:**
- **Character sets.** The charset comes from `Content-Type`, then from a `<meta>` declaration in the first 1,024 bytes, then UTF-8; a byte-order mark overrides all three. `requests` decodes any `text/*` response that names no charset as ISO-8859-1 and never reads `<meta>`, which garbled docs.python.org (`re â\x80\x94 Regular expression…` in the title), doc.rust-lang.org, MDN and gnu.org's language list.
- **SearXNG's HTTP errors** are reported as the status it answered, with the `search.formats` hint for 403 (its answer to a JSON request when the format is off), where the Python version called any status "unreachable" and suggested starting an instance that was running.
- **Proxies.** SearXNG is always reached directly. `ling-fetch` honours `<scheme>_proxy` and `all_proxy` (lower case over upper) and skips hosts `no_proxy` names (exact host, domain suffix, `*`), as `requests` does, minus address ranges (`10.0.0.0/8`). ureq's own detection would ignore `no_proxy`.
- **Cookies** are kept for the life of one command, as `requests` keeps them across a redirect chain. Without them theweathernetwork.com ended on a `?_guid_iss_=1` bounce URL; nothing is written to disk.
- **JSON output** is UTF-8 rather than `\uXXXX` escapes, and `final_url` is normalised (`https://example.com/` for `https://example.com`).

**Measured on 2026-09-30:**
- **Text:** 16 real pages (Wikipedia, BBC Weather, docs.python.org, doc.rust-lang.org, MDN, GitHub page, raw file and API, Hacker News, gnu.org, a weather site, httpbin's HTML page, a redirect, two error pages) fetched by both implementations: identical text on every page served with a charset or plain ASCII; on the four affected by the ISO-8859-1 default, a word-sequence similarity of 0.978–0.999 in which every difference is Python's mojibake. Titles identical except the docs.python.org one. The two HTTP errors (404, 403) are reported by both.
- **Search:** six queries through both, back to back: the same five URLs for four, and 3 or 4 of 5 shared for the other two, which is SearXNG's engine variance between two calls rather than a difference in the client.
- **Start-up:** 1.4 ms per `ling-search --help` against 151 ms for the Python console script; a search round trip 0.2–0.35 s against 0.4–0.7 s.
- **In a session** (2026-10-01, rebuilt `ling`, `ling exec -s workspace-write`, asked for Lisbon's weather): the model ran `ling-search` and `ling-fetch` from its shell with no virtualenv on `PATH`. Every SearXNG engine was rate-limited at the time; `ling-search` said so, naming each engine, and the model went on to fetch a forecast with `ling-fetch` and cited it.

**Tests:** 22 unit tests (HTML extraction, charset choice, URL checks, SearXNG mapping and the failed-engines error, rendering, proxy choice) and 12 that run the two binaries against a local server standing in for SearXNG and for web pages: query parameters, redirects and `final_url`, 403 and non-JSON answers, the size cap, no proxy for SearXNG, and `http_proxy`/`no_proxy` for fetch. `cargo test --release --locked` in `ling-web-rs/`; the release job runs them too. `tests/test_web_commands.py` and `tests/test_codex_branded_builder.py` cover the build, the install, the links, and that neither command is a console script any more (in a shell with the virtualenv active, `.venv/bin` comes first on `PATH`, so a leftover script would shadow the binary).

**Known duplication.** `WebTools` stays, because the MCP server's `web_search` and `web_fetch` tools use it, so the same behaviour now exists twice and is kept in step by hand. The follow-up is to have `WebTools` run the two binaries with `--json`; the MCP server would then depend on a Rust build being installed, which is why it was not done here.

---

## 5. What is deliberately not changed

- **Most "Codex" strings in the TUI** (about 365 in `tui/src`): tips, onboarding, approval wording. Only the identity the user sees on every screen (the header, status card, banner, `--version` and usage lines) and the CLI's help text (via `help.rs`) are renamed. More TUI strings can be added to `0001`, one line each.
- **`codex-code-mode-host`**, crate names and the `CODEX_HOME` variable name: renaming them would break lookups inside Codex. The *folder* it points at is Mightling's own (§4).
- **The vendor-hosted features.** Gmail and the other connectors (`codex_apps`) need the vendor's sign-in and run on the upstream vendor's servers, so they never activate in an `--oss` session. Local mail access is `ling-admin gmail` (`DREAMFERENCE_MIGHTLING_GMAIL.md`). Upstream's `app` is replaced by the launcher (§4, step 2).
- **Still visible and unchanged:** `/model` (vLLM serves one model, so the picker lists one entry), `/memories`, `/import`, `/ide`, `/daemon`, and the `plugin` / `doctor` subcommands. The last two still refer to the upstream vendor's marketplace and to `chatgpt.com` connectivity checks. These were reviewed on 2026-09-28 and left for later decisions.
- **`ling-admin gmail`**, the agent's mail command, is still Python: it is a thin client of the Gmail service the web UI runs. The web commands became Rust on 2026-09-30 (§4.1).

---

## 6. Moving to a newer Codex release

1. In `codex/`, fetch the new tag (`git fetch --depth 1 origin tag rust-vX.Y.Z`) and check it out. Then commit the new gitlink in the parent repository.
2. Update `CODEX_RELEASE_TAG` in `codex_branded_builder.py`.
3. Run `ling-admin codex build`. It stops at the first patch that no longer applies.
4. Refresh that patch in a scratch export, never in `codex/`:
   - `git archive` the new commit into an empty directory and `git init` it;
   - apply the patches that still fit, and commit after each;
   - redo the failed patch by hand;
   - regenerate each file with `git diff -U1` between consecutive commits, which keeps the patches small.

5. Check for drift in anything the launcher depends on: the model-catalog fields, the `--oss` and `--local-provider` flags, the `config.toml` keys, `bundled_models_response()`, and the pinned `v8` version with its checksum manifest.
6. Verify:
   - `ling --version` prints `ling X.Y.Z`;
   - the header reads `>_ Mightling`;
   - asked its name, the model answers Mightling;
   - `git -C codex status --porcelain` prints nothing.
   - `ling-admin codex test` passes (§7). After a bump, run it with `--accept-snapshots` first: it rewrites the TUI snapshots the new release changed, keeps those that differ from upstream's by the name alone, and prints the rest for a person to look at.

## 7. Codex's own tests (`ling-admin codex test`)

`CodexTestRunner` (`runner/codex_test_runner.py`) runs Codex's ~20,000 Rust tests on the tree `ling` is built from: the pinned commit, exported with `git archive`, the launcher copied in, the patch series applied. It works in its own export and target directory (`test-src/`, `test-target/` in the builder's cache), never in `codex/` and never in the product build's directories.

**How it runs.** The pinned `cargo-nextest` (checksum-verified, as upstream's CI uses), upstream's `ci-test` profile without debug info, `lld` from the pinned toolchain, eight tests at a time, all inside a `systemd-run --user --scope` with a 24 GiB cap and `choom -n 1000`, so the model server outlives any failure. A full run is about an hour on GB10 beside the model server.

**Things that are load-bearing, each found by a failing run:**
- **Debug info is off.** With it, each of the ~280 test binaries is about 1 GB: 88 GB per tree. On 2026-10-01 two such trees filled the disk. A run now refuses to start without about 40 GiB free.
- **The workspace is put back at version 0.0.0.** Upstream's tests are written against `main`, where every crate is 0.0.0; the release tag says 0.158.0, and ~30 snapshots fail on that alone.
- **The temporary directory is private, short, outside any repository, and 11 random characters long.** Private and short because tests bind Unix sockets under it (108-byte limit, no group-writable directory); outside a repository because the skills tests treat an ancestor's `.git` as a project root; and the length because a pet-image test base64-encodes a path ending in `frame.png` and asserts the output lacks `cG5n`, which the path's own encoding contains whenever `png` falls on a multiple of three bytes.
- **Project markers in `/tmp`.** A test run left an empty `/tmp/.git` (and `.agents`) behind on 2026-10-01, which made `/tmp` a project root and failed a skills test in every later run. The runner warns about them before a run and after it.
- **The launcher steps aside** under `MIGHTLING_UPSTREAM_TESTS`, and `DREAMFERENCE_VLLM_HOST` points at a closed port, so no test reaches the model server.
- **Not isolated from the internet.** Several app-server tests let Codex's model-list refresh reach the real `https://chatgpt.com/backend-api/codex/models` with a test token (answered 401). It is upstream test behaviour, not Mightling's, but it means a test run is not air-gapped. Running the suite in a network namespace with only loopback would close it; that is not done yet.

**The skip list, `codex-tests/mightling-skips.toml`.** A test is listed only when it checks something Mightling does differently on purpose, or cannot pass on this machine for a reason outside Mightling. Every entry carries its reason, and the reasons were checked, not assumed: each patch-caused group passes when its patch is left out of the tree, and each machine-caused group fails the same way on unmodified Codex. The groups: analytics off (patch 0013), the closed network channels (0015), the hosted-account features removed or replaced (`login`, `logout`, `cloud`, `/usage`, `/feedback`, `/pets`, `update`, `doctor`'s network checks), three CLI tests that expect `Usage: codex`, and machine-specific failures (the TLS fallback, the remote exec-server's snapshots, the V8 build).

**Renaming is not skipped.** Patch 0001 changes "Codex" and "Ask Codex to do anything" on screen, which ~130 TUI tests check. Those tests also guard layout, wrapping and every popup, so instead of losing them the test export gets Mightling's expectations before it is built, and neither reaches the product build:
- `codex-tests/snapshots/` holds Mightling's versions of upstream `.snap` files, by path under `codex-rs/`, copied over the originals. A file whose original no longer exists stops the run.
- `codex-tests/patches/` holds test-only diffs (applied after the product patches) for expectations written in Rust: inline snapshots, `contains` checks and the PTY tests' waits. A test asserts they touch only test files.
- `--accept-snapshots` regenerates `codex-tests/snapshots/`: insta rewrites the snapshots the selected tests produce, and a rewritten snapshot is copied in only if it reduces to upstream's once both names become one token and whitespace and box rules are dropped (the shorter name leaves padding behind). Anything else is printed and not accepted. In an ordinary run insta is told never to write snapshots, so a layout change fails.

**Results, 2026-10-01.** After the startup-frame fix to `0001`, `codex test -E 'package(codex-tui)'` through a fresh export and the overlay passes all 5,517 TUI tests, the renamed ones included. **Open:** in the last full run, taken before that fix (20,380 tests: 20,362 passed), 16 app-server tests timed out and still do when run alone: nine `plugin_install` tests, four `external_agent_config_import` tests, two `client_metadata` tests and `guardian_review_turns_and_tools_reach_analytics`. Most wait for an analytics event, which points at patch 0013, but none of them failed in the earlier runs that established the skip list, and they log the model-list refresh reaching `chatgpt.com`, but the network is ruled out: rerun with every proxy variable pointed at a closed port (loopback exempt), all 16 still time out the same way. They were left out of the skip list until a run with 0013 left out settled the cause; it did on 2026-10-02 (below).

**Results, 2026-10-02** (20-patch tree, `--memory-max 14G --jobs 4 --test-threads 4`, beside the model server):
- **TUI: 5,516 of 5,517 pass, and no snapshot changed.** `codex test -E 'package(codex-tui)' --accept-snapshots` rewrote nothing, so the popup snapshots were never broken by `/cavemode`, `/night` or `/airgapped`; none of the snapshotted popups lists them. The one failure is a behaviour change: `slash_tab_completion_moves_cursor_to_end` types `/c` and Tab and expects `/compact`, and gets `/cavemode`, which `0017` places after `/model`, ahead of `/compact`. Its assertion is inline in `chat_composer.rs`, which test-only patches may not touch, so it is in the skip list with that reason; moving `/cavemode` after `/compact` in `0017` would restore upstream's completion.
- **The 16 app-server timeouts are patch 0013's.** The four groups (`plugin_install`, `external_agent_config_import`, `client_metadata`, `guardian_review_turns_and_tools_reach_analytics`) match 72 tests. On the full series 16 time out and 57 pass; on the same tree with `0013` left out of the test export only (a driver replaced `CodexBrandedBuilder.patches()` for that process; `codex-patches/` was untouched), all 72 pass in 46 s. They wait for the analytics event their action emits, which a disabled client never sends. All 16 are now in the skip list, by name.
