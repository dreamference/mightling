# Building `ling` from Codex

Developer notes behind the "`ling` is Codex, built here" summary in `AGENTS.md`. Specs: `specs/DREAMFERENCE_MIGHTLING_CODEX.md`, `specs/DREAMFERENCE_CODEX.md`. The launcher compiled into it is described in [launcher.md](launcher.md).

## `ling` is Codex with Mightling's branding and launcher compiled in, never upstream `codex`

`codex/` is a submodule of the `dgxcoder/codex` fork of upstream Codex (`openai/codex`), pinned to a stable tag (`CODEX_RELEASE_TAG`, currently `rust-v0.158.0`) and **never modified**.

`CodexBrandedBuilder` (`runner/codex_branded_builder.py`):

1. exports the pinned commit's `codex-rs/` with `git archive` into `~/.cache/dreamference/puffin-codex/src`;
2. copies the launcher crate `ling-rs/` in as `codex-rs/ling`;
3. applies the numbered diffs in `codex-patches/` there with `git apply --check` then `git apply`;
4. builds Cargo's `codex` binary (installed as `ling`) plus `codex-code-mode-host` (Code Mode's V8 host, which Codex looks for next to its own executable, resolving symlinks).

The result goes to `~/.local/share/dreamference/mightling/bin/`, with `~/.local/bin/ling` symlinked to it, `~/.local/bin/ling-search` and `ling-fetch` symlinked to the installed web binaries, and `~/.local/bin/ling-admin` to the building environment's console script, because the prompt tells the model to run `ling-search`, `ling-fetch` and `ling-admin gmail` and the shell `ling` gives it has no virtualenv on PATH (every such call used to end in exit 127; `codex build` refreshes the links even when the binary is current). Those commands reach the network only under the workspace-write sandbox, where the launcher sets `network_access = true`; `ling exec`'s default read-only sandbox has no network, so there they fail by design.

A stamp keyed on the commit, the patch bytes, the launcher source and the profile overrides means an unchanged tree is not rebuilt. (The cache directory keeps its old `puffin-codex` name because renaming it would discard the compiled dependencies.) `CodexInstaller` resolves that path **only**: a fallback to a `codex` on PATH would silently bring back the unbranded agent. Build or rebuild it with `ling-admin codex build [--force]`.

The agent's web commands, `ling-search` and `ling-fetch`, are Rust binaries from the standalone crate `ling-web-rs/` (own lockfile, own target directory, built `--locked` and stamped separately by `CodexBrandedBuilder.build_web_tools()`, so changing them never relinks Codex), installed beside `ling`; `WebTools` in `mcp_server/web_tools.py` is now only the MCP server's copy of the same behaviour.

## Things found the hard way

### The patches are one-line hooks and nothing else

23 patches, ~42.7 KB in all; `test_the_patches_stay_small` caps them at 43 KB. The cap's history:

- raised from 20 KB on 2026-09-30 for the last on-screen Codex names;
- to 25 KB on 2026-10-01 for `0017-cave-mode`;
- to 27.5 KB the same day for `0018-night-slash-command`;
- to 31.5 KB for `0019-airgapped`;
- to 32.5 KB on 2026-10-02 for `0020-flat-mcp-tools` (MCP tools reach the local model only as plain functions: SGLang and vLLM drop Codex's `namespace` tool type, so before it no MCP server, the code index's included, was ever visible to the model);
- to 33.75 KB on 2026-10-03 for `0019`'s Full Access hooks;
- to 34.75 KB the same day for `0021-observation-masking` (old tool outputs replaced in the request by a placeholder naming a saved copy; the rule is the leaf crate `ling-rs/masking`, off by default, spec `specs/DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md`);
- to 36.25 KB on 2026-10-06 for `0022-ling-apps` (`/apps` without the vendor's sign-in: Gmail, Drive and Calendar);
- to 37.5 KB the same day for `0023-airgapped-app-server` (the app server refuses Full Access at `on` for every client; the user approved up to 38.5 KB);
- to 39.5 KB on 2026-10-07 for `0024-airgapped-windows` (2,042 bytes: Codex's two elevated Windows sandbox entry points clear a sealed session's network, so the command runs as the offline account the firewall blocks; the user approved up to 39.5 KB; spec `specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md` §7.3);
- to 41 KB the same day for `0025-node-slash-command` (`/node` suspends the TUI and runs `ling-admin node …` in the terminal, outside the sandbox, through the external editor's `Tui::with_restored`; logic in `ling-rs/src/node_command.rs`; allowed at `/airgapped on` with a one-line notice, since it talks to the LAN only);
- to 43 KB on 2026-10-08 when the two were merged together (each approval was given with the other absent: 42.7 KB in all);
- and to be raised again, explicitly, for each hook that needs it.

`0001` renames what is visible: session header, status card, `exec` banner, clap `name`/`bin_name`/usage (so `--version` says `ling`) and subcommand usage lines. `0002` adds `ling-launcher = { path = "../ling" }` to the CLI's manifest (a path dependency inside the workspace root joins the workspace by itself) and replaces `MultitoolCli::parse()` with `ling_launcher::help::parse(ling_launcher::args().await?)`, which also rewrites "Codex" in every about/help string of the clap tree at parse time (`ling-rs/src/help.rs`) so no doc comment needs a patch; paths such as `~/.codex` and `$CODEX_HOME` are left alone. `0001` still carries `exec`'s hard-coded `override_usage`, since clap has no getter for it.

Everything else is done from `ling-rs/` instead of a patch: vendored OpenSSL is a target dependency in its `Cargo.toml` (Cargo unifies the feature across the build), the prompt's identity is renamed by `rebrand()` when the catalog is written (it used to be a ~390 KB diff of `models.json`), and the binary is not renamed in Cargo at all: Cargo builds `codex` and the builder installs it as `ling`. Regenerate patches with `git diff -U1` in a scratch export.

### V8 is not denoland's

Codex needs V8 built with pointer compression and the sandbox on, which denoland does not publish for aarch64 Linux, so the `v8` crate's own download 404s. `fetch_rusty_v8()` does what the submodule's `.github/actions/setup-rusty-v8` does: it fetches the upstream vendor's `rusty-v8-v<version>` release assets, checks them against `third_party/v8/rusty_v8_*_release_manifests.sha256` from the submodule, and passes them to Cargo as `RUSTY_V8_ARCHIVE` / `RUSTY_V8_SRC_BINDING_PATH`.

### No `--locked`

Upstream's `Cargo.lock` records the workspace crates at `0.0.0`, which its release job bumps just before building. Only those 158 entries change; third-party pins do not.

### Never run cargo inside `codex/`

Even `cargo tree` rewrites the submodule's `Cargo.lock`. The launcher's unit tests run with `cargo test --release -p ling-launcher` in the *export* directory, never in `codex/`.

### `ling-engine/` is the other submodule

`dreamference/ling-engine`, added 2026-10-08: the spec of a Qwen3.8-27B-specific model server, nothing more yet. CI and the release fetch only `codex` by name, the builder exports only `codex`, and the wheel carries neither, so it never reaches a build; the code index includes it (same owner and authors).

## Codex's own tests

`ling-admin codex test` (`runner/codex_test_runner.py`) runs Codex's own tests on the patched tree. `codex-tests/` holds what it needs: `mightling-skips.toml` (each skipped test with its reason), `snapshots/` (Mightling's accepted versions of upstream's `.snap` files) and `patches/` (test-only diffs); none of it reaches the build `ling` comes from.
