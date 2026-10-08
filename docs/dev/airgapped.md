# `/airgapped`

Developer notes behind the `/airgapped` line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md`, whose §14 records what was built and what was not.

`/airgapped` has two levels, and `on` is enforced. `off` is the default; `on` gives every sandboxed command an empty network namespace.

A third level, `duckduckgo` (search through that engine alone), was removed on 2026-10-03 because DuckDuckGo answered SearXNG with a CAPTCHA, so the level searched nothing; a stored `duckduckgo` is now an invalid value, named in `/airgapped`'s status and passed over to the next tier.

## Resolution

The level is resolved by one std-only crate, `ling-rs/airgapped/` (`ling-airgapped`):

1. session file `$CODEX_HOME/airgapped/<thread-id>`;
2. `DREAMFERENCE_MIGHTLING_AIRGAPPED`;
3. `mightling_airgapped` in the config files, with the **strictest of the repository's and the user-level file winning** (the agent can write the repository's);
4. `off`.

## Load-bearing

- The enforcement hook is in **Codex's sandbox helper** (`linux-sandbox`'s `resolve_permission_profile`, patch `0019`), not in core, because core derives a command's network policy in a dozen places while every sandboxed command passes through the helper, which runs in the command's own environment and so finds the session by `CODEX_THREAD_ID`, then `CODEX_SESSION_ID` (a subagent takes its parent's level).
- The web crate carries a **byte-identical copy** of the resolver (`ling-web-rs/src/airgapped.rs`, a test compares them) because it is built outside the Codex workspace.
- Full Access has no sandbox, so the two are **never allowed together**: the launcher refuses Full Access (flag, `-c` or config file) at a configured `on`, the permission pickers show Full Access disabled at `on`, and `/airgapped on` is refused in a Full Access session, each saying why (AIRGAPPED §14.7). The app server refuses Full Access at `on` for every client (patch `0023`), and on Windows patch `0024` covers the elevated sandbox; see [codex-build.md](codex-build.md).

The launcher module `ling-rs/src/airgapped.rs` holds the command, `ling airgapped`, and the World State fragments. The desktop app asks `ling airgapped --thread <id>` rather than carrying a copy of the crate ([desktop.md](desktop.md)). SWE-bench cannot set `on`, because it runs with the sandbox bypass ([swe-bench.md](swe-bench.md)).
