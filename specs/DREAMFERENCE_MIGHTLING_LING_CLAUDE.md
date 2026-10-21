# Mightling: Claude Code compatibility mode in `ling` — Claude Code's way of working, on the local model

**Status:** **being built, phase by phase from 2026-10-20: Phase 0 checks done (§16); the switch (§0.4: `--compat`, `DREAMFERENCE_MIGHTLING_COMPAT`, `mightling_compat`, `ling compat status|use|doctor`) built in `ling-rs/src/compat.rs`; the interface settings of §6.5.4 (the renderer and the key map) built in `ling-rs/src/compat_tui.rs`; print mode (§6.3, §6.4) in `ling-rs/src/compat_print.rs`, `ling-admin run --compat` and `audit egress --compat claude` (§8.2).** Asked for by the owner on 2026-10-19 and written the same day; updated on 2026-10-20 with the owner's six decisions of that day (§0, first block: D1–D3, then D4–D6 answering open questions 12–14) and a black-box study of Claude Code's terminal interface (§6.5). Nothing in this document exists in the code yet; every "today" below was checked against `main` at `a5c0ad7` and the pinned Codex source (`rust-v0.158.0`) on 2026-10-19, and the interface study against `ling` 1.6.4 and the same pin on 2026-10-20.

**Name:** there is **no new product name** (owner, 2026-10-20). This is a **mode of `ling`**, the compatibility mode, switched on with `ling --compat claude`, `DREAMFERENCE_MIGHTLING_COMPAT=claude` or `mightling_compat = "claude"` (§0.4). The file and branch keep the old working name `ling-claude` only as a label; it is never a command, a link or a package.

**Subject:** an open-source, clean-room re-implementation of the user experience and feature set of Anthropic's Claude Code terminal agent, as its public documentation describes them, running against Mightling's own model server (Qwen3.8-27B on SGLang today, ling-engine later) with Mightling's sandbox, `/airgapped` and egress audit.

**Builds on:**
- [MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md): `ling`, the patch series under its 45,000-byte cap, the launcher in `ling-rs/` (`docs/dev/launcher.md`, `docs/dev/codex-build.md`).
- [AGENTS](./DREAMFERENCE_AGENTS.md): the runner pairs and the strategy switch on `config.agent_runner`.
- [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md) and [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md): the audit, its verdict, and the `on` level enforced in Codex's sandbox helper (patch `0019`).
- [MIGHTLING_SKILLS](./DREAMFERENCE_MIGHTLING_SKILLS.md): `~/.claude/skills` and a trusted repository's `.claude/skills` are already linked into `ling` (`ling-rs/skills/`).
- [MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md), [MIGHTLING_DESKTOP_PARITY](./DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md), [MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) (§20–§23: how an arm is added), [MIGHTLING_COMPACTION](./DREAMFERENCE_MIGHTLING_COMPACTION.md), [MODELS](./DREAMFERENCE_MODELS.md).

---

## 0. Decisions, stated first

### Decided by the owner on 2026-10-20

- **D1. No new product name.** The work lives inside `ling` as a **mode of the same agent**, the *compatibility mode*, not as a second command, link or package. The switch follows the pattern `ling` already uses for named prompts and cave mode (a launcher flag, a `DREAMFERENCE_MIGHTLING_*` variable, a `mightling_*` key in the Dreamference TOML file, and a `ling <noun> use|status` subcommand): §0.4. The naming question (old §14 Q1) is closed.
- **D2. The terminal interface does what Claude Code does.** Studied as a **black box only**: what the installed CLI prints to a terminal, its `--help` and `--version` output, and its public documentation; never its bundled JavaScript or any other file of its package. The findings, the escape-sequence evidence in summary, and how `ling` gets there on Codex's TUI are §6.5. This also answers old §14 Q2 (the interface is in scope) and §14 Q11 (observing behaviour is allowed, black box only).
- **D3. The first scope is four things together**: (1) Claude Code's terminal interface (§6.5); (2) its slash command names and key bindings (§6.5.3, §6.5.5); (3) `CLAUDE.md`, `.claude/skills` and `.claude/agents` read beside `AGENTS.md` (§9.1, §9.4); (4) subagents and hooks configured the way Claude Code configures them (`.claude/agents/*.md`, the `hooks` block of `.claude/settings*.json`; §9.4, §9.5). Items (3) and (4) were already Phase 1 of §12; items (1) and (2) join them in Phase 1 for their zero-patch part and in Phase 3 for the part that needs four TUI hooks, under the patch cap the owner raised for them (D4).
- **D4. The patch cap goes to 48,500 bytes** for the four interface hooks of §6.5.6 (old §14 Q12). The number in `test_the_patches_stay_small` changes in the same change as the hooks, not before. **Raised again by the owner the same day to 49,000 bytes**, so the `Shift+Tab` hook Phase 0 found necessary (§16.1, §6.5.6 item 5) fits beside the four; nothing above 49,000 without asking.
- **D5. The mode starts in the classic inline renderer** (old §14 Q13): the normal screen, history in the terminal's own scrollback, a live region at the bottom; fullscreen only when the user asks for it the way Claude Code is asked (§6.5.4).
- **D6. `Shift+Tab` cycles `ling`'s approval modes** (old §14 Q14), and never into a combination `ling` forbids: Full Access is skipped while `/airgapped` is `on` (§6.5.4).

### Proposed in this spec

1. **Clean room, without exception.** Claude Code is proprietary. This spec and the implementation use only its public documentation and its observable behaviour; no code, prompt text or asset of it is copied, read from a leak, or decompiled (§2).
2. **Build on `ling`, do not write a second agent (option (c), §5).** The pinned Codex already carries most of what Claude Code's feature list asks for, including hooks with the same event names and the same JSON decision fields, plan mode, subagents, background terminals, `/review`, `/worktree`, `/resume`, MCP and skills, and even a one-time importer of Claude Code's configuration (`/import`, §4.2). What is missing is a **compatibility layer**: Claude Code's file formats read live, its print-mode command line, its tool roster as an option, and its permission rules. All of it fits in `ling-rs/` with **no new patch** in Phase 1.
3. **What "alternative agent" means here, stated openly.** The owner asked for an alternative agent with Claude Code's user experience and feature set. This spec delivers the **feature set, the file formats and the command line** a Claude Code user types (`ling --compat claude -p … --output-format stream-json`, `-c`, `-r`, `CLAUDE.md`, `.claude/settings.json`, `.claude/commands`, `.claude/agents`), and, by D2, the **behaviour of its terminal interface**: inline or full screen, scrollback, the live region at the bottom, the redraw method, the transcript view, the key bindings and the command names (§6.5). It does **not** copy its look: no colours, logo, spinner words, glyphs or wording are taken (§2.1 item 2), which keeps the interface Mightling's and answers the trade-dress concern.
4. **One binary, one name, one switch.** The compatibility mode is a launch-time setting of the installed `ling`. First match wins: the launcher flag `--compat claude` (or `--compat off`), consumed by the launcher before Codex parses the command line, like the launcher's other intercepted flags; then `DREAMFERENCE_MIGHTLING_COMPAT=claude|off`; then `mightling_compat = "claude"|"off"` in the Dreamference TOML file, which `ling compat use claude|off` writes and `ling compat status` reports (as `ling prompt use` does for `mightling_prompt`); then `off`. The value names the file format being read (as Claude Code's own `/import` names the agents it imports from), not a product of Mightling's. A repository can never turn the mode on: `mightling_compat` is honoured only from the user's own config file (`~/.config/dreamference/config.toml`), never from a `dreamference.toml` inside the repository, and nothing in `.claude/` or `CLAUDE.md` is read until the user has switched the mode on (AGENTS.md's rule against taking the user's choices from where a clone could write them). The mode is fixed for the life of a session, because its command line, memory and key map are fixed at launch; there is no `/compat` slash command (a TUI hook would cost cap bytes for a rare action), and `ling compat use` takes effect at the next start. Everything Mightling guarantees for `ling` (the vendor channels closed, `CODEX_HOME=~/.mightling`, the sandbox, `/airgapped`, the egress audit, Night Shift, the desktop app) holds unchanged because it is the same binary and the same name. **One flag collides**: Codex's `-p` is `--profile`, Claude Code's `-p` is `--print`; in the mode `-p` means print and Codex's profile is reachable only as `--profile`; outside it nothing changes.
5. **Read, never write, the other agent's files.** `CLAUDE.md`, `.claude/settings*.json`, `.claude/commands`, `.claude/agents` and `~/.claude/…` are read at start and translated in memory or into `$CODEX_HOME`; nothing is ever written into the repository or into `~/.claude` (AGENTS.md's rule against writing the user's choices where a clone or the agent could write them).
6. **Measured before it is anyone's default.** The compatibility profile is opt-in. Its one model-visible change, the Claude-Code-shaped tool roster (§7.2), is an SWE-bench arm against `ling`'s default before any default changes (§11).

---

## 1. Goals and non-goals

### 1.1 Goals

1. A developer who uses Claude Code at work can use Mightling at home or offline **without relearning**: the same memory files, the same settings files, the same hooks, the same custom slash commands, skills and subagents, the same print-mode flags and output formats in scripts and CI, and a terminal interface that behaves the same way: where the conversation goes, what redraws, which key does what, what the commands are called (§6.5).
2. A repository set up for Claude Code (a `CLAUDE.md`, a `.claude/` folder) works in Mightling as it is, with no conversion step and nothing written into it.
3. Everything runs against the local model server; no request ever goes to Anthropic or any other vendor. The egress audit proves it for the new front door as it does for `ling` (§8).
4. The local model gets the tool shapes it handles best, decided by measurement (§7, §11), not by imitation.
5. The work stays inside Mightling's architecture: launcher code in `ling-rs/`, at most one-line hooks in `codex-patches/` under the explicit cap, a runner entry in `ling-admin`, and offline tests.

### 1.2 Non-goals

- **Copying Claude Code.** No source, no system prompt, no tool descriptions, no UI assets, no names or wording from it beyond the public file and flag names a user types (§2).
- **Anthropic's services.** No sign-in, no API key helper that reaches Anthropic, no Anthropic model ids, no claude.ai features, no remote MCP connectors from Anthropic's directory, no GitHub App or hosted Actions runner (`claude-code-action`), no web or mobile session hand-off.
- **Telemetry, cost accounting in dollars, usage limits.** Mightling has none and adds none (`/usage` reports tokens on the local server only).
- **Its look.** The interface's behaviour is in scope (D2, §6.5); its colours, logo and splash art, spinner words, glyphs, animations and wording are not, in any phase (§2.1 item 2).
- **Marketplace plugins fetched from the internet** (Claude Code's plugin marketplaces). A plugin already on disk may be read; nothing is fetched (§8.3).
- **Bit-exact output schemas.** The JSON and stream-JSON output follows the fields the public documentation names (§6.4); undocumented fields are not guessed.
- **Model choice.** Mightling serves one model; `--model` and `/model` accept only what `/v1/models` lists.

---

## 2. Clean room and licensing

### 2.1 The rule

**Claude Code is proprietary software.** Its source code and its system prompts are **not** open source. Its GitHub repository's licence file reads "© Anthropic PBC. All rights reserved. Use is subject to Anthropic's Commercial Terms of Service", and its npm package refers to the same terms (§3.17); the repository holds documentation, examples and issue tracking, not an open-source licence for the agent.

Therefore:

1. **Sources allowed:** Anthropic's public documentation pages (cited in §3 and §15) and Anthropic's public announcements; the installed CLI's `--help` and `--version` output; and the **observable behaviour** of an installed copy (what it prints to a terminal, including the escape sequences it writes, what files it reads and writes, what a flag does), recorded as behaviour and never as code. The owner allowed observation on 2026-10-20 (D2), **as a black box only**: the CLI is run in a pseudo-terminal in a throwaway folder, with a few trivial prompts, without signing in to anything new and without changing its settings; its package files are never opened. Whether Anthropic's commercial and usage terms restrict using the product to build a competing one was not checked by a lawyer (§14 Q11 keeps that part open); the owner accepted the risk for behaviour observation.
2. **Sources forbidden:** Claude Code's source, minified or not; decompiled, deobfuscated or "leaked" copies of it; its system prompts or tool descriptions, however obtained (including from a model's output, a proxy capture or a debugging log); its images, icons, colours, animations, spinner words and other assets.
3. **Open-source projects derived from forbidden sources are forbidden too.** Several popular community projects were built from Claude Code's own code, recovered from source maps or from the 2026-03-31 leak (anon-kode, Kode, claw-code, OpenClaude, free-code and mirrors of the leak; §5.2), whatever licence they now claim. They are excluded from option (b) on purpose, and nobody working on this spec's implementation opens their code. A project that only *reads* Claude Code's documented files for compatibility (OpenCode, Crush) is not in this class.
4. **The implementation contains no copied code, prompts or assets.** Every tool description, every prompt block, every message the user sees is written fresh for Mightling, in Mightling's voice, and reviewed against this rule before merge. Names of files, folders, settings keys, hook event names, flag names and JSON field names that the public documentation gives users to type or parse are interface facts and are used as such (the same way `ling-rs/skills/` already reads `.claude/skills` and Codex's own upstream code reads `CLAUDE.md`, §4.2).
5. **One person, one direction.** Whoever observes Claude Code's behaviour writes a behavioural note (inputs, outputs, files touched, escape sequences in summary); the implementer works from that note and the public docs. The first study is §6.5 (version 2.1.304, 2026-10-20, with its method and environment); later notes go to `docs/dev/ling-claude-observations.md` with the version observed and the date, so a later question about provenance has an answer.
6. **A review step per merge**: the pull request template for this work asks "every user-visible string and every model-visible string written for this change; no text copied from Claude Code; sources: …".

### 2.2 What the licences allow

