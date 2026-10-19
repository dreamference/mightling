# Mightling `ling-claude` — Claude Code's way of working, on the local model (working name)

**Status:** **proposal, not built.** Asked for by the owner on 2026-10-19 and written the same day. Nothing in this document exists in the code yet; every "today" below was checked against `main` at `a5c0ad7` and the pinned Codex source (`rust-v0.158.0`) on 2026-10-19.

**Name:** `ling-claude` is a **working name** and must not ship (§2.4, §14 Q1). Everywhere below it means "the agent this spec proposes".

**Subject:** an open-source, clean-room re-implementation of the user experience and feature set of Anthropic's Claude Code terminal agent, as its public documentation describes them, running against Mightling's own model server (Qwen3.8-27B on SGLang today, ling-engine later) with Mightling's sandbox, `/airgapped` and egress audit.

**Builds on:**
- [MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md): `ling`, the patch series under its 45,000-byte cap, the launcher in `ling-rs/` (`docs/dev/launcher.md`, `docs/dev/codex-build.md`).
- [AGENTS](./DREAMFERENCE_AGENTS.md): the runner pairs and the strategy switch on `config.agent_runner`.
- [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md) and [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md): the audit, its verdict, and the `on` level enforced in Codex's sandbox helper (patch `0019`).
- [MIGHTLING_SKILLS](./DREAMFERENCE_MIGHTLING_SKILLS.md): `~/.claude/skills` and a trusted repository's `.claude/skills` are already linked into `ling` (`ling-rs/skills/`).
- [MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md), [MIGHTLING_DESKTOP_PARITY](./DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md), [MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) (§20–§23: how an arm is added), [MIGHTLING_COMPACTION](./DREAMFERENCE_MIGHTLING_COMPACTION.md), [MODELS](./DREAMFERENCE_MODELS.md).

---

## 0. Decisions, stated first (proposed, for the owner)

1. **Clean room, without exception.** Claude Code is proprietary. This spec and the implementation use only its public documentation and its observable behaviour; no code, prompt text or asset of it is copied, read from a leak, or decompiled (§2).
2. **Build on `ling`, do not write a second agent (option (c), §5).** The pinned Codex already carries most of what Claude Code's feature list asks for, including hooks with the same event names and the same JSON decision fields, plan mode, subagents, background terminals, `/review`, `/worktree`, `/resume`, MCP and skills, and even a one-time importer of Claude Code's configuration (`/import`, §4.2). What is missing is a **compatibility layer**: Claude Code's file formats read live, its print-mode command line, its tool roster as an option, and its permission rules. All of it fits in `ling-rs/` with **no new patch** in Phase 1.
3. **What "alternative agent" means here, stated openly.** The owner asked for an alternative agent with Claude Code's user experience and feature set. This spec delivers the **feature set, the file formats and the command line** a Claude Code user types (`ling-claude -p … --output-format stream-json`, `-c`, `-r`, `CLAUDE.md`, `.claude/settings.json`, `.claude/commands`, `.claude/agents`), inside `ling`'s engine and TUI. It does **not** clone Claude Code's full-screen interface (its look, its key bindings beyond the documented few) in Phases 1–3; that is a separate decision (§14 Q2), and a TUI clone would raise trade-dress questions as well as cost.
4. **One binary, two front doors.** `ling-claude` is a second name for the installed `ling` (a link created by `CodexBrandedBuilder`, like `ling-search`), and the launcher switches on the compatibility profile when it is invoked under that name, or with `DREAMFERENCE_MIGHTLING_COMPAT=claude`, or `mightling_compat = "claude"`. Everything Mightling guarantees for `ling` (the vendor channels closed, `CODEX_HOME=~/.mightling`, the sandbox, `/airgapped`, the egress audit, Night Shift, the desktop app) holds unchanged because it is the same binary.
5. **Read, never write, the other agent's files.** `CLAUDE.md`, `.claude/settings*.json`, `.claude/commands`, `.claude/agents` and `~/.claude/…` are read at start and translated in memory or into `$CODEX_HOME`; nothing is ever written into the repository or into `~/.claude` (AGENTS.md's rule against writing the user's choices where a clone or the agent could write them).
6. **Measured before it is anyone's default.** The compatibility profile is opt-in. Its one model-visible change, the Claude-Code-shaped tool roster (§7.2), is an SWE-bench arm against `ling`'s default before any default changes (§11).

---

## 1. Goals and non-goals

### 1.1 Goals

1. A developer who uses Claude Code at work can use Mightling at home or offline **without relearning**: the same memory files, the same settings files, the same hooks, the same custom slash commands, skills and subagents, the same print-mode flags and output formats in scripts and CI.
2. A repository set up for Claude Code (a `CLAUDE.md`, a `.claude/` folder) works in Mightling as it is, with no conversion step and nothing written into it.
3. Everything runs against the local model server; no request ever goes to Anthropic or any other vendor. The egress audit proves it for the new front door as it does for `ling` (§8).
4. The local model gets the tool shapes it handles best, decided by measurement (§7, §11), not by imitation.
5. The work stays inside Mightling's architecture: launcher code in `ling-rs/`, at most one-line hooks in `codex-patches/` under the explicit cap, a runner entry in `ling-admin`, and offline tests.

### 1.2 Non-goals

