# Crew: `ling` coordinating sub-agents

Status: postponed to 2.0 (owner, 2026-10-28); not scheduled for 1.x.

> **Status:** first draft, proposed 2026-10-28, nothing built. Owner request: a mode in which `ling` runs a project through background sub-agents, with the same working habits a human-run coordinator session has needed in practice. This document specifies those habits as tools and files, not as prompt advice.

## 0. Why

Running a project through many background agents from one long session has a measurable cost structure (see [DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md](./DREAMFERENCE_MIGHTLING_CLAUDE_PLUGIN.md) §2 and §3, and the analysis behind this draft):

- The coordinator's own context is re-read on every turn. In one measured session, about 420K tokens were re-read per turn over 4,420 turns, so a turn costs about the same whether it does a lot or little. The lever is **fewer turns**, then smaller results, then shorter messages.
- About 30% of the coordinator's shell calls were bookkeeping: appending a dated line to a notes file, editing a to-do list. Another 25% were status probes (machines, queue, units, git state) that return the same facts every time.
- A quarter of every brief's bytes was the same rules block, written out again for each agent.
- Roughly one wake-up in six was an agent reporting a routine completion.

`ling` already has the parts that run work (`ling exec`, `ling-rs/delegate`'s `task` and `watch`, Night Shift's queue and worktrees). Crew adds the **coordination layer** on top: a small set of model-free commands and files so that a `ling` session can run many agents without its own context becoming the bottleneck. A local model's context is also bounded, so the same design helps `ling` more than it helps a hosted model.

## 1. Goals and non-goals

Goals: (1) one-call answers for "what is going on"; (2) briefs of a few lines; (3) decisions and to-dos kept in files that survive the session; (4) agents return a short summary plus a report file; (5) routine events are batched, urgent ones interrupt; (6) everything works with the local model server and no network.

