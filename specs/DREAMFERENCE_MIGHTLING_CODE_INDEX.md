# Mightling Code Index — codebase-memory-mcp + SCIP

**Status:** implemented on 2026-10-01 in `ling-code-rs/`, with the differences and the parts not built listed in §14; the rest of this document is the design as specified. The design below was revised on 2026-09-30 after measuring both layers on this machine (§2): several assumptions of the first draft did not survive contact with the tools. Revised again the same day: freshness is now decided for the whole repository, not only for the files an answer already names (§7.3), the router is a binary of its own, `ling-code`, not a subcommand compiled into `ling` (§4.2), and memory admission covers every indexer run on the host, not each executing run on its own (§6.4), and on 2026-10-01 which submodules are indexed (§4.3).
**Target:** the `ling` terminal agent. The same index is also offered over MCP to Claude Code and to IDEs.
**Builds on:** the `ling-search` / `ling-admin gmail` pattern of giving the local model shell commands rather than MCP tools, and of putting each such command on `PATH` as a program of its own beside `ling`. `ling-code` is a separate Rust binary (§4.2); the launcher in `ling-rs/` only starts it and asks it for the prompt block. It also builds on the builder's rule that nothing ever runs Cargo inside the `codex/` submodule.

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

- **codebase-memory-mcp** is the universal layer. It covers every language and every file, stays current cheaply, and answers names, outlines, text search and approximate call edges. Measured here, its call edges find **about 80% of the files that reference a Rust function and 58% for Python (52% for methods)** (§2). It is a fast, broad first answer, never the last word on "who calls this".
- **SCIP** is the exact layer: compiler-exact definitions and references, as a snapshot. For Python and TypeScript it runs no project code and is cheap, so it runs **for every repository by default**. For Rust, Java and .NET it executes the project's build logic and needs about 21 GiB on Codex, so it runs only for trusted repositories and only when memory admits it.

A router, the `ling-code` binary, answers each question from the better layer. It tags every result `exact` or `heuristic`, and says what it could not resolve or did not index.

**Non-goals:**
- Editing code: the index answers questions, and the agent edits.
- Replacing `rg`/`ast-grep`: they stay the fallback and the verification step.
- Documents: PDF, Office and Markdown are indexed by a separate knowledge-layer spec.
- Anything that needs a network at query time.

## 2. What was measured (2026-09-30)

On this GB10, with the default model (Qwen3.8-27B on SGLang) resident and idle, about 35 GiB of host memory available. Every number here is from a run, not from a README. "Derived" rows follow from measured ones; "unmeasured" rows are open (§13).

