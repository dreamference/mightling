# Building `ling` from Codex

Developer notes behind the "`ling` is Codex, built here" summary in `AGENTS.md`. Specs: `specs/DREAMFERENCE_MIGHTLING_CODEX.md`, `specs/DREAMFERENCE_CODEX.md`. The launcher compiled into it is described in [launcher.md](launcher.md).

## `ling` is Codex with Mightling's branding and launcher compiled in, never upstream `codex`

`codex/` is a submodule of the `dreamference/codex` fork of upstream Codex (`openai/codex`), pinned to a stable tag (`CODEX_RELEASE_TAG`, currently `rust-v0.158.0`) and **never modified**.

`CodexBrandedBuilder` (`runner/codex_branded_builder.py`):

1. exports the pinned commit's `codex-rs/` with `git archive` into `~/.cache/dreamference/puffin-codex/src`;
2. copies the launcher crate `ling-rs/` in as `codex-rs/ling`;
3. applies the numbered diffs in `codex-patches/` there with `git apply --check` then `git apply`;
4. builds Cargo's `codex` binary (installed as `ling`) plus `codex-code-mode-host` (Code Mode's V8 host, which Codex looks for next to its own executable, resolving symlinks).

The result goes to `~/.local/share/dreamference/mightling/bin/`, with `~/.local/bin/ling` symlinked to it, `~/.local/bin/ling-search` and `ling-fetch` symlinked to the installed web binaries, and `~/.local/bin/ling-admin` to the building environment's console script, because the prompt tells the model to run `ling-search`, `ling-fetch` and `ling-admin gmail` and the shell `ling` gives it has no virtualenv on PATH (every such call used to end in exit 127; `codex build` refreshes the links even when the binary is current). Those commands reach the network in every sandbox below `/airgapped on`: the launcher sets `network_access = true` for workspace-write, and patch `0031` widens the read-only sandbox ([airgapped.md](airgapped.md)).

A stamp keyed on the commit, the patch bytes, the launcher source and the profile overrides means an unchanged tree is not rebuilt. (The cache directory keeps its old `puffin-codex` name because renaming it would discard the compiled dependencies.) `CodexInstaller` resolves that path **only**: a fallback to a `codex` on PATH would silently bring back the unbranded agent. Build or rebuild it with `ling-admin codex build [--force]`.

The agent's web commands, `ling-search` and `ling-fetch`, are Rust binaries from the standalone crate `ling-web-rs/` (own lockfile, own target directory, built `--locked` and stamped separately by `CodexBrandedBuilder.build_web_tools()`, so changing them never relinks Codex), installed beside `ling`; `WebTools` in `mcp_server/web_tools.py` is now only the MCP server's copy of the same behaviour.

## The compile cache

Several builds compile the same crates in separate target directories: `ling-admin codex build` (the export's `target/`), the launcher and bridge tests in that export, and `ling-web-rs/`, `ling-code-rs/` and `ling-docs-rs/` in every worktree. [sccache](https://github.com/mozilla/sccache) sits in front of `rustc` and keeps what it compiled in one local disk cache, so a crate compiled by one of them is fetched by the next.

**Install it per user, no sudo.** The prebuilt musl binary from the project's GitHub release, checked against the `.sha256` published beside it, into `~/.cargo/bin` (already on cargo's PATH). On the GB10 (aarch64), 2026-10-26: v0.18.0 (v0.19.0 was two days old), tarball `sccache-v0.18.0-aarch64-unknown-linux-musl.tar.gz`, SHA-256 `2b3284d5da3b46a47dc4229e75bb7b88ac4aa99c8d754fb7d2f84997e5a4354a`:

```bash
gh release download v0.18.0 -R mozilla/sccache -p 'sccache-v0.18.0-aarch64-unknown-linux-musl.tar.gz*'
echo "$(cat sccache-v0.18.0-aarch64-unknown-linux-musl.tar.gz.sha256)  sccache-v0.18.0-aarch64-unknown-linux-musl.tar.gz" | sha256sum -c
tar xzf sccache-v0.18.0-aarch64-unknown-linux-musl.tar.gz
install -m 755 sccache-v0.18.0-aarch64-unknown-linux-musl/sccache ~/.cargo/bin/sccache
```

The cap is in `~/.config/sccache/config`, so every server gets it however it was started:

```toml
[cache.disk]
dir = "/home/<user>/.cache/sccache"
size = 42949672960  # 40 GiB
```

**The builder uses it by itself.** `CodexBrandedBuilder.compiler_cache_environment()` adds `RUSTC_WRAPPER=sccache` to the Codex build and to each standalone crate's build when `sccache` is on the build's PATH, with `SCCACHE_DIR` (`~/.cache/sccache`) and `SCCACHE_CACHE_SIZE` (`40G`) unless already set, `SCCACHE_IGNORE_SERVER_IO_ERROR=1` (a server that dies mid-build costs a miss, not the build) and `CARGO_INCREMENTAL=0` (sccache cannot cache an incremental compile). It starts the server first (`sccache --show-stats`) and builds without it if the server does not answer. The build line says `through sccache` when it is on. `DREAMFERENCE_SCCACHE=0` turns it off; a `RUSTC_WRAPPER` already in the environment is left alone. Where sccache is absent (a release install, the CI runners) nothing changes. `ling-admin codex test` does not use it: its environment moves `HOME` and `TMPDIR` to folders it deletes, which a server started there would outlive.

