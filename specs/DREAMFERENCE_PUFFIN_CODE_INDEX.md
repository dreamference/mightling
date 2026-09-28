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
  (one-shot CLI, JSON)           .dreamference/scip/index.db (§6.5)
  graph, BM25, semantic,         exact defs/refs for files unchanged
  incremental re-index at        since the snapshot
  launch / on demand (§3.1)
```

- **Both layers stay unmodified.** codebase-memory-mcp is invoked as a pinned binary. SCIP files are produced by the upstream indexers. The only new code is the router and the SCIP scheduler.
- **No long-lived process by default.** `puffin code …` runs codebase-memory's one-shot CLI, which by its README "leaves no standing process behind", and reads the SCIP query store (§6.5) with indexed lookups, never the raw `.scip` file. Freshness comes from incremental re-indexing at defined moments (§3.1), not from a resident daemon. codebase-memory's own watcher is opt-in.
- **Why Rust, in the launcher:** it keeps `puffin` self-contained. `puffin code` works in any shell with no Python environment, like `puffin app` and `puffin update`. The `scip` crate is the reference reader.

### 3.1 Lifecycle of the universal layer

`index_repository` is what creates a codebase-memory project at all, so the launcher owns when that happens:

- **First run:**
  - at `puffin` launch, the launcher calls `cli index_status` for the working directory's repository root;
  - if the repository has no project, it starts `cli index_repository --repo-path <root>` **detached**, under §8's memory limit and priority, and continues launching.
  - It never indexes synchronously in the agent's path; the session starts immediately with `rg` available.
- **Prompt timing:** the prompt is assembled once, at launch, so a session that starts during a first index would never hear about `puffin code` if the block were simply omitted. Instead:
  - **Index ready:** the full `# Code navigation` block (§7).
  - **Index building:** a reduced block: "a code index is being built; use `rg` until `puffin code status` reports it ready, then use `puffin code …`". `puffin code` commands answer "index not ready yet (N% done)" rather than failing obscurely.
  - **No repository, or indexing disabled or failed:** no block.
- **Keeping it current, without a daemon:**
  - an incremental `index_repository` runs detached at every `puffin` launch, which is cheap when little changed;
  - it also runs on `puffin code index`;
  - it does **not** run inside a query. A query that finds its result files newer than the last index (by the mtime and size check of §6.3) answers at once, marks those rows `heuristic (stale)`, and starts the incremental run detached for the next query. Re-indexing on the query path would put an unbounded wait in front of every `refs`/`impact`.
  - codebase-memory's background watcher is off (`watcher_enabled = false`, `auto_watch = false`) unless the user sets `puffin_code_watch = true`. That keeps §3's "no long-lived process" true.
- **Data:**
  - graphs live where codebase-memory keeps them, `~/.cache/codebase-memory-mcp/`;
  - `puffin code status` reports their disk use;
  - `puffin code forget [<repo>]` runs `delete_project`, and the launcher offers it when a project's root no longer exists.
- **Expected cost:** to be measured, as for §5.4. Its README claims the Linux kernel (75k files) in 3 minutes on an M3 Pro, so this repository (6.5k files) should index well under a minute, but that is unverified here.

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

| Found | Indexer | Command (run under §8's limits and §8.1's sandbox) |
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

- **Files:** `<repo>/.dreamference/scip/<indexer>.scip`, the query store `index.db` built from it (§6.5), and `manifest.json`: `{indexer, version, mode, commit, path_prefix, dirty_files, file_hashes, started, duration_s, peak_rss_mb, cap_mb, status}`. `status` is one of `ok`, `failed: <reason>`, `deferred: memory`, `deferred: busy` or `deferred: model-start`. `mode` is `full` or `no-macros` (§5.5). `.dreamference/` is already git-ignored.
- **Never index inside a submodule's checkout with a build tool that writes to it.** rust-analyzer runs `cargo metadata` and build scripts, and even `cargo tree` rewrites a submodule's `Cargo.lock`. This project's `codex/` workspace is indexed from the builder's exported copy (`~/.cache/dreamference/puffin-codex/src/codex-rs`), and paths are remapped back to `codex/codex-rs/…`. In general, a submodule is indexed from a scratch export (`git archive`), never in place.
- **`CARGO_TARGET_DIR` points at a scratch directory** so indexing never touches a build cache that another build is using.