| Quantity | Value | Kind |
|---|---|---|
| codebase-memory CLI, any command, incl. `list_projects` | **6.6 s** per invocation (fixed start-up: each call spawns an internal daemon, `--cbm-daemon-internal`, which exits afterwards) | measured |
| codebase-memory CLI with another server already running | 2.5–3.3 s | measured |
| codebase-memory CLI with `CBM_SEMANTIC_ENABLED=0` (and `CBM_LSP_DISABLED=1`) | still 6.6 s: the start-up is not the embedding model | measured |
| codebase-memory as a long-lived MCP (stdio) process | 6.4 s once, then **12–105 ms** per query (`search_graph`, `trace_path`, `get_file_outline`) | measured |
| Direct read-only SQL on its graph database (`nodes`/`edges`) | **< 10 ms** for "callers of X", with call-site lines | measured |
| Its network use during a query | none beyond its own Unix socket and a `git rev-parse` (strace on `connect`/`execve`) | measured |
| Its line numbers | **1-based** (a definition at `grep -n` line 793 is reported as 793) | measured |
| Index of this repository **with** the `codex/` submodule | 7,096 files (6,879 of them in `codex/`), ~45 s, ~450 MB peak, **665 MB** database | measured 2026-09-29 |
| Index of this repository **without** the submodule | 236 files, **9.4 s** (6.6 s of it start-up), 142 MiB peak, **21 MB** database | measured |
| Incremental re-index after a one-line edit, and a no-change re-index | 8.9 s each (start-up dominated) | measured |
| Its call-edge recall vs rust-analyzer SCIP, Codex (15,660 functions with cross-file references, file level, indexed files only) | **78%** (`CALLS`+`USAGE`); 80% counting `IMPORTS` | measured |
| Its call-edge recall vs scip-python, `dreamference/` (154 functions, file level, re-exports in `__init__.py` excluded) | **58%**; methods 52%, functions 86% | measured |
| Its coverage gaps | it applies the **superproject's** `.gitignore` inside a submodule (git does not): this repository's `config/` rule silently dropped `codex/codex-rs/core/src/config/` (a 4,926-line module) and our own tracked `dreamference/config/`. 3,683 Rust reference files fell outside its coverage | measured |
| `rust-analyzer scip` (1.95.0) on Codex, full, cold (build scripts compiled) | **completes under a 22 GiB cap: 691 s wall, peak ≥ 21.3 GiB**; 355 MB `.scip`, 4,687 documents, 3.53 M occurrences | measured; the peak is cap-bounded (both runs ended within 1 GiB of the cap, and cgroup `memory.peak` includes page cache), so the unconstrained peak is unknown |
| Same, warm (build-script cache reused) | 569 s, peak ≥ 21.5 GiB | measured, cap-bounded |
| Host memory available, lowest during those runs | 10.1 GiB (earlyoom's SIGTERM line is ~6.2 GiB, 5% of 121.6 GiB) | measured |
| A `no-macros` mode (`--config-path` disabling build scripts and proc-macros) | **does not exist**: scip mode hard-codes `load_out_dirs_from_check`, the proc-macro server and cache priming (verified in `cli/scip.rs`); the config is accepted and ignored | verified in source and by run |
| Decoding the 355 MB Codex `.scip` whole (`scip stats`) | 2.0 s, 2.1 GB | measured |
| `scip expt-convert` (scip CLI v0.10.0) on it | 9.8 s, 2.7 GB peak, **186 MB** SQLite; symbol→references lookup **2 ms** | measured |
| scip-python 0.6.6 on `dreamference/` (81 files) | **19 s, 2.1 GiB peak, 2.7 MB**; resolves `vllm_mgr.start_server(…)` to `VLLMServerManager.start_server` and not to `DiffusionServerManager.start_server` (codebase-memory found **no** caller of either) | measured |
| What scip-python executes | by default `pip3 list` and `python3` from `PATH` (here the repository's own `.venv`), and even a `node` found there; with `--environment <file>` and a `PATH` without the repository, only the system `python3 -c "import os…"` to read `sys.path`, with identical references | measured (strace on `execve`) |
| A snapshot replayed against a later tree: the scip-python store of `dreamference/` at `706c511`, queried at `8c2f9a3` (5 commits later, 23 Python files changed, 4 of them deleted) | `ModelDownloader`: the store names 5 files, 2 of them edited since. **2 more files reference it now** (`diffusion_server_manager.py`, `sglang_launch_builder.py`, both edited after the snapshot) and appear in no row; a file created after the snapshot (`runner/vllm_readiness_waiter.py`) references `VLLMServerManager`, `DreamferenceConfig` and `resolve_model_hf_repo` and appears in none of their answers. A freshness check that visits only the files an answer names never looks at any of them | measured |
| Finding those files | `git diff --name-only <snapshot> HEAD` plus `git status --porcelain`: **39 ms**; a whole-word search for the name over the 19 changed files still present: 3 ms, and it finds both missing files; the same search over all 4,900 Rust files of Codex: 21 ms (warm page cache) | measured |
| git inside Codex's sandbox | `git diff --name-only` and `git status --porcelain` run under `ling sandbox`; `.git` is read-only there (`touch .git/x` fails), so the router passes `--no-optional-locks` and never refreshes git's index | measured |
| One limit for all index runs | a scope started with `systemd-run --user --scope --slice=mightling-index.slice` lands in `…/user@1000.service/ling.slice/mightling-index.slice/`; `systemctl --user set-property --runtime <slice> MemoryMax=… CPUQuota=400%` takes effect at once (`memory.max` and `cpu.max` of the slice read back the new values), systemd 255. `choom -n 1000 -- <cmd>` raises `oom_score_adj` without privileges | measured (with a throwaway slice) |
| This repository's submodules, as §4.3's tests see them | `fano`: URL `github.com/dgxcoder/fano`, same namespace as the superproject's `origin` (`github.com/dgxcoder/dgxcoder`); **212 of its 216 commits** (and all 200 of the most recent non-merge ones the test reads) authored by `dgxcoder@dreamference.ai`, the superproject's only author (the other 4 by an address the superproject has never used); 252 tracked files (151 PDF, 63 TeX, 5 Python). `codex`: URL `github.com/dgxcoder/codex`, **also the same namespace**; a shallow clone (`shallow = true`) of 1 parentless (grafted) commit, detached at upstream's tag `rust-v0.158.0`, authored by `imac@openai.com`; 8,670 tracked files | measured |
| Cost of §4.3's tests, offline | resolving a submodule URL through `insteadOf` (`git ls-remote --get-url`): 3 ms, no network; the superproject's author set (1,000 commits) plus 200 commits of each submodule: 13 ms for both submodules | measured |
| `defn_enclosing_ranges` in the store | maps a reference to its innermost enclosing definition (line 2007 of the CLI controller → `DreamferenceCLIController#run_cli()`), 0-based lines | measured |
| The indexing sandbox, probed with a crate whose `build.rs` reports what it can reach | with `$CARGO_HOME` bound read-write (the first draft's command): it can open `~/.cargo/bin/cargo` for writing and create files in `$CARGO_HOME`, and it sees `~/.ssh`, `~/.mightling/config.toml` and `~/.config/dreamference`. With the home directory a tmpfs and `$RUSTUP_HOME`, `$CARGO_HOME` and the source bound read-only: none of those, and `cargo check --offline` (0.4 s, one registry dependency) and `rust-analyzer scip` (1.95.0, 3.1 s, build script run, `.scip` written to the scratch directory) both still succeed | measured |
| SGLang's idle gauges | `sglang:num_running_reqs`, `sglang:num_queue_reqs` (0 when idle), on `/metrics` of the served model | measured |
| scip-typescript 0.4.0 (npm), run as §6.1 says, 2026-10-01 | Codex's generated protocol schema (732 `.ts` files, a `package.json` and no tsconfig, so the inferred configuration): **~2 s, peak 271 MiB**; Codex's TypeScript SDK (24 files, its own tsconfig): ~2 s, 181 MiB; `refs AbsolutePathBuf` gives 101 exact rows in 48 files, the same 48 files `rg -lw` finds, in 40 ms. It starts child processes only for `--pnpm-workspaces`/`--yarn-workspaces` (`pnpm`, `yarn`), which Mightling never passes; `--infer-tsconfig` writes `tsconfig.json` **into the project**, so Mightling writes its inferred configuration into scratch instead | measured |
| scip-go 0.2.7 (`scip-go-linux-arm64.tar.gz`, sha256 checked against the release's `.sha256`), on Go 1.27.1 for linux-arm64 in a scratch directory (this machine has no Go) | a three-package fixture module: indexed in the sandbox with `GOPROXY=off GOTOOLCHAIN=local GOFLAGS=-mod=readonly CGO_ENABLED=0`, **peak 21 MiB**, the module untouched; `refs`, `callers` and `impl` exact. With a `require` of a module in no cache it still succeeds, leaving that dependency unresolved, and reaches for no network; a `go.mod` saying `go 1.99` fails with `go.mod requires go >= 1.99 (running go 1.27.1; GOTOOLCHAIN=local)` instead of downloading that toolchain | measured |
| scip-java 0.13.1 (`scip-java-v0.13.1`, one 82 MB file, sha256 checked against the release's) | a shell launcher with 74 jars embedded: it loads with no network and an empty home directory, so nothing is fetched at index time. Its classes are Java 17 bytecode (class file version 61): this machine's only Java, an OpenJDK 8 runtime without `javac`, fails with `UnsupportedClassVersionError`. No JDK 17 is installed here; the index runs of 2026-10-02 used one unpacked into scratch (§14.1) | measured |
| scip-dotnet 0.2.14 (`scip-dotnet.0.2.14.nupkg` from nuget.org; its SHA-512 equals the catalog's `packageHash`) | framework-dependent builds for net6.0–net10.0 with the `any` runtime identifier: nothing architecture-specific, so arm64 is not the obstacle. It needs a .NET SDK, which this machine does not have; the index runs of 2026-10-02 used SDK 8.0.425 installed into scratch (§14.1) | measured |
| scip-clang 0.4.0 | release assets `scip-clang-x86_64-linux`, `scip-clang-dev-x86_64-linux` and `scip-clang-arm64-darwin` only: **no linux-arm64 build** | verified |
| Licences | codebase-memory-mcp MIT; scip CLI, scip-typescript, scip-clang, scip-java, scip-go, scip-dotnet Apache-2.0; scip-python MIT (Pyright's); all maintained (pushed within the last month) | verified |
| Decode rate while indexing, frozen/unfrozen scopes, codebase-memory's search by meaning quality | — | **unmeasured** (§10, §13) |

Consequences for the design, each carried into the sections below:

1. **No per-query CLI.** At 6.6 s a call, the CLI cannot serve queries. The router reads codebase-memory's database directly (§7.5), with a fallback ladder.
2. **Exact layer by language, not by trust alone.** Static indexers (scip-python, scip-typescript) run for every repository; executing ones (rust-analyzer, scip-java, scip-dotnet) need trust and admission.
3. **A submodule is indexed only if it is your own code or you ask for it** (§4.3). 97% of this repository's universal index was the `codex/` submodule, a fork of a third-party project that sits under the same GitHub organisation, so ownership is decided by namespace *and* authorship, not by the URL alone.
4. **Admission from measured numbers:** the Codex exact index needs more than 21 GiB and runs only when the machine can spare it.
5. **The query store is the `scip` CLI's own SQLite export**, pinned and fingerprinted, with our converter as the fallback.
6. **A snapshot cannot say what it is missing.** A file that gained a reference after the snapshot is in no row, so checking the rows' files cannot find it. Every query therefore computes the files changed since each layer's snapshot and searches them for the name (§7.3).
7. **Memory is one pool, so admission is one ledger.** Every index run on the host, of every kind and from every session, is admitted against the same budget and runs inside one slice whose limits the kernel enforces (§6.4).

## 3. Components

| Component | Version pinned | Licence | Role |
|---|---|---|---|
| [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | v0.11.0 (2026-09-15), `linux-arm64` | MIT | Universal layer: builds the graph (158 tree-sitter grammars, "Hybrid LSP" resolution for 13 languages), keeps it incrementally, and serves search by meaning when a session asks for it. Configured through `CBM_CACHE_DIR`, `CBM_RUNTIME_DIR`, `CBM_MEM_BUDGET_MB` (a hard budget since v0.11.0), `CBM_WORKERS`, `CBM_ALLOWED_ROOT`, `CBM_SEMANTIC_ENABLED`, and a per-repository `.cbmignore`. |
| [scip CLI](https://github.com/scip-code/scip) | v0.10.0 (2026-09-03), `scip-linux-arm64.tar.gz`, sha256 `6ab677dc2c4bf2955975d0530766152e45daaa988f9404068d8adecacd0bb24c` | Apache-2.0 | `expt-convert` builds the query store (§7.5). |
| `scip` Rust crate | 0.10.0 | Apache-2.0 | Decodes the occurrence blobs of the store in `ling-code`. |
| `rust-analyzer scip` | from the pinned toolchain (1.95.0) | MIT/Apache-2.0 | Exact Rust layer. Executes build scripts and proc-macros (§9.1). |
| scip-python | 0.6.6 (npm `@sourcegraph/scip-python`) | MIT | Exact Python layer. Static (Pyright). |
| scip-typescript | 0.4.0 (npm `@sourcegraph/scip-typescript`, beside scip-python in the same lockfile) | Apache-2.0 | Exact TypeScript/JavaScript layer. Static. Runs on the recorded Node.js. |
| scip-go | 0.2.7, `scip-go-linux-arm64.tar.gz`, sha256 `6b93476c7578c5aeb5acacb41f8234c20130168271adea0db8d8ae63d1355acb` | Apache-2.0 | Exact Go layer. Static, offline by environment (§6.1). Runs on a Go toolchain recorded at setup. |
| scip-java | 0.13.1, `scip-java-v0.13.1` (self-contained launcher), sha256 `a694cae143c32c5b6226362fb4bd268a8d13d3cd9b482819b3b0029a9a97b8fe` | Apache-2.0 | Exact JVM layer for Gradle and Maven projects. Executes the build (§9.1). Needs a JDK 17 or newer, recorded at setup. |
| scip-dotnet | 0.2.14, `scip-dotnet.0.2.14.nupkg` from nuget.org, sha256 `e2d183fe39b9a56cb8bb2ed2d8b96828fb5434c6db084002bf8a5c6009391b52` | Apache-2.0 | Exact .NET layer (C#, Visual Basic). Executes MSBuild (§9.1). Needs a .NET SDK 8 or newer, recorded at setup. |
| scip-clang | not pinned: upstream ships no linux-arm64 build (§2) | Apache-2.0 | Exact C/C++ layer where a binary exists; on Mightling's platform C and C++ stay on the universal layer, and `ling-code status` says why. |

Rejected, with the reason:
- jCodeMunch: its licence forbids renaming, modified redistribution and commercial use.
- GitNexus: PolyForm Noncommercial.
- codegraph: MIT, but has telemetry on by default and no search by meaning.
- Serena: GPL-3.0, heavier, and has no graph or search by meaning. Its MIT SolidLSP layer remains an option if a live language server is ever needed; note that a live rust-analyzer on a large workspace has been reported at 40+ GB (serena#1556), which is why this design uses rust-analyzer only as a batch indexer under a cap.
- The `dreamference` context engine (TF-IDF + FTS5 + file embeddings): kept until the router passes §10, then retired (§8).
- The full survey is in the session record of 2026-09-28.

## 4. Architecture

```
            agent (ling, local model)          Claude Code / IDE
                      │ shell                          │ MCP (stdio)
                      ▼                                ▼
        ling-code <cmd>  ─────────────  ling-code mcp
                      │   (one binary, crate ling-code-rs/)
          ┌───────────┴─────────────────────────┐
          ▼                                     ▼
  codebase-memory graph (SQLite)          SCIP query store (SQLite)
  read directly, read-only (§7.5)         .dreamference/scip/index.db
  written only by its indexer, run        built by `scip expt-convert`
  detached at launch / on demand          after each exact run (§6)
```

- **Both tools stay unmodified.** codebase-memory is a pinned binary that writes its graph; the scip CLI and the indexers are pinned binaries that write SCIP. The new code is the router, the scheduler and the store's small post-processing (§7.5).
- **Queries never start a tool.** A `ling-code refs` opens two SQLite files read-only, runs indexed lookups and exits: milliseconds, and no process survives it. The tools run only to *index*, started by the session process outside the sandbox (below), under §9's limits.
- **Two sides of the sandbox.** The agent runs `ling-code …` as a shell command, and Codex runs every such command inside its bwrap sandbox (`workspace-write` or `read-only`): the process can read the two databases and write only under the working directory and `/tmp`. Probed with `ling sandbox` in `workspace-write` mode: reading the graph database works; `~/.cache` is a read-only file system; `$XDG_RUNTIME_DIR` is visible but read-only; `systemd-run --user` fails ("Failed to connect to bus"); and nothing it starts outlives it. So the design splits:
  - **Queries are pure readers.** `ling-code refs|def|callers|…` opens the databases read-only and exits. That is all it may do.
  - **Everything that starts an indexer belongs to `ling-code session`**, a process the launcher starts at `ling` launch, *outside* the sandbox, for as long as that `ling` lives (§4.2). It owns indexing: the launch-time runs, the supervisor of §9.2, and a request queue. A query that wants a re-index appends one line to `<repo>/.dreamference/code_index.requests` when it can write there; the session process drains it every few seconds, coalesces (§9.2) and runs what admission allows. A line is a fixed word (`index` or `index-exact`) and nothing else: the file is written from inside the sandbox, so the session process treats it as untrusted and ignores any other content. In a `read-only` sandbox the query cannot write the request, so it only says in its header that the index is stale; the next launch refreshes it.
  - **`ling-code index` typed by the user** in an ordinary shell is outside any sandbox and runs the index itself. Typed by the agent, it is inside the sandbox, cannot create a scope, and queues a request instead; it tells the two apart by whether the user's systemd bus answers.
  - **`ling-code mcp`** (for Claude Code and IDEs) runs outside any sandbox and may do both.
- **The fallback ladder**, used only when the direct read is impossible (the graph's schema fingerprint does not match the pinned one, §7.5): the CLI, correct at 6.6 s per call. A session-scoped codebase-memory child (12–105 ms per query) is reachable only from `ling-code mcp`, which can hold it; from inside the sandbox there is no channel to it.
- **Search by meaning** needs the embedding model compiled into codebase-memory, so it is served only by `ling-code mcp`'s session child, and only when `mightling_code_semantic = true`. It is off by default: it returned unrelated build files for plain questions on this repository (2026-09-29), and upstream has open issues on it (#1155, #1462). `search` without it is FTS5 over names, qualified names and bodies, ranked by BM25 (§7.1).
- **Why Rust:** `ling-code` works in any shell with no Python environment, and the model's shell has none on `PATH` (the reason `ling-admin` had to be linked there). rusqlite plus the `scip` crate are all it needs.

### 4.1 Lifecycle of the universal layer

- **First run:**
  - at `ling` launch, the session process (`ling-code session`, §4.2) checks for the repository's project in codebase-memory's cache (by reading the `projects` table directly, not through the CLI);
  - if there is none, it starts `index_repository` **detached**, under §9's limits, and continues launching. It never indexes in the agent's path.
- **Prompt timing:** the prompt is assembled once, at launch:
  - **Index ready:** the full `# Code navigation` block (§8).
  - **Index building:** a reduced block: "a code index is being built; use `rg` until `ling-code status` reports it ready". `ling-code` answers "index not ready yet" rather than failing obscurely.
  - **No repository, or indexing disabled or failed:** no block.
- **Keeping it current, without a daemon:**
  - an incremental `index_repository` runs detached at every `ling` launch (8.9 s measured, almost all fixed start-up);
  - it also runs on `ling-code index`;
  - it never runs inside a query. A query answers at once from the snapshots, covers the files changed since them with a text search (§7.3), and queues a re-index request for the session process (§4) when any file is changed.
  - **The agent's own edits are the normal case, not the exception.** Between two launches the graph sees nothing the session wrote, and a file the agent created is in neither layer. §7.3's changed set is what covers that gap; the re-index only turns its `heuristic` rows back into `exact` ones.
  - codebase-memory's watcher stays off (`watcher_enabled = false`, `auto_watch = false`) unless `mightling_code_watch = true`.
- **Scope, written by the session process:**
  - **Submodules follow §4.3.** The session process keeps a managed block in `<repo>/.cbmignore` listing every submodule §4.3 excludes, rewritten from the policy before every run, and adds `.cbmignore` to `.git/info/exclude`, so the file never shows up in `git status`.
  - **Ignore rules inside submodules.** codebase-memory applies the superproject's `.gitignore` inside a submodule, which git does not. When a submodule is included, the router reports every excluded subtree from `index_coverage` in `ling-code status`, so the gap is visible rather than silent.
  - **Tracked files an ignore rule matches are skipped** (this repository's `config/` rule drops the tracked `dreamference/config/`). The router lists them from `index_coverage` (`not_indexed_dir`/`not_indexed_file`) and answers queries touching them with `rg`-backed `heuristic` rows.
- **Git worktrees share the main worktree's index.** A worktree (`git rev-parse --git-common-dir` differs from `--git-dir`) is not indexed as a project of its own, which would cost a full database per worktree (Night Shift and fan-out create many). The router maps its paths onto the main worktree's project, and files whose hash differs are stale by §7.3, which is the safe direction.
- **Data:**
  - graphs live in codebase-memory's cache, `CBM_CACHE_DIR` (default `~/.cache/codebase-memory-mcp/`);
  - `ling-code status` reports their disk use;
  - `ling-code forget [<repo>]` deletes a project, and `ling-code status` offers it when a project's root no longer exists.

### 4.2 A binary of its own: `ling-code`

The router is not compiled into `ling`. It is a separate program, installed and linked beside `ling`, `ling-admin`, `ling-search` and `ling-fetch`.

- **Source:** the crate `ling-code-rs/` at the repository root, a Cargo workspace of its own with its own committed `Cargo.lock`. It is not copied into the Codex export and is not a member of Codex's workspace, so it builds with `cargo build --release --locked` in its own directory and none of the builder's Codex rules (the export, the patches, no `--locked`) apply to it.
- **Why not inside `ling`:**
  - **Build cost.** A change to the router would otherwise mean rebuilding Codex: about ten minutes, a 330 MB binary, and a link step that has to be scheduled around the model server's memory. The router alone builds in a fraction of that and can be rebuilt while the model is serving.
  - **No Codex patch and no launcher growth.** rusqlite and the `scip` crate stay out of Codex's dependency graph, and the patch series does not grow.
  - **It is useful without `ling`.** Claude Code and IDEs start `ling-code mcp` directly; a user can run `ling-code refs` in any shell.
  - **Tests run where the code is:** `cargo test --locked` in `ling-code-rs/`, with no export directory.
  - **Isolation.** A crash or a hang in the index cannot take the agent's process with it.
- **What the launcher still does, and all it does:**
  1. at launch, outside the sandbox, it starts `ling-code session --parent-pid <pid>` detached and continues; the session process exits when that `ling` does. With several `ling` sessions in one repository, a lock under `<repo>/.dreamference/` makes one session process the owner and the others exit at once; when the owner's `ling` ends, the next launch takes over;
  2. it runs `ling-code prompt-block` (a read of index state, milliseconds) and appends what it prints to the model's prompt (§8). The text lives in `ling-code`, so the commands and the prompt that describes them cannot drift apart.
  - If `ling-code` is not installed, both steps are skipped and `ling` runs as it does today, with no `# Code navigation` block.
- **Install and `PATH`:** the binary goes to `~/.local/share/dreamference/mightling/bin/ling-code` with `~/.local/bin/ling-code` linked to it, by the same builder step that links `ling`, `ling-admin` and `ling-search`. The link is not optional: the prompt tells the model to run `ling-code`, and a command missing from its shell's `PATH` ends in exit 127, as `ling-admin` did before it was linked.
- **Versions move together.** `ling-code` pins the schema fingerprints of the two stores (§7.5) and the checksums of the pinned tools (§5), so it is built, released and updated with them: `ling-admin codex build` builds it as a second, independent Cargo run, the release workflow attaches it, and `ling update` installs it with `ling`. `ling-code --version` prints its own version and the pinned tool versions.
- **Configuration:** it reads the same files the launcher does (`DREAMFERENCE_CONFIG_PATH`, `./dreamference.toml`, `~/.config/dreamference/config.toml`) for the model server's address and the `code_index_*` / `mightling_code_*` keys, and `$CODEX_HOME/config.toml` for project trust (§9.1). `CODEX_HOME` resolves as in `ling-rs/src/home.rs`: `~/.mightling` unless set, never upstream's `~/.codex`. It needs no running model server and no `ling`.

### 4.3 Submodules: your code, or what you ask for

A submodule is indexed only when it belongs to the same organisation as the repository that contains it, or when the user asks for that submodule by name. Everything else is left out, and every answer says so.

**"Belongs to the same organisation" is two tests, because the URL alone is not enough.** In this repository both submodules live under `github.com/dgxcoder/`, but only one is ours (§2): `fano` is written by the superproject's own author, while `codex` is a fork of Codex, pinned at an upstream tag and authored upstream. A rule on the URL alone would index the 8,670-file submodule this spec was written to leave out.

- **Namespace.** The submodule's host and owner equal the superproject's.
  - **The submodule's URL** is the effective one: `git config submodule.<name>.url` (what `git submodule init` wrote, including the user's own override), else `.gitmodules`. It is expanded through `git ls-remote --get-url`, which applies `url.<base>.insteadOf` and makes no network request (3 ms, §2).
  - **The superproject's URL** is that of the current branch's remote (`branch.<branch>.remote`), else `origin`, else the only remote.
  - **Host and owner** are parsed from `https://`, `ssh://`, `git://` and scp-like `user@host:path` forms: the host lowercased without user information or port, and the owner the first path segment, lowercased. That is the GitHub user or organisation, the top-level GitLab group, the Bitbucket workspace and the Azure DevOps organisation.
  - **A relative URL** (`./x`, `../x`) passes by construction, since git resolves it against the superproject's own remote. With no superproject remote there is nothing to resolve against, and the namespace test is unavailable.
  - **Unavailable** when either side has no remote, or the URL is a local path or `file://`. Then authorship alone decides, and trust is never inherited (below).
  - **Credentials never leave the parser.** A URL carrying user information (`https://user:token@host/…`) is printed, logged and recorded only as `host/owner/repo`.
- **Authorship.** Most of the submodule's own recent history is written by the people who write the superproject.
  - **Our authors:** the author and committer addresses of the superproject's last 1,000 commits, plus `git config user.email`, without GitHub's web-flow committer `noreply@github.com`. A personal `…@users.noreply.github.com` address counts as itself. **Our domains:** the domains of those addresses, less public mail providers (`gmail.com`, `outlook.com`, `hotmail.com`, `yahoo.com`, `icloud.com`, `proton.me`, `protonmail.com`, `users.noreply.github.com`, and the like).
  - **The submodule's commits:** up to 200 non-merge commits reachable from its checked-out `HEAD` (`git log -200 --no-merges --format=%ae`). Author addresses only: a committer is often a bot or a web merge. A shallow clone is decided by the commits it has; when it has no non-merge commit, its merge commits decide. A grafted shallow commit has no parents and counts as a non-merge (`codex`'s does).
  - **The test passes** when more than half of those commits have an author address among our authors or in our domains.
  - **Corroborating, never deciding:** `shallow = true` in `.gitmodules`, and a `HEAD` detached at a tag. `ling-code submodules` prints them beside the verdict, because they are what a reader recognises as "vendored".
- **The decision**, for each submodule that is checked out:

| Namespace | Authorship | Decision | Reason shown |
|---|---|---|---|
| same | passes | **indexed** | `yours` |
| same | fails | not indexed | `third-party` (a fork or a vendored copy under your namespace) |
| different | either | not indexed | `other organisation` |
| unavailable | passes | **indexed** | `yours (by authorship)` |
| unavailable | fails, or no commits readable | not indexed | `third-party` / `unknown` |

  The other reasons, which apply before or after the tests: `too large` and `not checked out` (below), `parent not indexed` for a nested submodule, and `included by you` / `excluded by you` for an explicit choice.

  - **A fork stays third-party until the user says otherwise.** A fork carrying a few of our commits on top of upstream's history fails the majority test over 200 commits. That is intended: the cost and the noise of indexing someone else's project are the same whoever forked it.
  - **This repository**, as a check that the tests are calibrated: `fano` is indexed (same namespace; the 200 commits the test reads are all ours, 212 of 216 overall), and `codex` is not (same namespace; 0 of its 1 commit ours, shallow, detached at `rust-v0.158.0`). `fano` was excluded before this section; `codex` stays excluded.
- **Size guard for automatic inclusion.** A submodule that passes both tests but has more than `code_index_submodule_max_files` tracked files (default 5,000) is not indexed automatically (reason `too large`, with the count). The policy above names the conditions under which a submodule *may* be indexed, not ones that oblige it to be. At this repository's measured rate (7,096 files: ~45 s, 665 MB of graph, §2), 5,000 files are about half a minute and half a gigabyte on every first run; above that, the user decides. The key is honoured only when the file it comes from is outside the repository, whichever way that file was found (`DREAMFERENCE_CONFIG_PATH` can point anywhere, including into the workspace), for the reason below. Admission (§6.4) still applies to whatever is included.
- **Not checked out** (`-` in `git submodule status`): nothing is on disk, so nothing is indexed (reason `not checked out`), whatever the policy says.
- **Nested submodules** (`git submodule status --recursive`) are considered only when their parent is indexed, and are tested against the top-level superproject's namespace and authors, never their parent's: a third-party library's own submodules are not ours because the library's authors wrote them.

**The user decides, outside the workspace.**

- **The commands:**

| Command | Effect |
|---|---|
| `ling-code submodules` | Lists every submodule with its decision, its reason and the evidence: namespaces compared, `n of m commits yours`, file count, `shallow`, the tag `HEAD` is detached at. |
| `ling-code submodules include <path>` | Indexes that submodule from now on, whatever the tests say (reason `included by you`). The size guard does not apply. |
| `ling-code submodules exclude <path>` | Never indexes it (reason `excluded by you`), even if it is yours. |
| `ling-code submodules auto <path>` | Removes either override; the tests decide again. |

  `<path>` is the submodule's path or name; an unknown one is an error that lists the known ones. A change requests a re-index (§4), which adds or drops the submodule's files.
- **Where the choice is kept:** `$CODEX_HOME/ling-code.toml`, keyed by the canonical path of the main worktree (so linked worktrees and Night Shift's share it, as they share the index, §4.1):
  ```toml
  [projects."/home/user/mightling".submodules]
  codex = "exclude"
  "vendor/shared-lib" = "include"   # a path with "/" must be quoted
  ```
  A file of its own, not Codex's `config.toml`, whose schema Mightling does not own.
- **Why not inside the repository.** The same rule as trust (§9.1): `<repo>/dreamference.toml`, `.dreamference/` and `.cbmignore` are in the workspace, so a clone can ship them and the agent can write them from inside the `workspace-write` sandbox. Choosing what is indexed is the user's decision, so it is read only from `$CODEX_HOME`, which is outside the workspace and read-only in the sandbox. The managed `.cbmignore` block is output, rewritten from the policy before every run; editing it changes nothing for longer than one run. With `mightling_code_watch = true`, codebase-memory re-indexes on its own between runs and reads the file as it is then, so an edit holds until the session's next run; the watcher is off by default (§4.1).
- **The agent cannot change it.** Run inside Codex's sandbox, `include`, `exclude` and `auto` fail to write and say so: *"what is indexed is your decision: run `ling-code submodules include codex` in your own shell"*. Listing works everywhere. The request file (§4) stays fixed words only, and `ling-code mcp` reads the policy but has no tool that writes it.
- **Precedence:** an explicit choice beats the tests, and `auto` removes it. A submodule nested in one that is not indexed is never examined, whatever is recorded for it; `ling-code submodules` lists it with the reason `parent not indexed`.
- **`--include-submodules` is removed.** It lasted one run: the session process rewrote `.cbmignore` from the flag's default at every launch (`index/mod.rs`, `write_cbmignore`). Passing it is an error that names `ling-code submodules include`.

**What an included submodule gets.**

- **The universal layer:** its files are indexed with the rest. codebase-memory applies the superproject's ignore rules inside it (§2, §13), so `ling-code status` reports what that drops.
- **The static exact layers** (scip-python, scip-typescript, scip-clang with a compilation database, scip-go offline), detected in the submodule's root and its immediate subdirectories as for the superproject (§6.1), run read-only on the submodule's checkout in the sandbox of §9.1. Paths carry the submodule's path as their prefix (§6.2).
- **The executing exact layers** (rust-analyzer, scip-java, scip-dotnet) need two things:
  - **trust:** **including is not trusting.** A submodule inherits the superproject's Codex trust only when it passes both tests with the namespace test actually available, because commit authorship is not authentication: anyone can write any address into a commit, but only the organisation can publish under its namespace. An included third-party submodule, or one decided by authorship alone, needs its own entry in Codex's trust table (`[projects."<repo>/<path>"]`);
  - **the scratch copy of §6.2**, because a build tool must never write into a submodule's checkout: rust-analyzer indexes a copy of the submodule's tracked files in scratch, as scip-java and scip-dotnet index a copy of their root everywhere. `ling-code submodules` says beside each submodule with such a project on whose trust it runs (`it inherits this repository's trust`, `trusted by its own entry`) or why it does not (`not run: it needs its own trust entry`, with the entry to add), and `status` and `ling-code index` give the same reason.

**Telling the agent.**

- **Every answer** carries one line when any submodule is not indexed: `submodules not indexed: codex/ (third-party)`, at most three names and `+N more`. It is the same principle as `changed since snapshot` (§7.2): a definition that lives in a left-out submodule is reported as not found, and this line is what tells the model why. It repeats the prompt block on purpose: about eight tokens, against a model that has long since stopped reading its prompt when it decides an empty answer means "no such code".
- **The prompt block** (§8) has the same line, inside its 250-token budget, with the instruction to use `rg` there. Its worst case is three names and `+N more`; the budget test (§12) covers it.

**Cost and freshness.** The tests are recomputed whenever they are needed (by the session process before every run, and by the router for each answer's line), never cached: a cache would live in the workspace, where the agent could edit it. They cost 16 ms here (§2), well inside §10's 200 ms bound. A submodule whose `HEAD` moves is re-tested at the next run, so a bump that replaces our code with upstream's flips it to `third-party` without anyone asking.

## 5. Distribution

- **Pinned binaries, verified:**
  - The Mightling release workflow downloads the pinned codebase-memory-mcp and scip CLI releases for `linux-arm64` and checks each against its published checksum.
  - The expected SHA-256s are committed in `ling-code-rs/code-index.sha256`, the same trust model as the rusty_v8 archive.
  - They are shipped beside `ling` and `ling-code` in `~/.local/share/dreamference/mightling/bin/` and attached to Mightling releases, so `ling update` refreshes them together with the `ling-code` that knows their schema fingerprints (§7.5).
  - `ling-admin codex build` fetches the same pinned archives for local builds, and builds and links `ling-code` (§4.2).
- **Fork only when a patch is needed.** No change to codebase-memory is required. If one becomes necessary, for example §11's SCIP import or a fix for its submodule ignore handling, fork it to `dgxcoder/codebase-memory-mcp` as a submodule and build from source, following the Codex pattern: pinned tag, patches in a directory, submodule never edited. The MIT notice must stay in the shipped files.
- **SCIP indexers:**
  - `rust-analyzer` comes from rustup, the toolchain the Codex build already pins.
  - scip-python and scip-typescript are npm packages; `ling-admin code setup` installs them into `~/.local/share/dreamference/mightling/indexers/`, pinned by version and by the lockfile's `integrity` hashes. They need Node.js; a machine without it keeps the universal layer for those languages and says so in `ling-code status`.
  - **scip-go, scip-java and scip-dotnet need a toolchain** that Mightling does not install. `ling-admin code setup` looks once for Go (`go env GOROOT`), a JDK 17 or newer (`$JAVA_HOME`, else `javac` on `PATH`; a runtime without `javac` does not count), and a .NET SDK 8 or newer (`dotnet --list-sdks`), and records each as a link under the indexers directory (`go`, `java`, `dotnet`), the way it records `node`. Maven and Gradle are recorded the same way when `mvn` or `gradle` is on `PATH` (`maven`, `gradle`: the home that holds `bin/`), because scip-java runs them and an installation under the home directory is neither on the sandbox's `PATH` nor visible in it; they are optional, since a system package is on that `PATH` already and a project's wrapper brings its own (§9.1). A toolchain no longer found loses its link. `ling-code` reads only those links, never `PATH`.
  - Each of the three is installed only beside its recorded toolchain, from the archive pinned in `code-index.sha256`: scip-go's tarball; scip-java's launcher, installed as downloaded (a pin whose member is `-`); scip-dotnet's NuGet package, from which the newest `tools/net<N>.0/any/` the SDK runs is unpacked into `indexers/scip-dotnet/`. No `dotnet tool install`, which would fetch the package again. Without the toolchain, setup says so and skips it: that language stays on the universal layer, and `ling-code status` names what to install.
  - scip-clang is not installed: there is no linux-arm64 build (§2).
  - Nothing is ever fetched at index or query time.

## 6. The SCIP layer

### 6.1 Which indexers run

Detection is by project files at the repository root and in immediate subdirectories. A monorepo can get several.

| Found | Indexer | Executes project code? | Runs |
|---|---|---|---|
| `pyproject.toml` / `setup.py` / `requirements.txt` / `*.py` package | scip-python | **no, when run as below**; by default it runs `pip3` and `python3` from `PATH`, i.e. the repository's venv (§2) | **every repository**, automatically |
| `tsconfig.json` / `jsconfig.json`, or a `package.json` beside tracked `.ts`/`.tsx`/`.js`/`.jsx`/`.mjs`/`.cjs` sources | scip-typescript | **no** | **every repository**, automatically |
| `compile_commands.json`, in the directory or its `build/` | scip-clang | no, given the compdb | every repository, on demand |
| `go.mod` | scip-go | no code, but `go list` would download | every repository, on demand, offline enforced |
| `Cargo.toml` (workspace or crate) | rust-analyzer | **yes**: build scripts and proc-macros, always (§2) | trusted repositories, under admission (§6.4) |
| `build.gradle(.kts)` / `settings.gradle(.kts)` / `pom.xml` | scip-java | **yes**: the build | trusted repositories, under admission |
| `*.sln` / `*.slnx`, else `*.csproj` / `*.vbproj` | scip-dotnet | treat as **yes** | trusted repositories, under admission |

**A root project covers its subdirectories,** except for Go: when the repository root is itself a TypeScript (tsconfig or jsconfig), JVM or .NET project, its subdirectories are part of it and are not roots of their own. Go modules are separate whatever their nesting, so every `go.mod` at the root or one level down is a root. `build.sbt` is not detected: `scip-java index` drives only Gradle and Maven.

**A Python file at the root itself is a root of its own.** `setup.py`, or a repository of loose scripts, is in no directory, and indexing `.` would index every directory a second time (the run the per-directory roots exist to avoid). scip-python accepts a file as its target (`--target-only setup.py`), so each tracked `.py` or `.pyi` file directly at the root of the repository, or of an included submodule, is a run of its own, the 12 largest (`ROOT_PYTHON_FILES`); the rest are named by `ling-code index` and `status` and stay on the universal layer. Its store is `scip-python-<file>.db` and its manifest key `scip-python:<file>`. What it costs is in §14.1.

**"On demand"** means on a `ling-code index` the user types, or alongside an exact run (`--exact`); never at launch. A repository with a `go.mod` does not pay for `go list` at every `ling` start.

**How each new indexer runs** (in the sandbox of §9.1, with the convert step of §7.5 after it):
- **scip-typescript:** `node --max-old-space-size=<¾ of the cap> main.js index <project> --cwd <root> --no-progress-bar`. The project is the root's own tsconfig or jsconfig; without one, a configuration Mightling writes into scratch (`allowJs`, the root's files, `node_modules` excluded), because the tool's own `--infer-tsconfig` writes into the tree. The whole repository is readable, since a tsconfig may extend one above its root. Node's heap stays under the cgroup's cap, so a project too large fails in Node with a message instead of being killed.
- **scip-go:** `scip-go index --module-root . --repository-remote local --module-version <HEAD>` in the module (both values given, so scip-go never runs git for its defaults), with `GOPROXY=off`, `GOSUMDB=off`, `GOFLAGS=-mod=readonly`, `GOTOOLCHAIN=local` (without it a `go.mod` naming a newer Go downloads that toolchain), `GOWORK=off`, `GOTELEMETRY=off`, `CGO_ENABLED=0` (cgo would run the C compiler over the module's C files), `GOCACHE` and `GOPATH` in scratch, and the user's module cache (`$GOMODCACHE`, else `~/go/pkg/mod`) bound read-only.
- **scip-clang:** `scip-clang --compdb-path=<compdb> --jobs=4` from the root, where a binary exists.
- **scip-java and scip-dotnet** build the project, and both build tools write their output into it, so each runs on a **copy of the root's tracked files in scratch** (`git ls-files` piped through `tar`; outside git, everything but the usual build and dependency directories). The checkout itself stays read-only. Their caches, offline flags, build-tool wrappers and the .NET restore are in §9.1.

**scip-python is only static when Mightling controls its environment.** It is run with `--environment <file>` (the package list Mightling writes, empty for an untrusted repository), a `PATH` holding only Mightling's own Node.js and the system `/usr/bin`, and `PYTHONSAFEPATH=1` and `PYTHONNOUSERSITE=1`, so the one Python it still starts (to read `sys.path`) is the system interpreter and cannot import a `sitecustomize.py` from the repository. Measured: no `pip3`, no repository interpreter, identical references. The same `PATH` rule applies to scip-typescript's `node`.

The static indexers run for untrusted repositories too because, run this way, they cannot execute anything from it, and because the universal layer alone gets Python method callers wrong half the time (§2). They still run inside the sandbox of §9.1, with no network. A project that fails to index keeps the universal layer only; the failure is recorded in the manifest, not retried in a loop.

### 6.2 Where output goes

- **Files:** `<repo>/.dreamference/scip/<indexer>.scip` (moved there by the supervisor after the run, never written in place by the indexer, §9.1), the query store `index.db` (§7.5), and `manifest.json`: `{indexer, version, commit, path_prefix, dirty_files, file_hashes, started, duration_s, peak_rss_mb, peak_cap_bounded, cap_mb, status}`. `status` is one of `ok`, `failed: <reason>`, `deferred: memory`, `deferred: busy` or `deferred: model-start`. The session process also writes `graph.json` beside it after every universal run: `{commit, dirty_files, finished}`, so the graph's snapshot has a commit too (codebase-memory records file hashes but no commit). `.dreamference/` is already git-ignored.
- **Paths are relative to the indexed root**, for every indexer: rust-analyzer's are relative to the workspace (`core/src/…`), scip-python's to the `--target-only` directory (`cli/…` for `dreamference/cli/…`). The manifest's `path_prefix` restores the repository path.
- **Never index inside a submodule's checkout with a build tool that writes to it.** rust-analyzer runs `cargo metadata` and build scripts, a build script may write beside its manifest, and even `cargo tree` rewrites a submodule's `Cargo.lock`. A Rust workspace in an included submodule is indexed from a **copy of the whole submodule's tracked files in scratch** (`git -C <submodule> ls-files` piped through `tar`, inside the sandbox): the whole submodule, not only the crate, so a `path = "../sibling"` dependency inside it still resolves. rust-analyzer's paths are relative to the workspace it is given, so the run's prefix (`<submodule>/<crate>/`) maps them back. In the superproject the workspace is still indexed where it is, read-only (§9.1). This project's `codex/` workspace, if the user includes and trusts it, is copied the same way from its checkout; the builder's export is not used.
- **`CARGO_TARGET_DIR` points at a scratch directory** so indexing never touches a build cache another build is using. The build-script cache it accumulates is what makes the second Rust run ~2 minutes faster (§2).

### 6.3 When it runs

- **Static indexers (Python, TypeScript):** after the universal index at launch when the manifest is older than `HEAD` or files changed, detached; and on `ling-code index`. Cheap enough (19 s for `dreamference/`) to follow every launch.
- **Executing indexers, trusted repositories only (§9.1):** in the background, never blocking a session:
  - when no exact index exists, or it is older than `HEAD` by more than `code_index_stale_commits` commits (default 20);
  - when idle, preferring the moments the model server is stopped (§6.4);
  - in the Night Shift window, before night tasks start, so they begin with a fresh index (see `DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md`).
- **On demand:** `ling-code index --exact`.
- **Before a risky operation:** when `refs`/`impact` runs while any file has changed since the snapshot (§7.3's changed set). This only *requests* a run; the answer is given immediately, with the changed files searched by text and their rows tagged.
- **Every trigger goes through the scheduler of §9.2,** and every executing run through §6.4's admission.

### 6.4 Memory admission

A fixed cap either kills the run (8 GB did, on 2026-09-28) or has to be sized for the worst repository, taking memory from the model. Runs therefore go through admission control.

**Admission is host-wide and covers every run.** The earlier revision admitted only executing indexers, one repository at a time, and gave the universal and static indexers a fixed 4 GiB cap with no check. Host memory is one pool, so that left three ways to repeat the 2026-09-29 kill:

- **A run nobody admitted.** The universal and static indexers start at every `ling` launch. With the 122B fallback model resident the host has about 4 GB above earlyoom's line; one scip-python run (2.1 GiB measured on 81 files, 4 GiB allowed) can cross it, and earlyoom then kills the largest process, which is the model server.
- **Two runs that each fit alone.** A cap is computed from `MemAvailable` at the start, but an indexer takes minutes to grow into it. Two sessions in two repositories, started seconds apart, both read the same `MemAvailable`: two Rust runs would each be given 25.8 GiB out of the same 36.
- **Per-scope CPU limits add up.** `CPUQuota=400%` on each of N scopes is N × 4 cores, which is what §9.2 exists to prevent.

So:

- **One slice.** Every index scope, of every kind, is started with `--slice=mightling-index.slice`. The slice carries the aggregate limits, enforced by the kernel however many sessions and repositories are involved: `MemoryMax` (set by the admitter, below), `MemorySwapMax=0`, `CPUQuota=400%` and `AllowedCPUs=<4 cores>`. Verified on this machine (§2). Each scope keeps its own `MemoryMax` inside it, so one run cannot take another's share.
- **One ledger.** Admission runs under an exclusive `flock` on `$XDG_RUNTIME_DIR/mightling-index/admission.lock`, held only while deciding. Under the lock the admitter:
  1. lists the live scopes of the slice and reads each one's cap (`memory.max`) and use (`memory.current`);
  2. computes `budget = MemAvailable − code_index_reserve − Σ (cap − use)` over them: what is left after every run already admitted grows into its cap;
  3. gives the new run `cap = min(ceiling for its kind, budget)` and starts it only if `cap ≥ need` (below);
  4. sets the slice's `MemoryMax` to the sum of the live caps, and releases the lock once the scope exists.
  The ledger is the cgroup tree itself, so a crashed session leaves nothing stale: a scope that is gone is no longer counted.
- **Reserve:** `code_index_reserve = earlyoom SIGTERM threshold + 4 GiB`, read from earlyoom's `-m` percentage and `MemTotal` (on this machine 6.2 + 4 = ~10.2 GiB). The model's memory is already allocated when it is resident, so `MemAvailable` excludes it; the reserve protects the host, the session and the model's transient allocations. On 2026-09-29 an index run that ignored this pushed the host under earlyoom's line and earlyoom killed vLLM.
- **Ceilings by kind:** universal and static indexers 4 GiB (`code_index_small_ceiling`); executing indexers `code_index_memory_ceiling`, default 40 GiB.
- **Need, and the first run:** `need = 1.2 × peak_rss_mb` from the manifest for this indexer and repository. With no record yet, a floor by kind stands in: 512 MiB for the universal indexer (142 MiB measured), 3 GiB for a static one (2.1 GiB measured), 8 GiB for an executing one. A run the budget cannot cover is not started: `status: "deferred: memory"`, `ling-code status` says so, and queries keep answering from the last snapshot plus §7.3's text search.
- **At most one executing run on the host**, not one per repository: a lock in `$XDG_RUNTIME_DIR/mightling-index/` held for the run's life. Universal and static runs may start beside it when the budget admits them; the slice's CPU limit is shared between them.
- **If the host crosses the line anyway, the indexer dies first.** Every indexer is started through `choom -n 1000`, so earlyoom and the kernel, which both pick by `oom_score`, choose an index run before the model server.
- **Cap-bounded peaks are marked.** When a run's peak ends within 10% of its cap (`peak_cap_bounded: true`), the recorded peak is a lower bound: the cgroup was reclaiming against the cap. The next admitted run gets `min(ceiling, 1.5 × peak)` if available, so the first uncapped run raises the record. An OOM-killed run records its cap as the lower bound, so the next attempt is not doomed the same way.
- **What this means for Codex today:** recorded peak ≥ 21.5 GiB, cap-bounded, so admission needs `cap ≥ 25.8 GiB`, i.e. `MemAvailable ≥ ~36 GiB` with no other index run holding part of the budget. With Qwen3.8 resident the machine has 35–38 GiB available, so a run is admitted only at the quiet end of that range; with the model server stopped (~100 GiB available) always. Scheduled runs therefore prefer `ling-admin server stop`, idle periods with no server, and the Night Shift window. While no fresh exact index exists, the router serves the last snapshot, with changed files tagged `heuristic (stale)`.
- **The conversion is admitted too:** `expt-convert` peaked at 2.7 GB on Codex and runs in the indexer's scope, under the same cap, right after it.
- **Partitioning, as an option to measure, not a plan:** index the workspace in passes over subsets of `[workspace] members`, merged in the store. The caveat stands: `codex-cli`'s dependency closure is most of the workspace, so the passes that matter may peak almost as high as the whole.

## 7. The router

### 7.1 Operations

| `ling-code …` | Answered by | Notes |
|---|---|---|
| `search <text>` | the graph's `nodes_fts` (FTS5, BM25) over names, qualified names and bodies | `nodes_fts` is contentless (`content=''`): it returns rowids, joined back to `nodes` for names and locations. Semantic ranking only through `ling-code mcp` with `mightling_code_semantic = true` (§4). |
| `outline <file>` | the graph's `nodes` for that file, ordered by line | |
| `show <symbol>` | the definition's `file:start-end` from the graph, read from disk | Returns one symbol's source, not the whole file. |
| `def <symbol>` | SCIP if fresh (§7.3), else the graph | A definition written since the snapshots is found by the changed-set search (§7.3) and tagged `heuristic (text)`. |
| `refs <symbol>` | SCIP for fresh files; for every file changed since the snapshot, the graph where it is fresh **and** a whole-word text search (§7.3); merged | Tagged per row. |
| `callers` / `callees <symbol>` | SCIP references mapped to their innermost enclosing definition (`defn_enclosing_ranges` in the store, verified §2), else the graph's `CALLS`/`USAGE` edges | For Python and Rust the graph finds 58% and 78% of calling files (§2); graph-only answers carry a header note saying so. A text hit in a changed file is mapped to its enclosing definition through the graph's outline when the graph is fresh for that file, and printed as a bare `file:line` otherwise. |
| `impl <trait-or-interface>` | SCIP `relationships` (`is_implementation`), else the graph's `IMPLEMENTS`/`OVERRIDE` edges | Changed files are searched for the trait's name (§7.3). |
| `impact <symbol-or-diff>` | SCIP references, transitive to depth N via enclosing definitions; the graph's edges for stale files | Answers "what breaks". Text hits in changed files count at depth 1 and are not followed further; the header says so. |
| `status` | both | Layers present, freshness, languages covered, excluded subtrees, disk use, last index time, deferred runs and why. |
| `index [--exact]` | both | Re-index. `--exact` also schedules executing indexers. |
| `submodules [include\|exclude\|auto <path>]` | §4.3 | Which submodules are indexed and why; the three verbs write the user's choice outside the workspace, and fail inside the sandbox. |

### 7.2 Output

Output is plain text, compact, one row per result, which is the shape the local model reads best:

```
refs codex_core::config::Config::load   (13 results; 10 exact, 3 heuristic)
exact = SCIP rust-analyzer @ 47f4d81; heuristic = codebase-memory or text search
changed since snapshot: 6 files, all searched
exact     codex-rs/cli/src/main.rs:1041
exact     codex-rs/tui/src/app.rs:318
heuristic codex-rs/exec/src/lib.rs:77
heuristic (text) codex-rs/core/src/session.rs:412
unresolved 1 call through `dyn ConfigSource` in codex-rs/core/src/lib.rs:2204
```

The source and commit are stated once in the header. `--json` gives the same data for tools. Five lines are mandatory, because they are what tell the agent it has to verify:
- **`changed since snapshot`**, on every answer, with the number of files changed since the older of the two snapshots and whether they were searched. `0 files` is the only state in which an all-`exact` answer is complete.
- **`unresolved`**, whenever either layer reports calls it could not resolve.
- **`not indexed`**, listing touched paths outside a layer's coverage (§4.1).
- **`not checked`**, whenever the changed set was too large to search (§7.3). It names the count and says the answer may be missing references from those files.
- **`submodules not indexed`**, whenever a submodule is left out (§4.3), with its reason.

**Every answer is bounded by construction.** The served model's context is 262,144 tokens, so the bound is no longer about fitting the window. It is about cost: every token of tool output is re-read on later turns, and a cold prefill runs at ~1,700 tokens/s (~1,000 at 116K tokens), so a 5,000-token answer costs about 3 s on every later turn the prefix cache misses, and dilutes the model's attention.

- **Row cap:** 40 rows by default (`--limit N`, hard ceiling 200). Rows are ordered `exact`, then `heuristic`, then `heuristic (text)`, so text hits on a common name never push exact rows off the first page. At 15–20 tokens a row plus the header and at most 15 summary lines, a full answer stays under about 1,000 tokens (§10's acceptance figure).
- **Grouping first, rows second:** above the cap, the answer opens with a per-file summary sorted by count, limited to 15 files. The rows follow, taken from those files.
- **The header always states what was cut:** `refs Config::load (312 results in 41 files; showing 40; next: --offset 40)`.
- **Narrowing flags:** `--path <glob>`; `--kind def|read|write|import` from SCIP roles where the indexer distinguishes them (scip-python marks imports as reads, so `--kind import` is unavailable for Python and says so); `--exact-only`; `--offset N`, a cursor invalidated with an error, not silently shifted, if the index changes between pages (detected by the graph's `store_meta.mutation_gen` and the store's run id).
- **The prompt block is budgeted too:** the `# Code navigation` block of §8 rides on every turn and is kept under 250 tokens.

### 7.3 Freshness and merging

A snapshot goes stale in two ways, and only the first is visible from its own rows:

- **A file the answer names has changed.** Its rows may point at the wrong lines.
- **A file the answer does not name now references the symbol:** one created since the snapshot, or an edit that added a call. It is in no row, so no check of the rows' files can find it. Measured in §2: five commits after a snapshot, `ModelDownloader` had two such files and three other symbols had one. The agent's own edits create these in every session, and `refs` is asked right after them.

Freshness is therefore decided for the repository, not for the answer:

- **The changed set `C`, computed on every query.** For each layer, the files that differ from its snapshot:
  - `git --no-optional-locks diff --name-only <snapshot commit> HEAD`, plus `git --no-optional-locks status --porcelain` (modified, staged, deleted and untracked files outside the ignore rules), plus the snapshot's own `dirty_files`;
  - the snapshot commit is `manifest.json`'s `commit` for SCIP and `graph.json`'s for the graph (§6.2);
  - each candidate is confirmed against that layer's hash table (`file_hashes[path]` in the SCIP manifest; the graph's `file_hashes(rel_path, sha256, mtime_ns, size)`), by `(mtime, size)` first and by hash only if either differs. A path with no entry is new; a path that no longer exists is deleted;
  - measured: 39 ms for both git calls on this repository, inside the sandbox too (§2).
- **When git cannot answer** (the snapshot commit is gone after a rebase and garbage collection, or the directory is not a git repository): every file of `git ls-files -co --exclude-standard`, or of the layer's own file table, is checked by `(mtime, size)` against the hash tables. That is one `stat` per file, bounded by `code_index_max_files`.
- **A file outside `C` is fresh** for that layer; its rows are served as they are. Rows in a deleted file are dropped, and the header counts them.
- **Every file in `C` is searched for the symbol's bare name, as a whole word.** This is the only step that can see a reference added since the snapshot:
  - the router reads the files itself (no `rg` binary is needed) and matches the last path segment of the symbol (`load` for `Config::load`), on word boundaries;
  - a hit on a `(path, line)` no layer already reports becomes a row tagged `heuristic (text)`;
  - where the graph is fresh for the file, its rows are merged in as before (`heuristic`). The text search still runs there, because the graph finds only 58% of Python callers and 78% of Rust ones (§2) and a miss would otherwise leave no trace;
  - it over-reports: another symbol with the same name matches too. That is the safe direction, and the tag says so. It cannot see a reference through an alias (`from m import f as g`) or a name built at run time; §13 records it.
- **Bounded:** at most `code_index_scan_max_files` files (default 2,000) and 64 MiB are read. Above either, nothing is searched, the mandatory `not checked` line (§7.2) gives the count, and no row of the answer is called complete. Measured: 3 ms for 19 files; 21 ms for a whole-word search of all 4,900 Rust files of Codex (§2), so the bound is a guard against a pathological tree, not a cost the agent will meet.
- **`refs` and `def`** use SCIP rows for files fresh for SCIP, graph rows for files fresh only for the graph, and text rows for every file in `C`.
- **Rows present in more than one source** are de-duplicated by `(path, line)`; SCIP wins over the graph, and the graph over text.
- **Any changed file requests a run.** If `C` is not empty, the router *requests* a background re-index (§4) and says so in the header. It never runs one in the query's path. Until the run finishes, the text rows are what keep the answer complete.
- **Cost:** two git calls, one `stat` per file of `C`, and a read of those files. Files outside `C` are not touched at all, so an answer on an unchanged tree costs the 39 ms of git and nothing else.

### 7.4 Joining the two layers: symbol identity

The layers name things differently. The graph has qualified names (`dreamference.vllm_server.vllm_server_manager.VLLMServerManager.start_server`, prefixed by the project name). SCIP has opaque symbol strings (`rust-analyzer cargo codex-core 0.158.0 config/load_config_as_toml_with_cli_overrides().`, ``scip-python python dreamference <hash> `dreamference.vllm_server.vllm_server_manager`/VLLMServerManager#start_server().``) and a role bitmask with `Definition = 1`. The shared key is the **definition's location**:

1. **Name to candidates.** The agent types a name as it sees it in code. The graph's `nodes` (by `name`, then `qualified_name` suffix) returns candidate definitions with `(file_path, start_line, end_line)`. Several candidates are listed with their paths; the router never guesses.
2. **Location to SCIP symbol.** For the chosen definition, the router finds the store's definition mention (role & 1) whose document is that file and whose chunk range contains the definition line, decodes that chunk's occurrences, and takes the one overlapping the name. Its symbol string is the exact identity.
3. **Symbol to exact references.** Every mention of that symbol is an exact reference. Roles distinguish reads and writes where the indexer sets them; `relationships` give `is_implementation`, `is_reference` and `is_type_definition`, which answer `impl`.
4. **Fallback when step 2 cannot run** (the definition's file is stale, or has no SCIP document): the router searches the store's `global_symbols` by `display_name` and by symbol-string suffix, through indexes the router adds after conversion (§7.5).
   - Exactly one match: use it, and tag the definition `heuristic` (located by name, not by position).
   - Several: list them. None: graph only, all rows `heuristic`.
5. **Coordinates.** SCIP ranges are **0-based**; the graph's lines are **1-based** (measured, §2). Everything is normalised to 1-based lines before de-duplication and printing. A fixture test (§12) catches any regression, because an off-by-one would turn every overlap into a duplicate pair.
6. **Paths.** SCIP document paths are relative to the indexed root, the graph's to the repository; the manifest's `path_prefix` bridges them (§6.2).

### 7.5 The two stores, read directly

**The SCIP query store** is `scip expt-convert` output from the pinned scip CLI:
- `documents(id, relative_path, language, …)`, `chunks(id, document_id, start_line, end_line, occurrences BLOB)`, `global_symbols(id, symbol, display_name, kind, enclosing_symbol, relationships BLOB, …)`, `mentions(chunk_id, symbol_id, role)`, and `defn_enclosing_ranges`, indexed on `mentions(symbol_id, role)`, `global_symbols(symbol)` and the chunk ranges.
- After conversion the router adds its own indexes, `global_symbols(display_name)` and a reversed-symbol column for suffix lookups, and records the run id. The database is written as `index.db.new` and renamed into place atomically.
- A reference lookup is a join through `mentions` to a chunk (2 ms measured); exact positions come from decoding that chunk's occurrence blob with the `scip` crate, a few KB per chunk.
- **`expt-convert` is marked experimental.** The router pins the scip CLI version and a **schema fingerprint** (the SHA-256 of the ordered `sql` column of `sqlite_master`). On a mismatch it builds the store with its own converter instead (the table layout of the previous revision of this spec: `documents`, `symbols`, `occurrences` stored 1-based, `relationships`), decoding the `.scip` whole with the `scip` crate (2 s and 2.1 GB on Codex).
- The `.scip` file is kept so the store can be rebuilt without re-indexing; queries never touch it.

**The codebase-memory graph** is read in place, read-only:
- Tables used: `nodes(project, label, name, qualified_name, file_path, start_line, end_line, properties)`, `edges(project, source_id, target_id, type, properties)` (with the call-site line in `properties`), `nodes_fts`, `file_hashes`, `index_coverage`, `projects`, `store_meta`. All the joins the router needs are covered by the tool's own indexes.
- **Compatibility:** the router pins a schema fingerprint per codebase-memory version, like the SCIP store's. A mismatch drops to the fallback ladder of §4 and logs once; it never guesses at a changed schema.
- **Concurrency:** the database uses a rollback journal (`journal_mode = delete`), so a query that coincides with a detached re-index commit can see `SQLITE_BUSY`. The router opens it read-only with a 2 s busy timeout, reads `store_meta.mutation_gen` before and after its lookups, and retries once if it changed. It opens the file by path for each query and never holds a handle, because an upgrade replaces the file (v0.11.0's release notes: "the first run after upgrading rebuilds your index once").

## 8. Agent interface

- **Prompt:** the launcher appends a `# Code navigation` block to the model's prompt, next to web access and Gmail. The text is what `ling-code prompt-block` prints (§4.2). It covers the commands of §7.1, the meaning of `exact`/`heuristic`/`unresolved`/`not indexed`/`not checked`, and one rule: *before changing a signature, renaming or deleting, run `ling-code refs`. If any row is `heuristic`, `unresolved` or `not indexed`, or the answer has a `not checked` line, confirm with `rg` and run the build or tests after the edit.*
- **Ready, building or absent:** the block is full, reduced or absent according to the index state at launch (§4.1).
- **MCP:**
  - `ling-code mcp` serves the same operations over stdio for Claude Code and IDEs, and replaces jCodeMunch in `~/.claude.json` once implemented.
  - `ling-admin mcp`'s `workspace_search_code` is re-pointed at the router; the `dreamference` context engine (per-file TF-IDF, FTS5 and embeddings) is retired once the router passes §10, since it answers a subset of `ling-code search` with no call graph.
  - The local model keeps using shell commands, because it does not reliably call MCP tools under Codex's Code Mode (see `codex_runner.py`'s history).

## 9. Resources and safety

On GB10, host RAM and GPU memory are the same memory, and running it out can freeze the host, not just kill a process (see `psi_watchdog.py`). On 2026-09-29 an unconstrained index of this repository (the old `dreamference` context engine walking 2.3 GB of files) pushed available memory under earlyoom's line and earlyoom killed vLLM. Everything below exists so that cannot recur.

- **Memory limits:** every indexer, universal or exact, runs under `systemd-run --user --scope -p MemoryMax=<cap> -p MemorySwapMax=0`, so exhaustion OOM-kills the indexer instead of stalling the host. `<cap>` comes from §6.4's admission for **every** run; for codebase-memory and the static indexers it is at most 4 GiB (measured peaks 142 MiB and 2.1 GiB), with `CBM_MEM_BUDGET_MB` set below it so the tool budgets itself before the cgroup has to act. No indexer starts without being admitted, at launch or otherwise, and all of them share `mightling-index.slice`, whose limits hold for the sum.
- **Sharing the machine with inference:** §9.2. `nice`/`ionice` alone are not enough on this machine.

### 9.1 Exact indexing executes project code

The Rust exact layer always runs the repository's build scripts and proc-macros (§2: it cannot be turned off in scip mode). Indexing an untrusted repository with an executing indexer means running that repository's code.

Three rules follow.

- **Trust gate for executing indexers** (rust-analyzer, scip-java, scip-dotnet):
  - they run only for repositories the user has marked trusted through Codex's own per-project trust (`[projects."<path>"] trust_level = "trusted"` in `$CODEX_HOME/config.toml`, which the TUI already asks about);
  - **nothing inside the repository can grant trust.** A file there can be shipped in a clone and written by the agent from inside the `workspace-write` sandbox, so a `trusted` key in `<repo>/.dreamference/` or in a repository's `dreamference.toml` is ignored. `$CODEX_HOME` is outside the workspace and read-only in the sandbox;
  - untrusted repositories get the universal layer plus the static exact layers, and `ling-code status` says so in one line;
  - **a submodule is trusted only on its own terms** (§4.3): it inherits the superproject's trust when it passes both ownership tests with a namespace to compare, and otherwise needs its own entry. Choosing to index it is not trusting it.
- **Sandbox, for every indexer.** Each runs as `systemd-run … -- bwrap …`, with the network removed, the home directory hidden, and nothing writable except a scratch directory of its own. For rust-analyzer (§2's measurements were taken with an earlier, looser form of this command; the differences are the binds, which cost nothing):
  ```
  systemd-run --user --scope --unit="mightling-index-$REPO_ID" \
    --slice=mightling-index.slice \
    -p MemoryMax="$CAP" -p MemorySwapMax=0 -- \
    choom -n 1000 -- nice -n 10 ionice -c3 \
    bwrap --die-with-parent --unshare-net --unshare-pid \
          --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
          --tmpfs "$HOME" \
          --ro-bind "$RUSTUP_HOME" "$RUSTUP_HOME" \
          --ro-bind "$CARGO_HOME" "$CARGO_HOME" \
          --ro-bind "$SRC" "$SRC" \
          --bind "$SCRATCH" "$SCRATCH" \
          --setenv CARGO_NET_OFFLINE true --setenv CARGO_TARGET_DIR "$SCRATCH/target" \
          --setenv CARGO_BUILD_JOBS 4 \
          -- rust-analyzer scip "$SRC" --output "$SCRATCH/out/rust-analyzer.scip"
  ```
  - **Nothing outside `$SCRATCH` is writable, `$CARGO_HOME` included.** The first draft bound `$CARGO_HOME` read-write, on the belief that Cargo needs its lock file there. It does not: Cargo skips the lock on a read-only file system (measured, §2). And the write access was an escape: a build script could replace `~/.cargo/bin/cargo` or add a `rustc-wrapper` to `~/.cargo/config.toml`, and that code would run unsandboxed the next time anyone ran Cargo, for example in `ling-admin codex build`, which builds `ling` itself. It also runs with no one asking: the exact index starts in the background at launch. Codex's own sandbox never gives the agent's builds that access.
  - **The home directory is an empty tmpfs**, with only the toolchain, Cargo's registry and the source bound back, read-only. A build script has no reason to read `~/.ssh`, `$CODEX_HOME` or the mail service's credentials, and with them hidden it cannot copy them into an output the agent (which has the network) can later read. Binds are given after the tmpfs they sit under, or bwrap hides them again; that includes a source or scratch directory under `/tmp`.
  - **The output is not written into the repository by the indexer.** It writes `$SCRATCH/out/`, empty at the start of each run. The supervisor, outside the sandbox, checks that the file decodes as SCIP and then moves it to `<repo>/.dreamference/scip/` and writes the manifest. With `.dreamference/scip` bound writable, as in the first draft, a build script could overwrite `index.db`, `manifest.json` or another indexer's `.scip`, and the router would serve its content tagged `exact`; a run killed half-way would also leave a truncated `.scip` in place of the last good one.
  - **Other executing indexers follow the same shape,** on the scratch copy of §6.1: their caches are bound read-only and their writable state is redirected into `$SCRATCH`.
    - **Maven** (scip-java): `--batch-mode --offline -DskipTests -Dmaven.repo.local=$SCRATCH/m2 -Dmaven.repo.local.tail=~/.m2/repository clean verify`; the tail is a read-only chained repository (Maven 3.9 and newer).
    - **Gradle** (scip-java): `GRADLE_USER_HOME=$SCRATCH/gradle-home`, the user's `~/.gradle/caches` supplied read-only through `GRADLE_RO_DEP_CACHE`, `org.gradle.daemon=false`.
    - **Wrappers** (`gradlew`, `mvnw`): scip-java runs the project's wrapper when it has one, and a wrapper downloads the Gradle or Maven its properties file names. Its home is in scratch (`GRADLE_USER_HOME`, `MAVEN_USER_HOME=$SCRATCH/maven-home`), so: when the user already has that distribution (`~/.gradle/wrapper/dists/<name>`, `~/.m2/wrapper/dists/<name>`: they have built the project), it is copied once into the run's own home, markers included, and the wrapper finds it installed. The user's directory is bound read-only and never used in place, because the wrapper takes a lock file beside the distribution. Without it, and with the tool itself installed, the wrapper script is removed from the scratch copy and scip-java falls back on `gradle` or `mvn`. With neither, the wrapper tries its download and the run is `failed: offline`.
    - **The launcher's cache** (scip-java): `COURSIER_CACHE=$SCRATCH/coursier`. The launcher unpacks its jars before anything runs, by default under `~/.cache/coursier` of the account's home, which the JVM takes from the password database, not from `$HOME`.
    - **.NET** (scip-dotnet): `NUGET_PACKAGES=$SCRATCH/nuget-packages`, `DOTNET_CLI_HOME` in scratch, and a `nuget.config` written into scratch whose only source is the user's `~/.nuget/packages`, read-only: no network feed is configured, so restore can only use what is on disk. Telemetry, the first-run experience, node reuse and the MSBuild server are off. **The restore is run by Mightling, not by scip-dotnet** (`--skip-dotnet-restore`): scip-dotnet's own is `dotnet restore /p:EnableWindowsTargeting=true`, which asks NuGet for the Windows desktop reference pack for every project, fails offline even for a console project with no packages (NU1100), and is followed by an index written without the project's packages and exit status 0 (measured, §14.1). So a plain `dotnet restore` runs first, the Windows-targeting one only if that fails, and the indexer only after a restore that succeeded; `obj/` and `bin/` are excluded from the documents.
    - An indexer that cannot work with this fails and is recorded (§6.1), never given a writable cache.
  - **A dependency Cargo has downloaded but not yet unpacked** (`registry/cache` without `registry/src`) cannot be unpacked into a read-only `$CARGO_HOME`. The run fails with `failed: dependencies not unpacked` and `ling-code status` names the remedy: one `cargo fetch` or build by the user.
  - `/usr/bin/bwrap` is already installed; it is Codex's own sandbox.
  - The CPU limits are on the slice, not the scope (§6.4), so they bound all runs together. The measurements of §2 were taken with them on a single scope, which is the same limit for one run. systemd places `mightling-index.slice` under `ling.slice`, from its name.
  - The scope is named so §9.2's supervisor can `freeze`/`thaw` it and `ling-code status` can find a run in progress.
  - The session process creates the bound directories beforehand, because bwrap cannot bind a path that does not exist.
  - `$SRC` is read-only. A run that needs to rewrite the lockfile fails and is recorded; inside a submodule the run is on a scratch copy instead (§6.2).
- **Offline is enforced, not assumed.**
  - Environment: `CARGO_NET_OFFLINE=true`, `GOFLAGS=-mod=readonly`, `GOPROXY=off`, `GOSUMDB=off`, `GOTOOLCHAIN=local`, `npm_config_offline=true`, Maven's `--offline`, and a NuGet configuration with no network source.
  - Nothing ever runs `npm install`, `pip install` or a dependency download on the indexer's behalf.
  - An indexer that needs something not on disk fails with `status: "failed: offline"`. That is the accepted outcome, not a retry.
- **Air-gapped, stated precisely:** no network access is *permitted* to any indexer at index time, and none is used at query time. The only downloads happen at install (`ling-admin code setup`, `ling update`), each pinned by checksum. codebase-memory's own traffic was checked with strace: none (§2).
- **Scope:** only the current repository is indexed, or explicitly listed ones. `~`, `/` and anything over `code_index_max_files` (default 50,000) are never indexed.

### 9.2 Indexing must not slow the model

On GB10, token generation is limited by memory bandwidth, and CPU, GPU and model weights share the same LPDDR5X. Background indexing competes for that bandwidth, which `nice` and `ionice` do not limit. The Codex exact run averaged about 3.7 busy cores in its first phase. The number to protect is the default model's single-stream decode rate: Qwen3.8-27B, 25.5 / 50.3 / 87.0 tokens/s on prose / code / JSON (`DREAMFERENCE_INFERENCE.md` §5.3).

- **Start only when the model is idle.** This and the next rule hold for every kind of run, including the launch-time universal and static ones. The supervisor reads the served model's `/metrics` and treats it as idle when the engine's request gauges are 0: `sglang:num_running_reqs` and `sglang:num_queue_reqs` on SGLang, `vllm:num_requests_running` and `vllm:num_requests_waiting` on vLLM, whichever answers. No model server at all also counts as idle.
- **Never alongside a model load.**
  - A model container (`dreamference-vllm-<port>`, the name both engines use) that exists but does not answer `/health` is **loading**. Nothing starts then: `MemAvailable` is still high before the weights are mapped, so admission would wrongly pass.
  - A run in flight when a load begins is **stopped**, not frozen, because frozen memory stays resident; it is recorded as `deferred: model-start`.
  - `ling-admin server start` stops every `mightling-index-*` scope before its host-safety pre-flight (`systemctl --user stop 'mightling-index-*'`), keeping `check_host_safety()`'s guarantee intact.
- **Pause instead of competing:**
  - The indexer cannot watch the model itself: it has no network in its sandbox. The session process (or a `ling-code index` typed by the user, §4) therefore starts a detached **supervisor** outside the sandbox that creates and owns the scope, polls `/metrics` every 2 s, applies these rules, writes the manifest, and exits when the scope ends. It lives exactly as long as one run.
  - When a request appears, the supervisor runs `systemctl --user freeze <scope>`, and `thaw` once the model has been idle for 10 s (supported on systemd 255).
  - A run frozen for more than 30 minutes is stopped and recorded as `deferred: busy`.
- **Cap what all runs can take together:** `CPUQuota=400%` and `AllowedCPUs=<4 cores>` on `mightling-index.slice` (§6.4), so two sessions indexing two repositories still share four cores; `CARGO_BUILD_JOBS=4` and the Go and MSBuild equivalents, `CBM_WORKERS=4` for codebase-memory. Newer rust-analyzer than the pinned 1.95.0 adds `--num-threads`; the router passes 4 once the toolchain has it.
- **One run at a time, coalesced:** at most one executing run in flight on the host (§6.4), and one run of each kind per repository; requests during a run collapse into one follow-up, which waits at least `code_index_min_interval` (default 15 minutes) unless it is `ling-code index --exact`. A lock file makes this hold across sessions, and Night Shift's admission counts an exact run as work in progress.

## 10. Evaluation (before building the router)

§2 already answers the cost and correctness questions the first draft left open: the universal layer is too inaccurate on call edges to stand alone (78% Rust, 58% Python), and the exact layer is affordable for static languages and admissible for Codex only when the machine is quiet. What remains:

1. **Question set:** 15–20 real questions about this repository: at least 8 about `codex/codex-rs` Rust (trait methods, re-exports, macro-generated code, `dyn` dispatch) and at least 5 about `dreamference/` Python.
2. **Ground truth:** SCIP from rust-analyzer and scip-python (both now produced on this machine), spot-checked by hand for `unresolved` cases.
3. **Contenders:** the router (both layers); the graph alone; `rg`/`ast-grep` as the baseline.
4. **Metrics:**
   - reference recall and precision against ground truth, line level (§2's figures are file level);
   - **recall after edits:** the same questions asked of a snapshot some commits old (as in §2's replay), and again after adding a reference in an edited file and in a new file without re-indexing;
   - wall time per question and output tokens per answer;
   - the unconstrained peak of the Codex exact index: one run with the model server stopped and a 40 GiB cap;
   - the model's decode rate while an exact index runs, frozen and unfrozen, against §9.2's baseline;
   - the quality of search by meaning, before it is ever enabled by default.
5. **Acceptance:**
   - the router's `refs`/`callers` recall is at least 98% of SCIP's on fresh files, and every miss is visible as `heuristic`, `unresolved`, `not indexed` or `not checked`;
   - **no silent miss after an edit:** every reference by name added since the snapshot, in an edited file or a new one, appears in the answer (under any tag), or the answer carries a `not checked` line;
   - its answers cost fewer tokens than the `rg` baseline;
   - **query latency:** p95 at most 200 ms for `refs`/`def`/`callers`, from process start to last byte, on a warm page cache (the direct reads of §7.5 measured under 10 ms);
   - **answer size:** no answer exceeds 1,000 tokens at the default limit;
   - **inference cost:** the decode rate while indexing runs within §9.2's caps stays within 5% of baseline; with the scope frozen it is indistinguishable from baseline;
   - **admission:** the Codex exact index completes under §6.4 with the model server stopped, and its peak replaces §2's cap-bounded figure.

## 11. Later: SCIP inside the graph

If the router shows that the graph's edges are wrong in concentrated places, the better long-term design is to fork codebase-memory and add SCIP import: exact SCIP edges would replace its `CALLS` edges for indexed files, so its `trace_path` and `detect_changes` become exact too, inside one graph. It is C, and a larger change, so it is deferred until the router has shown where it matters. The same fork would fix its application of the superproject's ignore rules inside submodules (§4.1).

## 12. Tests

- **Router unit tests** (`cargo test --locked` in `ling-code-rs/`):
  - merging with fresh, stale and missing files; de-duplication; `unresolved` and `not indexed` always printed when reported; ambiguous names listed, not guessed; a non-empty changed set requests a re-index.
- **Fixtures** under `tests/fixtures/code_index/`: a tiny Rust crate and Python package with known references, plus **recorded stores**: a codebase-memory v0.11.0 graph database and an `expt-convert` store for them. Router tests run against the recorded stores, so they need neither binary; tests that regenerate them are skipped when the binaries are absent.
- **Schema fingerprints:** a test computes both stores' fingerprints from the recorded fixtures and compares them with the pinned values, so a version bump that changes a schema fails CI rather than a user's query. A mismatched fixture must send the router down the fallback ladder, not into a wrong answer.
- **Direct reads:** a query during a simulated write (a held `BEGIN EXCLUSIVE` on a copy) waits and retries within the busy timeout; a `mutation_gen` change between two reads causes one retry; the router holds no handle after it exits.
- **Join (§7.4):** a recorded SCIP chunk and a recorded graph node for the same definition resolve to the same symbol; their rows de-duplicate to one per `(path, line)`, which catches a 0-/1-based mismatch; a stale definition file falls back to the `display_name` search and tags the result `heuristic`.
- **Changed since the snapshot (§7.3):** against a recorded store of the fixture package,
  - a call added to a file the snapshot already had, and a call in a file created afterwards, both appear in `refs` tagged `heuristic (text)`, and the header reads `changed since snapshot: 2 files, all searched`;
  - the same holds when the graph is fresh for the edited file but has no edge for the call (the 58% case): the text row is still printed;
  - an unchanged tree prints `changed since snapshot: 0 files` and only `exact` rows;
  - rows in a deleted file are dropped and counted;
  - with `code_index_scan_max_files = 1` and two changed files, nothing is searched and the `not checked` line is printed;
  - with the snapshot commit missing from the repository, the fallback by `stat` finds the same changed set;
  - run under Codex's `read-only` sandbox, the git calls succeed and `.git` is not written (`--no-optional-locks`).
- **Safety:**
  - indexing a submodule never writes to it (`git status --porcelain` empty afterwards);
  - **the sandbox holds against a hostile build script:** a fixture crate's `build.rs` tries to create a file in `$CARGO_HOME`, to open `$CARGO_HOME/bin/cargo` for writing, to read a marker file placed in the test's home, and to overwrite `<repo>/.dreamference/scip/index.db`. All four fail, the index still completes, and the previous `index.db` and `.scip` are byte-identical afterwards (skipped without `bwrap`, `cargo` and `rust-analyzer`; the test's home and `CARGO_HOME` are its own, never the user's);
  - a run killed before it finishes leaves the last good `.scip` and store in place;
  - the bwrap argument list built for each executing indexer contains no read-write bind outside its scratch directory (checked on the argument list, so it runs everywhere);
  - indexer runs are killed at the memory cap, not left to exhaust the host (fixture with a tiny cap);
  - no network during `index` or queries: a fixture crate with a missing dependency must fail with `failed: offline`;
  - an untrusted fixture repository never gets an executing index, and its `build.rs` (which writes a marker file) never runs, while its Python package does get a static exact index. The same holds when the fixture ships `.dreamference/code_index.toml` and `dreamference.toml` with `trusted = true`;
- **Isolation from the user's machine**, the lesson of 2026-09-29 (tests recreated live containers and wrote the real compile-signature file):
  - tests set `CBM_CACHE_DIR` under their temporary home and `CBM_RUNTIME_DIR` to a **short** private directory (mode 0700; the socket path must stay under 108 bytes, or the CLI fails with "secure CLI coordination could not be created");
  - no test creates a real `systemd-run` scope or freezes a real unit: `mightling-index-*` scope creation is behind a seam the tests replace, the way `tests/conftest.py` refuses real mutating `docker` commands.
- **Sandbox sides (§4):** `ling-code refs` run under Codex's `read-only` sandbox answers from both stores and writes nothing; under `workspace-write` a stale answer appends exactly one request line, and the session process coalesces ten such lines into one run.
- **scip-python's environment:** a fixture repository whose `.venv/bin/python3` and `sitecustomize.py` write marker files is indexed without either marker appearing.
- **Prompt:** the `# Code navigation` block is full when the index is ready, reduced while building, absent without a repository, and under 250 tokens.
- **Submodules (§4.3)**, on fixture repositories built in the test's temporary directory with local bare remotes and fixed author addresses:
  - same namespace and our authors: indexed (`yours`); same namespace and a foreign author: not indexed (`third-party`); another owner: not indexed (`other organisation`), even with our authors; a relative URL with our authors: indexed;
  - a shallow single-commit submodule is decided by that commit; a fork with 3 of our commits on 50 upstream ones is `third-party`;
  - no remote on either side: authorship decides, and an executing indexer never inherits trust from it;
  - URL forms: `https://`, `ssh://` with a port, scp-like, upper-case owner, an `insteadOf` rewrite, and `https://user:token@host/…`, whose token appears nowhere in the output, the logs or the manifest;
  - an automatically qualifying submodule above `code_index_submodule_max_files` is `too large`; an explicit `include` overrides it;
  - an uninitialised submodule is `not checked out`; a nested submodule of an excluded parent is never examined;
  - `include` and `exclude` in each direction, `exclude` winning over `include`, and `auto` restoring the tests; the choice survives the next launch, which rewrites `.cbmignore` from it (the clobber of the old flag, as a regression test);
  - under Codex's `read-only` and `workspace-write` sandboxes, `include` fails with the message of §4.3 and writes nothing; a `.cbmignore` or `dreamference.toml` edited inside the repository changes nothing after the next run;
  - codebase-memory's incremental run both **adds** a newly included submodule's files and **drops** a newly excluded one's (§14.1 measured the drop, not the add);
  - every answer carries the `submodules not indexed` line while one is left out, and the prompt block names it, still under 250 tokens with five excluded submodules of long paths;
  - `code_index_submodule_max_files` in a file inside the repository is ignored, both as `./dreamference.toml` and when `DREAMFERENCE_CONFIG_PATH` points at it;
  - **this repository**, read-only: `fano` is `yours` and `codex` is `third-party` (skipped where the submodules are not checked out).
- **The separate binary (§4.2):**
  - with `ling-code` absent from the install directory, `ling` launches, starts nothing and adds no block (launcher test, in the export);
  - the builder links `~/.local/bin/ling-code` beside the other three commands, and refreshes the link when the binary is current (Python test, as for `ling-admin`);
  - `ling-code session` exits when its parent pid is gone, and a second one for the same repository exits at once while the first holds the lock;
  - a request file holding anything but the fixed words starts no run;
  - `ling-code index` with no systemd user bus queues a request and starts no process.
- **Performance:**
  - **Output budget:** a fixture symbol with 500 references prints the totals header, at most 15 file-summary lines and 40 rows; `--offset 40` returns the next 40; an `--offset` taken before a re-index fails with an error.
  - **Freshness cost:** on an unchanged tree a `refs` runs the two git calls and opens no source file; with `k` changed files it `stat`s and reads exactly those `k`.
  - **Admission:** with `peak_rss_mb` above the available cap, the run is not started (`deferred: memory`); a cap-bounded peak raises the next cap; an OOM-killed run records its cap as the new lower bound.
  - **Host-wide admission (§6.4),** against a fake cgroup tree and a fake `MemAvailable`:
    - with the budget below the static floor, a launch starts neither the universal nor the static indexer and records `deferred: memory`;
    - two admissions raced from two processes for two repositories, each fitting alone and not together, start exactly one run;
    - a live scope using 1 GiB of a 20 GiB cap takes 19 GiB off the next budget;
    - a first run with no recorded peak uses its kind's floor;
    - a second executing run for another repository waits while the first holds the host lock;
    - every scope is created in `mightling-index.slice`, the slice's `MemoryMax` equals the sum of the live caps, and the indexer's command starts with `choom -n 1000`.
  - **Scheduler:** five triggers in quick succession produce one run and one coalesced follow-up; no run starts while the engine's request gauges are non-zero (fake `/metrics` for both `vllm:` and `sglang:` names); a frozen scope resumes on thaw with the same output as an uninterrupted run.
  - **Model load:** with a model container present but `/health` not answering, no run starts; a run in flight is stopped, not frozen (`deferred: model-start`); `ling-admin server start` stops any `mightling-index-*` scope before its pre-flight.
  - **Worktrees:** a query in a linked worktree reads the main worktree's project, and files that differ are tagged stale.

## 13. Open questions and unverified claims

- **The first-run floors of §6.4** (512 MiB, 3 GiB, 8 GiB) come from one repository's measurements. A large Python or TypeScript project may need more than the 4 GiB small ceiling; it is then killed at its cap, recorded, and not retried in a loop (§6.1), which is safe and leaves that language on the universal layer. Whether the small ceiling should grow with a recorded peak, as the executing one does, is open.
- **The unconstrained peak of the Codex exact index** is unknown: ≥ 21.5 GiB, cap-bounded (§2). One run with the model server stopped and a 40 GiB cap settles it.
- **The decode-rate cost of indexing beside the model**, frozen and unfrozen, is unmeasured.
- **Does freezing a scope mid-analysis leave rust-analyzer healthy?** A cgroup freeze is `SIGSTOP`-like, so it should, but it is untested.
- **scip-java and scip-dotnet have run here only on fixtures** (§14.1), with toolchains in scratch: a multi-module Maven or Gradle build, Kotlin, an Android project, a `.sln` with several projects, a project that needs a private feed or a mirror from `~/.m2/settings.xml` (which the sandbox hides), and their memory on a real code base are unmeasured.
- **A wrapper whose distribution is not on disk** (the user has never built the project on this machine) still fails as `offline`, unless the tool itself is installed (§9.1). A Gradle installed that way may not be the version the project pins.
- **scip-go with a populated module cache** is unmeasured: the fixture has no dependency in any cache. Whether `go list` reads a read-only `GOMODCACHE` without trying to write its lock files is the open point.
- **scip-typescript's `--infer-tsconfig` is never passed:** it writes `tsconfig.json` into the project (§2), which the read-only tree refuses and which would be a write into the user's checkout anyway. Mightling writes the same configuration into scratch (§6.1); a project relying on the flag gets the same files indexed.
- **scip-clang on linux-arm64** needs a build from source (a Bazel project) or an upstream release; neither is planned.
- **A `package.json` at the root over sources everywhere** makes the root a TypeScript root as well as any subdirectory with a tsconfig: the overlapping files are indexed twice, in two stores, and de-duplicated by `(path, line)` at query time. Correct, but twice the work for those files.
- **The `stat` fallback of §7.3 counts files the graph never covers as changed.** With the snapshot's commit lost, a tracked file codebase-memory does not index (it skips `tsconfig.json`, for one) has no stamp, so it is counted as new. That over-reports, the safe direction, and only in the fallback.
- **Search by meaning** is off by default until measured (§4); upstream issues #1155 and #1462 suggest it is weak.
- **codebase-memory's call-edge gaps** are upstream issues (#1153 method calls through instances, #1271 polymorphic Python calls, #1277 cross-file receiver inference, #1354 TypeScript cross-file methods). A release that fixes them changes §2's recall figures, not this design: the exact layer stays the authority where it exists.
- **Its application of the superproject's `.gitignore` inside submodules** is a bug to report upstream; the design works around it (§4.1).
- **The ownership tests (§4.3) are evidence, not proof.** A majority-of-authors rule misjudges a repository whose authors use addresses the superproject never has (a new colleague on a personal address): it is then `third-party` until the user includes it, which is the cheap direction. It cannot be fooled into granting trust, which needs the namespace. The list of public mail domains is a judgement, not a standard.
- **The 5,000-file guard** is scaled from one repository's measurement (§2), not measured on a submodule of that size.
- **`expt-convert`'s schema** may change: that is why it is pinned and fingerprinted, with our converter as the fallback (§7.5).
- **SCIP freshness for rust-analyzer is whole-workspace.** It has no crate-scoped mode (its `scip` flags are `--output`, `--config-path` and `--exclude-vendored-libraries`), so the exact Rust layer after a small edit is a full re-run.
- **Partitioning** may not lower the peak, because the dependency closure of the crates that matter is most of the workspace (§6.4).
- **The tightened sandbox is measured on a probe crate, not on Codex.** A full `rust-analyzer scip` of the Codex workspace with the home directory hidden and `$CARGO_HOME` read-only has not been run; a proc-macro or build script that expects something else under the home directory would fail there, and would be recorded as a failed run.
- **Connecting to an existing Unix socket from inside the sandbox** (the runtime directory is read-only there) is untested; the design does not rely on it (§4).
- **The changed-set search matches names, not symbols.** It cannot see a reference through an alias or a name built at run time, and it reports same-named symbols as `heuristic (text)`. How often an alias hides a new reference in practice is unmeasured; the re-index the query requests closes the gap within one run.
- **`expt-convert` rejects indexes a real indexer writes** (definitions without `SymbolInformation`); `scip-repair` (§14.2) covers the one case seen. Another rejection would leave that root on the universal layer, recorded as `failed`.
- **Several repositories:** start with the working directory only.

## 14. Implementation (2026-10-01)

`ling-code` is built from `ling-code-rs/` (its own lockfile, toolchain 1.95.0), installed and linked by `ling-admin codex build`, and started by the launcher (`ling-rs/src/code_index.rs`). `ling-admin code setup` installs the pinned tools. 113 Rust tests (`cargo test --locked` in `ling-code-rs/`) and the Python tests of `tests/test_code_index.py`, the builder, the MCP server and Night Shift cover it. The language indexers of §6.1 beyond Python and Rust were added the same day (§14.1, §14.2), and so were the submodule policy of §4.3, `impact`, the Night Shift refresh of §6.3 and the MCP re-point of §8 (§14.5). On 2026-10-02 a Python file at a root got an index of its own, the executing indexers began to run inside an included submodule, and scip-java and scip-dotnet were run for the first time (§14.1, §14.2).

### 14.1 Measured on this repository

| Quantity | Value |
|---|---|
| Launch-time run through the session and supervisor | codebase-memory (3,689 nodes once `.cbmignore` listed the submodules: an incremental run drops `codex/`, which had been 132,225 of 137,176 nodes), scip-python over `dreamference/`, `tests/`, `scripts/` and two other tracked Python directories (peaks 166–629 MiB each), rust-analyzer over `ling-code-rs/` and `ling-web-rs/` (1,027 and 1,203 MiB), each in its own scope of `mightling-index.slice`, in the sandbox of §9.1 |
| Query latency, 20 runs each, warm, this repository | `refs ModelDownloader` p50 0.09 s, p95 0.11 s; `callers VLLMServerManager.check_health` p95 0.12 s; `def resolve_model_hf_repo` p95 0.12 s (§10's bound is 0.2 s) |
| The §2 replay | `refs ModelDownloader` now lists `diffusion_server_manager.py` and `sglang_launch_builder.py`: as `heuristic (text)` rows against the morning's snapshot, as `exact` rows once re-indexed; `tests/` callers are exact through the tests store |
| A hostile `build.rs` in the rust-analyzer sandbox | could not create a file in `$CARGO_HOME`, open `$CARGO_HOME/bin/cargo` for writing, read a marker in `$HOME`, or overwrite the store; the index was still written (test, run for real) |
| scip-typescript through `ling-code index --wait` | the `tsgeom` fixture: ok, peak 179–183 MiB with codebase-memory beside it; `refs makeDisk` 5 exact rows, `callers Disk.surface` and `impl Figure` exact. Codex's protocol schema and SDK copied into a scratch repository: 271 and 181 MiB, about 2 s each, 14.4 s for the whole `index --wait` including codebase-memory |
| scip-go through `ling-code index --wait` | the `gogeom` fixture on a scratch Go 1.27.1: ok, peak 21 MiB, 12.2 s for the whole run including codebase-memory; `refs MakeDisk` 3 exact rows, `callers Disk.Surface` 2, `impl Figure` 2. Nothing was written into the module or the home directory |

| The submodule policy on this repository (§4.3) | `fano`: indexed (`yours`: same namespace, 200 of 200 commits ours, 252 files). `codex`: not indexed (`third-party`: same namespace, 0 of 1 commits ours, 8,670 files, shallow, detached at `rust-v0.158.0`). `ling-code index --wait` then took 49 s for the graph and seven scip-python roots, two of them found inside `fano` (`fano/backup_english`, `fano/paper`, peaks 150 MiB) |
| What the policy costs a query | `ling-code submodules` p50 53 ms, p95 57 ms, for 41 git processes (counted with `strace -c`; the listing also gathers the evidence a query does not print). §4.3's 16 ms was the two tests alone. With it, 20 warm runs each: `refs ModelDownloader` p50 0.105 s, p95 0.114 s; `def resolve_model_hf_repo` p95 0.112 s; `callers VLLMServerManager.check_health` p95 0.108 s, with 28 changed files searched (§10's bound is 0.2 s). A first version listed submodules with `git submodule status --recursive`, 30 ms on its own, and put `refs` at p95 0.154 s |
| An edit inside the included submodule | A function added to `fano/main.py` that names `green_diag`: `refs fano/make_figures.py:44` listed `heuristic (text) fano/main.py:20`, with `changed since snapshot` one file higher than before the edit |
| Binary files and the text search | `fano` holds 151 PDFs, 64 MiB. Counted against §7.3's 64 MiB bound they put **every** answer over it (`not checked 312 changed files were not searched`) until the first re-index, and would again whenever the submodule's commit moved. A file with a NUL in its first 8 KiB now counts neither against the bytes nor as searched |
| A Python file at a root (§6.1), 2026-10-02 | This repository's three (`setup.py`, `rewrite_spec.py`, `squash_todays_commits.py`) and `fano`'s three, each run in the sandbox of §9.1: 0.6–1.3 s each, except `fano/make_figures.py` (278 definitions, 43,144 occurrences), 87 s and 1,050 MiB (the peak measured outside the sandbox). `refs fano/make_figures.py:44` (`green_diag`) went from no exact row to 14 exact and 2 heuristic; `outline fano/main.py` and `setup.py` answer from their own stores. The six runs add about 95 s of background work to every index run of this repository, nearly all of it that one file |
| How the runs of 2026-10-02 below were made | Through the bwrap command line the plan builds, as the tests run it, with the store installed as the supervisor installs it: **not** through `ling-code index --exact --wait`. The model server was serving three or more requests all afternoon (other work on this machine), and the one run tried through the supervisor waited its ten minutes and was recorded `deferred: busy`, as designed. So these rows have times and answers but no scope, no admission and no recorded peak. Java and .NET used toolchains unpacked into scratch and an install directory of their own (`MIGHTLING_CODE_TOOLS_DIR`, `MIGHTLING_CODE_INDEXERS_DIR`), filled by `CodeIndexSetup`'s own code; this machine's install was not changed and still has neither |
| Rust in an included submodule (§6.2) | A scratch superproject `acme/super` with the submodule `vendor` (yours, same namespace; crates `geo` and `util`, `geo` depending on `util` by path), the superproject trusted in a scratch `$CODEX_HOME`: `rust-analyzer:vendor/geo` and `:vendor/util` 3.3 s each, `callers area` → `exact vendor/geo/src/lib.rs:6  in volume`, `def helper` exact from `vendor/tool.py` (a root-level file in the submodule), and `git status` in the submodule empty afterwards |
| scip-dotnet 0.2.14 on .NET SDK 8.0.425 | **As first planned** (scip-dotnet's own restore): exit 0 and a usable index, but the restore had failed, `error NU1100: Unable to resolve 'Microsoft.WindowsDesktop.App.Ref (= 8.0.31)' for 'net8.0'`, and MSBuildWorkspace reported the project as failed. **With Mightling's restore** (§9.1): the `dotnetgeom` fixture (a console project, two files) restored and indexed in 3.1 s; `refs MakeDisk` 2 exact rows, `callers Disk.Surface` 1, `impl IFigure` 2 (`Circle`, `Disk`). A project referencing Newtonsoft.Json 13.0.3, present in the user's package folder: restored from that folder alone in 3.2 s, and `JsonConvert.SerializeObject` resolves to `scip-dotnet nuget Newtonsoft.Json 13.0.0.0 …`. A package on no disk: `error NU1101`, the run fails as `offline` in 1.8 s and writes no index |
| scip-java 0.13.1 on Temurin JDK 21.0.12 | **First run:** `Error creating cache directory … /home/user/.cache/coursier: Read-only file system` (the launcher's cache, §9.1), then `Cannot run program "mvn"` (Maven was in scratch, not on the sandbox's `PATH`). **After both fixes:** Maven 3.9.9, a four-file project, its plugins in the user's `~/.m2/repository` from one earlier online build: 3.3 s offline through the read-only tail; `refs makeDisk` 2 exact rows, `callers Disk.surface` 1, `impl Figure` 2. Gradle 8.10.2 with a dependency (commons-lang3 3.14.0) in the user's `~/.gradle/caches`: 6.4 s, `BUILD SUCCESSFUL`, the dependency read through `GRADLE_RO_DEP_CACHE`. Neither needed scip-java's plugins from a repository: its compiler plugin is in the launcher and reaches the build through a `javac` wrapper (Maven) or an init script (Gradle) |
| Wrappers (§9.1) | `gradlew` with no Gradle installed: `UnknownHostException: services.gradle.org` as first planned; with the distribution copied from the user's `~/.gradle/wrapper/dists` (146 MB, once), 6.5 s. The same project with the distribution absent and Gradle installed: the wrapper removed from the copy, `gradle` used, 5.9 s. `mvnw` (script-only wrapper 3.3.4, `apache-maven-3.9.9`) with no Maven installed: 3.2 s |
| Nothing cached | Maven: `Cannot access central (https://repo.maven.apache.org/maven2) in offline mode and the artifact org.apache.maven.plugins:maven-clean-plugin:jar:3.2.0 has not been downloaded from it before`. Gradle: `repo.maven.apache.org: Temporary failure in name resolution`. Both are recorded `failed: offline`; the remedy is one build of the project by the user |
| `impact` (§7.1) | `impact resolve_model_hf_repo`, three levels: 141 rows (98 exact) in 21 files, p50 0.39 s; `impact VLLMServerManager.check_health --depth 2`: 24 rows |

### 14.2 Where the code differs from the text above

- **One store per run** (§6.2, §7.5): `scip/<indexer>-<root>.db` and `.scip`, with `manifest.json` keyed `<indexer>:<root>`, not a single `index.db`. A definition's references are gathered from every store that has a document at its location.
- **Documents outside a root** (§6.2): scip-python writes documents for what a root imports (`../dreamference/…` in the `tests` store). Paths are normalised back into the repository, snapshots stamp every file (not only the root's), and changed sets are computed without a root scope.
- **The changed set of an answer** (§7.3) is the graph's plus that of the stores holding the symbol, not the union over all stores: a stale Rust index no longer widens a Python name's text search and count.
- **Freshness cost** (§7.3): `git diff`, `git status`, `git ls-files` (for the graph's not-indexed files), `git submodule status` and two `git rev-parse`, not two calls; still 0.09–0.12 s per answer.
- **No `scip` crate** (§3, §7.5): a small protobuf wire reader decodes the chunks, streaming, instead of parsing a whole `Index`.
- **`expt-convert` stores no relationships and scip-python and rust-analyzer leave `display_name` empty.** Post-processing adds `puffin_names` (each symbol's descriptor name) and `puffin_relationships` (read from the `.scip`), excluded from the fingerprint. rust-analyzer emits no relationships at all: `impl` for Rust reads the `impl#[Type][Trait]member` symbol strings. Roles come from the decoded occurrences; `mentions.role` is a per-chunk set.
- **`scip-repair`** (new): scip-python wrote definitions without `SymbolInformation` for this repository's `tests/`, and `expt-convert` stops on the first. `ling-code scip-repair` adds the missing entries (every other byte kept) inside the sandbox, before conversion.
- **Stores are switched from WAL to rollback journal** after conversion: a WAL database cannot be opened read-only where its directory is not writable (the read-only sandbox). Old `-wal`/`-shm` files are removed before a new store is renamed into place.
- **Identity** (§7.4): candidates come from the SCIP stores as well as the graph (a name the graph lacks, in a submodule or an ignored file, still resolves); a query may name a definition as `path:line`; an identity found by name keeps its references `exact` (only the definition is `heuristic`).
- **Only executing runs are frozen** (§9.2): a frozen codebase-memory missed its daemon's 30 s start-up deadline and failed; the universal and static runs take seconds and the slice's CPU limit bounds them.
- **Only a killed run records its cap as its peak** (§6.4): a run that failed otherwise kept a cap-sized "peak" whose need exceeded its ceiling, and was never admitted again.
- **The sandbox also hides `/run`, `/var/tmp` and `/dev/shm`** (§9.1): the Docker socket and the user's systemd bus are there, and a Unix socket on a read-only bind is still connectable. codebase-memory's own cache directory is its one writable bind outside scratch.
- **Detection** (§6.1): every top-level directory holding tracked `.py` files is a scip-python root (not only packages; untracked directories are the user's); a crate that inherits from a workspace elsewhere is not a root.
- **A file as a root** (§6.1, added 2026-10-02): with a file as its target, scip-python makes the file itself the project root and writes its document with an empty `relative_path`, which `expt-convert` refuses ("relative path must not be empty"). `ling-code scip-repair`, which already runs between the two inside the sandbox, gives a path-less document the path `.`: the file relative to itself. The run's prefix is `<file>/`, as for a directory root, so the same normalisation that maps `../dreamference/a.py` back into the repository maps `.` to `setup.py` and `../pkg/__init__.py` to `pkg/__init__.py`; nothing else knows a root can be a file. One thing did have to learn it: the run's systemd unit is named after its root, and a script may be called `plot (v2).py`. A unit name takes only ASCII letters, digits and `:-_.`, so every other character becomes `-` in the unit (`systemd-run` refuses the name otherwise); the store and the manifest keep the file's own name. A scope named `…-scip-python-setup.py.scope` was created for real to check that the dot is accepted.
- **The header groups its sources** (§7.2): `exact = SCIP scip-python dreamference, tests, setup.py, fano/main.py and 9 more @ c673c5e; rust-analyzer ling-code-rs @ e240c2d`. Stores are grouped by indexer and snapshot commit and at most four roots are named per group. With a store per root file, naming every store on every ambiguous answer had grown to fifteen entries on this repository.
- **Language indexers** (§6.1, added 2026-10-01): detection, the commands and the sandboxes are as §6.1 and §9.1 now describe. Each `Target` carries an `on_demand` flag (scip-go, scip-clang) and a project file (the compdb, the solution or project). One function, `Tools::unavailable`, says why an indexer cannot run (no scip CLI, no Node.js, no recorded toolchain, no arm64 build), and both the plan's skipped list and `ling-code status` use it: status prints `exact: <indexer> for <root>: <why>` for every detected language without a store.
- **Module paths split on `/` as well as `.`** (§7.4): scip-go's package namespace (`` `example.com/m/shapes` ``) and scip-typescript's file namespaces (`` src/`shapes.ts` ``) become segments, so `shapes.Circle.Area` and `Disk.surface` resolve.
- **Failure classes** (§6.4's records): Go's `module lookup disabled by GOPROXY=off`, Maven's and Gradle's offline-mode errors, resolver failures and NuGet's NU1100/NU1101/NU1102/NU1301 read as `offline`; a `go.mod` asking for a newer Go than the recorded one reads as such; `Cannot run program "mvn"` (or `"gradle"`) reads as the build tool not being installed, with the remedy. Cargo's offline marker is `--offline was specified`, not the bare flag: scip-java echoes its Maven command line, which carries `--offline` on every run, and until 2026-10-02 any failed Maven build, a compile error included, was recorded `failed: offline`.
- **Tools** are resolved from Mightling's install directories only; scip-python runs on the `node` recorded at setup.
- **Executing indexes refresh rarely by design** (§6.3): until 2026-10-03 a session re-ran them only past `code_index_stale_commits` or on `ling-code index --exact`, so after an ordinary session this repository's Rust stores were about ten commits behind; the idle trigger (below) now catches up when the model is quiet. Their answers stay complete through the text search; `ling-code index --exact` refreshes them.
- **State on disk**: this repository's `.dreamference/scip/` held about 30 MB of stores and logs, and codebase-memory keeps one database per indexed repository in its cache, including throwaway ones. `ling-code forget` removes a repository's; nothing prunes the cache yet.
- **Submodules are read from `.gitmodules`** (§4.3 names `git submodule status`): one `git config -f .gitmodules` per declaring repository, and a submodule is checked out when its directory has a `.git`. The policy runs on every query, uncached by design, so its cost is a query's cost (§14.1).
- **An included submodule's changed files come from git inside it** (§7.3 speaks only of the superproject's git, which sees a submodule as one entry): its own `git status`, and the diff between the commit its parent recorded at the snapshot and what is checked out. Where that cannot be told (the old commit is not in a shallow clone, or the submodule is nested and the snapshot's commit is the superproject's), every file of it is a candidate, each confirmed against the layer's hashes.
- **Executing indexers inside an included submodule run on the submodule's own trust** (§4.3, added 2026-10-02): `Target.submodule` names the submodule a root is in, and one function, `untrusted_reason`, decides for `ling-code index`, `status` and the launch-time rule alike: the superproject's trust for its own roots; for a submodule, its own entry in Codex's trust table, or the superproject's trust when it passed both ownership tests with a namespace to compare. rust-analyzer then runs on the scratch copy of §6.2; scip-java and scip-dotnet already ran on one. The copy step runs git inside the sandbox, so the git directories it reads (a submodule's is in its superproject's `.git/modules`, a linked worktree's in the main checkout) are bound read-only for every run that copies. The session's launch-time rule (§6.3) counts a trusted submodule's executing roots with the superproject's.
- **The prompt's submodules line gives way to a count** when the names would not fit: three long paths would take the block past its 250-token budget, so above 110 characters it reads `Submodules not indexed: N (\`ling-code submodules\` lists them)`. Answers always carry the names.
- **`impact`** (§7.1): breadth first to `--depth` (default 3, at most 6), at most 200 definitions followed, and the answer says when it stopped. Rows are in the order found, nearest first, each tagged by the weakest link of its chain. A text hit is listed at the level it was found, not only at depth 1, and never followed. `impact --diff [<rev>]` starts from every definition a `git diff -U0` hunk falls in or begins in, at most 25 of them; its files are changed files by definition, so those definitions are located by the index's line numbers and the answer says so.
- **The idle trigger** (§6.3, added 2026-10-03): `ling-code session` probes the model server every 30 s and, with nothing pending and no run in flight, asks for the executing indexers itself when the server has been absent for two probes in a row (a `server start` passes through a moment with nothing answering and no container yet) or has served nothing for 10 minutes. It does so only when a root the session may index is behind `HEAD` at all, not by more than `code_index_stale_commits` as at launch: with the model quiet, one commit is worth a run. It asks at most once per `HEAD` in a session, because a failed or deferred run records its status but no commit, so its index stays behind and would otherwise be asked for on every probe; and never within `code_index_min_interval_s` of the last run, which an explicit exact request is allowed to skip. The admission of §6.4 is unchanged, so beside a resident model a Rust run of this size is still deferred for memory; a stopped server is the moment it is admitted. The probe also runs while a supervisor works, so the idle clock never spans a stretch nobody watched. Unit-tested on the trigger's decision (`session.rs`); not watched live.
- **The Night Shift refresh** (§6.3): `ling-admin night run` calls `ling-code index --exact --wait` once per repository with queued tasks, after admission and before the first task, for at most `[night] index_timeout` (20 min) or half of what is left of the window. A refresh that times out stops **every** scope of `mightling-index.slice`: admission refuses to start a night while any index scope is live, so they are the night's own.
- **The context engine is the MCP fallback, not retired** (§8): `workspace_search_code` answers from `ling-code search --json` when the workspace has an index, and from the engine when `ling-code` is not installed, there is no index, or the call fails. §10's evaluation has not been declared passed, which is §8's condition for retiring it.
- **Test seams**: `MIGHTLING_CODE_GRAPH_DB`, `MIGHTLING_CODE_PROJECT`, `MIGHTLING_CODE_STATE_DIR`, `MIGHTLING_CODE_ROOT`, `MIGHTLING_CODE_TOOLS_DIR`, `MIGHTLING_CODE_INDEXERS_DIR`, `MIGHTLING_CODE_SCRATCH_DIR`, `MIGHTLING_CODE_SELF`; the tests also cut `DBUS_SESSION_BUS_ADDRESS` and `XDG_RUNTIME_DIR`, so none of them can reach the user's systemd. conftest refuses a mutating `systemctl` or any `systemd-run`, as it does `docker`.

### 14.3 Not built

- The fallback converter and the CLI fallback ladder (§4, §7.5): an unknown schema is reported, naming `ling-code index` or `ling update`, and the other layer answers. Both tools are pinned by checksum and released with `ling-code`, so a mismatch can only follow an upgrade of one without the other; a converter needs a whole-`.scip` decoder and a second store layout in the router.
- scip-java and scip-dotnet through the supervisor (a systemd scope, admission, the recorded peak): the runs of §14.1 went through the sandbox's command line directly. scip-clang on linux-arm64 (no build); search by meaning (§4).
- Retiring the `dreamference` context engine (§8): it is still the MCP server's fallback (§14.2).

### 14.5 Added on 2026-10-01, after the first build

| Piece | Where | Tests |
|---|---|---|
| Submodule policy (§4.3): the two tests, the decision table, the size guard, nested submodules, the user's choices in `$CODEX_HOME/ling-code.toml`, `ling-code submodules [include\|exclude\|auto <path>]` | `ling-code-rs/src/submodules.rs`; wired into `changed.rs` (the git view), `index/mod.rs` (`.cbmignore`), `index/plan.rs` (detection inside a submodule), `router.rs`, `output.rs`, `prompt.rs` | `tests/submodules.rs`, 16 tests on real git repositories with local remotes that are only names: yours and a fork under your namespace; another organisation; no remote (authorship alone, no inherited trust); the size guard, and that it is not read from a file in the repository; nested submodules; not checked out; include, exclude and auto, with the file under the home directory and nothing in the repository; a read-only `$CODEX_HOME` refusing the choice and still listing; a choice file planted in the repository ignored; the line on every answer, the prompt block and status; `.cbmignore` rewritten from the policy, an edit to it undone, an old block replaced; `--include-submodules` refused; edits, new files and a moved commit inside an included submodule; detection inside one. Unit tests: URL forms, credentials never printed, public mail domains, the decision table |
| `impact` (§7.1) | `router.rs` (`impact`, `impact_of_diff`), `main.rs`, `mcp.rs` (`code_impact`), `prompt.rs` | `tests/router.rs`: callers of callers on the fixture, `--depth 1`, a text hit listed and not followed, `--diff` from a real edit, a diff that touches no definition, the two usage errors; a unit test of the hunk parser |
| Binary files outside the byte bound (§7.3) | `textscan.rs` | a unit test: a 4 KiB PDF beside 11 bytes of text under a 1 KiB bound |
| Night Shift refresh (§6.3) | `dreamference/night_shift/night_shift_index.py`, `NightShiftRunner.refresh_indexes` | `tests/test_night_shift.py`: once per repository and before the first task's branch exists; switched off; `ling-code` not installed; a refused admission indexing nothing; half the remaining window; the report line; the installed binary only |
| MCP re-point (§8) | `dreamference/mcp_server/code_index_search.py` | `tests/test_mcp_server.py`: the router's rows returned without building the engine, the query passed as separate arguments; the fallback when not installed, on exit 3, on a failed call and on output that is not JSON |

Not run: `ling-code submodules include` typed inside Codex's own sandbox (the refusal is tested with a read-only `$CODEX_HOME`, which is what the sandbox makes it); a Night Shift refresh in a real night; `impact` through the model.

### 14.4 §12, covered and not

| §12 item | State |
|---|---|
| Router merging, de-duplication, ambiguity, a changed set requesting a re-index | covered |
| Recorded fixtures, schema fingerprints (a changed schema is refused) | covered |
| Direct reads during a write (busy timeout); `mutation_gen` retry | busy wait covered; the retry is implemented, not tested |
| Join by location, 0-/1-based lines | covered through the fixtures |
| Changed since the snapshot: edited, new, committed, stale for SCIP but fresh for the graph, deleted, `not checked`, lost commit, read-only sandbox | covered |
| Hostile build script, no writable bind outside scratch, tmpfs before binds | covered |
| Killed at the memory cap | not testable without a real scope (§12's own rule); evidence: the 8 GB kill of 2026-09-28 and the capped runs of 2026-09-30 (§2) |
| `failed: offline` | log classification covered; no fixture run |
| Untrusted repository gets no executing index, whatever it ships | covered, for rust-analyzer, scip-java and scip-dotnet (a Gradle and an MSBuild marker never written) |
| Language detection and root rules; every new run's sandbox writes only to scratch; Go offline environment; TypeScript's inferred configuration in scratch; JVM and .NET on a scratch copy | covered (unit tests on the argument lists) |
| Executing indexers in a submodule: detection, trust inherited or by its own entry or refused, the reason printed by `submodules` and `status` | covered, on real git repositories; one real rust-analyzer run of a crate with a path dependency and a build script that writes beside its manifest, from the scratch copy, with the checkout unchanged |
| scip-java and scip-dotnet for real | a Gradle project with no dependencies and a .NET console project, each built offline in a copy and read back through the store, and a NuGet package on no disk failing as `offline` with no index written. Skipped without the toolchains, which this machine does not have installed: run on 2026-10-02 against the scratch install (`MIGHTLING_CODE_TOOLS_DIR`, `MIGHTLING_CODE_INDEXERS_DIR`) |
| Maven and Gradle recorded at setup; a missing build tool named; a Maven failure that is not `offline` | covered |
| scip-typescript writes nothing into the tree and runs no package script | covered, run for real |
| scip-go offline: an uncached dependency, a `go 1.99` line refused | covered, run for real when scip-go and Go are installed (or named by `MIGHTLING_CODE_TEST_SCIP_GO` and `MIGHTLING_CODE_TEST_GOROOT`); skipped otherwise |
| TypeScript answers through the router (refs, callers, impl, def, an edit since the snapshot) | covered, against the recorded `tsgeom` store |
| scip-python's environment | covered, run for real |
| A Python file at a root: detection, the cap, the path-less document | covered; one real run of a root file that imports a package and another root file, installed and read back through the store |
| Session: lock, parent exit, junk requests, no systemd bus | covered |
| Admission: floor, headroom of live scopes, racing admissions, one executing run, slice limits, `choom` | covered with a fake host |
| Scheduler: coalescing, idle gauges of both engines, freeze and thaw | gauges, freeze, thaw and never freezing a short run covered; coalescing implemented, not tested |
| Model load: no start while loading, stopped not frozen, `server start` stops the scopes | covered |
| Worktrees | covered |
| Output budget, stable pages, refused stale cursor, answer size | covered |
| Launcher: no `ling-code`, no block | covered, in the Codex export (`cargo test -p ling-launcher`, 33 tests); run live: `ling exec -s read-only` started `ling-code session --parent-pid <ling>`, the catalog carried the `# Code navigation` block, and the model ran `ling-code refs` inside the read-only sandbox and read its answer |

---

## 15. Getting the index used (2026-10-02)

**The finding.** The index was close to unused: the agent queried it in none of 24 SWE-bench instances that offered it, and a parse of the 146 sessions recorded on this machine found one real query. It was not a wording problem alone. Four causes, in the order they mattered:

1. **No MCP tool ever reached the model.** Codex declares an MCP server's tools as one `{"type": "namespace"}` entry, a tool type of the Responses API. SGLang and vLLM render only `{"type": "function"}` entries into the prompt and drop the rest silently. A recorded request showed `mcp__code` declared; the model, asked to call `code_def`, answered that no such tool exists. This holds for **every** MCP server configured in `ling`, the user's own included.
2. **Codex's prompt teaches the opposite habit:** "When you search for text or files, you reach first for `rg`". And `rg` is not installed on the GB10 or in the SWE-bench images, so sessions began with a failed command.
3. **The prompt block said what the index offers, not when to use it.** Its one instruction, "before changing a signature, renaming or deleting, run `ling-code refs`", never applies to a bug fix.
4. **`search` could not answer a task phrased as a symptom.** It matched names and documentation only (codebase-memory's full-text table), so the words of a bug report ("wall time", "until") found specs, not code.

### 15.1 What changed

| Change | Where |
|---|---|
| The index is offered as tools for the session: the launcher adds `-c mcp_servers.ling_code.{command,args,env_vars}` (arguments, not `config.toml`, so it is per session and per repository); a `-c mcp_servers.ling_code…` of the user's wins; `mightling_code_tools = false` or `DREAMFERENCE_MIGHTLING_CODE_TOOLS=0` turns it off | `ling-rs/src/code_index.rs` (`with_tools`, `tools_enabled`) |
| MCP tools reach the model as plain functions, and a call is mapped back to its server (`code_def`, or `<namespace>__<name>` on a clash) | crate `ling-rs/tools`, patch `0020-flat-mcp-tools` (two one-line hooks in core, 1,250 bytes; cap raised to 32,500) |
| Every `code_*` tool carries `readOnlyHint`, so Codex's `auto` approval runs it without asking (without it `ling exec` refused every call: "MCP tool call requires approval, but approval policy is never"); descriptions say when to use each, `code_search` first | `ling-code-rs/src/mcp.rs` |
| The `rg` sentence of Codex's prompt is rewritten to name the index for code and `grep -rn`/`find` when `rg` is absent; a test fails if a Codex bump removes the sentence | `code_index::search_habit` |
| The prompt block opens with when to use the index (a symptom: `search`; a name: `def`/`show`/`refs`; before a change: `impact`), in a shell variant and a tool variant (`prompt-block --tools`) | `ling-code-rs/src/prompt.rs` |
| `search` also reads bodies: a definition holding at least half of the query's words (all, for one or two) is listed, ranked by the rarity of its words and by its best line, tests below code, documentation sections last | `Context::search_bodies` |
| The launcher asks `ling-code` about the session's directory (`-C`/`--cd`), not its own: `ling exec -C <tree>` started elsewhere, as Night Shift and SWE-bench do, got no block and no tools | `code_index::session_dir` |
| SWE-bench counts a `code_*` tool call as an index query | `swe_bench_run_store.py` |

### 15.2 Measured

Twelve navigation tasks on this repository (find a definition and its callers, plan a parameter change, rename, trace a status flow, fix a symptom-phrased bug) and three text tasks where `grep` is right, one `ling exec -s workspace-write` each, the launcher bypassed so each arm's prompt is exactly what it says. **Used** = the index was queried at least once; **first** = it was the first search; **text** = text tasks that queried it (should be 0).

| Arm | Used | First | Index / grep calls (nav) | Text |
|---|---|---|---|---|
| As shipped before (shell commands, old block) | 6/12 | 5 | 7 / 51 | 0/3 |
| The same, repeated (14 of 15 tasks finished) | 6/12 | 6 | 9 / 40 | 0/2 |
| Reworded block (v1) | 9/12 | 9 | 17 / 39 | 0/3 |
| `rg` sentence rewritten only | 7/12 | 7 | 10 / 46 | 0/3 |
| Block moved to the top of the prompt | 6/12 | 6 | 10 / 51 | 0/3 |
| MCP server configured, unflattened (as any `ling` today) | 6/12 | 6 | 11 / 33 | 0/3 |
| Reworded block (v2, "first step whenever you need to find code") | 9/12 | 9 | 22 / 31 | 0/3 |
| v2 as a developer message instead of in the system prompt | 10/12 | 10 | 15 / 45 | 0/3 |
| Tools flattened, tool block as a developer message | 9/12 | 8 | 29 / 12 | 0/3 |
| Tools flattened, old block, old `search` | 10/12 | 9 | 30 / 30 | 0/3 |
| Tools flattened, no block at all | 7/12 | 6 | 17 / 26 | 0/3 |
| **Tools flattened, tool block, body search (shipped)** | **12/12** | **11** | **43 / 15** | **0/3** |

- **Through the shipped binary** (the launcher and patch 0020 compiled in, no proxy, Codex's default `auto` approval, `ling exec -C <tree>` started outside the tree): of the five navigation tasks the earlier arms missed most (n03, n05, n10, n11, n12), the index was the first search in four, with 24 index calls against 18 greps; 0 of 2 text tasks used it. The fifth, n10, starts from the option's literal text, where `grep` is the right tool. The same run before the `-C` fix gave 0 of 5: the launcher had asked `ling-code` about its own directory.
- **Each arm ran once,** apart from the baseline, whose repeat landed on the same 6/12: that is the noise floor, and moving the block, the `rg` sentence alone and the unflattened MCP server are within it. What clears it is the flattened tools together with the new block and body search.
- **Wall time and tokens are not comparable across arms.** Other tasks were using the model server throughout (5 to 7 concurrent requests during the tool arms), and 8 of the shipped arm's 12 tasks hit the 420 s harness limit mid-task. Their first steps, which is what the table counts, completed.
- **The one task where the index did not come first** (n10, a crash on `--until 25:00`) began with a grep for the option's text, which is the right first step for a string; it used the index afterwards.
- **Body search on its own:** `search wall time` found `NightShiftReport._task_block` (it prints "wall time"), where it found only spec sections before; `search night run until crashes traceback` ranks `NightShiftRunner.run` and `window_end` in its top four. 0.2 s on this repository.

### 15.3 Consequences and limits

- **Every MCP server now reaches the model**, not only `ling-code`: a user's `[mcp_servers.…]` (here, `jcodemunch`) appears in every session. Its tools need approval under `auto` unless they carry `readOnlyHint`: the TUI asks; `ling exec` refuses them.
- **The MCP server runs outside the sandbox**, as Codex starts every MCP server. Queries only read (§4), so this changes no permission, but it is a departure from "queries run inside the sandbox"; at `/airgapped on` it is unaffected, having no network code.
- **Not measured yet:** SWE-bench with the tools (the run of §12 offered only the shell commands); interactive TUI sessions over days.

### 15.4 The tools on SWE-bench (2026-10-03)

The first SWE-bench arm with the tools (SWE_BENCH §13.6) found one defect in how they reach the model and two in their answers.

- **The tools could be missing from the model's tool list.** Codex waits 1 s (`mcp_optional_startup_grace_ms`, default 1000) for an optional MCP server before the first request, then leaves out the tools of one that is not up (`codex-mcp/src/connection_manager/tool_catalog.rs`, `must_wait_for_startup`). With three containers starting at once, `ling-code mcp` missed that second: the model, told to use `code_search`, called it anyway, got `unsupported call: code_search`, and used grep for the rest of the task (2 of 3 instances read before the run was stopped). A `ling exec` probe in an idle container listed the tools and called them, which is why the 12 navigation tasks of §15.2 never showed it. The benchmark runner now declares the server itself with `required = true` (a required server is waited for; `5e7a134`), and the launcher passes a 15 s grace with its own declaration (`6ffb18f`; `required` is not used there because a required server that fails to start ends the session). **The defect showed under the benchmark's load, not in an ordinary session.** Checked on 2026-10-03 on this GB10 with the model resident: three `ling exec` sessions with the build of 2 October (1 s grace), run while a Codex build loaded the CPU, each called `code_search` as its first action and got an answer (no `unsupported call`); three more with the build of `258c3b5` (15 s grace, `6ffb18f` compiled and its launcher tests passing, 138) did the same. So the 15 s grace is a margin for a loaded host, not a fix for every session, and its effect on a normal session is not measurable here.
- **An absolute `path` matched nothing.** The model passes the path it sees (`/testbed/astropy`); rows carry repository-relative paths, so a search with results answered "0 results" (3 of 263 calls). `path` is now made repository-relative (`93386d5`).
- **`refs`, `callers` and `impact` answered 0 for methods the repository calls** (10 of 263 calls): a call through an attribute names no type the graph can resolve, and the note said to confirm with `rg`, which is not installed in the images or on the GB10. When no exact layer covers the definition, whole-word matches of the name in tracked files of its language are now added as `heuristic (text)` rows from `git grep` (at most 100, the rest counted); `impact` lists them and does not follow them (`93386d5`). Checked by hand in a container: `refs get_related_updates` went from 0 rows to the one real call site.

The two `ling-code` fixes were made after the arm started and are **not measured**: the arm ran the installed `ling-code` (`a2343be2…`).

