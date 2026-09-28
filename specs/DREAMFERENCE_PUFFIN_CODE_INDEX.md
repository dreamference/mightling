# Puffin Code Index — codebase-memory-mcp + SCIP

**Status:** proposed. Nothing in this spec is implemented yet.
**Target:** the `puffin` terminal agent. The same index is also offered over MCP to Claude Code and to IDEs.
**Builds on:** the launcher in `puffin-rs/`. It handles `puffin app` and `puffin update` before Codex parses argv, and `puffin code` would be handled the same way. It also builds on the `puffin-admin search` / `gmail` pattern of giving the local model shell commands rather than MCP tools, and on the builder's rule that nothing ever runs Cargo inside the `codex/` submodule.

---

## 1. Goal

Give the agent fast, token-cheap and *trustworthy* answers about code in any language:
- "where is X defined";
- "who calls X";
- "what implements this trait";
- "what breaks if I change this signature";
- "find code about token refresh".

Today it answers these with `rg` and file reads, one round at a time. On the 4,800-file Codex workspace that is slow and expensive, and it silently misses calls through traits, generics and re-exports.

The index has two layers:

- **codebase-memory-mcp** is the universal layer. It covers every language and every file, stays current as files change, and adds search by meaning. Its references are approximate but good.
- **SCIP** is the exact layer. It covers buildable projects in languages that have a compiler-based indexer, and gives compiler-exact definitions and references, as a snapshot.

A router in `puffin-rs` answers each question from the better layer. It tags every result `exact` or `heuristic`, and says what it could not resolve.

**Non-goals:**
- Editing code: the index answers questions, and the agent edits.
- Replacing `rg`/`ast-grep`: they stay the fallback and the verification step.
- Documents: PDF, Office and Markdown are indexed by a separate knowledge-layer spec.
- Anything that needs a network at query time.

## 2. Components