### 5.3 When it runs

- **Automatic triggers, for trusted repositories only (§8):** in the background, never blocking a session:
  - at `puffin` start when there is no index, or the index is older than `HEAD` by more than N commits;
  - after a commit;
  - when idle.
- **On demand:** `puffin code index --exact`.
- **Before a risky operation:** when the router answers `references`/`impact` for a symbol whose files changed since the snapshot (§6.3). This only *requests* a run; the answer itself is given immediately, from both layers with stale rows tagged `heuristic`.
- **Every trigger goes through the scheduler of §8.2:** one run in flight per repository, later requests coalesced into one, a minimum interval between runs, idle-gated and frozen while the model is busy. A lock file, like the builder's `flock`, makes that hold across sessions.
- **Every start goes through §5.5's memory admission.**

### 5.4 Cost on this machine (measured 2026-09-28)

`rust-analyzer scip` (toolchain 1.95.0) on the Codex workspace, from the builder's exported copy, inside an 8 GB cgroup cap, while the model server was resident (about 13 GB of host memory available):

| Phase | Measured | Notes |
|---|---|---|
| 1. Compile and run build scripts and proc-macros | 560 of them. At least 2 min wall, 7 min 21 s CPU (about 3.7 cores on average, of 20) | Lower bound: the run was cut off by a harness timeout before phase 1 ended. The output is cached in the scratch target directory. |
| 1, second run | All 560 loaded from that cache in about 1 s | The cache makes phase 1 a one-time cost per dependency set. |
| 2. Analysis and SCIP emission | **OOM-killed by the 8 GB cgroup cap 78 s in** (1 min 17 s CPU) | The cap did its job: only the indexer died, not the host. No SCIP file has been produced for Codex yet. |

So for this repository, the exact layer **does not fit its own default budget**. Its true peak memory and total time are unknown. The next measurement is a run with a 24 GB cap while the model server is stopped; record the peak here. Section 5.5 is written so that the layer degrades safely until then.

### 5.5 Memory admission, not a fixed cap

A fixed cap either kills the run, as above, or has to be sized for the worst repository. On GB10 that is memory taken from the model. Exact indexing therefore goes through admission control:

- **Budget at start:** `cap = min(code_index_memory_ceiling, MemAvailable − code_index_reserve)`. The reserve protects the resident model and the session: default 8 GB, and 16 GB while a vLLM container is loading or serving.
- **Refuse rather than fail:** if the manifest has a previous `peak_rss_mb` for this indexer and repository, and `cap < 1.2 × peak_rss_mb`, the run is not started. The manifest records `status: "deferred: memory"`, and `puffin code status` says so. A run that is OOM-killed records the cap it died at as a lower bound for `peak_rss_mb`, so the next attempt is not doomed the same way. This makes `peak_rss_mb`, already in the manifest, the value that drives the decision.
- **Expected steady state on this machine:** given §5.4, the Codex exact index refreshes when the model server is stopped (`puffin-admin server stop`, or idle with no server). While the model is resident, the router serves the last snapshot, with changed files tagged `heuristic (stale)`.
- **Prefer the empty machine:** with no vLLM container resident, about 100 GB is available. Scheduled runs (§5.3) are placed there first: at `puffin-admin server stop`, or when the server is not running at an idle trigger. A run started while the model is resident gets the reduced budget above.
- **Cheaper configurations, to verify in §9:**
  - `rust-analyzer scip --config-path` takes a JSON Cargo configuration. `cargo.buildScripts.enable = false` and `procMacro.enable = false` skip phase 1 and should lower phase 2's memory. The cost is that code generated by `build.rs` or proc-macros is missing from the index, which matters for `serde`/`clap` derives and `thiserror`. A run with this configuration is recorded in the manifest as `mode: "no-macros"`. Every row it produces is tagged `exact (no-macros)`, and any answer that uses it carries a fixed header note: "derive- and proc-macro-generated code is absent: `impl` and `refs` into generated code are incomplete".
  - `--exclude-vendored-libraries` drops vendored code from the output.