| Component | Licence | Use here |
|---|---|---|
| Mightling | AGPL-3.0-or-later | the whole of the compatibility mode |
| Codex (the pinned `codex/` submodule) | Apache-2.0 | the engine, unchanged; its `external-agent-migration` crate is upstream Apache-2.0 code and may be called as it is |
| Claude Code | proprietary (Anthropic's terms) | nothing; behaviour and documented interfaces only |
| Option (b) candidates | see §5.2 | only an OSI-approved licence compatible with AGPL-3.0 (Apache-2.0, MIT) could be forked; source-available licences are excluded |

### 2.3 Interoperability, not imitation

Reading another tool's documented file formats and accepting its documented command-line flags is interoperability; it is what lets a user's existing `CLAUDE.md` and hook scripts work. The spec stops at the interface: the agent's behaviour behind it (how it plans, what its prompt says, what its tools are told) is Mightling's own.

### 2.4 The name

"Claude" and "Claude Code" are Anthropic's trademarks, and Anthropic's public legal page says their names may not be used "as part of your own product, feature, or company name … or in a way that suggests Anthropic built, endorses, or is partnered with your product" (§3.17). By the owner's decision D1 there is **no new name at all**: the product stays `ling`, and the feature is called the **compatibility mode** everywhere a user reads it (help text, start-up line, documentation). The one place the mark appears is the switch's *value*, `claude`, and the sentence that says what the mode reads ("reads Claude Code's `CLAUDE.md` and `.claude/` files"): a nominative, factual use that names the format, not Mightling's product, the same way Claude Code's own `/import` names the agents whose files it reads. Nothing in the mode says or suggests that Anthropic built, endorses or is partnered with it.

The old candidates (`ling-claude`, `ling-compat`, `ling-cc`, `lingo`) are withdrawn; no `ling-*` link is added.

---

## 3. Claude Code's feature set, from its public documentation

This section is the inventory the parity table (§4) is built from. Every row cites the public page it comes from; a fact not confirmed on a public page is marked *unverified*. It records **what** the feature does for the user, not how Claude Code implements it.

The documentation now lives at `https://code.claude.com/docs/en/<page>`; the older `docs.anthropic.com/en/docs/claude-code/…` and `docs.claude.com/…` addresses redirect there (checked for `hooks` and `agent-sdk/overview`). The pages read on 2026-10-19 describe Claude Code 2.1.2xx–2.1.303, so details drift; §14 Q9 asks who re-checks them.

### 3.1 The loop and its tools
[how-claude-code-works](https://code.claude.com/docs/en/how-claude-code-works), [tools-reference](https://code.claude.com/docs/en/tools-reference)
- A loop of gathering context, acting and verifying, repeated until the task is done; the user can interrupt at any time.
- Tool names as documented (the strings used in permission rules, hook matchers and subagent `tools` lists): `Read`, `Edit`, `Write`, `Bash`, `Glob`, `Grep`, `NotebookEdit`, `WebFetch`, `WebSearch`, `Agent` (subagents; the older name `Task` no longer appears), `Skill`, `AskUserQuestion`, `EnterPlanMode`, `ExitPlanMode`, `EnterWorktree`, `ExitWorktree`, `TaskCreate`/`TaskGet`/`TaskList`/`TaskUpdate`/`TaskStop` (task tracking; the older `TodoWrite` is off by default), `Monitor`, `LSP`, `ToolSearch`, MCP resource tools, `PowerShell` and scheduling tools. `MultiEdit` is called legacy; there is no `LS`.
- On Linux, macOS and WSL, `Glob` and `Grep` are absent by default and searching goes through `Bash`; naming them in `--tools`/`--allowedTools` brings them back.
- `Edit` replaces an exact, unique old string (or every occurrence with `replace_all`) in a file read first; `Read` returns numbered lines and pages with `offset`/`limit`.

### 3.2 Permission modes and rules
[permission-modes](https://code.claude.com/docs/en/permission-modes), [permissions](https://code.claude.com/docs/en/permissions)
- Modes: `default`, `acceptEdits`, `plan`, `auto` (a classifier approves calls), `dontAsk` (anything that would ask is denied) and `bypassPermissions`; set by `--permission-mode` or `permissions.defaultMode`.
- Rules `Tool` or `Tool(specifier)` in `permissions.allow`, `ask` and `deny`, evaluated deny, then ask, then allow; the lists merge across settings files. `Bash(npm run *)` (the older `Bash(ls:*)` means the same), `Read(./.env)` and `Edit(…)` in gitignore syntax, `WebFetch(domain:example.com)`, `mcp__server__*`, `Agent(Explore)`, `Skill(name *)`.
- `--add-dir`, `/add-dir` and `permissions.additionalDirectories` grant access to more folders. Writes to protected paths (`.git`, `.claude/`, …) are never approved automatically outside `bypassPermissions`.

### 3.3 Hooks
[hooks](https://code.claude.com/docs/en/hooks), [hooks-guide](https://code.claude.com/docs/en/hooks-guide)
- Events include `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PermissionDenied`, `PostToolUse`, `PostToolUseFailure`, `Notification`, `SubagentStart`, `SubagentStop`, `Stop`, `PreCompact`, `PostCompact`, `SessionEnd`, `InstructionsLoaded`, `ConfigChange`, `CwdChanged`, `FileChanged`, `WorktreeCreate`, `WorktreeRemove` and more (over thirty).
- Shape: `"hooks": {"<Event>": [{"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "…"}]}]}`; a matcher of names and `|` is an exact list, anything else a regular expression. Handler types `command`, `http`, `mcp_tool`, `prompt`, `agent`. Hooks live in the settings files, managed policy, plugins and skill or subagent frontmatter.
- Input on stdin as JSON (`session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, and per event `tool_name`, `tool_input`, …). Exit 0 is success (stdout JSON parsed), exit 2 blocks and returns stderr to the model, any other code is a non-blocking error.
- Output fields: `continue`, `stopReason`, `systemMessage`; `decision: "block"` with `reason`; for `PreToolUse`, `hookSpecificOutput.permissionDecision` (`allow`, `deny`, `ask`, `defer`), `permissionDecisionReason`, `updatedInput`, `additionalContext`. A hook's `allow` does not override a deny or ask rule.

### 3.4 Slash commands, skills and plugins
[commands](https://code.claude.com/docs/en/commands), [skills](https://code.claude.com/docs/en/skills), [plugins](https://code.claude.com/docs/en/plugins)
- Built-ins include `/init`, `/compact [instructions]`, `/clear`, `/resume`, `/model`, `/permissions`, `/hooks`, `/mcp`, `/agents`, `/memory`, `/config`, `/cost`, `/context`, `/rewind`, `/plan`, `/ide`, `/add-dir`, `/output-style`, `/statusline`, `/tasks`, `/export`, `/doctor`, `/plugin`, `/skills`, `/code-review` (alias `/review`), `/security-review`.
- "Custom commands have been merged into skills": `.claude/commands/deploy.md` and `.claude/skills/deploy/SKILL.md` both make `/deploy`. Skills live in `~/.claude/skills/<name>/SKILL.md`, `.claude/skills/<name>/SKILL.md` (also in parent and, on demand, nested folders), plugins, and the legacy `commands/` folders.
- Frontmatter: `name`, `description`, `when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `disallowed-tools`, `model`, `effort`, `context: fork`, `agent`, `hooks`, `paths`; substitutions `$ARGUMENTS`, `$ARGUMENTS[N]`/`$N`, named arguments, `${CLAUDE_SKILL_DIR}`, `${CLAUDE_SESSION_ID}`, and `` !`cmd` `` for a command's output. Only the description stays in context until the skill is used; the format follows the open [Agent Skills](https://agentskills.io) specification.
- A plugin is a folder with `.claude-plugin/plugin.json` bundling skills, agents, hooks and MCP servers, distributed through marketplaces and managed with `/plugin`.

### 3.5 Memory files
[memory](https://code.claude.com/docs/en/memory)
- Scopes: managed (`/etc/claude-code/CLAUDE.md` on Linux), user (`~/.claude/CLAUDE.md`), project (`./CLAUDE.md` or `./.claude/CLAUDE.md`), local (`./CLAUDE.local.md`).
- Files in the working directory and every ancestor load at start, root first; files in subfolders load when the agent first touches a file there; `.claude/rules/*.md` can be scoped to paths.
- `@path` imports resolve relative to the importing file, at most four hops deep, and are ignored inside code.
- `AGENTS.md` is read only when no `CLAUDE.md` or `CLAUDE.local.md` exists in the working directory or above it.
- Auto memory under `~/.claude/projects/<project>/memory/`; `/memory` lists and opens the files. A `#` quick-add shortcut is **not in the current docs** (the quick-commands table lists `/`, `!` and `@`); whether it still works is *unverified*.

### 3.6 MCP
[mcp](https://code.claude.com/docs/en/mcp)
- Transports stdio, HTTP (streamable), SSE (deprecated), WebSocket; `claude mcp add|list|get|remove|add-json|serve`.
- Scopes: local (default) and user in `~/.claude.json`, project in `.mcp.json` at the repository root (approved before first use, `${VAR}` and `${VAR:-default}` expanded).
- Tools appear as `mcp__<server>__<tool>`, resources as `@server:protocol://path`, prompts as slash commands. Large outputs are capped (default 25K tokens) and saved to a file.

### 3.7 Plan mode
[permission-modes](https://code.claude.com/docs/en/permission-modes)
- `Shift+Tab` cycles default → `acceptEdits` → `plan` (and further modes when enabled); also `/plan` and `--permission-mode plan`.
- The agent reads and explores without editing, then presents a plan (`ExitPlanMode`) that the user approves, edits or sends back; a read-only `Plan` subagent does the research.

### 3.8 Context compaction
[context-window](https://code.claude.com/docs/en/context-window), [how-claude-code-works](https://code.claude.com/docs/en/how-claude-code-works)
- Automatic near the limit: old tool outputs are cleared first, then the conversation is summarised; tuned by `autoCompactEnabled` and related settings.
- `/compact [focus]` by hand; a "Compact Instructions" section in `CLAUDE.md` steers the summary; `PreCompact`/`PostCompact` hooks fire.
- After a compaction the project memory is re-injected and a few recent files are re-read.

### 3.9 Headless and print mode
[headless](https://code.claude.com/docs/en/headless), [cli-reference](https://code.claude.com/docs/en/cli-reference), [agent-sdk/typescript](https://code.claude.com/docs/en/agent-sdk/typescript)
- `claude -p`/`--print` with `--output-format text|json|stream-json` and `--input-format text|stream-json`; `--max-turns`, `--max-budget-usd`, `--allowedTools`, `--disallowedTools`, `--tools`, `--permission-mode`, `--permission-prompt-tool`, `--system-prompt[-file]`, `--append-system-prompt[-file]`, `--json-schema`, `--include-partial-messages`, `--verbose`, `--mcp-config`, `--strict-mcp-config`, `--bare` (no hooks, skills, plugins, MCP or memory discovery).
- A stream begins with a `system`/`init` message and ends with a `result` message. The result carries `type`, `subtype` (`success`, `error_max_turns`, `error_during_execution`, …), `session_id`, `duration_ms`, `duration_api_ms`, `is_error`, `num_turns`, `result`, `stop_reason`, `total_cost_usd`, `usage`, `permission_denials`, `structured_output`.

### 3.10 Sessions
[sessions](https://code.claude.com/docs/en/sessions)
- `claude -c` continues the latest session in the folder; `claude -r <id|name>` resumes one (a picker without an argument); `/resume`; `--fork-session`, `--session-id`, `-n/--name`.
- Transcripts are JSON Lines under `~/.claude/projects/<folder>/<session-id>.jsonl`, a format the docs call internal and unstable.

### 3.11 IDE integrations
[vs-code](https://code.claude.com/docs/en/vs-code), [jetbrains](https://code.claude.com/docs/en/jetbrains)
- A VS Code extension (side-by-side diffs, selection shared, file mentions) and a JetBrains plugin (the IDE's diff viewer, selection sharing); `/ide` connects a terminal session to the IDE over a local connection. Both extensions are Anthropic's own and not open source.

### 3.12 Background work
[interactive-mode](https://code.claude.com/docs/en/interactive-mode), [tools-reference](https://code.claude.com/docs/en/tools-reference)
- `Bash` with `run_in_background`; `Ctrl+B` moves a running command or subagent to the background; a command that times out is backgrounded rather than killed; output goes to a file the agent reads; `/tasks` lists and stops them.
- Subagents may run in the background (`background: true` in their frontmatter).

### 3.13 Git and pull requests
[common-workflows](https://code.claude.com/docs/en/common-workflows), [best-practices](https://code.claude.com/docs/en/best-practices), [github-actions](https://code.claude.com/docs/en/github-actions), [worktrees](https://code.claude.com/docs/en/worktrees)
- Commits and pull requests on request, through `git` and the `gh` CLI; an `attribution` setting controls the attribution lines.
- `claude -w/--worktree [name]` works in a new git worktree under `.claude/worktrees/`; subagents can take `isolation: worktree`.
- A GitHub Action (`anthropics/claude-code-action`, MIT) answers `@claude` mentions; it calls Anthropic's service and is out of scope here.

### 3.14 Settings files
[settings](https://code.claude.com/docs/en/settings), [env-vars](https://code.claude.com/docs/en/env-vars)
- Precedence, highest first: managed (`/etc/claude-code/managed-settings.json` on Linux), command line (`--settings`), `.claude/settings.local.json`, `.claude/settings.json`, `~/.claude/settings.json`; arrays merge.
- Keys include `permissions`, `env`, `hooks`, `model`, `apiKeyHelper`, `statusLine`, `outputStyle`, `attribution`, `cleanupPeriodDays`, `autoCompactEnabled`. MCP servers are kept in `~/.claude.json` and `.mcp.json`, not in the settings files.

### 3.15 Subagents
[sub-agents](https://code.claude.com/docs/en/sub-agents)
- Markdown files with frontmatter (`name` and `description` required; `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `skills`, `mcpServers`, `hooks`, `background`, `isolation`, …); the body is the subagent's instructions.
- Priority managed, then `--agents` JSON, then `.claude/agents/`, then `~/.claude/agents/`, then plugins; `/agents` manages them. Built-ins include `general-purpose`, `Explore` and `Plan` (read-only).
- Each subagent has its own context; only its final result returns to the caller.

### 3.16 Output styles, status line, checkpoints
[output-styles](https://code.claude.com/docs/en/output-styles), [statusline](https://code.claude.com/docs/en/statusline), [checkpointing](https://code.claude.com/docs/en/checkpointing)
- Output styles change the system prompt's manner (built-in and custom `.md` files in `output-styles/`).
- A status line command gets the session's JSON on stdin and prints one line.
- Every prompt makes a checkpoint; `Esc Esc` or `/rewind` restores code, conversation or both, or summarises from a point. Changes made through `Bash` are not tracked.

### 3.17 Licence and trademark
[LICENSE.md in anthropics/claude-code](https://github.com/anthropics/claude-code/blob/main/LICENSE.md), [agent-sdk/overview](https://code.claude.com/docs/en/agent-sdk/overview), [legal-and-compliance](https://code.claude.com/docs/en/legal-and-compliance), [Anthropic trademark guidelines](https://www.anthropic.com/legal/trademark-guidelines)
- The repository's licence file reads "© Anthropic PBC. All rights reserved. Use is subject to Anthropic's Commercial Terms of Service." The npm package's licence field points to its README. The TypeScript Agent SDK carries the same terms; the Python Agent SDK wrapper is MIT but drives the proprietary CLI.
- The legal page says the Claude Code or Anthropic names may not be used "as part of your own product, feature, or company name … or in a way that suggests Anthropic built, endorses, or is partnered with your product"; a plain statement of compatibility is allowed.

---

## 4. Feature parity

### 4.1 The table

**Mightling today** is `ling` on `main` at `a5c0ad7` with the pinned Codex. **Plan** is this spec's phase (§12) or "no" with a reason. **Priority**: P0 is needed for the first usable release, P1 for parity, P2 nice to have, "—" not planned.

| # | Feature (§3) | Mightling today | Plan | Priority |
|---|---|---|---|---|
| 1 | The agentic loop, interrupt, steer | Codex's loop in `ling` | exists | — |
| 2 | `Read`, `Edit`, `Write` as separate tools | shell reads, `apply_patch` edits | discrete roster, Phase 2, an SWE-bench arm (§7.2, §11) | P1 |
| 3 | `Bash`, `run_in_background`, `Ctrl+B`, `/tasks` | Codex's shell, background terminals (`/ps`, `/stop`) | exists; `/tasks` as an alias of `/ps` in Phase 3 | P0 (exists) |
| 4 | `Grep`, `Glob` (off by default on Linux in Claude Code itself) | `rg`/`find` in the shell | optional in the discrete roster | P2 |
| 5 | `WebFetch`, `WebSearch` | `ling-fetch`, `ling-search` as shell commands, following `/airgapped` | as tools in the discrete roster, Phase 2 | P1 |
| 6 | Task tracking (`TaskCreate`…, older `TodoWrite`) | Codex's `update_plan` | exists (the names differ; hooks map them, §9.5) | P0 (exists) |
| 7 | Subagents: the `Agent` tool, `.claude/agents/*.md`, `Explore`/`Plan` built-ins | Codex's subagents and agent roles; `/import` converts once | converted live per session, Phase 1 (§9.4) | P0 |
| 8 | `AskUserQuestion` | Codex's `request_user_input` | exists | — |
| 9 | Permission modes (`default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions`) | Codex's approval policies and sandbox modes, Plan mode; Codex's auto-review hidden (patch `0012`) | mapped, Phase 2 (§9.3); `auto` not planned | P0 (`auto`: —) |
| 10 | Permission rules (`allow`/`ask`/`deny`, `Bash(npm run *)`, `Read(./.env)`) | Codex exec-policy rules (a different syntax), no path rules | translated, Phase 2 (§9.3) | P0 |
| 11 | Command hooks in settings files | Codex's hooks (same core events, same exit-code and decision fields), `/import` once | read live with a translating shim, Phase 1 (§9.5) | P0 |
| 12 | `http`, `mcp_tool`, `prompt`, `agent` hook handlers | none | `mcp_tool` and loopback `http` in Phase 3; `prompt`/`agent` P2 | P2 |
| 13 | Built-in slash commands | most exist under Codex's names (`/init`, `/compact`, `/resume`, `/model`, `/permissions`, `/hooks`, `/mcp`, `/agents`, `/plan`, `/review`, `/export`, `/status`, `/statusline`, `/ide`, `/skills`) | Claude Code's names through one slash-command fallback served from `ling-rs/` (§6.5.5, §6.5.6 item 1): renamed ones (`/branch`, `/keybindings`, `/tasks`, `/cost`) and missing ones (`/memory`, `/rewind`, `/context`, `/config`, `/add-dir`, `/doctor`, `/help`); Phase 3, under the 48,500-byte cap (D4) | P0 (D3) |
| 14 | Custom commands, `.claude/commands/` | not read | converted into skills per session, Phase 1 (§9.4) | P0 |
| 15 | Skills, `.claude/skills/` | **done**: linked as `from-claude` and `from-repo-claude` (SKILLS §15) | exists | P0 (exists) |
| 16 | Plugins (`.claude-plugin/plugin.json`, marketplaces) | Codex's own plugins; `/import` can add marketplaces (§8.3) | local plugins only, Phase 3; no marketplace fetch | P2 |
| 17 | `CLAUDE.md` hierarchy, `CLAUDE.local.md`, `@path` imports | **not read** | Phase 1 (§9.1) | P0 |
| 18 | `.claude/rules/*.md` scoped to paths | none | Phase 3 | P2 |
| 19 | Auto memory, `/memory` | Codex's memories feature (off) | `/memory` Phase 3; auto memory not planned | P2 / — |
| 20 | MCP: `.mcp.json`, `~/.claude.json` servers, `mcp__server__tool` names | Codex MCP from `config.toml`, flattened to functions by `0020` | `.mcp.json` and `~/.claude.json` read, Phase 1 | P0 |
| 21 | Plan mode, `Shift+Tab`, plan approval | `/plan` (Codex's Plan mode) | `--permission-mode plan`; `Shift+Tab` cycles `ling`'s approval modes (D6, §6.5.4); plan through `/plan` | P0 |
| 22 | Automatic compaction, `/compact [focus]` | Codex's plus Mightling's tuned compaction (KV-pool limit, turn-end, summary cap) | exists; the focus argument checked in Phase 0 | P1 |
| 23 | Print mode `-p`, `--output-format text\|json\|stream-json` | `ling exec --json` (Codex's event schema), `-o` | Phase 1 (§6.3, §6.4) | P0 |
| 24 | `--input-format stream-json` | none | Phase 2 | P1 |
| 25 | `--json-schema` structured output | `ling exec --output-schema <file>` | mapped, Phase 1 | P1 |
| 26 | `-c`, `-r`, `--fork-session`, `/resume` | `ling resume --last`, `ling exec resume`, `ling fork` | flags mapped, Phase 1 | P0 |
| 27 | Importing Claude Code sessions | `/import` (Codex) | exists; audited in Phase 0 | P2 |
| 28 | VS Code and JetBrains extensions, `/ide` | Anthropic's extensions are proprietary; Mightling has `ling-admin mcp` for JetBrains/VS Code, the Cline and Continue runners, Codex's `/ide`, the desktop app | no new extension | — |
| 29 | Git and pull-request work, `attribution` | the model uses `git`/`gh` in the shell, `/review`, `/diff` | `attribution` mapped to the prompt's commit guidance, Phase 3 | P1 |
| 30 | GitHub Actions (`claude-code-action`) | none | not planned (calls Anthropic's service) | — |
| 31 | Worktrees (`-w`, subagent `isolation: worktree`) | Codex's `/worktree`; Night Shift's worktrees | `-w` mapped, Phase 1 | P1 |
| 32 | Settings files and their precedence | not read | Phase 1 (hooks, `env`), Phase 2 (permissions) (§9.2) | P0 |
| 33 | Output styles | named system prompts (`ling prompt`) | not planned; nearest is a named prompt | P2 |
| 34 | Command status line | Codex's `/statusline` items | Phase 3 | P2 |
| 35 | Checkpoints and `/rewind` (code and conversation) | `/fork` and backtracking restore the conversation only | Phase 4, by the owner's decision (§14 Q7) | P1 |
| 36 | Agent SDK | `ling exec`, `ling app-server` (Codex's protocol) | not planned (the SDK is Anthropic's) | — |
| 37 | Sign-in, cost in dollars, usage limits | none (patches `0005`, `0013`, `0015`) | not planned, by design | — |
| 38 | The terminal interface's behaviour (inline or fullscreen, scrollback, live region, transcript view) and key bindings | Codex's TUI: fullscreen by default, `--no-alt-screen` inline, its own key map | §6.5: zero-patch configuration in Phase 1 (§6.5.4), hooks in Phase 3 (§6.5.6) | P0 (D2, D3) |

### 4.2 What the pinned Codex already brings

Checked in `codex/codex-rs/` at `rust-v0.158.0` on 2026-10-19 (read only):

- **`/import`** (`tui/src/slash_command.rs`: "import setup, this project, and recent chats from Claude Code"), backed by the crate `external-agent-migration`: `ClaSource` reads `.claude/`, `CLAUDE.md` and `settings.json`; `hooks_cla.rs` converts Claude Code hook groups (`matcher`, `hooks`) into Codex hooks; `subagents.rs` converts `.claude/agents/*.md` into Codex agent roles (`agents/*.toml`); `command_migration.rs` (in `core-plugins`) turns custom commands into skills; `mcp.rs` converts MCP server entries; `sessions/` imports recent conversations; a `RewriteProfile` replaces "Claude"/"Claude Code" and `CLAUDE.md` with Codex's names in what it copies. It is **visible in `ling` today**, untouched by any Mightling patch, and not covered by the egress audit (§8.3). It is a **one-time conversion** into Codex's own formats, not live reading; the memory part is behind the feature `external_agent_memory_import` (under development, off).
- **Hooks** (`codex-rs/hooks/`): events `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt`; command hooks with JSON on stdin; exit code 2 as a block; JSON output with `decision: "block"`, `hookSpecificOutput.permissionDecision` (`deny` with a reason supported; `allow` accepted only together with `updatedInput`; a plain `allow` and any `ask` rejected as unsupported by `engine/output_parser.rs`), `additionalContext`; `hooks.json` discovered in config folders; hook trust by hash. Mightling already relies on them (refine's `UserPromptSubmit`, SWE-bench's `--hooks issue-v1`).
- **Project documents**: `AGENTS.md` from the repository root down to the working directory, plus `project_doc_fallback_filenames` (`core/src/agents_md.rs`), a list of other file names read in a folder **only when it has neither `AGENTS.override.md` nor `AGENTS.md`** (the first of the three that exists wins, per folder); no `@path` import expansion.
- **Plan mode** (`/plan`, `collaboration-mode-templates/templates/plan.md`), **subagents** (`/agents`, `/multi-agents`, the `multi_agents` tools), **the plan tool** (`update_plan`, Codex's to-do list), **background terminals** (`unified_exec`, `/ps`, `/stop`), **`/review`**, **`/worktree`**, **`/fork`**, **`/resume`**, **`/export`**, **`/compact`**, **`/init`** (writes `AGENTS.md`), **`/mcp`**, **`/hooks`**, **`/permissions`**, **`/statusline`**, **`/ide`**, **`/skills`**, **`/plugins`**, and `ling exec --json`, `ling exec resume`, `ling resume --last`.

So most of the work is translation at the edges, which is why §5 recommends (c).

### 4.3 The top gaps against `ling` today

In order of how often a Claude Code user would hit them:

1. **`CLAUDE.md` is not read.** A repository with only a `CLAUDE.md` gives `ling` no project instructions; `~/.claude/CLAUDE.md`, `CLAUDE.local.md` and `@path` imports are never read. (This repository's own `CLAUDE.md` is the one line `@AGENTS.md`, which works only because `AGENTS.md` exists beside it.)
2. **`.claude/settings.json` is not read**: its hooks, permission rules and environment do nothing in `ling` unless the user runs `/import` once, and a later change to the file is not seen.
3. **No print mode with Claude Code's flags**: `-p`, `--output-format json|stream-json`, `--input-format stream-json`, `--max-turns`, `--allowedTools`, `--permission-mode`, `-c`/`-r` in scripts; `ling exec --json` emits Codex's own event schema.
4. **Hooks written for Claude Code see different tool names and inputs**: a `matcher: "Bash"` or `"Edit|Write"` matches nothing in Codex (whose tools are `exec_command`/`shell` and `apply_patch`), and a script reading `tool_input.file_path` finds no such field.
5. **Permission rules** (`allow`/`ask`/`deny` with `Bash(npm run test:*)`, `Read(./.env)`, `Edit(…)`) have no live equivalent; Codex's sandbox modes and exec-policy prefix rules are a different model.
6. **Custom slash commands** in `.claude/commands/` and `~/.claude/commands/` (`$ARGUMENTS`, frontmatter) and **subagents** in `.claude/agents/` are not seen live.
7. **The tool roster**: Claude Code offers separate `Read`, `Write`, `Edit`, `WebFetch` and `WebSearch` tools (and `Grep`/`Glob`, though on Linux it now searches through `Bash` by default, §3.1); `ling` offers a shell and `apply_patch`, with `ling-search`/`ling-fetch` as shell commands. Whether the separate tools help Qwen3.8 is unmeasured (§7.2, §11).
8. **Rewind of file changes** (Claude Code's checkpoints) has no equivalent in the pinned Codex; `/fork` and backtracking restore the conversation, not the files.
9. **`/memory`** to list and edit the memory files, and **`.claude/rules/*.md`** scoped to paths.
10. **`/import` is an unaudited network path**: its plugin step can add marketplaces from GitHub (§8.3).

---

## 5. How to build it: three options

### 5.1 The options

**(a) From scratch in Rust**, a new agent loop reusing the `ling-rs` leaf crates (`airgapped/`, `node-locator/`, `skills/`, `masking/`, `web/`, `tools/`).

- *For:* full control of the loop, the tool roster and the interface; no Codex rebase cost; could match Claude Code's interface most closely.
- *Against:* everything `ling` gets from Codex has to be written again and kept secure: the TUI, sessions and resume, compaction (and the six compaction measures Mightling has tuned since 2026-10-02), MCP, skills loading, subagents, plan mode, the hook engine, the app-server protocol the desktop app speaks, and above all **the sandbox**. `ling-admin`'s own sandbox (`SandboxManager`) was removed on 2026-10-01; `/airgapped on` is enforced inside Codex's sandbox helper (patch `0019`) and on Windows by patch `0024`; bwrap needs the AppArmor profile (`docs/dev/host-safety.md`). A new binary would need its own `bwrap` policy, its own network-namespace enforcement, its own `audit egress --compat claude` trace, its own SWE-bench runtime (another patchelf'd binary per container) and its own desktop adapter. Months of work before the first parity row, and a second security surface for good.

**(b) Fork or extend an open-source agent** with a compatible licence (§5.2).

- *For:* a working loop and interface on day one; several candidates already read `CLAUDE.md` or `AGENTS.md`, speak MCP and take a chat-completions-compatible local endpoint; Qwen Code is tuned for the Qwen family's tool calls.
- *Against:* the strongest candidates are TypeScript/Node or Go, not Rust, so none of `ling-rs`'s crates is reused directly; every one has its own sandbox story (Docker/Podman, Seatbelt, or none) that would have to be replaced with bwrap and `/airgapped on`; several carry telemetry or update checks on by default that must be found and closed the way patches `0013`, `0015`, `0016` closed Codex's (a new egress audit and a new patch series to rebase on every upstream release); and Mightling would then maintain two agents, two patch series and two session formats. Night Shift, SWE-bench and the desktop app each need an adapter.

**(c) Extend `ling` (Codex) with a compatibility layer** in `ling-rs/`: read `CLAUDE.md` and `.claude/` live, translate hooks, permission rules, commands and subagents, accept Claude Code's print-mode flags, and offer a Claude-Code-shaped tool roster as a measured option.

- *For:* the engine already has most of the feature set (§4.2); the sandbox, `/airgapped`, the egress audit, the vendor channels closed, the compaction work, Night Shift, the desktop app, the code index, `ling-docs` and the SWE-bench harness all keep working because it is the same binary; Phase 1 needs no patch, so the 45 KB cap is untouched; the work is small enough to measure in days.
- *Against:* the interface is Codex's TUI made to behave like Claude Code's through configuration and a few hooks (§6.5), not a new interface, and the hooks take the patch cap to 48,500 bytes (D4, §6.5.6); some semantics cannot be mapped exactly (permission rules over paths, hook `allow`/`ask` decisions that Codex rejects, `acceptEdits`), so the layer must say clearly where it differs; and every Codex bump must re-check the translation (a test pins it, §13).

### 5.2 The open-source agents that already clone parts of it

Checked on 2026-10-19 (licence from each repository's licence file, activity from its last push):

| Agent | Repository | Language | Licence | Status | How much of Claude Code's way of working |
|---|---|---|---|---|---|
| OpenCode | [anomalyco/opencode](https://github.com/anomalyco/opencode) (`sst/opencode` redirects) | TypeScript | MIT | active | the closest: `AGENTS.md` with `CLAUDE.md` and `~/.claude/CLAUDE.md` as fallbacks ([rules](https://opencode.ai/docs/rules/)), skills including `.claude/skills/` ([skills](https://opencode.ai/docs/skills/)), a read-only plan agent, subagents, `.opencode/commands/`, plugin hooks ([plugins](https://opencode.ai/docs/plugins/)), `opencode run --format json`; any chat-completions-compatible endpoint |
| Kilo Code | [Kilo-Org/kilocode](https://github.com/Kilo-Org/kilocode) | TypeScript | MIT | active | its CLI is a fork of OpenCode |
| Crush | [charmbracelet/crush](https://github.com/charmbracelet/crush) | Go | **FSL-1.1-MIT** (source-available; bars competing use until it converts to MIT) | active | `CRUSH.md`/`AGENTS.md`, skills, MCP, early hooks; custom providers. Excluded from (b) by its licence |
| opencode (Go) | [opencode-ai/opencode](https://github.com/opencode-ai/opencode) | Go | MIT | archived 2025-09, became Crush | historical |
| Qwen Code | [QwenLM/qwen-code](https://github.com/QwenLM/qwen-code) | TypeScript | Apache-2.0 | active | began from Gemini CLI; subagents, skills, hooks, MCP, plan mode, `qwen -p`; built for the Qwen family's tool calls; vLLM and Ollama endpoints |
| Gemini CLI | [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | TypeScript | Apache-2.0 | active | hooks, skills, subagents, plan mode, MCP, custom commands, `--output-format json\|stream-json`; `GEMINI.md`; a local chat-completions-compatible endpoint is *unverified* |
| Codex CLI | [openai/codex](https://github.com/openai/codex) | Rust | Apache-2.0 | active | `ling`'s engine: `AGENTS.md`, hooks, subagents, skills, MCP, `exec`, and the Claude Code importer (§4.2) |
| Goose | [aaif-goose/goose](https://github.com/aaif-goose/goose) (`block/goose` redirects) | Rust | Apache-2.0 | active | Agent Skills and MCP extensions; Ollama; the rest *unverified* |
| Cline | [cline/cline](https://github.com/cline/cline) | TypeScript | Apache-2.0 | active | Plan/Act modes, `.clinerules`, skills, MCP, a CLI with JSON output; SDK hooks, not shell hooks; already a Mightling runner |
| OpenHands | [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands), CLI in [OpenHands-CLI](https://github.com/OpenHands/OpenHands-CLI) | Python/TypeScript | MIT | active | its own design; already a Mightling runner |
| Aider | [Aider-AI/aider](https://github.com/Aider-AI/aider) | Python | Apache-2.0 | quiet since 2026-05 | a pair-programming chat, not Claude Code's model |
| Roo Code | [RooCodeInc/Roo-Code](https://github.com/RooCodeInc/Roo-Code) | TypeScript | Apache-2.0 | archived 2026-05 | — |
| Plandex | [plandex-ai/plandex](https://github.com/plandex-ai/plandex) | Go | MIT | dormant | — |

**Excluded on purpose, and not to be opened by anyone working on this spec** (§2.1 item 3). These are built from Claude Code's own code, recovered from source maps or a leak, whatever licence they claim:

- **anon-kode** (`dnakov/anon-kode`): taken down after Anthropic's DMCA notice of 2025-04-28, which calls it a derivative work "source mapped" from Claude Code ([notice](https://github.com/github/dmca/blob/master/2025/04/2025-04-28-anthropic.md)).
- **Kode** (`shareAI-lab/Kode-CLI`): advertises `.claude` and `CLAUDE.md` compatibility, which makes it look like an interoperable clone, but its README says it contains code from anon-kode.
- **claw-code** (`ultraworkers/claw-code`): created the night of the 2026-03-31 source leak, reported as a rewrite of the leaked code ([36kr](https://eu.36kr.com/en/p/3747613304193796)); that it was not named in the [DMCA notice of that day](https://github.com/github/dmca/blob/master/2026/03/2026-03-31-anthropic.md) does not make it clean.
- **OpenClaude** (`Twigpine/openclaude`): says it "originated from the Claude Code codebase"; it is the first search result for running Claude Code against a local endpoint, which is exactly this spec's topic, so the warning matters.
- **free-code** (`freecodexyz/free-code`): describes itself as a fork of Claude Code made from the 2026 leak.
- Mirrors of the leaked source under various names.

**For (b), only OpenCode/Kilo Code (MIT), Qwen Code and Gemini CLI (Apache-2.0) and Goose (Apache-2.0, Rust) qualify.** OpenCode copies the most of Claude Code's way of working; Goose is the only Rust one but copies the least; Qwen Code is the one built around this model family.

### 5.3 Recommendation: (c)

Choose **(c)**. The decisive facts:

1. **The feature set is mostly there already.** Hooks with the same events and decision fields, plan mode, subagents, background terminals, review, worktrees, resume and fork, MCP, skills, and an upstream importer of Claude Code's own files are in the pinned Codex (§4.2). The gaps (§4.3) are about reading files and translating names, which is launcher work.
2. **Security is the expensive part, and (c) gets it for free.** `/airgapped on`, the bwrap sandbox, the vendor channels closed and the egress audit took several weeks to get right for one binary; (a) and (b) would repeat that for a second.
3. **Measurement is the point.** Mightling's decisions are made by A/B on this machine. Under (c) the Claude-Code-shaped tool roster is one environment variable on the existing harness (§11), so its effect on Qwen3.8 can be known within a night; under (a) or (b) an arm would compare two whole agents and tell little about why one wins.
4. **No license question.** Everything new is AGPL Mightling code over an Apache-2.0 engine; no fork of a third-party agent and no source-available licence is involved.

Keep from (b) only what costs nothing: Qwen Code (Apache-2.0) is the best public reference for the tool shapes the Qwen family is used to, and its tool *schemas* (not its prompts) may be read to inform §7.2's measurement; a `qwen-code` runner pair for one SWE-bench comparison night is listed as an open question (§14 Q6), not a plan.

---

## 6. Architecture

### 6.1 Where it lives

```text
ling --compat claude …   (or DREAMFERENCE_MIGHTLING_COMPAT=claude, or mightling_compat = "claude")
   ~/.local/bin/ling ──► ~/.local/share/dreamference/mightling/bin/ling   (the one binary, no new link)
                                   │
                                   ▼
         ling-rs/src/lib.rs (the launcher, before Codex parses its arguments)
           ├── compat::profile()          --compat, then the variable, then the TOML key, then off (§0.4);
           │                              `ling compat use|status|doctor` (launcher subcommands)
           ├── compat::tui                the interface of §6.5: -c tui.* overrides and [tui.keymap]
           ├── compat::cli                Claude-Code-style flags  →  Codex flags / `exec` / `resume`
           ├── compat::memory             CLAUDE.md hierarchy + @imports  →  a SessionStart hook's context
           ├── compat::settings           .claude/settings*.json  →  -c overrides, exec-policy rules,
           │                              translated hooks (in $CODEX_HOME, never in the repository)
           ├── compat::commands           .claude/commands/*.md  →  skills linked like from-claude
           ├── compat::agents             .claude/agents/*.md  →  agent roles under $CODEX_HOME
           ├── compat::output             Codex exec JSONL  →  text | json | stream-json
           └── ling compat tools (MCP)    optional Read/Write/Edit/Grep/Glob/WebFetch/WebSearch
                                          as plain functions (patch 0020 flattens MCP tools)
                                   │
                                   ▼
         Codex (TUI, exec, sandbox, hooks engine, compaction, app-server): unchanged in Phase 1;
         Phase 3 adds the TUI hooks of §6.5.6 under a raised patch cap
                                   │
                                   ▼
         the model server: SGLang (Qwen3.8-27B, qwen3_coder tool parser) or ling-engine
```

A leaf crate, `ling-rs/compat/`, holds the parsers and translators (pure functions, unit-tested without a model, as `ling-rs/skills/` is); `ling-rs/src/compat.rs` wires them into the launcher. The new MCP server is a subcommand of the same binary (`ling compat tools serve`, intercepted by the launcher like `ling apps serve`).

### 6.2 What happens at start

1. The launcher decides the profile (§0.4). Without it, `ling` behaves exactly as today: every compatibility path is gated on the profile, and a test asserts that a plain `ling` start writes the same `config.toml` and catalog as before (§13).
2. It resolves the model server and reads `/v1/models` as it does for `ling`.
3. It finds the project root (git top level, as Codex does) and reads, in this order, never writing any of them: managed settings if present, `~/.claude/settings.json`, `.claude/settings.json`, `.claude/settings.local.json` (precedence as documented, §3.14), plus `~/.claude/CLAUDE.md`, the project's `CLAUDE.md` files and `CLAUDE.local.md` (§9).
4. It translates what it read into Codex's terms for **this session only**: `-c` overrides on the command line, a session folder `$CODEX_HOME/compat/claude/<project-hash>/` for the translated hooks, rules and agent roles, and links for commands (§9.4). The folder is rebuilt at every start from the source files, so an edit to `.claude/settings.json` is seen at the next session.
5. It prints one start-up line naming what it read (`Claude Code compatibility: CLAUDE.md (2), .claude/settings.json (hooks 3, rules 7), commands 4, agents 2; differences: …`), and the full report under `ling compat doctor`.
6. It runs Codex with the translated arguments.

### 6.3 The command line

The launcher accepts the print-mode flags the public CLI reference documents (§3.9) and maps them; anything it cannot honour is refused with a message, never silently ignored (the `ling-admin` rule that every command rejects unknown arguments). In the mode `-p` is Claude Code's `--print`, not Codex's `--profile`, which stays reachable as `--profile` (§0.4); `--compat` itself is removed from the arguments before Codex sees them.

| Claude Code (documented) | `ling --compat claude` maps it to |
|---|---|
| `ling --compat claude` (no prompt) | the `ling` TUI with the profile on and the interface of §6.5 |
| `ling --compat claude "prompt"` | the TUI with a first prompt |
| `-p`, `--print "prompt"` | `ling exec` with the profile, output translated (§6.4) |
| `--output-format text\|json\|stream-json` | `exec --json`, translated into the chosen format |
| `--input-format stream-json` | a launcher reader that turns each user message into a turn of one `exec` session (`exec resume` between messages) |
| `-c`, `--continue` | `ling resume --last` (TUI) or `ling exec resume --last` (print) |
| `-r`, `--resume <id>` | `ling resume <id>` / `ling exec resume <id>` |
| `--max-turns N` | a turn counter in the output translator that interrupts the session after N model turns (result subtype `error_max_turns`) |
| `--json-schema <schema>` | `ling exec --output-schema <file>`, the answer reported as `structured_output` |
| `--tools`, `--bare` | the roster (§7.2); `--bare` skips every compatibility read (memory, settings, commands, agents, `.mcp.json`) |
| `--fork-session` | `ling fork` |
| `-w`, `--worktree [name]` | Codex's worktree start (`/worktree`), under `$CODEX_HOME`, never `.claude/worktrees/` |
| `--system-prompt`, `--permission-prompt-tool`, `--max-budget-usd`, `--include-partial-messages` | refused in Phase 1 with a message (`--system-prompt` would replace Mightling's prompt, which the named prompts do instead; there is no budget in dollars) |
| `--allowedTools`, `--disallowedTools` | session-only permission rules (§9.3) |
| `--permission-mode default\|acceptEdits\|plan\|bypassPermissions` | §9.3's table |
| `--append-system-prompt "…"` | appended to the catalog's prompt for this session (the launcher already writes the prompt) |
| `--add-dir <path>` | Codex's writable roots (`--add-dir`) |
| `--model <id>` | accepted only if `/v1/models` lists it; otherwise refused with the served id |
| `--mcp-config <file>` | MCP entries translated into `-c mcp_servers.…` for this session |
| `--verbose`, `--debug` | `RUST_LOG` as `ling-admin run --debug` sets it |

Session ids are Codex's; `ling --compat claude -r` accepts them and prints them in `json` output as `session_id`.

**Built** (`ling-rs/src/compat_print.rs`). The launcher resolves the model server once, checks `--model` against `/v1/models`, and runs the task as a child `ling exec --json --skip-git-repo-check`, because Claude Code's print mode runs in any folder. The child gets the server's address in `DREAMFERENCE_VLLM_HOST`.
- Input: piped standard input follows the prompt, or is the prompt when none is given.
- Mapped: `-p`, `--output-format`, `-c`, `-r <id>`, `--max-turns` (the translator counts tool calls and answers as turns, and sends the child Ctrl+C past the limit), `--json-schema` (`--output-schema`, the answer parsed into `structured_output`), `--append-system-prompt[-file]` (`-c developer_instructions=…`), `--add-dir`, `--model`, `--permission-mode default|dontAsk|acceptEdits|bypassPermissions`, `--verbose`, `--debug`, and `--bare` (recorded for later phases' reads). `-C`, `--config` and `--profile` pass through.
- Refused with their reason: `plan` and `auto`, every flag of the table's refused row, and the flags of later phases (`--allowedTools`, `--tools`, `--input-format`, `--mcp-config`, `--settings`, `--agents`, `--fork-session`, `-w`).

### 6.4 Output formats

`compat::output` reads `ling exec --json`'s JSON Lines and writes:

- **`text`**: the last agent message only (what `exec -o` writes).
- **`json`**: one result object at the end with the fields the public docs list (§3.9): `type`, `subtype`, `session_id`, `duration_ms`, `duration_api_ms` (the time spent waiting on the model server), `is_error`, `num_turns`, `result`, `stop_reason`, `usage` (tokens from the local server), `permission_denials`, `structured_output` when `--json-schema` was given, and `total_cost_usd` as `0`, since nothing is billed. Fields the docs do not list are not invented.
- **`stream-json`**: one JSON object per line as events happen: a `system` message of subtype `init` (session id, working folder, the tools offered), assistant and user messages with tool calls and their results, and the final `result` object, using the documented message types only.

The exact field list is pinned by fixtures written from the public reference (§13); the spec does not reproduce it here because the documentation, not this file, is the authority, and it changes.

**Built.** The fields follow the Agent SDK reference's `SDKSystemMessage`, `SDKAssistantMessage`, `SDKUserMessage` and `SDKResultMessage`, read on 2026-10-20.
- Codex's items become `tool_use` blocks under Claude Code's names: `Bash` (`command`), `Write` or `Edit` (`file_path`), `mcp__<server>__<tool>`, `WebSearch` (`query`) and `Agent` (`prompt`). Their outcomes become `tool_result` blocks.
- A command the sandbox declined is listed in `permission_denials`.
- `usage` comes from Codex's token counts: `input_tokens` without the cached part, the cached part as `cache_read_input_tokens`.
- `modelUsage` has the served model with its context window. `total_cost_usd` and `costUSD` are 0.
- A failure is a result of subtype `error_during_execution` or `error_max_turns`, with `errors`, and the exit code is 1.

### 6.5 The terminal interface: what Claude Code does, and how `ling` does the same

By the owner's decision D2 the mode's interface does what Claude Code's does. This section is the black-box study behind that sentence (§2.1 items 1 and 5): what the installed CLI writes to a terminal, summarised from captures, plus what its public documentation says about what was not observed. It records **mechanics only** (where text goes, how the screen is redrawn, which key does what); the look (colours, logo, spinner words, glyphs, wording) is not recorded and not copied. Every row says whether it was **observed** in a capture or is **documented** only.

**Method and provenance.** Claude Code **2.1.304**, 2026-10-20, run from `~/.nvm/…/bin/claude` in throwaway folders under the session's scratch directory, with a scrubbed environment (`env -i` with only `HOME`, `USER`, `SHELL`, `LANG=C.UTF-8`, `TERM=xterm-256color`, `PATH`), inside **tmux 3.4** (`-f /dev/null`, 120×40) with `pipe-pane` recording the raw bytes and `capture-pane` the rendered screen. Four runs: (A) a fresh, signed-out configuration folder (`CLAUDE_CONFIG_DIR` in the scratch directory), which stops at the theme and sign-in screens; (B1) the existing configuration with `--safe-mode` (no hooks, plugins, MCP servers or custom key bindings) and `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1`, the **classic** renderer, after accepting the folder-trust dialog for the throwaway folder; (B2) the same session resumed with `--continue` and `CLAUDE_CODE_NO_FLICKER=1`, the **fullscreen** renderer; (P) a Python `pty` run of the signed-out screens that answered the terminal probes as a capable terminal would (synchronized output supported, kitty keyboard flags 0, an XTVERSION reply), because tmux 3.4 does not report synchronized output. Five trivial prompts in total (a greeting, writing a five-line file, editing one line of it, `seq 1 120` through the shell tool, a second greeting); the project's state was removed afterwards with `claude purge`. No setting was changed and no package file was opened. `ling` 1.6.4 was captured the same way: its default start (L1), `--no-alt-screen` (L2) and `/raw` (L3); the model gate refused its requests during a benchmark night, which does not affect rendering, so no `ling` tool call was seen.

#### 6.5.1 Two renderers, and which one starts

Claude Code has **two renderers**, both observed:

- **Classic** (B1): **inline, on the normal screen**. No `ESC[?1049h` in 57.6 KB of output, and no `ESC[2J`, `ESC[3J` or `ESC c` either: it never clears the screen or the scrollback. Finished output (the user's prompt, the reply, each tool call and its result) is written once and then scrolls up into the terminal's own scrollback (tmux history grew from 0 to 27 rows as the session filled the screen); a **live region** at the bottom (a spinner line with a verb, the elapsed time and a token count while a turn runs, then the input box between two horizontal rules, then a footer with the permission mode and hints) is redrawn in place. On exit the live region is left on screen, the cursor moved below it, the terminal modes reset, and a one-line hint printed with the command that resumes the session.
- **Fullscreen** (B2): **the alternate screen**. `ESC[?1049h` and `ESC[2J` after start-up, mouse reporting on (`?1000h`, `?1002h`, `?1003h`, `?1006h`: any-motion tracking with SGR coordinates), absolute cursor positioning. The conversation scrolls *inside* the application (`PgUp`, a "jump to bottom" control while scrolled up, a dim header row naming the prompt that scrolled above the view), the input box stays fixed at the bottom, and nothing reaches the terminal's scrollback during the session (history stayed at 0). On exit `ESC[?1049l` restores the screen as it was before the session, and the resume hint is printed below it.

Which one starts is documented, not observed: the classic renderer when `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1` or `CLAUDE_CODE_NO_FLICKER=0`; fullscreen when `CLAUDE_CODE_NO_FLICKER=1`; otherwise the saved `tui` setting (`"default"` or `"fullscreen"`, written by `/tui default|fullscreen`, which relaunches into the other renderer with the conversation intact); otherwise, for a session that fetches no feature flags (the case of a machine with no network) and whose machine first ran 2.1.239 or later, **fullscreen**; long-time accounts that fetch flags start in classic and are offered the switch. The sign-in and theme screens (run A) render inline before either renderer starts.

**`ling` today** has the same two shapes, under Codex's names: `tui.alternate_screen = "auto"|"always"|"never"` (and `--no-alt-screen`) decides whether the alternate screen is used at all, and `tui.fullscreen_transcript` (default `true`) whether the session **owns the transcript** in it (scrolling inside the application) or leaves it to the terminal's scrollback; Codex's own `/tui` picker chooses between "Scrollback" and "Fullscreen" for the next launch. Its default start (L1) is the fullscreen shape: `?1049h`, absolute positioning, mouse reporting on while the screen is owned (`?1000h`/`?1002h`/`?1003h`/`?1006h`, seen in L3). `--no-alt-screen` (L2) is the classic shape: inline, history in the terminal's scrollback. So **the renderer split needs no new code**, only a mapping of Claude Code's switches onto Codex's keys (§6.5.4).

#### 6.5.2 How each draws: the escape-sequence evidence, summarised

| | Claude Code classic (B1) | Claude Code fullscreen (B2) | `ling` fullscreen (L1) | `ling --no-alt-screen` (L2) |
|---|---|---|---|---|
| Screen | normal screen, never cleared | `?1049h` + `2J`; `?1049l` at exit | `?1049h`; `?1049l` at exit | normal screen |
| Cursor motion | **relative only**: `CUU`/`CUD`/`CUF`/`CUB`, `CR`, `EL` (`K`, `2K`); it never learns its absolute row | absolute `CUP` (`ESC[row;colH`) | absolute `CUP` (ratatui) | asks the row once with `CSI 6n`, then absolute `CUP` inside its viewport |
| A frame | hide cursor, move up to the changed rows, rewrite **only the changed cells**, erase to end of line, move back to the input caret, show cursor (about 200 frames in the session) | same cell diff in absolute coordinates, cursor parked at the input caret | ratatui's cell diff | ratatui's cell diff in the viewport |
| New history | printed above the live region, scrolls into the terminal's scrollback | kept in the application | kept in the application | inserted above the viewport with a scroll region (`DECSTBM`) and reverse index (`ESC M`), so it scrolls into the terminal's scrollback |
| Live region grows past the screen (a permission dialog with a diff), then shrinks | cursor home, erase every visible row (`2K` + `CUD` per row), repaint the visible screen; scrollback untouched | redrawn in place | redrawn in place | the viewport is resized |
| Synchronized output (`?2026h`/`?2026l` around each frame) | **only after the terminal says it supports it**: probed with `DECRQM ?2026`; none under tmux 3.4, every frame wrapped in run P | same | **always**, without waiting for a reply (20 frames in L1, 95 in L2) | always |
| Kitty keyboard protocol | queried (`CSI ?u`); pushed with flags 5 (`CSI >5u`: disambiguate + report alternate keys) only when the terminal answers (run P); popped with `CSI <u` at exit | same | pushed with flags 7 (`CSI >7u`) after the query; popped at exit | same |
| Other modes set at start | bracketed paste `?2004h`, focus events `?1004h`, colour-palette change notifications `?2031h` | same, plus mouse | bracketed paste, focus events, cursor style `CSI 0 q`, `?1007` (alternate scroll) toggled | bracketed paste, focus events |
| Probes at start | OSC 11 (background colour), DA1, XTVERSION (`CSI >0q`), kitty keyboard, `DECRQM ?2026`, `DECRQM ?1016` (pixel mouse), `CSI 16t` (cell size), an APC query, and a private OSC 7501 query | same | OSC 10/11, DA1, kitty keyboard, `CSI 6n` | same |
| Window title | OSC 0 set to a session title, animated while a turn runs, cleared at exit | same | OSC 0 set to the folder name, animated while a turn runs, cleared at exit | same |
| Exit | resets focus events, `CSI >4m` (modifyOtherKeys, though it was never seen set), `?2031`, `?2004`, every mouse mode (defensively, even in classic), shows the cursor, prints the resume hint | same, after `?1049l` | resets mouse, kitty, alternate scroll, bracketed paste, focus; `?1049l`; prints the resume hint | erases the input box; the history stays in the scrollback |

**Resize** (SIGWINCH through `tmux resize-window`, 120×40 to 90×30): classic Claude Code moves the cursor home, erases each visible row and repaints the tail of the conversation at the new width; **the scrollback is left as it was** (older rows keep their old wrapping). Fullscreen Claude Code erases every row and repaints the whole frame. `ling` fullscreen repaints its frame; **`ling --no-alt-screen` writes `ESC[2J ESC[3J`, clearing the terminal's scrollback, and replays the transcript rewrapped** (capped by `tui.terminal_resize_reflow_max_rows`). That is the one redraw behaviour where the two differ in a way a user sees: after a resize, Claude Code's classic scrollback still holds everything, and `ling`'s is rebuilt.

#### 6.5.3 What the user sees and does

| Behaviour | Claude Code (2.1.304) | `ling` today | Source |
|---|---|---|---|
| Live region | spinner line (verb, seconds, tokens), input box between two rules, footer: permission mode, the interrupt key while running, background-agent count, effort; a greyed **prompt suggestion** in the empty input after a turn | status line above the composer, composer, footer with model, folder and hints | observed |
| Tool call in the history | one line naming the tool and its argument (`Write(notes.txt)`, `Bash(seq 1 120)`), its result indented below; long output **collapsed to its first 3 lines and one row counting the hidden lines and naming the key that expands them** in classic, to **one summary row counting the calls** in fullscreen | an exec cell with the command and the output's head and tail, at most 5 lines (`TOOL_CALL_MAX_LINES`) | observed (Claude Code); read at the pin (`ling`) |
| Diffs | a write shows the new file's numbered lines; an edit shows numbered rows with removed and added rows on distinct backgrounds, the changed words highlighted, and a row counting added and removed lines | Codex's diff rendering of `apply_patch` | observed; `ling` not captured (no model) |
| Permission prompt | replaces the input box inside the live region: a rule, the kind of action, the file, a framed preview (the whole new file or the diff), a question, three numbered choices (yes; yes and switch to accept-edits for the session; no), and a hint that `Esc` cancels and `Tab` adds a comment; `Enter` or the digit answers | Codex's approval overlay (`[tui.keymap.approval]`: approve, approve for session, approve for prefix, deny, cancel) | observed; `ling` not captured |
| Multi-line input | `\` then `Enter`, and `Ctrl+J`, insert a newline; the box grows; `Ctrl+G` opens `$EDITOR` (hint shown once the input has text) | `Ctrl+J` inserts a newline; `Ctrl+G` opens the editor; a trailing `\` is not special | observed (Claude Code); read at the pin (`ling`) |
| Paste | bracketed; a multi-line paste (12 lines) becomes a numbered placeholder with its line count, a long single line (1,500 characters) a numbered placeholder; pasting again expands it | bracketed; pastes over 1,000 characters become "[Pasted Content N chars]" | observed; read at the pin |
| `Ctrl+C` | with a draft: clears it; on an empty idle prompt: a hint to press it again, and a second press within about a second exits | with a draft: clears it and shows the quit hint; on an empty prompt: **quits at once** (`DOUBLE_PRESS_QUIT_SHORTCUT_ENABLED = false`) | observed both |
| `Ctrl+D` | empty prompt: the same hint, a second press exits | quits | observed (Claude Code); read at the pin |
| `Esc` | while running: interrupts (the footer says so; the effect is documented); with a draft: a hint, and the second press clears it | interrupts (`interrupt_turn`) | observed; documented |
| `Esc Esc` on an empty prompt | opens the **rewind** list in the live region: every prompt with the file changes it caused (or none), and the current point; restores code, conversation or both | edits the last message (Codex's backtrack): conversation only | observed both |
| `Shift+Tab` | cycles the permission mode: auto → manual → accept edits → plan → auto (from the owner's auto default) | "Change mode" (collaboration modes); the permission-mode action exists unbound | observed both |
| `?` on an empty prompt | shortcut panel in the live region | shortcut overlay | observed both |
| `/` | command menu in the live region, filtered as you type | command popup | observed both |
| `Ctrl+O` | classic: prints the **detailed transcript inline** (timestamps, model id, every tool output expanded) below the conversation, into the scrollback (history grew from 27 to 149 rows), no screen switch; `Ctrl+E` shows all; `Ctrl+O` again returns to the prompt. Fullscreen: transcript mode **inside** the alternate screen, with `less`-style keys | `Ctrl+O` copies the last answer; `Ctrl+T` opens Codex's transcript overlay | observed both |
| Transcript `[` (fullscreen) | erases the screen, turns the mouse off, **leaves the alternate screen** and writes the whole conversation, expanded, into the normal screen and its scrollback (history 0 to 152 rows); `q` re-enters (`?1049h` + `2J`) and repaints | `/raw` (`Alt+R`) switches to a copy-friendly rendering but **stays** in the alternate screen with the mouse captured | observed both |
| Transcript `v` | writes the conversation to a temporary file and opens `$VISUAL`/`$EDITOR` | `/export` writes Markdown | documented |
| Scrolling (fullscreen) | `PgUp`/`PgDn` half a screen, `Ctrl+Home`/`Ctrl+End`, the wheel; a "jump to bottom" control while auto-follow is paused | `PgUp`/`PgDn`, `Ctrl+Home`/`Ctrl+End` in the transcript | observed `PgUp` and the control; the rest documented |
| Mouse (fullscreen) | click to place the cursor, click a collapsed tool result to expand it, drag to select (copied on release), `Ctrl`+click opens links; `CLAUDE_CODE_DISABLE_MOUSE=1` turns capture off | mouse captured in the owned screen; selection with `Ctrl+Space` | modes observed; behaviour documented |
| `Ctrl+L` | redraws, keeping the conversation and the input | `clear_terminal` | documented; to check in Phase 0 |
| Folder trust | a dialog before the first session in a folder: exit, or trust it | a dialog: trust and continue, or quit | observed both |

#### 6.5.4 In the mode, with no patch: configuration the launcher writes

**Built** (`ling-rs/src/compat_tui.rs`). Between Claude Code's variables and its settings files' `tui` key, Mightling's own `tui.alternate_screen` or `tui.fullscreen_transcript` in `$CODEX_HOME/config.toml` decides when set, which Codex's `/tui` picker writes. That keeps the picker working in the mode. All of this is launcher code, as `-c` overrides on the command line of a session started in the mode (never written into `~/.mightling/config.toml`, so a plain `ling` start is unchanged; §13):

1. **The renderer**, from Claude Code's own switches in Claude Code's documented order: `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1` or `CLAUDE_CODE_NO_FLICKER=0` → classic, i.e. `-c tui.alternate_screen="never"` (the inline viewport, history in the terminal's scrollback, L2's shape); `CLAUDE_CODE_NO_FLICKER=1` → fullscreen, `-c tui.alternate_screen="auto" -c tui.fullscreen_transcript=true` (L1's shape); otherwise the `tui` key of the Claude Code settings files the mode already reads (§9.2: `"default"` → classic, `"fullscreen"` → fullscreen); otherwise **classic** (D5). `--no-alt-screen` on the command line still wins, as in Codex. The `/tui` picker keeps working and chooses for the next launch, as in both programs.
2. **The key map**, as `-c tui.keymap.<context>.<action>=…` overrides of the pinned TUI's `[tui.keymap]` (`config/src/tui_keymap.rs`), checked in the built `ling` in Phase 0 (§16.2): `global.open_transcript = "ctrl-o"` and `global.copy = []` (the answer is copied with `/copy`, as in Claude Code), so `Ctrl+O` opens the transcript and, with `pager.close_transcript = ["ctrl-t", "ctrl-o"]`, closes it again; the pager adds `g` to `jump_top`, `shift-g` to `jump_bottom` and `b` to `page_up`, each list written with Codex's defaults kept. Most of Claude Code's pager keys are already Codex's defaults (`j`/`k`, `ctrl-d`/`ctrl-u`, `space`, `/`, and `q` through `pager.close`); `close_transcript` can take neither `esc` (reserved for the transcript's backtrack) nor `q` (already `close`), and the pinned TUI refuses to start on either. **`Shift+Tab` cannot be bound by configuration**: it is the fixed binding `fixed.cycle_collaboration_mode`, so `chat.next_permission_mode = "shift-tab"` stops the TUI with an "Ambiguous `tui.keymap.main` bindings" error (§16.1); D6 needs a hook (§6.5.6 item 5). Bindings both programs already share stay as they are: `Ctrl+J`, `Shift+Enter` (both bound to `insert_newline` by default), `Ctrl+G`, `Ctrl+R`, `Ctrl+V`, `Ctrl+Z`, `Esc` to interrupt, `?`, `/`, `@`, `!`, `PgUp`/`PgDn`, `Ctrl+Home`/`Ctrl+End`. A user's own `~/.claude/keybindings.json` is **not** translated in Phase 1 (its action names are Claude Code's; a later translation table can map the ones with a Codex action); the user's own `[tui.keymap]` in `~/.mightling/config.toml` is applied after the mode's, so it wins.
3. **Mouse capture**: the pinned TUI has no switch for it (§16.2): it captures the mouse while it owns the alternate screen and in the transcript overlay, never in the classic renderer's main view, and turns it off by itself under tmux when tmux's mouse option is off. `CLAUDE_CODE_DISABLE_MOUSE` is therefore reported by `ling compat doctor` as not honoured.
4. **The resize reflow cap**: `tui.terminal_resize_reflow_max_rows` stays at Codex's terminal-specific default; it bounds the replay but does not stop the scrollback being cleared (§6.5.6 item 8).

#### 6.5.5 Command names

Claude Code's built-in commands (the public commands page, read on 2026-10-20; its bundled skills and workflows such as `/simplify` or `/loop` are skills, not commands, and come in as skills if the user has them on disk) against the pinned Codex's (`tui/src/slash_command.rs`):

| Group | Claude Code names | In the mode |
|---|---|---|
| Same name and job in `ling` | `/agents`, `/btw`, `/cd`, `/clear`, `/compact`, `/copy`, `/diff`, `/exit`, `/export`, `/fork`, `/goal`, `/hooks`, `/ide`, `/init`, `/mcp`, `/model`, `/permissions`, `/plan`, `/recap`, `/rename`, `/resume`, `/skills`, `/status`, `/statusline`, `/theme`, `/tui`, `/usage` | as they are. `/init` writes `AGENTS.md` (which of the two files it writes in the mode is §14 Q10); `/tui` is Codex's picker and takes no argument; `/review` exists under the same name but reviews differently, and is listed as such by `ling compat doctor` |
| Another name in `ling` | `/branch` → `/fork`, `/keybindings` → `/keymap`, `/tasks` → `/ps`, `/plugin` → `/plugins`, `/cost` and `/stats` → `/usage`, `/checkpoint` and `/undo` (aliases of `/rewind`) → see below | served by the slash-command fallback (§6.5.6 item 1), which runs the Codex command |
| Missing, written in `ling-rs/` | `/memory` (list and open the memory files, §9.1), `/rewind` (Codex's backtrack list, conversation only until §14 Q7), `/context` (the session's token use from the local server, as `/status` prints it), `/config` (the mode's settings and where each came from, `ling compat doctor`'s report), `/help`, `/add-dir` (a writable root for the session), `/doctor` (the same report), `/reload-skills`, `/effort` (the reasoning effort, as `Alt+,`/`Alt+.` change it) | served by the slash-command fallback; Phase 3 |
| Not planned | everything tied to Anthropic's services or accounts (`/login`, `/logout`, `/upgrade`, `/usage-credits`, `/passes`, `/privacy-settings`, `/rate-limit-options`, `/mobile`, `/desktop`, `/remote-control`, `/remote-env`, `/teleport`, `/web-setup`, `/install-github-app`, `/install-slack-app`, `/chrome`, `/schedule`, `/ultrareview`, `/autofix-pr`, `/artifacts`, `/advisor`, `/fast`, `/insights`, `/release-notes`, `/feedback`, `/bug`, `/radio`, `/stickers`, `/powerup`, `/heapdump`); and `/focus`, `/output-style`, `/color`, `/scroll-speed`, `/terminal-setup`, `/sandbox` (Mightling's sandbox is always on), `/background`, `/voice` (hidden in `ling` by patch `0010`), `/vim` (removed from Claude Code; `ling`'s stays) | the fallback answers with one line saying the command is not part of the mode |

#### 6.5.6 What needs a patch, and what stays different

The patch series is at **44,952 of 45,000 bytes** (44,835 when this section was written; 117 bytes went to the exit summary's `ling resume` line on 2026-10-20), and the owner raised the cap to **48,500 bytes** for the four hooks below and then to **49,000** for the fifth (D4, 2026-10-20); the number in `test_the_patches_stay_small` changes with their implementation, with a comment in the test's style naming each hook and its bytes, not before. Each is the smallest call into `ling-rs/`, in the style of `0017`/`0018`, with the logic in the launcher crate:

1. **A slash-command fallback** (about 1.2 KB): where the composer rejects an unknown `/name`, call `ling_launcher::compat::slash(name, args, thread_id)`, which returns either lines to show or the name of a Codex command to run instead. One hook serves every row of §6.5.5's second and third groups from `ling-rs/`, so later commands cost no patch bytes; outside the mode it returns nothing and Codex's message stands.
2. **Double-press exit** (about 0.4 KB): `DOUBLE_PRESS_QUIT_SHORTCUT_ENABLED` read from the launcher instead of a constant, so `Ctrl+C` and `Ctrl+D` on an empty prompt ask for a second press in the mode.
3. **`\` + `Enter`** (about 0.6 KB): in the mode, a draft ending in a backslash gets a newline instead of being sent.
4. **Transcript to scrollback and to the editor** (about 1.3 KB): two keys in the transcript overlay, `[` and `v`, calling a launcher function that leaves the alternate screen with the TUI's own `with_restored`, writes the transcript (the `/export` text) to the terminal or to a temporary file opened in `$VISUAL`/`$EDITOR`, and re-enters on a key, as run B2 showed Claude Code doing.

5. **`Shift+Tab` on the approval modes** (D6; found in Phase 0, §16.1, not in the owner's budget): `Shift+Tab` is a fixed binding of Codex's collaboration-mode cycle, so the mode's binding needs one allowed overlap in the keymap's reserved-key check (`tui/src/keymap.rs`, the `main` context's `allowed_overlaps`); the permission shortcut is tried before the fixed binding, so with the overlap allowed it takes the key. Estimated 250–350 bytes.

About **3.5 KB** for items 1–4 and 250–350 bytes for item 5 (44,952 + about 3,850 = about 48,800 bytes), inside the **49,000** the owner set for the five together (D4, 2026-10-20).

**Built** (2026-10-20) as `codex-patches/0029-compat-hooks.patch`, 3,464 bytes. The series is 50,642 bytes, under the 55,000 the owner set the same day for 1.7.1 and these hooks. Each hook is one call into `ling-rs/src/compat_keys.rs` that answers "Codex's own behaviour" outside the mode:

1. `Enter` in the composer, ahead of the slash popup: a draft ending in `\` gets a newline. A name in §6.5.5's second group runs Codex's command (`/branch` → `/fork`, `/tasks` → `/ps`, `/context` → `/status`, …). The third group is answered with lines (`/help`, `/memory` lists the memory files, `/config` and `/doctor` print `ling compat doctor`'s report, `/rewind` points at `Esc Esc`, `/add-dir` at `--add-dir`, `/effort` at `Alt+,`/`Alt+.`). The fourth gets one line saying the command is not part of the mode.
2. `Ctrl+C` and `Ctrl+D` on an empty prompt ask for a second press, with Codex's own hint. A draft is still cleared by the first `Ctrl+C`.
3. `[` and `v` in the transcript overlay close it. `[` adds the whole conversation, rendered from the session's rollout file, to the session's history. In the classic renderer that history is the terminal's scrollback, which gives Claude Code's classic `Ctrl+O`. Text written to the terminal directly was tried first and lost, because Codex rebuilds the inline scrollback from its history when it redraws. `v` opens the conversation in `$VISUAL`/`$EDITOR` through `Tui::with_restored`. In a fullscreen session that owns its transcript there is no overlay to take the keys; that case is left as a difference.
4. `Shift+Tab` (`BackTab`) is the approval-mode shortcut in the mode. A keymap binding cannot do it: a spec can only name `shift-tab`, which never matches `BackTab`, and the key is reserved anyway. The session also gets `features.guardian_approval=false`, because Codex's "Approve for me" asks a review model ling does not serve; so the cycle is Read Only and the default mode. Its test, `compat_shift_tab_cycles_approval_modes_and_never_full_access` (`codex-tests/patches/0004`), cycles from Full Access and asserts no step is Full Access and the plan mode is untouched.

Not planned, each a visible difference stated in `ling compat doctor` and the documentation:

6. **The look**: line glyphs, the spinner, the 3-line collapse with its "+N lines" row against Codex's 5-line exec cell, the one-row tool summaries of fullscreen, the paste chips' wording. These are presentation, and copying them is what §2.1 forbids.
7. **`Ctrl+L` in the classic renderer** (owner, 2026-10-20: a stated difference, no hook): Codex's `clear_terminal` writes `ESC[2J ESC[3J` and repaints only the session header, so the scrollback that holds the conversation is erased; Claude Code's `Ctrl+L` redraws and keeps it (§16.2).
8. **Classic resize keeping the scrollback**: Codex's inline mode rebuilds the scrollback after a resize (`2J`/`3J` and a replay); Claude Code's classic renderer repaints only the visible rows. Changing it means a different history model in Codex's `insert_history`, far beyond a hook.
9. **Mouse behaviour beyond wheel scrolling** (click to expand a tool result, drag-to-select with copy on release, click to place the cursor), the prompt suggestion, the rewind of files (§14 Q7), and the synchronized-output probe (`ling` always sends `?2026h`, which terminals that do not know it ignore; no user-visible difference was found).

#### 6.5.7 Checks for Phase 0

- The keymap overrides of §6.5.4 load in the built `ling` without a conflict error and do what the table says (a pseudo-terminal run like this study's, marked `installed_binary`).
- Whether the pinned TUI's approval-mode cycle already skips a preset that the config's permission constraint refuses (patch `0023` refuses Full Access at `/airgapped on`). If it does not, D6's skip is launcher configuration where the cycle's list is configurable, and otherwise a hook that must fit in the 165 bytes left under 48,500 or go back to the owner. **Checked (§16.1):** Full Access is never in the cycle, but `Shift+Tab` is a fixed binding, so the hook is needed and goes back to the owner.
- Whether the pinned TUI has a switch for mouse capture (§6.5.4 item 3), and what its `Ctrl+L` does against Claude Code's redraw.
- `ling`'s own tool-call and approval rendering against §6.5.3's rows, once the model gate lets a request through (not captured on 2026-10-20).
- Shift+Enter in a kitty-protocol terminal: Claude Code documents it as a newline; `ling` pushes the protocol with flags 7, so it can tell the keys apart, but its binding was not checked.

---

## 7. The model side

### 7.1 What the server can do

- **Tool calls on SGLang** go through the `qwen3_coder` tool parser with the `qwen3` reasoning parser (registry entry `qwen3.8-27b-nvfp4-dflash2`, [AGENTS §7](./DREAMFERENCE_AGENTS.md), [MODELS](./DREAMFERENCE_MODELS.md)). The chat template is patched at launch so Codex's reasoning efforts and a second system message are accepted (`ChatTemplatePatcher`).
- **Only plain function tools reach the model.** SGLang and vLLM silently drop Codex's `custom` (freeform) and `namespace` tool types. That is why patch `0020` flattens MCP tools into functions, why `apply_patch` is offered freeform only on ling-engine, and why Code Mode's `exec` is translated into a function on SGLang (`ling-rs/tools/src/custom.rs`). Any tool the mode adds is a plain JSON-schema function, served over MCP so that `0020` presents it, and must stay within the parser's handling (no deeply nested `anyOf`, string-typed free text).
- **ling-engine** (`owned_by: ling-engine` on `/v1/models`) runs custom tools; `ServedModel::runs_custom_tools` stays the one gate, and the compatibility layer does not add a second.

### 7.2 The tool roster

Rosters chosen per session by `DREAMFERENCE_MIGHTLING_CLAUDE_TOOLS=codex|discrete|discrete-search` (default `codex` until §11 decides):

| Claude Code's tool (documented name, §3.1) | `codex` roster (today's `ling`) | `discrete` roster |
|---|---|---|
| Bash | Codex's shell (`exec_command`, sandboxed) | the same shell |
| Read | `cat`/`sed -n` in the shell | `Read` (path, offset, limit; numbered lines), from `ling compat tools` |
| Write | `apply_patch` (add file) | `Write` (path, content) |
| Edit, multiple edits | `apply_patch` | `Edit` (path, old text, new text, replace all) |
| Grep | `rg` in the shell | the shell by default, as Claude Code itself does on Linux (§3.1); `Grep` (pattern, path, glob, output mode, running `rg`) only in a `discrete-search` variant |
| Glob | `rg --files`/`find` | likewise; `Glob` (pattern, path) only in `discrete-search` |
| WebFetch | `ling-fetch` in the shell | `WebFetch`, calling `ling-fetch` |
| WebSearch | `ling-search` in the shell | `WebSearch`, calling `ling-search` |
| Task tracking (`TaskCreate`…, older `TodoWrite`) | Codex's `update_plan` | `update_plan` |
| Agent (subagents) | Codex's `multi_agents` tools | the same |
| AskUserQuestion | Codex's `request_user_input` | the same |
| NotebookEdit | `apply_patch` on the JSON | P2, not in the first roster |

Every description is written fresh (§2.1). Each discrete tool is a thin, typed front for something `ling` already does, so the arm measures the **shape** of the tools and not new capability.

**Where the discrete tools run, and the sandbox.** MCP servers started by Codex are not run inside the command sandbox (as read in the pinned source; Phase 0 confirms it on this host), so `ling compat tools` confines itself: writes only under the session's writable roots (the workspace, `--add-dir`), reads refused for paths a deny rule names (§9.3), no network of its own (`WebFetch`/`WebSearch` run `ling-fetch`/`ling-search`, which already follow `/airgapped`, [AIRGAPPED §4](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md)), and the server process itself started under bwrap with the network unshared when `/airgapped on`. Edits through `Edit`/`Write` must still show as diffs in the TUI and the desktop app: Phase 2 checks whether Codex's turn-diff tracker sees them (it tracks `apply_patch`); if it does not, `Edit` is implemented as a translation to `apply_patch` inside the launcher's request hook (the way `custom.rs` translates `exec`), which keeps Codex's approval and diff paths. This choice is Phase 2's first measurement (§12).

### 7.3 Context limits

- The model's window is **262,144 tokens** (`max_model_len`), but SGLang's KV pool is **about 157K tokens shared by every stream** ([COMPACTION §1](./DREAMFERENCE_MIGHTLING_COMPACTION.md)). The launcher already sets the interactive compaction limit to 60% of the pool (~94K) and turns on turn-end compaction at 20% of the usable window with an 800-word summary cap (1.6.3). The mode inherits all of it unchanged.
- **Memory files cost context on every request.** Codex caps project documents at `project_doc_max_bytes` (32 KiB); the compatibility layer applies one budget to `AGENTS.md` plus the `CLAUDE.md` hierarchy together (default the same 32 KiB, the larger files truncated first with a notice), because a 27B model's useful context is smaller than its window.
- **The discrete roster adds schema tokens.** Five tool schemas (seven with `discrete-search`) are measured once (Phase 2) and counted in the SWE-bench report's token column; [CONTEXT_BUDGET](./DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md) found the index's tool schemas were 23% of unattended context, so this is not free.
- **`/compact <instructions>`** (Claude Code's focused compaction) maps to Codex's `/compact` with the text passed to the compaction prompt; whether the pinned Codex accepts an argument is checked in Phase 1, and if not, the launcher's turn-end compaction hook carries the focus ([BACKLOG](./DREAMFERENCE_BACKLOG.md) item 12 already proposes a context meter with `/compact <focus>`).
- **Thinking**: Claude Code's "think" keywords have no meaning for a local model with one reasoning setting; they are passed through as text.

---

## 8. Sandbox, air gap and egress

### 8.1 Unchanged guarantees

Because the mode is the same binary:

- Commands run in Codex's sandbox (bwrap on Linux, which needs the AppArmor profile `ling-admin host setup` installs; Seatbelt on macOS; the elevated sandbox on Windows).
- `/airgapped on` gives every sandboxed command an empty network namespace (patch `0019`) and is refused together with Full Access; it applies to compatibility-mode sessions with no new code. `bypassPermissions` maps to Full Access and is therefore refused at `on` (§9.3).
- The vendor channels stay closed (patches `0013`, `0015`, `0016`, the forced `chatgpt_base_url`), and nothing in the layer adds a channel to Anthropic: there is no Anthropic endpoint, model id, key helper or sign-in anywhere in the code, and a test greps the compatibility crate for Anthropic hosts (§13).

### 8.2 The egress audit

`ling-admin audit egress` gains `--compat claude`: the same strace method ([EGRESS §3.1](./DREAMFERENCE_MIGHTLING_EGRESS.md)) around `ling --compat claude -p "<prompt>" --output-format json` in a throwaway repository that contains a `CLAUDE.md`, a `.claude/settings.json` with one hook and one permission rule, a `.claude/commands/` file and a `.claude/agents/` file, so every translation path runs inside the trace. With `--tools discrete` it also starts `ling compat tools`. The verdict is the existing one (only allowlisted loopback ports, no DNS query, no networked git). `codex build` runs the `--compat claude` form after `--tui`, and the JSON record is `<timestamp>-claude.json`.

### 8.3 `/import` and plugins

`/import` is in `ling` today and unaudited. Its plugin step reads Claude Code's known-marketplaces file and can add a marketplace whose source is a GitHub repository (`OFFICIAL_MARKETPLACE_SOURCE = "anthropics/claude-plugins-official"` in `source_cla.rs`). Phase 0 traces `/import` on a home with a Claude Code setup and either (i) confirms it fetches nothing, or (ii) adds a one-line hook that skips non-local marketplace sources (Codex already has `is_local_marketplace_source`), counted against the cap with the owner's approval. The compatibility layer itself never fetches a plugin (§1.2).

### 8.4 Hooks are code from the repository

A repository's `.claude/settings.json` can name hook commands, which run on the user's machine. Claude Code's own documentation treats them as the user's responsibility; Mightling is stricter:

- translated hooks from a **repository** are registered only after the user trusts the project (Codex's trust prompt) and then go through Codex's hook trust (`/hooks`), by hash, so a changed command needs approving again;
- hooks from `~/.claude/settings.json` are the user's own and are trusted like Codex's own `hooks.json` in `$CODEX_HOME`;
- hooks run outside the command sandbox, as Codex's own hooks do (Phase 0 confirms this at the pin); at `/airgapped on` the start-up line says so, and `ling compat doctor` lists every hook command.

---

## 9. Configuration and memory files

### 9.1 Memory: `CLAUDE.md` read live, beside `AGENTS.md`

**Yes, the mode reads `CLAUDE.md`**, with the hierarchy the public memory page documents (§3.5): managed (`/etc/claude-code/CLAUDE.md`, if present), the user's `~/.claude/CLAUDE.md`, the project's `CLAUDE.md` (or `.claude/CLAUDE.md`) in the working directory and every ancestor, root first, `CLAUDE.local.md`, files in subfolders when the agent first works there, and `@path` imports relative to the importing file, at most four hops, ignored inside code.

**How, without a patch:**

- Codex keeps reading `AGENTS.md` itself (root to working directory, plus `$CODEX_HOME/AGENTS.md`). That stays Mightling's canonical project document.
- The launcher composes the `CLAUDE.md` hierarchy, with imports expanded, and hands it to the session through a built-in **`SessionStart` hook** (`ling compat memory`, a subcommand of the same binary) whose `additionalContext` carries the text. Hooks support `additionalContext` at the pinned tag (§4.2), the hook is registered by the launcher with its trust entry the way `SweBenchHooks` registers the benchmark's, and no patch is needed.
- **Deduplication:** files are keyed by canonical path. A `CLAUDE.md` that only imports `AGENTS.md` (this repository's case) adds nothing; an `AGENTS.md` that imports `CLAUDE.md` is handled the same way; two different files in one folder are both read, `AGENTS.md` first.
- **A deliberate difference from Claude Code**, which reads `AGENTS.md` only when no `CLAUDE.md` exists (§3.5): in Mightling `AGENTS.md` is the canonical project document and is always read, because `ling`, Night Shift and SWE-bench depend on it. A repository with both is read with both; §14 Q10 asks whether a setting should restore Claude Code's rule.
- **Subfolder files** are read when the agent first touches a path under them: a `PostToolUse` hook on reads and edits notices the new folder and injects its `CLAUDE.md` as context once per session.
- **Budget:** one budget for both families (§7.3).
- **Outside the profile**, plain `ling` gains one small, separate option: `mightling_claude_md = true` adds `CLAUDE.md` to `project_doc_fallback_filenames`, so a repository with only a `CLAUDE.md` gives `ling` its instructions (a folder with an `AGENTS.md` keeps reading only that, which is Codex's rule). It is off by default and costs one `toml_edit` line in the launcher.

**Built (2026-10-20)** in `ling-rs/src/compat_memory.rs`.
- **Files and order:** as Claude Code's memory page gives them: the managed file, `~/.claude/CLAUDE.md`, then each folder from the filesystem's root down to the working folder with its `CLAUDE.md`, `.claude/CLAUDE.md` and `CLAUDE.local.md`.
- **Imports:** `@path` imports are relative to their file. `~/` is the home folder, four hops at most. An import in a code span or fence, a quoted path, and a path that does not exist are left as text. `\ ` is a space.
- **Comments:** block-level HTML comments are dropped.
- **Project imports:** an import in a project's file that leads outside the project is not followed and is reported. Claude Code asks first; there is no dialog here. A user-scope file's imports are followed.
- **Deduplication:** files are deduplicated against the `AGENTS.md` files Codex reads and against each other, by canonical path. A line that is only an import is replaced by what it imports, so this repository's `CLAUDE.md` (`@AGENTS.md`) adds nothing.
- **Budget:** the shared budget is Codex's 32 KiB less the `AGENTS.md` bytes; the largest files are cut first, with a notice.
- **Delivery:** the hook is `ling compat memory`, registered with `-c` and its trust entry for the session's process only. It matches `startup|clear|compact` and has `additionalContextLimit = 0`, so Codex never moves the memory to a file. It is skipped under print mode's `--bare`.
- **Plain `ling`:** `mightling_claude_md = true` (or `DREAMFERENCE_MIGHTLING_CLAUDE_MD=on`) adds `CLAUDE.md` to Codex's `project_doc_fallback_filenames` for the session, keeping the names the user set there.
- **Reporting:** `ling compat doctor` lists the files a session in the folder gets, and what was left out.
- **Skills:** `~/.claude/skills` and a trusted repository's `.claude/skills` were already linked into every session (SKILLS §15) and need nothing from the mode.
- **Not built yet:** the subfolder files read on first touch and `.claude/rules/`.

**`/memory`** (list and open the memory files) is Phase 3: it opens the chosen file in `$EDITOR` through the external-editor path patch `0025` already uses for `/node`, which needs a one-line hook counted against the cap. A `#` quick-add shortcut is not in the current public docs (§3.5) and is not planned. **`.claude/rules/*.md`** with `paths:` scoping is Phase 3, through the same `PostToolUse` hook as subfolder files. **Auto memory** (`~/.claude/projects/…/memory/`) is not read: it is Claude Code's own store of its sessions.

**Writing memory.** `/init` in the profile writes **`AGENTS.md`** (Codex's behaviour), and, if the project already has a `CLAUDE.md`, says so and offers to make `CLAUDE.md` the one line `@AGENTS.md` (this repository's convention) rather than keeping two copies. It never rewrites a `CLAUDE.md` without asking.

### 9.2 Settings files

Read, in the documented precedence (managed, then `--settings`, then `.claude/settings.local.json`, then `.claude/settings.json`, then `~/.claude/settings.json`, arrays merged, §3.14), never written:

| Setting | Translation | Phase |
|---|---|---|
| `permissions.allow` / `ask` / `deny`, `additionalDirectories`, `defaultMode` | §9.3 | 2 |
| `hooks` | §9.5 | 1 |
| MCP servers in `.mcp.json` (project, approved before first use) and `~/.claude.json` (user and local scopes; only its `mcpServers` entries are read) | `-c mcp_servers.<name>…` for the session, `${VAR}` and `${VAR:-default}` expanded; a project server is offered only after the trust prompt; remote (`http`, `sse`, WebSocket) servers are refused at `/airgapped on` | 1 |
| `attribution` | the commit guidance in the prompt block | 3 |

**MCP servers: built (2026-10-21)** in `ling-rs/src/compat_mcp.rs`, by the owner's decision that each needs the user's approval first.
- **Scopes:** Claude Code's three, in its precedence (local `~/.claude.json` `projects.<path>.mcpServers`, project `.mcp.json`, user `~/.claude.json` `mcpServers`). A project's `.mcp.json` is read only in a trusted folder.
- **Approval:** `ling compat mcp approve <name>|--all` records a hash of the server's name, scope, file and definition in `$CODEX_HOME/compat/claude/mcp-approved.json`, which the sandbox cannot write. A changed definition waits again. A TUI start names each server that waits; `ling compat mcp` lists them and `revoke` removes an approval.
- **stdio servers:** Codex runs `ling compat mcp-run …`, which checks the approval once more at spawn and expands `${VAR}`/`${VAR:-default}` itself. Codex forwards only the named variables through `env_vars`, so no value is on a command line. The wrapper then becomes the server, in the project's root. At a configured `/airgapped on` it runs the server under `bwrap --unshare-net`, or refuses without `bwrap`.
- **HTTP servers:** Codex connects them with `env_http_headers`, `bearer_token_env_var` or literal headers. A header mixing text and variables is refused. HTTP servers are not offered at `/airgapped on`.
- **Refused:** SSE and WebSocket servers, names Codex rejects, and Mightling's own server names.
- **Egress audit:** the `--compat claude` audit's repository has an approved stdio server (it must start and reach nothing) and an unapproved HTTP one (it must never be connected).
- **Mid-session `/airgapped on`:** stdio servers already running keep what they had at start. Start the session at `on` to have them offline.
| `env` | set in the session's environment (not the sandbox's network settings; `HTTP_PROXY` and similar are refused at `/airgapped on`) | 1 |
| `model` | accepted if served, else a notice | 1 |
| `apiKeyHelper`, Anthropic credentials, `forceLoginMethod` and similar | ignored with a notice ("no vendor sign-in in Mightling") | 1 |
| `statusLine` | Codex's `/statusline` items where one matches; a command status line is P2 | 3 |
| `outputStyle` | P2 (a named Mightling prompt, [PROMPT](./DREAMFERENCE_MIGHTLING_PROMPT.md), is the nearest) | — |
| `enabledPlugins`, marketplaces | local plugins only (§8.3) | 3 |
| anything else | listed by `ling compat doctor` as not supported | 1 |

Mightling's own settings stay where they are (`dreamference.toml`, `~/.config/dreamference/config.toml`, `$CODEX_HOME/config.toml`). When a Mightling setting and a Claude Code setting disagree, **Mightling's safety settings win** (the `/airgapped` rule "the strictest wins" applies: a `.claude/settings.json` cannot loosen `/airgapped`, the sandbox mode set by `ling-admin`, or the approval policy below what Mightling's config sets).

### 9.3 Permissions

| Claude Code (documented, §3.2) | `ling --compat claude` |
|---|---|
| mode `default` | `-a on-request -s workspace-write` (Mightling's default) |
| mode `acceptEdits` | `-s workspace-write` with edits inside the workspace approved; commands still asked. Codex's workspace-write already lets edits inside the workspace through, so the difference is in what is asked for commands (documented as a difference) |
| mode `plan` | Codex's Plan mode (`/plan`) at start |
| mode `bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox`; refused at `/airgapped on` and by a managed setting that disables it |
| mode `dontAsk` | `-a never` with the sandbox kept: whatever would ask is refused |
| mode `auto` (a classifier approves) | not planned: Codex's auto-review is hidden in `ling` (patch `0012`) and a second model call per tool call costs a 27B model too much; refused with a message |
| rule order | deny, then ask, then allow, first match wins (§3.2); the translator emits the rules so Codex's evaluation gives the same answer, and a test checks every documented example |
| `Bash(npm run *)`, legacy `Bash(ls:*)`, `Bash(exact)` in `allow` | an exec-policy prefix rule with `allow`, in the session's rules folder; a `*` inside a word (not a prefix) cannot be a prefix rule and becomes the `guard` hook's check |
| `Bash(…)` in `deny` | a prefix rule with `forbidden` |
| `Bash(…)` in `ask` | a prefix rule with `prompt` |
| `Read(path)`, `Edit(path)`, `Write(path)` (gitignore syntax) | a `PreToolUse` hook (`ling compat guard`) that denies or asks on matching paths, for `apply_patch` and the discrete tools; shell reads of a denied path cannot be matched reliably and the doc says so |
| `Agent(name)`, `Skill(name *)` | the guard hook on Codex's subagent tool; skills: the catalogue left out of the session |
| protected paths (`.git`, `.claude/`) | never approved automatically: the guard asks |
| `WebFetch(domain:…)` | passed to `ling-fetch` as an allow-list (a flag to add in `ling-web-rs/`) |
| `mcp__server__tool` | Codex's MCP tool approval settings |
| `additionalDirectories` | writable roots |

**Built (2026-10-21)** in `ling-rs/src/compat_permissions.rs`.
- **The guard:** Claude Code's rules from the settings files (a repository's only in a trusted folder) and from `--allowedTools`/`--disallowedTools` go to one `PreToolUse` hook, `ling compat guard`. The launcher registers and trusts it with the session's other hooks; there are no exec-policy files.
  - Deny is checked first, then ask, then allow.
  - A **deny** rule refuses the call and names the rule.
  - An **ask** rule refuses it too and says so: a hook cannot make Codex ask, so the mode takes the safe direction.
  - An **allow** rule needs nothing. Codex runs what its sandbox permits without asking and still asks for what would leave the sandbox.
- **`Bash(…)` rules:** matched on every subcommand (Claude Code's separators, substitutions included, inside double quotes too), after its wrappers and any leading assignment. `*` works as on the permissions page, including the bare-command rule for a trailing ` *` and the older `:*`.
- **`Read(…)`/`Edit(…)` rules:** the four gitignore anchors (`//`, `~/`, `/` from the settings file's anchor, relative), against `apply_patch`'s files and the file commands and redirections of a shell command. A `Read` deny also refuses an edit.
- **Other rules:** `WebFetch(domain:…)` matches `ling-fetch` URLs, with Claude Code's label rules. Bare `WebSearch` matches `ling-search`. Tool names and globs match (`mcp__*`, `Agent(role)`). Writes to `.claude/` and `.git/` are refused as protected.
- **`--permission-mode` in the TUI:**
  - `default`, `manual` and `acceptEdits` change nothing (Codex's workspace-write already lets edits through).
  - `dontAsk` is `-a never`.
  - `bypassPermissions` is Full Access, refused at `/airgapped on`.
  - `plan` starts as usual with a line saying to type `/plan`, since Codex has no setting for the starting mode.
  - `auto` is refused.
- **`permissions.defaultMode`** in a settings file is not read: a repository must not choose a looser mode.

The mapping is not exact (§14 Q4): where Claude Code would *allow* without asking and Codex's model cannot express it (a `PreToolUse` hook may not return a plain `allow` at the pinned tag, only `allow` with a rewritten input), the mode asks, which is the safe direction. Claude Code's own rule that a hook's `allow` never overrides a deny or ask rule holds trivially.

### 9.4 Commands, skills and subagents

- **Skills**: already done. `~/.claude/skills` and a trusted repository's `.claude/skills` are linked into `ling` today ([SKILLS §15](./DREAMFERENCE_MIGHTLING_SKILLS.md)); the profile changes nothing there.
- **Built (2026-10-21), custom commands**, in `ling-rs/src/compat_commands.rs`, with no patch and no files written. Codex's custom prompts are gone at the pin, so the command is not converted into a skill: the mode expands it where it is typed.
  - **Where:** in the `Enter` hook (TUI) and in print mode's prompt.
  - **Sources:** `~/.claude/commands` and a trusted repository's `.claude/commands`, subfolders as `dir:name`. A project's file wins over the user's, and a skill over a command.
  - **Expansion:** `/name args` becomes the file's body with `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N` and `${CLAUDE_PROJECT_DIR}` put in. A body with no placeholder gets `ARGUMENTS: …` appended.
  - **Skills:** a Claude Code skill typed as `/name args` becomes `$name args`, the form Codex selects skills by.
  - **Shell lines:** `` !`cmd` `` is not run, because the TUI's process is outside the sandbox. It becomes a request to run the command with the shell first.
  - **Name clashes:** a Codex command of the same name wins (the list is checked against the pinned TUI's enum), and `doctor` says so.
- **Custom slash commands** (`.claude/commands/*.md`, `~/.claude/commands/*.md`): Claude Code's docs now say custom commands "have been merged into skills" (§3.4), so converting them into skills is the documented model, not an approximation. They are converted at start into skills in the session folder and linked like `from-claude` skills, using the conversion Codex's own `command_migration.rs` performs, so `/name args` works. Substitutions (`$ARGUMENTS`, `$N`, named arguments, `${CLAUDE_SKILL_DIR}`, `${CLAUDE_SESSION_ID}`) are expanded by the launcher when the skill is invoked; `` !`cmd` `` runs the command in the sandbox, never outside it. `allowed-tools`/`disallowed-tools` become session permission rules while the skill runs where they can be expressed, otherwise a notice; `context: fork` and `agent` run the skill in a subagent (Phase 3).
- **Built (2026-10-20), subagents**, in `ling-rs/src/compat_agents.rs`, written by the launcher itself rather than through `external-agent-migration`.
  - **Sources:** `~/.claude/agents` and every `.claude/agents` from the repository root down to the working folder, subfolders included. The nearest definition of a name wins. A repository's agents are read only once the folder is trusted.
  - **Roles:** each agent becomes a Codex role file under `$CODEX_HOME/compat/claude/<project>/agents/`, rebuilt at every start, and is declared with `-c agents={…}`. The body becomes `developer_instructions`.
  - **Read-only:** a tool list (or `disallowedTools`) with no editing tool and no `Bash`, or `permissionMode: plan`, makes the role read-only (`sandbox_mode = "read-only"`).
  - **Other fields:** `effort` becomes `model_reasoning_effort`, and `model` is ignored. Other tool lists, `bypassPermissions`, `maxTurns`, `skills`, `mcpServers`, `hooks`, `memory`, `isolation` and `background` are reported by `doctor` as not honoured.
  - **Check:** a test parses the written file with Codex's own `parse_agent_role_file_contents`.
- **Subagents** (`.claude/agents/*.md`, `~/.claude/agents/*.md`, in the documented priority: `name`, `description`, `tools`, `disallowedTools`, `permissionMode`, `maxTurns`, `skills`, `mcpServers`, `hooks`, and the body as the subagent's instructions): converted into Codex agent roles (`agents/*.toml`) in the session folder, through `external-agent-migration`'s `import_subagents_with_rewrite_profile`, called with a rewrite profile that changes nothing (Mightling wants the user's text as written). `model` is ignored (one model).

### 9.5 Hooks

**Built (2026-10-20)** in `ling-rs/src/compat_hooks.rs`.
- **Sources:** the managed file, `~/.claude/settings.json`, `.claude/settings.json` and `.claude/settings.local.json`, all merged.
- **Delivery:** one `-c hooks={…}` table that carries the memory hook too. Each command runs as `ling compat hook-shim -- '<command>'`, readable in `/hooks`.
- **Trust:** the user's and the managed hooks are trusted beside their groups (session-flags keys, the hash checked against `codex_config::version_for_toml`). A repository's hooks are offered only in a trusted folder and then wait for Codex's own review.
- **Matchers:** a list of exact names becomes Codex's `|` list (`MultiEdit` → `Edit`), or an anchored expression when a name holds `-`. An expression with look-around or back-references is skipped.
- **Shim input:** the shim rewrites `apply_patch` into `Write` (`file_path`, `content`) or `Edit` (`file_path`, `old_string`, `new_string`; the first file of a patch), and `spawn_agent` into `Agent`. It sets `CLAUDE_PROJECT_DIR`.
- **Shim output:** it drops a plain `allow`, an `ask` and a `defer`, so Codex's approval applies, and keeps `allow` with `updatedInput` on `PreToolUse`.
- **Skipped:** events Codex lacks, non-command handlers, and everything under `disableAllHooks`. `doctor` lists every hook and each skip, and a TUI start says how many were read.

Translated at start from every settings file into Codex hook groups in the session folder:

- **Events**: the core names are the same (§4.2): `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, `Stop`, `SessionEnd`. Claude Code documents over twenty more (`PostToolUseFailure`, `PermissionDenied`, `Notification`, `InstructionsLoaded`, `CwdChanged`, `FileChanged`, `WorktreeCreate`, …, §3.3) that the pinned Codex lacks; `doctor` lists each one skipped. `PostToolUseFailure` can be emulated from `PostToolUse` by the shim (a failed result) and is the one worth doing in Phase 2.
- **Handler types**: `command` in Phase 1; `mcp_tool` and `http` to a loopback address in Phase 3 (an `http` hook to any other address is refused at `/airgapped on` and named by the egress audit when off); `prompt` and `agent` hooks, which call the model, are P2.
- **Matchers**: Claude Code's tool names are mapped onto Codex's: `Bash` → the shell tools, `Edit`/`Write`/`MultiEdit` → `apply_patch` (and the discrete `Edit`/`Write`), `Read`/`Grep`/`Glob`/`WebFetch`/`WebSearch` → the discrete tools when that roster is on, and to the shell otherwise with a notice that a shell command cannot be matched by tool name; `mcp__<server>__<tool>` → the flattened MCP name `0020` gives the tool.
- **Inputs and outputs**: each translated command runs through `ling compat hook-shim -- <the user's command>`, which rewrites Codex's stdin JSON into the shape the public hooks reference documents (`tool_name` in Claude Code's names, `tool_input` with its documented fields such as `command` or `file_path`, the session and transcript fields) and passes the output back. Exit code 2 and `decision`/`hookSpecificOutput` already mean the same thing; a plain `allow`, an `ask` or a `defer`, which Codex rejects, is turned into "no decision" (Codex's own approval then applies) by the shim and reported once; `allow` with `updatedInput`, which Codex accepts, is passed on with the input's fields translated back into Codex's tool input.
- **Environment**: the documented project-directory variable is set to the project root.

---

## 10. Integration

### 10.1 As a runner

The compatibility mode is not a new runner pair, because it is not a new program: `config.agent_runner` keeps `codex` and the mode is a setting of it. `ling-admin` gains:

- `ling-admin run "…" --compat claude`: the Codex runner with `DREAMFERENCE_MIGHTLING_COMPAT=claude` in the environment; not a new choice in the strategy switch (no new installer, no new link: `CodexInstaller` resolves the same binary).
- `ling-admin codex build` runs `audit egress --compat claude` (§8.2) beside the plain audit; it creates no link.
- `ling --compat`, `ling compat use|status|doctor`, `DREAMFERENCE_MIGHTLING_COMPAT`, `mightling_compat`, `DREAMFERENCE_MIGHTLING_CLAUDE_TOOLS` and `mightling_claude_md` documented in [CLI](./DREAMFERENCE_CLI.md).

Under (a) or (b) this would have been a real pair (`ling_claude_installer.py`, `ling_claude_runner.py`, exported from `runner/__init__.py`, a `VLLMReadinessWaiter` wait); under (c) the launcher waits for the server as it does for `ling`.

### 10.2 The desktop app

The app drives its own `ling app-server` over one IPC channel vetted against `ling-rs/web/policy.json` ([DESKTOP_PARITY](./DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md)). A Work thread opened in a folder with a `CLAUDE.md` or `.claude/` gets the profile when the user turns on **"Read Claude Code project files"** in settings (per project, off by default); the app-server is then started with `DREAMFERENCE_MIGHTLING_COMPAT=claude`. The memory hook, translated hooks and rules are app-server-side, so the policy file needs no new method. Parity rows the app shows (hooks list, skills list) already exist.

### 10.3 Night Shift

Night Shift runs `ling exec -C <worktree> -s workspace-write --json -o …` ([NIGHT_SHIFT §5.3](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md)). `/night add --compat claude` (and `[night] compat = "claude"` per repository) sets the profile for that task's sessions, so a repository configured for Claude Code is worked with its `CLAUDE.md` and hooks. Nothing else in the runner changes: worktrees, scopes, the stall nudge, best-of-N and the morning report are the same. Permission modes are not taken from `.claude/settings.json` at night: the runner's `workspace-write` stands.

### 10.4 Messengers and `ling web`

Ask threads are `ling` sessions; they take the profile only if the node's config says so (`mightling_compat`). No new surface.

---

## 11. SWE-bench: the arm against `ling`

Following the pattern of §20–§23 of [SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md):

- **Built (2026-10-21), the `--compat` part:** `swe-bench run --compat claude|off` sets the variable in every container and records `compat` in the manifest (`auto` without the flag, which a manifest from before reads as). The report prints a `Compatibility` line and `--against` lists the field when two runs differ. No night is armed; the main session schedules one. `--claude-tools` waits for the discrete roster.
- **`swe-bench run --compat claude`** sets `DREAMFERENCE_MIGHTLING_COMPAT=claude` for every session of the run (the runner's extra environment of each container); **`--claude-tools codex|discrete|discrete-search`** sets `DREAMFERENCE_MIGHTLING_CLAUDE_TOOLS`. With no flag neither variable is set and the launcher decides as for every user. The manifest records `compat` and `claude_tools` (`auto` when absent; a manifest from before the options reads as `auto`); `report` prints them and `report --against` lists them when they differ.
- **The instances carry no `CLAUDE.md`**, so the profile alone changes only the command line and the output; the arm that matters is **`--compat claude --claude-tools discrete`** against the record arm (`default`). A second pair adds a generated `CLAUDE.md` per instance (the same text the record arm gets as its task preamble, moved into the memory file) to check that memory delivered through the `SessionStart` hook scores the same as through the prompt.
- **The list**: the night-2 list without mismatched tasks (`docs/dev/swe-bench/night2-no-mismatch.txt`, 44 tasks), record arm first, as on every night; McNemar exact, the report's verdict. Columns: resolved, tokens (the roster's schema tokens counted), tool calls by name, edits by tool, compactions, wall time.
- **Decision rule** (proposed for the owner to set before the night): a clear win for `discrete` makes it the profile's default and opens the question for `ling`'s default; a tie keeps `codex` (fewer schema tokens, one less server); a loss keeps `codex` and the discrete roster stays an option.
- **The container**: the same patchelf'd `ling` (the variable set in each container's environment); `ling compat tools` runs inside the container as the agent's MCP server, so it needs nothing outside it. Under (a)/(b) the harness would have needed a second runtime per container.
- **Tests**: as for `--apply-patch` and `--exec-tool`: the variables are set only when the flags are, the manifest and the report tell the arms apart, a call is counted for the discrete `Edit` and not for a shell command naming it.

---

## 12. Phases and milestones

| Phase | What | Exit criterion |
|---|---|---|
| **0. Checks** (1–2 days) | Trace `/import` for network (§8.3); confirm `-c` overrides can register hooks with a trust identity and an `additionalContext` `SessionStart` hook reaches the model on SGLang; confirm `/compact` takes an argument; confirm whether Codex's turn-diff tracker sees MCP edits; the interface checks of §6.5.7 (the keymap overrides load without conflict, Shift+Tab can drive both the permission and the plan cycle, a Claude-shaped `[tui.keymap]` survives a Codex bump); later observations of Claude Code into `docs/dev/ling-claude-observations.md` | a page of findings appended here as §16; the name is decided (D1) |
| **1. Files and the command line** | `ling-rs/compat/` and `src/compat.rs`; the switch (`--compat`, `DREAMFERENCE_MIGHTLING_COMPAT`, `mightling_compat`, `ling compat use\|status`, §0.4); the zero-patch interface settings (§6.5.4); `CLAUDE.md` hierarchy with imports through the `SessionStart` hook; `mightling_claude_md` for plain `ling`; settings read with `env`, `model`, hooks (with the shim) and the ignored keys; commands and agents converted; `-p`, `--output-format`, `-c`, `-r`, `--max-turns`, `--append-system-prompt`, `--add-dir`, `--model`; `ling compat doctor`; `audit egress --compat claude` | offline tests green; the audit passes; a repository with only `CLAUDE.md`, a `.claude/settings.json` hook and a `.claude/agents/` subagent works in `ling --compat claude -p` and in the TUI; in the TUI, `Ctrl+O` opens the transcript, `Shift+Tab` cycles the modes and the renderer follows Claude Code's switches (§6.5.4) |
| **2. Permissions and the tool roster** | `--permission-mode`, `--allowedTools`/`--disallowedTools`, permission rules (exec-policy rules and `ling compat guard`); `ling compat tools` with the discrete roster; `--input-format stream-json`; SWE-bench options | the SWE-bench night of §11 run and reported |
| **3. Comfort** | the interface hooks of §6.5.6 (one slash-command fallback that serves `/memory`, `/rewind`, `/context`, `/config` and the other names of §6.5.5 from `ling-rs/`, the double-press exit, `\`+`Enter`, the transcript's write-to-scrollback; all counted against the patch cap, raised to 48,500 bytes by the owner for them, D4); `.claude/rules/`; `mcp_tool` and loopback `http` hooks; skills with `context: fork`; the desktop setting; `/night add --compat claude`; status line and local plugins; documentation in `docs/` ("Coming from Claude Code") | the desktop app and Night Shift run a profile session end to end on this machine |
| **4. Optional** | rewind of file changes (checkpoints; a patch or a launcher-side snapshot of the worktree per turn); the remaining interface differences §6.5.6 lists as not planned (classic resize that keeps the scrollback, mouse selection and click-to-expand) | each only by a separate owner decision |

Milestone for a release note: Phase 1 can ship as a preview in the next minor release, off unless switched on (§0.4).

---

## 13. Tests

**Tests never touch the real machine.** `tests/conftest.py` already gives every test its own `HOME` and fails any real `docker` command that changes something, any spawn of the installed `ling` or anything under `~/.local/bin`, and any connection to the model server's ports (8000, 18000). The new tests follow it:

- **Rust, offline** (`ling-rs/compat/`, `cargo test` in a copy of the folder as for `ling-rs/skills/`; the launcher's in the build's export directory, never in `codex/`): memory discovery with fixtures under a temporary home (hierarchy, `CLAUDE.local.md`, `@path` imports, the depth limit, a cycle, a symlink out of the tree refused, deduplication with `AGENTS.md`, this repository's `@AGENTS.md` case, the budget); settings precedence and every translated key; the hook shim's stdin and stdout rewriting against fixtures written from the public hooks reference; matcher mapping; permission rules into exec-policy rules; the `guard` hook's decisions; commands and agents conversion; every CLI flag mapped or refused; the output translator against recorded `exec --json` streams (no model); a grep that the crate contains no Anthropic host name, key variable or model id.
- **The profile is opt-in**: a plain `ling` launch in a fixture home writes byte-for-byte the same `config.toml` and catalog with the compatibility code present as without it.
- **Nothing written outside `$CODEX_HOME`**: a test runs the start-up translation on a fixture repository with a full `.claude/` and asserts the repository and the fixture `~/.claude` are unchanged (mtime and content).
- **Python, offline** (`tests/`): `ling-admin run --compat claude` sets the variable and runs `CodexRunner` against a `FAKE_MIGHTLING`-style stand-in; `codex build` calls the audit's `--compat claude` form (mocked); `audit egress --compat claude` builds its fixture repository and parses a recorded trace; SWE-bench's `--compat` and `--claude-tools` options (as §22–§23's tests do); Night Shift's `--compat` passes the variable to the stand-in.
- **Patch size**: `test_the_patches_stay_small` is unchanged in Phases 1 and 2, which add no patch. Phase 3's interface hooks raise its cap to the 48,500 bytes the owner approved (D4), in the same change as the hooks, with a comment in the test's style naming each hook and its bytes. A test asserts that `Shift+Tab` in the mode never lands on Full Access while `/airgapped` is `on` (D6).
- **The interface, offline**: the launcher's `-c tui.*` overrides and the generated `[tui.keymap]` are written only when the mode is on (a plain `ling` start passes none of them); the key map loads in the pinned TUI's keymap validation without a conflict (a test builds it against the export's `tui_keymap.rs` schema); the renderer choice follows `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN`, `CLAUDE_CODE_NO_FLICKER` and the `tui` setting in the documented order (§6.5.4); a test fails if the pinned Codex renames a keymap action or a `tui.*` key the mode writes. A recorded-bytes test (like §6.5.2's captures, made from the built `ling` in a pseudo-terminal, marked `installed_binary`) checks the mode's fullscreen start enters the alternate screen and its classic start never does.
- **Live, marked `installed_binary`** (like `test_mightling_slash_commands.py`, run only by hand against a built binary and a running server): `ling --compat claude -p "…" --output-format json` in a fixture repository whose `CLAUDE.md` names a word the answer must contain; a `PreToolUse` hook from `.claude/settings.json` that blocks a command; `-c` continuing the last session.
- **Codex bump**: a test reads the pinned Codex's hook event names and `HookEventName` list from the export and fails if the translation table names an event that no longer exists.

---

## 14. Open questions

1. ~~**The product name.**~~ **Decided 2026-10-20 (D1):** no new name; a mode of `ling` (§0.4, §2.4).
2. ~~**Must the interface behave like Claude Code's too?**~~ **Decided 2026-10-20 (D2):** yes, its behaviour (not its look), from a black-box study (§6.5).
3. **Should plain `ling` read `CLAUDE.md` by default** (`mightling_claude_md = true`), given many repositories now carry one? Proposed: off in Phase 1, decided after a release with it available.
4. **Permission semantics that do not map** (path rules for shell reads, plain `allow` and `ask` decisions from hooks, `acceptEdits`): is "ask in doubt" acceptable, or should a small patch let a `PreToolUse` hook allow (counted against the cap)?
5. **`/import` today**: trace it in Phase 0 and, if it fetches, hide it or confine it to local sources with a one-line hook?
6. **A comparison night with Qwen Code** (Apache-2.0, built for the Qwen family's tools) as a `qwen-code` runner pair: worth one night to learn whether a different loop, not only a different roster, helps the 27B model? It would need its own sandbox and egress check for that night only.
7. **Rewind of file changes**: Claude Code's checkpoints are a frequently used feature with no equivalent in the pinned Codex. Launcher-side snapshots (a `git stash create` per turn into a hidden ref), a patch, or wait for upstream?
8. **Managed settings** (an organisation's policy file): read it if present (strictest wins) or ignore it on a single-user machine?
9. **Documentation drift**: the docs read on 2026-10-19 describe Claude Code 2.1.2xx–2.1.303 and change often (the subagent tool was renamed from `Task` to `Agent`, `TodoWrite` gave way to the `Task*` tools, custom commands merged into skills). Who re-checks §3 and the output fixtures, and how often (a line in the Codex-bump checklist)?
10. **`AGENTS.md` and `CLAUDE.md` together.** Claude Code reads `AGENTS.md` only when there is no `CLAUDE.md`; the mode reads both, `AGENTS.md` first (§9.1). Keep that, or offer a setting for Claude Code's rule? And which of the two should `/memory` and `/init` write when both exist?
11. **Observing a running copy.** **Decided by the owner 2026-10-20 (D2)** for black-box observation: running the CLI in a pseudo-terminal and recording what it writes, never opening its package (§2.1 item 1). Still open: whether a lawyer should confirm that Anthropic's terms allow it for building a competing product; until then nothing beyond the black-box rule is done.
12. ~~**The patch cap for the interface hooks.**~~ **Decided 2026-10-20 (D4):** raised to 48,500 bytes for the four hooks of §6.5.6; the test's number changes with the implementation.
13. ~~**Which renderer the mode starts in.**~~ **Decided 2026-10-20 (D5):** the classic inline renderer; fullscreen through Claude Code's own switches (§6.5.4).
14. ~~**Shift+Tab.**~~ **Decided 2026-10-20 (D6):** it cycles `ling`'s approval modes, skipping every combination `ling` forbids (Full Access while `/airgapped` is `on`); plan through `/plan` (§6.5.4, §6.5.7).

---

## 15. Sources

Public documentation of Claude Code, read on 2026-10-19 (each page is the authority for the facts §3 cites from it):

- Overview of the loop: https://code.claude.com/docs/en/how-claude-code-works
- Tools: https://code.claude.com/docs/en/tools-reference
- Permission modes and plan mode: https://code.claude.com/docs/en/permission-modes
- Permission rules: https://code.claude.com/docs/en/permissions
- Hooks: https://code.claude.com/docs/en/hooks and https://code.claude.com/docs/en/hooks-guide
- Commands: https://code.claude.com/docs/en/commands
- Skills: https://code.claude.com/docs/en/skills (and the open format, https://agentskills.io)
- Plugins: https://code.claude.com/docs/en/plugins
- Memory: https://code.claude.com/docs/en/memory
- MCP: https://code.claude.com/docs/en/mcp
- Context and compaction: https://code.claude.com/docs/en/context-window
- Headless: https://code.claude.com/docs/en/headless; CLI reference: https://code.claude.com/docs/en/cli-reference; result message fields: https://code.claude.com/docs/en/agent-sdk/typescript
- Sessions: https://code.claude.com/docs/en/sessions
- IDE: https://code.claude.com/docs/en/vs-code, https://code.claude.com/docs/en/jetbrains
- Interactive mode and background work: https://code.claude.com/docs/en/interactive-mode
- Workflows, git and pull requests: https://code.claude.com/docs/en/common-workflows, https://code.claude.com/docs/en/best-practices, https://code.claude.com/docs/en/worktrees, https://code.claude.com/docs/en/github-actions
- Settings and environment: https://code.claude.com/docs/en/settings, https://code.claude.com/docs/en/env-vars
- Subagents: https://code.claude.com/docs/en/sub-agents
- Output styles, status line, checkpoints: https://code.claude.com/docs/en/output-styles, https://code.claude.com/docs/en/statusline, https://code.claude.com/docs/en/checkpointing
- Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview
- Licence: https://github.com/anthropics/claude-code/blob/main/LICENSE.md
- Trademark: https://code.claude.com/docs/en/legal-and-compliance, https://www.anthropic.com/legal/trademark-guidelines
- Index of all pages: https://code.claude.com/docs/llms.txt

Mightling and the pinned Codex (read on 2026-10-19):

- `codex/codex-rs/external-agent-migration/src/` (`source/cla.rs`, `source_cla.rs`, `hooks_cla.rs`, `subagents.rs`, `mcp.rs`, `memory.rs`, `sessions/`), `codex/codex-rs/core-plugins/src/command_migration.rs`, `codex/codex-rs/tui/src/slash_command.rs`, `codex/codex-rs/hooks/src/` (`events/`, `engine/output_parser.rs`, `engine/discovery.rs`), `codex/codex-rs/core/src/agents_md.rs`, `codex/codex-rs/features/src/lib.rs`, `codex/codex-rs/core/src/tools/handlers/`, `codex/codex-rs/agent-roles/src/loader.rs`.
- `ling-rs/skills/src/catalog.rs`, `ling-rs/skills/src/links.rs` (the `.claude/skills` links), `docs/dev/launcher.md`, `docs/dev/codex-build.md`, and the specs listed under "Builds on".

Open-source agents (§5.2): the repositories linked in the table, read for their licence files, READMEs and documentation only; OpenCode's [rules](https://opencode.ai/docs/rules/), [skills](https://opencode.ai/docs/skills/) and [plugins](https://opencode.ai/docs/plugins/) pages. The excluded projects were identified from their READMEs as quoted in public reporting and from the DMCA notices linked in §5.2, not from their code.

---

## 16. Phase 0 findings (2026-10-20)

Checked against `main` at `09a656c` (patch series 44,952 bytes) and the pinned Codex (`rust-v0.158.0`, read only), and in the installed `ling` 1.6.4 (same pin). The runs used a pseudo-terminal (tmux, 120×40), a throwaway `HOME` and `CODEX_HOME`, and a stand-in model server on loopback. The stand-in served `/v1/models` and answered `/v1/responses` with fixed events: an error, or one Code Mode `exec` call that applies a patch and then a final message. No real model was involved, because a SWE-bench night held the model server. Claude Code was not run again; nothing in this section comes from it.

### 16.1 Shift+Tab and the approval modes (D6): needs a hook, and the bytes go back to the owner

- **Full Access is never in the cycle.** The permission shortcut (`tui/src/chatwidget/permission_shortcuts.rs`, actions `chat.next_permission_mode` and `chat.previous_permission_mode`) only offers the presets `read-only` and `auto`. It offers the auto-review variant of `auto` only when `GuardianApproval` is on, and patch `0012` hides that. It also skips any preset `permission_mode_disabled_reason` refuses. Full Access is never offered, at any `/airgapped` level. In `ling` the cycle is therefore two entries: Read Only and the default approval mode. D6's "never into Full Access" holds by construction, and the test of §13 can assert it.
- **The binding is not configurable.** `Shift+Tab` is the fixed binding `fixed.cycle_collaboration_mode` (Plan ↔ Default), listed in `MAIN_RESERVED_BINDINGS` (`tui/src/keymap.rs`). `-c tui.keymap.chat.next_permission_mode="shift-tab"` stops the built `ling` before its first frame: ``Error: Ambiguous `tui.keymap.main` bindings: `chat.next_permission_mode` uses a key reserved by `fixed.cycle_collaboration_mode` ``. `collaboration_modes_enabled()` returns `true` unconditionally, and the `BackTab` arm in `chatwidget/interaction.rs` has no switch either.
- **The smallest hook** is one more `allowed_overlaps` entry in the `main` context's `validate_no_reserved` call: `("chat.next_permission_mode", "fixed.cycle_collaboration_mode", shift(Tab))`. `handle_permission_shortcut` runs before the `BackTab` match (`interaction.rs:117`), so once the overlap is allowed the bound key goes to the approval modes. Plan mode is then reached with `/plan`. The estimate is 250–350 bytes.
- **The budget.** 48,500 − 44,952 = 3,548 bytes. §6.5.6 estimated about 3,500 bytes for the four approved hooks, which leaves 48 bytes. This hook is not part of the owner's D4 budget. §6.5.7 named this case "a hook that must fit in the bytes left under 48,500 or go back to the owner". It goes back to the owner. This gates Phase 3, not Phase 1: Phase 1's key map leaves `Shift+Tab` on Codex's collaboration modes. **Decided by the owner (2026-10-20):** the cap goes to 49,000 bytes for the five hooks together (D4).

### 16.2 The rest of the interface checks (§6.5.7)

- **The key map** of §6.5.4, as corrected there, loads in the built `ling` without a conflict. `global.open_transcript="ctrl-o"`, `global.copy=[]`, `pager.close_transcript=["ctrl-t","ctrl-o"]`, `pager.jump_top=["home","g"]`, `pager.jump_bottom=["end","shift-g"]` and `pager.page_up` with `b` added all load. `Ctrl+O` opens the transcript and `Ctrl+O` closes it, which was checked in the classic renderer. `close_transcript` refuses `esc` (reserved by `fixed.transcript_edit_previous`) and `q` (the same key as `pager.close`, which already closes it). Most of Claude Code's pager keys are already Codex's defaults. Overriding an action replaces its list, so the mode writes each list in full, Codex's defaults included.
- **The classic renderer:** `-c tui.alternate_screen="never"` starts inline. The raw output contained no `ESC[?1049h`, including while the transcript overlay was open.
- **Mouse capture:** there is no configuration switch. `OverlayInput::captures_mouse` captures in the owned (alternate) screen and in the transcript and usage overlays. It never captures in the classic main view, and under tmux it is off when tmux's mouse option is off. `CLAUDE_CODE_DISABLE_MOUSE` is reported by `doctor` as not honoured.
- **`Ctrl+L`** (`global.clear_terminal`) in the classic renderer writes `ESC[2J ESC[3J` and repaints only the session header. The terminal's scrollback, which holds the conversation in that renderer, is erased. Claude Code documents `Ctrl+L` as a redraw that keeps the conversation. This is a visible difference to add to §6.5.6's "stays different" list. Otherwise it is a further hook, which is the owner's call.
- **`Shift+Enter`** is bound to `editor.insert_newline` by default, beside `Ctrl+J`, `Ctrl+M` and `Alt+Enter`. A terminal that reports it (the kitty protocol, which `ling` pushes) gets a newline. No change is needed.
- **`\` + `Enter`:** with a pause between typing and `Enter`, one press sends `xyz\` unchanged. That is the behaviour §6.5.6 item 3 changes. When typing and `Enter` arrive in one burst, the paste-burst heuristic makes the first `Enter` part of the paste.
- **An unknown command** (`/memory`) is refused in the composer (`bottom_pane/chat_composer.rs`, the `SubmissionValidation::UnknownCommand` branch). The composer prints "Unrecognized command '/memory'…" and keeps the draft. A queued draft is refused the same way in `chatwidget/slash_dispatch.rs`. These are the two places item 1's fallback calls into `ling-rs/`.
- **Double-press exit:** `DOUBLE_PRESS_QUIT_SHORTCUT_ENABLED` is a `const` in `bottom_pane/mod.rs`, used in `bottom_pane/mod.rs` and three times in `chatwidget/interaction.rs`. Making it a launcher call changes every use site, so item 2 may cost more than its 0.4 KB estimate.
- **Not checked yet:** `ling`'s rendering of shell calls and approvals against §6.5.3. The stand-in showed an `apply_patch` as "Added b.txt (+1 -0)" with its line. That check needs the model server, after the benchmark night.

### 16.3 Hooks, memory and the session folder

- **Hooks can be registered with `-c`, trusted, for one session**, with nothing written. The hooks themselves go in as `-c 'hooks.SessionStart=[{hooks=[{type="command",command="…",timeout=10}]}]'`. The trust entry must be an **inline table**: `-c 'hooks.state={"/<session-flags>/config.toml:session_start:0:0"={trusted_hash="sha256:…"}}'`. A dotted key with a quoted segment (`hooks.state."…".trusted_hash=…`) is split at its dots and does not match. The key without its leading `/` (`<session-flags>/…`) does not match either. The hash is the one `SweBenchHooks.hook_hash` computes, over `{event_name, hooks:[{async, command, timeout, type}], matcher?}`. With both, the hook ran without the review prompt. Hooks given with `-c` sit beside the ones in `config.toml`: `/hooks` listed the launcher's `ling ledger` hook and the session hook, both trusted. `--dangerously-bypass-hook-trust` exists and is not needed.
- **A `SessionStart` hook's `additionalContext` reaches the request** as a `developer` message after the environment context and before the first user message. Codex also runs `SessionStart` after a compaction (source `compact`), which is when Claude Code re-injects memory, so the memory hook takes no matcher. `UserPromptSubmit` and `PostToolUse` context also arrive as `developer` messages. On SGLang the launcher's ledger and compaction `SessionStart` hooks already use this path, but Phase 0 did not see a `developer` message carrying hook context answered by the model. That check comes after the night.
- **Hooks and MCP servers run outside the command sandbox.** `hooks/src/engine/command_runner.rs` spawns a plain `Command` through the user's shell. `rmcp-client/src/stdio_server_launcher.rs` spawns a plain `Command`, and its executor path passes `sandbox: None`. §7.2's self-confinement of `ling compat tools`, and the start-up line about hooks at `/airgapped on` (§8.4), are needed as written.
- **PreToolUse decisions:** a plain `allow` and any `ask` are rejected (`hooks/src/engine/output_parser.rs`). `allow` is accepted only with `updatedInput`. This confirms §4.2 and §9.3.
- **Tool names hooks see:** the shell is already reported as `Bash` with `tool_input.command`. `apply_patch` is reported as `apply_patch` and also matches `Write` and `Edit`, with the patch text in `tool_input.command`. `spawn_agent` also matches `Agent`. The pinned Codex already gives Claude Code's matcher names to these three, so the matcher table of §9.5 maps only the rest. The shim still has to rewrite `tool_input` (`file_path`, `content`, `old_string`/`new_string`) for a hook that reads those fields.
- **Edits on SGLang reach no edit hook, because SGLang has no edit tool.** On a server other than ling-engine the launcher offers no `apply_patch` at all (`apply_patch_tool_type` is `None`). Code Mode's nested tools were `exec_command`, `write_stdin`, `view_image`, the goal and agent tools, and nothing else. The model edits through the shell, so an `Edit|Write` hook never fires there. Every edit is a `Bash` command that §9.3's guard can match only by its text. With `apply_patch` offered freeform (ling-engine, or `DREAMFERENCE_MIGHTLING_APPLY_PATCH=freeform`), a nested `tools.apply_patch(...)` inside Code Mode's `exec` fired `PreToolUse` and `PostToolUse` with `tool_name: apply_patch`. That run also applied the patch and showed the diff. The `exec` cell itself fires no hook. For the default server, the `Edit`/`Write` rows of §9.3 and §9.5 therefore depend on the discrete roster of §7.2.
- **`DREAMFERENCE_MIGHTLING_APPLY_PATCH=function` stops `ling` at start** with "unknown variant `function`, expected `freeform`". At this pin, Codex's `ApplyPatchToolType` has only `Freeform`, so the `function` value of the launcher's override (`apply_patch_tool_type`) is a defect independent of this mode.
- **The turn-diff tracker sees `apply_patch` only.** `track_delta` is called from `emit_patch_end` alone. Shell and unified-exec paths pass no tracker, and MCP tool calls never reach it. An `Edit` served by `ling compat tools` would show no diff, so §7.2's fallback applies: translate `Edit`/`Write` into `apply_patch`. On SGLang that also needs `apply_patch` to be offered at all.

### 16.4 `/compact` and `/import`

- **`/compact` takes no argument.** `Compact` is not in `supports_inline_args`, and `Op::Compact` carries no data. `/compact focus on tests` typed in the TUI is sent to the model as an ordinary prompt and is not refused. The focus text of §7.3 can reach the summary only through the launcher (the turn-end compaction hook) or the slash-command fallback.
- **`/import` can reach the network.** The TUI command is unreachable: patch `0012` makes it invisible, and `find_builtin_command` matches only visible commands. The app server's `externalAgentConfig/import` is open to every client, though. `ling-rs/web/policy.json` allows it, and the desktop app's Import panel leaves plugins out by its own choice. From that method, the plugin step can run `git clone` of a marketplace whose source is a Git repository (`core-plugins/src/marketplace_add/install.rs`). Such a source can come from `known_marketplaces.json` or `extraKnownMarketplaces`, or be added automatically for `enabledPlugins` entries naming Anthropic's official marketplaces (`source_cla.rs`). The step can also run `git clone` or `npm pack` for a plugin whose own source is Git or npm, even inside a local marketplace (`core-plugins/src/loader.rs`). No Mightling patch touches these paths, and the egress audit does not trace them.
- **The single place to cut them** is the plugin step's local/remote split in `external-agent-migration/src/plugins.rs`, together with the per-plugin Git and npm arms of `loader.rs`. It is a hook against the cap, as §8.3 said. Its bytes join the owner's question of §16.1, or the policy file can refuse the plugin item instead, at no cap cost. **Decided by the owner (2026-10-20):** the policy file refuses it (`refusedImportItems: ["PLUGINS"]` in `ling-rs/web/policy.json`). Both `ling web` and the desktop app's main process drop every plugin item from `externalAgentConfig/import`, refuse an import with nothing else left, and refuse an item that names no type. The other items, configuration and skills included, still import. Conformance cases on both sides cover it; it ships in 1.7.1.

### 16.5 What Phase 1 takes from this

The switch, the classic renderer, the corrected key map without `Shift+Tab`, memory through a `SessionStart` hook registered with `-c` as in §16.3, and settings hooks translated the same way all go ahead with no patch. Three items wait for the owner: `Shift+Tab` (§16.1), the `Ctrl+L` difference (§16.2) and `/import`'s plugin step (§16.4). All three were decided the same day: the cap goes to 49,000 bytes, `Ctrl+L` is a stated difference (§6.5.6 item 7), and the bridge policy drops the plugin items (§16.4).
