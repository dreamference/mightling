# The code index, `ling-code`

Developer notes behind the code-index line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_CODE_INDEX.md`, whose §14 records what was built and where it differs.

The code index is `ling-code`, a Rust binary of its own (`ling-code-rs/`, own lockfile). It answers `def`/`refs`/`callers`/`callees`/`impl`/`impact`/`show`/`outline`/`search` from two layers read directly as SQLite:

- codebase-memory's graph: approximate, every language;
- scip `expt-convert` stores: exact.
  - Static indexers for every repository: scip-python per tracked Python directory and per Python file at the root, scip-typescript, and on an explicit `ling-code index` scip-go and scip-clang.
  - Executing indexers only in trusted repositories: rust-analyzer, and scip-java and scip-dotnet on a scratch copy of the tracked sources.
  - Inside an included submodule all three run on a scratch copy and on the submodule's own trust, which it inherits only when it is provably yours.

scip-go, scip-java and scip-dotnet run on a toolchain `ling-admin code setup` records (Go, JDK 17+, .NET SDK 8+) and are skipped without one; scip-clang has no linux-arm64 build upstream, so C and C++ stay on the universal layer; `ling-code status` names what is missing.

## Three load-bearing rules

- **Freshness is decided for the repository**: every file changed since a snapshot is searched by name, so an edit is never a silent miss.
- **Queries only read**: they run inside Codex's sandbox; indexing belongs to `ling-code session`, which the launcher starts outside it.
- **Every index run is admitted against one host-wide memory budget** and runs in a network-less bwrap sandbox inside `mightling-index.slice` (`server start` stops those scopes before its pre-flight).

## Submodules

**A submodule is indexed only if it is yours or you ask** (§4.3, `src/submodules.rs`): same host and owner as the superproject *and* most of its recent commits by the superproject's authors, which is why `codex` (a fork under the same namespace) is not indexed here, while `ling-engine` is. (`fano`, the submodule that was indexed before, was removed on 2026-10-06.)

`ling-code submodules include|exclude|auto <path>` records the user's choice in `$CODEX_HOME/ling-code.toml`, never in the repository, where a clone or the agent could write it. The policy is recomputed on every query (about 50 ms of git), every answer names what is left out, and the git view descends into included submodules so an edit inside one is in the changed set.

## Build and tests

`ling-admin code setup` installs the pinned tools; `codex build` builds and links `ling-code` (build cache `~/.cache/dreamference/puffin-code-build`, old name kept). Night Shift refreshes indexes before its first task ([night-shift.md](night-shift.md)); SWE-bench can index each instance ([swe-bench.md](swe-bench.md)).

Test with `cargo test --locked` in `ling-code-rs/`; the fixtures' stores are recorded by `ling-code-rs/scripts/record-fixtures.sh`.

## Settings and memory rules (2026-10-30)

- The memory ceilings (`code_index_memory_ceiling_mb`, `code_index_small_ceiling_mb`) and `code_index_min_interval_s` come only from a settings file outside the repository. A `dreamference.toml` inside it (a clone ships its own) is read for the other keys; for these three it prints one stderr line naming the key and is ignored. `Settings::load` decides; `Settings::read_file` returns the warnings.
- A settings file that does not parse prints one stderr line with the file and the parse error, then the defaults stand.
- A run's remembered need is `min(1.2 x peak, the ceiling of its kind)` (`index::host::need`). If the last run ended within 10% of its cap (`peak_cap_bounded`), the peak is only a lower bound, so the need is not capped and `ling-code status` marks the entry "cap-bounded: the next need is not capped".