- **Partitioning, as an option to measure, not a plan:** index the workspace in several passes. Each pass exports a scratch copy whose `[workspace] members` is a subset, and the resulting SCIP files are merged in the query store (§6.5). The merge is safe because rust-analyzer's symbol strings carry the crate name and version. The caveat is serious: `codex-cli`'s dependency closure is most of the workspace, so the passes that matter may peak almost as high as the whole. §9 measures the peak per partition before this is adopted.

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
exact = SCIP rust-analyzer @ 47f4d81; heuristic = codebase-memory (file edited since)
exact     codex-rs/cli/src/main.rs:1041
exact     codex-rs/tui/src/app.rs:318
heuristic codex-rs/exec/src/lib.rs:77
unresolved 1 call through `dyn ConfigSource` in codex-rs/core/src/lib.rs:2204
```

The source and commit are stated once in the header, not repeated per row. Paths tokenise poorly, so each row costs about 15–20 tokens even when it carries nothing but the tag, path and line.

`--json` gives the same data for tools. The `unresolved` line is mandatory whenever either layer reports calls it could not resolve. It is what tells the agent it has to verify.

**Every answer is bounded by construction.** The served model's context is 32,768 tokens (`max_model_len` in the registry), and that holds the system prompt, the conversation and every tool output. A `refs` on a common method can return hundreds of rows, so an uncapped answer could fill a fifth of the window by itself.

- **Row cap:** 40 rows by default (`--limit N`, hard ceiling 200). At 15–20 tokens a row, plus the header and at most 15 summary lines (next bullet, about 10 tokens each), a full answer stays under about 1,000 tokens. That is the §9 acceptance figure.
- **Grouping first, rows second:** above the cap, the answer opens with a per-file summary, sorted by count (`12  codex-rs/core/src/config.rs`), limited to 15 files. The rows follow, taken from those files.
- **The header always states what was cut:** `refs Config::load (312 results in 41 files; showing 40; next: --offset 40)`. The model can then page, or narrow the query, instead of assuming the list is complete.
- **Narrowing flags:**
  - `--path <glob>`;
  - `--kind def|read|write|import`, from SCIP `symbol_roles`;
  - `--exact-only`;
  - `--offset N`, a stable cursor that is invalidated with an error, not silently shifted, if the index changes between pages.
- **Use the upstream limits:** codebase-memory's CLI already takes `result_limit`/`result_offset` and `max_output_tokens`. The router passes the budget down rather than fetching everything and cutting afterwards.
- **The prompt block is budgeted too:** the `# Code navigation` block of §7 rides on every turn, so it is kept under 250 tokens. The command list is one line each, and details live in `puffin code --help`.

### 6.3 Freshness and merging

- **A file is fresh for SCIP** when its current content hash equals `file_hashes[path]` in the manifest.
- **Only the files an answer touches are checked, and cheaply:** the definition's file plus the files in the rows being printed, not the whole repository. Each is checked by `(mtime, size)` against the manifest first, and hashed only if either differs. A 40-row answer therefore costs at most 41 `stat` calls in the common case, not 4,800 hashes.
- **`refs` and `def`** use SCIP rows for fresh files and codebase-memory rows for stale or unindexed files.
- **Rows present in both** are de-duplicated by `(path, line)`. The SCIP row wins and is tagged `exact`.
- **Stale share:** if more than 20% of the files a result touches are stale, the router *requests* a background SCIP run from §8.2's scheduler, which coalesces it with other requests, and says so in the output header. It never runs one in the query's path.

### 6.4 Joining the two layers: symbol identity

The layers name things differently. codebase-memory has qualified names such as `Config::load`. SCIP indexes nothing by name: every occurrence carries an opaque *symbol string* (rust-analyzer writes e.g. `rust-analyzer cargo codex-core 0.158.0 config/Config#load().`) and a `symbol_roles` bitmask in which `Definition = 1`. The shared key is therefore the **definition's location**, and the join runs as follows:

1. **Name to candidates.** The agent types a name as it sees it in code: `Config::load`, `load`, `dreamference.runner.codex_runner.CodexRunner.run_session`. `search_graph` returns candidate definitions, each with `(path, range)`. If there are several, the router lists them with their paths and asks for a qualified name; it never guesses.
2. **Location to SCIP symbol.** For the chosen definition, the router looks up the SCIP `Document` for that path, through the query store of §6.5, never by decoding the `.scip` file at query time. It takes the occurrence whose `symbol_roles & Definition` is set and whose range overlaps the definition's name range. That occurrence's symbol string is the exact identity.
3. **Symbol to exact references.** Every occurrence of that symbol string across all SCIP documents is an exact reference. Its roles distinguish reads, writes and imports, and `relationships` give `is_implementation`, `is_reference` and `is_type_definition`, which answer `impl`.
4. **Fallback when step 2 cannot run.** The definition's file may be stale (hash mismatch, §6.3), or have no SCIP document. The router then searches the SCIP symbol table (`SymbolInformation`, by `display_name` and by symbol-string suffix, e.g. `Config#load().`). It does this through the store's indexed `display_name` and reversed-symbol columns, not by scanning every document.
   - Exactly one match: use it, and tag the definition `heuristic` (located by name, not by position).
   - Several matches: list them.
   - No match: codebase-memory only, all rows `heuristic`.
