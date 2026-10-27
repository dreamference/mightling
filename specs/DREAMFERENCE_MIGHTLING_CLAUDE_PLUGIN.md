# Mightling — a Claude Code plugin that hands work to the local Mightling, with no loss of quality

**Status:** proposed on 2026-10-27. Nothing is built. The cost figures in §2 were measured from one night of a Claude Code coordinator session on the owner's machine (aggregates only; no content of those logs is quoted or stored here). Every figure about Mightling doing delegated work is an estimate until P0 (§9) measures it.

**Target:** the owner first, then any Mightling user who also runs Claude Code: "use your local Mightling from Claude Code". The aim is fewer Claude tokens for the same results. The owner's requirement governs every section: *quality must not drop; Mightling takes only the tasks it can be shown to do well.*

**Builds on:**
- `ling exec` (Codex's non-interactive mode, unmodified): `--output-schema`, `-o`, `--ephemeral`, `-s`, `-C` (`codex-rs/exec/src/cli.rs`, `codex-rs/utils/cli/src/shared_options.rs`).
- The launcher's node resolution, tiers 1–5 (`ling-rs/src/node.rs`, [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md)); `NodePairing.resolve` for a paired node, by id and never by address.
- `ling-code` ([MIGHTLING_CODE_INDEX](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md)) and `rg` for model-free lookups.
- Night Shift: `ling night add --test --best-of`, chooser v1, `night/<id>` branches, the runner lock, `ling sandbox` for the runner's own test run ([MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md)).
- The SWE-bench model gate and runner lock ([MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) §18), and the failure analysis ([MIGHTLING_SWE_BENCH_FAILURES](./DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md)), which supplies the verification rules of §5.2.
- The skills planner, which links `~/.claude/skills` into `ling` ([MIGHTLING_SKILLS](./DREAMFERENCE_MIGHTLING_SKILLS.md)).
- An earlier proposal, *Claude delegates to ling*, written on 2026-10-08 and taken out of this directory the same day (it is in git history at `6aca2f2`). Its rules carry over: verified quotes against unverified prose, fail open, break-even by the number of calls, "not found" proves nothing, and no hook that diverts work automatically. Its Phase 0 was never run. **The `ling-delegate` skill it planned does not exist**: the line in the owner's global Claude Code instructions that names it applies "when it is installed", and it is not installed. This plugin replaces that skill (§3.3).

---

## 0. Decisions, stated first

1. **Quality is protected by verification, never by trust in the model.** Claude never takes Mightling's prose as fact. What reaches Claude as fact is what the plugin checked itself: a quote equal to the file's text, an exit code, a list of failing test names, a line a watcher saw. Everything else is labelled a hint (§5.2).
2. **Mightling takes a task class only after it is measured there.** A whitelist of task classes (§5.1), each starting on probation in shadow mode, where Mightling's result is compared with Claude's own and counts for nothing (§5.3). Design, security-sensitive code, exactness-critical engine kernels, releases and history operations, and anything without an objective check are excluded and never promoted.
3. **A class is demoted automatically**: at once when a delegated change is reverted or a shadow audit finds a miss, and when its acceptance or its measured net saving falls below a threshold (§5.3).
4. **A failed delegation costs Claude only the attempt.** Claude does the task itself, and the tokens the failed attempt spent are charged against the class, so the net saving is measured honestly (§5.4).
5. **The largest saving does not need Mightling.** In the night measured, cache rewrites after idle gaps were 39% of the price-weighted cost; waiting turns 28%; calls made at contexts above 300K tokens 74%. Retiring long agents and waiting with watchers that wake an agent once are worth more than any delegation, and §6 states them as coordinator practice. The plugin's `watch` tool makes the second one easy.
6. **`lookup` and `watch` use no model.** They work during a benchmark night, need no probation and cannot lower quality. They ship first (P1).
7. **The plugin opens no port and stores no address.** It is a stdio MCP server that runs where Claude Code runs and calls the local `ling`, whose launcher finds the node by name or id. A second node is reached only through the existing SSH pairing (§4, §7).
8. **Busy means refuse, fast.** During a SWE-bench run the plugin uses another node serving the same model, or answers `busy` within seconds. It never queues behind the benchmark and never disturbs its timing (§4.3).

---

## 1. Goals and non-goals

**Goals**
- Fewer price-weighted Claude tokens per finished task, measured from Claude Code's own usage records.
- No outcome worse than Claude alone: the same merge and acceptance outcome on the replay set (§5.5) and in shadow audits, and no reverted delegated change.
- One install step; nothing changes for a person who does not install it.
- A side effect worth stating: file contents that Mightling reads in a delegation stay on the owner's machines and are not sent to Anthropic.

**Non-goals**
- Running Claude Code itself on the local model, or routing Claude Code's small background model to it.
- Mightling reviewing Claude's work. A weaker reviewer adds noise.
- Writing that needs judgement: specs, designs, user-facing text, release notes.
- A `PreToolUse` hook that diverts Claude's searches automatically. It would take the decision from the model that knows whether the answer matters, and it adds text to every call.

---

## 2. Evidence: where the tokens went (measured 2026-10-27)

**Source.** The subagent transcripts of one Claude Code coordinator session on the owner's machine, 2026-10-26 00:00 to 2026-10-27 12:00 UTC (the logs stay outside the repository, under `~/.claude/projects/<project>/<session>/subagents/`). Responses were deduplicated by `message.id`, because a transcript writes one line per content block.

**Counting by line overstates.** The figures first quoted for this night (37 agents, 7,274 calls, about 2.1 B input tokens, 1.95 B of them cache reads, 3.3 M output) counted lines: counting lines over the same window gives 7,838 "calls" and 2.30 B input. Deduplicated, the night is:

| | |
|---|---|
| Agents active | 38 |
| Model responses | **4,392** |
| Input tokens | **1.35 B**, of which 1.24 B (92%) cache reads |
| Output tokens | 3.5 M |
| Context per call | median 231K, p90 691K |
| Three longest-lived agents | 425 M input (32%), peak contexts 770K–900K |
| Most agents active in one 10-minute window | 16 |

**Weights.** List-price ratios: input 1, five-minute cache write 1.25, cache read 0.1, output 5. "Price-weighted" below means tokens times these weights. Whether a subscription's limits track these ratios is not published; the assumption is that they roughly do.

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

The agents' briefs, classed by keyword rules (checked by eye) over the session's 158 agents since 2026-10-15, fall mostly in the excluded classes of §5.1; under a tenth of the calls (about 7%) belong to briefs that a whitelisted class could take whole. **Few whole briefs are delegable.** What is delegable lives *inside* the briefs: the search-and-read runs (`ask`, `lookup`), the waiting runs (`watch`), and the small mechanical steps of larger work (`task`). The whitelist therefore covers a minority of the workload, and §6's practices, which need no delegation, cover the rest.

### 2.4 The break-even rule (carried over, re-fitted to this night)

With context **C**, **k** calls Claude would make itself, **t** tokens those calls bring back, a brief **b** and a result **r**, and each added token costing w = 1.25 + 0.1·R over its R later re-reads (R ≈ 40):

> delegation pays when (k − 1)·0.1·C + (t − r)·w > b·(5 + w)

At this night's median C = 231K, a five-call exploration returning 6K tokens, replaced by a 300-token brief and an 800-token result, saves about 4 × 23K + 5.2K × 5.2 − 3K ≈ 116K token-equivalents. One small lookup never pays through `ask`; `lookup` (model-free, one call) is the tool for it. The skill encodes this as: **use `ask` when you expect three or more searches or more than ~5K tokens of output for one question; use `lookup` for a single symbol or string.**

---

## 3. The plugin

### 3.1 Shape (Claude Code's plugin format)

Checked against Claude Code's public plugin documentation (plugins overview, manifest reference, components, sub-agents, MCP) on 2026-10-27. The standard layout, with every component in its default place:

```
mightling/                      # the plugin root
├── .claude-plugin/plugin.json  # name "mightling", version, description, userConfig
├── .mcp.json                   # the stdio server "mightling"
├── skills/delegate/SKILL.md    # the delegation rules (§3.3)
├── agents/delegate.md          # a subagent restricted to the plugin's tools (§3.4)
├── agents/diff-reviewer.md     # the diff review of §5.2, no tools
├── hooks/hooks.json            # records outcomes only (§3.5)
└── bin/                        # empty: the commands come from Mightling's own install
```

- **Manifest.** `name: "mightling"`, a `version` stamped from `dreamference.__version__`, and `userConfig` for three values: `node` (a node *name* or id, optional; the launcher's tiers decide otherwise), `max_parallel` (default 2) and `allow_classes` (the classes the owner switches on; empty means "probation only").
- **The MCP server** (`.mcp.json`): `{"mightling": {"command": "ling", "args": ["delegate", "mcp"]}}`. Its tools are named `mcp__plugin_mightling_mightling__<tool>`, the form subagent `tools` lists and permission rules use.
- **Size of results.** Claude Code warns above 10,000 tokens of MCP output and cuts at 25,000 by default (`MAX_MCP_OUTPUT_TOKENS`). Every tool caps its result at 1,500 tokens, except `task` and `night fetch`, which cap at 8,000 and otherwise return paths (§3.2).
- **Time.** A stdio MCP server has no per-request timer in Claude Code, so `watch` and `task` may block for as long as they need; each takes its own `timeout_s`.
- **Not used:** plugin monitors (experimental, and they do not start under `claude -p`, which the evaluation and headless coordinators use); `bin/` executables (claude.ai and Cowork refuse a plugin that has them, and `ling` is already on `PATH`).
- **Install, local:** `claude --plugin-dir <checkout>/claude-plugin` while developing; for daily use, `/plugin marketplace add <checkout>` (a `.claude-plugin/marketplace.json` at the repository root listing one plugin) and `/plugin install mightling@mightling`. The product path is §8.

### 3.2 The MCP server `mightling` and its five tools

`ling delegate mcp` is a launcher subcommand in `ling-rs` (no Codex patch; a client install has no Python, so it is Rust). It prints nothing but MCP frames on stdout, and keeps a ledger (§5.3) under `$CODEX_HOME/claude-plugin/`.

| Tool | Model? | What it does | What Claude receives |
|---|---|---|---|
| `lookup` | **no** | `ling-code def/refs/callers/impl/search` and `rg` (literal or regex) over a stated scope | Hits as `{path, start, end, quote}`, every quote read back from the file by the server; for zero hits, "no literal match for `X` in scope `Y` (N files searched)", a fact about the string, not about the concept |
| `ask` | yes | `ling exec --ephemeral -s read-only -C <repo> --output-schema ask.schema.json` on one question about code, files, logs or session rollouts | `{status, answer (≤120 words, a hint), evidence[{path, start, end, quote, why}], searched[], confidence, full_output}`; each quote verified (§5.2) |
| `task` | yes | One whitelisted change by `ling exec -s workspace-write` in a fresh worktree, then the class's checks run by the server | The diff (≤8,000 tokens, else its path and a refusal for that class), the checks' facts (§5.2), the policy findings, Mightling's own summary marked unverified |
| `night` | yes | `queue`: `ling night add --test <cmd> --best-of N <task>` in the repository; `fetch <id>`: the chosen `night/<id>` branch, re-checked by the server exactly as `task` | `queue`: the task id. `fetch`: the same shape as `task`, plus the chooser's `why` |
| `watch` | **no** | Waits for one condition and returns once | `{met, observed, at}`: the exit code, the matching line verbatim, the status seen |

**The brief** for `ask` and `task` is a template, never free prose, because Claude's output costs five times its input:

```
CLASS: <one whitelisted class, §5.1>
GOAL: <one sentence>
SCOPE: <paths>
DONE WHEN: <the check: a command, or for ask what counts as an answer>
LIMITS: <files that must not change; at most n evidence items>
```

The server refuses a brief whose `CLASS` is not active (or on probation, which runs as shadow, §5.3), whose `DONE WHEN` names no command for a `task`, or whose scope includes an excluded path (§5.1).

**`watch` conditions** (closed list; no free-form script): a process id exits; a file contains a line matching a pattern; a command from an allowlist (`git`, `gh run view`, `ling night show`, `ling-admin night status`, `systemctl --user is-active`, `curl` to loopback) reaches a given exit code or prints a pattern, polled at an interval of 15 s or more; a URL on loopback, or on a node by name, returns a status; a `task` or `night` id finishes; a git ref moves. The same code runs as `ling delegate watch …` from a shell, so an agent can also start it with Claude Code's background shell and be woken by its completion.

### 3.3 The skill `delegate`

`skills/delegate/SKILL.md`, one-sentence description: "Hand a whitelisted task to the local Mightling; quotes and checks are verified, prose is a hint." The body holds:

- **The rules of fact.** A verified quote, an exit code and a failing-test list are facts. The answer sentence and the summary are hints. `not_found`, `partial`, `unverified` or `low` sends Claude to do the work itself when it matters. Absence is never inferred from a model; a `lookup` zero-hit is a fact about the literal string only.
- **When to use which tool:** the break-even rule of §2.4; `watch` for every wait longer than a minute.
- **The active classes**, read at session start from `ling delegate classes --json` (the ledger's current state), not written into the skill, so a demotion takes effect at once.
- **The brief template**, and the fallback rule: on `busy`, a refusal or a failed check, do the task yourself, with no retry.
- **Precedence.** A user's own instructions about exploration tools win. On the owner's machine the global instructions already route single lookups to another code-navigation tool and multi-step lookups to "ling-delegate"; the install step suggests replacing that name with this plugin's `ask`. The user decides.

**The loop-back problem.** Mightling's skills planner links `~/.claude/skills` into `ling`'s catalogue. A plugin skill lives under Claude Code's plugin cache, not there, so it is not linked; P1 still adds `delegate` to the planner's built-in exclusions, so `ling` is never told to delegate to itself.

### 3.4 The subagent types

- **`mightling:delegate`** (`agents/delegate.md`): `tools` lists only the five `mcp__plugin_mightling_mightling__*` tools and `Read`. A coordinator gives it a brief; it calls the tools, checks the facts, and returns at most a page. Its point is the context it does not inherit: it starts fresh, so its waiting and its tool results never enter the coordinator's 500K context. (Plugin subagents may not set `mcpServers`, `hooks` or `permissionMode`, so the plugin's server must be enabled for the session, which installing the plugin does.)
- **`mightling:diff-reviewer`** (`agents/diff-reviewer.md`): no tools. Its prompt is the brief, the diff and the checks' output, nothing else (§5.2).

### 3.5 Hooks: outcomes only

`hooks/hooks.json` has one `PostToolUse` hook on `Bash` that matches `git commit`, `git merge`, `git revert` and `git apply`, and runs `ling delegate record` to attribute commits to accepted delegations by patch id (§5.3). It never changes a tool call and adds no text to Claude's context. No hook routes work.

---

## 4. Where it runs

### 4.1 On a node, and on a client

- **On a node** (a machine with `~/.config/dreamference/node-id`), `ling` uses loopback.
- **On a client** (a laptop with Claude Code and a checkout), the MCP server runs on the client, and the local `ling` finds the model server through the launcher's tiers: `DREAMFERENCE_VLLM_HOST`, `MIGHTLING_NODE`, the node remembered in `$CODEX_HOME/node.json` (found again by id), then an mDNS browse. The plugin passes `userConfig.node` as `MIGHTLING_NODE` when set. It never stores or passes an address.
- **`task` runs beside the code**, on the machine where Claude Code's checkout is: `ling exec` there, the model on the node. The alternative, a job on the node through `node run`, is rejected for P2: the code and the toolchain the checks need are on the client, and a node job has the home folder hidden and no GPU.
- **`night` runs where the repository's Night Shift runs.** On a client, `/night add --on <node>` runs a task once (`--best-of` with `--on` is refused today, spec §18.8), so best-of-N from a client is a P3 change (§8). Until then, `night` from a client is refused for classes whose evidence is best-of-3.

### 4.2 Concurrency and latency

- The model server serves 8 requests at once. The plugin holds at most `max_parallel` (default 2) delegations per machine, with a lock file under `$CODEX_HOME/claude-plugin/`.
- Expected times: `lookup` under 2 s; `ask` 30–120 s; `task` 5–30 min. A `task` is best called from a `mightling:delegate` subagent running in the background, whose completion is the coordinator's next turn, so the wait costs the coordinator no calls.

### 4.3 When the model is busy

Before any model call, in this order, all within about 3 s:

1. **The runner lock** (`$CODEX_HOME/night/runner.lock`, on a node): held by a SWE-bench run → that node is busy. Held by Night Shift → go ahead; Night Shift gives way to outside requests.
2. **The model gate:** a one-token completion with a 3 s timeout. A 503 from the gate (which answers 503 with `Retry-After: 0` to requests from outside a running benchmark's network) → busy. The plugin sends no retry; `ling` itself would retry about 30 times, so the plugin never starts `ling exec` against a gate that refused.
3. **Another node:** `ling node list --json` names the nodes on the network; the plugin uses another one only if it serves the same model (by `/v1/models`, matched with `model_key_for_served_id`), is not advertising `state=stopped`, and is not one the owner reserved (`[claude_plugin] exclude_nodes`, by name; the node kept for engine development goes there). A paired node is reached through `NodePairing.resolve`, by id.
4. **Otherwise** the tool answers `{"status": "busy", "reason": "benchmark running on <node>, until about <time>"}`, and the skill tells Claude to do the work itself.

`lookup` and `watch` skip all of this: they use no model.

---

## 5. Quality guarantees

### 5.1 The capability map

A class is on the whitelist only if (1) there is measured evidence that Mightling does work of this kind, (2) a machine can check the result, and (3) a wrong result is cheap to detect before it lands. **No class has a measured acceptance rate yet**; the evidence column says what supports trying it, and every model-backed class starts on probation (§5.3).

| Class | Tool | Evidence that supports it | Objective check | Starts |
|---|---|---|---|---|
| L1 Lookups with quotes | `lookup` | Deterministic: the index and `rg` | Quotes re-read from the files | **active** |
| L2 Multi-step questions with quotes | `ask` | Localisation in SWE-bench runs: in the 100-task round, a wrong file accounts for only 5 of the 32 failures; no question-answering rate is measured | Quotes verified; Claude decides from quotes only | probation |
| L3 Log and rollout summaries with quotes | `ask` | None measured; the reading half is L2's, and the deterministic extract carries the facts | Deterministic extract first (failing tests, errors with file:line, exit code); model excerpts verified | probation |
| W1 Waiting and monitoring | `watch` | No model | The plugin observes the condition | **active** |
| T1 Lint, typing and format fixes | `task` | Mechanical; the gates are the oracle | ruff/mypy counts per file only fall; tests pass; no other file changes | probation |
| T2 Test-only CI fixes | `task` | The single-run SWE-bench resolve rate, 68/100 (round `im100-default`) and 22/50 to 26/50 on fresh tasks; 31/50 with best-of-3 (night 13, p = 0.022) | The failing CI test passes; the rest of the suite still passes by name; the assertion rules below | probation |
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

### 5.2 Verification per class

**Quotes (L1–L3, T4).** The server reads each cited file and checks that the quote equals its text at the cited lines (whitespace runs normalised, nothing else) and that the path is inside the scope. A failing item is removed and counted; with no item left the status becomes `unverified`. Claude builds only on quotes; the answer sentence is a hint. Claude also checks that the quotes answer *its* question: a verified quote can still answer a different one, and P0 counts those cases by hand.

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

### 5.3 Routing by measured success

**The ledger.** Every delegation appends one record to `$CODEX_HOME/claude-plugin/ledger.jsonl`: class, tool, repository (a hash of its path), node, times, Mightling's token counts, the Claude tokens spent on the brief, the result and the review (from Claude Code's usage records for that turn, when the hook can see them; otherwise estimated from sizes), the verification outcome, the review verdict, and, for an applied diff, its `git patch-id --stable`. No content: no brief text, no code, no answer.

**Outcomes per delegation:** *accepted* (verified, reviewed, applied or used); *rejected by verification* (a check, a policy rule or the quote check failed); *rejected by review*; *reverted later* (the hook of §3.5 sees a `git revert` of a commit carrying an accepted patch id, or a later commit whose patch id is its inverse, within 30 days); *shadow miss* (§ below).

**States of a class:** `probation` → `active` → `demoted` (→ `probation` again only by the owner's command).

- **Probation is shadow mode.** Claude does the task itself as it would without the plugin; Mightling's result is produced in the background (a `mightling:delegate` subagent) and compared, and is never used. Comparison per class: L2/L3, Claude's own answer's file:line is among the verified evidence and the reviewer judges the answers equivalent; T-classes, both diffs pass the same checks and the reviewer, given both, judges them equivalent in effect. Probation costs tokens (both runs) and saves none; it is the price of the evidence.
- **Promotion** needs at least 20 shadow comparisons with at most one non-severe disagreement and no *severe* one: a result that passed every check and the review but was wrong by Claude's own result. Promotion is proposed by `ling delegate classes`, and the owner confirms it (`ling delegate promote <class>`).
- **Active classes keep a 10% shadow sample**, so a drift is seen.
- **Automatic demotion**, written by the server, effective at the next tool call:
  - at once, on any *reverted later* or *shadow miss* in the class;
  - when acceptance over the last 20 delegations falls below 60%: below that, the failed attempts' cost eats the saving (§5.4);
  - when the class's measured net saving over the last 20 is not positive.
- **A model change resets all model-backed classes to probation.** The evidence belongs to the model that produced it, so a different `model_key_for_served_id`, or a new `ling` minor version, starts the classes again.
- `ling delegate classes` prints the table (state, n, acceptance, reverts, net saving); the skill reads its JSON form.

### 5.4 Fallbacks and honest accounting

- On `busy`, a refusal, a timeout, a failed check or a `REJECT`, Claude does the task itself, at once, with no retry and no second delegation of the same task.
- The ledger charges the failed attempt to the class: the Claude tokens of the brief, of reading the result and of the review, plus the time. The **net saving** of a class is Σ(counterfactual − actual) over its delegations, where the counterfactual is the class's median Claude cost for doing the task alone, measured in its shadow runs, and the actual includes every failed attempt's cost and the fallback's own cost.
- What Mightling spends is free in Claude tokens but not in time or power; the ledger records its wall time so the owner can see the trade.

### 5.5 Evaluation before rollout (the replay set)

**Units**, extracted by a P0 script that reads the transcripts where they are and writes the set to `~/.local/share/dreamference/claude-plugin/replay/` (outside every repository; nothing of it is committed, and the spec and reports carry aggregates only):

- **Exploration units** for L1/L2: the 197 search-and-read runs of three or more calls (§2.2), plus a stratified sample from earlier nights to reach 150. For each, a reader writes the question the run answered and the answer it reached; that conclusion, from the run itself, is the reference.
- **Log units** for L3: the runs that read a large output (a test log, a rollout, a report) to answer a question; the same reference method.
- **Wait units** for W1: the night's 169 waiting runs, single calls included (329 calls, the classifier of §2.2); each is replayed as one `watch` condition against the recorded timeline, checking that it would have woken the agent on the same event and no later than the polling did.
- **Change units** for T1–T5: whole briefs are mostly excluded (§2.3), so these come from the history of the owner's repositories instead: commits of each class (lint and typing fixes, test-only fixes, renames and moves, docs corrections, UI fixes with e2e tests), 30 per class where history has them. The task is the commit message, the start is its parent, the hidden check is the commit's own tests and the project's gates, and the reference outcome is the commit itself, made and merged without the plugin.

**Metrics:**

- **Quality parity**, per class: the share of units where the plugin's outcome equals Claude alone's: for questions, the reference file:line is among the verified evidence and the answer is judged equivalent; for changes, the diff passes the hidden check *and* the reviewer accepts it, exactly when the reference commit did (always). A unit where Mightling's diff passes the visible checks and the review but fails the hidden check is **severe**, and every one is read by hand.
- **Token saving**, per class: Claude tokens with the plugin (brief, result, review, fallback on failure) against the reference run's own tokens from its usage records.
- **Coverage**: the share of the measured night's price-weighted cost the passing classes touch.

**The gate to enable a class** (beyond probation): parity ≥ 95%, zero severe units, and a positive median saving on the replay set. A class that fails stays off; a class without enough units waits for live shadow data.

**Noise.** Thirty units per class cannot separate 95% from 90% with confidence; the zero-severe rule and the live shadow sample carry the quality claim, not the parity percentage alone.

---

## 6. Coordinator practices that cut tokens without the plugin

These are the larger lever (§0.5). They belong in the coordinator's own instructions, and the skill repeats them.

1. **Retire an agent whose context passes ~300K.** It writes a one-page hand-off note (state, decisions, open items, paths), and a fresh agent continues from the note and a short brief. In the night measured, 38% of calls ran above 300K and carried 74% of the price-weighted cost.
2. **Cap concurrent expensive agents at 8–10.** The night peaked at 16 in one 10-minute window. More agents than the owner can follow produce more waiting, more idle gaps past the cache's lifetime, and more contexts kept alive only to be rewritten.
3. **Wait with a watcher that wakes the agent once, on a real result.** A background shell command (or the plugin's `watch`) that ends when the condition holds, never a `sleep` loop of agent turns. Any turn after more than five idle minutes rewrites the whole context into the cache.
4. **Prefer a fresh short brief to resuming a long history.** A status check or a follow-up question goes to a fresh agent with the facts it needs, not to the 800K agent that did the work.

**Estimated savings on the measured night** (replaying its 4,392 responses under each rule; the rules overlap, so they do not add):

| Practice | Saving of price-weighted cost | Assumption |
|---|---|---|
| Waiting runs collapsed to one wake-up each (3) | **≈ 25%** | One call per waiting run (169 runs, 329 calls) instead of several |
| Retirement at 300K, fresh agent at 60K (1, 4) | ≈ 30% (lower bound) | Only cache *reads* scale with the smaller context |
| The same, if cache *rewrites* scale too | ≈ 69% (upper bound) | A wake-up after a gap rewrites 60K–300K instead of up to 900K |
| Both together | 53–74% | The range between the two assumptions, before the recovery cost |

The fresh agent's cost of recovering context (re-reading files it needs) is not modelled. It is paid once per retirement against rewrites paid on every late wake-up. A **planning figure of 40–50%**, after the recovery cost and imperfect adherence, is defensible; the replay of §5.5 and a week of the practices measure it. Delegation through the plugin adds to this, on a smaller base: the search-and-read runs it can take are 11.7% of the cost, so at most about 5–7% more once the practices are in place.

---

## 7. Privacy and safety

- **Everything stays on the owner's machines.** The plugin calls the local `ling` and the owner's model server; no request leaves for a third party. The ledger holds no content.
- **No new network exposure.** The MCP server is stdio. A node is found by the launcher's tiers (by name or id; an mDNS browse is the last tier), a paired node through `NodePairing.resolve` and the SSH forced command that already exists. No address is stored or configured, and nothing listens.
- **`task` never edits the owner's checkout.** It works in its own worktree under `$CODEX_HOME/claude-plugin/worktrees/`, `ling exec` runs with `-s workspace-write` in Codex's sandbox (bubblewrap, which needs the AppArmor profile from `ling-admin host setup` outside a permissive shell), and the server's checks run through `ling sandbox`. The task's `/airgapped` level is the strictest of the repository's and the user's. Worktrees are removed after the result is taken or after a day.
- **Claude applies the change.** The plugin never commits to, merges into or pushes any branch of the caller's repository.
- **`night` adds no new runner.** It queues through `ling night add`, the one writer of task files; the runner, its scope, its memory cap and its sandbox are unchanged.
- **`watch` runs no free-form script**, only the closed list of §3.2.
- **The egress audit.** `ling-admin audit egress` gains a `--claude-plugin` run: one `ask`, one `lookup` and one `watch` through `ling delegate mcp`, with the same verdict rules (every connection an allowlisted loopback port or the named node's model port; no DNS query). Its tests stub every model call.
- **Host safety.** The plugin starts no model server and loads nothing; `max_parallel` bounds what it adds beside a resident model.

---

## 8. Product angle: "use your local Mightling from Claude Code"

For a Mightling user who also uses Claude Code, the plugin is a saving in Claude tokens and a privacy gain (the delegated reading stays local). What has to change before it ships:

1. **Install.** `ling-admin claude-plugin install` (and an install-time offer, off by default) writes a local marketplace pointing at the plugin folder that the release ships beside the binaries (`~/.local/share/dreamference/mightling/claude-plugin/`), runs `claude plugin marketplace add` and `install` when the `claude` command exists, and prints the two commands otherwise. `ling update` updates the folder; the manifest's `version` follows the release.
2. **Gates per repository.** The owner's `[claude_plugin] gates` is hand-written for this repository. Users need detection with confirmation: `ling delegate init` proposes gates from the project's own files (`pytest`, `cargo test`, `npm test`, ruff, mypy, eslint), and nothing runs until the user accepts them.
3. **Ledgers are per user.** Every user's classes start on probation on their own machine; no class is shipped active except L1 and W1. Shared defaults would export one codebase's evidence to another.
4. **Best-of-N from a client.** `--best-of` with `--on` must work (the node runs the attempts and the chooser), so `night` works for client installs.
5. **Windows and macOS clients.** `ling delegate mcp` is Rust like the launcher; the Windows sandbox for `ling exec` and `ling sandbox` must pass the same checks before `task` is offered there.
6. **Documentation** in the user site, including what is and is not delegated and why.
7. **Wording.** The product page says what the plugin does for Claude Code users, with the quality mechanism stated; it names Claude Code as the integration target and makes no other claim about it.

---

## 9. Phases, each with a quality gate

**P0 — the replay evaluation.** No plugin yet: the extraction script, the replay set of §5.5 (outside the repository), a minimal `ling delegate ask|lookup` CLI, and one question answered first: whether `--output-schema` reaches SGLang through the local provider and constrains the answer (if not, the server validates the JSON itself, and a non-conformant answer fails open).
*Gate:* L2 and L3 parity ≥ 95% with zero severe units; W1 replays wake on the same event in every unit; T-class units measured with the change-unit method, with results per class. The owner approves the Claude-token spend before the replay runs.

**P1 — `ask`, `lookup` and `watch`.** The plugin with the server's three read-only tools, the skill, `mightling:delegate`, the ledger, busy handling (§4.3), the planner exclusion, the egress-audit run, and §6's practices in the coordinator's instructions.
*Gate:* two weeks of live use: no shadow miss in L2/L3; W1 results equal to the event in every audited case; a transcript analysis like §2 shows the waiting share and the share of calls above 300K both down, and the price-weighted cost per finished task down; no regression in the owner's own merge outcomes over the period.

**P2 — `task` and `night` for the whitelist classes.** Worktrees, the server's checks, the policy rules, the reviewer agent, the revert hook, automatic demotion. T1 first, then T2, T3, T4, T5, then N1; each enters on probation.
*Gate per class:* promotion as in §5.3 (20 shadow comparisons, no severe miss), positive net saving after failed attempts, and zero reverted delegated changes; any revert demotes the class until the owner reviews it.

**P3 — the product.** §8's changes, the documentation, the release's plugin folder.
*Gate:* a user who did not write the plugin installs it from a release on a client and on a node; the egress audit passes with it; L1 and W1 work out of the box, every other class starts on probation; the suite's tests cover refusal during a benchmark, the closed `watch` list, and quote verification.

---

## 10. Risks

| Risk | Effect | Answer |
|---|---|---|
| A verified quote answers a different question | Claude builds on the wrong code | Claude checks the quotes against its question; P0 counts these cases by hand |
| A diff passes the visible checks and the review but is wrong | A defect lands | Hidden-check units in P0; the shadow sample; demotion on revert; T-classes limited to work with strong checks |
| Probation never ends for rare classes | No saving in that class | Accepted: a class without evidence is not used |
| The skill over-delegates | Claude tokens rise | The break-even rule; the ledger's net saving demotes a class that loses |
| A benchmark night is disturbed | Its timing is wrong | The lock and gate check before any model call; no retries; `lookup` and `watch` use no model |
| The model or `ling` changes | The evidence no longer applies | All model-backed classes return to probation |
| Practices are not followed | §6's saving is not realised | The P1 gate measures them from the transcripts |

---

## 11. What was measured, and what was not

**Measured** (2026-10-27): the night's cost structure from 4,392 deduplicated responses; the line-count overstatement; cache rewrites after idle gaps; the waiting and search-and-read shares; the share above 300K; the savings replays of §6 under their stated assumptions; the rough distribution of brief classes.

**Read from source or documents:** the `ling exec` flags; Night Shift's `--test` and `--best-of` and the refusal of `--best-of` with `--on`; the model gate's 503 behaviour; the launcher's node tiers; Claude Code's plugin layout, tool naming, output limits, stdio timing, monitors and plugin-subagent limits, from its public documentation.

**Not measured:** any acceptance rate of Mightling on these classes; whether `--output-schema` constrains SGLang through the local provider; latency on real delegations; whether the reviewer at a cheaper model reaches the same verdicts; the recovery cost of retiring agents; whether a subscription's limits track the price ratios. The brief classes of §2.3 come from keyword rules checked by eye, and the run boundaries of §2.2 from command patterns; both are estimates.
