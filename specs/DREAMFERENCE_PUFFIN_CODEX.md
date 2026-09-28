# Puffin — Changes Made to Codex

**Status:** implemented. The patch series was cut down from about 406 KB to about 9 KB: everything larger than a one-line hook or a renamed string lives in `puffin-rs/`.
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
| Patches | `codex-patches/000N-*.patch` | Small unified diffs with one line of context, applied in numeric order: renamed strings, the launcher hook, and `hide = true` on subcommands Puffin switches off. |
| Launcher | `puffin-rs/` | Dreamference's own Rust crate. It is kept as source, not as a patch. |
| Builder | `dreamference/runner/codex_branded_builder.py` | Turns the four inputs above into the installed binary. |

The patches are applied to an **exported copy** at build time, never to the submodule. The fork therefore stays byte-identical to upstream. Moving to a newer Codex means bumping the submodule and refreshing whichever hunks no longer apply (§6).

Running `cargo` inside `codex/` is forbidden: even `cargo tree` rewrites the submodule's `Cargo.lock`.

---

## 2. Build pipeline (`puffin-admin codex build [--force]`)

1. **Export.** `git archive <pinned commit> codex-rs` into `~/.cache/dreamference/puffin-codex/src`. Only the Rust workspace is exported; the npm wrapper, Bazel files and SDKs play no part.
2. **Add the launcher.** Copy `puffin-rs/` to `codex-rs/puffin`. This has to happen before the patches, because `0002` makes the CLI depend on it. A path dependency inside the workspace root becomes a workspace member automatically, so the workspace manifest is not patched.
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

1. **Skips setup for commands that never reach a model.** These are `--help`/`-h`, `--version`/`-V`, and the subcommands `help completion apply a features doctor login logout mcp plugin archive unarchive delete sandbox`. They answer at once instead of waiting for a server.
2. **Resolves the vLLM URL**, first match wins:
   - the `DREAMFERENCE_VLLM_HOST` environment variable;
   - `vllm_host` in `DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then `~/.config/dreamference/config.toml`;
   - `http://localhost:8000`.
3. **Waits for the server.** It prints `⏳ Waiting for local vLLM server at … to become available...` with a dot per second, and gives up after 600 s with the `puffin-admin server start` hint.
4. **Reads the served model from `GET /v1/models`**, both its `id` and its `max_model_len`, so the launcher needs no copy of Dreamference's model registry.
5. **Writes `$CODEX_HOME/model_catalog.json`**, a single entry Codex requires before it will use an unknown model. Codex parses it with strict serde structs, so a wrong shape stops it at startup:
   - context window, compaction limit and truncation limit set to `max_model_len`;
   - one reasoning level, `none`;
   - `visibility = "list"`;
   - `tool_mode = "code_mode"`, so the model gets Code Mode's `exec` tool;
   - `base_instructions`: the longest bundled template, passed through `rebrand()` (the opening sentence, which also claims a GPT model, becomes `You are Puffin, a coding agent.`, and every later `Codex` becomes `Puffin`), followed by `WEB_ACCESS_INSTRUCTIONS`, which tells the model to use `puffin-admin search` and `puffin-admin fetch`.
6. **Edits `$CODEX_HOME/config.toml` with `toml_edit`.** Editing the document rather than appending text means a top-level key can never be absorbed into the preceding table, which twice stopped Codex from starting. It sets:
   - `model_catalog_json`, always;
   - `suppress_unstable_features_warning = true` and `check_for_update_on_startup = false`, only if absent. The update check would offer to replace Puffin with upstream Codex.
   - `[model_providers.openai-custom]`: `name`, and a `base_url` that is always rewritten to follow the server;
   - `[features] code_mode = true`, `enable_mcp_apps = false`, only if absent;
   - `[sandbox_workspace_write] network_access = true`, only if absent. Without it, DNS fails inside the sandbox and the web commands break.
7. **Prepends `--oss --local-provider openai-custom --model <id>`**, skipping any of these the user already gave. They are root options, so `exec`, `resume` and `fork` inherit them.

`CODEX_HOME` is still `~/.codex`. Existing sessions and history, and `puffin-admin logs mcp` (which reads `~/.codex/logs_2.sqlite`), keep working.

The crate's unit tests cover argument injection, the no-model commands, `/v1/models` parsing, TOML scoping, preservation of the user's settings, the prompt's identity and web section, and host resolution. Run them in the **export** directory, never in `codex/`: `cargo test --release -p puffin-launcher`.

---

## 5. What is deliberately not changed

- **Most "Codex" strings in the TUI** (about 365 in `tui/src`): tips, onboarding, approval wording. Only the identity the user sees on every screen, the header, status card, banner, `--version` and usage lines, is renamed. More can be added to `0001`, one line each.
- **`codex-code-mode-host`**, crate names, `CODEX_HOME` and the `~/.codex` directory: renaming them would break lookups inside Codex, or separate users from their existing sessions.
- **OpenAI-hosted features.** Gmail and the other connectors (`codex_apps`) need a ChatGPT login and run on OpenAI's servers, so they never activate in an `--oss` session. `codex app` opens OpenAI's closed-source desktop app and is compiled only for macOS and Windows. Local mail access is specified separately in `DREAMFERENCE_PUFFIN_GMAIL.md`.
- **The tools the agent calls.** The launcher is Rust, but `puffin-admin search`, `puffin-admin fetch` and the planned `puffin-admin gmail` are still Python.

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
