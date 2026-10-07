# Puffin Airgapped — `/airgapped`

**Status:** Phase 1 partly implemented on 2026-10-01: the levels, `/airgapped` and `puffin airgapped`, the sandbox enforcement of `on`, the web commands, and the configuration field. §14 records what was built, where it departs from the design below (the enforcement hook is in the sandbox helper, not in core), what ran live, and what is not built. The rest of this document is the design as specified; §9 lists what was checked before the build. **On 2026-10-03 the `duckduckgo` level was removed** (§14.6): DuckDuckGo answered SearXNG with a CAPTCHA, so the level searched nothing. The design below still describes it; where it does, it is history.
**Goal:** one command that says how much of a `puffin` session may reach the internet, with two levels: everything (the default) and nothing at all. (A third, search through DuckDuckGo only, was removed on 2026-10-03; §14.6.)
**Target:** the `puffin` terminal agent. The web chat is not covered (§8).
**Builds on:**
- the web commands `puffin-search` and `puffin-fetch` ([PUFFIN_CODEX §4.1](./DREAMFERENCE_PUFFIN_CODEX.md)), and the SearXNG instance on `127.0.0.1:8888` they search through;
- Codex's command sandbox, which already runs a command with no network when its policy says so (`bwrap --unshare-net`);
- the pattern `/cavemode` set for a per-session level: a file keyed by the session, configuration tiers, a World State section ([CAVE_MODE §3, §5](./DREAMFERENCE_PUFFIN_CAVE_MODE.md));
- the airlock of [PUFFIN_EGRESS §4](./DREAMFERENCE_PUFFIN_EGRESS.md), which becomes the second layer of this spec's strictest level (§5.4).

**One hook patch**, `0019-airgapped` (§6.4, as built in §14), which raised the patch-size cap explicitly.

---

## 1. Levels

| Level | Search | `puffin-fetch` | Gmail | Network for commands the agent runs | Enforced by |
|---|---|---|---|---|---|
| `off` (default) | Every engine SearXNG has enabled | Any page | Yes | Yes | Nothing to enforce: this is today's behaviour |
| ~~`duckduckgo`~~ | DuckDuckGo only | Any page | Yes | Yes | The web commands (§4). A preference, not a barrier. **Removed 2026-10-03** (§14.6) |
| `on` | None | None | None | None | The kernel (§5) |

**`off` is today's `puffin`.** On this machine a general search currently goes to Brave, DuckDuckGo, Google (through `google cse`), Startpage (which serves Google's results), Wikipedia and Wikidata, plus four small answer engines. SearXNG sends them the query text; they see SearXNG's address, not an account.

**`duckduckgo` changed who read the queries, and nothing else** (removed, §14.6). Search goes to DuckDuckGo alone. Fetching a page, Gmail and the agent's own commands (`git`, `pip`, `cargo`, `curl`) work as at `off`: the level is about which search company sees what the session is looking for.

**`on` is the only level that may be called air-gapped.** Nothing the agent runs can open a connection to anything, on the internet or on this machine. Search, fetch and Gmail are off because each sends something out: a query to an engine, a URL to a site, a mail search to Google.

**What still talks at `on`,** all of it on this machine:
- `puffin` itself to the model server (`vllm_host`). If that is another machine on the LAN, prompts go there, as the user configured.
- The launcher's check of the Gmail service on `127.0.0.1:8767` is skipped at `on`, since Gmail is not offered.
- The code index reads its files; it has no network code.

**Names.** The command is `/airgapped`, so `on` and `off` read as answers to it. No document, message or prompt text may call `off` or `duckduckgo` air-gapped; [PUFFIN_EGRESS](./DREAMFERENCE_PUFFIN_EGRESS.md) already holds the docs to that.

---

## 2. The command

`/airgapped` is a built-in slash command of the `puffin` TUI. It writes one small file (§6.1) and prints a few lines. It never calls the model, so it answers at once, including while a turn is running.

| Form | Effect |
|---|---|
| `/airgapped` | Shows the level in force, where it came from, the levels with one line each, and what is and is not enforced right now |
| `/airgapped <level>` | Sets `off` or `on` for this session. Applies to the next command the agent starts, mid-turn included |
| `/airgapped default <level>` | Also writes `puffin_airgapped = "<level>"` to the user-level configuration file (§6.1), so new sessions start at it |
| anything else | Prints the usage line. Nothing changes |

What `/airgapped` prints at the default:

```
Airgapped: off (default)
  off         search through every engine SearXNG has enabled; pages fetched directly   ← in force
  on          no network for anything the agent runs: no search, no fetch, no Gmail
Not covered at any level: the web chat, MCP servers you configured.
Change: /airgapped on (this session) or /airgapped default on (new sessions).
```

At `on` it adds one line saying whether the level is enforced, for example `Enforced: commands run with no network (workspace-write sandbox).` or, in the cases of §5.3, a warning that starts `NOT ENFORCED:` and names the reason.

**Rules:**
- **The level belongs to the session and to everything it starts.** `puffin resume` keeps it. Subagents, forks and side conversations take their parent's level: a restriction that a subagent could step around is not one. (This differs from cave mode, where a subagent starts at the default.)
- **The model cannot change it.** The session file lives under `$CODEX_HOME`, which the command sandbox cannot write, with one exception that §5.3 closes: a session whose working directory contains `$CODEX_HOME`. A configuration file in the repository can only tighten the level (§6.1), because the agent, or a clone, can write there.
- **A switch does not reach back.** A command already running when the level changes keeps the network it started with. Going to `on` mid-turn applies from the next command. What the session already read from the web stays in its history.
- **`puffin exec` and scripts** take the configured level, or `DREAMFERENCE_PUFFIN_AIRGAPPED=on puffin exec …` for one run.
- **From a shell:** `puffin airgapped` prints the same status for the configured level, and `puffin airgapped default <level>` sets it, as `puffin night …` mirrors `/night`.

---

## 3. What the model is told

The system prompt stays one text for every level, so the prefix cache survives a switch ([CAVE_MODE §5.1](./DREAMFERENCE_PUFFIN_CAVE_MODE.md)). The level reaches the model as a World State section, `airgapped`, whose value is the level name. A fragment is added to the conversation only when the value changes:

| Change | Fragment |
|---|---|
| Session starts at `off` | None. The session is exactly today's |
| To `duckduckgo` | Search uses DuckDuckGo only. If it does not answer, say so; there is no other engine at this level and nothing to restart |
| To `on` | This session has no network. `puffin-search`, `puffin-fetch`, `puffin-admin gmail`, `curl`, `git fetch`/`push` and package installs fail by design. Do not try them, and do not ask to run a command outside the sandbox to get around it. Work from the files here; when an answer needs something you cannot look up, say which part is from memory and may be out of date |
| Back to `off` | The earlier restriction no longer applies; web access is as the system prompt describes |

The exact texts go in an appendix when they are written, and are checked against the live model first (§10, Phase 0).

**One change to the system prompt.** `WEB_ACCESS_INSTRUCTIONS` says "Do not say you cannot browse the web. You can". It gains one clause: unless a later message says web access is off for this session. Without it, the two instructions contradict each other at `on`. This is a one-time change of the prompt text, not a per-level one.

**Gmail.** The launcher adds the Gmail section to the prompt at start when an account is connected. A session that starts at `on` does not get it. A session switched to `on` later still has it in its prompt, and the `on` fragment says Gmail is unavailable.

**After compaction** the fragment must still be in the history, so the section uses a retained-fragment matcher, as cave mode's does. Unlike cave mode there is no per-turn reminder: the level is enforced outside the model at `on` and by the web commands at `duckduckgo`, so the model forgetting it costs a failed command, not a leak.

---

## 4. `duckduckgo`: the web commands follow the level

> **Removed on 2026-10-03** (§14.6). The web commands now send `categories=general` at `off` and refuse at `on`; the rest of this section is the design as it was.

`puffin-search` reads the level before it sends anything (§6.2):

- **`off`:** as today, `GET /search?q=…&format=json&categories=general&language=en`.
- **`duckduckgo`:** `GET /search?q=…&format=json&engines=duckduckgo&language=en`, with **no `categories` parameter**. SearXNG adds the engines of a named category to the ones in `engines`: with both parameters it queried all five general engines here, and with `engines` alone only DuckDuckGo (§9).
- **`on`:** it refuses before any request, exits non-zero and prints `Web access is off in this session (/airgapped on). Only the user can change that, with /airgapped.`

`puffin-fetch` refuses the same way at `on` and is unchanged at the other two levels.

**The error texts change with the level.** Today an unreachable SearXNG prints the `docker run` line that starts it, and a search in which every engine failed suggests restarting the container. Both would send the model off to repair something that is not broken:
- at `on`, neither hint is ever printed (the refusal above comes first);
- at `duckduckgo`, when DuckDuckGo does not answer, the error is `DuckDuckGo did not answer (<reason>). This session searches through DuckDuckGo only (/airgapped duckduckgo).` with no restart hint.

**`--json` output** gains `"airgapped": "<level>"`, so a script can tell a refused search from an empty one, and each result gains `"engines"`, the full list SearXNG reports for it. Today a result carries only `engine`, the first of them, which cannot show that no other engine contributed.

**This level is a preference, and the spec says so everywhere it is described.** A command the agent runs could still ask SearXNG for another engine directly (`curl '127.0.0.1:8888/search?engines=google…'`). The threat this level answers is a search company reading the queries of a model that is doing what it was told, not a model working against its user. A user who needs a barrier uses `on`.

**Risk: one engine is less reliable than five.** DuckDuckGo rate-limits SearXNG instances from time to time, and at this level nothing else can step in. Phase 0 measures how often (§10).

**Gmail stays on at this level.** The user connected the account on purpose, and a Gmail search goes to the user's own mailbox, not to a search engine. If that turns out to be the wrong line, it is a one-word change in the table of §1.

---

## 5. `on`: enforced by the kernel

### 5.1 The command sandbox

Every command the agent runs goes through Codex's sandbox. Under `workspace-write`, the launcher today sets `[sandbox_workspace_write] network_access = true`, so the web commands work. At `on` the sandbox policy for a command is the restricted one whatever the configuration says, so `bwrap` gives the command a network namespace of its own with nothing in it. `curl`, `git fetch`, `pip install`, `puffin-search` and a hand-written socket all fail the same way, because none of them has an interface to send on.

**How it composes with the user's own sandbox choice.** `on` forces the restricted policy; at the other two levels the sandbox's own answer stands. `puffin exec` without `-s` runs read-only, which has no network at any level, so the web commands fail there at `off` as they do today; the level does not grant what the sandbox withholds.

**One hook decides it, per command.** The session's network policy is computed in one place in Codex's core (`TurnContext::network_sandbox_policy()`, from the permission profile). The patch adds one line there: when `puffin_launcher::airgapped::sealed(session)` is true, the answer is `Restricted`. `session` is the same root-session key the level file uses (§6.1), so the hook and `/airgapped` can never read different files. That covers the TUI, `puffin exec`, a resumed session, a subagent and a switch in the middle of a turn, because it is asked again for every command. Phase 0 confirms that this is the only path a command's network policy takes, and that the core crate can depend on the launcher crate without a cycle (§9). If the dependency would be a cycle, the resolution moves into a leaf crate, `puffin-airgapped`, with no dependency beyond the standard library, which both the core and the launcher use; the hook stays one line. The other route, in which the launcher passes `-c sandbox_workspace_write.network_access=false` at start and `/airgapped on` sends what `/permissions` sends, is rejected: it misses a session resumed under a different configured level, and it turns the slash command's one-line arm into policy construction inside a patch.

### 5.2 What `on` switches off beside the sandbox

- **`puffin update`** keeps working at `on` (decided 2026-10-03). The level is about what a session's commands may reach; an update is the user replacing the binaries from a shell, outside any session and its sandbox, so it is not refused. `update` is one of the launcher's offline subcommands and reads no level. From inside a session at `on` it fails like any other command, since the sandbox has no network.
- **The Gmail check at start** is skipped, and Gmail is not advertised (§3).
- **`puffin-code session`** is unaffected: indexing already runs in a network-less sandbox.

### 5.3 Where the sandbox alone is not enough

`/airgapped` reports each of these with its reason (`NOT ENFORCED:` for the ones that leave a way out), and the launcher's start-up line says the same when a session starts at `on`:

- **Full Access.** With `-s danger-full-access` or `--dangerously-bypass-approvals-and-sandbox` there is no sandbox to take the network away, so `on` and Full Access are **incompatible and never allowed together** (§14.7). The launcher refuses to start a session at `on` with Full Access chosen by a flag, a `-c` override or a configuration file; inside a session, `/permissions` shows Full Access disabled while the level is `on`, and `/airgapped on` is refused while the session runs in Full Access. Each refusal says why.
- **`$CODEX_HOME` inside a writable root.** The sandbox lets commands write the working directory, `/tmp` and `$TMPDIR`. `puffin` started in the home directory therefore makes `~/.puffin` and `~/.config/dreamference` writable, and a command could rewrite the session's level file or the user-level configuration file (measured, §14.5). The level is therefore **held outside those folders**: a session seen at `on` gets a seal, a file under the user's runtime directory (`$XDG_RUNTIME_DIR/puffin-airgapped/<thread-id>`), which the sandbox mounts read-only. While the seal exists the level is `on` whatever the files say. It is written by the `puffin` process (when the World State section sees `on`, before the turn's first command, and by `/airgapped on`), removed by `/airgapped off` or `/airgapped duckduckgo` typed by the user, and pruned at the next launch once the process that wrote it has exited. Where there is no runtime directory, the first design applies as a fallback: an exposed session file may tighten the configured level and not loosen it, and `/airgapped` reports the hole as `NOT ENFORCED`.
- **A command the user approves to run outside the sandbox** has the network. The `on` fragment tells the model not to ask (§3), and the approval prompt is the user's own decision, but nothing stops it.
- **MCP servers** the user configured run outside the command sandbox, with the network.
- **`puffin` itself.** Its known channels to the upstream vendor and GitHub are closed at their call sites (patches `0013`, `0015`, `0016`), and a traced session reaches only the model server and the Gmail service. A channel a future Codex release adds would not be stopped by the command sandbox.

### 5.4 The airlock closes those

[PUFFIN_EGRESS §4](./DREAMFERENCE_PUFFIN_EGRESS.md) describes running the whole `puffin` process tree in a network namespace where only allowlisted local ports are reachable. That is the second layer of `on`, not a separate feature:

- **One knob.** `puffin --airlock` and `airlock = true` are dropped from that spec in favour of this level. A session whose configured level is `on` at launch starts inside the airlock, once it exists.
- **Its allowlist at `on` is the model server only.** EGRESS allowed SearXNG (`8888`) and Gmail (`8767`) because its airlock was a mode that kept search. At `on` neither is reachable.
- **Switching inside a running session.** A process cannot be moved into or out of a namespace after it starts. `/airgapped on` typed in a session that started at another level gets the command sandbox at once and prints that the whole-process layer needs a restart (`puffin resume`). `/airgapped off` typed in a session that started inside the airlock prints that the session must be restarted to get the network back, and changes nothing until then.
- **The audit** (`puffin-admin audit egress`, EGRESS Phase 1) gains `--airgapped on`, which must show the model server as the only destination.

Until the airlock is built, `on` means: nothing the agent runs has a network, with the exceptions of §5.3 stated on screen.

---

## 6. How it is built

### 6.1 Where the level comes from

Resolved before every command and every model request. First match wins:

1. **This session:** `$CODEX_HOME/airgapped/<root-session-id>`, a file holding the level name, written by `/airgapped <level>`. Keyed by the root session, not the thread, so subagents and forks read their parent's file (Codex exports the root session's id to commands as `CODEX_SESSION_ID`; whether the World State input and the hook of §5.1 can reach it is a Phase 0 check, and the fallback is to write one file per thread when a child thread starts).
2. **`DREAMFERENCE_PUFFIN_AIRGAPPED`**, from the environment `puffin` was started in.
3. **`puffin_airgapped` in the configuration files, strictest wins.** The repository's `./dreamference.toml` (or the file `DREAMFERENCE_CONFIG_PATH` names) and the user-level `~/.config/dreamference/config.toml` are both read, and the stricter level is taken (`on` over `duckduckgo` over `off`). Every other key takes the first file that exists; this one does not, because the agent can write `./dreamference.toml`, and a file it can write must not be able to loosen the level.
4. **`DEFAULT_PUFFIN_AIRGAPPED`**, `"off"`.

- **`/airgapped default <level>` always writes the user-level file,** with `toml_edit`, creating it if needed. If the repository's file or the environment variable would still give a different result, it says which and why.
- **An invalid value** at any tier is skipped; `/airgapped` names it and where it was.
- **The Python side** mirrors tiers 2–4. `DreamferenceConfig.puffin_airgapped` is the setting as one configuration file holds it, validated against the three names and written by `save_config()` only when it differs from the default; `DreamferenceConfig.resolve_airgapped_level(cwd)` is the resolver for anything that acts on the level, and takes the stricter of the two files as tier 3 does.
- **Session files** older than 30 days are deleted by the launcher at start, as cave mode's are.

### 6.2 How the web commands learn the level

`puffin-search` and `puffin-fetch` run inside the command sandbox, as children of the agent's shell. They resolve the level by the same four tiers, finding the session file through `CODEX_SESSION_ID` and `$CODEX_HOME` (default `~/.puffin`), both of which the sandbox can read and not write. The resolution is a small module in `puffin-web-rs/` with the same unit tests as the launcher's, since the two crates share no code.

**A second signal: the sandbox's own.** Codex sets `CODEX_SANDBOX_NETWORK_DISABLED=1` for a command it runs without a network. That happens at `on`, and also at any level under a read-only sandbox (`puffin exec` without `-s`). When either web command sees the variable it sends nothing and prints no restart hint, and the message depends on the level it resolved:
- **`on`:** the message of §4.
- **Another level, or none found:** `This command has no network: the sandbox it runs in does not allow it. Web access needs the workspace-write sandbox (puffin exec -s workspace-write).` Saying `/airgapped on` here would name a setting nobody chose.

**Finding `$CODEX_HOME`.** The launcher sets `CODEX_HOME` in `puffin`'s own environment (`use_puffin_home()`), and Codex's default policy passes the environment on to commands, so the variable is normally there. A user's `shell_environment_policy` can strip it; the web commands then fall back to `~/.puffin`.

Run by hand from a terminal, outside any session, the commands follow tiers 2–4.

### 6.3 Launcher module `puffin-rs/src/airgapped.rs`

- the three levels, their aliases and their order of strictness;
- resolution (§6.1) and `sealed(session)` for the hook (§5.1);
- `command(args, session)`, which returns the lines `/airgapped` prints, and `run_cli` for `puffin airgapped …`;
- the World State section and its fragments (§3);
- the enforcement report: which of the cases in §5.3 applies now;
- the refusals of §5.2 and §5.3 in `prepare_args`.