Non-goals: no new agent runtime (agents are `ling exec` runs, plugin `task` calls or Night Shift tasks); no scheduler that replaces Night Shift; no automatic merge or push (merging stays the user's step, as in Night Shift); nothing leaves the machine.

## 2. Pieces

All commands are subcommands of `ling crew` (a leaf crate beside `ling-rs/delegate`, same dependencies: no model call anywhere in this table) and are also exposed as MCP tools by `ling delegate mcp` so a hosted-model coordinator can call them.

| Command | Replaces | Output |
|---|---|---|
| `crew status [--live]` | probing each machine, the queue, failed units, the repository by hand | at most 15 lines: machines (up/down, load, current job), Night Shift queue, failed units, agents over their context limit, repository state, inbox count. Reads files by default; `--live` re-probes nodes |
| `crew git` | fetch, fast-forward, log | branch, dirty files, ahead/behind, last N merges; fast-forwards only when clean; never commits or switches branches |
| `crew note "text" [--file F] [--todo-add T] [--todo-done SUBSTR]` | appending to notes by shell | a dated line in the current decisions file; the owner's to-do list edited in the same call. Refuses secret-looking text |
| `crew brief TEAM [--report N] [--name N]` | rewriting the rules in every brief | the standard rules footer (§3) |
| `crew inbox add\|show\|clear\|check` | one wake-up per completed agent | an append-only event file (§5) |
| `crew wait [--action\|--file\|--cmd]` | polling turns | blocks in one call, prints one line; builds on `watch` |
| `crew report NAME` | reading long agent output | prints the first lines of a report file and its path |

The commands are small enough to run without a model, so they cost no tokens beyond their output, which each command caps and prints tersely by default.

## 3. Standard briefs

A brief has two parts: the **task** (goal, scope, done-when check, limits: the `CLASS/GOAL/SCOPE/DONE WHEN/LIMITS` template of the plugin spec §4.2) and the **rules**, which are the same for every agent of a project and live in one file, `<project>/.mightling/crew/rules.toml`, rendered by `crew brief`. The task part says "Rules: run `ling crew brief TEAM` first". The rules file holds, as plain bullets the project edits:

- where agents work (their own worktree, never the main checkout) and how they commit (a project-defined command, so a team identity or a UTC timestamp is applied the same way every time);
- what must never appear in commits or docs (a denylist check the repository already runs before a push);
- how to merge (`--no-ff` from the worktree) and what refuses a push;
- resource etiquette (a systemd slice and `nice` for heavy work on a shared machine, kill by pid, one model server per machine);
- how to report (§4) and when to hand off (§6).

Teams, if the project has them, come from a project file; `none` means no commit identity.

## 4. Reports: a file and three lines

An agent writes its **full report** to `<scratch>/reports/<name>.md` and ends with exactly three lines: (1) the outcome, starting with `ROUTINE` or `ACTION`; (2) what needs the user or coordinator, or `nothing`; (3) what is next, and the report path. `ACTION` is only for a failure, a security issue, a release blocker or a decision. The coordinator reads the report file only when line 2 is not `nothing`. A routine completion also runs `crew inbox add "<name>: <one line>"`. Night Shift's own report already follows this shape and is read the same way.

## 5. The event inbox

The simplest workable design, given that a finished background agent always wakes its parent:

1. **The file.** `crew inbox add` appends `<UTC time> ROUTINE|ACTION <text>` to `~/.mightling/crew/inbox.txt` under a flock. Agents, watchers and timers are the writers. Text that looks like a secret or an address is dropped.
2. **What cannot be batched.** The wake-up itself. Its cost is held down by the three-line final message and by agents sending nothing between milestones.
3. **The 2-hourly pass.** A timer or session schedule runs one prompt: "`crew status`, then `crew inbox show`, handle everything, `crew inbox clear`". All routine items are processed in one turn.
4. **ACTION interrupts.** `crew inbox add --action` (and any watcher that fails) also touches `~/.mightling/crew/action-needed`; a `crew wait --action` armed in the background returns on it, so the coordinator reacts at once to a failure, a security issue, a release blocker or a decision, and to nothing else.
5. **Unattended mode.** With no live session, Night Shift's morning report is the inbox's reader: the user sees the inbox after a night as one page.

## 6. Context discipline

Agents hand off at about 150K tokens of context: they write a one-page handoff (state, branches, open items, next step) and stop; a fresh agent continues from the handoff with a short brief. The status digest lists agents over the limit (the session monitor of the plugin spec Part B already computes this from local transcripts). The coordinator itself compacts on a schedule.

## 7. Where it runs and what it uses

- Agents are `ling exec` sessions in worktrees (plugin `task` for bounded, test-checked changes; Night Shift for queued, windowed, best-of-N work). Crew adds no agent type.
- Memory-capped scopes, the model gate and the busy check are those of Night Shift and the plugin spec §5.3; `crew status` shows them, it does not change them.
- Nodes are named, never addressed by IP (the node spec); `crew status --live` goes through the same locator as `ling node`.
- State lives under `~/.mightling/crew/` (inbox, decisions, to-do list, last snapshot) and the project's `.mightling/crew/rules.toml`; none of it is written into the repository by the agent (the repository rule that choices belong to the user's config applies).
- Egress: none. The egress audit lists no new connection.

## 8. Measurement and rollout

Measured on the session that motivated this draft (turns, tokens re-read, bookkeeping calls): the same counts are the acceptance numbers. Target: bookkeeping and probe calls from 55% to under 20% of shell calls, brief bytes down by a quarter, routine wake-ups handled in at most one pass per two hours.

Phases: C0 `status`, `git`, `note`, `brief` (model-free, read and append only); C1 `inbox`, `wait`, `report`, and the three-line final-message convention in the Night Shift runner's prompts; C2 MCP exposure and the skill text; C3 a `/crew` slash command in the terminal and the desktop app that shows the same digest.

## 9. Decisions to take

1. Whether `crew` is a separate binary or `ling delegate` subcommands (proposal: the latter, one MCP server).
2. The default review interval for the inbox pass (proposal: 2 hours; configurable).
3. Whether hosted-model sessions get the same commands through the plugin (proposal: yes, C2).
4. Where the rules file lives for a project that forbids files of this kind in its tree (proposal: the user's config directory, keyed by repository).