| Component | Version checked | Licence | Why this one |
|---|---|---|---|
| [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | v0.11.0 (2026-09-15), `codebase-memory-mcp-linux-arm64.tar.gz`, 45k★ | MIT | One C binary. 158 tree-sitter grammars and the `nomic-embed-code` model are compiled in. Its "Hybrid LSP" type resolution covers 13 languages, including Rust (traits, UFCS) and Python. It has a call graph, `trace_path`, `detect_changes` (blast radius) and search by meaning. Its README states "makes no network request of its own accord" and "collects no telemetry". It has 17 MCP tools, each also runnable as a one-shot CLI (`cli <tool> --format json`). |
| [SCIP protocol](https://github.com/scip-code/scip) and the `scip` Rust crate | crate 0.10.0 (2026-09-03) | Apache-2.0 | The index format rust-analyzer writes. The crate reads it natively in `puffin-rs`. |
| `rust-analyzer scip` | from the pinned toolchain (`rustup component add rust-analyzer`) | MIT/Apache-2.0 | Exact Rust definitions and references, including trait dispatch and macros. |
| `scip-python` (Pyright), `scip-typescript`, `scip-clang`, `scip-java`, `scip-go`, `scip-dotnet` | pinned per release | **verify each before bundling** | Exact references for the other major languages. Only languages whose project builds get this layer. |

Rejected, with the reason:
- jCodeMunch: its licence forbids renaming, modified redistribution and commercial use.
- GitNexus: PolyForm Noncommercial.
- codegraph: MIT, but has telemetry on by default and no search by meaning.
- Serena: GPL-3.0, heavier, and has no graph or search by meaning. Its MIT SolidLSP layer remains an option if a live language server is ever needed.
- The full survey is in the session record of 2026-09-28.

## 3. Architecture

```
            agent (puffin, local model)          Claude Code / IDE
                      │ shell                          │ MCP (stdio)
                      ▼                                ▼
        puffin code <cmd>  ─────────────  puffin code mcp
                      │   (same router, puffin-rs/src/code_index/)
          ┌───────────┴───────────────┐
          ▼                           ▼
  codebase-memory-mcp            SCIP reader (`scip` crate)
  (one-shot CLI, JSON)           .dreamference/scip/*.scip + manifest
  graph, BM25, semantic,         exact defs/refs for files unchanged
  live via its watcher           since the indexed commit
```

- **Both layers stay unmodified.** codebase-memory-mcp is invoked as a pinned binary. SCIP files are produced by the upstream indexers. The only new code is the router and the SCIP scheduler.
- **No new long-lived process for the agent.** `puffin code …` runs codebase-memory's one-shot CLI, which by its README "leaves no standing process behind", and reads SCIP files directly. codebase-memory's own background watcher, which keeps its graph current, is the one resident piece. It is allowed, but it must be switchable off (§8).
- **Why Rust, in the launcher:** it keeps `puffin` self-contained. `puffin code` works in any shell with no Python environment, like `puffin app` and `puffin update`. The `scip` crate is the reference reader.

## 4. Distribution

- **Pinned binary, verified:**
  - The Puffin release workflow downloads the pinned codebase-memory-mcp release for `linux-arm64` and checks it against the release's `checksums.txt`.
  - The expected SHA-256 is committed in `puffin-rs/codebase-memory.sha256`, the same trust model as the rusty_v8 archive.
  - It is shipped beside `puffin` in `~/.local/share/dreamference/puffin/bin/` and attached to Puffin releases, so `puffin update` refreshes it.
  - `puffin-admin codex build` fetches the same pinned archive for local builds.
- **Fork only when a patch is needed.** No change to codebase-memory is required for this spec. If one becomes necessary, for example §10's SCIP import, fork it to `dgxcoder/codebase-memory-mcp` as a submodule and build from source (`build/c/codebase-memory-mcp`), following the Codex pattern: pinned tag, patches in a directory, submodule never edited. The MIT notice must stay in the shipped files.
- **SCIP indexers:**
  - `rust-analyzer` comes from rustup, the same toolchain as the Codex build.
  - The others are fetched at *install* time by `puffin-admin code setup`, never at query time, and pinned by version and checksum.
  - A machine that cannot fetch them still gets the universal layer.

## 5. The SCIP layer

### 5.1 Which indexers run

Detection is by project files at the repository root and in immediate subdirectories. A monorepo can get several.

| Found | Indexer | Command (run under §8's limits) |
|---|---|---|
| `Cargo.toml` (workspace or crate) | rust-analyzer | `rust-analyzer scip <dir> --output <out>` |
| `pyproject.toml` / `setup.py` / `requirements.txt` | scip-python | `scip-python index --project-name <p> --output <out>` |
| `tsconfig.json` / `jsconfig.json` / `package.json` | scip-typescript | `scip-typescript index --output <out>` |
| `compile_commands.json` | scip-clang | `scip-clang --compdb-path=compile_commands.json` |
| `build.gradle*` / `pom.xml` / `build.sbt` | scip-java | `scip-java index` |
| `go.mod` | scip-go | `scip-go` |
| `*.sln` / `*.csproj` | scip-dotnet | `scip-dotnet index` |

A project that fails to build or index keeps the universal layer only. The failure is recorded in the manifest, not retried in a loop.

### 5.2 Where output goes

- **Files:** `<repo>/.dreamference/scip/<indexer>.scip`, plus `manifest.json`: `{indexer, version, commit, dirty_files, file_hashes, started, duration_s, peak_rss_mb, status}`. `.dreamference/` is already git-ignored.
- **Never index inside a submodule's checkout with a build tool that writes to it.** rust-analyzer runs `cargo metadata` and build scripts, and even `cargo tree` rewrites a submodule's `Cargo.lock`. This project's `codex/` workspace is indexed from the builder's exported copy (`~/.cache/dreamference/puffin-codex/src/codex-rs`), and paths are remapped back to `codex/codex-rs/…`. In general, a submodule is indexed from a scratch export (`git archive`), never in place.
- **`CARGO_TARGET_DIR` points at a scratch directory** so indexing never touches a build cache that another build is using.

### 5.3 When it runs

- **Automatic triggers:** in the background, never blocking a session:
  - at `puffin` start when there is no index, or the index is older than `HEAD` by more than N commits;
  - after a commit;
  - when idle.
- **On demand:** `puffin code index --exact`.
- **Before a risky operation:** when the router answers `references`/`impact` for a symbol whose files changed since the snapshot (§6.3).
- **Serialised** per repository with a lock file, like the builder's `flock`, so two sessions do not index at once.

### 5.4 Cost on this machine

Being measured: first run of `rust-analyzer scip` on the Codex workspace, 8 GB memory cap, 2026-09-28.
- A first attempt spent over two minutes compiling the workspace's 560 build scripts and proc-macros before indexing began.
- That output is cached in the scratch target directory and reused on later runs.
- Record the full wall time, peak memory and output size here once the run completes. Until then treat SCIP on Codex as a multi-minute background job.

## 6. The router

### 6.1 Operations

| `puffin code …` | Answered by | Notes |
|---|---|---|
| `search <text>` | codebase-memory `search_graph`, BM25 plus semantic | Names, meaning and text. Always the universal layer. |
| `outline <file>` | codebase-memory `get_file_outline` | |
| `show <symbol>` | codebase-memory `get_code_snippet` | Returns one symbol's source, not the whole file. |
| `def <symbol>` | SCIP if fresh (§6.3), else codebase-memory | |
| `refs <symbol>` | SCIP for fresh files, codebase-memory for stale ones, merged | Tagged per row. |
| `callers` / `callees <symbol>` | codebase-memory `trace_path` (depth 1–5), with SCIP-exact rows substituted where available | |
| `impl <trait-or-interface>` | SCIP (implementation relationships), else codebase-memory `IMPLEMENTS` edges | |
| `impact <symbol-or-diff>` | codebase-memory `detect_changes`, cross-checked with SCIP refs | Answers "what breaks". |
| `status` | both | Layers present, freshness, languages covered, last index time. |
| `index [--exact]` | both | Re-index. `--exact` also schedules SCIP. |

### 6.2 Output

Output is plain text, compact, one row per result, which is the shape the local model reads best:

```
refs codex_core::config::Config::load   (12 results; 10 exact, 2 heuristic)
exact     codex-rs/cli/src/main.rs:1041        SCIP rust-analyzer @ 47f4d81
exact     codex-rs/tui/src/app.rs:318          SCIP rust-analyzer @ 47f4d81
heuristic codex-rs/exec/src/lib.rs:77          codebase-memory (file edited since 47f4d81)
unresolved 1 call through `dyn ConfigSource` in codex-rs/core/src/lib.rs:2204
```

`--json` gives the same data for tools. The `unresolved` line is mandatory whenever either layer reports calls it could not resolve. It is what tells the agent it has to verify.

### 6.3 Freshness and merging

- **A file is fresh for SCIP** when its current content hash equals `file_hashes[path]` in the manifest.
- **`refs` and `def`** use SCIP rows for fresh files and codebase-memory rows for stale or unindexed files.
- **Rows present in both** are de-duplicated by `(path, line)`. The SCIP row wins and is tagged `exact`.
- **Stale share:** if more than 20% of the files a result touches are stale, the router schedules a background SCIP run and says so in the output header.

### 6.4 Symbol names

The agent types names the way it sees them in code: `Config::load`, `load`, `dreamference.runner.codex_runner.CodexRunner.run_session`. The router resolves the name through codebase-memory's `search_graph` first. If there are several candidates, it lists them and asks for a qualified name instead of guessing.

## 7. Agent interface

- **Prompt:** the launcher appends a `# Code navigation` block to the model's prompt, next to web access and Gmail. The block covers:
  - the commands in §6.1;
  - the meaning of `exact`/`heuristic`/`unresolved`;
  - one rule: *before changing a signature, renaming or deleting, run `puffin code refs`. If any row is `heuristic` or `unresolved`, confirm with `rg` and run the build or tests after the edit.*
- **Omitted when unusable:** the block is left out when `puffin code status` reports no index for the working directory, so the model is never told about a command that cannot answer.
- **MCP:** `puffin code mcp` serves the same operations over stdio for Claude Code and IDEs. It replaces jCodeMunch in `~/.claude.json` once this is implemented. The local model keeps using shell commands, because it does not reliably call MCP tools under Codex's Code Mode (see `codex_runner.py`'s history).

## 8. Resources and safety

On GB10, host RAM and GPU memory are the same memory, and running it out can freeze the host, not just kill a process (see `psi_watchdog.py`).

- **Memory limits:**
  - SCIP indexing always runs under `systemd-run --user --scope -p MemoryMax=<limit> -p MemorySwapMax=0`, so exhaustion OOM-kills the indexer instead of stalling the host. The default limit is 8 GB, configurable as `code_index_memory_max`.
  - codebase-memory indexing runs under the same kind of limit, with a separate setting. Its pipeline is RAM-first, per its README.
- **Low priority:** indexers run at `nice 10` and `ionice -c3`, and never while a vLLM load is in progress. The router checks for the vLLM container starting and defers.
- **Watcher:** codebase-memory's watcher is on by default, and `puffin_code_watch = false` turns it off. That maps to its `watcher_enabled` / `auto_watch` settings.
- **Air-gapped:** no component makes network requests at query or index time. The rusty_v8-style checksum pins cover everything downloaded at install.
- **Scope:** only the current repository is indexed, or explicitly listed ones. `~`, `/` and anything over `code_index_max_files` are never indexed (default 50,000, codebase-memory's `auto_index_limit`).

## 9. Evaluation (before building the router)

The bake-off decides whether codebase-memory is good enough as the universal layer, and how much SCIP adds.

1. **Question set:** 15–20 real questions about this repository. At least 8 are about `codex/codex-rs` Rust (trait methods, re-exports, macro-generated code, `dyn` dispatch) and at least 5 about `dreamference/` Python.
2. **Ground truth:** SCIP from `rust-analyzer` and `scip-python`, spot-checked by hand for the `unresolved` cases.
3. **Contenders:**
   - codebase-memory-mcp;
   - `rg`/`ast-grep` as the baseline;
   - optionally octocode with `--with-lsp=rust-analyzer`, and codegraph.
4. **Metrics:**
   - reference recall and precision against ground truth;
   - wall time per question;
   - output tokens per answer;
   - index time and peak memory on the Codex workspace.
5. **Acceptance:**
   - codebase-memory reaches at least 90% recall on Rust references and 95% on Python;
   - its answers cost fewer tokens than the `rg` baseline;
   - it indexes this repository within the §8 limits.

   If Rust recall misses the target, SCIP becomes mandatory for Rust `refs`/`impact`, not an optimisation, and §10's SCIP import moves up.

## 10. Later: SCIP inside the graph

If §9 shows that codebase-memory's approximate edges are wrong in concentrated places, the better long-term design is to fork it and add SCIP import. Exact SCIP edges would replace its `CALLS`/`RESOLVED_CALLS` edges for indexed files, so `trace_path` and `detect_changes` become exact too, inside one graph. It is C, and it is a larger change, so it is deferred until the router has shown where it matters.

## 11. Tests

- **Router unit tests** (`puffin-rs`, `cargo test -p puffin-launcher` in the export):
  - merging with fresh, stale and missing files;
  - de-duplication;
  - `unresolved` always printed when reported;
  - ambiguous names listed, not guessed;
  - stale threshold triggers a re-index request.
- **Fixture repository** with a tiny Rust crate and Python package with known references, committed under `tests/fixtures/code_index/`. Its tests check exact rows against SCIP and heuristic rows against codebase-memory. They are skipped when the binaries are absent.
- **Safety:**
  - indexing a submodule never writes to the submodule (`git status --porcelain` empty afterwards);
  - SCIP runs are killed at the memory cap, not left to exhaust the host (fixture with a tiny cap);
  - no network access during `index` or queries (network namespace test).
- **Prompt:** the `# Code navigation` block appears only when `status` reports a usable index.

## 12. Open questions and unverified claims

- **codebase-memory's figures are its own:** the "Hybrid LSP" accuracy ("target ~95% resolution on idiomatic code") and the performance figures (Linux kernel in 3 min on an M3 Pro). §9 measures them here.
- **Its CLI output shape** for `trace_path`/`detect_changes` in `--format json` has to be pinned in the router against v0.11.0. A version bump could change it, so the router's parser is tested against recorded fixtures.
- **SCIP freshness for rust-analyzer is whole-workspace.** Per-crate re-indexing would make the exact layer much cheaper after small edits. Whether rust-analyzer's `scip` command can be scoped to a crate is not verified.
- **Licences of the non-Rust SCIP indexers** must each be checked before they are bundled.
- **Several repositories:** whether `puffin code` indexes several repositories per session, or only the working directory's, is left to implementation. Start with the working directory only.