### 6.4 Patch `0019-airgapped`

Modelled on `0017-cave-mode` and `0018-night-slash-command`:
- `codex-rs/tui/src/slash_command.rs`: the variant `Airgapped`, placed after `Permissions`, since both set what the agent may reach; its description, "set how much of the internet this session may use"; membership in `supports_inline_args()` and `available_during_task()`;
- `codex-rs/tui/src/chatwidget/slash_dispatch.rs`: one arm in each of the two dispatch functions, calling `puffin_launcher::airgapped::command(…)`, and membership in the list of commands that run at once when queued;
- `codex-rs/app-server/src/extensions.rs` and `codex-rs/cli/src/main.rs`: one `install` line each for the World State section, beside cave mode's;
- `codex-rs/core`: the one-line hook of §5.1, and one path dependency in its manifest: `puffin-launcher`, or the leaf crate `puffin-airgapped` if Phase 0 finds a cycle.

**Budget.** The series stands at 26,933 bytes under a 27,500-byte cap (`test_the_patches_stay_small`). `0018` cost 2,133 bytes for the slash command alone; the two `install` lines and the core hook add about 1.2 KB, so `0019` is estimated at 3.3 KB and the series at about 30.2 KB. The cap is raised in the same commit, explicitly, to the measured size rounded up to the next 500 bytes, as cave mode and `/night` did.

---

## 7. Other things that run `puffin`

- **Night Shift.** A night task runs at the configured level, fixed once per task before the agent starts and exported as `DREAMFERENCE_PUFFIN_AIRGAPPED` to every command of the task, so the agent's edit of the worktree's `dreamference.toml` cannot loosen it for its own tests. `[night] airgapped = "<level>"` in the user-level file may set a stricter one for night runs alone; a looser one is ignored. The runner's own test command goes through the same sandbox helper as the agent's commands (`puffin sandbox`), so at `on` it has no network either: tests of code the agent just wrote are the agent's code running. With `[night] test_sandbox = false` and level `on` the tests are not run, and the report says why. (Built by the Night Shift sandboxing change of 2026-10-02; see [NIGHT_SHIFT](./DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md).) This replaces NIGHT_SHIFT §6's "night runs use the airlock by default": a night at `on` cannot install a missing dependency or read documentation, which is the user's choice to make, not a default.
- **`puffin-admin run`** with Codex as the agent goes through the launcher, so it follows the configured level. The other agents (Cline, Continue, OpenHands) are not covered.
- **`puffin-admin mcp`.** Its `web_search` and `web_fetch` tools (`WebTools`) follow tiers 2–4, resolved on every call, with the same three behaviours: at `on` nothing is sent, at `duckduckgo` the search names that engine and no category, at `off` nothing changes. There is no session there and no slash command in an IDE, so the `on` message names the setting (`puffin_airgapped = on`) and `puffin airgapped default off`, not `/airgapped`.

---

## 8. What this does not cover

Stated in the command's own output (§2), so nobody takes `on` for more than it is:

- **The web chat.** Onyx searches through the same SearXNG, opens pages itself, and has the image-search and Gmail sidecars. None of them read this level. A machine-wide switch would have to stop or reconfigure containers, and belongs in `puffin-admin`, not in a session's slash command (§11).
- **`puffin-admin`'s own downloads:** models from Hugging Face, Docker images, the Codex build's crates and V8, the fonts.
- **Inbound connections.** The model server listens on every interface by design ([README](./README.md), "Accepted by design").
- **What the user runs** in their own terminal.

---

## 9. Checked and not checked (2026-10-01)

**Checked on this machine:**
- **SearXNG's engine parameter.** `engines=duckduckgo` with `categories=general` queried five engines (Brave, DuckDuckGo, Google CSE, Startpage, Wikipedia); `engines=duckduckgo` alone queried DuckDuckGo only. Read from `unresponsive_engines`, because the SearXNG container had no outbound connectivity at the time (the host had just restarted) and every engine failed. So which engines are *asked* is verified; that results then carry only `duckduckgo` is not.
- **The enabled general engines** are the ones §1 lists (`GET /config`).
- **The sandbox's writable roots** are the working directory, `/tmp` and `$TMPDIR` (`SandboxPolicy::get_writable_roots_with_cwd`), so `$CODEX_HOME` is not writable from a command.
- **`CODEX_THREAD_ID` and `CODEX_SESSION_ID`** are set for the agent's commands (`core/src/exec_env.rs`).
- **`CODEX_HOME`** is set in `puffin`'s own environment by `use_puffin_home()` (`puffin-rs/src/home.rs`), and Codex's default shell-environment policy inherits the environment, leaving out only a short list of its own token variables (`protocol/src/shell_environment.rs`).
- **`puffin-search --json`** carries one `engine` per result today, not the list.

**Read from the code, not run:**
- a restricted network policy becomes `bwrap --unshare-net` (`linux-sandbox`, tests `inserts_unshare_net_when_network_isolation_requested`);
- `TurnContext::network_sandbox_policy()` is where a turn's network policy is read;
- `/permissions` changes a running session's policy through `OverrideTurnContext { permission_profile, … }`.

**Not checked:**
- whether hooking `network_sandbox_policy()` covers every way a command is started (the unified exec path, the legacy `SandboxPolicy` conversion beside it, `apply_patch`, Code Mode);
- whether `codex-core` can depend on `puffin-launcher` without a dependency cycle (the launcher depends on `codex-extension-api` and `codex-models-manager`);
- whether the root session's id is available where the level is resolved;
- that `CODEX_SANDBOX_NETWORK_DISABLED` is set on Linux in this Codex release, and not only on macOS (`core/src/spawn.rs` sets it; under which conditions was not read);
- that `CODEX_HOME` and `CODEX_SESSION_ID` are in fact present in a sandboxed command's environment here (run `env` through the agent);
- that the model obeys the `on` fragment against the system prompt's "you can browse the web".

---

## 10. Phases

**Phase 0, check (no product code).**
- The items under "Not checked" in §9, each by a scratch export or a traced run.
- The `on` and `duckduckgo` fragments against the live model with `puffin debug prompt-input` and `puffin exec`: at `on`, asked for today's weather, the model answers that it has no network in this session and does not run a web command or ask for one outside the sandbox.
- DuckDuckGo alone, 50 varied queries through SearXNG over a day: how many are answered, and what the failures say. If fewer than nine in ten are answered, the level's line in `/airgapped` says so.

