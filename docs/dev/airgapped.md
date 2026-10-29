# `/airgapped`

Developer notes behind the `/airgapped` line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md`, whose §14 records what was built and what was not.

`/airgapped` has two levels, and `on` is enforced. `off` is the default; `on` gives every sandboxed command an empty network namespace.

**At `off` every sandboxed command has the network** (owner decision, 2026-10-22), whatever the permission mode: `pip install`, `npm install`, `curl` and `git fetch` work in Read Only too. Two settings carry it:

- the workspace-write sandbox: the launcher writes `[sandbox_workspace_write] network_access = true` when absent (`updated_config` in `ling-rs/src/lib.rs`);
- the read-only sandbox, which has no setting in Codex (`PermissionProfile::read_only()` is hard-wired to no network, and `-s read-only`, the TUI's Read Only, `/permissions` and an app-server `sandbox: "read-only"` all build that profile): patch `0031` widens it in `SandboxManager::transform` (`codex-rs/sandboxing`), the one place every platform's sandbox (bwrap's helper, Seatbelt, the Windows sandbox) takes its profile from and the place core reads back to set `CODEX_SANDBOX_NETWORK_DISABLED`. A command whose permissions are exactly the read-only ones gets `NetworkSandboxPolicy::Enabled` when `ling_airgapped::read_only_network` says so: not at `on` (resolved from the command's own environment, as the Windows hook does), and not when `DREAMFERENCE_MIGHTLING_READ_ONLY_NETWORK=off`. The launcher sets that variable when the user chose `sandbox_workspace_write.network_access = false` (a `-c` or any configuration layer), so the user's "no network" holds for both sandboxes. Not covered: a user-written `[permissions]` profile that extends `:read-only` and says nothing else compiles to the same profile and is widened too.

What the model is told follows: Codex words the read-only sandbox from the session's profile ("Network access is restricted."), so the launcher puts `model_messages.permissions.read_only` (`airgapped::READ_ONLY_SANDBOX_TEXT`, "Network access is enabled") into the catalog when the configured level is `off` and the user has not said no network; a change of level in the session is told by the World State texts as before. The Linux helper's hook from `0019` still forces `Restricted` at `on` and still denies the host's control sockets at `on` (only there since AIRGAPPED §14.13), so neither depends on `0031`.

A third level, `duckduckgo` (search through that engine alone), was removed on 2026-10-03 because DuckDuckGo answered SearXNG with a CAPTCHA, so the level searched nothing; a stored `duckduckgo` is now an invalid value, named in `/airgapped`'s status and passed over to the next tier.

## Resolution

The level is resolved by one std-only crate, `ling-rs/airgapped/` (`ling-airgapped`):

1. session file `$CODEX_HOME/airgapped/<thread-id>`;
2. `DREAMFERENCE_MIGHTLING_AIRGAPPED`;
3. `mightling_airgapped` in the config files, with the **strictest of the repository's and the user-level file winning** (the agent can write the repository's);
4. `off`.

## Load-bearing

- The enforcement hook is in **Codex's sandbox helper** (`linux-sandbox`'s `resolve_permission_profile`, patch `0019`), not in core, because core derives a command's network policy in a dozen places while every sandboxed command passes through the helper, which runs in the command's own environment and so finds the session by `CODEX_THREAD_ID`, then `CODEX_SESSION_ID` (a subagent takes its parent's level).
- **The host's control sockets are denied to every sandboxed command at `on`** (1.7.1; only at `on` since 2026-10-22, the owner's decision), in the same hook: the hook resolves the command's level once (`let on = ling_airgapped::sealed_for_command()`), and after the runtime permissions are derived, `ling_airgapped::host_sockets_at(on)` adds a deny entry for each one that exists (Docker and containerd, root and rootless, Podman, the D-Bus system and session buses, the systemd user manager, snapd, `$XDG_RUNTIME_DIR/mightling`), and bubblewrap masks it. A read-only bind of `/` does not stop a `connect()`: a command in `ling exec`'s workspace-write sandbox ran `docker restart` on the host on 2026-10-20 (the user is in the `docker` group). At `off` the workspace-write sandbox is Codex's own and the sockets stay reachable: **with `/airgapped` off the default sandbox does not protect the host from anything that can use Docker, and Docker access is root on the host.** Full Access has no sandbox at either level (Codex never starts the helper for it, `SandboxManager::select_initial` → `SandboxType::None`), so it reaches them too; the seals' folder stays readable. Tests: `tests/test_sandbox_host_sockets.py` (both levels), the crate's own (`the_host_control_sockets_are_masked_only_at_on`).
- **One reader** (since 1.8.1, spec §6.5): every Rust component links `ling-rs/airgapped/` (the web and docs crates by path dependency, which `CodexBrandedBuilder.crate_key` covers), and Python (`AirgappedResolver`) and the desktop app ask `ling airgapped --json`, taking `on` for any missing or malformed answer. Its conformance cases are `ling-rs/airgapped/cases.json`: the crate runs the `resolve` cases, `tests/test_airgapped.py` and `airgapped.test.ts` the `answer` cases. Python tests answer through a shell stand-in in `tests/conftest.py` (`REAL_AIRGAPPED_BINARY` restores the real one).
- Full Access has no sandbox, so the two are **never allowed together**: the launcher refuses Full Access (flag, `-c` or config file) at a configured `on`, the permission pickers show Full Access disabled at `on`, and `/airgapped on` is refused in a Full Access session, each saying why (AIRGAPPED §14.7). The app server refuses Full Access at `on` for every client (patch `0023`), and on Windows patch `0024` covers the elevated sandbox; see [codex-build.md](codex-build.md).

## Plugins

Codex's plugins (marketplaces, `ling plugin`, `/plugins`, and a plugin's skills, MCP servers, hooks and apps) are allowed at `off` since 2026-10-22, reversing a blanket refusal; the vendor's curated catalogue, remote catalogue and sharing stay closed at every level (patch 0015; `features.remote_plugin` and `features.plugin_sharing` forced off in `config.toml`). At `on` they are refused without a patch (`ling-rs/src/plugins.rs`; AIRGAPPED §14.12):

- **A session started at a configured `on`** (TUI, `exec`, the app server) gets `-c features.plugins=false`: no installed plugin loads, `/plugins` is hidden, and a user's `-c features.plugins=true` or `--enable plugins` is refused at launch.
- **`ling plugin add` and `ling plugin marketplace add|upgrade`** are refused at a configured `on` before Codex parses them (anything but `list`, `remove` and help): `git` fetches them in `ling`'s own process, outside the sandbox.
- **The desktop app and `ling web`**: `policy.json` refuses `plugin/install` and `marketplace/add|upgrade` at `on`, drops `PLUGINS` from an import, and gives every thread opener `config: {"features.plugins": false}`, so a thread opened at `on` has no plugins even when the app server started at `off`. The level is the configured one (`ling airgapped`, no thread): an install is not one thread's act. Conformance cases in `ling-rs/web/vectors/outgoing.json` carry `"airgapped": "on"`.
- **Not covered:** a TUI or `exec` session switched to `on` after it started keeps the plugins it loaded, and its `/plugins` can still install (the app server does not gate `plugin/install` or `marketplace/add` on the feature, and gating it needs a patch the series has no room for). The commands a plugin's skills run are sandboxed like any other, so they get the empty namespace; its MCP servers keep the network they started with, as servers the user configured do. `/airgapped` says so at `on`. `/import` stays hidden (patch 0012) for the same reason: it runs in-process and cannot follow the level.

## The `!` command

The app's `!` command is refused at `on` (2026-10-22, AIRGAPPED §14.13): `thread/shellCommand` runs in the user's shell with no sandbox, so `ling web` and the desktop app's main process refuse it for a thread at `on` (it is in `airgappedRefusedRequests` in `ling-rs/web/policy.json`, with its reason in `airgappedRefusalReasons`; [desktop.md](desktop.md)) and allow it at `off`, as Codex does. Unlike an install, it is judged by the level of the thread it names (`threadId`), not only the configured one. The TUI's own `!` is Codex's, unpatched, and runs at either level, like `/node` (spec §5.2).

The launcher module `ling-rs/src/airgapped.rs` holds the command, `ling airgapped`, and the World State fragments. The desktop app asks `ling airgapped --thread <id>` rather than carrying a copy of the crate ([desktop.md](desktop.md)). SWE-bench cannot set `on`, because it runs with the sandbox bypass ([swe-bench.md](swe-bench.md)).