- **Copying Claude Code.** No source, no system prompt, no tool descriptions, no UI assets, no names or wording from it beyond the public file and flag names a user types (§2).
- **Anthropic's services.** No sign-in, no API key helper that reaches Anthropic, no Anthropic model ids, no claude.ai features, no remote MCP connectors from Anthropic's directory, no GitHub App or hosted Actions runner (`claude-code-action`), no web or mobile session hand-off.
- **Telemetry, cost accounting in dollars, usage limits.** Mightling has none and adds none (`/usage` reports tokens on the local server only).
- **Its full-screen interface** in Phases 1–3 (§0.3, §14 Q2).
- **Marketplace plugins fetched from the internet** (Claude Code's plugin marketplaces). A plugin already on disk may be read; nothing is fetched (§8.3).
- **Bit-exact output schemas.** The JSON and stream-JSON output follows the fields the public documentation names (§6.4); undocumented fields are not guessed.
- **Model choice.** Mightling serves one model; `--model` and `/model` accept only what `/v1/models` lists.

---

## 2. Clean room and licensing

### 2.1 The rule

**Claude Code is proprietary software.** Its source code and its system prompts are **not** open source. Its GitHub repository's licence file reads "© Anthropic PBC. All rights reserved. Use is subject to Anthropic's Commercial Terms of Service", and its npm package refers to the same terms (§3.17); the repository holds documentation, examples and issue tracking, not an open-source licence for the agent.

Therefore:

1. **Sources allowed:** Anthropic's public documentation pages (cited in §3 and §15) and Anthropic's public announcements. Observable behaviour of an installed copy (what it prints, what files it reads and writes, what a flag does), recorded as behaviour and never as code, is allowed **only after the owner has confirmed that the terms the copy is used under permit it**: Anthropic's commercial and usage terms may restrict using its services to build a competing product (not checked for this spec; §14 Q11). Until then, the public documentation is the only source, and this spec was written from it alone.
2. **Sources forbidden:** Claude Code's source, minified or not; decompiled, deobfuscated or "leaked" copies of it; its system prompts or tool descriptions, however obtained (including from a model's output, a proxy capture or a debugging log); its images, icons, colours, animations, spinner words and other assets.
3. **Open-source projects derived from forbidden sources are forbidden too.** Several popular community projects were built from Claude Code's own code, recovered from source maps or from the 2026-03-31 leak (anon-kode, Kode, claw-code, OpenClaude, free-code and mirrors of the leak; §5.2), whatever licence they now claim. They are excluded from option (b) on purpose, and nobody working on this spec's implementation opens their code. A project that only *reads* Claude Code's documented files for compatibility (OpenCode, Crush) is not in this class.
4. **The implementation contains no copied code, prompts or assets.** Every tool description, every prompt block, every message the user sees is written fresh for Mightling, in Mightling's voice, and reviewed against this rule before merge. Names of files, folders, settings keys, hook event names, flag names and JSON field names that the public documentation gives users to type or parse are interface facts and are used as such (the same way `ling-rs/skills/` already reads `.claude/skills` and Codex's own upstream code reads `CLAUDE.md`, §4.2).
5. **One person, one direction.** If observation is allowed (item 1), whoever observes Claude Code's behaviour writes a behavioural note (inputs, outputs, files touched); the implementer works from that note and the public docs. Notes are kept in `docs/dev/ling-claude-observations.md` with the version observed and the date, so a later question about provenance has an answer.
6. **A review step per merge**: the pull request template for this work asks "every user-visible string and every model-visible string written for this change; no text copied from Claude Code; sources: …".

### 2.2 What the licences allow