**Phase 1, build.** `airgapped.rs`, patch `0019` with the cap raise, the level module and messages in `puffin-web-rs/`, `puffin_airgapped` in `DreamferenceConfig`, the one clause in `WEB_ACCESS_INSTRUCTIONS`, the Night Shift test wrapper, `WebTools`, and the docs: [PUFFIN_CODEX](./DREAMFERENCE_PUFFIN_CODEX.md), `README.md`, `CLAUDE.md`, `docs/`.

**Phase 2, the airlock for `on`** ([PUFFIN_EGRESS §4](./DREAMFERENCE_PUFFIN_EGRESS.md)), after which the cases of §5.3 are closed and the status line at `on` has no exceptions to list.

**Phase 3, optional.** A status-line item showing the level when it is not `off`.

---

## 11. Tests and acceptance

**Launcher unit tests** (`cargo test --release -p puffin-launcher`, in the export): parsing of every form; resolution through the tiers, including an invalid value and the strictest-wins rule between the two files; a repository file saying `off` does not loosen a user-level `on`; `default` writes the user-level file and keeps its other keys; each fragment for each change of level; the refusal of Full Access at `on`; the enforcement report for each case of §5.3; a level file under a writable root cannot loosen `on`.

**Web command tests** (`puffin-web-rs/`, against the local stand-in server): at `duckduckgo` the request carries `engines=duckduckgo` and no `categories`; at `on` no request is sent and the message is the level's; with `CODEX_SANDBOX_NETWORK_DISABLED=1` at `off` or with no level file, no request is sent and the message names the sandbox, not `/airgapped`; neither restart hint appears at `duckduckgo` or `on`; `--json` carries the level.

**Python:** `DreamferenceConfig.puffin_airgapped` through its tiers; `WebTools` at each level; the Night Shift runner wraps the test command at `on` (asserted on the command line, no real namespace in the suite).

**Acceptance, live against the model:**
- **`off`:** a search and a fetch work exactly as before the change, and `puffin debug prompt-input` shows no `airgapped` fragment.
- **`duckduckgo`:** every result of `puffin-search --json` has `engines` equal to `["duckduckgo"]`, and there are no answers or infoboxes from another engine.
- **`on`, in a `workspace-write` session:**
  - the agent's `curl https://example.com` fails;
  - `puffin-search` prints the level's message and no restart hint;
  - `puffin-admin gmail search` fails;
  - a strace of the session shows no connection from any command;
  - the model, asked something that needs the web, says it cannot look it up in this session.
- **Mid-turn:** `/airgapped on` typed while the agent works applies to its next command.
- **Inheritance:** a subagent started in an `on` session cannot fetch a page.
- **The model cannot loosen it:** asked to, it cannot write the session file, and writing `puffin_airgapped = "off"` into `./dreamference.toml` changes nothing under a user-level `on`.
- **Resume:** a session set to `on` and resumed is still `on`, whatever the configured level.
- **Docs:** nothing calls `off` or `duckduckgo` air-gapped, and every description of `duckduckgo` says it is a preference.

---

## 12. Alternatives considered

- **Rewrite SearXNG's settings for `duckduckgo`,** so that no other engine exists. That would be a barrier, not a preference, but SearXNG is shared with the web chat and with every other session, so one session's level would change search for all of them. It is the right mechanism for a machine-wide level (§13), not for a session's.
- **A second SearXNG with only DuckDuckGo enabled.** A real barrier per session only if the sandbox could allow one port and not another, which it cannot; without that it is the same preference with one more container.
- **Codex's managed network proxy** (the sandbox's proxy-only mode with a domain allowlist) could let `duckduckgo` allow only chosen hosts. It would also break `pip`, `git` and `cargo` at that level, which the level is not meant to touch, and it is a large upstream feature this fork has never run.
- **Per-level system prompts.** Changing the catalog's prompt mid-session re-reads the whole conversation ([CAVE_MODE §5.2](./DREAMFERENCE_PUFFIN_CAVE_MODE.md)), and the catalog file is shared by sessions at different levels.
- **`puffin --airlock` as its own switch,** as EGRESS proposed. Two switches for one question ("may this session reach the internet?") would need a rule for every combination. The airlock is the mechanism; the level is the knob.
- **Only tool-level refusal at `on`,** with no kernel enforcement. Then `curl` works and the level's name is false.

---

## 13. Open questions

- **A machine-wide level.** "Air-gap this machine" would also mean the web chat, its sidecars and `puffin-admin`'s downloads (§8). The natural form is `puffin-admin airgapped <level>`: SearXNG's engine list rewritten for `duckduckgo`; at `on`, SearXNG and the Gmail and image-search sidecars stopped, Onyx's web-search provider removed, downloads refused. Onyx's own page fetching runs in a container this project does not configure, so closing it needs a host firewall rule and root. Left out of this spec because every part of it is a different mechanism from a session's level; `puffin_airgapped` is named so that a later `airgapped` key can sit above it.
- **Should `duckduckgo` also switch Gmail off?** §4 keeps it on.
- **Fetch at `duckduckgo`.** A fetched page's site sees this machine's address and the URL. A user who wants search results without any direct visit has no level for that today; a fourth level between `duckduckgo` and `on` (search only, no fetch) is easy to add if asked for.
- **The model server on another machine.** At `on` the prompts still go to `vllm_host`. If that host is not on this machine, `/airgapped` should say so in its status; whether `on` should refuse a non-loopback `vllm_host` is undecided.

---

## 14. As built (2026-10-01)

### 14.1 Departures from the design

