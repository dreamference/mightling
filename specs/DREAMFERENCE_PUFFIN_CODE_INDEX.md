# Puffin Code Index — codebase-memory-mcp + SCIP

**Status:** proposed. Nothing in this spec is implemented yet. The design below was revised on 2026-09-30 after measuring both layers on this machine (§2): several assumptions of the first draft did not survive contact with the tools.
**Target:** the `puffin` terminal agent. The same index is also offered over MCP to Claude Code and to IDEs.
**Builds on:** the launcher in `puffin-rs/`. It handles `puffin app` and `puffin update` before Codex parses argv, and `puffin code` would be handled the same way. It also builds on the `puffin-search` / `puffin-admin gmail` pattern of giving the local model shell commands rather than MCP tools, and on the builder's rule that nothing ever runs Cargo inside the `codex/` submodule.

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

A router in `puffin-rs` answers each question from the better layer. It tags every result `exact` or `heuristic`, and says what it could not resolve or did not index.

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
| `defn_enclosing_ranges` in the store | maps a reference to its innermost enclosing definition (line 2007 of the CLI controller → `DreamferenceCLIController#run_cli()`), 0-based lines | measured |
| SGLang's idle gauges | `sglang:num_running_reqs`, `sglang:num_queue_reqs` (0 when idle), on `/metrics` of the served model | measured |
| Licences | codebase-memory-mcp MIT; scip CLI, scip-typescript, scip-clang, scip-java, scip-go, scip-dotnet Apache-2.0; scip-python MIT (Pyright's); all maintained (pushed within the last month) | verified |
| Decode rate while indexing, frozen/unfrozen scopes, scip-typescript cost, codebase-memory's search by meaning quality | — | **unmeasured** (§10) |

Consequences for the design, each carried into the sections below:

1. **No per-query CLI.** At 6.6 s a call, the CLI cannot serve queries. The router reads codebase-memory's database directly (§7.5), with a fallback ladder.
2. **Exact layer by language, not by trust alone.** Static indexers (scip-python, scip-typescript) run for every repository; executing ones (rust-analyzer, scip-java, scip-dotnet) need trust and admission.
3. **Submodules are excluded by default** (97% of this repository's universal index was the `codex/` submodule), and indexed deliberately when wanted.
4. **Admission from measured numbers:** the Codex exact index needs more than 21 GiB and runs only when the machine can spare it.
5. **The query store is the `scip` CLI's own SQLite export**, pinned and fingerprinted, with our converter as the fallback.

## 3. Components

| Component | Version pinned | Licence | Role |
|---|---|---|---|
| [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | v0.11.0 (2026-09-15), `linux-arm64` | MIT | Universal layer: builds the graph (158 tree-sitter grammars, "Hybrid LSP" resolution for 13 languages), keeps it incrementally, and serves search by meaning when a session asks for it. Configured through `CBM_CACHE_DIR`, `CBM_RUNTIME_DIR`, `CBM_MEM_BUDGET_MB` (a hard budget since v0.11.0), `CBM_WORKERS`, `CBM_ALLOWED_ROOT`, `CBM_SEMANTIC_ENABLED`, and a per-repository `.cbmignore`. |
| [scip CLI](https://github.com/scip-code/scip) | v0.10.0 (2026-09-03), `scip-linux-arm64.tar.gz`, sha256 `6ab677dc2c4bf2955975d0530766152e45daaa988f9404068d8adecacd0bb24c` | Apache-2.0 | `expt-convert` builds the query store (§7.5). |
| `scip` Rust crate | 0.10.0 | Apache-2.0 | Decodes the occurrence blobs of the store in `puffin-rs`. |
| `rust-analyzer scip` | from the pinned toolchain (1.95.0) | MIT/Apache-2.0 | Exact Rust layer. Executes build scripts and proc-macros (§9.1). |
| scip-python | 0.6.6 (npm `@sourcegraph/scip-python`) | MIT | Exact Python layer. Static (Pyright). |
| scip-typescript | pinned at setup | Apache-2.0 | Exact TypeScript/JavaScript layer. Static. |
| scip-clang, scip-java, scip-go, scip-dotnet | pinned at setup | Apache-2.0 | Exact layers for C/C++, JVM, Go and .NET (§6.1). |

Rejected, with the reason:
- jCodeMunch: its licence forbids renaming, modified redistribution and commercial use.
- GitNexus: PolyForm Noncommercial.
- codegraph: MIT, but has telemetry on by default and no search by meaning.
- Serena: GPL-3.0, heavier, and has no graph or search by meaning. Its MIT SolidLSP layer remains an option if a live language server is ever needed; note that a live rust-analyzer on a large workspace has been reported at 40+ GB (serena#1556), which is why this design uses rust-analyzer only as a batch indexer under a cap.
- The `dreamference` context engine (TF-IDF + FTS5 + file embeddings): kept until the router passes §10, then retired (§8).
- The full survey is in the session record of 2026-09-28.

## 4. Architecture

```
            agent (puffin, local model)          Claude Code / IDE
                      │ shell                          │ MCP (stdio)
                      ▼                                ▼
        puffin code <cmd>  ─────────────  puffin code mcp
                      │   (same router, puffin-rs/src/code_index/)
          ┌───────────┴─────────────────────────┐
          ▼                                     ▼
  codebase-memory graph (SQLite)          SCIP query store (SQLite)
  read directly, read-only (§7.5)         .dreamference/scip/index.db
  written only by its indexer, run        built by `scip expt-convert`
  detached at launch / on demand          after each exact run (§6)
```

- **Both tools stay unmodified.** codebase-memory is a pinned binary that writes its graph; the scip CLI and the indexers are pinned binaries that write SCIP. The new code is the router, the scheduler and the store's small post-processing (§7.5).
- **Queries never start a tool.** A `puffin code refs` opens two SQLite files read-only, runs indexed lookups and exits: milliseconds, and no process survives it. The tools run only to *index*, started by the session thread outside the sandbox (below), under §9's limits.
- **Two sides of the sandbox.** The agent runs `puffin code …` as a shell command, and Codex runs every such command inside its bwrap sandbox (`workspace-write` or `read-only`): the process can read the two databases and write only under the working directory and `/tmp`. Probed with `puffin sandbox` in `workspace-write` mode: reading the graph database works; `~/.cache` is a read-only file system; `$XDG_RUNTIME_DIR` is visible but read-only; `systemd-run --user` fails ("Failed to connect to bus"); and nothing it starts outlives it. So the design splits:
  - **Queries are pure readers.** `puffin code refs|def|callers|…` opens the databases read-only and exits. That is all it may do.
  - **Everything that starts an indexer belongs to the `puffin` process itself**, which runs *outside* the sandbox for the whole session (the launcher is linked into it, patch `0002`). A session thread in it owns indexing: the launch-time runs, the supervisor of §9.2, and a request queue. A query that wants a re-index appends one line to `<repo>/.dreamference/code_index.requests` when it can write there; the session thread drains it every few seconds, coalesces (§9.2) and runs what admission allows. In a `read-only` sandbox the query cannot write the request, so it only says in its header that the index is stale; the next launch refreshes it.
  - **`puffin code mcp`** (for Claude Code and IDEs) runs outside any sandbox and may do both.
- **The fallback ladder**, used only when the direct read is impossible (the graph's schema fingerprint does not match the pinned one, §7.5): the CLI, correct at 6.6 s per call. A session-scoped codebase-memory child (12–105 ms per query) is reachable only from `puffin code mcp`, which can hold it; from inside the sandbox there is no channel to it.
- **Search by meaning** needs the embedding model compiled into codebase-memory, so it is served only by `puffin code mcp`'s session child, and only when `puffin_code_semantic = true`. It is off by default: it returned unrelated build files for plain questions on this repository (2026-09-29), and upstream has open issues on it (#1155, #1462). `search` without it is FTS5 over names, qualified names and bodies, ranked by BM25 (§7.1).
- **Why Rust, in the launcher:** it keeps `puffin` self-contained. `puffin code` works in any shell with no Python environment, like `puffin app` and `puffin update`, and rusqlite plus the `scip` crate are all it needs.

### 4.1 Lifecycle of the universal layer

- **First run:**
  - at `puffin` launch, the launcher checks for the repository's project in codebase-memory's cache (by reading the `projects` table directly, not through the CLI);
  - if there is none, it starts `index_repository` **detached**, under §9's limits, and continues launching. It never indexes in the agent's path.
- **Prompt timing:** the prompt is assembled once, at launch:
  - **Index ready:** the full `# Code navigation` block (§8).
  - **Index building:** a reduced block: "a code index is being built; use `rg` until `puffin code status` reports it ready". `puffin code` answers "index not ready yet" rather than failing obscurely.
  - **No repository, or indexing disabled or failed:** no block.
- **Keeping it current, without a daemon:**
  - an incremental `index_repository` runs detached at every `puffin` launch (8.9 s measured, almost all fixed start-up);
  - it also runs on `puffin code index`;
  - it never runs inside a query. A query whose result files changed since the last index (§7.3) answers at once, tags those rows `heuristic (stale)`, and queues a re-index request for the session thread (§4).
  - codebase-memory's watcher stays off (`watcher_enabled = false`, `auto_watch = false`) unless `puffin_code_watch = true`.
- **Scope, written by the launcher:**
  - **Submodules are excluded.** The launcher keeps a managed block in `<repo>/.cbmignore` listing every submodule path (`git submodule status`), and adds `.cbmignore` to `.git/info/exclude`, so the file never shows up in `git status`. `puffin code index --include-submodules` indexes them deliberately.
  - **Ignore rules inside submodules.** codebase-memory applies the superproject's `.gitignore` inside a submodule, which git does not. When submodules are included, the router reports every excluded subtree from `index_coverage` in `puffin code status`, so the gap is visible rather than silent.
  - **Tracked files an ignore rule matches are skipped** (this repository's `config/` rule drops the tracked `dreamference/config/`). The router lists them from `index_coverage` (`not_indexed_dir`/`not_indexed_file`) and answers queries touching them with `rg`-backed `heuristic` rows.
- **Git worktrees share the main worktree's index.** A worktree (`git rev-parse --git-common-dir` differs from `--git-dir`) is not indexed as a project of its own, which would cost a full database per worktree (Night Shift and fan-out create many). The router maps its paths onto the main worktree's project, and files whose hash differs are stale by §7.3, which is the safe direction.
- **Data:**
  - graphs live in codebase-memory's cache, `CBM_CACHE_DIR` (default `~/.cache/codebase-memory-mcp/`);
  - `puffin code status` reports their disk use;
  - `puffin code forget [<repo>]` deletes a project, and the launcher offers it when a project's root no longer exists.

## 5. Distribution

- **Pinned binaries, verified:**
  - The Puffin release workflow downloads the pinned codebase-memory-mcp and scip CLI releases for `linux-arm64` and checks each against its published checksum.
  - The expected SHA-256s are committed in `puffin-rs/code-index.sha256`, the same trust model as the rusty_v8 archive.
  - They are shipped beside `puffin` in `~/.local/share/dreamference/puffin/bin/` and attached to Puffin releases, so `puffin update` refreshes them together with the router that knows their schema fingerprints (§7.5).
  - `puffin-admin codex build` fetches the same pinned archives for local builds.
- **Fork only when a patch is needed.** No change to codebase-memory is required. If one becomes necessary, for example §11's SCIP import or a fix for its submodule ignore handling, fork it to `dgxcoder/codebase-memory-mcp` as a submodule and build from source, following the Codex pattern: pinned tag, patches in a directory, submodule never edited. The MIT notice must stay in the shipped files.
- **SCIP indexers:**
  - `rust-analyzer` comes from rustup, the toolchain the Codex build already pins.
  - scip-python and scip-typescript are npm packages; `puffin-admin code setup` installs them into `~/.local/share/dreamference/puffin/indexers/`, pinned by version and by the lockfile's `integrity` hashes. They need Node.js; a machine without it keeps the universal layer for those languages and says so in `puffin code status`.
  - The rest are fetched by `puffin-admin code setup` when their language is present, pinned by version and checksum.
  - Nothing is ever fetched at index or query time.

## 6. The SCIP layer

### 6.1 Which indexers run

Detection is by project files at the repository root and in immediate subdirectories. A monorepo can get several.

| Found | Indexer | Executes project code? | Runs |
|---|---|---|---|
| `pyproject.toml` / `setup.py` / `requirements.txt` / `*.py` package | scip-python | **no, when run as below**; by default it runs `pip3` and `python3` from `PATH`, i.e. the repository's venv (§2) | **every repository**, automatically |
| `tsconfig.json` / `jsconfig.json` / `package.json` | scip-typescript | **no** | **every repository**, automatically |
| `compile_commands.json` | scip-clang | no, given the compdb | every repository, on demand |
| `go.mod` | scip-go | no code, but `go list` would download | every repository, on demand, offline enforced |
| `Cargo.toml` (workspace or crate) | rust-analyzer | **yes**: build scripts and proc-macros, always (§2) | trusted repositories, under admission (§6.4) |
| `build.gradle*` / `pom.xml` / `build.sbt` | scip-java | **yes**: the build | trusted repositories, under admission |
| `*.sln` / `*.csproj` | scip-dotnet | treat as **yes** | trusted repositories, under admission |

**scip-python is only static when Puffin controls its environment.** It is run with `--environment <file>` (the package list Puffin writes, empty for an untrusted repository), a `PATH` holding only Puffin's own Node.js and the system `/usr/bin`, and `PYTHONSAFEPATH=1` and `PYTHONNOUSERSITE=1`, so the one Python it still starts (to read `sys.path`) is the system interpreter and cannot import a `sitecustomize.py` from the repository. Measured: no `pip3`, no repository interpreter, identical references. The same `PATH` rule applies to scip-typescript's `node`.

The static indexers run for untrusted repositories too because, run this way, they cannot execute anything from it, and because the universal layer alone gets Python method callers wrong half the time (§2). They still run inside the sandbox of §9.1, with no network. A project that fails to index keeps the universal layer only; the failure is recorded in the manifest, not retried in a loop.

### 6.2 Where output goes

- **Files:** `<repo>/.dreamference/scip/<indexer>.scip`, the query store `index.db` (§7.5), and `manifest.json`: `{indexer, version, commit, path_prefix, dirty_files, file_hashes, started, duration_s, peak_rss_mb, peak_cap_bounded, cap_mb, status}`. `status` is one of `ok`, `failed: <reason>`, `deferred: memory`, `deferred: busy` or `deferred: model-start`. `.dreamference/` is already git-ignored.
- **Paths are relative to the indexed root**, for every indexer: rust-analyzer's are relative to the workspace (`core/src/…`), scip-python's to the `--target-only` directory (`cli/…` for `dreamference/cli/…`). The manifest's `path_prefix` restores the repository path.
- **Never index inside a submodule's checkout with a build tool that writes to it.** rust-analyzer runs `cargo metadata` and build scripts, and even `cargo tree` rewrites a submodule's `Cargo.lock`. This project's `codex/` workspace is indexed from a scratch copy of the builder's export (`~/.cache/dreamference/puffin-codex/src/codex-rs`), never from the export itself, which the builder owns, and paths are remapped to `codex/codex-rs/…`.
- **`CARGO_TARGET_DIR` points at a scratch directory** so indexing never touches a build cache another build is using. The build-script cache it accumulates is what makes the second Rust run ~2 minutes faster (§2).

### 6.3 When it runs

- **Static indexers (Python, TypeScript):** after the universal index at launch when the manifest is older than `HEAD` or files changed, detached; and on `puffin code index`. Cheap enough (19 s for `dreamference/`) to follow every launch.
- **Executing indexers, trusted repositories only (§9.1):** in the background, never blocking a session:
  - when no exact index exists, or it is older than `HEAD` by more than `code_index_stale_commits` commits (default 20);
  - when idle, preferring the moments the model server is stopped (§6.4);
  - in the Night Shift window, before night tasks start, so they begin with a fresh index (see `DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md`).
- **On demand:** `puffin code index --exact`.
- **Before a risky operation:** when `refs`/`impact` touches a symbol whose files changed since the snapshot (§7.3's staleness check). This only *requests* a run; the answer is given immediately, stale rows tagged.
- **Every trigger goes through the scheduler of §9.2,** and every executing run through §6.4's admission.

### 6.4 Memory admission

A fixed cap either kills the run (8 GB did, on 2026-09-28) or has to be sized for the worst repository, taking memory from the model. Executing indexers therefore go through admission control:

- **Reserve:** `code_index_reserve = earlyoom SIGTERM threshold + 4 GiB`, read from earlyoom's `-m` percentage and `MemTotal` (on this machine 6.2 + 4 = ~10.2 GiB). The model's memory is already allocated when it is resident, so `MemAvailable` excludes it; the reserve protects the host, the session and the model's transient allocations. On 2026-09-29 an index run that ignored this pushed the host under earlyoom's line and earlyoom killed vLLM.
- **Cap at start:** `cap = min(code_index_memory_ceiling, MemAvailable − code_index_reserve)`, ceiling default 40 GiB.
- **Admit only a run that can finish:** the manifest's `peak_rss_mb` for this indexer and repository must satisfy `cap ≥ 1.2 × peak_rss_mb`. Otherwise the run is not started: `status: "deferred: memory"`, and `puffin code status` says so.
- **Cap-bounded peaks are marked.** When a run's peak ends within 10% of its cap (`peak_cap_bounded: true`), the recorded peak is a lower bound: the cgroup was reclaiming against the cap. The next admitted run gets `min(ceiling, 1.5 × peak)` if available, so the first uncapped run raises the record. An OOM-killed run records its cap as the lower bound, so the next attempt is not doomed the same way.
- **What this means for Codex today:** recorded peak ≥ 21.5 GiB, cap-bounded, so admission needs `cap ≥ 25.8 GiB`, i.e. `MemAvailable ≥ ~36 GiB`. With Qwen3.8 resident the machine has 35–38 GiB available, so a run is admitted only at the quiet end of that range; with the model server stopped (~100 GiB available) always. Scheduled runs therefore prefer `puffin-admin server stop`, idle periods with no server, and the Night Shift window. While no fresh exact index exists, the router serves the last snapshot, with changed files tagged `heuristic (stale)`.
- **The conversion is admitted too:** `expt-convert` peaked at 2.7 GB on Codex and runs under the same cap right after the indexer.
- **Partitioning, as an option to measure, not a plan:** index the workspace in passes over subsets of `[workspace] members`, merged in the store. The caveat stands: `codex-cli`'s dependency closure is most of the workspace, so the passes that matter may peak almost as high as the whole.

## 7. The router

### 7.1 Operations

| `puffin code …` | Answered by | Notes |
|---|---|---|
| `search <text>` | the graph's `nodes_fts` (FTS5, BM25) over names, qualified names and bodies | `nodes_fts` is contentless (`content=''`): it returns rowids, joined back to `nodes` for names and locations. Semantic ranking only through `puffin code mcp` with `puffin_code_semantic = true` (§4). |
| `outline <file>` | the graph's `nodes` for that file, ordered by line | |
| `show <symbol>` | the definition's `file:start-end` from the graph, read from disk | Returns one symbol's source, not the whole file. |
| `def <symbol>` | SCIP if fresh (§7.3), else the graph | |
| `refs <symbol>` | SCIP for fresh files, the graph for stale or unindexed ones, merged | Tagged per row. |
| `callers` / `callees <symbol>` | SCIP references mapped to their innermost enclosing definition (`defn_enclosing_ranges` in the store, verified §2), else the graph's `CALLS`/`USAGE` edges | For Python and Rust the graph finds 58% and 78% of calling files (§2); graph-only answers carry a header note saying so. |
| `impl <trait-or-interface>` | SCIP `relationships` (`is_implementation`), else the graph's `IMPLEMENTS`/`OVERRIDE` edges | |
| `impact <symbol-or-diff>` | SCIP references, transitive to depth N via enclosing definitions; the graph's edges for stale files | Answers "what breaks". |
| `status` | both | Layers present, freshness, languages covered, excluded subtrees, disk use, last index time, deferred runs and why. |
| `index [--exact] [--include-submodules]` | both | Re-index. `--exact` also schedules executing indexers. |

### 7.2 Output

Output is plain text, compact, one row per result, which is the shape the local model reads best:

```
refs codex_core::config::Config::load   (12 results; 10 exact, 2 heuristic)
exact = SCIP rust-analyzer @ 47f4d81; heuristic = codebase-memory (file edited since)
exact     codex-rs/cli/src/main.rs:1041
exact     codex-rs/tui/src/app.rs:318
heuristic codex-rs/exec/src/lib.rs:77
unresolved 1 call through `dyn ConfigSource` in codex-rs/core/src/lib.rs:2204
```

The source and commit are stated once in the header. `--json` gives the same data for tools. The `unresolved` line is mandatory whenever either layer reports calls it could not resolve, and a `not indexed` line lists touched paths outside a layer's coverage (§4.1). They are what tell the agent it has to verify.

**Every answer is bounded by construction.** The served model's context is 262,144 tokens, so the bound is no longer about fitting the window. It is about cost: every token of tool output is re-read on later turns, and a cold prefill runs at ~1,700 tokens/s (~1,000 at 116K tokens), so a 5,000-token answer costs about 3 s on every later turn the prefix cache misses, and dilutes the model's attention.

- **Row cap:** 40 rows by default (`--limit N`, hard ceiling 200). At 15–20 tokens a row plus the header and at most 15 summary lines, a full answer stays under about 1,000 tokens (§10's acceptance figure).
- **Grouping first, rows second:** above the cap, the answer opens with a per-file summary sorted by count, limited to 15 files. The rows follow, taken from those files.
- **The header always states what was cut:** `refs Config::load (312 results in 41 files; showing 40; next: --offset 40)`.
- **Narrowing flags:** `--path <glob>`; `--kind def|read|write|import` from SCIP roles where the indexer distinguishes them (scip-python marks imports as reads, so `--kind import` is unavailable for Python and says so); `--exact-only`; `--offset N`, a cursor invalidated with an error, not silently shifted, if the index changes between pages (detected by the graph's `store_meta.mutation_gen` and the store's run id).
- **The prompt block is budgeted too:** the `# Code navigation` block of §8 rides on every turn and is kept under 250 tokens.

### 7.3 Freshness and merging

- **A file is fresh for SCIP** when its current content hash equals `file_hashes[path]` in the SCIP manifest; **fresh for the graph** when it matches the graph's own `file_hashes(rel_path, sha256, mtime_ns, size)` table, which codebase-memory already maintains.
- **Only the files an answer touches are checked, and cheaply:** by `(mtime, size)` first, hashed only if either differs. A 40-row answer costs at most 41 `stat` calls in the common case.
- **`refs` and `def`** use SCIP rows for fresh files and graph rows for stale or unindexed ones.
- **Rows present in both** are de-duplicated by `(path, line)`; the SCIP row wins and is tagged `exact`.
- **Stale share:** if more than 20% of the files a result touches are stale, the router *requests* a background run and says so in the header. It never runs one in the query's path.

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

- **Prompt:** the launcher appends a `# Code navigation` block to the model's prompt, next to web access and Gmail. It covers the commands of §7.1, the meaning of `exact`/`heuristic`/`unresolved`/`not indexed`, and one rule: *before changing a signature, renaming or deleting, run `puffin code refs`. If any row is `heuristic`, `unresolved` or `not indexed`, confirm with `rg` and run the build or tests after the edit.*
- **Ready, building or absent:** the block is full, reduced or absent according to the index state at launch (§4.1).
- **MCP:**
  - `puffin code mcp` serves the same operations over stdio for Claude Code and IDEs, and replaces jCodeMunch in `~/.claude.json` once implemented.
  - `puffin-admin mcp`'s `workspace_search_code` is re-pointed at the router; the `dreamference` context engine (per-file TF-IDF, FTS5 and embeddings) is retired once the router passes §10, since it answers a subset of `puffin code search` with no call graph.
  - The local model keeps using shell commands, because it does not reliably call MCP tools under Codex's Code Mode (see `codex_runner.py`'s history).

## 9. Resources and safety

On GB10, host RAM and GPU memory are the same memory, and running it out can freeze the host, not just kill a process (see `psi_watchdog.py`). On 2026-09-29 an unconstrained index of this repository (the old `dreamference` context engine walking 2.3 GB of files) pushed available memory under earlyoom's line and earlyoom killed vLLM. Everything below exists so that cannot recur.

- **Memory limits:** every indexer, universal or exact, runs under `systemd-run --user --scope -p MemoryMax=<cap> -p MemorySwapMax=0`, so exhaustion OOM-kills the indexer instead of stalling the host. `<cap>` comes from §6.4 for executing indexers; for codebase-memory and the static indexers it is a fixed ceiling (default 4 GiB; measured peaks 142 MiB and 2.1 GiB), with `CBM_MEM_BUDGET_MB` set below it so the tool budgets itself before the cgroup has to act.
- **Sharing the machine with inference:** §9.2. `nice`/`ionice` alone are not enough on this machine.

### 9.1 Exact indexing executes project code

The Rust exact layer always runs the repository's build scripts and proc-macros (§2: it cannot be turned off in scip mode). Indexing an untrusted repository with an executing indexer means running that repository's code.

Three rules follow.

- **Trust gate for executing indexers** (rust-analyzer, scip-java, scip-dotnet):
  - they run only for repositories the user has marked trusted: `trusted = true` in `<repo>/.dreamference/code_index.toml`, or Codex's own per-project trust (`[projects."<path>"] trust_level = "trusted"` in `$CODEX_HOME/config.toml`, which the TUI already asks about);
  - untrusted repositories get the universal layer plus the static exact layers, and `puffin code status` says so in one line.
- **Sandbox, for every indexer.** Each runs as `systemd-run … -- bwrap …`, with the network removed and the filesystem read-only except for its outputs. For rust-analyzer (this is the command that produced §2's measurements, less the paths):
  ```
  systemd-run --user --scope --unit="puffin-index-$REPO_ID" \
    -p MemoryMax="$CAP" -p MemorySwapMax=0 \
    -p CPUQuota=400% -p AllowedCPUs="$INDEX_CPUS" -- \
    nice -n 10 ionice -c3 \
    bwrap --die-with-parent --unshare-net --unshare-pid \
          --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
          --bind "$SCRATCH" "$SCRATCH" \
          --bind "$REPO/.dreamference/scip" "$REPO/.dreamference/scip" \
          --bind "$CARGO_HOME" "$CARGO_HOME" \
          --setenv CARGO_NET_OFFLINE true --setenv CARGO_TARGET_DIR "$SCRATCH/target" \
          --setenv CARGO_BUILD_JOBS 4 \
          -- rust-analyzer scip "$SRC" --output "$REPO/.dreamference/scip/rust-analyzer.scip"
  ```
  - `/usr/bin/bwrap` is already installed; it is Codex's own sandbox.
  - The scope is named so §9.2's supervisor can `freeze`/`thaw` it and `puffin code status` can find a run in progress.
  - The router creates the bound directories beforehand, because bwrap cannot bind a path that does not exist.
  - `$CARGO_HOME` is writable only because Cargo takes a lock file there even offline; with no network, nothing can be fetched into it.
  - `$SRC` is read-only. A run that needs to rewrite the lockfile fails and is recorded; the remedy is a scratch copy, as for `codex/` (§6.2).
- **Offline is enforced, not assumed.**
  - Environment: `CARGO_NET_OFFLINE=true`, `GOFLAGS=-mod=readonly`, `GOPROXY=off`, `npm_config_offline=true`.
  - Nothing ever runs `npm install`, `pip install` or a dependency download on the indexer's behalf.
  - An indexer that needs something not on disk fails with `status: "failed: offline"`. That is the accepted outcome, not a retry.
- **Air-gapped, stated precisely:** no network access is *permitted* to any indexer at index time, and none is used at query time. The only downloads happen at install (`puffin-admin code setup`, `puffin update`), each pinned by checksum. codebase-memory's own traffic was checked with strace: none (§2).
- **Scope:** only the current repository is indexed, or explicitly listed ones. `~`, `/` and anything over `code_index_max_files` (default 50,000) are never indexed.

### 9.2 Indexing must not slow the model

On GB10, token generation is limited by memory bandwidth, and CPU, GPU and model weights share the same LPDDR5X. Background indexing competes for that bandwidth, which `nice` and `ionice` do not limit. The Codex exact run averaged about 3.7 busy cores in its first phase. The number to protect is the default model's single-stream decode rate: Qwen3.8-27B, 25.5 / 50.3 / 87.0 tokens/s on prose / code / JSON (`DREAMFERENCE_INFERENCE.md` §5.3).

- **Start only when the model is idle.** The supervisor reads the served model's `/metrics` and treats it as idle when the engine's request gauges are 0: `sglang:num_running_reqs` and `sglang:num_queue_reqs` on SGLang, `vllm:num_requests_running` and `vllm:num_requests_waiting` on vLLM, whichever answers. No model server at all also counts as idle.
- **Never alongside a model load.**
  - A model container (`dreamference-vllm-<port>`, the name both engines use) that exists but does not answer `/health` is **loading**. Nothing starts then: `MemAvailable` is still high before the weights are mapped, so admission would wrongly pass.
  - A run in flight when a load begins is **stopped**, not frozen, because frozen memory stays resident; it is recorded as `deferred: model-start`.
  - `puffin-admin server start` stops every `puffin-index-*` scope before its host-safety pre-flight (`systemctl --user stop 'puffin-index-*'`), keeping `check_host_safety()`'s guarantee intact.
- **Pause instead of competing:**
  - The indexer cannot watch the model itself: it has no network in its sandbox. `puffin code index` therefore starts a detached **supervisor** outside the sandbox that creates and owns the scope, polls `/metrics` every 2 s, applies these rules, writes the manifest, and exits when the scope ends. It lives exactly as long as one run.
  - When a request appears, the supervisor runs `systemctl --user freeze <scope>`, and `thaw` once the model has been idle for 10 s (supported on systemd 255).
  - A run frozen for more than 30 minutes is stopped and recorded as `deferred: busy`.
- **Cap what it can take while running:** `-p CPUQuota=400% -p AllowedCPUs=<4 cores>` on the scope, `CARGO_BUILD_JOBS=4` and the Go and MSBuild equivalents, `CBM_WORKERS=4` for codebase-memory. Newer rust-analyzer than the pinned 1.95.0 adds `--num-threads`; the router passes 4 once the toolchain has it.
- **One run at a time, coalesced:** at most one exact run in flight per repository; requests during a run collapse into one follow-up, which waits at least `code_index_min_interval` (default 15 minutes) unless it is `puffin code index --exact`. A lock file makes this hold across sessions, and Night Shift's admission counts an exact run as work in progress.

## 10. Evaluation (before building the router)

§2 already answers the cost and correctness questions the first draft left open: the universal layer is too inaccurate on call edges to stand alone (78% Rust, 58% Python), and the exact layer is affordable for static languages and admissible for Codex only when the machine is quiet. What remains:

1. **Question set:** 15–20 real questions about this repository: at least 8 about `codex/codex-rs` Rust (trait methods, re-exports, macro-generated code, `dyn` dispatch) and at least 5 about `dreamference/` Python.
2. **Ground truth:** SCIP from rust-analyzer and scip-python (both now produced on this machine), spot-checked by hand for `unresolved` cases.
3. **Contenders:** the router (both layers); the graph alone; `rg`/`ast-grep` as the baseline.
4. **Metrics:**
   - reference recall and precision against ground truth, line level (§2's figures are file level);
   - wall time per question and output tokens per answer;
   - the unconstrained peak of the Codex exact index: one run with the model server stopped and a 40 GiB cap;
   - the model's decode rate while an exact index runs, frozen and unfrozen, against §9.2's baseline;
   - the quality of search by meaning, before it is ever enabled by default.
5. **Acceptance:**
   - the router's `refs`/`callers` recall is at least 98% of SCIP's on fresh files, and every miss is visible as `heuristic`, `unresolved` or `not indexed`;
   - its answers cost fewer tokens than the `rg` baseline;
   - **query latency:** p95 at most 200 ms for `refs`/`def`/`callers`, from process start to last byte, on a warm page cache (the direct reads of §7.5 measured under 10 ms);
   - **answer size:** no answer exceeds 1,000 tokens at the default limit;
   - **inference cost:** the decode rate while indexing runs within §9.2's caps stays within 5% of baseline; with the scope frozen it is indistinguishable from baseline;
   - **admission:** the Codex exact index completes under §6.4 with the model server stopped, and its peak replaces §2's cap-bounded figure.

## 11. Later: SCIP inside the graph

If the router shows that the graph's edges are wrong in concentrated places, the better long-term design is to fork codebase-memory and add SCIP import: exact SCIP edges would replace its `CALLS` edges for indexed files, so its `trace_path` and `detect_changes` become exact too, inside one graph. It is C, and a larger change, so it is deferred until the router has shown where it matters. The same fork would fix its application of the superproject's ignore rules inside submodules (§4.1).

## 12. Tests

- **Router unit tests** (`puffin-rs`, `cargo test -p puffin-launcher` in the export):
  - merging with fresh, stale and missing files; de-duplication; `unresolved` and `not indexed` always printed when reported; ambiguous names listed, not guessed; the stale threshold requests a re-index.
- **Fixtures** under `tests/fixtures/code_index/`: a tiny Rust crate and Python package with known references, plus **recorded stores**: a codebase-memory v0.11.0 graph database and an `expt-convert` store for them. Router tests run against the recorded stores, so they need neither binary; tests that regenerate them are skipped when the binaries are absent.
- **Schema fingerprints:** a test computes both stores' fingerprints from the recorded fixtures and compares them with the pinned values, so a version bump that changes a schema fails CI rather than a user's query. A mismatched fixture must send the router down the fallback ladder, not into a wrong answer.
- **Direct reads:** a query during a simulated write (a held `BEGIN EXCLUSIVE` on a copy) waits and retries within the busy timeout; a `mutation_gen` change between two reads causes one retry; the router holds no handle after it exits.
- **Join (§7.4):** a recorded SCIP chunk and a recorded graph node for the same definition resolve to the same symbol; their rows de-duplicate to one per `(path, line)`, which catches a 0-/1-based mismatch; a stale definition file falls back to the `display_name` search and tags the result `heuristic`.
- **Safety:**
  - indexing a submodule never writes to it (`git status --porcelain` empty afterwards);
  - indexer runs are killed at the memory cap, not left to exhaust the host (fixture with a tiny cap);
  - no network during `index` or queries: a fixture crate with a missing dependency must fail with `failed: offline`;
  - an untrusted fixture repository never gets an executing index, and its `build.rs` (which writes a marker file) never runs, while its Python package does get a static exact index.
- **Isolation from the user's machine**, the lesson of 2026-09-29 (tests recreated live containers and wrote the real compile-signature file):
  - tests set `CBM_CACHE_DIR` under their temporary home and `CBM_RUNTIME_DIR` to a **short** private directory (mode 0700; the socket path must stay under 108 bytes, or the CLI fails with "secure CLI coordination could not be created");
  - no test creates a real `systemd-run` scope or freezes a real unit: `puffin-index-*` scope creation is behind a seam the tests replace, the way `tests/conftest.py` refuses real mutating `docker` commands.
- **Sandbox sides (§4):** `puffin code refs` run under Codex's `read-only` sandbox answers from both stores and writes nothing; under `workspace-write` a stale answer appends exactly one request line, and the session thread coalesces ten such lines into one run.
- **scip-python's environment:** a fixture repository whose `.venv/bin/python3` and `sitecustomize.py` write marker files is indexed without either marker appearing.
- **Prompt:** the `# Code navigation` block is full when the index is ready, reduced while building, absent without a repository, and under 250 tokens.
- **Performance:**
  - **Output budget:** a fixture symbol with 500 references prints the totals header, at most 15 file-summary lines and 40 rows; `--offset 40` returns the next 40; an `--offset` taken before a re-index fails with an error.
  - **Freshness cost:** a `refs` on a 40-row result `stat`s at most 41 files and hashes none when nothing changed.
  - **Admission:** with `peak_rss_mb` above the available cap, the run is not started (`deferred: memory`); a cap-bounded peak raises the next cap; an OOM-killed run records its cap as the new lower bound.
  - **Scheduler:** five triggers in quick succession produce one run and one coalesced follow-up; no run starts while the engine's request gauges are non-zero (fake `/metrics` for both `vllm:` and `sglang:` names); a frozen scope resumes on thaw with the same output as an uninterrupted run.
  - **Model load:** with a model container present but `/health` not answering, no run starts; a run in flight is stopped, not frozen (`deferred: model-start`); `puffin-admin server start` stops any `puffin-index-*` scope before its pre-flight.
  - **Worktrees:** a query in a linked worktree reads the main worktree's project, and files that differ are tagged stale.

## 13. Open questions and unverified claims

- **The unconstrained peak of the Codex exact index** is unknown: ≥ 21.5 GiB, cap-bounded (§2). One run with the model server stopped and a 40 GiB cap settles it.
- **The decode-rate cost of indexing beside the model**, frozen and unfrozen, is unmeasured.
- **Does freezing a scope mid-analysis leave rust-analyzer healthy?** A cgroup freeze is `SIGSTOP`-like, so it should, but it is untested.
- **scip-typescript's cost and correctness here** are unmeasured; this repository has little TypeScript beyond the desktop app.
- **Search by meaning** is off by default until measured (§4); upstream issues #1155 and #1462 suggest it is weak.
- **codebase-memory's call-edge gaps** are upstream issues (#1153 method calls through instances, #1271 polymorphic Python calls, #1277 cross-file receiver inference, #1354 TypeScript cross-file methods). A release that fixes them changes §2's recall figures, not this design: the exact layer stays the authority where it exists.
- **Its application of the superproject's `.gitignore` inside submodules** is a bug to report upstream; the design works around it (§4.1).
- **`expt-convert`'s schema** may change: that is why it is pinned and fingerprinted, with our converter as the fallback (§7.5).
- **SCIP freshness for rust-analyzer is whole-workspace.** It has no crate-scoped mode (its `scip` flags are `--output`, `--config-path` and `--exclude-vendored-libraries`), so the exact Rust layer after a small edit is a full re-run.
- **Partitioning** may not lower the peak, because the dependency closure of the crates that matter is most of the workspace (§6.4).
- **Connecting to an existing Unix socket from inside the sandbox** (the runtime directory is read-only there) is untested; the design does not rely on it (§4).
- **Several repositories:** start with the working directory only.
