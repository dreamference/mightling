# Puffin — Changes Made to Codex

**Status:** implemented. The patch series was cut down from about 406 KB to about 9 KB; the later patches `0005`–`0012` bring it to about 15 KB (15,389 bytes on 2026-09-28), just under the 16,000-byte limit `test_the_patches_stay_small` enforces. Everything larger than a one-line hook or a renamed string lives in `puffin-rs/`.
**Supersedes:** `DREAMFERENCE_CODEX.md`, which describes the older setup where an upstream `codex` on PATH was launched from Python.
**Upstream:** [openai/codex](https://github.com/openai/codex), release `rust-v0.158.0`.

`puffin` is Dreamference's terminal coding agent. It is OpenAI's Codex CLI with the name changed, the system prompt's identity changed, and a small launcher compiled in that points each session at the model this machine serves. Everything else, including the TUI, `exec`, `resume`, `fork`, sandboxing, Code Mode and every flag, is Codex's own code, unchanged. Someone who knows `codex` can use `puffin` the same way.

This document lists every change and where it lives.

---

## 1. Principle: the Codex source is never edited

| Piece | Location | Role |
| --- | --- | --- |
| Fork | `github.com/dgxcoder/codex` | A fork of openai/codex that carries upstream's history and tags. |
| Submodule | `codex/` (shallow) | Pinned to the `rust-v0.158.0` tag commit (`064c6b8`). **Never modified.** |
| Patches | `codex-patches/00NN-*.patch` | Small unified diffs with one line of context, applied in file-name order: renamed strings, the launcher hook, and one-line switches that hide or reroute commands (§3). Numbers `0003`–`0004` are unused; they belonged to the pre-minimisation series. |
| Launcher | `puffin-rs/` | Dreamference's own Rust crate. It is kept as source, not as a patch. |
| Builder | `dreamference/runner/codex_branded_builder.py` | Turns the four inputs above into the installed binary. |

The patches are applied to an **exported copy** at build time, never to the submodule. The fork therefore stays byte-identical to upstream. Moving to a newer Codex means bumping the submodule and refreshing whichever hunks no longer apply (§6).

Running `cargo` inside `codex/` is forbidden: even `cargo tree` rewrites the submodule's `Cargo.lock`.

---

## 2. Build pipeline (`puffin-admin codex build [--force]`)

0. **Lock.** `build()` takes an exclusive `flock` on `~/.cache/dreamference/puffin-codex/.build.lock`. Every build begins by wiping the shared source tree, so two concurrent builds used to destroy each other mid-compile ("Could not locate working directory"). A second build now waits, then re-checks the build key and returns at once if the first build already produced it.
1. **Export.** `git archive <pinned commit> codex-rs` into `~/.cache/dreamference/puffin-codex/src`. Only the Rust workspace is exported; the npm wrapper, Bazel files and SDKs play no part.
2. **Add the launcher.** Copy `puffin-rs/` to `codex-rs/puffin`. This has to happen before the patches, because `0002` makes the CLI depend on it. A path dependency inside the workspace root becomes a workspace member automatically, so the workspace manifest is not patched.
   - The copy uses `shutil.copy`, not `copy2`. That gives the files fresh modification times, which matters because Cargo decides freshness by mtime. A copied file with its original, older mtime was once taken as already compiled, and a stale launcher got linked in.
3. **Patch.** For each file in `codex-patches/`, run `git apply --check` and then `git apply`. A patch that does not fit stops the build before anything is half-applied, and the error names the release tag the patches were written for.
4. **Fetch V8.** Code Mode embeds V8 built with pointer compression and the sandbox enabled. denoland does not publish that build for aarch64 Linux, so the `v8` crate's own download returns 404. `fetch_rusty_v8()` does what upstream's `.github/actions/setup-rusty-v8` does:
   - it downloads OpenAI's `rusty-v8-v<version>` release assets;
   - it verifies them against `third_party/v8/rusty_v8_*_release_manifests.sha256` from the submodule;
   - it passes the archive and bindings to Cargo as `RUSTY_V8_ARCHIVE` and `RUSTY_V8_SRC_BINDING_PATH`.

   Verified files are cached, so later builds don't download them again.
5. **Compile.** Run `cargo build --release -p codex-cli --bin codex -p codex-code-mode-host --bin codex-code-mode-host` with these settings. Cargo still calls the binary `codex`; step 6 names the file.
   - `CARGO_TARGET_DIR=~/.cache/dreamference/puffin-codex/target`. This directory persists, so a patch edit recompiles only the crates it touches.
   - `CARGO_PROFILE_RELEASE_DEBUG=none` and `CARGO_PROFILE_RELEASE_STRIP=debuginfo`. Upstream keeps line tables and strips only when it packages; built as-is, the binary is 1.4 GB instead of about 315 MB. These are set through the environment, not a patch, so they touch no Codex source.
   - No `--locked`. Upstream's `Cargo.lock` records the workspace crates at `0.0.0`, and its release job bumps them just before building, so Cargo rewrites those 158 entries. Every third-party version stays as pinned.
   - In release builds, the `puffin` job of `.github/workflows/release.yml` sets `PUFFIN_VERSION=<release version>`. `update.rs` reads it with `option_env!`; a local build has none and reports itself as `source`.
6. **Install.** Copy the binaries to `~/.local/share/dreamference/puffin/bin/`:
   - `puffin`, which is Cargo's `codex` binary under its new name;
   - `codex-code-mode-host`, which keeps its upstream name because Codex looks for that exact name next to its own executable.

   Each is written to a temporary name and renamed into place. `~/.local/bin/puffin` is then symlinked to the installed binary. The helper is still found through the link, because Codex resolves its own executable path. The link only replaces a missing file or an existing symlink.
7. **Stamp.** A `build-key` file records a hash of every input: the source commit, the patch bytes, the launcher source and the profile overrides. An unchanged tree is not rebuilt; any change to an input triggers a rebuild.

**Host requirements:** Rust through rustup (the toolchain version is pinned by `codex-rs/rust-toolchain.toml` and fetched on first use), `git`, `tar`, `perl` and a C compiler. No `libssl-dev` (the launcher crate enables `openssl-sys/vendored`, §4) and no `libcap-dev`: the bundled `bwrap` binary is not built, and Codex falls back to the system's `/usr/bin/bwrap`.

---

## 3. The patches

Only changes that cannot be made from outside are patches, and each is a one-line edit. Anything larger goes in `puffin-rs/`, which Cargo compiles into the same binary. A test (`test_the_patches_stay_small`) keeps the series under 16 KB.

### `0001-brand-puffin-name.patch` (visible name)

| File | Change |
| --- | --- |
| `tui/src/history_cell/session.rs` | Session header `>_ OpenAI Codex (vX)` → `>_ Puffin (vX)`, in the compact, boxed and raw renderings |
| `tui/src/status/card.rs` | `/status` card title → `Puffin` |
| `exec/src/event_processor_with_human_output.rs` | `exec` banner `OpenAI Codex vX` → `Puffin vX` |
| `cli/src/main.rs` | clap `name`, `bin_name`, `override_usage` and the shell-completion name → `puffin`, so `--version` prints `puffin 0.158.0` |
| `cli/src/plugin_cmd.rs`, `marketplace_cmd.rs`, `mcp_cmd.rs` | Subcommand usage lines `codex plugin …`, `codex mcp add …` → `puffin …` |
| `exec/src/cli.rs` | `exec`'s hard-coded usage `codex exec …` → `puffin exec …` (clap exposes no getter for `override_usage`, so the parse-time rebrand cannot reach it) |

### `0002-puffin-launcher-hook.patch` (the hook)

| File | Change |
| --- | --- |
| `cli/Cargo.toml` | `puffin-launcher = { path = "../puffin" }` |
| `cli/src/main.rs` | `MultitoolCli::parse()` → `puffin_launcher::help::parse(puffin_launcher::args().await?)`, which rebrands the clap help tree at parse time (`puffin-rs/src/help.rs`) |

The call sits in `cli_main`. That is after `arg0` dispatch, so the `codex-linux-sandbox`, `apply_patch` and `codex-execve-wrapper` aliases never reach it, and before Codex parses its command line.

### `0005`–`0012` (switches: hide, reroute, replace)

Hiding a subcommand only removes it from `--help`, so each hidden CLI subcommand that must not run is *also* refused by the launcher (§4, step 1). A hidden slash command (`is_visible() == false`) is gone from the popup, and typing it is not recognised either, because the command lookup only matches visible commands. In every case the code behind the command stays compiled.

| Patch | File(s) | Change |
| --- | --- | --- |
| `0005-hide-openai-login` | `cli/src/main.rs`, `tui/src/slash_command.rs` | `login` / `logout` get `hide = true`; `/logout` is not visible. There is no OpenAI account to sign in to. |
| `0006-hide-cloud` | `cli/src/main.rs` | `cloud` (alias `cloud-tasks`) gets `hide = true`. It is kept for a future private cloud. |
| `0007-hide-remote-control` | `cli/src/main.rs` | `remote-control` gets `hide = true`. It relays through OpenAI's servers today and is kept for a private relay later. It is hidden only, **not** refused: `puffin remote-control` still runs, and fails without a ChatGPT login. |
| `0008-puffin-update` | `cli/src/main.rs` | `update` calls `puffin_launcher::update::run()` instead of Codex's installer logic, and its help reads "Update Puffin to the latest release." |
| `0009-remove-feedback` | `tui/src/slash_command.rs`, `tui/src/chatwidget/turn_runtime.rs` | `/feedback`, which uploads session logs to OpenAI, is not visible. The interrupted-turn hint no longer says "Hit `/feedback`…". |
| `0010-hide-voice` | `tui/src/slash_command.rs` | `/voice`, OpenAI's realtime voice API, is not visible. It is kept for a future local voice. |
| `0011-usage-token-stats` | `tui/Cargo.toml`, `tui/src/bottom_pane/slash_commands.rs`, `tui/src/chatwidget/slash_dispatch.rs`, `tui/src/slash_command.rs` | `/usage` is always listed, and shows this session's token statistics from `puffin_launcher::usage::report()`, fed by the counters `/status` already uses, instead of ChatGPT plan limits. It drops upstream's `/usage daily\|weekly\|cumulative` form. The TUI crate gains a path dependency on the launcher. |
| `0012-hide-auto-review` | `tui/src/slash_command.rs` | `/approve` (`SlashCommand::AutoReview`) is not visible. It defaults to OpenAI's `codex-auto-review` model, which the local catalog lacks. |

### What used to be patches

| Former patch | Now |
| --- | --- |
| Vendored OpenSSL in `core/Cargo.toml` | `puffin-rs/Cargo.toml` depends on `openssl-sys` with `vendored` for glibc Linux. Cargo unifies features across the build, so every crate that links OpenSSL gets the vendored build. |
| `[[bin]] name` and `default-run` in `cli/Cargo.toml` | Not renamed: Cargo builds `codex` and the builder installs it as `puffin`. |
| Workspace `members` and `[workspace.dependencies]` | Not needed: the launcher is a path dependency inside the workspace root. |
| The identity in `models-manager/models.json` (~390 KB, because each template is one JSON line) | `rebrand()` in the launcher renames the prompt when it writes the model catalog. |

---

## 4. The launcher (`puffin-rs/`, crate `puffin-launcher`)

This is the Rust port of what Dreamference's Python `puffin` entry point used to do before exec'ing Codex. That entry point is gone. `CodexRunner` now only builds `puffin` and runs it, so there is no second copy of the setup to drift.

`args()`, the function the hook calls, runs `prepare_args` on the process's argv:

1. **Refuses switched-off commands** (`REMOVED_COMMANDS`): `login`, `logout`, `cloud` and `cloud-tasks` exit with `` `puffin <name>` is not available: <reason>. `` before Codex parses anything. The check looks at the *first* argument only, so `puffin -c key=value cloud` still reaches Codex; that is a known gap.
2. **Handles `app` itself** (`app.rs`): `puffin app` opens Puffin's desktop window, `puffin-app`, instead of OpenAI's closed-source app.
   - It finds the window on PATH, or through the `Exec=` line of the `puffin-app` desktop entry.
   - It checks that the Onyx web UI answers on `localhost:3000` and empties the webview's HTTP cache (keeping the sign-in).
   - It starts the window detached, so closing the terminal does not close it.
   - A folder argument, which Codex's `app` takes, is ignored with a note.
3. **Skips setup for commands that never reach a model.** These are `--help`/`-h`, `--version`/`-V`, and the subcommands `help completion apply a features doctor mcp plugin archive unarchive delete sandbox update`. They answer at once instead of waiting for a server. (`update` goes on to `update.rs` through patch `0008`.)
4. **Resolves the vLLM URL**, first match wins:
   - the `DREAMFERENCE_VLLM_HOST` environment variable;
   - `vllm_host` in `DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then `~/.config/dreamference/config.toml`;
   - `http://localhost:8000`.
5. **Waits for the server.** It prints `⏳ Waiting for local vLLM server at … to become available...` with a dot per second, and gives up after 600 s with the `puffin-admin server start` hint.
6. **Reads the served model from `GET /v1/models`**, both its `id` and its `max_model_len`, so the launcher needs no copy of Dreamference's model registry.
7. **Checks for Gmail.** Unless `puffin_gmail = false` (config file) or `DREAMFERENCE_PUFFIN_GMAIL=false`, it asks the Gmail service's `/status` (`http://127.0.0.1:8767`, 1 s timeout). If an account is connected, `gmail_access_instructions()` adds an "Email access" block after the web section. The block names the accounts and the `puffin-admin gmail` commands, and warns that email content is untrusted data, never instructions. See `DREAMFERENCE_PUFFIN_GMAIL.md`.
8. **Writes `$CODEX_HOME/model_catalog.json`**, a single entry Codex requires before it will use an unknown model. Codex parses it with strict serde structs, so a wrong shape stops it at startup:
   - context window, compaction limit and truncation limit set to `max_model_len`;
   - one reasoning level, `none`;
   - `visibility = "list"`;
   - `tool_mode = "code_mode"`, so the model gets Code Mode's `exec` tool;
   - `base_instructions`: the longest bundled template, passed through `rebrand()` (the opening sentence, which also claims a GPT model, becomes `You are Puffin, a coding agent.`, and every later `Codex` becomes `Puffin`), followed by `WEB_ACCESS_INSTRUCTIONS`, which tells the model to use `puffin-admin search` and `puffin-admin fetch`, then the Gmail block from step 7 if any.
9. **Edits `$CODEX_HOME/config.toml` with `toml_edit`.** Editing the document rather than appending text means a top-level key can never be absorbed into the preceding table, which twice stopped Codex from starting. It sets:
   - `model_catalog_json`, always;
   - `suppress_unstable_features_warning = true` and `check_for_update_on_startup = false`, only if absent. The update check would offer to replace Puffin with upstream Codex.
   - `[model_providers.openai-custom]`: `name`, and a `base_url` that is always rewritten to follow the server;
   - `[features] code_mode = true`, `enable_mcp_apps = false`, only if absent;
   - `[sandbox_workspace_write] network_access = true`, only if absent. Without it, DNS fails inside the sandbox and the web commands break.
10. **Prepends `--oss --local-provider openai-custom --model <id>`**, skipping any of these the user already gave. They are root options, so `exec`, `resume` and `fork` inherit them.

The hook then parses the result with `help::parse` (`help.rs`), not `MultitoolCli::parse_from`. It walks the whole clap command tree once and replaces the product name in every about, help and usage string: "OpenAI Codex" and "Codex CLI" become "Puffin", `codex` as the typed command becomes `puffin`. Paths and identifiers that merely contain the name (`~/.codex`, `$CODEX_HOME`, `codex-code-mode-host`, `openai/codex`) are left alone. This is how dozens of help strings across several crates are rebranded without a patch per string.

**Other launcher modules:**

| Module | Reached from | What it does |
| --- | --- | --- |
| `update.rs` | `puffin update` (patch `0008`) | Asks the GitHub API for the latest *published* release of `dgxcoder/dgxcoder`; drafts and pre-releases are not offered. The repo is private, so it authenticates with `GH_TOKEN`, `GITHUB_TOKEN` or `gh auth token`. It compares the release with `PUFFIN_VERSION`; a source build has none and always installs. It downloads the gzipped `puffin` and `codex-code-mode-host` and the `sha256sums` file, verifies both archives before replacing either, and swaps them in next to the running executable. |
| `usage.rs` | `/usage` (patch `0011`) | Formats the session's input tokens (cached / new), output tokens (plus reasoning tokens), total, and the last request's share of the context window, or "No tokens used yet in this session." |
| `app.rs` | `puffin app` (step 2) | Opens `puffin-app`. |
| `help.rs` | every parse | Rebrands the help tree (above). |

`CODEX_HOME` is still `~/.codex`. Existing sessions and history, and `puffin-admin logs mcp` (which reads `~/.codex/logs_2.sqlite`), keep working.

The crate's unit tests cover:
- argument injection and the no-model commands;
- the refused commands;
- `/v1/models` parsing;
- TOML scoping and preservation of the user's settings;
- the prompt's identity, web section and Gmail block;
- host resolution;
- the help rebrand, the update decision and the `/usage` report.

Run them in the **export** directory, never in `codex/`: `cargo test --release -p puffin-launcher`. `tests/test_puffin_slash_commands.py` drives every slash command against the installed binary on a pseudo-terminal. It skips its live cases when no model server answers.

---

## 5. What is deliberately not changed

- **Most "Codex" strings in the TUI** (about 365 in `tui/src`): tips, onboarding, approval wording. Only the identity the user sees on every screen (the header, status card, banner, `--version` and usage lines) and the CLI's help text (via `help.rs`) are renamed. More TUI strings can be added to `0001`, one line each.
- **First-run sign-in screen.** With an empty `CODEX_HOME`, `puffin` still opens on Codex's "Sign in with ChatGPT" onboarding screen instead of the composer. The launcher writes the provider config, but the onboarding check runs anyway. This is a known defect found by `test_a_fresh_home_opens_on_the_composer` (2026-09-28), not yet fixed.
- **`codex-code-mode-host`**, crate names, `CODEX_HOME` and the `~/.codex` directory: renaming them would break lookups inside Codex, or separate users from their existing sessions.
- **OpenAI-hosted features.** Gmail and the other connectors (`codex_apps`) need a ChatGPT login and run on OpenAI's servers, so they never activate in an `--oss` session. Local mail access is `puffin-admin gmail` (`DREAMFERENCE_PUFFIN_GMAIL.md`). Upstream's `app` is replaced by the launcher (§4, step 2).
- **Still visible and unchanged:** `/model` (vLLM serves one model, so the picker lists one entry), `/memories`, `/import`, `/ide`, `/daemon`, and the `plugin` / `doctor` subcommands. The last two still refer to OpenAI's marketplace and to `chatgpt.com` connectivity checks. These were reviewed on 2026-09-28 and left for later decisions.
- **The tools the agent calls.** The launcher is Rust, but `puffin-admin search`, `puffin-admin fetch` and `puffin-admin gmail` are Python.

---

## 6. Moving to a newer Codex release

1. In `codex/`, fetch the new tag (`git fetch --depth 1 origin tag rust-vX.Y.Z`) and check it out. Then commit the new gitlink in the parent repository.
2. Update `CODEX_RELEASE_TAG` in `codex_branded_builder.py`.
3. Run `puffin-admin codex build`. It stops at the first patch that no longer applies.
4. Refresh that patch in a scratch export, never in `codex/`:
   - `git archive` the new commit into an empty directory and `git init` it;
   - apply the patches that still fit, and commit after each;
   - redo the failed patch by hand;
   - regenerate each file with `git diff -U1` between consecutive commits, which keeps the patches small.

5. Check for drift in anything the launcher depends on: the model-catalog fields, the `--oss` and `--local-provider` flags, the `config.toml` keys, `bundled_models_response()`, and the pinned `v8` version with its checksum manifest.
6. Verify:
   - `puffin --version` prints `puffin X.Y.Z`;
   - the header reads `>_ Puffin`;
   - asked its name, the model answers Puffin;
   - `git -C codex status --porcelain` prints nothing.