- **The enforcement hook is in the sandbox helper, not in core.** §5.1's premise was wrong: a command's network policy is not read in one place. `network_sandbox_policy()` has twelve call sites in `codex-core`, three of which take it from a permission profile directly (`exec.rs`, `sandboxing/mod.rs`, `tools/orchestrator.rs`), and `to_legacy_sandbox_policy` reads the profile's field without it. Every sandboxed command on Linux does pass through one function, `resolve_permission_profile` in `codex-rs/linux-sandbox` (the helper `puffin` re-executes itself as, for the outer bubblewrap stage and the inner seccomp stage alike). Patch `0019` adds three lines there: when `puffin_airgapped::sealed_for_command()` is true, the profile's network is `Restricted`, so the helper builds `bwrap --unshare-net` and installs the network seccomp filter whatever the session's policy says.
- **The helper finds the session by itself.** It runs in the command's own environment (`spawn_child_async` clears the environment and sets the command's), which carries `CODEX_THREAD_ID`, `CODEX_SESSION_ID` and `CODEX_HOME`. So the question is asked per command, in the process that builds the sandbox, with no channel from the TUI: a switch mid-turn, `puffin exec`, a resumed session and another process's app server are all the same case. The model cannot change that environment: it belongs to the helper, which Codex starts, not to the shell inside it.
- **The level file is keyed by thread id** (`$CODEX_HOME/airgapped/<thread-id>`), because that is the only id the TUI's hook and the World State input carry. The resolver looks under `CODEX_THREAD_ID` first and `CODEX_SESSION_ID` second, which is how a subagent with no file of its own takes its parent's level (§2); a test of the web commands covers that order. Whether the root session's id equals the root thread's id on every path was checked only for `puffin exec` (§14.3).
- **A leaf crate, as §5.1 allowed for, but for the helper.** `puffin-rs/airgapped/` (`puffin-airgapped`) holds the levels and the resolution with no dependency beyond the standard library: `linux-sandbox` must not depend on the launcher. The launcher depends on it too. `puffin_airgapped` is read from TOML by a line scan (a top-level `puffin_airgapped = "<level>"` before the first table), which keeps a TOML parser out of the helper; `/airgapped default` writes it with `toml_edit`.
- **The web commands carry a copy, not a module of their own.** `puffin-web-rs/src/airgapped.rs` is byte-for-byte `puffin-rs/airgapped/src/lib.rs` (`tests/test_airgapped.py` compares them), since the web crate is built outside the Codex workspace and its build stamp covers only its own folder.
- **`CODEX_SANDBOX_NETWORK_DISABLED` is not set by the hook.** Core sets it from the session's policy before the helper runs, so at `on` under `workspace-write` a command does not see it. The web commands do not need it there: they resolve the level themselves and refuse first. It remains the second signal for a read-only sandbox (§6.2).
- **What the model is told about the sandbox is unchanged at `on`:** core still describes the session's own policy. The `on` fragment (§3) is what tells it.
- **`/airgapped` sits after `/permissions` in the popup;** in the membership lists it is beside `/night`.
- **The patch is 4,242 bytes, not 3.3 KB:** seven hunks for the slash command, two `install` lines, and the helper's dependency and hook. The series is 17 patches and 31,175 bytes; the cap went from 27,500 to 31,500 in the same commit.

### 14.2 What is built

| Piece | Path |
|---|---|
| Levels, tiers, strictest-of-two-files, `sealed_for_command()`, the seals and the exposure check | `puffin-rs/airgapped/src/lib.rs` |
| `/airgapped`, `puffin airgapped`, status, `default`, World State fragments, Full Access refusal, 30-day prune | `puffin-rs/src/airgapped.rs`, `puffin-rs/src/lib.rs` |
| Slash command, section registration, helper hook | `codex-patches/0019-airgapped.patch` |
| `puffin-search` and `puffin-fetch` following the level; `--json` gains `airgapped` and `engines` | `puffin-web-rs/src/{airgapped,lib,search}.rs`, `src/bin/` |
| `DreamferenceConfig.puffin_airgapped`, `resolve_airgapped_level()` | `dreamference/config/dreamference_config.py` |
| `web_search` and `web_fetch` over MCP following the level | `dreamference/mcp_server/web_tools.py` |
| The one clause in `WEB_ACCESS_INSTRUCTIONS` | `puffin-rs/src/lib.rs` |

### 14.3 Run live

All on 2026-10-01, with the rebuilt `puffin` (17 patches) against the default model (Qwen3.8-27B on SGLang), in a throwaway repository, `puffin exec -s workspace-write` unless said otherwise. Each command line was given to the model to run verbatim, and its output read from the `--json` events, not from the model's account of it.

