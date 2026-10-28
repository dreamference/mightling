# Mightling helping Claude Code: a delegation plugin and a session monitor

**Status:** proposed on 2026-10-27; revised the same day into one design with the session monitor (Part B); the owner's answers to its open questions recorded the same day (§0.1). Phase M0 (§13) and, of Part A, W1 (`watch`), L1 (`lookup` plus the exact tools `run_test`, `git_log`, `diff_stat`) and T1–T5 (`task` on probation, `task_verdict`, `ledger`, `agents/diff-reviewer.md`) are built (2026-10-27 to 2026-10-28, §6.1, §14); `ask` (L2/L3), `night` (N1) and M1–M2 are not. The cost figures in §2 were measured from one night of a Claude Code coordinator session on the owner's machine (aggregates only; no content of those logs is quoted or stored here). Every figure about Mightling doing delegated work, and every figure about the monitor's effect, is an estimate until the phases of §10 measure it.

**Target:** the owner first, then any Mightling user who also runs Claude Code: "your local Mightling helps your Claude Code". Two parts serve one aim, fewer Claude tokens for the same results:

- **Part A, the delegation plugin** (§4–§6): Claude Code hands whitelisted work to the local Mightling, and only work Mightling has been shown to do well.
- **Part B, the session monitor** (§7): plain code, and the local model only where reading is needed, watches the agents' transcripts on the node and tells the coordinator and the owner what needs attention, so nobody re-reads long logs to find out. The same monitor watches Mightling's own `ling` sessions and Night Shift, which makes it a product feature ("is my agent stuck?").

The owner's requirement governs every section: *quality must not drop; Mightling takes only the tasks it can be shown to do well, and the monitor's facts come from code, not from a model.*

**Builds on:**
- `ling exec` (Codex's non-interactive mode, unmodified): `--output-schema`, `-o`, `--ephemeral`, `-s`, `-C` (`codex-rs/exec/src/cli.rs`, `codex-rs/utils/cli/src/shared_options.rs`).
- The launcher's node resolution, tiers 1–5 (`ling-rs/src/node.rs`, [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md)); `NodePairing.resolve` for a paired node, by id and never by address.
- `ling-code` ([MIGHTLING_CODE_INDEX](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md)) and `rg` for model-free lookups.
- Night Shift: `ling night add --test --best-of`, chooser v1, `night/<id>` branches, the task files under `$CODEX_HOME/night/tasks/`, the runner lock, `ling sandbox` for the runner's own test run, the `mightling-night.timer` user unit ([MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md)).
- The SWE-bench model gate and runner lock ([MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) §18), and the failure analysis ([MIGHTLING_SWE_BENCH_FAILURES](./DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md)), which supplies the verification rules of §6.2.
- The skills planner, which links `~/.claude/skills` into `ling` ([MIGHTLING_SKILLS](./DREAMFERENCE_MIGHTLING_SKILLS.md)).
- `ling web` and its bridge policy `ling-rs/web/policy.json` ([MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md)), for the monitor's status page.
- The owner's alert command `~/.local/bin/owner-notify` (a local script outside the repository that sends one short message to the owner's paired Telegram chat through the Mightling bot), for the monitor's alerts on the owner's machine.
- An earlier proposal, *Claude delegates to ling*, written on 2026-10-08 and taken out of this directory the same day (in git history at `6aca2f2`). Its rules carry over: verified quotes against unverified prose, fail open, break-even by the number of calls, "not found" proves nothing, and no hook that diverts work automatically. Its Phase 0 was never run, and the skill it planned was never built (D14).

---

## 0. Decisions

Decided by this spec; the owner's answers of 2026-10-27 to its open questions are in §0.1.

**Both parts**

- **D1. Quality is protected by verification, never by trust in a model.** What reaches Claude or the owner as fact is what code checked itself: a quote equal to the file's text, an exit code, a list of failing test names, a line a watcher saw, a count from a transcript. Everything a model writes is labelled a hint and carries a verified quote (§6.2, §7.4).
- **D2. The largest saving needs neither part.** In the night measured, cache rewrites after idle wakes were 39% of the price-weighted cost; waiting turns 28%; calls made at contexts above 300K tokens 74%. Coordinator practice (§3) is estimated at **40–50%** of the price-weighted cost; delegation through the plugin adds **about 5–7%** on top. The monitor exists mainly to make the practices happen: it says when an agent is due for a hand-off, when a wait has gone stale, and what each agent is doing, so the coordinator need not wake agents or re-read their logs to find out.
- **D3. Nothing opens a port or stores an address.** The plugin is a stdio MCP server; the monitor is a timer that reads files. Both call the local `ling`, whose launcher finds the node by name or id. A second node is reached only through the existing SSH pairing (§5, §8).
- **D4. A benchmark night is never disturbed.** Before any model call both parts run the same busy check (§5.3): during a SWE-bench run the plugin uses another node serving the same model or answers `busy` within seconds, and the monitor drops to its deterministic checks. Neither queues behind the benchmark. Model-free work (`lookup`, `watch`, the monitor's checks) runs at any time.
- **D5. Everything is Rust in `ling-rs/`, as launcher subcommands** (`ling delegate …`, `ling monitor …`), with no Codex patch: a client install has no Python, and both parts share the transcript reader, the quote verifier and the busy check.
- **D6. Transcripts never leave the machine they are on, and neither part writes into them** (§8).

**Part A, the plugin**

- **D7. Mightling takes a task class only after it is measured there.** A whitelist of task classes (§6.1), each starting on probation in shadow mode, where Mightling's result is compared side by side with Claude's own and counts for nothing; **promotion needs 20 shadow comparisons** with no severe miss, and the owner confirms it (§6.3). Design, security-sensitive code, exactness-critical engine kernels, releases and history operations, and anything without an objective check are excluded and never promoted.
- **D8. A class is demoted automatically**: at once on **any revert** of a delegated change or any shadow miss, and when its **acceptance falls below 60%** or its measured net saving is not positive over the last 20 delegations (§6.3).
- **D9. A failed delegation costs Claude only the attempt.** Claude does the task itself, with no retry, and the tokens the failed attempt spent are charged against the class, so the net saving is measured honestly (§6.4).
- **D10. `lookup` and `watch` use no model.** They need no probation, cannot lower quality and ship first (P1).
- **D11. No hook routes work.** The one hook records outcomes (§4.5); the decision to delegate stays with the model that knows whether the answer matters.

**Part B, the monitor**

- **D12. The monitor's facts are deterministic and authoritative.** Thresholds, stalls, usage limits, loops, rule violations and token counts come from plain code (§7.3). The local model writes only a short digest per agent and labels for unusual log lines (§7.4); where a digest and a fact disagree, the fact wins and the disagreement is counted.
- **D13. What leaves the node is facts in fixed templates.** An owner alert goes out through the user's messenger bridge (on the owner's machine `owner-notify`, Telegram), which is an external service, so its body is a fixed template of facts: the alert's kind, the agent's ordinal and type, counts, times, a reset time. No transcript text, no digest, and not the subagent's task description, which the coordinator wrote and which is content (§7.5). The summary file is read by the Claude Code coordinator, so its content reaches Anthropic: entries about Claude Code agents may carry digests and quotes (content Anthropic already holds), while entries about `ling` sessions and Night Shift carry facts only, unless the owner opts in (§7.5).

**Carried in from the earlier analysis**

- **D14. The `ling-delegate` skill does not exist, and this plugin replaces it.** The line in the owner's global Claude Code instructions that names it applies "when it is installed"; it is not installed. The install step proposes replacing that name with this plugin's `ask` (§4.3).

### 0.1 The owner's answers (2026-10-27)

Every question is decided. Where the owner chose, the answer is theirs; where the owner left a question unselected, the default this spec proposed was recorded as the decision ("default kept"); two questions were not put to the owner, and their proposed defaults stand.

1. **Alert content.** *Decided:* alerts are short and carry no task text: not the subagent's `.meta.json` description, no transcript text, no digest. An alert names the agent's ordinal and type; the summary on the node names the task (D13, §7.5).
2. **Alert hours.** *Decided:* quiet hours **23:00–07:00 local time**. During them only urgent alerts go out: usage limits (C3), a failed release, a night blocked or failed. Other alerts are not sent then; they stay in the summary and on the status page. The rate limit of §7.5 applies at all hours.
3. **Release alerts.** *Decided (default kept):* from the transcripts only; no polling of GitHub with `gh`.
4. **Expected wait durations** (§7.3, C2). *Proposed default stands* (not put to the owner): CI run 30 min, Night Shift until the window's end, a GPU window until its stated end, a shell wait 15 min, a background subagent 60 min, in `[monitor.waits]` of the user's config.
5. **`ling` and Night Shift in the coordinator's summary.** *Decided (default kept):* facts only; no digests of the owner's own `ling` sessions or of Night Shift (D13).
6. **The product alert channel.** *Decided:* the messenger bridge the user turned on (Signal, Telegram or Matrix, if one is set up), plus the `ling web` status page (§7.5). This needs a send entry point in the bridges, which does not exist yet (M2). With no bridge set up, alerts reach only the summary and the status page. On the owner's machine `~/.local/bin/owner-notify` (Telegram) serves until the entry point exists.
7. **The summary as a plugin tool.** *Proposed default stands* (not put to the owner): no sixth tool; the coordinator reads the summary file with `Read`, which costs the same.
8. **The digest during SWE-bench and Night Shift runs.** *Decided (default kept):* no Qwen digest while a SWE-bench run holds the model; the monitor runs its deterministic checks only (D4, §7.4). A Night Shift run outside a benchmark was not asked separately; the proposed rule holds there: the digest is allowed, one model call at a time.
9. **Digest retention.** *Decided (default kept):* digests are kept 7 days, then deleted; facts are kept 30 days for the weekly comparisons.
10. **The Claude-token spend of P0's replay.** *Decided:* a small sample first, at most **about 50M Claude tokens**, enough to rank the task classes; the full replay set of §6.5 runs only if the sample is promising (§10, P0).

