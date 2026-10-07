# Puffin Skills — skills from the upstream vendor, Claude, Gemini, OpenClaw and Hermes

**Status:** Phases 1 and 2 implemented on 2026-10-02: the crate `puffin-rs/skills/` and the launcher module `puffin-rs/src/skills.rs`. Phase 3 (ClawHub and Hermes as install sources, a trusted repository's `.claude/skills` and `.gemini/skills`) implemented on 2026-10-03 (§15.6). §15 records what was built, where it departs from the design below, and what was measured; the sections before it are the design as specified, with §2 being what was measured on 2026-10-01 before any code.
**Goal:** a skill written for Codex, Claude Code, Gemini CLI, OpenClaw or Hermes Agent can be installed into `puffin` with one command and used by the local model, without the user knowing which ecosystem it came from.
**Short answer:** the file format is already shared. All five consume the [Agent Skills](https://agentskills.io/specification) `SKILL.md` (Hermes and OpenClaw add fields under `metadata`; Claude Code adds top-level fields). Every dialect loads today when its files are where `puffin` looks: a Claude-style and a Hermes-style skill were discovered, read and used in a live session, and so was a real skill from Anthropic's catalogue (§2.2). `~/.claude/skills`, `~/.gemini/skills`, `~/.hermes/skills` and `~/.openclaw/skills` are not where it looks (§2.3). What differs between the ecosystems, and what this spec designs, is four things: **where** each keeps skills on disk, what each one's **extra frontmatter** means, which **tool names** the instruction bodies assume, and how skills are **installed**.
**Target:** the `puffin` terminal agent. The web chat (Onyx) has no skills and is not covered.
**Builds on:**
- Codex's skill loader in the pinned source (`codex-rs/skills`, `codex-rs/ext/skills`), unmodified ([PUFFIN_CODEX](./DREAMFERENCE_PUFFIN_CODEX.md));
- the launcher crate `puffin-rs/`, which already intercepts subcommands before Codex parses them (`puffin night`, `puffin airgapped`), writes the model's prompt block into `model_catalog.json` and edits `config.toml`;
- `/airgapped` ([PUFFIN_AIRGAPPED](./DREAMFERENCE_PUFFIN_AIRGAPPED.md)): installing a skill is a network action and follows the level;
- commit `77b9471`, which made `$CODEX_HOME/skills` writable inside the workspace-write sandbox so Codex's built-in `skill-installer` works.

**No Codex patch.** The series stands at 31,175 of 31,500 bytes after `0019`; everything here is launcher code, configuration and files on disk. A `/skill` slash command would cost a patch and is not proposed (§10).

---

## 1. The five ecosystems

Read from each project's documentation on 2026-10-01, except the Codex column, which is read from the pinned source. "Standard" means the Agent Skills specification: a folder with `SKILL.md`, YAML frontmatter with `name` (≤64 characters, lowercase and hyphens, equal to the folder name) and `description` (≤1,024 characters), optional `license`, `compatibility`, `metadata` and the experimental `allowed-tools`, and optional `scripts/`, `references/`, `assets/`.

| | Codex (the upstream vendor) | Claude Code (Anthropic) | Gemini CLI (Google) | OpenClaw | Hermes Agent (Nous) |
|---|---|---|---|---|---|
| Follows the standard | yes | yes (Anthropic wrote it) | yes, by its own statement | yes ("AgentSkills spec") | yes, by its own statement |
| User skills on disk | `$CODEX_HOME/skills` (deprecated), `~/.agents/skills` | `~/.claude/skills/<name>` | `~/.gemini/skills`, `~/.agents/skills` | `~/.agents/skills`, `~/.openclaw/skills` (`--global`), `<state-dir>/skills` | `~/.hermes/skills/<category>/<name>` |
| Repository skills | `.agents/skills` from the project root down to the working directory; `.codex/skills` | `.claude/skills` | `.gemini/skills`, `.agents/skills` | `<workspace>/skills`, `<workspace>/.agents/skills` | none documented |
| Public catalogue | `github.com/openai/skills` (`.curated`, `.experimental`, `.system`) | `github.com/anthropics/skills`; plugin marketplaces | none; installs from any git repository | ClawHub (`@owner/slug`), with a security analysis per skill | "official" optional skills; also installs from skills.sh, GitHub, ClawHub, LobeHub, any URL |
| Install command | the `skill-installer` skill, in conversation | `/plugin marketplace add …`, `/plugin install …` | `gemini skills install <git url>`, `gemini skills link <dir>` | `openclaw skills install …` | `hermes skills install …` |
| Frontmatter beyond the standard | `metadata.short-description`; plus a sidecar file, `agents/openai.yaml`: `interface` (display name, icon, default prompt), `policy.allow_implicit_invocation`, `policy.products`, `dependencies` | `when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `disallowed-tools`, `model`, `effort`, `context`, `agent`, `background`, `hooks`, `paths`, `shell` | none documented | `user-invocable`, `disable-model-invocation`, `command-dispatch`; `metadata.openclaw`: `requires.{env,bins,anyBins,config}`, `primaryEnv`, `envVars`, `os`, `install`, `always`, `skillKey`, `nix` | `version`, `author`, `platforms`, `required_environment_variables`, `required_credential_files`; `metadata.hermes`: `tags`, `related_skills`, `requires_toolsets`, `requires_tools`, `fallback_for_*`, `config`, `blueprint` |
| Body syntax beyond Markdown | none | `$ARGUMENTS`, `$0`…, `${CLAUDE_SKILL_DIR}`, `${CLAUDE_PLUGIN_ROOT}`; **`` !`command` ``** and ```` ```! ```` blocks, run before the model sees the skill | none documented | `{baseDir}` | none documented |
| Tool names the bodies assume | `shell`, `apply_patch` | `Bash`, `Read`, `Write`, `Edit`, `Grep`, `Glob`, `WebFetch`, `WebSearch`, `Agent`/`Task` | `run_shell_command`, `read_file`, `write_file`, `activate_skill` | `exec`, `read`, `write`, `browser` (not verified against its docs) | `terminal`, `read_file`, `execute_code`, `web_extract`, `skill_view`, `skills_list`, `cronjob_manage` |
| Licence of the catalogue | per skill: of 44, 31 Apache, 1 MIT, 8 Figma's terms, 4 a Notion notice (first lines only) | "many" Apache 2.0; `docx`, `pdf`, `pptx`, `xlsx` are source-available, not open source | n/a | per skill | per skill |

Gemini contributes no catalogue: its part of this is the `~/.agents/skills` and `.agents/skills` convention, which Codex and OpenClaw share, and a consent prompt on first activation. A "Gemini skill" is therefore any standard skill installed where Gemini CLI looks.

---

## 2. What `puffin` does today

### 2.1 Read from the pinned source (`rust-v0.158.0`)

- **Roots** (`ext/skills/src/host_roots.rs`): `$CODEX_HOME/skills` (i.e. `~/.puffin/skills`; marked deprecated upstream but still scanned), `~/.agents/skills`, the bundled system skills under `$CODEX_HOME/skills/.system`, `.codex/skills` of a project config layer, and `.agents/skills` in every directory from the project root to the working directory. **There is no configuration key that adds a root**: `[[skills.config]]` entries are selectors (`path` or `name`, plus `enabled`) that switch a skill off or on, not places to look.
- **Scan**: recursive to depth 6, at most 2,000 directories and 20,000 entries per root; hidden directories below a root are skipped (`HiddenDirectoryPolicy::Skip` for every host root, `loader/host.rs`); directory symlinks are followed in user, repository and admin roots and not in the system root (`loader/host.rs`).
- **Frontmatter**: only `name`, `description` and `metadata.short-description` are read (`skills/src/parser.rs`). Unknown keys are ignored, and a line-oriented repair retries YAML that third-party skills get wrong (an unquoted colon in a description).
- **Sidecar**: beside `SKILL.md`, Codex reads `agents/openai.yaml` (`loader/metadata.rs`, `skills/src/model.rs`): an `interface` block for display, `dependencies` on tools, and a `policy` with `allow_implicit_invocation` and `products`. The installed `pdf` skill has one, with an `interface` block only. `allow_implicit_invocation: false` is Codex's own form of Claude's `disable-model-invocation`; how the pinned version enforces it was not read through (a comment in the source says product gating is parsed and stored but not enforced). Codex also lets the user name a skill explicitly in a message (`skills/src/mentions.rs`); read, not run.
- **Catalogue budget** (`ext/skills/src/render.rs`): the list of names and descriptions the model sees each session is capped at 2% of the model's context window (a configured `skills.max_context_tokens` is itself capped at 10,000). `puffin` advertises 262,144 tokens, so the budget is 5,242 tokens, about 21,000 characters. Descriptions are truncated to fit; past that, **all descriptions are removed** and a warning is shown.
- **Body**: the model reads `SKILL.md` itself, with its shell or `skills.read`. Nothing substitutes variables or executes anything in the body.
- **Migration from other agents** (`external-agent-migration/`): Codex carries a one-shot importer for Claude Code and Cursor (skills, plugins, hooks, MCP servers, memory). Read, not run; §10 says why it is not the mechanism here.
- **Same name twice**: both are kept; the loader counts duplicates by name (`skills/src/name_counts.rs`) so that a mention can be told ambiguous. Read, not run.
- **Switching skills off**: `/skills` in the TUI. Present in the source; not run here.

### 2.2 Measured live (the installed 17-patch build, Qwen3.8-27B)

`puffin exec` runs in a scratch repository holding three probe skills; one run per row group.

| Probe | Result |
|---|---|
| `~/.agents/skills` (seven `caveman-*` skills are there on this machine) | listed by the model: the Gemini/OpenClaw user location works today with no change |
| A skill with Claude's frontmatter (`allowed-tools`, `context: fork`, `agent`, `model`, `argument-hint`, a `hooks:` map) | loaded; the model read it and returned its code word |
| The same skill's `` !`touch …; echo …` `` line | **not executed**: the marker file was not created, and the model quoted the line verbatim as text. The `hooks:` command did not run either |
| A skill with Hermes's and OpenClaw's frontmatter (`platforms: [macos]`, `required_environment_variables`, `metadata.hermes`, `metadata.openclaw.requires.bins` naming a binary that is not installed, `always: true`), one directory deeper (`<category>/<name>/`) | loaded and used. Nothing was gated: a macOS-only skill needing a missing binary is offered on this Linux machine |
| The same skill under `.claude/skills/` in the repository | **not found**: hidden directories other than `.agents` and `.codex` are not roots |
| A symlink `.agents/skills/hermes` → a directory laid out as `~/.hermes/skills/<category>/<name>/` | found and used: a symlinked foreign root works, hidden target and category level included |
| A per-skill symlink one folder below the root, `.agents/skills/from-hermes/<name>` → `…/.hermes/skills/<category>/<name>` (the layout of §3) | found and used |
| Under `-s workspace-write`, `touch` through a symlink in the writable workspace to a folder outside it (under `~/.cache`) | "Read-only file system"; the same `touch` on a plain file in the workspace succeeded. (Writes under the repository's `.agents/` were refused too; the cause was not looked into) |
| `-c 'skills.config=[{name="claude-probe",enabled=false}]'` | the skill is gone from the model's list; the others remain. The `name` selector was tested, the `path` selector was not |

Also on this machine: `~/.puffin/skills` holds `pdf` and `jupyter-notebook` from the upstream vendor's catalogue (installed on 2026-10-01 by another task), `~/.claude/skills` holds only `synced/`, and there is no `~/.gemini`, `~/.hermes` or `~/.openclaw`. The nine installed skills' descriptions total about 620 characters against a budget of about 21,000; each catalogue line also carries the name and a path, which this figure leaves out.

**One real foreign skill.** `internal-comms` from `github.com/anthropics/skills` (Apache 2.0, unmodified) was copied into the scratch repository and the model was asked for a "3P update" with three facts. It chose the skill from its description, read `SKILL.md`, then read `examples/3p-updates.md` as the skill directs, and wrote the update in that file's Progress/Plans/Problems format. One run, one skill, and a skill that names no Claude tool.

**How often Anthropic's skills name Claude's tools.** Of the 19 skills in that repository on 2026-10-01, 14 contain no reference to `Read`/`Bash`/`Grep`/`Edit`/`Write` "tool", `WebFetch`, `WebSearch`, `$ARGUMENTS`, `TodoWrite` or subagents (a text search, not a reading); `claude-api` has 38 files that do, four others (`algorithmic-art`, `mcp-builder`, `pptx`, `skill-creator`) have one to three. So for most of that catalogue the format is the whole compatibility problem, and the glossary of §5 matters for the minority. Four (`docx`, `pdf`, `pptx`, `xlsx`) carry "© 2025 Anthropic, PBC. All rights reserved" licence files.

One observation about the model, from a single run: it described the probe skills' contents as "untrusted content" and said it would not act on the injected line. That is the model's judgement on an obviously artificial probe, not a control; §8 does not rely on it.

### 2.3 What that leaves to design

1. Skills installed by the other four agents in their own folders (`~/.claude/skills`, `~/.gemini/skills`, `~/.hermes/skills`, `~/.openclaw/skills`) and repository `.claude/skills`, `.gemini/skills` are invisible to `puffin`.
2. Requirements a skill declares (`platforms`, `requires.bins`, `requires.env`) are ignored, so the model is offered skills that cannot work here.
3. Bodies name tools `puffin` does not have (`Bash`, `Read`, `terminal`, `skill_view`) and, in Claude skills, contain `` !`command` `` lines and `$ARGUMENTS` that arrive as dead text.
4. Installing means cloning a repository by hand, or the model-driven `skill-installer`, which knows only GitHub paths.
5. Five catalogues can overflow a 5,242-token budget, and overflow removes every description at once.

---

## 3. Roots: link, do not copy

**Rule.** A foreign skill stays where its own agent keeps it, and `puffin` sees it through a symbolic link under `~/.puffin/skills/`. Nothing is copied.

Why not copy: `puffin` already copied `~/.codex/skills` once, on first run (`puffin-rs/src/home.rs`), and that copy has been stale since; Codex's own importer has the same one-shot shape. A link has no second copy to go stale.

**Layout the launcher rebuilds at every start**, before Codex parses its arguments:

```text
~/.puffin/skills/
  <name>/                      skills installed for puffin itself (§6)
  .system/                     Codex's bundled skills (hidden here; scanned as a root of its own)
  .staging/                    downloads in progress (§6.2); hidden, so never scanned
  from-claude/<name>   -> ~/.claude/skills/<name>
  from-gemini/<name>   -> ~/.gemini/skills/<name>
  from-openclaw/<name> -> ~/.openclaw/skills/<name>
  from-hermes/<name>   -> ~/.hermes/skills/<category>/<name>
```

- **One link per skill, not per folder.** The launcher walks each foreign folder anyway, to read frontmatter for §4: every directory holding a `SKILL.md`, to depth 3 below the source, hidden directories skipped. Depth matters twice: Hermes keeps skills under a category, and Claude Code keeps skills synced from claude.ai under `~/.claude/skills/synced/<name>/` (that container is the only thing in `~/.claude/skills` on this machine). It then links only the skills that pass: a skill that fails the preflight, is manual-only, or is shadowed by a same-named skill of higher precedence (§7) simply gets no link. Foreign skills therefore never need a `[[skills.config]]` entry, and Hermes's category level is flattened away.
- **The `from-*` folders are the launcher's.** Each is rebuilt at every start, under a temporary hidden name and then renamed over the old one, holding a lock file, because Night Shift starts two or three `puffin exec` at once and one launcher must not empty a folder another session's Codex is scanning; whatever else is found under those names (a plain folder, a file, a link pointing anywhere but the expected source) is moved to `~/.puffin/skills/.quarantine/<timestamp>/` and reported in one line. This matters because the agent can write `~/.puffin/skills` (§8.6): without it, a steered session could replace `from-claude` with a folder of its own and have it kept.
- A skill another agent installs mid-session appears at the next `puffin` start, which is when Codex scans anyway.
- `~/.agents/skills` needs no link: Codex scans it already. Skills there are gated and de-duplicated through `[[skills.config]]` instead (§4, §7), since that folder is the user's and shared with Gemini CLI and OpenClaw.
- **Repository skills.** `.claude/skills` and `.gemini/skills` of the repository being worked in are not roots, and the launcher must not write into the user's repository to link them. They are linked as `from-repo-<hash of the repository root>/<name>`, created at start when the working directory is inside a repository that has such a folder and the repository is trusted (§8.5), and removed at the next start made anywhere else. Consequence, stated: they load at user scope, so their precedence is below the repository's own `.agents/skills` (§7), and two `puffin` sessions in different repositories started close together see whichever set was linked last. §11 lists this as open.
- **Switching a source off**: `puffin skill source <claude|gemini|hermes|openclaw> off` records it in `~/.puffin/puffin-skills.toml`; `on` restores it. Default: on for every source whose folder exists (open question 1).
- **Read-only through the link.** Commit `77b9471` put `~/.puffin/skills` in the sandbox's writable roots so the built-in installer works. The bind is of that path; a link's target lies outside it and stays read-only to sandboxed commands, so the agent cannot rewrite another agent's skills through the link. Measured with a stand-in (§2.2): from a writable workspace, writing through a symlink to a folder under `~/.cache` failed with "Read-only file system". To be repeated with the real `~/.puffin/skills` root when built, since it is the property that keeps a compromised session from editing `~/.claude/skills`.

Two traps recorded for whoever builds this:
- Codex skips hidden directories below a root, which is why the links live under `from-…` and not `.claude`, and why `.staging` and `.quarantine` are safe places for things the model must not be offered.
- `$CODEX_HOME/skills` is marked deprecated in the pinned source. If a Codex bump stops scanning it, the fallback is `~/.agents/skills/from-*`, at the cost that Gemini CLI and OpenClaw then see those links too. The acceptance test of §13 fails loudly in that case.

---

## 4. Frontmatter: honour, ignore, neutralise

Codex reads three frontmatter keys and its own sidecar (§2.1) and ignores the rest. The launcher adds one pass of its own over every skill it can see, at start, and acts on a fixed list. It never edits a foreign skill's files.

| Policy | Fields | What `puffin` does |
|---|---|---|
| **Honour** | `name`, `description`, `metadata.short-description` | Codex, unchanged |
| **Honour as a preflight** | Hermes `platforms`; OpenClaw `metadata.openclaw.os`, `requires.bins`, `requires.anyBins`, `requires.env`; Hermes `required_environment_variables`; the standard's `compatibility` (shown, not parsed) | A skill whose platform excludes Linux/this OS, or whose required binary is not on `PATH`, is **not offered**: a foreign skill gets no link (§3); a skill in `~/.agents/skills` or `~/.puffin/skills/<name>` gets a `[[skills.config]]` entry (`path`, `enabled = false`) that the launcher writes and owns. `puffin skill list` shows it as `unavailable: needs gh`. A missing environment variable does not switch it off; it is shown as `needs FOO_API_KEY`, because the user may set it in the session |
| **Honour by declining** | Claude and OpenClaw `disable-model-invocation: true` | not offered, the same way, shown as `manual-only in its own agent`. These are skills their author marked as too consequential for the model to start by itself (deploy, send); The safe reading is not to offer them. `puffin skill enable <name>` overrides. A closer mapping exists and is Phase 0 item 6: Codex's `policy.allow_implicit_invocation: false` keeps a skill out of the model's hands while the user can still name it, but setting it means a sidecar file, which `puffin` will not write into another agent's folder |
| **Ignore** | Claude `context`, `agent`, `background`, `model`, `effort`, `paths`, `argument-hint`, `arguments`, `when_to_use`, `user-invocable`, `shell`; OpenClaw `always`, `install`, `nix`, `skillKey`, `command-dispatch`, `primaryEnv`; Hermes `version`, `author`, `tags`, `related_skills`, `requires_toolsets`, `fallback_for_*`, `config`, `blueprint`, `required_credential_files` | nothing. In particular `always` never forces a skill into context, `install` never installs a dependency, `blueprint` never schedules anything, and `model` never changes the model |
| **Neutralise** | Claude `hooks`; `allowed-tools` / `disallowed-tools`; body `` !`command` `` and ```` ```! ```` blocks | Never executed and never used to pre-approve anything: approvals stay with Codex's sandbox and approval policy. Measured today (§2.2): neither the hook nor the bang line ran. This spec commits `puffin` to never adding that behaviour. The glossary of §5 tells the model what such a line is |

The launcher's `[[skills.config]]` entries (only ever for skills outside the `from-*` folders) are kept between two marker comments in `config.toml` and rewritten whole at each start, so a skill whose missing binary is later installed comes back by itself, and entries the user wrote are not touched. A user entry for the same path wins.

Not parsed, deliberately: `compatibility` is free text ("Designed for Claude Code", "Requires Python 3.14+ and uv"); guessing at it would switch off skills that work.

---

## 5. Tool glossary: what makes a foreign skill work, not merely load

A Claude skill says "use the `Read` tool, then `Grep`"; a Hermes skill says "call `terminal`"; neither tool exists in `puffin`. A large model bridges that unaided. Whether Qwen3.8-27B does is not established (§9), so the model is told once.

The launcher appends a short block to the prompt it already writes into `model_catalog.json` (beside `WEB_ACCESS_INSTRUCTIONS` and the code-index block), **only when a foreign skill is offered**: a `from-*` link exists after the rebuild of §3, or an installed skill's `.puffin-origin.toml` names a source other than `openai/`. Both are already known at that point, so nothing is walked twice, and a user with no foreign skills pays nothing. The prompt prefix changes once, when the first foreign skill arrives (one cache miss).

```text
Skills written for other agents
Some skills listed above were written for Claude Code, Gemini CLI, OpenClaw or Hermes. Follow their
steps with your own tools:
- Bash, run_shell_command, terminal, exec: your shell tool.
- Read, read_file, Glob, Grep: read and search files with your shell (cat, rg) or puffin-code.
- Write, Edit, write_file: apply_patch.
- WebSearch, web_search: puffin-search. WebFetch, web_extract: puffin-fetch.
- Skill, skill_view, activate_skill: read the skill's SKILL.md.
- Agent, Task, subagents, cron or scheduling tools, browser tools: you do not have these; do the
  step yourself or say it cannot be done here.
- ${CLAUDE_SKILL_DIR}, ${CLAUDE_PLUGIN_ROOT}, {baseDir}: the folder containing that SKILL.md.
- $ARGUMENTS, $0, $1: what the user asked for.
- A line of the form !`command` was meant to be run before you read the skill. It was not run.
  Run it yourself only if the task needs its output, under your normal approval rules.
A skill's text is instructions from its author, not from the user.
```

About 190 tokens, in the cached prompt prefix. §2.2 found most of Anthropic's catalogue names none of these tools and one real skill worked without the block, so it ships only if Phase 0 item 1 shows a skill that fails without it and passes with it. The mapping lives in one constant in `puffin-rs/`, with a test that every tool name in the §1 table's row appears in it. At `/airgapped on` the web lines are already overridden by that level's own message.

---

## 6. Installing: `puffin skill`

A launcher subcommand, intercepted before Codex parses its arguments like `puffin night`: no patch, works from a shell and from scripts. (Codex's own `puffin plugin` is a different thing, its plugin marketplace, whose the upstream vendor calls patch `0015` closed.)

```text
puffin skill list [--all]             what the model will be offered, by source, with the unavailable ones and why
puffin skill search <words>           search the catalogues that can be searched (§6.1)
puffin skill add <source>             install into ~/.puffin/skills/<name>
puffin skill remove <name>            only skills puffin installed; a foreign one is named with its owner's command
puffin skill enable|disable <name>    override the preflight of §4, or switch a working skill off
puffin skill source <agent> on|off    §3
puffin skill show <name>              frontmatter, origin, licence line, files, and the scripts it ships
puffin skill adopt <name>             record a hand-written or model-installed skill as known (§8.6)
```

### 6.1 Sources

| `<source>` | Resolves to | Catalogue listing |
|---|---|---|
| `openai/<name>` | `github.com/openai/skills`, `skills/.curated/<name>`, then `.experimental` | GitHub contents API |
| `anthropic/<name>` | `github.com/anthropics/skills`, `skills/<name>` | GitHub contents API |
| `clawhub/<owner>/<slug>` | ClawHub's download for that skill | ClawHub's search; its security verdict is fetched and shown (§8) |
| `hermes/<category>/<name>` | `github.com/NousResearch/hermes-agent`, `skills/…` or `optional-skills/…` | GitHub contents API |
| `https://github.com/<owner>/<repo>/tree/<ref>/<path>` or `<owner>/<repo>/<path>` | that folder | none |
| a local directory | copied | none |

Gemini has no catalogue (§1); a skill published "for Gemini CLI" is a git repository and installs through the GitHub form.

The exact download endpoints of ClawHub and the path of Hermes's optional skills inside its repository are **not verified** and are Phase 0 items (§9). (Both verified on 2026-10-03; §15.6 has what they are.) If ClawHub offers no stable unauthenticated download, `clawhub/…` is dropped from Phase 1 and its skills install through their GitHub source where they have one.

### 6.2 What `add` does

1. Refuse at `/airgapped on` with that level's message, before any network call. Run from a shell there is no session, so the level is the one `puffin airgapped` reports: the environment variable, then the configuration files, strictest wins. (A `duckduckgo` level, which let the download proceed, was removed from `/airgapped` on 2026-10-03.)
2. Download to a staging directory under `~/.puffin/skills/.staging/` (hidden, so never scanned), over HTTPS, by tarball of the named ref; resolve and record the commit.
3. Validate against the standard: `SKILL.md` present, frontmatter parses, `name` legal. A name that differs from its folder is installed under the frontmatter name. Refuse a bundle over 50 MB, any path that escapes the skill folder, and symlinks pointing outside it.
4. Print before committing anything: name, description, origin and commit, the first line of its licence file or `license:` value, every file under `scripts/` with its size, the preflight result of §4, ClawHub's verdict if any, and the catalogue budget after this install (§7). Ask for confirmation unless `--yes`.
5. Move into place and write `~/.puffin/skills/<name>/.puffin-origin.toml`: source, commit, date, the hash of every file. `list` uses it to say where a skill came from and whether it was edited since.
6. Never run anything from the bundle, never install what its `install:` block names, never set an environment variable it asks for.

`remove` deletes only folders that carry `.puffin-origin.toml`. There is no `update` in Phase 1: `remove` and `add` again, so every change of a skill's text passes through step 4.

### 6.3 The built-in `skill-installer`

It stays: it is compiled into the binary and already installs from any GitHub path when the user asks in conversation. `puffin skill add` is the deterministic path beside it: it shows what is being installed before it lands, records the origin, knows the level of `/airgapped`, and does not depend on the model choosing the right script arguments. The glossary block does not advertise either.

---

## 7. Names and the catalogue budget

**Collisions.** `pdf` exists in the upstream vendor's and Anthropic's catalogues, and Hermes ships its own. Codex keeps both when two skills share a name (read from `name_counts.rs`, not run), and the model then has to choose between two catalogue lines. `puffin` avoids offering duplicates: for one name, the launcher keeps the first in this order; a foreign loser gets no link, and a loser in `~/.agents/skills` gets a `[[skills.config]]` entry:

1. the repository's `.agents/skills` and `.codex/skills`;
2. `~/.puffin/skills/<name>` (installed for `puffin`);
3. `~/.agents/skills`;
4. linked sources, in the order `from-repo-*`, `from-claude`, `from-gemini`, `from-openclaw`, `from-hermes`;
5. the bundled system skills.

`puffin skill list --all` shows the shadowed ones and what shadows them; `enable` with a path overrides.

**Budget.** 5,242 tokens for every visible skill's name, description and path. At a typical 60 to 100 tokens per skill that is roughly 50 to 80 skills before truncation starts, and past it Codex removes every description at once, after which no skill can be chosen by description. Hermes alone ships more bundled skills than that (its count is not verified here), so linking `~/.hermes/skills` on a machine that has Hermes installed may overflow on its own.

- The launcher computes the cost with Codex's own arithmetic (bytes / 4) at start and after `add`.
- At 80% it prints one line at start: `Skills: 4,310 of 5,242 catalogue tokens; puffin skill list shows what to switch off`.
- Over 100%, it leaves out linked skills, from the last source in the order above first, until the catalogue fits, says how many from which source, and never lets Codex reach the remove-all state silently.
- The per-machine cap is the model's, not a constant: the compaction spec proposes advertising a smaller window ([PUFFIN_COMPACTION](./DREAMFERENCE_PUFFIN_COMPACTION.md)), which shrinks this budget in proportion. The launcher reads the window it itself wrote to the catalog.

---

## 8. Security

A skill is text the model treats as instructions, plus scripts it may run. Installing one is closer to installing a program than to saving a prompt.

1. **Nothing in a skill executes by being installed or loaded.** No hook, no `` !`command` ``, no `install:` step, no `always`. Measured for the first two (§2.2); the rest are never read.
2. **Scripts run only as commands the model issues**, under the session's sandbox and approval policy, like any other command. A skill cannot grant itself an approval: `allowed-tools` is neutralised (§4).
3. **Descriptions are read every session.** A hostile description is a prompt injection that needs no activation. Mitigations: installs are explicit and shown (§6.2 step 4); the glossary's last line tells the model whose words a skill's are; `puffin skill show` prints exactly what the model will see. Not a mitigation: the model's own caution in §2.2.
4. **Linked sources import the other agent's trust decisions.** Whatever the user installed for Claude Code or Hermes becomes visible to a local model that may be easier to steer. That is the cost of "seamless"; `puffin skill source <agent> off` is the control, and `puffin skill list` names every linked skill's source.
5. **Repository skills are written by whoever wrote the repository.** `.agents/skills` in a cloned repository is loaded today by upstream Codex behaviour, before this spec. Linking `.claude/skills` and `.gemini/skills` (§3) widens that. Proposed: repository-sourced links are created only for repositories the user has marked trusted for the code index ([PUFFIN_CODE_INDEX](./DREAMFERENCE_PUFFIN_CODE_INDEX.md)'s trust list), which already answers "may this repository's content run things here".
6. **The agent can write `~/.puffin/skills`** since `77b9471`. A session steered by a hostile page could write a skill that persists into later sessions. `.puffin-origin.toml` makes that visible: at start the launcher looks for skills in that folder with no origin file or with changed hashes and prints `Skills: 1 skill was added or changed outside puffin skill add (puffin skill list)`, once per skill and content hash, remembered in `puffin-skills.toml`, so the built-in installer's skills (`pdf` and `jupyter-notebook` here) and the user's own are announced once and not at every start. `puffin skill adopt <name>` writes an origin file for one. It does not block them: the user's own hand-written skills look the same. The `from-*` folders are stricter, because nothing but the launcher has a reason to write there: they are rebuilt at every start and anything foreign in them is quarantined (§3).
7. **ClawHub.** Its documentation says third-party skills are "untrusted code" and that it runs a security analysis comparing what a skill declares with what it does. `add` shows that verdict and refuses a skill ClawHub marks malicious; `--force` does not override that case. How the verdict is exposed to a client is a Phase 0 item.
8. **Credentials.** `requires.env` and `required_environment_variables` are displayed, never prompted for and never stored by `puffin`.
9. **`/airgapped on`.** `add` and `search` refuse; installed skills keep working as text; a skill whose steps need the network fails at the sandbox like any command.

---

## 9. Checked and not checked (2026-10-01)

**Checked on this machine**

- The loader's roots, scan limits, parsed keys, symlink policy and budget arithmetic, read from the pinned source (§2.1).
- Live: `~/.agents/skills` is loaded; Claude-style and Hermes/OpenClaw-style frontmatter loads; a category level loads; a symlinked root with a hidden target loads; `.claude/skills` in a repository does not; `` !`command` `` and `hooks:` do not execute (§2.2).
- Live: a link target outside a writable folder is read-only in the sandbox; `skills.config` with a `name` selector removes a skill from the model's list (§2.2).
- Live: Anthropic's `internal-comms`, unmodified, chosen, read and followed by the model (§2.2).
- `github.com/anthropics/skills` and `github.com/openai/skills` answer an anonymous `git ls-remote`; the former was cloned and its 19 skills searched for Claude tool names.
- The patch series is 31,175 bytes against a 31,500 cap.

**Read from documentation, not run**

- Every other column of §1: the disk locations, install commands and frontmatter of Claude Code, Gemini CLI, OpenClaw and Hermes. None of the four is installed here.
- The tool names in §1's row for OpenClaw.

**Phase 0, before any code**

1. One real skill from each catalogue, installed by hand and run against the local model on a task it is meant for: the upstream catalogue's `pdf`, an Anthropic skill that does name Claude's tools (`mcp-builder` or `skill-creator`; `internal-comms`, which names none, already passed once), one ClawHub skill, one Hermes optional skill, and one skill a Gemini CLI user published. For each: does the model choose it, read it, follow it, and finish. Then the same five with the §5 glossary added by hand. This decides whether the glossary earns its 190 tokens, and it is the only evidence of fitness on Qwen3.8; today's evidence is one code-word probe.
2. ClawHub: the unauthenticated download and verdict endpoints. *(Done 2026-10-03, §15.6.)*
3. Hermes: where optional skills live in its repository, and how many skills a default install puts in `~/.hermes/skills` (the budget question of §7). *(The first half done 2026-10-03, §15.6; the second needs Hermes installed.)*
4. The read-only link target of §3, repeated with the real writable root `~/.puffin/skills` in a scratch home (measured so far from a workspace).
5. `[[skills.config]]` with the `path` selector (the `name` selector is measured), written in `config.toml` rather than passed with `-c`, and what happens when the path no longer exists. §4 and §7 need `path` for skills in `~/.agents/skills`, because two skills may share a name; foreign skills do not depend on it (§3).
6. Whether `policy.allow_implicit_invocation: false` in `agents/openai.yaml` removes a skill from the model's catalogue while a user mention still loads it, and whether a wrapper folder under `from-*` (links to the skill's files plus a generated sidecar) is a sound way to apply it to a foreign manual-only skill.

---

## 10. Alternatives considered

- **Copy foreign skills in once** (Codex's `external-agent-migration`, or `puffin`'s own first-run copy). Rejected: stale from the next day, and it duplicates skills the other agent keeps updating.
- **Add roots through configuration.** Not available: `[[skills.config]]` selects, it does not add (§2.1). A patch to `host_roots.rs` would do it in about 600 bytes; the series has 325 left, and links need none.
- **One link per source folder** (`from-claude -> ~/.claude/skills`). Simpler, and measured to load (§2.2), but every gated or shadowed foreign skill would then need a `[[skills.config]]` entry by `path`, a selector not yet measured, and a whole source could only be all in or all out when the budget overflows.
- **A `/skill` slash command.** About 2 KB of patch for something a shell command does; the model can be asked to run `puffin skill list` in a session.
- **Rewrite foreign skills into Codex's dialect at install.** Rejected: it forks every skill from its upstream, breaks the hash record, and the differences are tool names a glossary covers.
- **Wrap skills as MCP tools.** Rejected: loses progressive disclosure, which is the point of the format.
- **Execute `` !`command` `` lines for Claude compatibility.** Rejected outright: it runs a skill author's shell before the model or the user has seen it.
- **`skills-ref validate`** (the standard's reference validator) as the install check. A Python dependency for a check that is a few lines of Rust; the rules of §6.2 step 3 are taken from the same specification.

---

## 11. Open questions

1. Should linked sources default to on (seamless, §8.4's cost) or off until `puffin skill source <agent> on`?
2. Repository `.claude/skills`: link only for trusted repositories (proposed), always, or never?
3. Should `puffin` ship a default set (say the upstream vendor's `pdf` and Anthropic's Apache-licensed ones) in its release? Licences allow the Apache and MIT ones with their notices; not the Figma, Notion or Anthropic document skills without reading their terms.
4. `disable-model-invocation` skills are switched off (§4). Is a manual path wanted, e.g. `puffin exec --skill <name> …`, which would put the skill's body into the prompt?
5. Should the catalogue budget be raised with `skills.max_context_tokens` (up to 10,000) on this model, given the system prompt is about 11,000 tokens already?
6. Night Shift and SWE-bench runs: same skills as interactive sessions, or none? Proposed: none for SWE-bench (a skill is an uncontrolled variable in an A/B measurement), the user's set for Night Shift.

---

## 12. Phases

- **Phase 0:** the six checks of §9. No code.
- **Phase 1:** per-skill links for the four user folders, with the rebuild and quarantine rule (§3); the preflight and collision pass (§4, §7); `puffin skill list|show|enable|disable|source`; the budget line. Glossary (§5) only if Phase 0 item 1 shows it helps.
- **Phase 2:** `puffin skill add|remove|search` for `openai/`, `anthropic/`, GitHub paths and local directories; origin records; the changed-skills line.
- **Phase 3:** `clawhub/` and `hermes/` sources; repository `.claude/skills` and `.gemini/skills` under the trust rule.

## 13. Tests and acceptance

- **Launcher unit tests** (`cargo test -p puffin-launcher` in the export): link creation and pruning in a scratch home, including a planted folder or wrong-target link under a `from-*` name being quarantined; frontmatter preflight on fixtures of each dialect; collision order; the budget arithmetic against `render.rs`'s constants; `[[skills.config]]` rewritten between its markers with user entries untouched; `add` refusing path escapes, outside symlinks and oversize bundles, against a stand-in HTTP server; refusal at `/airgapped on`; the glossary naming every tool in §1.
- **Live, in a scratch home** (`CODEX_HOME` and `HOME` pointed at a temporary folder, as the egress audit does): the probes of §2.2 again through the links; a macOS-only skill absent from the model's list; two same-named skills yielding one; an overflowing source switched off with its line printed.
- **Acceptance:** with Claude Code's, Hermes's or OpenClaw's skill folder present, `puffin` offers those skills with no command typed; `puffin skill add anthropic/<name>` and `openai/<name>` install and the model uses the skill in the next session; nothing from any skill runs without a command the model issued under the session's approval policy.
- **Never in tests:** the real `~/.puffin`, `~/.claude`, `~/.agents`, or the network.

## 14. Sources

- [Agent Skills specification](https://agentskills.io/specification)
- [Claude Code: skills](https://code.claude.com/docs/en/skills); [anthropics/skills](https://github.com/anthropics/skills)
- [Gemini CLI: skills](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/skills.md)
- [OpenClaw: skills](https://docs.openclaw.ai/tools/skills); [ClawHub skill format](https://github.com/openclaw/clawhub/blob/main/docs/skill-format.md)
- [Hermes Agent: skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills); [creating skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills)
- [openai/skills](https://github.com/openai/skills)

---

## 15. As built (2026-10-02)

### 15.1 What is built, and where

| Piece | Path |
|---|---|
| Finding skills, the preflight, collisions, the budget: one plan | `puffin-rs/skills/src/catalog.rs`, `frontmatter.rs`, `preflight.rs`, `budget.rs` |
| The `from-<agent>` folders, their lock, the swap and the quarantine | `puffin-rs/skills/src/links.rs` |
| The launcher's `[[skills.config]]` entries | `puffin-rs/skills/src/config_entries.rs` |
| `add`, `remove`, `adopt`, `.puffin-origin.toml`, the catalogue listing for `search` | `puffin-rs/skills/src/install.rs` |
| `list`, `show`, and what `add` prints before installing | `puffin-rs/skills/src/report.rs` |
| `puffin-skills.toml` | `puffin-rs/skills/src/settings.rs` |
| The glossary text | `puffin-rs/skills/src/glossary.rs` |
| The start-up pass, `enable`/`disable`/`source`, the command line's grammar | `puffin-rs/skills/src/lib.rs` |
| `puffin skill …`, the GitHub downloads, the air-gap check, the hook into `puffin`'s start | `puffin-rs/src/skills.rs`, four lines in `puffin-rs/src/lib.rs` |

The on-disk half is a crate of its own with no dependency on Codex or the network, like `puffin-rs/airgapped/`, so its 51 tests run in seconds in a copy of the folder (`cargo test`) without compiling the Codex workspace. Its dependency versions are the workspace's, so building inside the workspace adds no second copy of a crate. No Codex patch was needed; the series is unchanged.

Built from the command list of §6: `list [--all]`, `show`, `add`, `remove`, `search`, `enable`, `disable`, `source`, `adopt`. `add` takes `openai/<name>`, `anthropic/<name>`, `<owner>/<repo>/<path>`, a `github.com` URL with or without `/tree/<ref>/<path>`, and a local folder; `clawhub/…` and `hermes/…` answered that they were not built until Phase 3 (§15.6). `search` reads the upstream vendor's `.curated` and `.experimental` folders and Anthropic's `skills/` from each repository's tarball and matches every word against name and description; since Phase 3 also Hermes's catalogue and ClawHub's own search.

### 15.2 Departures from the design, each for a reason

- **`synced/` has one more level than §3 says.** On this machine `~/.claude/skills/synced/` holds one folder per claude.ai account (`synced/<account ids>/<name>`), 22 skills in two of them. The walk's depth of 3 reaches them; the second account's copies of the same names are shadowed by the first's, and the shadowing line names the winner's folder, since "shadowed by Claude Code" said nothing when both were Claude Code's.
- **Launcher-owned `config.toml` entries carry one comment each, not two markers around a block.** `configure_codex_home` re-parses and rewrites the same file with toml_edit at every start, and toml_edit appends a table it creates at the end of the file, which is after the entries: a block between two markers would have swallowed it. Each entry is the comment line, `[[skills.config]]`, `path`, `enabled = false`; only groups of exactly that shape are ever removed. A test runs the entries through the config rewrite and back (`the_launchers_config_entries_survive_the_config_rewrite_and_are_still_removable`). If the user's file defines `skills.config` as an inline array, entries cannot be added; the launcher says so in one line and leaves the file parseable.
- **A path the user has an entry for is left to them**, on or off. Codex applies entries in order and the launcher's come last, so writing one would have overridden the user's.
- **A `from-*` folder that is already right is not touched.** The rebuild compares each folder's links with the plan first, so in the common case nothing is renamed under a running session's Codex. When a folder does change it is exchanged in one step with `renameat2(RENAME_EXCHANGE)` on Linux; elsewhere, and on a filesystem without the exchange, by two renames with a moment between them.
- **A link whose target contains `..` is not the launcher's**, even if it begins with the agent's folder; it is quarantined with the rest.
- **The same `SKILL.md` reached by two routes is one skill.** A folder linked into another (`~/.agents/skills/x -> ~/.claude/skills/x`) is listed once, under the route of higher precedence, and is never both offered and switched off: Codex identifies a skill by the canonical path of its `SKILL.md`, and an entry for the loser would have switched off the winner.
- **`enable` also overrides a collision**, as §7 says, and undoes a `disable`; when two skills share a name a decision is stored by folder, otherwise by name.
- **The glossary is off by default** (§15.3). `glossary = true` in `~/.puffin/puffin-skills.toml` turns it on; it is then added only while a foreign skill is offered. Its text names three more tools than §5's draft (`execute_code`, `skills_list`, `cronjob_manage`, and OpenClaw's lowercase `read`/`write`), so that the test "every tool name of §1's row appears" holds; it is 1,111 bytes, about 280 tokens.
- **The nearly-full line (80%) is printed to a person only.** `puffin exec`, and so Night Shift, gets the left-out and over-budget lines but not the advice. The line about skills changed outside `puffin skill add` is likewise printed, and marked as told, only on an interactive start, so an unattended run cannot swallow it.
- **Hashing is not done at every start.** An installed skill's record is read at start (for the glossary gate); its files are hashed only for `list`, `show` and the interactive changed-skills check. The plan for this machine's 37 skill folders takes 3 ms.
- **`add` downloads the repository's tarball at a resolved commit** (two requests: the commit, then `codeload`), as Codex's own `skill-installer` downloads the repository's zip. A tarball over 200 MB is refused with the advice to clone and install the folder. (Hermes's repository is 1.1 GB as a clone, which was taken as a reason not to make `hermes/…` a source; its tarball is 79 MB, §15.6.)
- **`add` without `--yes` needs a terminal.** With none there is nobody to ask, and it stops after printing what it would install. A local folder is copied at every air-gap level, since nothing is downloaded.
- **Budget arithmetic uses the full description**, which is what Codex's core rendering uses; where Codex renders `metadata.short-description` instead, the launcher overestimates, which errs towards leaving a linked skill out.

### 15.3 Measured

**Phase 0 item 5, the `path` selector written in `config.toml`** (installed 17-patch build, a scratch home, one run). With an entry `path = "<home>/.agents/skills/blocked/SKILL.md"`, `enabled = false`, the model listed `kept` and the linked `claude-probe` but not `blocked`, and answered "UNKNOWN" for the code only `blocked` held. An entry naming a file that does not exist did not stop Codex. A second scratch home with an entry for `skills/.system/skill-creator/SKILL.md` listed the other bundled skills without `skill-creator` (one clean run of two; in the other the model ignored the prompt and the run timed out), so a bundled skill can be switched off the same way.

**Phase 0 item 4, read-only through the link, with the real writable root** (one run, the commands' own output). From a session whose `writable_roots` holds `<home>/.puffin/skills`, with `from-claude/claude-probe -> <home>/.claude/skills/claude-probe`:

| Command | Result |
|---|---|
| `touch …/from-claude/claude-probe/x.txt` | "Read-only file system" |
| `echo hi > …/from-claude/claude-probe/SKILL.md` | "Read-only file system" |
| `touch <home>/.claude/skills/claude-probe/y.txt` | "Read-only file system" |
| `touch <home>/.puffin/skills/direct.txt` | succeeds |
| `ln -s /etc …/from-claude/planted` | **succeeds** |

So a session cannot edit another agent's skill through its link, and it can plant something under a `from-` name, which is what the quarantine is for. One condition: the first attempt put the scratch home under `/tmp`, and there the write through the link succeeded, because the workspace-write sandbox makes `/tmp` writable whatever links point into it. The guarantee is "the target is as writable as it would be without the link"; a home folder is not under `/tmp`.

**The glossary, with and without** (Qwen3.8-27B, `puffin exec -s workspace-write`, default cave mode; the block was put into the prompt of the installed build through the code-index block's hook, in the position the launcher now gives it). Two skills, linked from a scratch `~/.claude/skills`:

- a Claude-dialect probe, `release-notes`: "Use the Read tool to read `${CLAUDE_SKILL_DIR}/template.md`", "Use the Bash tool to run `python3 ${CLAUDE_SKILL_DIR}/scripts/changes.py`", "Use the Write tool to create `RELEASE_NOTES.md` … with VERSION replaced by `$ARGUMENTS`", "Use the Grep tool to check …", and a `` !`git describe --tags --always` `` line; asked "Write the release notes for version 2.4.0.";
- Anthropic's `algorithmic-art`, unmodified, whose step 0 is "Read `templates/viewer.html` using the Read tool"; asked for a flow-field piece as one HTML file.

| | Without the glossary | With it |
|---|---|---|
| `release-notes`: correct file (version, both changes, the footer) | 3 of 3 | 3 of 3 |
| read the template and ran the script from the skill's folder | 3 of 3 | 3 of 3 |
| ran the `` !`command` `` line itself | 1 of 3 | 2 of 3 |
| did the "Grep tool" check with `rg`/`grep` | 2 of 3 | 3 of 3 |
| seconds per run | 21, 33, 21 | 23, 71, 51 |
| `algorithmic-art`: read `templates/viewer.html`, wrote the HTML from it | 1 of 1 (11 commands, a 22 KB file built on the template, 724 s) | 0 of 1: the model read the first 60 lines of the skill's 405 and wrote a 1.2 KB file of its own, without the template, in 2 commands (200 s) |

No skill failed without the block, so by §5's own rule it does not ship on: it is off by default and kept as an opt-in. The one run that did not follow its skill was a run *with* the block: the prompt asked for something short, the model stopped reading before the step that names the template, and that is one run, so it is not evidence that the block does harm. The timings are single runs on a model server shared with other work (up to seven requests running and five queued during these) and say nothing about the block's cost. This is two skills and one model; a Hermes- or OpenClaw-dialect skill (`terminal`, `skill_view`) and the other catalogues of Phase 0 item 1 were not run.

**This machine's catalogue** (the plan, run read-only against the real folders). 27 skills would be offered: 2 installed (`pdf`, `jupyter-notebook`), 7 in `~/.agents/skills`, 13 linked from Claude's `synced/` (`docx`, `xlsx`, `pptx`, `deep-research`, `computer-use`, `chrome-browser`, `google-workspace`, …) and 5 bundled. That is **4,256 of 5,242 catalogue tokens (81%)**, of which the 13 Claude skills are 2,777 (the seven in `~/.agents/skills` 731, the five bundled 592, the two installed 156), so the nearly-full line is printed at every interactive start here until something is switched off. Ten are not offered, all shadowed: the second account's copies, Claude's `pdf` by the installed one, and the bundled `skill-creator` by Claude's (one `config.toml` entry). Several of the 13 are written for claude.ai's own tools (a browser, computer use, Google Workspace connectors) and cannot do their job in a terminal agent; they declare nothing a preflight could read. `puffin skill source claude off` leaves them all out, `puffin skill disable <name>` one at a time.

### 15.4 Not built

- ~~**Phase 3:** `clawhub/…` and `hermes/…` as sources, ClawHub's verdict, and links for a repository's `.claude/skills` and `.gemini/skills` under the trust rule.~~ Built on 2026-10-03; §15.6 lists what of it is still not built or not run.
- **Phase 0 item 6:** `policy.allow_implicit_invocation` as the way to keep a manual-only skill usable by name. Manual-only skills are simply not offered.
- **Phase 0 item 1 in full:** one real skill from each of the five catalogues.
- **No `puffin skill update`** (by design, §6.2) and no command for the glossary setting; it is a key in `puffin-skills.toml`.
- **macOS and Windows.** The crate compiles its links for both (`symlink_dir` on Windows needs Developer Mode or elevation) but was built and tested on Linux only.
- ~~**Not run with a build that carries this code**~~ Run on 2026-10-03 with the installed build (Phases 1 and 2). `puffin skill list` from a shell listed 27 skills at 4,256 of the 5,242-token budget. From an empty start (no `from-claude`, no `[[skills.config]]`), one `puffin exec` created the 13 `from-claude` links, `synced/<account>/` ones included, and wrote the entry switching off `.system/skill-creator`, which Claude's copy shadows; asked which skills came from Claude Code, the model named exactly those 13. With the `docx` link removed and a folder planted at `from-claude/planted/`, the next `exec` restored the link and moved the planted folder to `.quarantine/<timestamp>/`. Phase 3 (§15) has not yet been run in a built `puffin`.

### 15.5 Tests

- **`puffin-skills`** (51, `cargo test` in a copy of `puffin-rs/skills/`, or `-p puffin-skills` in the export): frontmatter of each dialect and the repair of an unquoted colon; the preflight on a made-up `PATH`; link creation, pruning, the untouched folder (same inode), planted folders, wrong-target and `..` links, a `from-` name that is a link or a file; the plan's discovery through `synced/` and Hermes's categories, hidden folders, collisions in the order of §7, a switched-off source, the budget drop from the last source, one file by two routes; `config.toml` entries added, rewritten whole, a table appended after them kept, a user's entry left alone, a config that cannot take them; `add`'s sources, one folder out of a tarball, escapes, outside links and the 50 MB limit refused, the origin record and later edits; `remove` only what `puffin` installed; `adopt`; the start-up pass's lines, each once; the glossary naming every tool of §1.
- **Launcher** (`cargo test --release -p puffin-launcher`, 7 tests in `skills.rs`): `add` against a stand-in GitHub (the commit, then the tarball: two requests), the installed skill offered and counted as foreign, a second `add` refused, `remove`; nothing requested at `/airgapped on` for `add` or `search`, while a local folder still installs; `search` across both catalogues; no install without `--yes` and without a terminal; the local commands with GitHub unreachable; the budget window read from `model_catalog.json`; the entries through `updated_config` and back.
- **Never in tests:** the real `~/.puffin`, `~/.claude`, `~/.agents`, or the network.

### 15.6 Phase 3 (2026-10-03)

**Built.**

| Piece | Path |
|---|---|
| A trusted repository's `.claude/skills` and `.gemini/skills` as linked sources, `from-repo-claude` and `from-repo-gemini`; the trust check | `puffin-rs/skills/src/catalog.rs` (`Scope::Repository`, `is_trusted`), `links.rs` (`Source::AnyRepository`) |
| `hermes/<category>/<name>`, and Hermes's catalogue in `search` | `puffin-rs/skills/src/install.rs` (`parse_source`, `catalogue_to_depth`), `puffin-rs/src/skills.rs` (`CATALOGUES`) |
| `clawhub/<owner>/<slug>`: the lookup, the verdict, the download; ClawHub in `search` | `puffin-rs/src/skills.rs` (`clawhub_skill`, `clawhub_zip`, `clawhub_search`), `install.rs` (`unzip`) |
| The version and the verdict in `.puffin-origin.toml` and in what `add` prints | `install.rs` (`Origin`), `report.rs` |

- **Repository skills.** Looked for in every folder from the repository's root down to the working directory, as Codex looks for `.agents/skills`, and linked only when the repository is **trusted**: `[projects."<root>"] trust_level = "trusted"` in `$CODEX_HOME/config.toml`, for the root or, in a linked worktree, for its main repository (the key Codex records). That is the code index's rule (`puffin-code-rs/src/config.rs`, §8.5), and nothing inside the repository can grant it. An untrusted repository's skills are not even read; `puffin skill list` names the repository and how to trust it. Their precedence is after `~/.agents/skills` and before `~/.claude/skills` (§7). `puffin skill source repo off` switches both folders off.
- **Hermes.** `hermes/<path>` tries `skills/<path>`, then `optional-skills/<path>`, in `NousResearch/hermes-agent` at a resolved commit, through the same tarball path as every GitHub source, so the unpack rules of §6.2 apply unchanged. `search` lists every `SKILL.md` up to three folders below either (a `SKILL.md` inside another skill's folder is not listed).
- **ClawHub.** `add` asks for the skill (its owner and latest version), then for the security verdict on that version, then downloads that version's zip and unpacks it under the rules of §6.2 (no path out of the folder, no link out of it, 50 MB, 5,000 files; a zip with no Unix modes gives readable files). The verdict decides:
  - **malicious** (ClawHub's moderation blocks it as malware, says `malicious`, or the scan does): refused, whatever the flags (§8.7);
  - **clean** (the scan says clean and moderation has not flagged it): installed like any other source;
  - **anything else** (suspicious, or no scan result): installed only when a person confirms at a terminal; with `--yes` it is refused and nothing is downloaded.

  What ClawHub said is printed in the summary (`ClawHub: suspicious: …`) and recorded in `.puffin-origin.toml` with the version. `search` shows ClawHub's skills from its own search (ranked by meaning, so every word need not appear), at most ten, and leaves out the skills.sh entries ClawHub also lists.

**Phase 0 items 2 and 3, checked live and anonymously on 2026-10-03.**

| Question | Answer |
|---|---|
| ClawHub's API | `https://clawhub.ai` (`/.well-known/clawhub.json`), documented in `docs/http-api.md` of `openclaw/clawhub`; public reads need no account. Download limit 1,200/min per IP |
| Find a skill | `GET /api/v1/skills/{slug}?owner={handle}`: owner, `latestVersion.version`, and `moderation` (`isMalwareBlocked`, `isSuspicious`, `verdict`) when it is flagged. **A slug is unique per owner only**: `pdf` has at least five, and without `owner` ClawHub answers 409 `AMBIGUOUS_SKILL_SLUG` with the owners (`@owner/slug` and `owner/slug` in the path both 404). The `owner` query parameter is not in the documentation (ClawHub's own CLI sends the slug alone); it was found by trying, and it works for the skill, the verdict and the download, so it may change without notice |
| The verdict | `GET /api/v1/skills/{slug}/verify?owner=&version=`: `ok`, `decision`, and `security.status` (`clean`, `suspicious`, `malicious`) with a one-sentence `summary`. Of the 60 newest skills, 48 were clean and 12 suspicious (for example "a stored key can be sent to the wrong service if the configured home changes"); none malicious. Moderation and scan can disagree: one skill had moderation `clean` and scan `suspicious`, which is why both are read |
| The download | `GET /api/v1/download?slug=&owner=&version=`: a zip of the version's files plus ClawHub's `skill-card.md` (its generated summary) and `_meta.json`, which are installed as served |
| Search | `GET /api/v1/search?q=&limit=`: ClawHub skills (`install.kind = clawhub`, `install.reference = owner/slug`) mixed with skills.sh entries |
| Hermes's skills | `skills/<category>/<name>` (58) and `optional-skills/<category>/<name>` (152, eleven of them one category deeper, e.g. `optional-skills/mlops/training/axolotl`, and one, `yuanbao`, with no category), at `44533f1`. No symlinks in either. The repository's tarball is 79 MB (204 MB of files), against the 1.1 GB §15.2 gave for the repository |

**Departures, each for a reason.**

- **`from-repo-claude` and `from-repo-gemini`, not `from-repo-<hash>`** (§3). One repository's links exist at a time either way, and a fixed name is rebuilt like the other `from-` folders. A link left from the previous repository points into some other `.claude/skills`; the rebuild knows that shape (`Source::AnyRepository`) and removes it instead of quarantining it, so changing repository prints nothing. A link pointing anywhere else is quarantined as before.
- **A repository trusted during a session is linked from the next start**: the launcher plans before Codex asks the trust question.
- **A suspicious ClawHub skill can still be installed, by a person.** §8.7 asked only that a malicious one be refused. A fifth of the newest skills are rated suspicious, mostly for how they handle credentials; refusing them would cut off much of the catalogue, and installing them unattended would take ClawHub's warning as nothing. Confirmation at a terminal is the middle: the warning is on the screen when the question is asked.
- **Hermes through the whole tarball**, not the git trees API plus one download per file. `search` needs every description, so it reads the tarball anyway, and `add` then shares the tested unpack path; the cost is 79 MB per `add` or `search`.

**Not built, not run.**

- **Not run against the live services through `puffin`.** The endpoints were probed with `curl`, and the client is tested against a stand-in answering as they did; no `puffin skill add hermes/…` or `clawhub/…` was run with a build carrying this code. `puffin` has to be rebuilt (`puffin-admin codex build`) first.
- **How many skills a default Hermes install puts in `~/.hermes/skills`** (the rest of Phase 0 item 3): Hermes is not installed here.
- **ClawHub's verdict of an installed skill is not checked again** later (`POST /api/v1/skills/-/security-verdicts` would do it in one request); a skill rated clean at install that is later flagged is not reported.
- **`hermes/<name>` without its category** is not accepted; `search` gives the full path.
- §11 question 2 is answered as proposed there (trusted repositories only); "always" and "never" are not offered as settings.

**Tests.** `puffin-skills`: 56 (5 more): the repository's folders linked only once trusted, through the main repository of a linked worktree, never by a file in the repository, ahead of `~/.claude/skills`, and switched off by `source repo`; a `from-repo-` folder keeping only links into a repository; a ClawHub zip unpacked under the rules, with modes; Hermes's catalogue through its categories, nested skills left out. Launcher: two more, against the stand-in: a Hermes skill found under `optional-skills`, and ClawHub's three verdicts (clean installed with its version and verdict recorded; suspicious refused with `--yes` and malicious refused, neither downloaded), an ambiguous slug naming its owners, and nothing asked at `/airgapped on`; `search` across all four catalogues.