- **`off`:** `curl https://example.com` answers 200, `/proc/net/dev` lists the host's interfaces, the model server on `127.0.0.1:8000` answers 200, `puffin-search` returns results (`"airgapped": "off"`, each result with its `engines` list), and the session contains no `<airgapped>` fragment.
- **`on` from the environment** (`DREAMFERENCE_PUFFIN_AIRGAPPED=on`): `curl https://example.com` exits 6, and `puffin-search` prints the level's message with no hint.
- **`on` from the session file, on a resumed session.** A session run at `off`, then its file written as `/airgapped on` writes it, then `puffin exec resume <id>`: `/proc/net/dev` lists `lo` only; `curl https://1.1.1.1` and `curl http://127.0.0.1:8000/v1/models` both exit 7; `puffin-fetch` prints the level's message. The rollout holds the `on` fragment once. `puffin` itself still reached the model server, as §1 says it does.
- **The agent cannot loosen it.** From inside that session, `echo off > $CODEX_HOME/airgapped/$CODEX_THREAD_ID` fails with "Read-only file system", and the next `curl` still exits 7. (The same command also wrote `puffin_airgapped = "off"` into `./dreamference.toml`, but that proves nothing: the session's file said `on` and is read first. The strictest-of-two-files rule is covered by the resolver's unit test and by the web commands' test with `HOME` swapped, not live through the sandbox helper.)
- **A repository file tightens.** With only `./dreamference.toml` saying `on`, `puffin airgapped` names that file as the source and a command's `curl` exits 7.
- **Ids.** `CODEX_THREAD_ID` and `CODEX_SESSION_ID` are equal in a root `puffin exec` session, and `CODEX_SANDBOX_NETWORK_DISABLED` is unset under `workspace-write` at both levels.
- **Full Access at `on`:** `DREAMFERENCE_PUFFIN_AIRGAPPED=on puffin exec -s danger-full-access …` is refused before anything starts, naming both ways out.
- **In the TUI** (a real session in tmux, `-s workspace-write`): `/airg` shows `/airgapped  set how much of the internet this session may use`; `/airgapped on` typed before the first message is accepted and printed its two lines; the next turn's `curl https://1.1.1.1` printed `ip=000rc=7`; asked for Lisbon's weather, the model answered that it could not check in this session and that anything it gave would be from memory, and ran no command; `/airgapped` showed `on` in force with the `Enforced:` and `NOT ENFORCED for:` lines.
- **Found and fixed: the message that lifts the restriction.** After `/airgapped off` the fragment was delivered, and the model still refused to run `curl`, quoting the `on` message. The first wording ("the earlier restriction on web access no longer applies") was too weak against the `on` text still in its history; the fragment now says the user lifted it and names the commands that work again. Retested in a fresh session after the rebuild: `on`, `curl` printed `ip=000rc=7`; `/airgapped off`; asked to run it again, the model ran it and got `ip=301rc=0`.
- **`duckduckgo`:** not shown live. DuckDuckGo answered SearXNG with a CAPTCHA at the time, so `puffin-search` printed `DuckDuckGo did not answer (duckduckgo: CAPTCHA). This session searches through DuckDuckGo only (/airgapped duckduckgo).` with no hint: the risk §4 names, on the first try. SearXNG's `unresponsive_engines` named `duckduckgo` alone, which fits only that engine having been asked. That the request names the engine and no category is covered by the stand-in-server test; that results then carry only `duckduckgo` is still unverified.
- **Not run live:** a subagent in an `on` session, `/airgapped default` (it writes the user's real configuration file; unit-tested), a switch to `on` while a turn is running, and the strace of §11.

### 14.4 Not built

- ~~**The Gmail check at start is not skipped**~~ Closed on 2026-10-03: a session that starts at a configured `on` gets no Gmail section (`offers_gmail`, `puffin-rs/src/airgapped.rs`). A session switched to `on` later keeps it, and the `on` fragment says Gmail is unavailable. (`puffin update` is not refused at `on`, and that is the design, §5.2.)
- ~~**A session switched to Full Access through `/permissions`** shows no warning.~~ Closed on 2026-10-03: the two are refused together (§14.7).
- **A tampered level takes effect at the next restart.** A seal lasts as long as the `puffin` process that wrote it. If a command rewrote the level's files while the session was held, a later `puffin resume` reads those files and starts at what they say. Keeping seals across restarts would close it, at the price of a session nobody can loosen without the TUI; left as it is.
- **`writable_roots` the user adds** are not known to the exposure check, which looks at the working directory, `/tmp` and `$TMPDIR`. The seal does not depend on that check, so this only affects what `/airgapped` reports where there is no runtime directory.
- **The airlock** (§5.4, Phase 2) and the strace of an `on` session (§11). (The 50-query DuckDuckGo measurement of Phase 0 is moot: the level was removed, §14.6.)
- **Codex's own TUI snapshots** that list the slash-command popup change again with `/airgapped` in it.

### 14.4a Added on 2026-10-03: the start-up line

A session that starts at a configured `on` (environment or configuration file; the level is read before Codex parses its arguments, so a `puffin resume` of a session whose own file says `on` gets no line, though the session is still held) prints, before the wait for the model server:

```
🔒 Airgapped: on (<source>). Enforced: sandboxed commands run with no network.
NOT ENFORCED for: a command you approve to run outside the sandbox, MCP servers you configured.
```

with `/airgapped`'s `NOT ENFORCED against a command rewriting the level` between the two when the level files are inside a writable root and there is no runtime directory for a seal. Nothing is printed at `off`. Full Access is not listed: it is refused at launch and disabled in `/permissions` (§14.7). It goes to stderr for `puffin exec` too. `startup_lines` in `puffin-rs/src/airgapped.rs`, unit-tested.

**Watched in the build of `258c3b5` (2026-10-03).** A TUI session started in tmux with `DREAMFERENCE_PUFFIN_AIRGAPPED=on` and `-s workspace-write` wrote both lines to stderr (`🔒 Airgapped: on (DREAMFERENCE_PUFFIN_AIRGAPPED). Enforced: …` and the `NOT ENFORCED for:` line), captured by redirecting stderr to a file. **They are not visible in the interface:** with stderr on the terminal, the TUI's first frame starts at the top of the pane and the scrollback holds neither line (nor the skills line printed just after). Printed to stderr before Codex draws, they are drawn over. Fixed on branch `fix/startup-lines-app-server` (not yet in an installed build): every start-up line the launcher prints (this one, the skills line, the night line, a prompt other than `default`) is also kept, and for a TUI session (`puffin`, `puffin resume`, `puffin fork`) written to `$CODEX_HOME/notice/<hash>.txt` and registered as a trusted `SessionStart` hook, `<puffin> notice <hash>`, under matcher `startup|resume` (`puffin-rs/src/notice.rs`). Codex shows the hook's `systemMessage` in the history at the start of the first turn, above the first answer, not on the empty screen before it. No patch. For `exec` the hook is removed, since `exec` prints every hook run and the lines are on stderr already; `puffin exec` shows them, since it does not take over the screen.

### 14.5 Added on 2026-10-02: the three gaps of "not built"

**`puffin-admin mcp`'s web tools follow the level** (§7). Checked against the live SearXNG with the real `WebTools`, recording each request: at `on`, no request left the process and both tools returned the level's message; at `duckduckgo`, the one search request carried `engines=duckduckgo` and no `categories` (DuckDuckGo answered with a CAPTCHA, so the tool returned "DuckDuckGo did not answer (duckduckgo: CAPTCHA)…" with no hint) and `web_fetch` returned `example.com`; at `off`, the request carried `categories=general` and three results came back.

**The level is held outside the folders a command can write** (§5.3).
- **The hole, measured.** From the home directory, a command under the workspace-write sandbox created `~/.puffin/airgapped/<file>`, a file in `~/.puffin` and a file in `~/.config/dreamference/`: all three succeeded. So not only the session's file but tier 3's user-level file was the agent's to rewrite, and "the session file may not loosen the configured level" would not have been enough.
- **The place, measured.** In the same sandbox, from the home directory and from a project folder, `touch /run/user/1000/<file>` failed with "Read-only file system", and `XDG_RUNTIME_DIR` was present in the command's environment. Inside the sandbox `/proc/self` belongs to uid 65534, so the runtime directory is taken from `XDG_RUNTIME_DIR` first and from the uid only as a fallback.
- **Departure from §5.3's first wording:** `/airgapped off` typed by the user does lift the hold, because the TUI is a process no command controls; "sealed until `puffin` is restarted" applies to everything a command can do.
- **The resolver run inside the real sandbox; the helper not yet.** The new resolver, compiled into a small probe binary, was run under `puffin sandbox` (workspace-write) from a stand-in home directory with a seal in place. The command rewrote its session's level file to `off` (succeeded), wrote `puffin_airgapped = "off"` into the user-level config file (succeeded) and tried to delete the seal ("Read-only file system"); the resolver still answered `on`, source `Sealed`, `sealed_for_command() = true`, with a note naming the ignored `off`. With the seal removed, as `/airgapped off` does, the same files gave `off`. The launcher's side (writing the seal when the World State section sees `on`, lifting it on `/airgapped off`, pruning seals of exited processes, what the status prints) is unit-tested. The installed `puffin` predates this change, so its sandbox helper still uses the old resolver: a TUI session in the home directory at `on` whose command rewrites its level file, followed by a `curl` that must still fail, is the live check owed after the next `puffin-admin codex build`.
- **The live check, done on 2026-10-03** with the installed build (which carries the seal). A TUI session from `~` at `on` wrote its seal under `/run/user/1000/puffin-airgapped/`. A sandboxed command with the session's thread id (run through `puffin sandbox`, because the model **refused twice** to run the tampering script) rewrote the level file to `off` (succeeded) and could not delete the seal ("Read-only file system"); the next command's `curl` failed with exit 7 for the internet and the model server alike. The same call for an unsealed thread id reached the internet, so the cut came from the seal. In the TUI, `/airgapped` said `on ← in force`, `Held`, and `Note: ignored "off" from this session`; after `/airgapped off` the same session reached the internet. Not seen: a rewrite issued by the model itself, which it would not do.

**A night task's tests have no network at `on`** (§7): closed by the Night Shift change that runs the runner's test command through `puffin sandbox` (commit `7f55b56`; the `[night] airgapped` key landed in `4b82073`), measured there (level `off`: internet and model server reachable; `on`: neither). One departure for night tasks only: the runner exports `DREAMFERENCE_PUFFIN_AIRGAPPED` to every command of a task at every level, and the environment outranks the files, so an agent that writes `puffin_airgapped = "on"` into the worktree's `dreamference.toml` mid-task does not tighten its own later commands. The level is fixed at task start; a seal would still tighten it.

### 14.6 Removed on 2026-10-03: the `duckduckgo` level

DuckDuckGo answered SearXNG with a CAPTCHA when the level was first tried live (§14.3) and again when the MCP tools were checked (§14.5): a session at `duckduckgo` searched nothing, and the risk §4 names was the normal case, not the exception. The level is gone from every place that resolved or acted on it:

- **The resolver** (`puffin-rs/airgapped/`, and its byte-identical copy in `puffin-web-rs/src/airgapped.rs`): `Level` is `Off < On`; `duckduckgo` and `ddg` are unknown names. A session file, the environment or a configuration file still holding `duckduckgo` is **ignored and named**, like any invalid value: `/airgapped` prints `Note: ignored "duckduckgo" from …` and the next tier decides, so a session left at `duckduckgo` searches every engine again. No alias to `off` or `on` was added: `off` would hide the change, `on` would cut the network from a user who only asked about search engines.
- **The launcher** (`puffin-rs/src/airgapped.rs`): the usage line, the status table and the fragments know two levels. A session resumed after having been at `duckduckgo` gets the `off` fragment, which says web access is back.
- **The web commands and the MCP server's `web_search`**: always `categories=<category>`; the DuckDuckGo-only error text is gone, and every-engine-failed errors carry the restart hint at `off`.
- **Python** (`PUFFIN_AIRGAPPED_LEVELS`, `NodeJob.AIRGAP_LEVELS`): two levels. A node job from an older sender that still names `duckduckgo` counts as `on`, because `NodeJob.stricter` treats an unknown name as the strictest.
- **Not changed:** SearXNG's own engine list, which still includes DuckDuckGo for `off`; and, until 2026-10-03, the installed `puffin`, which kept the old resolver until the build of `258c3b5` replaced it (the live checks of §14.7 ran on that build).

### 14.7 Added on 2026-10-03: `on` and Full Access are refused together

Full Access has no sandbox, and the sandbox is the only thing that enforces `on`, so a session is never allowed to hold both. Every way of choosing one while the other is in force is refused, and each refusal says that the two are incompatible:

- **At launch** (`full_access_conflict`, `puffin-rs/src/airgapped.rs`), at a configured `on`: Full Access from `--yolo` / `--dangerously-bypass-approvals-and-sandbox`, `-s`/`--sandbox danger-full-access` in every spelling, a `-c sandbox_mode=…` or `-c default_permissions=":danger-full-access"` override, or a configuration file: `/etc/codex/config.toml`, `$CODEX_HOME/config.toml` and each project's `.codex/config.toml` from the repository root down to the working directory, with the selected profile's keys (`-p`, `--profile`, or `profile =`) before the top level's and a later file before an earlier one, as Codex layers them. A sandbox named on the command line (`-s workspace-write`, `--full-auto`) overrides the files, as in Codex. A project file counts even where Codex would not trust the project: refusing there is the safe side to be wrong on.
- **In the permissions picker** (patch `0019`, two one-line hooks in `permissions_menu.rs` and `permission_popups.rs`): while the session's level is `on`, the Full Access row is shown disabled, `Full Access (disabled) … (disabled: /airgapped is on, and Full Access has no sandbox to keep commands off the network; run /airgapped off first)`, and cannot be chosen. This covers both of Codex's pickers (the legacy one and the permission-profile one); the keyboard shortcut that cycles modes never offered Full Access.
- **`/airgapped on` and `/airgapped default on` in a Full Access session** (`session_command`; the TUI passes whether the session's permission profile is `Disabled`): refused with `Airgapped not changed: this session runs in Full Access. Full Access runs commands with no sandbox, and the sandbox is what takes their network away, so the two are incompatible.` and `To air-gap it, choose another mode with /permissions first, then /airgapped on.` Nothing is written and no seal is made.
- **What remains possible**, and is reported, not refused: a session already in Full Access whose level turns `on` from outside it (another shell running `puffin airgapped default on`, or `puffin resume` of a session whose own level file says `on`, which the launch check, reading only the configured level, does not see). `/airgapped` in such a session adds `NOT ENFORCED in this session: it runs in Full Access.` with the same reason.

**Watched in the build of `258c3b5` (2026-10-03), in tmux:** `--yolo` at `DREAMFERENCE_PUFFIN_AIRGAPPED=on` ended with exit 1 and the launch message above; `/permissions` in a session at `on` listed `Full Access (disabled)` with the reason and offered only the other two rows; `/airgapped on` typed in a session started with `-s danger-full-access` printed the two refusal lines and changed nothing.

Patch `0019` grew by 1,261 bytes (the series from 32,425 to 33,686; `test_the_patches_stay_small` raised to 33,750).