---

## 1. Goals and non-goals

**Goals**
- Fewer price-weighted Claude tokens per finished task, measured from Claude Code's own usage records.
- No outcome worse than Claude alone: the same merge and acceptance outcome on the replay set (§6.5) and in shadow audits, and no reverted delegated change.
- The coordinator and the owner learn of a stall, a usage limit, a loop or a broken rule within one check interval, without reading logs.
- The same monitor answers a Mightling user's "is my agent stuck?" for `ling` sessions and Night Shift.
- One install step for each part; nothing changes for a person who installs neither.
- A side effect worth stating: file contents Mightling reads in a delegation, and every transcript the monitor reads, stay on the owner's machines.

**Non-goals**
- Running Claude Code itself on the local model, or routing Claude Code's small background model to it.
- Mightling reviewing Claude's work. A weaker reviewer adds noise. (The monitor's digest describes what an agent is doing and whether it looks stuck; it does not judge the work.)
- Writing that needs judgement: specs, designs, user-facing text, release notes.
- A `PreToolUse` hook that diverts Claude's searches automatically. It would take the decision from the model that knows whether the answer matters, and it adds text to every call.
- The monitor acting on what it sees: it never stops, restarts, steers or messages an agent. It reports; the coordinator and the owner act.

---

## 2. Evidence: where the tokens went (measured 2026-10-27)

**Source.** The subagent transcripts of one Claude Code coordinator session on the owner's machine, 2026-10-26 00:00 to 2026-10-27 12:00 UTC (the logs stay outside the repository, under `~/.claude/projects/<project>/<session>/subagents/`). Responses were deduplicated by `message.id`, because a transcript writes one line per content block.

**The corrected baseline.** The figures first quoted for this night (37 agents, 7,274 calls, about 2.1 B input tokens, 1.95 B of them cache reads, 3.3 M output) counted lines; counting lines over the same window gives 7,838 "calls" and 2.30 B input. Deduplicated, the night is the baseline every later comparison in this spec uses:

| | |
|---|---|
| Agents active | **38** |
| Model responses | **4,392** |
| Input tokens | **1.35 B**, of which 1.24 B (92%) cache reads |
| Output tokens | 3.5 M |
| Context per call | median 231K, p90 691K |
| Three longest-lived agents | 425 M input (32%), peak contexts 770K–900K |
| Most agents active in one 10-minute window | 16 |
| Cache rewrites after idle wakes | **39%** of the price-weighted cost |

**Weights.** List-price ratios: input 1, five-minute cache write 1.25, cache read 0.1, output 5. "Price-weighted" means tokens times these weights. Whether a subscription's limits track these ratios is not published; the assumption is that they roughly do.

### 2.1 The shape of the bill

| Part | Share of price-weighted cost |
|---|---|
| Cache writes | **49%** |
| Cache reads | 45% |
| Output | 6% |

**The mechanism is the cache expiring while an agent waits.** 183 responses rewrote more than half of their context into the cache, and 160 of them came 5 to 60 minutes after the agent's previous response: past the five-minute cache lifetime. Those 160 rewrites alone are **39% of the night's price-weighted cost**. Each one pays 1.25 times the agent's whole context, so its cost scales with context size: a wake-up at 800K costs about 1M token-equivalents, at 60K about 75K.

### 2.2 What the calls did

Responses classed by their first tool call (command patterns; the edges overlap):

| Kind | Calls | Share of calls | Share of price-weighted cost |
|---|---|---|---|
| Waiting and polling (`sleep`, `until`, `timeout`, `gh run watch`, monitors) | 329 | 7.5% | **28.1%** |
| Search and read (`rg`, `grep`, `cat`, `sed -n`, `git log/show/diff`, code index) | 1,699 | 38.5% | 22.8% |
| Edits and file writes | 502 | 11.4% | 9.1% |
| Git writes, remote shells, messages, web | 545 | 12.4% | 12.4% |
| Build and test | 108 | 2.4% | 1.5% |
| Other shell commands, text turns | 1,232 | 28.0% | 26.2% |

Runs of three or more consecutive calls of one kind: 197 search-and-read runs (11.7% of the cost) and 31 waiting runs (23.2%); build-and-fix runs are negligible (4 runs, 0.2%).

### 2.3 What the whitelist can reach

The agents' briefs, classed by keyword rules (checked by eye) over the session's 158 agents since 2026-10-15, fall mostly in the excluded classes of §6.1; under a tenth of the calls (about 7%) belong to briefs that a whitelisted class could take whole. **Few whole briefs are delegable.** What is delegable lives *inside* the briefs: the search-and-read runs (`ask`, `lookup`), the waiting runs (`watch`), and the small mechanical steps of larger work (`task`). The whitelist therefore covers a minority of the workload, and the practices of §3, which need no delegation, cover the rest.

### 2.4 The break-even rule (carried over, re-fitted to this night)

With context **C**, **k** calls Claude would make itself, **t** tokens those calls bring back, a brief **b** and a result **r**, and each added token costing w = 1.25 + 0.1·R over its R later re-reads (R ≈ 40):

> delegation pays when (k − 1)·0.1·C + (t − r)·w > b·(5 + w)

At this night's median C = 231K, a five-call exploration returning 6K tokens, replaced by a 300-token brief and an 800-token result, saves about 4 × 23K + 5.2K × 5.2 − 3K ≈ 116K token-equivalents. One small lookup never pays through `ask`; `lookup` (model-free, one call) is the tool for it. The skill encodes this as: **use `ask` when you expect three or more searches or more than ~5K tokens of output for one question; use `lookup` for a single symbol or string.** The same arithmetic prices the monitor's summary: one `Read` of a 1,500-token file replaces the several log reads and agent wake-ups a coordinator makes at a check-in (§7.7).

---

## 3. Coordinator practices (the larger lever)

These need neither part (D2). They belong in the coordinator's own instructions; the plugin's skill repeats them, and the monitor's checks say when each one is due.

