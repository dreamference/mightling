# Puffin Code Index — codebase-memory-mcp + SCIP

**Status:** implemented on 2026-10-01 in `puffin-code-rs/`, with the differences and the parts not built listed in §14; the rest of this document is the design as specified. The design below was revised on 2026-09-30 after measuring both layers on this machine (§2): several assumptions of the first draft did not survive contact with the tools. Revised again the same day: freshness is now decided for the whole repository, not only for the files an answer already names (§7.3), the router is a binary of its own, `puffin-code`, not a subcommand compiled into `puffin` (§4.2), and memory admission covers every indexer run on the host, not each executing run on its own (§6.4).
**Target:** the `puffin` terminal agent. The same index is also offered over MCP to Claude Code and to IDEs.
**Builds on:** the `puffin-search` / `puffin-admin gmail` pattern of giving the local model shell commands rather than MCP tools, and of putting each such command on `PATH` as a program of its own beside `puffin`. `puffin-code` is a separate Rust binary (§4.2); the launcher in `puffin-rs/` only starts it and asks it for the prompt block. It also builds on the builder's rule that nothing ever runs Cargo inside the `codex/` submodule.

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

A router, the `puffin-code` binary, answers each question from the better layer. It tags every result `exact` or `heuristic`, and says what it could not resolve or did not index.

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
| git inside Codex's sandbox | `git diff --name-only` and `git status --porcelain` run under `puffin sandbox`; `.git` is read-only there (`touch .git/x` fails), so the router passes `--no-optional-locks` and never refreshes git's index | measured |
| One limit for all index runs | a scope started with `systemd-run --user --scope --slice=puffin-index.slice` lands in `…/user@1000.service/puffin.slice/puffin-index.slice/`; `systemctl --user set-property --runtime <slice> MemoryMax=… CPUQuota=400%` takes effect at once (`memory.max` and `cpu.max` of the slice read back the new values), systemd 255. `choom -n 1000 -- <cmd>` raises `oom_score_adj` without privileges | measured (with a throwaway slice) |
| `defn_enclosing_ranges` in the store | maps a reference to its innermost enclosing definition (line 2007 of the CLI controller → `DreamferenceCLIController#run_cli()`), 0-based lines | measured |
| The indexing sandbox, probed with a crate whose `build.rs` reports what it can reach | with `$CARGO_HOME` bound read-write (the first draft's command): it can open `~/.cargo/bin/cargo` for writing and create files in `$CARGO_HOME`, and it sees `~/.ssh`, `~/.puffin/config.toml` and `~/.config/dreamference`. With the home directory a tmpfs and `$RUSTUP_HOME`, `$CARGO_HOME` and the source bound read-only: none of those, and `cargo check --offline` (0.4 s, one registry dependency) and `rust-analyzer scip` (1.95.0, 3.1 s, build script run, `.scip` written to the scratch directory) both still succeed | measured |
| SGLang's idle gauges | `sglang:num_running_reqs`, `sglang:num_queue_reqs` (0 when idle), on `/metrics` of the served model | measured |
| Licences | codebase-memory-mcp MIT; scip CLI, scip-typescript, scip-clang, scip-java, scip-go, scip-dotnet Apache-2.0; scip-python MIT (Pyright's); all maintained (pushed within the last month) | verified |
| Decode rate while indexing, frozen/unfrozen scopes, scip-typescript cost, codebase-memory's search by meaning quality | — | **unmeasured** (§10) |

Consequences for the design, each carried into the sections below:

1. **No per-query CLI.** At 6.6 s a call, the CLI cannot serve queries. The router reads codebase-memory's database directly (§7.5), with a fallback ladder.
2. **Exact layer by language, not by trust alone.** Static indexers (scip-python, scip-typescript) run for every repository; executing ones (rust-analyzer, scip-java, scip-dotnet) need trust and admission.
3. **Submodules are excluded by default** (97% of this repository's universal index was the `codex/` submodule), and indexed deliberately when wanted.
4. **Admission from measured numbers:** the Codex exact index needs more than 21 GiB and runs only when the machine can spare it.
5. **The query store is the `scip` CLI's own SQLite export**, pinned and fingerprinted, with our converter as the fallback.
6. **A snapshot cannot say what it is missing.** A file that gained a reference after the snapshot is in no row, so checking the rows' files cannot find it. Every query therefore computes the files changed since each layer's snapshot and searches them for the name (§7.3).
7. **Memory is one pool, so admission is one ledger.** Every index run on the host, of every kind and from every session, is admitted against the same budget and runs inside one slice whose limits the kernel enforces (§6.4).

## 3. Components

| Component | Version pinned | Licence | Role |
|---|---|---|---|
| [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | v0.11.0 (2026-09-15), `linux-arm64` | MIT | Universal layer: builds the graph (158 tree-sitter grammars, "Hybrid LSP" resolution for 13 languages), keeps it incrementally, and serves search by meaning when a session asks for it. Configured through `CBM_CACHE_DIR`, `CBM_RUNTIME_DIR`, `CBM_MEM_BUDGET_MB` (a hard budget since v0.11.0), `CBM_WORKERS`, `CBM_ALLOWED_ROOT`, `CBM_SEMANTIC_ENABLED`, and a per-repository `.cbmignore`. |
| [scip CLI](https://github.com/scip-code/scip) | v0.10.0 (2026-09-03), `scip-linux-arm64.tar.gz`, sha256 `6ab677dc2c4bf2955975d0530766152e45daaa988f9404068d8adecacd0bb24c` | Apache-2.0 | `expt-convert` builds the query store (§7.5). |
| `scip` Rust crate | 0.10.0 | Apache-2.0 | Decodes the occurrence blobs of the store in `puffin-code`. |
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
        puffin-code <cmd>  ─────────────  puffin-code mcp
                      │   (one binary, crate puffin-code-rs/)
          ┌───────────┴─────────────────────────┐
          ▼                                     ▼
  codebase-memory graph (SQLite)          SCIP query store (SQLite)
  read directly, read-only (§7.5)         .dreamference/scip/index.db
  written only by its indexer, run        built by `scip expt-convert`
  detached at launch / on demand          after each exact run (§6)
```

- **Both tools stay unmodified.** codebase-memory is a pinned binary that writes its graph; the scip CLI and the indexers are pinned binaries that write SCIP. The new code is the router, the scheduler and the store's small post-processing (§7.5).
- **Queries never start a tool.** A `puffin-code refs` opens two SQLite files read-only, runs indexed lookups and exits: milliseconds, and no process survives it. The tools run only to *index*, started by the session process outside the sandbox (below), under §9's limits.
- **Two sides of the sandbox.** The agent runs `puffin-code …` as a shell command, and Codex runs every such command inside its bwrap sandbox (`workspace-write` or `read-only`): the process can read the two databases and write only under the working directory and `/tmp`. Probed with `puffin sandbox` in `workspace-write` mode: reading the graph database works; `~/.cache` is a read-only file system; `$XDG_RUNTIME_DIR` is visible but read-only; `systemd-run --user` fails ("Failed to connect to bus"); and nothing it starts outlives it. So the design splits:
  - **Queries are pure readers.** `puffin-code refs|def|callers|…` opens the databases read-only and exits. That is all it may do.
  - **Everything that starts an indexer belongs to `puffin-code session`**, a process the launcher starts at `puffin` launch, *outside* the sandbox, for as long as that `puffin` lives (§4.2). It owns indexing: the launch-time runs, the supervisor of §9.2, and a request queue. A query that wants a re-index appends one line to `<repo>/.dreamference/code_index.requests` when it can write there; the session process drains it every few seconds, coalesces (§9.2) and runs what admission allows. A line is a fixed word (`index` or `index-exact`) and nothing else: the file is written from inside the sandbox, so the session process treats it as untrusted and ignores any other content. In a `read-only` sandbox the query cannot write the request, so it only says in its header that the index is stale; the next launch refreshes it.
  - **`puffin-code index` typed by the user** in an ordinary shell is outside any sandbox and runs the index itself. Typed by the agent, it is inside the sandbox, cannot create a scope, and queues a request instead; it tells the two apart by whether the user's systemd bus answers.
  - **`puffin-code mcp`** (for Claude Code and IDEs) runs outside any sandbox and may do both.
- **The fallback ladder**, used only when the direct read is impossible (the graph's schema fingerprint does not match the pinned one, §7.5): the CLI, correct at 6.6 s per call. A session-scoped codebase-memory child (12–105 ms per query) is reachable only from `puffin-code mcp`, which can hold it; from inside the sandbox there is no channel to it.
- **Search by meaning** needs the embedding model compiled into codebase-memory, so it is served only by `puffin-code mcp`'s session child, and only when `puffin_code_semantic = true`. It is off by default: it returned unrelated build files for plain questions on this repository (2026-09-29), and upstream has open issues on it (#1155, #1462). `search` without it is FTS5 over names, qualified names and bodies, ranked by BM25 (§7.1).
- **Why Rust:** `puffin-code` works in any shell with no Python environment, and the model's shell has none on `PATH` (the reason `puffin-admin` had to be linked there). rusqlite plus the `scip` crate are all it needs.

### 4.1 Lifecycle of the universal layer

- **First run:**
  - at `puffin` launch, the session process (`puffin-code session`, §4.2) checks for the repository's project in codebase-memory's cache (by reading the `projects` table directly, not through the CLI);
  - if there is none, it starts `index_repository` **detached**, under §9's limits, and continues launching. It never indexes in the agent's path.
- **Prompt timing:** the prompt is assembled once, at launch:
  - **Index ready:** the full `# Code navigation` block (§8).
  - **Index building:** a reduced block: "a code index is being built; use `rg` until `puffin-code status` reports it ready". `puffin-code` answers "index not ready yet" rather than failing obscurely.
  - **No repository, or indexing disabled or failed:** no block.
- **Keeping it current, without a daemon:**
  - an incremental `index_repository` runs detached at every `puffin` launch (8.9 s measured, almost all fixed start-up);
  - it also runs on `puffin-code index`;
  - it never runs inside a query. A query answers at once from the snapshots, covers the files changed since them with a text search (§7.3), and queues a re-index request for the session process (§4) when any file is changed.
  - **The agent's own edits are the normal case, not the exception.** Between two launches the graph sees nothing the session wrote, and a file the agent created is in neither layer. §7.3's changed set is what covers that gap; the re-index only turns its `heuristic` rows back into `exact` ones.
  - codebase-memory's watcher stays off (`watcher_enabled = false`, `auto_watch = false`) unless `puffin_code_watch = true`.
- **Scope, written by the session process:**
  - **Submodules are excluded.** The session process keeps a managed block in `<repo>/.cbmignore` listing every submodule path (`git submodule status`), and adds `.cbmignore` to `.git/info/exclude`, so the file never shows up in `git status`. `puffin-code index --include-submodules` indexes them deliberately.
  - **Ignore rules inside submodules.** codebase-memory applies the superproject's `.gitignore` inside a submodule, which git does not. When submodules are included, the router reports every excluded subtree from `index_coverage` in `puffin-code status`, so the gap is visible rather than silent.
  - **Tracked files an ignore rule matches are skipped** (this repository's `config/` rule drops the tracked `dreamference/config/`). The router lists them from `index_coverage` (`not_indexed_dir`/`not_indexed_file`) and answers queries touching them with `rg`-backed `heuristic` rows.
- **Git worktrees share the main worktree's index.** A worktree (`git rev-parse --git-common-dir` differs from `--git-dir`) is not indexed as a project of its own, which would cost a full database per worktree (Night Shift and fan-out create many). The router maps its paths onto the main worktree's project, and files whose hash differs are stale by §7.3, which is the safe direction.
- **Data:**
  - graphs live in codebase-memory's cache, `CBM_CACHE_DIR` (default `~/.cache/codebase-memory-mcp/`);
  - `puffin-code status` reports their disk use;
  - `puffin-code forget [<repo>]` deletes a project, and `puffin-code status` offers it when a project's root no longer exists.

### 4.2 A binary of its own: `puffin-code`

The router is not compiled into `puffin`. It is a separate program, installed and linked beside `puffin`, `puffin-admin`, `puffin-search` and `puffin-fetch`.

- **Source:** the crate `puffin-code-rs/` at the repository root, a Cargo workspace of its own with its own committed `Cargo.lock`. It is not copied into the Codex export and is not a member of Codex's workspace, so it builds with `cargo build --release --locked` in its own directory and none of the builder's Codex rules (the export, the patches, no `--locked`) apply to it.
- **Why not inside `puffin`:**
  - **Build cost.** A change to the router would otherwise mean rebuilding Codex: about ten minutes, a 330 MB binary, and a link step that has to be scheduled around the model server's memory. The router alone builds in a fraction of that and can be rebuilt while the model is serving.
  - **No Codex patch and no launcher growth.** rusqlite and the `scip` crate stay out of Codex's dependency graph, and the patch series does not grow.
  - **It is useful without `puffin`.** Claude Code and IDEs start `puffin-code mcp` directly; a user can run `puffin-code refs` in any shell.
  - **Tests run where the code is:** `cargo test --locked` in `puffin-code-rs/`, with no export directory.
  - **Isolation.** A crash or a hang in the index cannot take the agent's process with it.
- **What the launcher still does, and all it does:**
  1. at launch, outside the sandbox, it starts `puffin-code session --parent-pid <pid>` detached and continues; the session process exits when that `puffin` does. With several `puffin` sessions in one repository, a lock under `<repo>/.dreamference/` makes one session process the owner and the others exit at once; when the owner's `puffin` ends, the next launch takes over;
  2. it runs `puffin-code prompt-block` (a read of index state, milliseconds) and appends what it prints to the model's prompt (§8). The text lives in `puffin-code`, so the commands and the prompt that describes them cannot drift apart.
  - If `puffin-code` is not installed, both steps are skipped and `puffin` runs as it does today, with no `# Code navigation` block.
- **Install and `PATH`:** the binary goes to `~/.local/share/dreamference/puffin/bin/puffin-code` with `~/.local/bin/puffin-code` linked to it, by the same builder step that links `puffin`, `puffin-admin` and `puffin-search`. The link is not optional: the prompt tells the model to run `puffin-code`, and a command missing from its shell's `PATH` ends in exit 127, as `puffin-admin` did before it was linked.
- **Versions move together.** `puffin-code` pins the schema fingerprints of the two stores (§7.5) and the checksums of the pinned tools (§5), so it is built, released and updated with them: `puffin-admin codex build` builds it as a second, independent Cargo run, the release workflow attaches it, and `puffin update` installs it with `puffin`. `puffin-code --version` prints its own version and the pinned tool versions.
- **Configuration:** it reads the same files the launcher does (`DREAMFERENCE_CONFIG_PATH`, `./dreamference.toml`, `~/.config/dreamference/config.toml`) for the model server's address and the `code_index_*` / `puffin_code_*` keys, and `$CODEX_HOME/config.toml` for project trust (§9.1). `CODEX_HOME` resolves as in `puffin-rs/src/home.rs`: `~/.puffin` unless set, never upstream's `~/.codex`. It needs no running model server and no `puffin`.

## 5. Distribution

- **Pinned binaries, verified:**
  - The Puffin release workflow downloads the pinned codebase-memory-mcp and scip CLI releases for `linux-arm64` and checks each against its published checksum.
  - The expected SHA-256s are committed in `puffin-code-rs/code-index.sha256`, the same trust model as the rusty_v8 archive.
  - They are shipped beside `puffin` and `puffin-code` in `~/.local/share/dreamference/puffin/bin/` and attached to Puffin releases, so `puffin update` refreshes them together with the `puffin-code` that knows their schema fingerprints (§7.5).
  - `puffin-admin codex build` fetches the same pinned archives for local builds, and builds and links `puffin-code` (§4.2).
- **Fork only when a patch is needed.** No change to codebase-memory is required. If one becomes necessary, for example §11's SCIP import or a fix for its submodule ignore handling, fork it to `dgxcoder/codebase-memory-mcp` as a submodule and build from source, following the Codex pattern: pinned tag, patches in a directory, submodule never edited. The MIT notice must stay in the shipped files.
- **SCIP indexers:**
  - `rust-analyzer` comes from rustup, the toolchain the Codex build already pins.
  - scip-python and scip-typescript are npm packages; `puffin-admin code setup` installs them into `~/.local/share/dreamference/puffin/indexers/`, pinned by version and by the lockfile's `integrity` hashes. They need Node.js; a machine without it keeps the universal layer for those languages and says so in `puffin-code status`.
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

- **Files:** `<repo>/.dreamference/scip/<indexer>.scip` (moved there by the supervisor after the run, never written in place by the indexer, §9.1), the query store `index.db` (§7.5), and `manifest.json`: `{indexer, version, commit, path_prefix, dirty_files, file_hashes, started, duration_s, peak_rss_mb, peak_cap_bounded, cap_mb, status}`. `status` is one of `ok`, `failed: <reason>`, `deferred: memory`, `deferred: busy` or `deferred: model-start`. The session process also writes `graph.json` beside it after every universal run: `{commit, dirty_files, finished}`, so the graph's snapshot has a commit too (codebase-memory records file hashes but no commit). `.dreamference/` is already git-ignored.
- **Paths are relative to the indexed root**, for every indexer: rust-analyzer's are relative to the workspace (`core/src/…`), scip-python's to the `--target-only` directory (`cli/…` for `dreamference/cli/…`). The manifest's `path_prefix` restores the repository path.
- **Never index inside a submodule's checkout with a build tool that writes to it.** rust-analyzer runs `cargo metadata` and build scripts, and even `cargo tree` rewrites a submodule's `Cargo.lock`. This project's `codex/` workspace is indexed from a scratch copy of the builder's export (`~/.cache/dreamference/puffin-codex/src/codex-rs`), never from the export itself, which the builder owns, and paths are remapped to `codex/codex-rs/…`.
- **`CARGO_TARGET_DIR` points at a scratch directory** so indexing never touches a build cache another build is using. The build-script cache it accumulates is what makes the second Rust run ~2 minutes faster (§2).

### 6.3 When it runs

- **Static indexers (Python, TypeScript):** after the universal index at launch when the manifest is older than `HEAD` or files changed, detached; and on `puffin-code index`. Cheap enough (19 s for `dreamference/`) to follow every launch.
- **Executing indexers, trusted repositories only (§9.1):** in the background, never blocking a session:
  - when no exact index exists, or it is older than `HEAD` by more than `code_index_stale_commits` commits (default 20);
  - when idle, preferring the moments the model server is stopped (§6.4);
  - in the Night Shift window, before night tasks start, so they begin with a fresh index (see `DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md`).
- **On demand:** `puffin-code index --exact`.
- **Before a risky operation:** when `refs`/`impact` runs while any file has changed since the snapshot (§7.3's changed set). This only *requests* a run; the answer is given immediately, with the changed files searched by text and their rows tagged.
- **Every trigger goes through the scheduler of §9.2,** and every executing run through §6.4's admission.

### 6.4 Memory admission

A fixed cap either kills the run (8 GB did, on 2026-09-28) or has to be sized for the worst repository, taking memory from the model. Runs therefore go through admission control.

**Admission is host-wide and covers every run.** The earlier revision admitted only executing indexers, one repository at a time, and gave the universal and static indexers a fixed 4 GiB cap with no check. Host memory is one pool, so that left three ways to repeat the 2026-09-29 kill:

- **A run nobody admitted.** The universal and static indexers start at every `puffin` launch. With the 122B fallback model resident the host has about 4 GB above earlyoom's line; one scip-python run (2.1 GiB measured on 81 files, 4 GiB allowed) can cross it, and earlyoom then kills the largest process, which is the model server.
- **Two runs that each fit alone.** A cap is computed from `MemAvailable` at the start, but an indexer takes minutes to grow into it. Two sessions in two repositories, started seconds apart, both read the same `MemAvailable`: two Rust runs would each be given 25.8 GiB out of the same 36.
- **Per-scope CPU limits add up.** `CPUQuota=400%` on each of N scopes is N × 4 cores, which is what §9.2 exists to prevent.

So:

- **One slice.** Every index scope, of every kind, is started with `--slice=puffin-index.slice`. The slice carries the aggregate limits, enforced by the kernel however many sessions and repositories are involved: `MemoryMax` (set by the admitter, below), `MemorySwapMax=0`, `CPUQuota=400%` and `AllowedCPUs=<4 cores>`. Verified on this machine (§2). Each scope keeps its own `MemoryMax` inside it, so one run cannot take another's share.
- **One ledger.** Admission runs under an exclusive `flock` on `$XDG_RUNTIME_DIR/puffin-index/admission.lock`, held only while deciding. Under the lock the admitter:
  1. lists the live scopes of the slice and reads each one's cap (`memory.max`) and use (`memory.current`);
  2. computes `budget = MemAvailable − code_index_reserve − Σ (cap − use)` over them: what is left after every run already admitted grows into its cap;
  3. gives the new run `cap = min(ceiling for its kind, budget)` and starts it only if `cap ≥ need` (below);
  4. sets the slice's `MemoryMax` to the sum of the live caps, and releases the lock once the scope exists.
  The ledger is the cgroup tree itself, so a crashed session leaves nothing stale: a scope that is gone is no longer counted.
- **Reserve:** `code_index_reserve = earlyoom SIGTERM threshold + 4 GiB`, read from earlyoom's `-m` percentage and `MemTotal` (on this machine 6.2 + 4 = ~10.2 GiB). The model's memory is already allocated when it is resident, so `MemAvailable` excludes it; the reserve protects the host, the session and the model's transient allocations. On 2026-09-29 an index run that ignored this pushed the host under earlyoom's line and earlyoom killed vLLM.
- **Ceilings by kind:** universal and static indexers 4 GiB (`code_index_small_ceiling`); executing indexers `code_index_memory_ceiling`, default 40 GiB.
- **Need, and the first run:** `need = 1.2 × peak_rss_mb` from the manifest for this indexer and repository. With no record yet, a floor by kind stands in: 512 MiB for the universal indexer (142 MiB measured), 3 GiB for a static one (2.1 GiB measured), 8 GiB for an executing one. A run the budget cannot cover is not started: `status: "deferred: memory"`, `puffin-code status` says so, and queries keep answering from the last snapshot plus §7.3's text search.
- **At most one executing run on the host**, not one per repository: a lock in `$XDG_RUNTIME_DIR/puffin-index/` held for the run's life. Universal and static runs may start beside it when the budget admits them; the slice's CPU limit is shared between them.
- **If the host crosses the line anyway, the indexer dies first.** Every indexer is started through `choom -n 1000`, so earlyoom and the kernel, which both pick by `oom_score`, choose an index run before the model server.
- **Cap-bounded peaks are marked.** When a run's peak ends within 10% of its cap (`peak_cap_bounded: true`), the recorded peak is a lower bound: the cgroup was reclaiming against the cap. The next admitted run gets `min(ceiling, 1.5 × peak)` if available, so the first uncapped run raises the record. An OOM-killed run records its cap as the lower bound, so the next attempt is not doomed the same way.
- **What this means for Codex today:** recorded peak ≥ 21.5 GiB, cap-bounded, so admission needs `cap ≥ 25.8 GiB`, i.e. `MemAvailable ≥ ~36 GiB` with no other index run holding part of the budget. With Qwen3.8 resident the machine has 35–38 GiB available, so a run is admitted only at the quiet end of that range; with the model server stopped (~100 GiB available) always. Scheduled runs therefore prefer `puffin-admin server stop`, idle periods with no server, and the Night Shift window. While no fresh exact index exists, the router serves the last snapshot, with changed files tagged `heuristic (stale)`.
- **The conversion is admitted too:** `expt-convert` peaked at 2.7 GB on Codex and runs in the indexer's scope, under the same cap, right after it.
- **Partitioning, as an option to measure, not a plan:** index the workspace in passes over subsets of `[workspace] members`, merged in the store. The caveat stands: `codex-cli`'s dependency closure is most of the workspace, so the passes that matter may peak almost as high as the whole.

## 7. The router

### 7.1 Operations

| `puffin-code …` | Answered by | Notes |
|---|---|---|
| `search <text>` | the graph's `nodes_fts` (FTS5, BM25) over names, qualified names and bodies | `nodes_fts` is contentless (`content=''`): it returns rowids, joined back to `nodes` for names and locations. Semantic ranking only through `puffin-code mcp` with `puffin_code_semantic = true` (§4). |
| `outline <file>` | the graph's `nodes` for that file, ordered by line | |
| `show <symbol>` | the definition's `file:start-end` from the graph, read from disk | Returns one symbol's source, not the whole file. |
| `def <symbol>` | SCIP if fresh (§7.3), else the graph | A definition written since the snapshots is found by the changed-set search (§7.3) and tagged `heuristic (text)`. |
| `refs <symbol>` | SCIP for fresh files; for every file changed since the snapshot, the graph where it is fresh **and** a whole-word text search (§7.3); merged | Tagged per row. |
| `callers` / `callees <symbol>` | SCIP references mapped to their innermost enclosing definition (`defn_enclosing_ranges` in the store, verified §2), else the graph's `CALLS`/`USAGE` edges | For Python and Rust the graph finds 58% and 78% of calling files (§2); graph-only answers carry a header note saying so. A text hit in a changed file is mapped to its enclosing definition through the graph's outline when the graph is fresh for that file, and printed as a bare `file:line` otherwise. |
| `impl <trait-or-interface>` | SCIP `relationships` (`is_implementation`), else the graph's `IMPLEMENTS`/`OVERRIDE` edges | Changed files are searched for the trait's name (§7.3). |
| `impact <symbol-or-diff>` | SCIP references, transitive to depth N via enclosing definitions; the graph's edges for stale files | Answers "what breaks". Text hits in changed files count at depth 1 and are not followed further; the header says so. |
| `status` | both | Layers present, freshness, languages covered, excluded subtrees, disk use, last index time, deferred runs and why. |
| `index [--exact] [--include-submodules]` | both | Re-index. `--exact` also schedules executing indexers. |

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

The source and commit are stated once in the header. `--json` gives the same data for tools. Four lines are mandatory, because they are what tell the agent it has to verify:
- **`changed since snapshot`**, on every answer, with the number of files changed since the older of the two snapshots and whether they were searched. `0 files` is the only state in which an all-`exact` answer is complete.
- **`unresolved`**, whenever either layer reports calls it could not resolve.
- **`not indexed`**, listing touched paths outside a layer's coverage (§4.1).
- **`not checked`**, whenever the changed set was too large to search (§7.3). It names the count and says the answer may be missing references from those files.

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

- **Prompt:** the launcher appends a `# Code navigation` block to the model's prompt, next to web access and Gmail. The text is what `puffin-code prompt-block` prints (§4.2). It covers the commands of §7.1, the meaning of `exact`/`heuristic`/`unresolved`/`not indexed`/`not checked`, and one rule: *before changing a signature, renaming or deleting, run `puffin-code refs`. If any row is `heuristic`, `unresolved` or `not indexed`, or the answer has a `not checked` line, confirm with `rg` and run the build or tests after the edit.*
- **Ready, building or absent:** the block is full, reduced or absent according to the index state at launch (§4.1).
- **MCP:**
  - `puffin-code mcp` serves the same operations over stdio for Claude Code and IDEs, and replaces jCodeMunch in `~/.claude.json` once implemented.
  - `puffin-admin mcp`'s `workspace_search_code` is re-pointed at the router; the `dreamference` context engine (per-file TF-IDF, FTS5 and embeddings) is retired once the router passes §10, since it answers a subset of `puffin-code search` with no call graph.
  - The local model keeps using shell commands, because it does not reliably call MCP tools under Codex's Code Mode (see `codex_runner.py`'s history).

## 9. Resources and safety

On GB10, host RAM and GPU memory are the same memory, and running it out can freeze the host, not just kill a process (see `psi_watchdog.py`). On 2026-09-29 an unconstrained index of this repository (the old `dreamference` context engine walking 2.3 GB of files) pushed available memory under earlyoom's line and earlyoom killed vLLM. Everything below exists so that cannot recur.

- **Memory limits:** every indexer, universal or exact, runs under `systemd-run --user --scope -p MemoryMax=<cap> -p MemorySwapMax=0`, so exhaustion OOM-kills the indexer instead of stalling the host. `<cap>` comes from §6.4's admission for **every** run; for codebase-memory and the static indexers it is at most 4 GiB (measured peaks 142 MiB and 2.1 GiB), with `CBM_MEM_BUDGET_MB` set below it so the tool budgets itself before the cgroup has to act. No indexer starts without being admitted, at launch or otherwise, and all of them share `puffin-index.slice`, whose limits hold for the sum.
- **Sharing the machine with inference:** §9.2. `nice`/`ionice` alone are not enough on this machine.

### 9.1 Exact indexing executes project code

The Rust exact layer always runs the repository's build scripts and proc-macros (§2: it cannot be turned off in scip mode). Indexing an untrusted repository with an executing indexer means running that repository's code.

Three rules follow.

- **Trust gate for executing indexers** (rust-analyzer, scip-java, scip-dotnet):
  - they run only for repositories the user has marked trusted through Codex's own per-project trust (`[projects."<path>"] trust_level = "trusted"` in `$CODEX_HOME/config.toml`, which the TUI already asks about);
  - **nothing inside the repository can grant trust.** A file there can be shipped in a clone and written by the agent from inside the `workspace-write` sandbox, so a `trusted` key in `<repo>/.dreamference/` or in a repository's `dreamference.toml` is ignored. `$CODEX_HOME` is outside the workspace and read-only in the sandbox;
  - untrusted repositories get the universal layer plus the static exact layers, and `puffin-code status` says so in one line.
- **Sandbox, for every indexer.** Each runs as `systemd-run … -- bwrap …`, with the network removed, the home directory hidden, and nothing writable except a scratch directory of its own. For rust-analyzer (§2's measurements were taken with an earlier, looser form of this command; the differences are the binds, which cost nothing):
  ```
  systemd-run --user --scope --unit="puffin-index-$REPO_ID" \
    --slice=puffin-index.slice \
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
  - **Nothing outside `$SCRATCH` is writable, `$CARGO_HOME` included.** The first draft bound `$CARGO_HOME` read-write, on the belief that Cargo needs its lock file there. It does not: Cargo skips the lock on a read-only file system (measured, §2). And the write access was an escape: a build script could replace `~/.cargo/bin/cargo` or add a `rustc-wrapper` to `~/.cargo/config.toml`, and that code would run unsandboxed the next time anyone ran Cargo, for example in `puffin-admin codex build`, which builds `puffin` itself. It also runs with no one asking: the exact index starts in the background at launch. Codex's own sandbox never gives the agent's builds that access.
  - **The home directory is an empty tmpfs**, with only the toolchain, Cargo's registry and the source bound back, read-only. A build script has no reason to read `~/.ssh`, `$CODEX_HOME` or the mail service's credentials, and with them hidden it cannot copy them into an output the agent (which has the network) can later read. Binds are given after the tmpfs they sit under, or bwrap hides them again; that includes a source or scratch directory under `/tmp`.
  - **The output is not written into the repository by the indexer.** It writes `$SCRATCH/out/`, empty at the start of each run. The supervisor, outside the sandbox, checks that the file decodes as SCIP and then moves it to `<repo>/.dreamference/scip/` and writes the manifest. With `.dreamference/scip` bound writable, as in the first draft, a build script could overwrite `index.db`, `manifest.json` or another indexer's `.scip`, and the router would serve its content tagged `exact`; a run killed half-way would also leave a truncated `.scip` in place of the last good one.
  - **Other executing indexers follow the same shape:** their caches are bound read-only (`~/.m2` and `~/.gradle` for scip-java, `~/.nuget/packages` for scip-dotnet) and their writable state is redirected into `$SCRATCH` (`GRADLE_USER_HOME` is a scratch directory whose read-only cache is supplied through `GRADLE_RO_DEP_CACHE`; `DOTNET_CLI_HOME`, `NUGET_HTTP_CACHE_PATH`). An indexer that cannot work without writing to its real cache fails and is recorded; it is not given the cache.
  - **A dependency Cargo has downloaded but not yet unpacked** (`registry/cache` without `registry/src`) cannot be unpacked into a read-only `$CARGO_HOME`. The run fails with `failed: dependencies not unpacked` and `puffin-code status` names the remedy: one `cargo fetch` or build by the user.
  - `/usr/bin/bwrap` is already installed; it is Codex's own sandbox.
  - The CPU limits are on the slice, not the scope (§6.4), so they bound all runs together. The measurements of §2 were taken with them on a single scope, which is the same limit for one run. systemd places `puffin-index.slice` under `puffin.slice`, from its name.
  - The scope is named so §9.2's supervisor can `freeze`/`thaw` it and `puffin-code status` can find a run in progress.
  - The session process creates the bound directories beforehand, because bwrap cannot bind a path that does not exist.
  - `$SRC` is read-only. A run that needs to rewrite the lockfile fails and is recorded; the remedy is a scratch copy, as for `codex/` (§6.2).
- **Offline is enforced, not assumed.**
  - Environment: `CARGO_NET_OFFLINE=true`, `GOFLAGS=-mod=readonly`, `GOPROXY=off`, `npm_config_offline=true`.
  - Nothing ever runs `npm install`, `pip install` or a dependency download on the indexer's behalf.
  - An indexer that needs something not on disk fails with `status: "failed: offline"`. That is the accepted outcome, not a retry.
- **Air-gapped, stated precisely:** no network access is *permitted* to any indexer at index time, and none is used at query time. The only downloads happen at install (`puffin-admin code setup`, `puffin update`), each pinned by checksum. codebase-memory's own traffic was checked with strace: none (§2).
- **Scope:** only the current repository is indexed, or explicitly listed ones. `~`, `/` and anything over `code_index_max_files` (default 50,000) are never indexed.

### 9.2 Indexing must not slow the model

On GB10, token generation is limited by memory bandwidth, and CPU, GPU and model weights share the same LPDDR5X. Background indexing competes for that bandwidth, which `nice` and `ionice` do not limit. The Codex exact run averaged about 3.7 busy cores in its first phase. The number to protect is the default model's single-stream decode rate: Qwen3.8-27B, 25.5 / 50.3 / 87.0 tokens/s on prose / code / JSON (`DREAMFERENCE_INFERENCE.md` §5.3).

- **Start only when the model is idle.** This and the next rule hold for every kind of run, including the launch-time universal and static ones. The supervisor reads the served model's `/metrics` and treats it as idle when the engine's request gauges are 0: `sglang:num_running_reqs` and `sglang:num_queue_reqs` on SGLang, `vllm:num_requests_running` and `vllm:num_requests_waiting` on vLLM, whichever answers. No model server at all also counts as idle.
- **Never alongside a model load.**
  - A model container (`dreamference-vllm-<port>`, the name both engines use) that exists but does not answer `/health` is **loading**. Nothing starts then: `MemAvailable` is still high before the weights are mapped, so admission would wrongly pass.
  - A run in flight when a load begins is **stopped**, not frozen, because frozen memory stays resident; it is recorded as `deferred: model-start`.
  - `puffin-admin server start` stops every `puffin-index-*` scope before its host-safety pre-flight (`systemctl --user stop 'puffin-index-*'`), keeping `check_host_safety()`'s guarantee intact.
- **Pause instead of competing:**
  - The indexer cannot watch the model itself: it has no network in its sandbox. The session process (or a `puffin-code index` typed by the user, §4) therefore starts a detached **supervisor** outside the sandbox that creates and owns the scope, polls `/metrics` every 2 s, applies these rules, writes the manifest, and exits when the scope ends. It lives exactly as long as one run.
  - When a request appears, the supervisor runs `systemctl --user freeze <scope>`, and `thaw` once the model has been idle for 10 s (supported on systemd 255).
  - A run frozen for more than 30 minutes is stopped and recorded as `deferred: busy`.
- **Cap what all runs can take together:** `CPUQuota=400%` and `AllowedCPUs=<4 cores>` on `puffin-index.slice` (§6.4), so two sessions indexing two repositories still share four cores; `CARGO_BUILD_JOBS=4` and the Go and MSBuild equivalents, `CBM_WORKERS=4` for codebase-memory. Newer rust-analyzer than the pinned 1.95.0 adds `--num-threads`; the router passes 4 once the toolchain has it.
- **One run at a time, coalesced:** at most one executing run in flight on the host (§6.4), and one run of each kind per repository; requests during a run collapse into one follow-up, which waits at least `code_index_min_interval` (default 15 minutes) unless it is `puffin-code index --exact`. A lock file makes this hold across sessions, and Night Shift's admission counts an exact run as work in progress.

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

- **Router unit tests** (`cargo test --locked` in `puffin-code-rs/`):
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
  - no test creates a real `systemd-run` scope or freezes a real unit: `puffin-index-*` scope creation is behind a seam the tests replace, the way `tests/conftest.py` refuses real mutating `docker` commands.
- **Sandbox sides (§4):** `puffin-code refs` run under Codex's `read-only` sandbox answers from both stores and writes nothing; under `workspace-write` a stale answer appends exactly one request line, and the session process coalesces ten such lines into one run.
- **scip-python's environment:** a fixture repository whose `.venv/bin/python3` and `sitecustomize.py` write marker files is indexed without either marker appearing.
- **Prompt:** the `# Code navigation` block is full when the index is ready, reduced while building, absent without a repository, and under 250 tokens.
- **The separate binary (§4.2):**
  - with `puffin-code` absent from the install directory, `puffin` launches, starts nothing and adds no block (launcher test, in the export);
  - the builder links `~/.local/bin/puffin-code` beside the other three commands, and refreshes the link when the binary is current (Python test, as for `puffin-admin`);
  - `puffin-code session` exits when its parent pid is gone, and a second one for the same repository exits at once while the first holds the lock;
  - a request file holding anything but the fixed words starts no run;
  - `puffin-code index` with no systemd user bus queues a request and starts no process.
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
    - every scope is created in `puffin-index.slice`, the slice's `MemoryMax` equals the sum of the live caps, and the indexer's command starts with `choom -n 1000`.
  - **Scheduler:** five triggers in quick succession produce one run and one coalesced follow-up; no run starts while the engine's request gauges are non-zero (fake `/metrics` for both `vllm:` and `sglang:` names); a frozen scope resumes on thaw with the same output as an uninterrupted run.
  - **Model load:** with a model container present but `/health` not answering, no run starts; a run in flight is stopped, not frozen (`deferred: model-start`); `puffin-admin server start` stops any `puffin-index-*` scope before its pre-flight.
  - **Worktrees:** a query in a linked worktree reads the main worktree's project, and files that differ are tagged stale.

## 13. Open questions and unverified claims

- **The first-run floors of §6.4** (512 MiB, 3 GiB, 8 GiB) come from one repository's measurements. A large Python or TypeScript project may need more than the 4 GiB small ceiling; it is then killed at its cap, recorded, and not retried in a loop (§6.1), which is safe and leaves that language on the universal layer. Whether the small ceiling should grow with a recorded peak, as the executing one does, is open.
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
- **The tightened sandbox is measured on a probe crate, not on Codex.** A full `rust-analyzer scip` of the Codex workspace with the home directory hidden and `$CARGO_HOME` read-only has not been run; a proc-macro or build script that expects something else under the home directory would fail there, and would be recorded as a failed run.
- **scip-java and scip-dotnet under read-only caches** are unmeasured; the redirections named in §9.1 are from the tools' documentation.
- **Connecting to an existing Unix socket from inside the sandbox** (the runtime directory is read-only there) is untested; the design does not rely on it (§4).
- **The changed-set search matches names, not symbols.** It cannot see a reference through an alias or a name built at run time, and it reports same-named symbols as `heuristic (text)`. How often an alias hides a new reference in practice is unmeasured; the re-index the query requests closes the gap within one run.
- **`expt-convert` rejects indexes a real indexer writes** (definitions without `SymbolInformation`); `scip-repair` (§14.2) covers the one case seen. Another rejection would leave that root on the universal layer, recorded as `failed`.
- **Several repositories:** start with the working directory only.

## 14. Implementation (2026-10-01)

`puffin-code` is built from `puffin-code-rs/` (its own lockfile, toolchain 1.95.0), installed and linked by `puffin-admin codex build`, and started by the launcher (`puffin-rs/src/code_index.rs`). `puffin-admin code setup` installs the pinned tools. 55 Rust tests (`cargo test --locked` in `puffin-code-rs/`) and 9 Python tests (`tests/test_code_index.py`, plus the builder's) cover it.

### 14.1 Measured on this repository

| Quantity | Value |
|---|---|
| Launch-time run through the session and supervisor | codebase-memory (3,689 nodes once `.cbmignore` listed the submodules: an incremental run drops `codex/`, which had been 132,225 of 137,176 nodes), scip-python over `dreamference/`, `tests/`, `scripts/` and two other tracked Python directories (peaks 166–629 MiB each), rust-analyzer over `puffin-code-rs/` and `puffin-web-rs/` (1,027 and 1,203 MiB), each in its own scope of `puffin-index.slice`, in the sandbox of §9.1 |
| Query latency, 20 runs each, warm, this repository | `refs ModelDownloader` p50 0.09 s, p95 0.11 s; `callers VLLMServerManager.check_health` p95 0.12 s; `def resolve_model_hf_repo` p95 0.12 s (§10's bound is 0.2 s) |
| The §2 replay | `refs ModelDownloader` now lists `diffusion_server_manager.py` and `sglang_launch_builder.py`: as `heuristic (text)` rows against the morning's snapshot, as `exact` rows once re-indexed; `tests/` callers are exact through the tests store |
| A hostile `build.rs` in the rust-analyzer sandbox | could not create a file in `$CARGO_HOME`, open `$CARGO_HOME/bin/cargo` for writing, read a marker in `$HOME`, or overwrite the store; the index was still written (test, run for real) |

### 14.2 Where the code differs from the text above

- **One store per run** (§6.2, §7.5): `scip/<indexer>-<root>.db` and `.scip`, with `manifest.json` keyed `<indexer>:<root>`, not a single `index.db`. A definition's references are gathered from every store that has a document at its location.
- **Documents outside a root** (§6.2): scip-python writes documents for what a root imports (`../dreamference/…` in the `tests` store). Paths are normalised back into the repository, snapshots stamp every file (not only the root's), and changed sets are computed without a root scope.
- **The changed set of an answer** (§7.3) is the graph's plus that of the stores holding the symbol, not the union over all stores: a stale Rust index no longer widens a Python name's text search and count.
- **Freshness cost** (§7.3): `git diff`, `git status`, `git ls-files` (for the graph's not-indexed files), `git submodule status` and two `git rev-parse`, not two calls; still 0.09–0.12 s per answer.
- **No `scip` crate** (§3, §7.5): a small protobuf wire reader decodes the chunks, streaming, instead of parsing a whole `Index`.
- **`expt-convert` stores no relationships and scip-python and rust-analyzer leave `display_name` empty.** Post-processing adds `puffin_names` (each symbol's descriptor name) and `puffin_relationships` (read from the `.scip`), excluded from the fingerprint. rust-analyzer emits no relationships at all: `impl` for Rust reads the `impl#[Type][Trait]member` symbol strings. Roles come from the decoded occurrences; `mentions.role` is a per-chunk set.
- **`scip-repair`** (new): scip-python wrote definitions without `SymbolInformation` for this repository's `tests/`, and `expt-convert` stops on the first. `puffin-code scip-repair` adds the missing entries (every other byte kept) inside the sandbox, before conversion.
- **Stores are switched from WAL to rollback journal** after conversion: a WAL database cannot be opened read-only where its directory is not writable (the read-only sandbox). Old `-wal`/`-shm` files are removed before a new store is renamed into place.
- **Identity** (§7.4): candidates come from the SCIP stores as well as the graph (a name the graph lacks, in a submodule or an ignored file, still resolves); a query may name a definition as `path:line`; an identity found by name keeps its references `exact` (only the definition is `heuristic`).
- **Only executing runs are frozen** (§9.2): a frozen codebase-memory missed its daemon's 30 s start-up deadline and failed; the universal and static runs take seconds and the slice's CPU limit bounds them.
- **Only a killed run records its cap as its peak** (§6.4): a run that failed otherwise kept a cap-sized "peak" whose need exceeded its ceiling, and was never admitted again.
- **The sandbox also hides `/run`, `/var/tmp` and `/dev/shm`** (§9.1): the Docker socket and the user's systemd bus are there, and a Unix socket on a read-only bind is still connectable. codebase-memory's own cache directory is its one writable bind outside scratch.
- **Detection** (§6.1): every top-level directory holding tracked `.py` files is a scip-python root (not only packages; untracked directories are the user's); a crate that inherits from a workspace elsewhere is not a root.
- **Tools** are resolved from Puffin's install directories only; scip-python runs on the `node` recorded at setup.
- **Test seams**: `PUFFIN_CODE_GRAPH_DB`, `PUFFIN_CODE_PROJECT`, `PUFFIN_CODE_STATE_DIR`, `PUFFIN_CODE_ROOT`, `PUFFIN_CODE_TOOLS_DIR`, `PUFFIN_CODE_INDEXERS_DIR`, `PUFFIN_CODE_SCRATCH_DIR`, `PUFFIN_CODE_SELF`; the tests also cut `DBUS_SESSION_BUS_ADDRESS` and `XDG_RUNTIME_DIR`, so none of them can reach the user's systemd. conftest refuses a mutating `systemctl` or any `systemd-run`, as it does `docker`.

### 14.3 Not built

- `impact` (§7.1); the prompt block does not name it.
- The fallback converter and the CLI fallback ladder (§4, §7.5): an unknown schema is reported, naming `puffin-code index` or `puffin update`, and the other layer answers.
- The scratch-copy exact index of a submodule (§6.2); `--include-submodules` changes only `.cbmignore`.
- scip-typescript, scip-clang, scip-java, scip-go and scip-dotnet (§6.1); search by meaning (§4).
- Night Shift scheduling (§6.3) and `puffin-admin mcp` re-pointed at the router (§8).

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
| Untrusted repository gets no executing index, whatever it ships | covered |
| scip-python's environment | covered, run for real |
| Session: lock, parent exit, junk requests, no systemd bus | covered |
| Admission: floor, headroom of live scopes, racing admissions, one executing run, slice limits, `choom` | covered with a fake host |
| Scheduler: coalescing, idle gauges of both engines, freeze and thaw | idle and loading covered; coalescing implemented, not tested; thaw not tested |
| Model load: no start while loading, stopped not frozen, `server start` stops the scopes | covered |
| Worktrees | covered |
| Output budget, stable pages, refused stale cursor, answer size | covered |
| Launcher: no `puffin-code`, no block | covered standalone; not yet run in the Codex export |