5. **Coordinates.** SCIP ranges are **0-based** lines and columns (`[startLine, startChar, endLine, endChar]`, or three elements when the range is on one line). The coordinate convention of codebase-memory's CLI output must be pinned against v0.11.0 with a recorded fixture. All rows are normalised to **1-based lines** (what editors and `rg` show) before de-duplication on `(path, line)` and before printing. An off-by-one here would silently turn every overlap into a duplicate pair, so the fixture test in §11 checks it.
6. **Path mapping.** SCIP documents store paths relative to the indexed root. For an exported copy (§5.2) the manifest records `path_prefix` (e.g. `codex/codex-rs/`), and it is prepended before comparison. Hashes are compared against the file at the mapped repository path, so files that differ from the export (patched, or uncommitted) are simply stale, which is the safe direction.

### 6.5 The query store

A `.scip` file is one protobuf message covering the whole project. Decoding it in every `puffin code refs` would cost seconds and gigabytes per query on a workspace this size, in a process that exists for a single answer. So the router pays that cost once per index run:

- **Conversion after each successful run:** `puffin-rs` decodes the `.scip` file with the `scip` crate and writes `.dreamference/scip/index.db`, a SQLite database, then replaces the previous one atomically (write `index.db.new`, then `rename`).
  - A SCIP index is a single protobuf message, so it is decoded whole, not streamed. The conversion's peak memory is a small multiple of the file size.
  - It is written by the router, not by the `scip` CLI, because whether the pinned `scip` CLI has a SQLite export is unverified.
  - The conversion runs inside the same memory admission (§5.5) and scheduling (§8.2) as the indexer.
- **Tables:**
  - `documents(id, path, mtime, size, hash, source_run)`: this is also where §6.3's freshness facts live;
  - `symbols(id, symbol, display_name, kind, rev_symbol)`, where `rev_symbol` is the symbol string reversed, so that suffix lookups become prefix scans;
  - `occurrences(symbol_id, document_id, line, col, end_line, end_col, roles)`, stored **already 1-based**, so §6.4 step 5 is paid once;
  - `relationships(symbol_id, target_id, kind)`, for `impl`.
  - Indexes on `occurrences(symbol_id)`, `occurrences(document_id, line)`, `symbols(symbol)`, `symbols(display_name)` and `symbols(rev_symbol)`.
- **Several SCIP files merge into one store:** one per indexer, or per partition (§5.5). Rows are keyed by symbol string, which is globally unique, and `source_run` records where each document came from, so a partition can be replaced without rebuilding the others.
- **Query cost:** opening the database read-only and running two or three indexed lookups is milliseconds, and stays so as the repository grows. Queries never touch the `.scip` file. It is kept only so the store can be rebuilt without re-indexing.

## 7. Agent interface

- **Prompt:** the launcher appends a `# Code navigation` block to the model's prompt, next to web access and Gmail. The block covers:
  - the commands in §6.1;
  - the meaning of `exact`/`heuristic`/`unresolved`;
  - one rule: *before changing a signature, renaming or deleting, run `puffin code refs`. If any row is `heuristic` or `unresolved`, confirm with `rg` and run the build or tests after the edit.*
- **Ready, building or absent:** the block is full, reduced or absent according to the index state at launch (§3.1), so the model is never told to rely on a command that cannot answer, and is still told the index is coming.
- **MCP:** `puffin code mcp` serves the same operations over stdio for Claude Code and IDEs. It replaces jCodeMunch in `~/.claude.json` once this is implemented. The local model keeps using shell commands, because it does not reliably call MCP tools under Codex's Code Mode (see `codex_runner.py`'s history).

## 8. Resources and safety