1. **Retire an agent whose context passes ~300K.** It writes a one-page hand-off note (state, decisions, open items, paths), and a fresh agent continues from the note and a short brief. In the night measured, 38% of calls ran above 300K and carried 74% of the price-weighted cost. *The monitor warns at ~250K and marks the hand-off due at ~300K (§7.3, C1).*
2. **Cap concurrent expensive agents at 8–10.** The night peaked at 16 in one 10-minute window. More agents than the owner can follow produce more waiting, more idle gaps past the cache's lifetime, and more contexts kept alive only to be rewritten. *The summary counts the agents active in the last 10 minutes.*
3. **Wait with a watcher that wakes the agent once, on a real result.** A background shell command (or the plugin's `watch`) that ends when the condition holds, never a `sleep` loop of agent turns. Any turn after more than five idle minutes rewrites the whole context into the cache. *The monitor counts idle-wake rewrites per agent and their cost (C7).*
4. **Prefer a fresh short brief to resuming a long history.** A status check or a follow-up question goes to a fresh agent with the facts it needs, not to the 800K agent that did the work.
5. **Read the monitor's summary at a check-in** instead of re-reading agent outputs or waking agents to ask how they are doing (§7.5).

**Estimated savings on the measured night** (replaying its 4,392 responses under each rule; the rules overlap, so they do not add):

| Practice | Saving of price-weighted cost | Assumption |
|---|---|---|
| Waiting runs collapsed to one wake-up each (3) | **≈ 25%** | One call per waiting run (169 runs, 329 calls) instead of several |
| Retirement at 300K, fresh agent at 60K (1, 4) | ≈ 30% (lower bound) | Only cache *reads* scale with the smaller context |
| The same, if cache *rewrites* scale too | ≈ 69% (upper bound) | A wake-up after a gap rewrites 60K–300K instead of up to 900K |
| Both together | 53–74% | The range between the two assumptions, before the recovery cost |

The fresh agent's cost of recovering context (re-reading files it needs) is not modelled. It is paid once per retirement against rewrites paid on every late wake-up. A **planning figure of 40–50%**, after the recovery cost and imperfect adherence, is defensible; the replay of §6.5 and the monitor's weekly comparison (§7.7) measure it. Delegation through the plugin adds to this on a smaller base: the search-and-read runs it can take are 11.7% of the cost, so **at most about 5–7%** more once the practices are in place.

---

# Part A — the delegation plugin

## 4. The plugin

### 4.1 Shape (Claude Code's plugin format)

Checked against Claude Code's public plugin documentation (plugins overview, manifest reference, components, sub-agents, MCP) on 2026-10-27. The standard layout, with every component in its default place:

```
mightling/                      # the plugin root
├── .claude-plugin/plugin.json  # name "mightling", version, description, userConfig
├── .mcp.json                   # the stdio server "mightling"
├── skills/delegate/SKILL.md    # the delegation rules (§4.3)
├── agents/delegate.md          # a subagent restricted to the plugin's tools (§4.4)
├── agents/diff-reviewer.md     # the diff review of §6.2, no tools
├── hooks/hooks.json            # records outcomes only (§4.5)
└── bin/                        # empty: the commands come from Mightling's own install
```

- **Manifest.** `name: "mightling"`, a `version` stamped from `dreamference.__version__`, and `userConfig` for three values: `node` (a node *name* or id, optional; the launcher's tiers decide otherwise), `max_parallel` (default 2) and `allow_classes` (the classes the owner switches on; empty means "probation only").
- **The MCP server** (`.mcp.json`): `{"mightling": {"type": "stdio", "command": "ling-delegate", "args": ["mcp"], "env": {"DREAMFERENCE_DELEGATE_NODE": "${user_config.node}", "DREAMFERENCE_DELEGATE_MAX_PARALLEL": "${user_config.max_parallel}", "DREAMFERENCE_DELEGATE_ALLOW_CLASSES": "${user_config.allow_classes}"}}}` (`claude-plugin/.mcp.json`; invokes the standalone `ling-delegate` binary — or `ling delegate mcp` — so the plugin works even if the installed `ling` predates a `ling-delegate` update). Its tools are named `mcp__plugin_mightling_mightling__<tool>`, the form subagent `tools` lists and permission rules use.
- **Size of results.** Claude Code warns above 10,000 tokens of MCP output and cuts at 25,000 by default (`MAX_MCP_OUTPUT_TOKENS`). Every tool caps its result at 1,500 tokens, except `task` and `night fetch`, which cap at 8,000 and otherwise return paths (§4.2).
- **Time.** A stdio MCP server has no per-request timer in Claude Code, so `watch` and `task` may block for as long as they need; each takes its own `timeout_s`.
- **Not used:** plugin monitors (experimental, and they do not start under `claude -p`, which the evaluation and headless coordinators use; the session monitor of Part B is a timer for this reason); `bin/` executables (claude.ai and Cowork refuse a plugin that has them, and `ling` is already on `PATH`).
- **Install, local:** `claude --plugin-dir <checkout>/claude-plugin` while developing; for daily use, `/plugin marketplace add <checkout>` (a `.claude-plugin/marketplace.json` at the repository root listing one plugin) and `/plugin install mightling@mightling`. The product path is §9.

### 4.2 The MCP server `mightling` and its five tools

`ling delegate mcp` prints nothing but MCP frames on stdout, and keeps a ledger (§6.3) under `$CODEX_HOME/claude-plugin/`.

| Tool | Model? | What it does | What Claude receives |
|---|---|---|---|
| `lookup` | **no** | `ling-code def/refs/callers/impl/outline` (`rg` mode not built yet) over a stated scope | Hits as `{path, start, end, quote}`, every quote read back from the file by the server; for zero hits, "no literal match for `X` in scope `Y` (N files searched)", a fact about the string, not about the concept |
| `run_test`, `git_log`, `diff_stat` | **no** | Exact model-free tools built beside `lookup` (2026-10-28, `ling-rs/delegate/src/tools.rs`): `run_test` runs one named `pytest`/`cargo`/`npm` test inside `ling sandbox -P :workspace` (network off); `git_log` and `diff_stat` run through `ling_safegit` | Bounded test output/status, file/line-range commit history, or working-tree diff stat |
| `ask` | yes | `ling exec --ephemeral -s read-only -C <repo> --output-schema ask.schema.json` on one question about code, files, logs or session rollouts *(not built yet)* | `{status, answer (≤120 words, a hint), evidence[{path, start, end, quote, why}], searched[], confidence, full_output}`; each quote verified (§6.2) |
| `task` (plus `task_verdict`) | yes | One whitelisted change by `ling exec -s workspace-write` in a clean linked worktree supplied by the caller, then the class's checks and Python gates run by the server inside `ling sandbox -P :workspace` (`task_verdict` records `accept\|reject\|reverted\|shadow_miss` in the ledger) | The diff (≤8,000 tokens, else its path and a refusal for that class), the checks' facts (§6.2), the policy findings, Mightling's own summary marked unverified |
| `night` | yes | `queue`: `ling night add --test <cmd> --best-of N <task>` in the repository; `fetch <id>`: the chosen `night/<id>` branch, re-checked by the server exactly as `task` *(not built yet)* | `queue`: the task id. `fetch`: the same shape as `task`, plus the chooser's `why` |
| `watch` | **no** | Waits for one condition and returns once | `{met, observed, at}`: the exit code, the matching line verbatim, the status seen |

**The brief** for `ask` and `task` is a template, never free prose, because Claude's output costs five times its input:

```
CLASS: <one whitelisted class, §6.1>
GOAL: <one sentence>
SCOPE: <paths>
DONE WHEN: <the check: a command, or for ask what counts as an answer>
LIMITS: <files that must not change; at most n evidence items>
```

The server refuses a brief whose `CLASS` is not active (or on probation, which runs as shadow, §6.3), whose `DONE WHEN` names no command for a `task`, or whose scope includes an excluded path (§6.1).

**`watch` conditions** (closed list; no free-form script): a process id exits; a file contains a line matching a pattern; a command from an allowlist (`git`, `gh run view`, `ling night show`, `ling-admin night status`, `systemctl --user is-active`, `curl` to loopback) reaches a given exit code or prints a pattern, polled at an interval of 15 s or more; a URL on loopback, or on a node by name, returns a status; a `task` or `night` id finishes; a git ref moves. The same code runs as `ling delegate watch …` from a shell, so an agent can also start it with Claude Code's background shell and be woken by its completion.

### 4.3 The skill `delegate`

`skills/delegate/SKILL.md`, one-sentence description: "Hand a whitelisted task to the local Mightling; quotes and checks are verified, prose is a hint." The body holds:

- **The rules of fact.** A verified quote, an exit code and a failing-test list are facts. The answer sentence and the summary are hints. `not_found`, `partial`, `unverified` or `low` sends Claude to do the work itself when it matters. Absence is never inferred from a model; a `lookup` zero-hit is a fact about the literal string only.
- **When to use which tool:** the break-even rule of §2.4; `watch` for every wait longer than a minute.
- **The active classes**, read at session start from `ling delegate classes --json` (the ledger's current state), not written into the skill, so a demotion takes effect at once.
- **The brief template**, and the fallback rule: on `busy`, a refusal or a failed check, do the task yourself, with no retry.
- **The practices of §3**, in five lines.
- **Precedence.** A user's own instructions about exploration tools win. On the owner's machine the global instructions route single lookups to another code-navigation tool and multi-step lookups to `ling-delegate`, which does not exist (D14); the install step suggests replacing that name with this plugin's `ask`. The user decides.

**The loop-back problem.** Mightling's skills planner links `~/.claude/skills` into `ling`'s catalogue. A plugin skill lives under Claude Code's plugin cache, not there, so it is not linked; P1 still adds `delegate` to the planner's built-in exclusions, so `ling` is never told to delegate to itself.

### 4.4 The subagent types

- **`mightling:delegate`** (`agents/delegate.md`): `tools` lists only the five `mcp__plugin_mightling_mightling__*` tools and `Read`. A coordinator gives it a brief; it calls the tools, checks the facts, and returns at most a page. Its point is the context it does not inherit: it starts fresh, so its waiting and its tool results never enter the coordinator's 500K context. (Plugin subagents may not set `mcpServers`, `hooks` or `permissionMode`, so the plugin's server must be enabled for the session, which installing the plugin does.)
- **`mightling:diff-reviewer`** (`agents/diff-reviewer.md`): no tools. Its prompt is the brief, the diff and the checks' output, nothing else (§6.2).

### 4.5 Hooks: outcomes only

`hooks/hooks.json` has one `PostToolUse` hook on `Bash` that matches `git commit`, `git merge`, `git revert` and `git apply`, and runs `ling delegate record` to attribute commits to accepted delegations by patch id (§6.3). It never changes a tool call and adds no text to Claude's context (D11).

---

## 5. Where it runs

### 5.1 On a node, and on a client

- **On a node** (a machine with `~/.config/dreamference/node-id`), `ling` uses loopback.
- **On a client** (a laptop with Claude Code and a checkout), the MCP server runs on the client, and the local `ling` finds the model server through the launcher's tiers: `DREAMFERENCE_VLLM_HOST`, `MIGHTLING_NODE`, the node remembered in `$CODEX_HOME/node.json` (found again by id), then an mDNS browse. The plugin passes `userConfig.node` as `MIGHTLING_NODE` when set. It never stores or passes an address.
- **`task` runs beside the code**, on the machine where Claude Code's checkout is: `ling exec` there, the model on the node. The alternative, a job on the node through `node run`, is rejected for P2: the code and the toolchain the checks need are on the client, and a node job has the home folder hidden and no GPU.
- **`night` runs where the repository's Night Shift runs.** On a client, `/night add --on <node>` runs a task once (`--best-of` with `--on` is refused today; `--on` is MIGHTLING_NODE §18.8), so best-of-N from a client is a P3 change (§9). Until then, `night` from a client is refused for classes whose evidence is best-of-3.

### 5.2 Concurrency and latency

- The model server serves 8 requests at once. The plugin holds at most `max_parallel` (default 2) delegations per machine, with a lock file under `$CODEX_HOME/claude-plugin/`; the monitor's digest holds at most one more (§7.6).
- Expected times: `lookup` under 2 s; `ask` 30–120 s; `task` 5–30 min. A `task` is best called from a `mightling:delegate` subagent running in the background, whose completion is the coordinator's next turn, so the wait costs the coordinator no calls.

### 5.3 The busy check (shared with the monitor)

Before any model call, in this order, all within about 3 s:

1. **The runner lock** (`$CODEX_HOME/night/runner.lock`, on a node): held by a SWE-bench run → that node is busy. Held by Night Shift → go ahead; Night Shift gives way to outside requests.
2. **The model gate:** a one-token completion with a 3 s timeout. A 503 from the gate (which answers 503 with `Retry-After: 0` to requests from outside a running benchmark's network) → busy. No retry is sent; `ling` itself would retry about 30 times, so neither part ever starts `ling exec` against a gate that refused.
3. **Another node:** `ling node list --json` names the nodes on the network; another one is used only if it serves the same model (by `/v1/models`, matched with `model_key_for_served_id`), is not advertising `state=stopped`, and is not one the owner reserved (`[claude_plugin] exclude_nodes`, by name; the node kept for engine development goes there). A paired node is reached through `NodePairing.resolve`, by id. *The monitor skips this step: its digest is not worth another node's time.*
4. **Otherwise** the plugin's tool answers `{"status": "busy", "reason": "benchmark running on <node>, until about <time>"}`, and the skill tells Claude to do the work itself; the monitor writes "digest skipped: model reserved" and runs its deterministic checks only.

`lookup`, `watch` and the monitor's checks skip all of this: they use no model.

---

## 6. Quality guarantees

### 6.1 The capability map

A class is on the whitelist only if (1) there is measured evidence that Mightling does work of this kind, (2) a machine can check the result, and (3) a wrong result is cheap to detect before it lands. **No class has a measured acceptance rate yet**; the evidence column says what supports trying it, and every model-backed class starts on probation (§6.3).

| Class | Tool | Evidence that supports it | Objective check | Starts |
|---|---|---|---|---|
| L1 Lookups with quotes | `lookup` | Deterministic: the index and `rg` | Quotes re-read from the files | **active** |
| L2 Multi-step questions with quotes | `ask` | Localisation in SWE-bench runs: in the 100-task round, a wrong file accounts for only 5 of the 32 failures; no question-answering rate is measured | Quotes verified; Claude decides from quotes only | probation |
| L3 Log and rollout summaries with quotes | `ask` | None measured; the reading half is L2's, and the deterministic extract carries the facts | Deterministic extract first (failing tests, errors with file:line, exit code); model excerpts verified | probation |
| W1 Waiting and monitoring | `watch` | No model | The plugin observes the condition | **active** |
| T1 Lint, typing and format fixes | `task` | Mechanical; the gates are the oracle | ruff/mypy counts per file only fall; tests pass; no other file changes | probation |
| T2 Test-only CI fixes | `task` | The single-run SWE-bench resolve rate, 68/100 (round `im100-default`) and 22/50 to 26/50 on fresh tasks; 31/50 with best-of-3 (night 13, p = 0.022) | The failing CI test passes; the rest of the suite still passes by name; the assertion rules of §6.2 | probation |
| T3 Mechanical refactors with tests | `task` | As T2; the change is defined by a rule, not by judgement | Existing tests pass unchanged; no test file touched; the diff is the stated rule (rename, move, signature) | probation |
| T4 Docs updates checked against the code | `task` | As L2 for the reading half | Every changed docs line that names code cites a `lookup`-verifiable path or symbol; the docs build and docs tests pass | probation |
| T5 Small UI items with e2e tests | `task` | The weakest evidence: none measured on UI | The named e2e test passes, and the rest of the UI suite by name | probation, last |
| N1 Best-of-N for T1–T5 | `night` | Night 13's 31/50 against 22/50 | As the class it carries, re-run by the plugin | probation, after T-classes |

**Excluded, never promoted** (the server refuses a brief whose scope touches them, by path rules in `[claude_plugin] exclude`):

- **Design decisions**: specs, architecture, API shape, user-facing wording.
- **Security-sensitive code**: sandboxing, the egress paths and allowlists, pairing and keys, release signing, the installers, permission and approval logic, anything under `/airgapped`.
- **Exactness-critical engine kernels**: code whose correctness is bit-exact output or numerical equivalence (CUDA kernels, speculative verification, the model step).
- **Releases and history**: tags, version bumps, changelogs, publishing, rebases, history rewrites, force pushes, merges into the main branch.
- **Anything without an objective check**: a task whose `DONE WHEN` cannot be a command (or, for `ask`, verifiable quotes).

**Build status, 2026-10-28.** By the owner's decision to move more work to the local model, `task` (T1 to T5) is built and on probation, with `task_verdict` and the ledger of 6.3 (`ling-delegate ledger`, gate: at least 20 decided tasks, 95% accepted, no revert, no shadow miss), and the model-free exact tools `run_test`, `git_log` and `diff_stat` beside `lookup`. This build checks, in the server, items 1 (a clean linked worktree supplied by the caller, not a worktree the server creates), 4 (test files, and T2's assertion heuristic), 5 (scope) and 6 (size) of 6.2 and runs the acceptance test and the Python gates; the acceptance test and the gates run through `ling sandbox -P :workspace` with the network off, and a task is refused when the sandbox does not start (6.2 item 2); it does not yet compare failing tests by name before and after (item 3), read `[claude_plugin] gates` from `dreamference.toml` or apply the automatic demotions: the reviewer is the caller. Details: `docs/dev/claude-plugin.md`.

### 6.2 Verification per class

**Quotes (L1–L3, T4; and the monitor's digest, §7.4).** The verifier reads each cited file and checks that the quote equals its text at the cited lines (whitespace runs normalised, nothing else) and that the path is inside the scope. A failing item is removed and counted; with no item left the status becomes `unverified`. Claude builds only on quotes; the answer sentence is a hint. Claude also checks that the quotes answer *its* question: a verified quote can still answer a different one, and P0 counts those cases by hand.

**Logs (L3).** The full output is saved and its path returned. A deterministic extract comes first, by rule: failing test names with their assertion lines (pytest, cargo test, ctest, npm), compiler errors with file:line, the exit code, the last 40 lines. The model adds at most 150 words and verified excerpts. Below 1,500 tokens of extract, no model is called.

**Diffs (T1–T5, N1).** A diff is offered to Claude only after all of these hold, each checked by the server, not by `ling`:

1. **Where it ran.** A worktree at `$CODEX_HOME/claude-plugin/worktrees/<id>` from a base the server fixes (`git stash create`, or `HEAD` when the tree is clean), so the caller's uncommitted work is included and the caller's checkout is never touched.
2. **The class's checks pass**, run by the server after `ling` stops, through `ling sandbox` with workspace-write and no extra writable roots (Night Shift's runner policy): the `DONE WHEN` command plus the project's gates, which the plugin reads from `[claude_plugin] gates` in the repository's `dreamference.toml` (for this repository: the touched test files, `tests/test_code_quality.py` and `tests/test_typing.py`).
3. **Failing tests are compared by name**, before (on the base) and after. A test that passed before and fails after rejects the diff. "Pre-existing" is never accepted without the comparison: the failure analysis found failures dismissed as pre-existing from a `tail -6` that showed no names.
4. **Tests are not bent to pass.** Outside T2, the diff may not touch a test file. In T2, it may not remove or weaken an existing assertion, add a skip, `xfail` or `ignore`, or widen a tolerance, unless the brief's `LIMITS` names that assertion. The failure analysis is the reason: 4 of the 7 easy losses came from tests (two rewrote a test the change had broken), and 21 of 100 patches removed lines from existing tests.
5. **Scope.** No file outside `SCOPE`, none under an excluded path, nothing in `LIMITS` changed, no new dependency, no lockfile change unless the class allows it.
6. **Size.** At most 400 changed lines (T1, T3) or 150 (T2, T4, T5); a larger diff is refused, not truncated.

Then **the diff review**: Claude starts `mightling:diff-reviewer` with the brief, the diff and the checks' facts, nothing else. Its context is about 10K tokens, so it is cheap at any model. It answers `ACCEPT` or `REJECT: <one line>`, judging whether the diff does what the brief asks and only that: missed second code paths (the commonest failure, 8 of 32), behaviour the brief did not ask for (5 of 32), and leftovers. Only an `ACCEPT` is applied, by Claude, with `git apply --3way` or a cherry-pick of the worktree's commit. The reviewer uses the session's own model until P2's data shows a cheaper one reaches the same verdicts.

**Waiting (W1).** `watch` returns what the plugin observed: the exit code, the line that matched (verbatim, with its file and line number), the HTTP status, the time. No model is involved, so the result is a fact.

### 6.3 Routing by measured success: probation, promotion, demotion

**The ledger.** Every delegation appends one record to `$CODEX_HOME/claude-plugin/ledger.jsonl`: class, tool, repository (a hash of its path), node, times, Mightling's token counts, the Claude tokens spent on the brief, the result and the review (from Claude Code's usage records for that turn, read with the monitor's transcript reader; otherwise estimated from sizes), the verification outcome, the review verdict, and, for an applied diff, its `git patch-id --stable`. No content: no brief text, no code, no answer.

**Outcomes per delegation:** *accepted* (verified, reviewed, applied or used); *rejected by verification* (a check, a policy rule or the quote check failed); *rejected by review*; *reverted later* (the hook of §4.5 sees a `git revert` of a commit carrying an accepted patch id, or a later commit whose patch id is its inverse, within 30 days); *shadow miss* (below).

**States of a class:** `probation` → `active` → `demoted` (→ `probation` again only by the owner's command).

- **Probation is shadow mode.** Claude does the task itself as it would without the plugin; Mightling's result is produced in the background (a `mightling:delegate` subagent) and compared side by side, and is never used. Comparison per class: L2/L3, Claude's own answer's file:line is among the verified evidence and the reviewer judges the answers equivalent; T-classes, both diffs pass the same checks and the reviewer, given both, judges them equivalent in effect. Probation costs tokens (both runs) and saves none; it is the price of the evidence.
- **Promotion** needs **at least 20 shadow comparisons** in the class with at most one non-severe disagreement and no *severe* one: a result that passed every check and the review but was wrong by Claude's own result. Promotion is proposed by `ling delegate classes`, and the owner confirms it (`ling delegate promote <class>`).
- **Active classes keep a 10% shadow sample**, so a drift is seen.
- **Automatic demotion**, written by the server, effective at the next tool call:
  - at once, on **any** *reverted later* or *shadow miss* in the class;
  - when **acceptance over the last 20 delegations falls below 60%**: below that, the failed attempts' cost eats the saving (§6.4);
  - when the class's measured net saving over the last 20 is not positive.
- **A model change resets all model-backed classes to probation.** The evidence belongs to the model that produced it, so a different `model_key_for_served_id`, or a new `ling` minor version, starts the classes again.
- `ling delegate classes` prints the table (state, n, acceptance, reverts, net saving); the skill reads its JSON form. A demotion also raises a monitor alert (§7.5).

### 6.4 Fallbacks and honest accounting

- On `busy`, a refusal, a timeout, a failed check or a `REJECT`, Claude does the task itself, at once, with no retry and no second delegation of the same task.
- The ledger charges the failed attempt to the class: the Claude tokens of the brief, of reading the result and of the review, plus the time. The **net saving** of a class is Σ(counterfactual − actual) over its delegations, where the counterfactual is the class's median Claude cost for doing the task alone, measured in its shadow runs, and the actual includes every failed attempt's cost and the fallback's own cost.
- What Mightling spends is free in Claude tokens but not in time or power; the ledger records its wall time so the owner can see the trade.

### 6.5 Evaluation before rollout: the replay set

**Units**, extracted by a P0 script (the monitor's transcript reader, run once over history) that reads the transcripts where they are and writes the set to `~/.local/share/dreamference/claude-plugin/replay/` (outside every repository; nothing of it is committed, and the spec and reports carry aggregates only):

- **Exploration units** for L1/L2: the 197 search-and-read runs of three or more calls (§2.2), plus a stratified sample from earlier nights to reach 150. For each, a reader writes the question the run answered and the answer it reached; that conclusion, from the run itself, is the reference.
- **Log units** for L3: the runs that read a large output (a test log, a rollout, a report) to answer a question; the same reference method.
- **Wait units** for W1: the night's 169 waiting runs, single calls included (329 calls, the classifier of §2.2); each is replayed as one `watch` condition against the recorded timeline, checking that it would have woken the agent on the same event and no later than the polling did.
- **Change units** for T1–T5: whole briefs are mostly excluded (§2.3), so these come from the history of the owner's repositories instead: commits of each class (lint and typing fixes, test-only fixes, renames and moves, docs corrections, UI fixes with e2e tests), 30 per class where history has them. The task is the commit message, the start is its parent, the hidden check is the commit's own tests and the project's gates, and the reference outcome is the commit itself, made and merged without the plugin.

**Metrics:**

- **Quality parity**, per class: the share of units where the plugin's outcome equals Claude alone's: for questions, the reference file:line is among the verified evidence and the answer is judged equivalent; for changes, the diff passes the hidden check *and* the reviewer accepts it, exactly when the reference commit did (always). A unit where Mightling's diff passes the visible checks and the review but fails the hidden check is **severe**, and every one is read by hand.
- **Token saving**, per class: Claude tokens with the plugin (brief, result, review, fallback on failure) against the reference run's own tokens from its usage records.
- **Coverage**: the share of the measured night's price-weighted cost the passing classes touch.

**Budget** (§0.1, question 10): the first replay is a small sample drawn across the classes, at most about 50M Claude tokens, enough to rank them; the full set above runs only if the sample is promising.

**The gate to enable a class** (beyond probation): parity ≥ 95%, zero severe units, and a positive median saving on the replay set. A class that fails stays off; a class without enough units waits for live shadow data.

**Noise.** Thirty units per class cannot separate 95% from 90% with confidence; the zero-severe rule and the live shadow sample carry the quality claim, not the parity percentage alone.

---

# Part B — the session monitor

## 7. The session monitor

`ling monitor` watches every agent on the node and writes down, in a page the coordinator can read in one call, what needs attention. It answers the questions a coordinator now answers by re-reading logs and waking agents, which is where the idle-wake rewrites of §2.1 come from.

### 7.1 Inputs, read-only

| Source | Where | What it gives |
|---|---|---|
| Claude Code sessions | `~/.claude/projects/<project>/<session>.jsonl` | The coordinator's own turns, its usage, its background tasks |
| Claude Code subagents | `~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl` and `.meta.json` | Each subagent's turns and usage; its type, task description and spawn depth |
| Background task outputs | The output files a session's background shells and subagents write, as the session's transcript names them | The last lines of a running wait or build |
| `ling` sessions | `$CODEX_HOME/sessions/**/rollout-*.jsonl` | Mightling's own interactive and `exec` sessions: turns, tool calls, token counts, compactions |
| Night Shift | `$CODEX_HOME/night/tasks/*.json`, the per-attempt `logs/`, the runner lock, `systemctl --user` state of `mightling-night.timer` | Queue, the task running, its attempt and window, its rollout |
| Git, read-only | `git log` in the repositories the transcripts' `cwd` names | Commits made since the last check, for the rule checks |

Every file is opened read-only; the monitor never locks, rotates, renames or writes anything under these paths (D6). It keeps a byte offset per file in its own state and reads only what was appended since the last check; a file that shrank or was replaced is read again from the start.

**The format is not a contract.** Claude Code's transcript format is undocumented and changes between versions. The reader uses a handful of fields (`type`, `timestamp`, `message.id`, `message.usage`, `message.content` blocks of `tool_use`, `tool_result` with `is_error`, `text`, and the `error` and `isApiErrorMessage` fields of an API error) and skips every line type it does not know. It counts the lines it could not class; when that share passes 5% in a check, it raises one `format-drift` alert naming the Claude Code `version` seen, and the checks that depend on the missing fields report "unknown" rather than a guess. Responses are deduplicated by `message.id` (§2).

### 7.2 The agent table

Each check rebuilds one row per agent seen in the last 24 hours: its kind (coordinator, subagent, `ling` session, Night Shift attempt), its ordinal in the session (`#7`), its type, its parent, the time of its last line, its current context, its state (`working`, `tool running`, `waiting`, `idle`, `finished`, `stopped by limit`), the pending tool call if any, and the counts of §7.3. The table is the input to every check and to the digest; it holds no message text.

### 7.3 Deterministic checks (plain code, no model)

Thresholds are defaults in `[monitor]` of the user's config (`~/.config/dreamference/config.toml`), never in a repository.

| # | Check | How it is decided | Default thresholds | Goes to |
|---|---|---|---|---|
| C1 | **Context size** per agent | Context of the agent's last response = input + cache writes + cache reads | Claude Code agents: **warn at 250K**, **hand-off due at 300K** (§3, practice 1). `ling` sessions: warn at 80% of the served `max_model_len`, and note each compaction | Summary; an alert only when a hand-off has been due for an hour |
| C2 | **Idle and stall** against the stated wait | The agent's *stated wait* is read from its pending `tool_use` (no `tool_result` yet) and its command pattern: a CI run (`gh run watch`, `gh run view` loops), a Night Shift window (`ling night`, the runner lock), a GPU window (a remote job with a stated end), a shell wait (`sleep`, `until`, `timeout`), a background subagent. Expected durations per kind (§0.1, question 4). A *stall* is no new transcript line for 1.5 × the expected duration while a wait is pending, or for 20 min with none pending and no live child process | CI 30 min, night until the window's end, GPU window until its end, shell wait 15 min, subagent 60 min | Summary; an alert for a stalled coordinator or a stuck night |
| C3 | **Usage limits** (HTTP 429) | An API error line with `error: "rate_limit"`. The kind is parsed from the message (a session limit, a weekly limit, a monthly spend limit) and the reset time ("resets <time> (<zone>)") converted to UTC; a spend limit has no reset time and says so. Every agent stopped by the same limit is grouped under it | Any one | Alert at once (one per limit and reset); "lifted" note at the reset time; summary lists the agents stopped |
| C4 | **Repeated failures and loops** | The same tool returning `is_error` 3 times in a row; the same normalised command (numbers, hashes and temporary paths masked) 5 times in 20 calls with no edit between; the same file read 4 times in 20 calls | As stated | Summary; an alert after 2 consecutive checks |
| C5 | **Rule violations** | (a) a commit since the last check whose author is not `[monitor.rules] commit_author`, or that carries a trailer matching `[monitor.rules] forbidden_trailers`; (b) a pre-push refusal in a tool result (the repository's anonymity hook prints a fixed refusal line); (c) a test run that wrote into the real home: the suite's own guard messages from `tests/conftest.py` in a tool result, or a file under the real `~/.local/bin` or directly under the real `HOME` that changed during a test command and outside any build or install command in the same window | The owner's rules (author identity, trailers) live only in the user's config | Alert at once for (a) and (c); summary for (b), alert if the same push is refused twice |
| C6 | **Reported and awaiting an answer** | The agent's last entry is a text turn with no pending tool call, and nothing followed for 10 min: a subagent whose result the coordinator has not acted on, or a coordinator whose last turn ends with a question to the owner | 10 min; 30 min for the coordinator's question | Summary; an alert for the coordinator's question after 30 min |
| C7 | **Tokens per agent per hour** | From `message.usage`, deduplicated: input, cache reads, cache writes, output, and the price-weighted total (§2 weights). An **idle-wake rewrite** is a response that wrote more than half its context into the cache 5 min or more after the agent's previous response; its count and cost are called out per agent and in total | None; it is a measurement | Summary (top agents, the hour's idle-wake share); the weekly figures of §7.7 |

For `ling` sessions and Night Shift the same checks apply where they mean something: C1 against the model's window, C2 (a night task past its window, an attempt with no rollout progress for 30 min, a runner lock held by no live process, a timer that did not start at the window), C4, and C7 in local tokens (no price). C3 does not apply; the model gate's refusals are counted under C4.

### 7.4 The local model, only where reading is needed (M1)

Two jobs need reading, and only these use Qwen through `ling`:

- **A digest per agent** active in the last hour: what it is doing, in at most 25 words, and whether it looks `progressing`, `waiting`, `stuck`, `done` or `unclear`. Input: the agent's row from the table, its facts from §7.3, and its last entries cut to about 16K tokens. Run as `ling exec --ephemeral -s read-only --output-schema digest.schema.json`, with no tools needed beyond reading the excerpt file it is given.
- **Labels for unusual log lines**: lines in tool results that no deterministic rule matched but that look like trouble (a non-zero exit, `error`, `Traceback`, `panicked`, `FAILED`), classed into a closed set: `benign`, `environment`, `test failure`, `build failure`, `network`, `permission or sandbox`, `limit`, `unknown`.

**Every model statement carries a quote from the log** (`{file, line, quote}`), checked by the quote verifier of §6.2 against the transcript; a statement whose quote fails is dropped and counted, and a digest with no verified quote is shown as "no digest". **The deterministic facts stay authoritative** (D12): if the digest says `progressing` while C2 says stalled, or `stuck` while a tool is running inside its expected wait, the summary shows the fact, marks the digest as contradicted, and the disagreement count feeds M1's gate.

**Budget.** At most 12 agents per hourly run, one call at a time, each about 16K tokens in and 300 out. The busy check of §5.3 runs first; when the node's model is reserved by a SWE-bench night the digest is skipped and the summary says so (D4).

### 7.5 Outputs

**The summary** (`~/.local/share/dreamference/monitor/summary.md`, `[monitor] state_dir`, and `summary.json` with the same content for programs), rewritten whole at every check (written to a temporary file and renamed), capped at 1,500 tokens:

1. Open alerts, newest first.
2. Waiting for an answer (C6).
3. Stalled or looping (C2, C4).
4. Over the context thresholds (C1), with the hand-offs due.
5. Usage limits and their reset times (C3).
6. The last hour: agents active, the top five by price-weighted tokens, the idle-wake rewrite share (C7).
7. Digests (M1), each with its quote, for Claude Code agents; facts only for `ling` sessions and Night Shift (D13).

The coordinator reads it at each check-in (practice 5), in place of re-reading agent outputs and transcripts.

**Owner alerts** go through the messenger bridge the user turned on (Signal, Telegram or Matrix), through a send entry point the bridges gain in M2, and are always shown in the summary and on the status page; with no bridge set up, those two are the only outputs (§0.1, question 6). Until the entry point exists, `[monitor] notify_command` runs a command with the message as its argument; on the owner's machine that is `~/.local/bin/owner-notify`, the Telegram bot. Alert kinds: usage limit hit and lifted (C3); repeated failures and loops (C4); rule violations (C5); a stalled coordinator (C2); a stuck night (C2 for Night Shift); a release published or failed (seen in the transcripts, §0.1 question 3); a plugin class demoted (§6.3); a format drift (§7.1).

- **Fixed templates of facts** (D13), for example `usage limit (session) hit; 6 agents stopped; resets 21:00 UTC` or `agent #12 (general-purpose) stalled 47 min in a CI wait`. Short, with no transcript text, no digest and no task description (§0.1, question 1).
- **Deduplicated**: one alert per kind, subject and episode; an episode ends when its condition clears, and only then can the same alert fire again.
- **Rate-limited**: at most one alert per kind and subject per hour, and at most 6 alerts per hour in all; beyond that, one message saying how many more are in the summary.
- **Quiet hours** 23:00–07:00 local time (§0.1, question 2): only urgent alerts are sent (usage limits, a failed release, a night blocked or failed); the rest stay in the summary and on the status page.
- **Never an alert on missing input**: a transcript that cannot be read is reported in the summary.

**A status page in `ling web`** (M2; with the messenger, the product's alert channel, §0.1 question 6): a read-only `/monitor` page that renders `summary.json`, on `ling web`'s loopback port, with the route added to `ling-rs/web/policy.json` so the desktop app and `ling web` enforce the same rule. It shows the same entries as the summary, facts-only for the user's own sessions unless they opt in to digests there.

### 7.6 Runtime

- **Two user units**, installed by `ling monitor enable` and removed by `ling monitor disable`; off until enabled. `mightling-monitor.timer` runs `ling monitor check` every 5 minutes (the deterministic checks, the summary, the alerts); `mightling-monitor-digest.timer` runs `ling monitor digest` hourly (M1).
- **Bounded beside the model server** (host safety): both service units set `MemoryMax=512M`, `Nice=10` and idle I/O priority; a check is expected to finish in under 10 s by reading only appended bytes; the digest holds one model call at a time and sits inside the 8 requests the server serves.
- **No model when the model is reserved** (D4): the digest's busy check skips it; `ling monitor check` never calls a model.
- **State** under `~/.local/share/dreamference/monitor/` (the owner's choice of 2026-10-27, over `$CODEX_HOME/monitor/`): offsets, the agent table, open alerts and their episodes, digests (kept 7 days) and hourly facts (kept 30 days), all written by the monitor alone.
- `ling monitor status` prints the summary; `ling monitor check --once` runs one check by hand; `ling monitor doctor` reports the paths it reads, the Claude Code version seen and the share of unclassed lines.

### 7.7 Measured value

**The comparison.** One week with the monitor and practice 5 in the coordinator's instructions, against the corrected baseline (§2: on 2026-10-26, 38 agents, 4,392 calls, about 1.35 B input tokens, and 39% of the price-weighted cost in cache rewrites after idle wakes) and against the week before the monitor, measured by the monitor's own C7 over both weeks (its reader runs over history as well as live).

**Metrics:**

- The **coordinator's** price-weighted tokens per active hour and per finished task (a merged branch or a closed brief), the headline figure the owner asked for. The baseline's coordinator-only share is not yet separated from the 38 agents' total; M0's first run computes it from the same transcripts.
- The coordinator's tokens spent on status reading: reads of agent outputs and transcripts, and agent wake-ups that only asked for status, classed as in §2.2.
- The idle-wake rewrite share of the whole session (baseline 39%).
- Detection: the share of usage limits, stalls and rule violations the owner learned of from an alert first, and the alert count per day.

**Confounds.** Weeks differ in workload and in how many practices of §3 were followed; the comparison is per active hour and per finished task, not per week, and the report states the number of agents and tasks beside each figure.

---

## 8. Privacy and safety (both parts)

- **Transcripts never leave the node** (D6). The monitor reads them where they are and writes only under its state folder (§7.6); the plugin's ledger and replay set hold no content (§6.3, §6.5). Digests stay local and are deleted after 7 days.
- **Nothing writes into a transcript or a session.** The monitor opens every input read-only and never steers, stops or messages an agent (§1). The plugin's hook only records.
- **What goes out goes as facts.** An owner alert is a fixed template sent by the owner's own notify command (D13). The summary reaches Anthropic through the coordinator that reads it, so its `ling` and Night Shift entries are facts only unless the owner opts in. Neither part sends content to any other service.
- **Everything else stays on the owner's machines.** The plugin calls the local `ling` and the owner's model server; no request leaves for a third party.
- **No new network exposure.** The MCP server is stdio and the monitor a timer. A node is found by the launcher's tiers (by name or id; an mDNS browse is the last tier), a paired node through `NodePairing.resolve` and the SSH forced command that already exists. No address is stored or configured, and nothing listens except `ling web`, which already does.
- **`task` never edits the owner's checkout.** It works in its own worktree under `$CODEX_HOME/claude-plugin/worktrees/`, `ling exec` runs with `-s workspace-write` in Codex's sandbox (bubblewrap, which needs the AppArmor profile from `ling-admin host setup` outside a permissive shell), and the server's checks run through `ling sandbox`. The task's `/airgapped` level is the strictest of the repository's and the user's. Worktrees are removed after the result is taken or after a day.
- **Claude applies the change.** The plugin never commits to, merges into or pushes any branch of the caller's repository.
- **`night` adds no new runner.** It queues through `ling night add`, the one writer of task files; the runner, its scope, its memory cap and its sandbox are unchanged.
- **`watch` runs no free-form script**, only the closed list of §4.2.
- **The owner's rules stay out of the repository.** The commit identity and the forbidden trailers the monitor checks (C5) are in the user's config only.
- **The egress audit.** `ling-admin audit egress` gains two runs: `--claude-plugin` (one `ask`, one `lookup` and one `watch` through `ling delegate mcp`) and `--monitor` (one `check` and one `digest`), with the same verdict rules: every connection an allowlisted loopback port or the named node's model port; no DNS query. The notify command is a separate program the owner configured and is left out of the run; with it unset, the monitor makes no connection beyond the model's. Their tests stub every model call.
- **Host safety.** Neither part starts a model server or loads a model; `max_parallel`, the digest's single call and the monitor's memory cap bound what they add beside a resident model.

---

## 9. Product angle: "your local Mightling helps your Claude Code"

For a Mightling user who also uses Claude Code, the plugin is a saving in Claude tokens and a privacy gain (the delegated reading stays local), and the monitor tells them, on their phone or in `ling web`, when an agent of either kind is stuck. What has to change before it ships:

1. **Install.** `ling-admin claude-plugin install` (and an install-time offer, off by default) writes a local marketplace pointing at the plugin folder that the release ships beside the binaries (`~/.local/share/dreamference/mightling/claude-plugin/`), runs `claude plugin marketplace add` and `install` when the `claude` command exists, and prints the two commands otherwise. `ling update` updates the folder; the manifest's `version` follows the release. The monitor is `ling monitor enable`, offered by the same step.
2. **Gates per repository.** The owner's `[claude_plugin] gates` is hand-written for this repository. Users need detection with confirmation: `ling delegate init` proposes gates from the project's own files (`pytest`, `cargo test`, `npm test`, ruff, mypy, eslint), and nothing runs until the user accepts them.
3. **Ledgers are per user.** Every user's classes start on probation on their own machine; no class is shipped active except L1 and W1. Shared defaults would export one codebase's evidence to another.
4. **Best-of-N from a client.** `--best-of` with `--on` must work (the node runs the attempts and the chooser), so `night` works for client installs.
5. **Alerts through the user's messenger** (§0.1, question 6), and the monitor's rules (C5) left empty for users until they set them.
6. **Windows and macOS clients.** `ling delegate mcp` and `ling monitor` are Rust like the launcher; the Windows sandbox for `ling exec` and `ling sandbox` must pass the same checks before `task` is offered there, and the monitor's timer needs each platform's scheduler.
7. **Documentation** in the user site, including what is and is not delegated and why, and what the monitor reads and sends.
8. **Wording.** The product page says what the plugin and the monitor do for Claude Code users, with the quality mechanism stated; it names Claude Code as the integration target and makes no other claim about it.

---

## 10. Phases, each with a gate

Two tracks. **M0 comes first or beside P0**: it needs no model, and its transcript reader produces the §2-style analysis that P0's extraction and P1's gate both use.

### Part A, the plugin

**P0 — the replay evaluation.** No plugin yet: the extraction (the monitor's reader over history), the replay set of §6.5 (outside the repository), a minimal `ling delegate ask|lookup` CLI, and one question answered first: whether `--output-schema` reaches SGLang through the local provider and constrains the answer (if not, the server validates the JSON itself, and a non-conformant answer fails open).
*Gate:* L2 and L3 parity ≥ 95% with zero severe units; W1 replays wake on the same event in every unit; T-class units measured with the change-unit method, with results per class. The replay runs first on a small sample, at most about 50M Claude tokens, enough to rank the task classes; the full set runs only if the sample is promising (§0.1, question 10).

**P1 — `ask`, `lookup` and `watch`.** The plugin with the server's three read-only tools, the skill, `mightling:delegate`, the ledger, the busy check (§5.3), the planner exclusion, the egress-audit run, and §3's practices in the coordinator's instructions.
*Gate:* two weeks of live use: no shadow miss in L2/L3; W1 results equal to the event in every audited case; the monitor's C7 shows the waiting share and the share of calls above 300K both down, and the price-weighted cost per finished task down; no regression in the owner's own merge outcomes over the period.

**P2 — `task` and `night` for the whitelist classes.** Worktrees, the server's checks, the policy rules, the reviewer agent, the revert hook, automatic demotion. T1 first, then T2, T3, T4, T5, then N1; each enters on probation.
*Gate per class:* promotion as in §6.3 (20 shadow comparisons, no severe miss), positive net saving after failed attempts, and zero reverted delegated changes; any revert demotes the class until the owner reviews it.

**P3 — the product.** §9's changes for the plugin, the documentation, the release's plugin folder.
*Gate:* a user who did not write the plugin installs it from a release on a client and on a node; the egress audit passes with it; L1 and W1 work out of the box, every other class starts on probation; the suite's tests cover refusal during a benchmark, the closed `watch` list, and quote verification.

### Part B, the monitor

**M0 — the deterministic monitor and alerts.** The reader, the agent table, checks C1–C7, the summary, the notify command with deduplication and rate limits, the two units (the digest unit installed but idle), `ling monitor status|check|doctor`, practice 5 in the coordinator's instructions.
*Gate:* (1) a replay over the transcripts since 2026-10-15 finds every usage limit in them with the right kind and reset time, every pre-push refusal, and every agent that passed 300K, with no false usage-limit alert; (2) a week live with at most two false alerts a day, every check under 10 s and 512 MB, and no write under any input path (checked by the tests and by comparing the inputs' modification times around each check); (3) the week's comparison of §7.7 reported, with the coordinator's status-reading tokens down against the week before.

**M1 — the Qwen digest.** The digest and the line labels of §7.4, the quote verification, the contradiction count, the skip on a reserved model.
*Gate:* 50 digests read by hand: every shown statement's quote verified (by construction) and judged to support the statement in at least 90%; no digest shown as `progressing` for an agent later found stuck for more than an hour; the digest skipped in every hour of a SWE-bench night that week, and that night's timing unchanged; model time under 5 minutes per hour.

**M2 — the product.** The `ling web` status page, `ling` sessions and Night Shift on by `ling monitor enable` for any user, the messenger alert channel, the documentation.
*Gate:* a user who did not write the monitor enables it from a release; an induced stall of a `ling` session and of a Night Shift task each shows in the summary and the page within one check; the egress audit passes with `--monitor`; the suite's tests cover the reader's tolerance of unknown line types, the read-only inputs, deduplication and rate limits, and the facts-only rule for `ling` entries.

---

## 11. Risks

| Risk | Effect | Answer |
|---|---|---|
| A verified quote answers a different question | Claude builds on the wrong code | Claude checks the quotes against its question; P0 counts these cases by hand |
| A diff passes the visible checks and the review but is wrong | A defect lands | Hidden-check units in P0; the shadow sample; demotion on revert; T-classes limited to work with strong checks |
| Probation never ends for rare classes | No saving in that class | Accepted: a class without evidence is not used |
| The skill over-delegates | Claude tokens rise | The break-even rule; the ledger's net saving demotes a class that loses |
| A benchmark night is disturbed | Its timing is wrong | The lock and gate check before any model call, in both parts; no retries; the model-free tools and checks call nothing |
| The model or `ling` changes | The evidence no longer applies | All model-backed classes return to probation |
| Claude Code changes its transcript format | Checks go blind | The reader keys on few fields, skips unknown lines, reports "unknown" and raises a `format-drift` alert (§7.1) |
| Alerts become noise | The owner stops reading them | Deduplication by episode, rate limits, the M0 gate's false-alert bound |
| A digest misleads the coordinator | A stuck agent is left alone | Facts are authoritative and shown first; every statement quoted; the M1 gate counts contradictions |
| Content leaks through an alert or the summary | Transcript text leaves the node | Fixed alert templates of facts; facts-only `ling` entries in the summary; the egress audit's `--monitor` run |
| Practices are not followed | §3's saving is not realised | The monitor measures them weekly (§7.7) |

---

## 12. What was measured, and what was not

**Measured** (2026-10-27): the night's cost structure from 4,392 deduplicated responses; the line-count overstatement; cache rewrites after idle gaps; the waiting and search-and-read shares; the share above 300K; the savings replays of §3 under their stated assumptions; the rough distribution of brief classes. Read from the owner's transcripts in aggregate: usage-limit errors appear as API error lines with `error: "rate_limit"`, in three kinds (session, weekly, monthly spend), the first two with a reset time and zone in the text.

**Read from source or documents:** the `ling exec` flags; Night Shift's `--test` and `--best-of`, the refusal of `--best-of` with `--on`, its task files and timer; the model gate's 503 behaviour; the launcher's node tiers; Claude Code's plugin layout, tool naming, output limits, stdio timing, monitors and plugin-subagent limits, from its public documentation.

**Not measured:** any acceptance rate of Mightling on these classes; whether `--output-schema` constrains SGLang through the local provider; latency on real delegations; whether the reviewer at a cheaper model reaches the same verdicts; the recovery cost of retiring agents; whether a subscription's limits track the price ratios; the coordinator's own share of the baseline's tokens; the monitor's effect on them; the digest's accuracy; the check's run time on a week of transcripts. The brief classes of §2.3 come from keyword rules checked by eye, and the run boundaries of §2.2 from command patterns; both are estimates.

---

## 13. As built: M0 (2026-10-27)

**Where.** The crate `ling-rs/monitor/` (`ling-monitor`), plain code with chrono, serde and toml only (no network, no model, no async runtime), routed by the launcher as `ling monitor …` (`ling-rs/src/monitor.rs`). The crate also builds a small `ling-monitor` binary with the same commands, for a machine whose installed `ling` predates the monitor; `ling monitor enable` writes units that name whichever of the two installed them. Commands: `check` (the timer's), `status [--json]`, `doctor`, `replay --since … [--until …]`, `enable`, `disable`, `notify-test`, and `digest` (M1; does nothing yet). Settings are `[monitor]`, `[monitor.waits]` and `[monitor.rules]` in the user's config; with no rules set, C5(a) is off, and nothing of the owner's rules is in the repository.

**Inputs in M0.** Claude Code's session and subagent transcripts under `~/.claude/projects/` (every project), the `.meta.json` beside each subagent, `git log` of the repositories named in `[monitor.rules] repos` (or the transcripts' `cwd`), and the entries of `~/.local/bin` and of the home for C5(c). `ling` sessions and Night Shift are M2's.

**Decisions taken while building, each from the replay over 2026-10-15 to 2026-10-27:**

- **Responses are counted by `message.id`, from their largest line.** A response that made several model calls around a server tool (an advisor) sums them on its last line; its context is the last call's (`usage.iterations`), not the sum. Counting the sum doubled some contexts past 300K that never reached it.
- **C1 is summary-only, never an owner alert.** A subagent's hand-off is the coordinator's to act on, and the coordinator compacts by itself, so nobody can act on an alert about it. With every agent alerting, history gave 6-18 alerts a day.
- **A release is an event only in the result of a call that follows one run to its end**: `gh run watch`, or `gh run view` of one run without `--log`. A `gh run list`, a `gh api` query, a log or a discussion of one names old runs, and on the first live day such a listing raised a false "release workflow failed".
- **A question put to the owner through a tool** (`AskUserQuestion`, `ExitPlanMode`) is C6 after 30 minutes, not a C2 stall.
- **A pre-push refusal counts only in the result of a `git push`.** Every one of the 8 refusal lines in the period came from reading the hook's source; none was a refused push.
- **A test run is a command that runs a suite** (`pytest` as a word, `-m pytest`, `cargo test`, `npm test`), so a search for the word does not arm C5(c).
- **A usage limit's reset is converted to UTC only in this machine's zone** (from `/etc/localtime`), with no time-zone database in the build; another zone keeps the time as written, and the key then includes the day it was seen. A monthly spend limit's message names the *weekly* limit's reset; the summary says so, and no "lifted" note is sent for it.
- **Urgent alerts are not held back by the hourly cap.**
- **The first check on a machine is a baseline**: everything it finds is marked known and nothing is sent.

**The M0 gate, part 1 (replay since 2026-10-15, nothing sent):** both usage limits in the period found with the right kind and reset (a monthly spend limit on 2026-10-20, 18 lines, weekly reset 10:00 UTC the next day; a session limit on 2026-10-27, 11 lines, reset 01:00 UTC), no other usage-limit alert; zero pre-push refusals, which is the true count; the 55 agents of this project that passed 300K, exactly (and 5 in another project). Alerts the replay would have sent: 1 to 7 a day, mostly a coordinator awaiting an answer and loops. The replay of twelve days took 2.3 s and 207 MB; a live check reads only appended bytes (under 0.1 s and 10 MB once the baseline exists; the baseline about 1 s and 130 MB).

**Not yet:** parts 2 and 3 of the gate (a week live); `ling` sessions and Night Shift (M2); the digest (M1); a send entry point in the messenger bridges (M2), so `[monitor] notify_command` serves.

---

## 14. As built: P1 and P2 probation (2026-10-27 to 2026-10-28)

**Where.** The leaf crate `ling-rs/delegate/` (`ling-delegate`), with `chrono`, `regex-lite`, `serde`, `serde_json`, `toml` and `ling-safegit` (no async runtime), routed by the launcher as `ling delegate …` (`ling-rs/src/delegate.rs`) and also built as a standalone `ling-delegate` binary (on Linux, in the same Cargo run as `ling` and installed beside it in `~/.local/share/dreamference/mightling/bin/` and linked into `~/.local/bin`, so the plugin's `.mcp.json` can run `ling-delegate mcp` whatever `ling` is installed; a release asset `ling-delegate-<target>.gz`, optional for `install.sh`; `docs/dev/claude-plugin.md`). Commands: `ling delegate mcp` (the JSON-RPC 2.0 stdio MCP server), `ling delegate lookup <op> <name> …`, `ling delegate run_test …`, `ling delegate git_log …`, `ling delegate diff_stat …`, `ling delegate task --json <file|->`, `ling delegate verdict <task-id> <accept|reject|reverted|shadow_miss>`, `ling delegate ledger [--json]`, `ling delegate watch …`, and `ling delegate classes [--json]`. The Claude Code plugin lives in `claude-plugin/` (`.claude-plugin/plugin.json`, `.mcp.json`, `agents/delegate.md`, `agents/diff-reviewer.md`, `skills/delegate/SKILL.md`), with `.claude-plugin/marketplace.json` at the repository root. The skills planner (`ling-rs/skills/src/catalog.rs`) excludes foreign `delegate` skills (`BUILTIN_EXCLUDED_FOREIGN_SKILLS`) so `ling` never links the Claude Code skill into its own sessions (§4.3).

**Scope (W1, L1, and T1–T5 on probation).** Class `W1` (`watch`) was turned on 2026-10-27; class `L1` (`lookup` over `ling-code` def/refs/callers/impl/outline, plus the model-free exact tools `run_test`, `git_log` and `diff_stat`) and classes `T1`–`T5` (`task` on probation when `task` is in `DREAMFERENCE_DELEGATE_ALLOW_CLASSES`, with `task_verdict` and the ledger of §6.1/§6.3) were built 2026-10-28. Calls to `ask` or `night` over MCP or CLI are refused with a message stating they are not built yet. `lookup` has no `rg` mode yet.

**Closed condition list and bounds:**

- `file_line` (aliases `file`, `log_line`): polls a local file (bounded to 64 MiB per read) for a substring or `regex-lite` match and returns `<path>:<line_number>: <matching line verbatim>`.
- `pid_exit` (aliases `pid`, `process_exit`): waits for a positive PID to exit (`pid <pid> exited`).
- `http_status` (alias `http`): polls an `http://` URL on loopback (`127.0.0.1`, `localhost`, `::1`, connected to without a name lookup) until the target HTTP status code (default `200`) is returned; every other host is refused, a node by name included (§4.2 allows one; it waits for the node locator, since a lookup by name could leave the machine).
- `gh_run`: polls `gh run view <run_id> [--repo <owner/name>] --json status,conclusion` at `>= 15s` until `status == "completed"`, returning `run <id> completed: <conclusion>`.
- `systemd_unit` (alias `unit`): polls `systemctl --user is-active <unit>` at `>= 15s` until the unit reaches the target state (default `"active"`).
- `command`: polls an allowlisted read-only command (`git status|rev-parse|log|diff|show-ref|ls-files` without `--output`, `--ext-diff` or `--textconv`, run through `ling_safegit`; `gh run view`, `ling night show|status`, `ling-admin night status|show`, `systemctl --user is-active`, or `curl` to loopback with only `-s -S -f -I -i` and their long forms; shell metacharacters are refused) at `>= 15s` until the target exit code and/or line pattern matches.
- `night_task` (aliases `night`, `task`): polls `$CODEX_HOME/night/tasks/<id>.json` until `status` is one of `done`, `no-change`, `stalled`, `failed`, `interrupted`, or `cancelled`.
- `git_ref`: polls `git rev-parse --verify <ref>` (via `ling_safegit`) at `>= 15s` until the resolved commit hash differs from `from` (or from the initial poll's hash).
- `timeout`: waits a bounded duration (`timeout elapsed (<n>s)`), and any condition that reaches `timeout_s` (default 1,800 s / 30 min, hard max 14,400 s / 4 h) returns `{met: false, observed: "timeout after <n>s (last: ...)", at: "<RFC3339 UTC>"}`.
- `observed` is capped at 6,000 characters (~1,500 tokens). `watch` uses no model, so it has no busy check and runs during a benchmark (D4, §5.3).

**Not built yet:** `ask` and `night` (refused over MCP and the CLI), `lookup`'s `rg` mode, the cross-node busy fallback (§5.3 item 3), the egress-audit run `--claude-plugin` (§8), `hooks/hooks.json` (§4.5), a `watch` URL on a node by name, and the automatic version stamp (`plugin.json` carries `dreamference.__version__` by hand, and `tests/test_claude_plugin.py` fails when they differ). Nothing enables the plugin automatically: it is installed only by the commands of §4.1.