**The target directory is a flag, never `CARGO_TARGET_DIR`.** sccache hashes every `CARGO_*` variable `rustc` sees, so the variable keys every crate on the folder and no other target directory can reuse it. Measured on `ling-docs-rs` into a fresh folder with a warm cache: 1 hit in 203 with `CARGO_TARGET_DIR`, 177 with `--target-dir`. The builder passes `--target-dir`; do the same by hand. The same goes for the profile overrides: a test build reuses the Codex build's crates only with the same `CARGO_PROFILE_RELEASE_*` values.

**By hand** (the test commands in `AGENTS.md`):

```bash
export RUSTC_WRAPPER=sccache CARGO_INCREMENTAL=0
# The launcher's tests, in the export, reusing the Codex build's crates:
cd ~/.cache/dreamference/puffin-codex/src/codex-rs
CARGO_PROFILE_RELEASE_DEBUG=none CARGO_PROFILE_RELEASE_STRIP=debuginfo \
  cargo test --release -p ling-launcher --target-dir ~/.cache/dreamference/puffin-codex/target
# A standalone crate:
cd ling-docs-rs && cargo test --locked
sccache --show-stats   # hits and misses since the server started
```

On the GB10, under `flock ~/.ling-build.lock nice -n 10` and `-j 12`, keep the disk above 150 GB free, and do not start a full Codex build while a SWE-bench night runs (`systemctl --user list-units 'ling-swe-*'`).