On GB10, host RAM and GPU memory are the same memory, and running it out can freeze the host, not just kill a process (see `psi_watchdog.py`).

- **Memory limits:**
  - SCIP indexing always runs under `systemd-run --user --scope -p MemoryMax=<cap> -p MemorySwapMax=0`, so exhaustion OOM-kills the indexer instead of stalling the host.
  - `<cap>` comes from §5.5's admission control, not a fixed default. A fixed 8 GB was measured to be too small for the Codex workspace (§5.4).
  - codebase-memory indexing runs under the same kind of limit, with its own ceiling. Its pipeline is RAM-first, per its README.
- **Sharing the machine with inference:** see §8.2. `nice`/`ionice` alone are not enough on this machine.
- **Watcher:** codebase-memory's watcher is off unless the user sets `puffin_code_watch = true` (§3.1). That maps to its `watcher_enabled` / `auto_watch` settings.

### 8.1 Exact indexing executes project code

SCIP indexing is not a passive read. The first rust-analyzer run on this machine compiled **and ran** 560 build scripts and proc-macros from the Codex dependency tree (`Loading build script aws-lc-sys run` in its log). Indexing an untrusted repository with the exact layer means running that repository's code.

| Indexer | Executes project code? | Why |
|---|---|---|
| rust-analyzer | **yes** | Runs `build.rs` scripts and proc-macros, and `cargo metadata`, which fetches missing crates unless offline |
| scip-java | **yes** | Runs the Gradle, Maven or sbt build |
| scip-dotnet | treat as **yes** | MSBuild evaluation and restore run project-defined logic |
| scip-go | no code, but **network** | Loads packages via `go list`, which downloads modules unless offline |
| scip-clang | no, given an existing `compile_commands.json` | Parses with Clang. *Generating* the compdb (CMake, `bear -- make`) does execute the build, and is the user's job, not the indexer's |
| scip-typescript | no | Static analysis with the TypeScript compiler, over `node_modules` as present |
| scip-python | no | Static analysis with Pyright |
| codebase-memory-mcp | no | Parses with tree-sitter and its own resolver; runs nothing from the repository |

Three rules follow.

- **Trust gate.**
  - Exact indexing runs only for repositories the user has marked trusted: `trusted = true` in `<repo>/.dreamference/code_index.toml`, or Codex's own per-project trust (`[projects."<path>"] trust_level = "trusted"` in `$CODEX_HOME/config.toml`, the `ProjectConfig.trust_level` the TUI already asks about).
  - Untrusted repositories get the universal layer only, and `puffin code status` says so in one line.
  - Indexers marked "no" in the table may run on an untrusted repository on demand (`puffin code index --exact`), still inside the sandbox, but never automatically.
- **Sandbox, in addition to the memory limit.** Every SCIP indexer runs as `systemd-run … -- bwrap …`, with the network removed and the filesystem read-only except for its outputs. For rust-analyzer:
  ```
  systemd-run --user --scope --unit="puffin-index-$REPO_ID" \
    -p MemoryMax="$CAP" -p MemorySwapMax=0 \
    -p CPUQuota=400% -p AllowedCPUs="$INDEX_CPUS" -- \
    nice -n 10 ionice -c3 \
    bwrap --die-with-parent --unshare-net --unshare-pid \
          --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
          --bind "$SCRATCH_TARGET" "$SCRATCH_TARGET" \
          --bind "$REPO/.dreamference/scip" "$REPO/.dreamference/scip" \
          --bind "$CARGO_HOME" "$CARGO_HOME" \
          --setenv CARGO_NET_OFFLINE true --setenv CARGO_TARGET_DIR "$SCRATCH_TARGET" \
          --setenv CARGO_BUILD_JOBS 4 \
          -- rust-analyzer scip "$SRC" --output "$REPO/.dreamference/scip/rust-analyzer.scip"
  ```
  - `/usr/bin/bwrap` is already installed; it is Codex's own sandbox.
  - The scope is named (`puffin-index-<repo id>`) so that §8.2's poller can `freeze`/`thaw` it, and so that `puffin code status` can find a run in progress. `$CAP` comes from §5.5, and `$INDEX_CPUS` is the four-core set of §8.2.
  - The router creates `$REPO/.dreamference/scip` and `$SCRATCH_TARGET` beforehand, because bwrap cannot bind a path that does not exist.
  - `$CARGO_HOME` is writable only because Cargo takes a lock file there even when offline; with no network, nothing can be fetched into it.
  - `$SRC` is read-only. If Cargo needs to rewrite the lockfile, the run fails and is recorded. The remedy is an exported scratch copy, as for `codex/` (§5.2), where `$SRC` is additionally bound writable.
