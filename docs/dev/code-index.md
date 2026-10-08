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