**Measured** 2026-10-26, `cargo build --release --locked -j 12` of `ling-docs-rs/` (261 rustc calls, 52 of them not cacheable: build scripts, the final link) into a fresh target directory each time: no cache 36.8 s; sccache with an empty cache 39.3 s (the cost of filling it, +7 %); sccache warm 15.7 s (177 hits, 26 misses: crates whose build script's `OUT_DIR` names the folder). The cache held 165 MiB afterwards. A cold and a warm `codex build --force` were not measured that day: a SWE-bench night was running.

## Things found the hard way

### The patches are one-line hooks and nothing else

32 patches, ~59.5 KB in all (59,496 bytes); `test_the_patches_stay_small` caps them at 60 KB. The cap's history:

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
- not raised on 2026-10-12, when `0020` grew by 217 bytes (to 42,958 in all) so Code Mode's `exec` works on SGLang: the request hook also takes the request's input and one line turns each output item back as it arrives (`ling-rs/tools/src/custom.rs`; [launcher.md](launcher.md));
- to 45 KB on 2026-10-14, once, by the owner, for three changes together: drop-first compaction (`0026-drop-old-outputs`, 1,039 bytes: three lines at the top of `run_auto_compact` that skip the summary when dropping old tool outputs was enough, and a method on the history handing its items to `ling-rs/masking`'s `drop_first`; off by default; compaction spec §13; 43,997 in all after it), the sandbox fix and the coming compaction cache patch;
- not raised on 2026-10-14 for `0027-compaction-same-tools` (675 bytes, 44,672 in all), the compaction cache patch the 45 KB allowed for: it changes 0020's request hook to `ling_tools::for_request`, which also takes the request's thread id and whether it is a compaction, so a local compaction offers the tools of its thread's last request and its prompt shares the server's cached prefix (`ling-rs/tools/src/compaction.rs`; before it, every compaction re-read 62–91K tokens, `docs/dev/turn-end-compaction/2026-10-13.md`). `DREAMFERENCE_MIGHTLING_COMPACTION_TOOLS=0` switches it off;
- not raised on 2026-10-19 for hiding `/import` (two lines in 0012's hunk, 163 bytes; 44,835 in all);
- not raised on 2026-10-20 for the exit summary's resume line, which said `codex resume <id>` (one hunk in 0001's `cli/src/main.rs` that rewrites `codex resume` to `ling resume` where `handle_app_exit` prints the lines, 117 bytes net: two of 0001's hunk headers lost their optional section names; 44,952 in all);
- to 46,849 bytes on 2026-10-20 for `0028-adapter-slash-command` (1,849 bytes; 46,801 in all);
- to 55 KB the same day by the owner, for 1.7.1 and ling-claude's five TUI hooks (about 3.8 KB): first used by 0019's sandbox hunk, which denies the host's control sockets to every sandboxed command (`ling_airgapped::host_sockets`, 377 bytes; 47,178 in all; [airgapped.md](airgapped.md));
- not raised again for ling-claude's five TUI hooks, `0029-compat-hooks` (3,464 bytes; 50,642 in all; spec `specs/DREAMFERENCE_MIGHTLING_LING_CLAUDE.md` §6.5.6): `Enter` in the composer, the double-press exit, `[`/`v` in the transcript overlay and `Shift+Tab` on the approval modes, each one call into `ling-rs/src/compat_keys.rs` that leaves Codex's behaviour as it is outside the mode. Its test, `compat_shift_tab_cycles_approval_modes_and_never_full_access`, is `codex-tests/patches/0004-tests-compat-shift-tab.patch`;
- to 58 KB on 2026-10-21 by the owner, for the vendor names (6,881 bytes; 57,523 in all): `/apps` in the slash-command popup (upstream's `CommandPopup::new` drops it whatever the connectors gate says) and the status card's account line in 0001, and `0030-neutral-vendor-names`, which rewords every user-reachable "ChatGPT"/"OpenAI" string the patches had left: the app page ("Manage in browser", "Install in browser", the sign-in and setup lines; APPS §3), the plugin install flow's app rows, the plugin catalog's "OpenAI Curated" (now "Curated"), the remote catalogs' sign-in hints, the model menu's base-URL warning and the `/hooks` screen's "Right before Codex ends its turn". Not reworded: strings on screens the patches hide (sign-in, feedback), `/usage`'s sign-in error (0011 replaced both callers) and internal errors. Tests: an overlay per changed snapshot, `codex-tests/patches/0004-tests-neutral-vendor-names.patch` for the plugin popup tests, and three inline tests in product files on the skip list with their reason. "Approve for me" (guardian approval, which reviews with the vendor's models) is not a patch: the launcher writes `features.guardian_approval = false` when absent;
- not raised on 2026-10-22 for `0031-read-only-network` (1,004 bytes: one dependency line and one line in `SandboxManager::transform` that gives the read-only sandbox the network below `/airgapped on`; [airgapped.md](airgapped.md)). Room was made by dropping the series' 59 `index` lines (1,770 bytes), which `git apply` does not need (`0024` never had them); 56,757 in all;
- not raised on 2026-10-26 for `0032-compat-guard-fails-closed` (467 bytes: a failed `PreToolUse` run of the compatibility mode's guard blocks the call instead of letting it through, for the one case the guard's own `||` fallback cannot answer, a guard killed at its timeout; [launcher.md](launcher.md), WINDOWS_ARM §23.4 W1); 57,280 in all;
- to 60 KB on 2026-10-26 by the owner, for the Windows fixes W1/W2/W9 (WINDOWS_ARM §23.4), with `0033-sandbox-window-station` (1,269 bytes: the Windows sandbox names its private desktop in the caller's real window station, read with `GetProcessWindowStation`, instead of `Winsta0\<desktop>`, which does not exist over SSH; a desktop named without a station would land a sandbox account's child in its own logon's station; the launcher's `src/winsta.rs` lets the sandbox into a service window station); 58,549 in all, the rest kept for later Windows fixes;
- not raised again on 2026-10-26 for `0034-doctor-windows` (947 bytes: on Windows `ling doctor` neither names Codex in its endpoint-protection check, which it skips there since the preview is unsigned, nor warns about a missing `rg.exe`, which the release does not ship and thread search does without; both only outside Codex's tests, through 0016's `MIGHTLING_DOCTOR_OFFLINE`); 59,496 in all;
- and to be raised again, explicitly, for each hook that needs it.

Not raised on 2026-10-22, when plugins were allowed at `/airgapped off`: the launcher and the bridge policy do it (`ling-rs/src/plugins.rs`), and the only patch change is 0030's empty Curated tab, which now says the vendor's catalogue is closed in Mightling (33 bytes; overlay `0004-tests-neutral-vendor-names`).

`0001` renames what is visible: session header, status card, `exec` banner, clap `name`/`bin_name`/usage (so `--version` says `ling`), subcommand usage lines and the exit summary's `ling resume <id>`. `0002` adds `ling-launcher = { path = "../ling" }` to the CLI's manifest (a path dependency inside the workspace root joins the workspace by itself) and replaces `MultitoolCli::parse()` with `ling_launcher::help::parse(ling_launcher::args().await?)`, which also rewrites "Codex" in every about/help string of the clap tree at parse time (`ling-rs/src/help.rs`) so no doc comment needs a patch; paths such as `~/.codex` and `$CODEX_HOME` are left alone. `0001` still carries `exec`'s hard-coded `override_usage`, since clap has no getter for it.

Everything else is done from `ling-rs/` instead of a patch: vendored OpenSSL is a target dependency in its `Cargo.toml` (Cargo unifies the feature across the build), the prompt's identity is renamed by `rebrand()` when the catalog is written (it used to be a ~390 KB diff of `models.json`), and the binary is not renamed in Cargo at all: Cargo builds `codex` and the builder installs it as `ling`. Regenerate patches with `git diff -U1` in a scratch export, and drop the `index` lines.

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