| Component | Licence | Use here |
|---|---|---|
| Mightling | AGPL-3.0-or-later | the whole of `ling-claude` |
| Codex (the pinned `codex/` submodule) | Apache-2.0 | the engine, unchanged; its `external-agent-migration` crate is upstream Apache-2.0 code and may be called as it is |
| Claude Code | proprietary (Anthropic's terms) | nothing; behaviour and documented interfaces only |
| Option (b) candidates | see §5.2 | only an OSI-approved licence compatible with AGPL-3.0 (Apache-2.0, MIT) could be forked; source-available licences are excluded |

### 2.3 Interoperability, not imitation

Reading another tool's documented file formats and accepting its documented command-line flags is interoperability; it is what lets a user's existing `CLAUDE.md` and hook scripts work. The spec stops at the interface: the agent's behaviour behind it (how it plans, what its prompt says, what its tools are told) is Mightling's own.

### 2.4 The name

"Claude" and "Claude Code" are Anthropic's trademarks, and Anthropic's public legal page says their names may not be used "as part of your own product, feature, or company name … or in a way that suggests Anthropic built, endorses, or is partnered with your product" (§3.17). A shipped command, package or product name containing "Claude" is therefore a legal risk. **`ling-claude` stays a working name** until the owner chooses (§14 Q1). Proposed alternatives, none of which contains a third party's mark or collides with an existing `ling-*` binary (`ling-admin`, `ling-search`, `ling-fetch`, `ling-code`, `ling-docs`, `ling-signal`):

1. **`ling-compat`**: plain, says what it is (`ling` with compatibility for other agents' files and flags), and extends naturally if Gemini CLI's or another agent's formats are added later.
2. **`ling-cc`**: short, and familiar to those who alias Claude Code as `cc`; but because "CC" is commonly read as "Claude Code" it may still "suggest" the association the legal page rules out, so it is the riskiest of the three.
3. **`lingo`**: a separate word ("speaks the other agents' language"), no mark in it, easy to type; it must be checked for collisions with existing packages before it is chosen.

User-facing documentation may say "reads Claude Code's `CLAUDE.md` and settings files" (a nominative, factual use) without using the mark in the product's name.

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
| 13 | Built-in slash commands | most exist under Codex's names (`/init`, `/compact`, `/resume`, `/model`, `/permissions`, `/hooks`, `/mcp`, `/agents`, `/plan`, `/review`, `/export`, `/status`, `/statusline`, `/ide`, `/skills`) | aliases for `/cost` and `/context` (→ `/status`), `/add-dir`, `/doctor` (→ `ling-claude doctor`), `/memory` in Phase 3 | P1 |
| 14 | Custom commands, `.claude/commands/` | not read | converted into skills per session, Phase 1 (§9.4) | P0 |
| 15 | Skills, `.claude/skills/` | **done**: linked as `from-claude` and `from-repo-claude` (SKILLS §15) | exists | P0 (exists) |
| 16 | Plugins (`.claude-plugin/plugin.json`, marketplaces) | Codex's own plugins; `/import` can add marketplaces (§8.3) | local plugins only, Phase 3; no marketplace fetch | P2 |
| 17 | `CLAUDE.md` hierarchy, `CLAUDE.local.md`, `@path` imports | **not read** | Phase 1 (§9.1) | P0 |
| 18 | `.claude/rules/*.md` scoped to paths | none | Phase 3 | P2 |
| 19 | Auto memory, `/memory` | Codex's memories feature (off) | `/memory` Phase 3; auto memory not planned | P2 / — |
| 20 | MCP: `.mcp.json`, `~/.claude.json` servers, `mcp__server__tool` names | Codex MCP from `config.toml`, flattened to functions by `0020` | `.mcp.json` and `~/.claude.json` read, Phase 1 | P0 |
| 21 | Plan mode, `Shift+Tab`, plan approval | `/plan` (Codex's Plan mode) | `--permission-mode plan`; the key binding checked in Phase 0 | P0 |
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

### 4.2 What the pinned Codex already brings

Checked in `codex/codex-rs/` at `rust-v0.158.0` on 2026-10-19 (read only):

- **`/import`** (`tui/src/slash_command.rs`: "import setup, this project, and recent chats from Claude Code"), backed by the crate `external-agent-migration`: `ClaSource` reads `.claude/`, `CLAUDE.md` and `settings.json`; `hooks_cla.rs` converts Claude Code hook groups (`matcher`, `hooks`) into Codex hooks; `subagents.rs` converts `.claude/agents/*.md` into Codex agent roles (`agents/*.toml`); `command_migration.rs` (in `core-plugins`) turns custom commands into skills; `mcp.rs` converts MCP server entries; `sessions/` imports recent conversations; a `RewriteProfile` replaces "Claude"/"Claude Code" and `CLAUDE.md` with Codex's names in what it copies. It is **visible in `ling` today**, untouched by any Mightling patch, and not covered by the egress audit (§8.3). It is a **one-time conversion** into Codex's own formats, not live reading; the memory part is behind the feature `external_agent_memory_import` (under development, off).
- **Hooks** (`codex-rs/hooks/`): events `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt`; command hooks with JSON on stdin; exit code 2 as a block; JSON output with `decision: "block"`, `hookSpecificOutput.permissionDecision` (`deny` supported; `allow` and `ask` rejected as unsupported by `engine/output_parser.rs`), `additionalContext`; `hooks.json` discovered in config folders; hook trust by hash. Mightling already relies on them (refine's `UserPromptSubmit`, SWE-bench's `--hooks issue-v1`).
- **Project documents**: `AGENTS.md` from the repository root down to the working directory, plus `project_doc_fallback_filenames` (`core/src/agents_md.rs`), a list of other file names read in a folder **only when it has no `AGENTS.md`**; no `@path` import expansion.
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
- *Against:* everything `ling` gets from Codex has to be written again and kept secure: the TUI, sessions and resume, compaction (and the six compaction measures Mightling has tuned since 2026-10-02), MCP, skills loading, subagents, plan mode, the hook engine, the app-server protocol the desktop app speaks, and above all **the sandbox**. `ling-admin`'s own sandbox (`SandboxManager`) was removed on 2026-10-01; `/airgapped on` is enforced inside Codex's sandbox helper (patch `0019`) and on Windows by patch `0024`; bwrap needs the AppArmor profile (`docs/dev/host-safety.md`). A new binary would need its own `bwrap` policy, its own network-namespace enforcement, its own `audit egress --claude` trace, its own SWE-bench runtime (another patchelf'd binary per container) and its own desktop adapter. Months of work before the first parity row, and a second security surface for good.

**(b) Fork or extend an open-source agent** with a compatible licence (§5.2).

- *For:* a working loop and interface on day one; several candidates already read `CLAUDE.md` or `AGENTS.md`, speak MCP and take an OpenAI-compatible endpoint; Qwen Code is tuned for the Qwen family's tool calls.
- *Against:* the strongest candidates are TypeScript/Node or Go, not Rust, so none of `ling-rs`'s crates is reused directly; every one has its own sandbox story (Docker/Podman, Seatbelt, or none) that would have to be replaced with bwrap and `/airgapped on`; several carry telemetry or update checks on by default that must be found and closed the way patches `0013`, `0015`, `0016` closed Codex's (a new egress audit and a new patch series to rebase on every upstream release); and Mightling would then maintain two agents, two patch series and two session formats. Night Shift, SWE-bench and the desktop app each need an adapter.

**(c) Extend `ling` (Codex) with a compatibility layer** in `ling-rs/`: read `CLAUDE.md` and `.claude/` live, translate hooks, permission rules, commands and subagents, accept Claude Code's print-mode flags, and offer a Claude-Code-shaped tool roster as a measured option.

- *For:* the engine already has most of the feature set (§4.2); the sandbox, `/airgapped`, the egress audit, the vendor channels closed, the compaction work, Night Shift, the desktop app, the code index, `ling-docs` and the SWE-bench harness all keep working because it is the same binary; Phase 1 needs no patch, so the 45 KB cap is untouched; the work is small enough to measure in days.
- *Against:* the interface stays Codex's, not Claude Code's (§0.3); some semantics cannot be mapped exactly (permission rules over paths, hook `allow`/`ask` decisions that Codex rejects, `acceptEdits`), so the layer must say clearly where it differs; and every Codex bump must re-check the translation (a test pins it, §13).

### 5.2 The open-source agents that already clone parts of it

Checked on 2026-10-19 (licence from each repository's licence file, activity from its last push):

| Agent | Repository | Language | Licence | Status | How much of Claude Code's way of working |
|---|---|---|---|---|---|
| OpenCode | [anomalyco/opencode](https://github.com/anomalyco/opencode) (`sst/opencode` redirects) | TypeScript | MIT | active | the closest: `AGENTS.md` with `CLAUDE.md` and `~/.claude/CLAUDE.md` as fallbacks ([rules](https://opencode.ai/docs/rules/)), skills including `.claude/skills/` ([skills](https://opencode.ai/docs/skills/)), a read-only plan agent, subagents, `.opencode/commands/`, plugin hooks ([plugins](https://opencode.ai/docs/plugins/)), `opencode run --format json`; any OpenAI-compatible endpoint |
| Kilo Code | [Kilo-Org/kilocode](https://github.com/Kilo-Org/kilocode) | TypeScript | MIT | active | its CLI is a fork of OpenCode |
| Crush | [charmbracelet/crush](https://github.com/charmbracelet/crush) | Go | **FSL-1.1-MIT** (source-available; bars competing use until it converts to MIT) | active | `CRUSH.md`/`AGENTS.md`, skills, MCP, early hooks; custom providers. Excluded from (b) by its licence |
| opencode (Go) | [opencode-ai/opencode](https://github.com/opencode-ai/opencode) | Go | MIT | archived 2025-09, became Crush | historical |
| Qwen Code | [QwenLM/qwen-code](https://github.com/QwenLM/qwen-code) | TypeScript | Apache-2.0 | active | began from Gemini CLI; subagents, skills, hooks, MCP, plan mode, `qwen -p`; built for the Qwen family's tool calls; vLLM and Ollama endpoints |
| Gemini CLI | [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | TypeScript | Apache-2.0 | active | hooks, skills, subagents, plan mode, MCP, custom commands, `--output-format json\|stream-json`; `GEMINI.md`; a local OpenAI-compatible endpoint is *unverified* |
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
~/.local/bin/ling-claude ──► ~/.local/share/dreamference/mightling/bin/ling   (same binary as ling)
                                   │
                                   ▼
         ling-rs/src/lib.rs (the launcher, before Codex parses its arguments)
           ├── compat::profile()          argv[0] = ling-claude, or DREAMFERENCE_MIGHTLING_COMPAT=claude,
           │                              or mightling_compat = "claude"
           ├── compat::cli                Claude-Code-style flags  →  Codex flags / `exec` / `resume`
           ├── compat::memory             CLAUDE.md hierarchy + @imports  →  a SessionStart hook's context
           ├── compat::settings           .claude/settings*.json  →  -c overrides, exec-policy rules,
           │                              translated hooks (in $CODEX_HOME, never in the repository)
           ├── compat::commands           .claude/commands/*.md  →  skills linked like from-claude
           ├── compat::agents             .claude/agents/*.md  →  agent roles under $CODEX_HOME
           ├── compat::output             Codex exec JSONL  →  text | json | stream-json
           └── ling-claude tools (MCP)    optional Read/Write/Edit/Grep/Glob/WebFetch/WebSearch
                                          as plain functions (patch 0020 flattens MCP tools)
                                   │
                                   ▼
         Codex, unchanged (TUI, exec, sandbox, hooks engine, compaction, app-server)
                                   │
                                   ▼
         the model server: SGLang (Qwen3.8-27B, qwen3_coder tool parser) or ling-engine
```

A leaf crate, `ling-rs/compat/`, holds the parsers and translators (pure functions, unit-tested without a model, as `ling-rs/skills/` is); `ling-rs/src/compat.rs` wires them into the launcher. The new MCP server is a subcommand of the same binary (`ling-claude tools serve`, intercepted by the launcher like `ling apps serve`).

### 6.2 What happens at start

1. The launcher decides the profile (§0.4). Without it, `ling` behaves exactly as today: every compatibility path is gated on the profile, and a test asserts that a plain `ling` start writes the same `config.toml` and catalog as before (§13).
2. It resolves the model server and reads `/v1/models` as it does for `ling`.
3. It finds the project root (git top level, as Codex does) and reads, in this order, never writing any of them: managed settings if present, `~/.claude/settings.json`, `.claude/settings.json`, `.claude/settings.local.json` (precedence as documented, §3.14), plus `~/.claude/CLAUDE.md`, the project's `CLAUDE.md` files and `CLAUDE.local.md` (§9).
4. It translates what it read into Codex's terms for **this session only**: `-c` overrides on the command line, a session folder `$CODEX_HOME/compat/claude/<project-hash>/` for the translated hooks, rules and agent roles, and links for commands (§9.4). The folder is rebuilt at every start from the source files, so an edit to `.claude/settings.json` is seen at the next session.
5. It prints one start-up line naming what it read (`ling-claude: CLAUDE.md (2), .claude/settings.json (hooks 3, rules 7), commands 4, agents 2; differences: …`), and the full report under `ling-claude doctor`.
6. It runs Codex with the translated arguments.

### 6.3 The command line

The launcher accepts the print-mode flags the public CLI reference documents (§3.9) and maps them; anything it cannot honour is refused with a message, never silently ignored (the `ling-admin` rule that every command rejects unknown arguments).

| Claude Code (documented) | `ling-claude` maps it to |
|---|---|
| `ling-claude` (no prompt) | the `ling` TUI with the profile on |
| `ling-claude "prompt"` | the TUI with a first prompt |
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

Session ids are Codex's; `ling-claude -r` accepts them and prints them in `json` output as `session_id`.

### 6.4 Output formats

`compat::output` reads `ling exec --json`'s JSON Lines and writes:

- **`text`**: the last agent message only (what `exec -o` writes).
- **`json`**: one result object at the end with the fields the public docs list (§3.9): `type`, `subtype`, `session_id`, `duration_ms`, `duration_api_ms` (the time spent waiting on the model server), `is_error`, `num_turns`, `result`, `stop_reason`, `usage` (tokens from the local server), `permission_denials`, `structured_output` when `--json-schema` was given, and `total_cost_usd` as `0`, since nothing is billed. Fields the docs do not list are not invented.
- **`stream-json`**: one JSON object per line as events happen: a `system` message of subtype `init` (session id, working folder, the tools offered), assistant and user messages with tool calls and their results, and the final `result` object, using the documented message types only.

The exact field list is pinned by fixtures written from the public reference (§13); the spec does not reproduce it here because the documentation, not this file, is the authority, and it changes.

---

## 7. The model side

### 7.1 What the server can do

- **Tool calls on SGLang** go through the `qwen3_coder` tool parser with the `qwen3` reasoning parser (registry entry `qwen3.8-27b-nvfp4-dflash2`, [AGENTS §7](./DREAMFERENCE_AGENTS.md), [MODELS](./DREAMFERENCE_MODELS.md)). The chat template is patched at launch so Codex's reasoning efforts and a second system message are accepted (`ChatTemplatePatcher`).
- **Only plain function tools reach the model.** SGLang and vLLM silently drop Codex's `custom` (freeform) and `namespace` tool types. That is why patch `0020` flattens MCP tools into functions, why `apply_patch` is offered freeform only on ling-engine, and why Code Mode's `exec` is translated into a function on SGLang (`ling-rs/tools/src/custom.rs`). Any tool `ling-claude` adds is a plain JSON-schema function, served over MCP so that `0020` presents it, and must stay within the parser's handling (no deeply nested `anyOf`, string-typed free text).
- **ling-engine** (`owned_by: ling-engine` on `/v1/models`) runs custom tools; `ServedModel::runs_custom_tools` stays the one gate, and the compatibility layer does not add a second.

### 7.2 The tool roster

Rosters chosen per session by `DREAMFERENCE_MIGHTLING_CLAUDE_TOOLS=codex|discrete|discrete-search` (default `codex` until §11 decides):

| Claude Code's tool (documented name, §3.1) | `codex` roster (today's `ling`) | `discrete` roster |
|---|---|---|
| Bash | Codex's shell (`exec_command`, sandboxed) | the same shell |
| Read | `cat`/`sed -n` in the shell | `Read` (path, offset, limit; numbered lines), from `ling-claude tools` |
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

**Where the discrete tools run, and the sandbox.** MCP servers started by Codex are not run inside the command sandbox (as read in the pinned source; Phase 0 confirms it on this host), so `ling-claude tools` confines itself: writes only under the session's writable roots (the workspace, `--add-dir`), reads refused for paths a deny rule names (§9.3), no network of its own (`WebFetch`/`WebSearch` run `ling-fetch`/`ling-search`, which already follow `/airgapped`, [AIRGAPPED §4](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md)), and the server process itself started under bwrap with the network unshared when `/airgapped on`. Edits through `Edit`/`Write` must still show as diffs in the TUI and the desktop app: Phase 2 checks whether Codex's turn-diff tracker sees them (it tracks `apply_patch`); if it does not, `Edit` is implemented as a translation to `apply_patch` inside the launcher's request hook (the way `custom.rs` translates `exec`), which keeps Codex's approval and diff paths. This choice is Phase 2's first measurement (§12).

### 7.3 Context limits

- The model's window is **262,144 tokens** (`max_model_len`), but SGLang's KV pool is **about 157K tokens shared by every stream** ([COMPACTION §1](./DREAMFERENCE_MIGHTLING_COMPACTION.md)). The launcher already sets the interactive compaction limit to 60% of the pool (~94K) and turns on turn-end compaction at 20% of the usable window with an 800-word summary cap (1.6.3). `ling-claude` inherits all of it unchanged.
- **Memory files cost context on every request.** Codex caps project documents at `project_doc_max_bytes` (32 KiB); the compatibility layer applies one budget to `AGENTS.md` plus the `CLAUDE.md` hierarchy together (default the same 32 KiB, the larger files truncated first with a notice), because a 27B model's useful context is smaller than its window.
- **The discrete roster adds schema tokens.** Five tool schemas (seven with `discrete-search`) are measured once (Phase 2) and counted in the SWE-bench report's token column; [CONTEXT_BUDGET](./DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md) found the index's tool schemas were 23% of unattended context, so this is not free.
- **`/compact <instructions>`** (Claude Code's focused compaction) maps to Codex's `/compact` with the text passed to the compaction prompt; whether the pinned Codex accepts an argument is checked in Phase 1, and if not, the launcher's turn-end compaction hook carries the focus ([BACKLOG](./DREAMFERENCE_BACKLOG.md) item 12 already proposes a context meter with `/compact <focus>`).
- **Thinking**: Claude Code's "think" keywords have no meaning for a local model with one reasoning setting; they are passed through as text.

---

## 8. Sandbox, air gap and egress

### 8.1 Unchanged guarantees

Because `ling-claude` is the same binary:

- Commands run in Codex's sandbox (bwrap on Linux, which needs the AppArmor profile `ling-admin host setup` installs; Seatbelt on macOS; the elevated sandbox on Windows).
- `/airgapped on` gives every sandboxed command an empty network namespace (patch `0019`) and is refused together with Full Access; it applies to `ling-claude` sessions with no new code. `bypassPermissions` maps to Full Access and is therefore refused at `on` (§9.3).
- The vendor channels stay closed (patches `0013`, `0015`, `0016`, the forced `chatgpt_base_url`), and nothing in the layer adds a channel to Anthropic: there is no Anthropic endpoint, model id, key helper or sign-in anywhere in the code, and a test greps the compatibility crate for Anthropic hosts (§13).

### 8.2 The egress audit

`ling-admin audit egress` gains `--claude`: the same strace method ([EGRESS §3.1](./DREAMFERENCE_MIGHTLING_EGRESS.md)) around `ling-claude -p "<prompt>" --output-format json` in a throwaway repository that contains a `CLAUDE.md`, a `.claude/settings.json` with one hook and one permission rule, a `.claude/commands/` file and a `.claude/agents/` file, so every translation path runs inside the trace. With `--tools discrete` it also starts `ling-claude tools`. The verdict is the existing one (only allowlisted loopback ports, no DNS query, no networked git). `codex build` runs the `--claude` form after `--tui`, and the JSON record is `<timestamp>-claude.json`.

### 8.3 `/import` and plugins

`/import` is in `ling` today and unaudited. Its plugin step reads Claude Code's known-marketplaces file and can add a marketplace whose source is a GitHub repository (`OFFICIAL_MARKETPLACE_SOURCE = "anthropics/claude-plugins-official"` in `source_cla.rs`). Phase 0 traces `/import` on a home with a Claude Code setup and either (i) confirms it fetches nothing, or (ii) adds a one-line hook that skips non-local marketplace sources (Codex already has `is_local_marketplace_source`), counted against the cap with the owner's approval. The compatibility layer itself never fetches a plugin (§1.2).

### 8.4 Hooks are code from the repository

A repository's `.claude/settings.json` can name hook commands, which run on the user's machine. Claude Code's own documentation treats them as the user's responsibility; Mightling is stricter:

- translated hooks from a **repository** are registered only after the user trusts the project (Codex's trust prompt) and then go through Codex's hook trust (`/hooks`), by hash, so a changed command needs approving again;
- hooks from `~/.claude/settings.json` are the user's own and are trusted like Codex's own `hooks.json` in `$CODEX_HOME`;
- hooks run outside the command sandbox, as Codex's own hooks do (Phase 0 confirms this at the pin); at `/airgapped on` the start-up line says so, and `ling-claude doctor` lists every hook command.

---

## 9. Configuration and memory files

### 9.1 Memory: `CLAUDE.md` read live, beside `AGENTS.md`

**Yes, `ling-claude` reads `CLAUDE.md`**, with the hierarchy the public memory page documents (§3.5): managed (`/etc/claude-code/CLAUDE.md`, if present), the user's `~/.claude/CLAUDE.md`, the project's `CLAUDE.md` (or `.claude/CLAUDE.md`) in the working directory and every ancestor, root first, `CLAUDE.local.md`, files in subfolders when the agent first works there, and `@path` imports relative to the importing file, at most four hops, ignored inside code.

**How, without a patch:**

- Codex keeps reading `AGENTS.md` itself (root to working directory, plus `$CODEX_HOME/AGENTS.md`). That stays Mightling's canonical project document.
- The launcher composes the `CLAUDE.md` hierarchy, with imports expanded, and hands it to the session through a built-in **`SessionStart` hook** (`ling-claude memory`, a subcommand of the same binary) whose `additionalContext` carries the text. Hooks support `additionalContext` at the pinned tag (§4.2), the hook is registered by the launcher with its trust entry the way `SweBenchHooks` registers the benchmark's, and no patch is needed.
- **Deduplication:** files are keyed by canonical path. A `CLAUDE.md` that only imports `AGENTS.md` (this repository's case) adds nothing; an `AGENTS.md` that imports `CLAUDE.md` is handled the same way; two different files in one folder are both read, `AGENTS.md` first.
- **A deliberate difference from Claude Code**, which reads `AGENTS.md` only when no `CLAUDE.md` exists (§3.5): in Mightling `AGENTS.md` is the canonical project document and is always read, because `ling`, Night Shift and SWE-bench depend on it. A repository with both is read with both; §14 Q10 asks whether a setting should restore Claude Code's rule.
- **Subfolder files** are read when the agent first touches a path under them: a `PostToolUse` hook on reads and edits notices the new folder and injects its `CLAUDE.md` as context once per session.
- **Budget:** one budget for both families (§7.3).
- **Outside the profile**, plain `ling` gains one small, separate option: `mightling_claude_md = true` adds `CLAUDE.md` to `project_doc_fallback_filenames`, so a repository with only a `CLAUDE.md` gives `ling` its instructions (a folder with an `AGENTS.md` keeps reading only that, which is Codex's rule). It is off by default and costs one `toml_edit` line in the launcher.

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
| `env` | set in the session's environment (not the sandbox's network settings; `HTTP_PROXY` and similar are refused at `/airgapped on`) | 1 |
| `model` | accepted if served, else a notice | 1 |
| `apiKeyHelper`, Anthropic credentials, `forceLoginMethod` and similar | ignored with a notice ("no vendor sign-in in Mightling") | 1 |
| `statusLine` | Codex's `/statusline` items where one matches; a command status line is P2 | 3 |
| `outputStyle` | P2 (a named Mightling prompt, [PROMPT](./DREAMFERENCE_MIGHTLING_PROMPT.md), is the nearest) | — |
| `enabledPlugins`, marketplaces | local plugins only (§8.3) | 3 |
| anything else | listed by `ling-claude doctor` as not supported | 1 |

Mightling's own settings stay where they are (`dreamference.toml`, `~/.config/dreamference/config.toml`, `$CODEX_HOME/config.toml`). When a Mightling setting and a Claude Code setting disagree, **Mightling's safety settings win** (the `/airgapped` rule "the strictest wins" applies: a `.claude/settings.json` cannot loosen `/airgapped`, the sandbox mode set by `ling-admin`, or the approval policy below what Mightling's config sets).

### 9.3 Permissions

| Claude Code (documented, §3.2) | `ling-claude` |
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
| `Read(path)`, `Edit(path)`, `Write(path)` (gitignore syntax) | a `PreToolUse` hook (`ling-claude guard`) that denies or asks on matching paths, for `apply_patch` and the discrete tools; shell reads of a denied path cannot be matched reliably and the doc says so |
| `Agent(name)`, `Skill(name *)` | the guard hook on Codex's subagent tool; skills: the catalogue left out of the session |
| protected paths (`.git`, `.claude/`) | never approved automatically: the guard asks |
| `WebFetch(domain:…)` | passed to `ling-fetch` as an allow-list (a flag to add in `ling-web-rs/`) |
| `mcp__server__tool` | Codex's MCP tool approval settings |
| `additionalDirectories` | writable roots |

The mapping is not exact (§14 Q4): where Claude Code would *allow* without asking and Codex's model cannot express it (a `PreToolUse` hook may not return `allow` at the pinned tag), `ling-claude` asks, which is the safe direction. Claude Code's own rule that a hook's `allow` never overrides a deny or ask rule holds trivially.

### 9.4 Commands, skills and subagents

- **Skills**: already done. `~/.claude/skills` and a trusted repository's `.claude/skills` are linked into `ling` today ([SKILLS §15](./DREAMFERENCE_MIGHTLING_SKILLS.md)); the profile changes nothing there.
- **Custom slash commands** (`.claude/commands/*.md`, `~/.claude/commands/*.md`): Claude Code's docs now say custom commands "have been merged into skills" (§3.4), so converting them into skills is the documented model, not an approximation. They are converted at start into skills in the session folder and linked like `from-claude` skills, using the conversion Codex's own `command_migration.rs` performs, so `/name args` works. Substitutions (`$ARGUMENTS`, `$N`, named arguments, `${CLAUDE_SKILL_DIR}`, `${CLAUDE_SESSION_ID}`) are expanded by the launcher when the skill is invoked; `` !`cmd` `` runs the command in the sandbox, never outside it. `allowed-tools`/`disallowed-tools` become session permission rules while the skill runs where they can be expressed, otherwise a notice; `context: fork` and `agent` run the skill in a subagent (Phase 3).
- **Subagents** (`.claude/agents/*.md`, `~/.claude/agents/*.md`, in the documented priority: `name`, `description`, `tools`, `disallowedTools`, `permissionMode`, `maxTurns`, `skills`, `mcpServers`, `hooks`, and the body as the subagent's instructions): converted into Codex agent roles (`agents/*.toml`) in the session folder, through `external-agent-migration`'s `import_subagents_with_rewrite_profile`, called with a rewrite profile that changes nothing (Mightling wants the user's text as written). `model` is ignored (one model).

### 9.5 Hooks

Translated at start from every settings file into Codex hook groups in the session folder:

- **Events**: the core names are the same (§4.2): `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, `Stop`, `SessionEnd`. Claude Code documents over twenty more (`PostToolUseFailure`, `PermissionDenied`, `Notification`, `InstructionsLoaded`, `CwdChanged`, `FileChanged`, `WorktreeCreate`, …, §3.3) that the pinned Codex lacks; `doctor` lists each one skipped. `PostToolUseFailure` can be emulated from `PostToolUse` by the shim (a failed result) and is the one worth doing in Phase 2.
- **Handler types**: `command` in Phase 1; `mcp_tool` and `http` to a loopback address in Phase 3 (an `http` hook to any other address is refused at `/airgapped on` and named by the egress audit when off); `prompt` and `agent` hooks, which call the model, are P2.
- **Matchers**: Claude Code's tool names are mapped onto Codex's: `Bash` → the shell tools, `Edit`/`Write`/`MultiEdit` → `apply_patch` (and the discrete `Edit`/`Write`), `Read`/`Grep`/`Glob`/`WebFetch`/`WebSearch` → the discrete tools when that roster is on, and to the shell otherwise with a notice that a shell command cannot be matched by tool name; `mcp__<server>__<tool>` → the flattened MCP name `0020` gives the tool.
- **Inputs and outputs**: each translated command runs through `ling-claude hook-shim -- <the user's command>`, which rewrites Codex's stdin JSON into the shape the public hooks reference documents (`tool_name` in Claude Code's names, `tool_input` with its documented fields such as `command` or `file_path`, the session and transcript fields) and passes the output back. Exit code 2 and `decision`/`hookSpecificOutput` already mean the same thing; a `permissionDecision` of `allow`, `ask` or `defer`, which Codex rejects, is turned into "no decision" (Codex's own approval then applies) by the shim and reported once; `updatedInput` is passed only where Codex accepts it, and the shim translates its fields back into Codex's tool input.
- **Environment**: the documented project-directory variable is set to the project root.

---

## 10. Integration

### 10.1 As a runner

`ling-claude` is not a new runner pair, because it is not a new program: `config.agent_runner` keeps `codex` and the profile is a setting of it. `ling-admin` gains:

- `ling-admin run "…" --agent ling-claude`: a fifth choice in the strategy switch, implemented by `CodexRunner` with `DREAMFERENCE_MIGHTLING_COMPAT=claude` in the environment (no new installer: `CodexInstaller` resolves the same binary; the new link is created by `CodexBrandedBuilder` beside `ling-search`).
- `ling-admin codex build` creates `~/.local/bin/ling-claude` and runs `audit egress --claude` (§8.2).
- `DREAMFERENCE_MIGHTLING_COMPAT`, `mightling_compat`, `DREAMFERENCE_MIGHTLING_CLAUDE_TOOLS` and `mightling_claude_md` documented in [CLI](./DREAMFERENCE_CLI.md).

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

- **`swe-bench run --compat claude`** sets `DREAMFERENCE_MIGHTLING_COMPAT=claude` for every session of the run (the runner's extra environment of each container); **`--claude-tools codex|discrete|discrete-search`** sets `DREAMFERENCE_MIGHTLING_CLAUDE_TOOLS`. With no flag neither variable is set and the launcher decides as for every user. The manifest records `compat` and `claude_tools` (`auto` when absent; a manifest from before the options reads as `auto`); `report` prints them and `report --against` lists them when they differ.
- **The instances carry no `CLAUDE.md`**, so the profile alone changes only the command line and the output; the arm that matters is **`--compat claude --claude-tools discrete`** against the record arm (`default`). A second pair adds a generated `CLAUDE.md` per instance (the same text the record arm gets as its task preamble, moved into the memory file) to check that memory delivered through the `SessionStart` hook scores the same as through the prompt.
- **The list**: the night-2 list without mismatched tasks (`docs/dev/swe-bench/night2-no-mismatch.txt`, 44 tasks), record arm first, as on every night; McNemar exact, the report's verdict. Columns: resolved, tokens (the roster's schema tokens counted), tool calls by name, edits by tool, compactions, wall time.
- **Decision rule** (proposed for the owner to set before the night): a clear win for `discrete` makes it the profile's default and opens the question for `ling`'s default; a tie keeps `codex` (fewer schema tokens, one less server); a loss keeps `codex` and the discrete roster stays an option.
- **The container**: the same patchelf'd `ling` (the link `ling-claude` is created in the container, or the variable set); `ling-claude tools` runs inside the container as the agent's MCP server, so it needs nothing outside it. Under (a)/(b) the harness would have needed a second runtime per container.
- **Tests**: as for `--apply-patch` and `--exec-tool`: the variables are set only when the flags are, the manifest and the report tell the arms apart, a call is counted for the discrete `Edit` and not for a shell command naming it.

---

## 12. Phases and milestones

| Phase | What | Exit criterion |
|---|---|---|
| **0. Checks** (1–2 days) | Trace `/import` for network (§8.3); confirm `-c` overrides can register hooks with a trust identity and an `additionalContext` `SessionStart` hook reaches the model on SGLang; confirm `/compact` takes an argument; confirm whether Codex's turn-diff tracker sees MCP edits; write `docs/dev/ling-claude-observations.md` from the public docs | a page of findings appended here as §16; the owner picks the name (Q1) |
| **1. Files and the command line** | `ling-rs/compat/` and `src/compat.rs`; the `ling-claude` link; `CLAUDE.md` hierarchy with imports through the `SessionStart` hook; `mightling_claude_md` for plain `ling`; settings read with `env`, `model`, hooks (with the shim) and the ignored keys; commands and agents converted; `-p`, `--output-format`, `-c`, `-r`, `--max-turns`, `--append-system-prompt`, `--add-dir`, `--model`; `ling-claude doctor`; `audit egress --claude` | offline tests green; the audit passes; a repository with only `CLAUDE.md` and a `.claude/settings.json` hook works in `ling-claude -p` and in the TUI |
| **2. Permissions and the tool roster** | `--permission-mode`, `--allowedTools`/`--disallowedTools`, permission rules (exec-policy rules and `ling-claude guard`); `ling-claude tools` with the discrete roster; `--input-format stream-json`; SWE-bench options | the SWE-bench night of §11 run and reported |
| **3. Comfort** | `/memory` (one hook, counted against the patch cap with the owner's approval); `.claude/rules/`; `mcp_tool` and loopback `http` hooks; skills with `context: fork`; the desktop setting; `/night add --compat claude`; status line and local plugins; documentation in `docs/` ("Coming from Claude Code") | the desktop app and Night Shift run a profile session end to end on this machine |
| **4. Optional** | rewind of file changes (checkpoints; a patch or a launcher-side snapshot of the worktree per turn), a Claude-Code-like full-screen interface | each only by a separate owner decision |

Milestone for a release note: Phase 1 can ship as a preview in the next minor release, off unless invoked as `ling-claude`.

---

## 13. Tests

**Tests never touch the real machine.** `tests/conftest.py` already gives every test its own `HOME` and fails any real `docker` command that changes something, any spawn of the installed `ling` or anything under `~/.local/bin`, and any connection to the model server's ports (8000, 18000). The new tests follow it:

- **Rust, offline** (`ling-rs/compat/`, `cargo test` in a copy of the folder as for `ling-rs/skills/`; the launcher's in the build's export directory, never in `codex/`): memory discovery with fixtures under a temporary home (hierarchy, `CLAUDE.local.md`, `@path` imports, the depth limit, a cycle, a symlink out of the tree refused, deduplication with `AGENTS.md`, this repository's `@AGENTS.md` case, the budget); settings precedence and every translated key; the hook shim's stdin and stdout rewriting against fixtures written from the public hooks reference; matcher mapping; permission rules into exec-policy rules; the `guard` hook's decisions; commands and agents conversion; every CLI flag mapped or refused; the output translator against recorded `exec --json` streams (no model); a grep that the crate contains no Anthropic host name, key variable or model id.
- **The profile is opt-in**: a plain `ling` launch in a fixture home writes byte-for-byte the same `config.toml` and catalog with the compatibility code present as without it.
- **Nothing written outside `$CODEX_HOME`**: a test runs the start-up translation on a fixture repository with a full `.claude/` and asserts the repository and the fixture `~/.claude` are unchanged (mtime and content).
- **Python, offline** (`tests/`): `ling-admin run --agent ling-claude` sets the variable and runs `CodexRunner` against a `FAKE_MIGHTLING`-style stand-in; `codex build` creates the link (in the test's home) and calls the audit's `--claude` form (mocked); `audit egress --claude` builds its fixture repository and parses a recorded trace; SWE-bench's `--compat` and `--claude-tools` options (as §22–§23's tests do); Night Shift's `--compat` passes the variable to the stand-in.
- **Patch size**: `test_the_patches_stay_small` is unchanged; Phase 1 adds no patch.
- **Live, marked `installed_binary`** (like `test_mightling_slash_commands.py`, run only by hand against a built binary and a running server): `ling-claude -p "…" --output-format json` in a fixture repository whose `CLAUDE.md` names a word the answer must contain; a `PreToolUse` hook from `.claude/settings.json` that blocks a command; `-c` continuing the last session.
- **Codex bump**: a test reads the pinned Codex's hook event names and `HookEventName` list from the export and fails if the translation table names an event that no longer exists.

---

## 14. Open questions

1. **The product name.** "Claude" is Anthropic's trademark; a shipped name containing it is a legal risk. `ling-claude` stays a working name. Alternatives: `ling-compat`, `ling-cc` (shorter, but read as "Claude Code"), `lingo` (§2.4). The owner chooses before Phase 1 ships.
2. **Is the feature set enough, or must the interface look like Claude Code's too?** This spec delivers the files, flags and features inside `ling`'s TUI (§0.3). A Claude-Code-like full-screen interface would be Phase 4 at the earliest, a large piece of work (and a trade-dress question): does the owner want it at all?
3. **Should plain `ling` read `CLAUDE.md` by default** (`mightling_claude_md = true`), given many repositories now carry one? Proposed: off in Phase 1, decided after a release with it available.
4. **Permission semantics that do not map** (path rules for shell reads, `allow`/`ask` decisions from hooks, `acceptEdits`): is "ask in doubt" acceptable, or should a small patch let a `PreToolUse` hook allow (counted against the cap)?
5. **`/import` today**: trace it in Phase 0 and, if it fetches, hide it or confine it to local sources with a one-line hook?
6. **A comparison night with Qwen Code** (Apache-2.0, built for the Qwen family's tools) as a `qwen-code` runner pair: worth one night to learn whether a different loop, not only a different roster, helps the 27B model? It would need its own sandbox and egress check for that night only.
7. **Rewind of file changes**: Claude Code's checkpoints are a frequently used feature with no equivalent in the pinned Codex. Launcher-side snapshots (a `git stash create` per turn into a hidden ref), a patch, or wait for upstream?
8. **Managed settings** (an organisation's policy file): read it if present (strictest wins) or ignore it on a single-user machine?
9. **Documentation drift**: the docs read on 2026-10-19 describe Claude Code 2.1.2xx–2.1.303 and change often (the subagent tool was renamed from `Task` to `Agent`, `TodoWrite` gave way to the `Task*` tools, custom commands merged into skills). Who re-checks §3 and the output fixtures, and how often (a line in the Codex-bump checklist)?
10. **`AGENTS.md` and `CLAUDE.md` together.** Claude Code reads `AGENTS.md` only when there is no `CLAUDE.md`; `ling-claude` reads both, `AGENTS.md` first (§9.1). Keep that, or offer a setting for Claude Code's rule? And which of the two should `/memory` and `/init` write when both exist?
11. **Observing a running copy.** Do the terms under which a Claude Code copy would be used permit observing its behaviour to build an interoperable alternative (§2.1 item 1)? Until a lawyer or the owner says yes, the work uses the public documentation only.

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