- **Offline is enforced, not assumed.**
  - Environment: `CARGO_NET_OFFLINE=true`, `GOFLAGS=-mod=readonly`, `GOPROXY=off`, and `npm_config_offline=true`.
  - Nothing ever runs `npm install`, `pip install` or a dependency download on the indexer's behalf.
  - An indexer that needs something not already on disk fails, and the manifest records `status: "failed: offline"`. That is the accepted outcome, not a retry.
  - The Codex workspace qualifies because the puffin build already filled `~/.cargo/registry` with its dependencies.
- **Air-gapped, stated precisely:** no network access is *permitted* to any indexer at index time, and none is used at query time. The only downloads happen at install (`puffin-admin code setup`, `puffin update`), each pinned by checksum like the rusty_v8 archive.
- **Scope:** only the current repository is indexed, or explicitly listed ones. `~`, `/` and anything over `code_index_max_files` are never indexed (default 50,000, codebase-memory's `auto_index_limit`).

### 8.2 Indexing must not slow the model

On GB10, token generation is limited by memory bandwidth, and CPU, GPU and model weights share the same LPDDR5X. Background indexing competes for that bandwidth, which `nice` and `ionice` do not limit. Phase 1 of §5.4 averaged about 3.7 busy cores and would take more if allowed. The number to protect is the single-stream decode rate recorded in `CLAUDE.md` for the default model: prose 23.8, code 49.9, JSON 53.1 tokens/s.

- **Start only when the model is idle.** vLLM's `/metrics` exposes `vllm:num_requests_running` and `vllm:num_requests_waiting` (verified on this server). An exact-indexing run starts only when both are 0, or when no vLLM container is running (§5.5).
- **Never alongside a model load.**
  - A vLLM container that exists (`docker ps`) but does not answer `/metrics` is **loading**. Nothing starts then, because §5.5's admission would wrongly pass: `MemAvailable` is still high before the weights are mapped.
  - If a run is in flight when a load begins, it is **stopped**, not frozen, because frozen memory stays resident. It is recorded as `status: "deferred: model-start"`.
  - `puffin-admin server start` itself stops every `puffin-index-*` scope before its host-safety pre-flight, which is one `systemctl --user stop 'puffin-index-*'` thanks to the named scope of §8.1. This keeps the unified-memory guarantee of `check_host_safety()` intact.
- **Pause instead of competing:**
  - The indexer cannot watch the model itself: it runs in `bwrap --unshare-net` and has no route to `localhost:8000`.
  - So `puffin code index` starts a detached **supervisor process** outside the sandbox. The supervisor creates and owns the scope, polls `/metrics` every 2 s, applies the rules above, enforces the frozen-time limit, writes the manifest, and exits when the scope ends. It lives exactly as long as one run, so §3's "no long-lived process" holds.
  - When a request appears, the supervisor runs `systemctl --user freeze <scope>`, and `thaw` once the model has been idle for 10 s (verified supported on systemd 255).
  - Freezing keeps every byte of progress, so a long run finishes in the model's idle gaps rather than being killed and restarted.
  - A run that stays frozen for more than 30 minutes is stopped and recorded as `status: "deferred: busy"`.
- **Cap what it can take while running:** `-p CPUQuota=400% -p AllowedCPUs=<4 cores>` on the scope (both verified accepted on systemd 255), with `CARGO_BUILD_JOBS=4` and the Go and MSBuild equivalents. Four cores is the default, configurable as `code_index_cpus`. The same caps apply to codebase-memory's `index_repository`.
- **One run at a time, coalesced:** several triggers can each ask for a multi-minute whole-workspace run: after a commit, when idle, at launch, and when too many files are stale (§6.3). The scheduler keeps at most one exact run in flight per repository. Requests that arrive during a run collapse into a single follow-up, and a follow-up waits at least `code_index_min_interval` (default 15 minutes) after the previous run ended, unless it is `puffin code index --exact`.

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
   - index time and peak memory on the Codex workspace, for each of:
     - the full exact index;
     - the `no-macros` configuration;
     - each candidate partition (§5.5).
   - decode tokens/s of the default model while an exact index runs, frozen and unfrozen, against the `CLAUDE.md` baseline (§8.2).
5. **Acceptance:**
   - codebase-memory reaches at least 90% recall on Rust references and 95% on Python;
   - its answers cost fewer tokens than the `rg` baseline;
   - it indexes this repository within the §8 limits;
   - **query latency:** p95 at most 200 ms for `refs`/`def` on a warm page cache, from process start to last byte;
   - **answer size:** no answer exceeds 1,000 tokens at the default limit;
   - **inference cost:** the model's decode rate while indexing runs within §8.2's caps stays within 5% of baseline. With the scope frozen it is indistinguishable from baseline;
   - **exact layer admitted:** the exact index for Codex completes under §5.5's admission control with the model server stopped. The peak is recorded in §5.4. If the peak exceeds what is available with the model resident, the exact layer for Codex is documented as "model-stopped only".

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
  - no network access during `index` or queries: a fixture crate with a missing dependency must fail with `failed: offline`, not fetch it;
  - an untrusted fixture repository never gets an exact index, and its `build.rs` (which writes a marker file) never runs.
- **Join (§6.4):**
  - a recorded SCIP fixture and a recorded codebase-memory v0.11.0 CLI response for the same file resolve to the same symbol;
  - their rows de-duplicate to one per `(path, line)`, which catches a 0-/1-based mismatch;
  - a stale definition file falls back to the `display_name` search and tags the result `heuristic`.
- **Prompt:** the `# Code navigation` block is full when the index is ready, reduced while it is building, and absent without a repository (§3.1). It stays under 250 tokens.
- **Performance:**
  - **Output budget:** a fixture symbol with 500 references prints the totals header, at most 15 file-summary lines and 40 rows. `--offset 40` returns the next 40. An `--offset` taken before a re-index fails with an error rather than shifting.
  - **Query store:** `refs` never opens the `.scip` file (the test deletes it after conversion and queries still succeed). The store's lines are 1-based. Two partitions merged into one store answer a cross-partition `refs`.
  - **Freshness cost:** a `refs` on a 40-row result `stat`s at most 41 files and hashes none when nothing changed.
  - **Admission:** with `peak_rss_mb` recorded above the available cap, the run is not started and the status is `deferred: memory`. An OOM-killed run records its cap as the new lower bound.
  - **Scheduler:** five triggers in quick succession produce one run and one coalesced follow-up. A run is not started while `vllm:num_requests_running > 0` (fake `/metrics` endpoint). A scope frozen mid-run resumes on thaw and completes with the same output as an uninterrupted run.
  - **Model load:** with a vLLM container present but `/metrics` not answering, no run starts. A run in flight is stopped, not frozen, and recorded as `deferred: model-start`. `puffin-admin server start` stops any `puffin-index-*` scope before its pre-flight.

## 12. Open questions and unverified claims

- **codebase-memory's figures are its own:** the "Hybrid LSP" accuracy ("target ~95% resolution on idiomatic code") and the performance figures (Linux kernel in 3 min on an M3 Pro). §9 measures them here.
- **Its CLI output shape** for `trace_path`/`detect_changes` in `--format json` has to be pinned in the router against v0.11.0. A version bump could change it, so the router's parser is tested against recorded fixtures.
- **SCIP freshness for rust-analyzer is whole-workspace.** Per-crate re-indexing would make the exact layer much cheaper after small edits. Whether rust-analyzer's `scip` command can be scoped to a crate is not verified.
- **Licences of the non-Rust SCIP indexers** must each be checked before they are bundled.
- **True cost of the exact layer on Codex:** peak memory and total time are unknown. The one full run was OOM-killed at 8 GB (§5.4).
- **Does partitioning lower the peak?** It may not, because the dependency closure of the crates that matter is most of the workspace (§5.5).
- **What the `no-macros` configuration saves** in memory, and what it loses in references, is unmeasured.
- **The `scip` CLI's SQLite export:** whether the pinned version has one is unverified. §6.5 does not rely on it.
- **Does freezing a scope mid-analysis leave rust-analyzer healthy?** It is a plain `SIGSTOP`-equivalent via cgroup freeze, so it should, but that is untested.
- **Several repositories:** whether `puffin code` indexes several repositories per session, or only the working directory's, is left to implementation. Start with the working directory only.
