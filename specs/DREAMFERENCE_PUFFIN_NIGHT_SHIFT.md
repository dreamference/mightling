# Puffin Night Shift — `/night`

**Status:** implemented on 2026-10-01: the launcher module `puffin-rs/src/night.rs`, patch `0018-night-slash-command`, and the runner package `dreamference/night_shift/` with `puffin-admin night {enable,disable,status,run}`. Where the build departs from the design below, §11 says how and why; the measured runs are in §11.4.
**Target:** the `puffin` terminal agent, and the GB10 it runs on overnight.
**Builds on:**
- the launcher in `puffin-rs/`;
- the patch series in `codex-patches/` ([PUFFIN_CODEX](./DREAMFERENCE_PUFFIN_CODEX.md));
- `puffin exec` and its `resume`;
- git worktrees;
- `VLLMServerManager.check_host_safety()` ([INFERENCE](./DREAMFERENCE_INFERENCE.md));
- the admission-control pattern in [PUFFIN_CODE_INDEX §6.4](./DREAMFERENCE_PUFFIN_CODE_INDEX.md);
- the nudge-on-stall loop in `tests/test_puffin_slash_commands.py`.

---

## 1. Goal

You queue coding tasks during the day with `/night add …`, from inside a `puffin` session. Overnight the GB10 works through them:
- each task runs in its own git worktree, on its own branch;
- tests run after each task;
- a report is waiting in the morning;
- nothing is merged, pushed or rebased.

**Why this fits the machine:**
- The GB10 is always on, and a token costs nothing.
- Its weakness is single-stream speed. Decode is limited by memory bandwidth, so a few streams side by side cost little more than one.
- A cloud agent does the same work on someone else's machine and bills for it.

**Non-goals:**
- **Merging or pushing:** a branch and a report are the whole output.
- **Scheduling on other machines.**
- **Running while you work:** the night run gives way to interactive use (§6.3).
- **Choosing tasks for you:** the queue holds only what was added explicitly.

---

## 2. The slash command

`/night` is a built-in slash command of the `puffin` TUI. Everything it does is local file work in the queue directory (§4). It never calls the model and never starts a run, so it answers instantly, even while a turn is in progress.

| Form | Effect |
|---|---|
| `/night add <task>` | Queues `<task>` for the repository of the current session, at its current `HEAD`. Prints the task id and branch name (`night/<id>`). |
| `/night add --test "<cmd>" <task>` | As above, with the command that decides pass or fail. Without it, the command is detected (§5.4). |
| `/night` or `/night list` | Lists this repository's queue: id, status, age, first line of the task. The last line is the next window. |
| `/night show <id>` | The full task, its status history, and for finished tasks the branch, diff stat, test result and stall notes. |
| `/night drop <id>` | Cancels a queued or interrupted task. A running task is marked `cancel-requested` and stopped at its next check (§5.3). A unique suffix of the id is enough (`/night drop a3f`). |
| `/night report` | The latest morning report (§5.6) for this repository. |

**Rules:**
- **Needs a git repository.** `add` outside one refuses, saying so.
- **The work starts from `HEAD`, not the working tree.** If the tree has uncommitted changes, `add` says the night run will not see them, and queues anyway.
- **The task text is stored verbatim**, together with the model id that was served when it was added (for the report, not to pin a model).
- **Also available outside the TUI:** the same forms work as `puffin night …`. The launcher handles them before Codex parses its arguments, as it does `puffin app` and `puffin update`, so scripts and cron can queue work.
- **After a night run:** the next `puffin` start prints one line before the TUI opens, e.g. `Night Shift: 3 done, 1 stalled — /night report`.

---

## 3. How it is built into `puffin`

The Codex source is never edited ([PUFFIN_CODEX §1](./DREAMFERENCE_PUFFIN_CODEX.md)), so `/night` is one more hook patch, and all the logic lives in the launcher crate.

**Patch `0018-night-slash-command`**, modelled on `0011-usage-token-stats`, adds to `codex-rs/tui`:
- the variant `SlashCommand::Night` (strum's kebab-case gives `night`), placed after `Goal` so it sits near the long-running-task commands in the popup;
- its description, "queue a task for the overnight run";
- membership in `supports_inline_args()` and `available_during_task()`;
- one arm in `dispatch_command` and one in `dispatch_command_with_args`. Both call `puffin_launcher::night::command(args, &self.config.cwd)`, which returns the lines to print, and add them with `add_plain_history_lines`, as `/usage` and `/cavemode` do;
- membership in `queued_command_drain_result`'s list of commands that run at once when queued.

`tui` already depends on the launcher crate (patch `0011`), so `0018` needs no manifest change.

**Budget.** The patch series was capped at 25,000 bytes (`test_the_patches_stay_small`) and stood at 24,800 bytes after `0017-cave-mode`. `0018` is 2,133 bytes (seven hunks in two files), so the series is 26,933 bytes and the cap was raised to 27,500 in the same commit, explicitly, as the name hooks and cave mode did.
- Adding a variant needs an arm in every exhaustive `match` over `SlashCommand`. Across the three files that is about six places, judging by where `Goal` appears.
- Where `_ =>` defaults already exist, rely on them.
- If `0018` cannot fit, raise the cap in the same commit and say why. Do not trim the other patches.

**Launcher module `puffin-rs/src/night.rs`:**
- argument parsing, for the TUI line and for `puffin night …`;
- queue reads and writes (§4);
- `git rev-parse` for the repository root and `HEAD` (a linked worktree's root is its main checkout, so a task queued from one is listed with the others);
- printing the latest report's section for this repository;
- the startup line.

Test-command detection is not here: it needs the worktree at the task's base, which exists only when the task runs, so the runner does it once (§11.1).

It has no network code and no knowledge of vLLM. Its unit tests run with the other launcher tests in the export directory.

**Tests:**
- `tests/test_puffin_slash_commands.py` enumerates slash commands from the source, so a visible `/night` gets a case there: `add` then `list` then `drop`, with no model call.

---

## 4. The queue

**Layout.** The queue lives under `$CODEX_HOME/night/`, i.e. `~/.puffin/night/`, one file per task so that concurrent `puffin` sessions never contend:

```
~/.puffin/night/
  tasks/<id>.json        one task; written atomically (write + rename)
  tasks/<id>.lock        flock held for every read-modify-write, by the launcher and the runner alike
  worktrees/<id>/        the task's git worktree while it exists
  logs/<id>.jsonl        `puffin exec --json` events of every attempt
  reports/<date>.md      the morning report
  runner.lock            flock held by the runner for the whole night
  seen.json              when each repository's results were last announced at startup
```

**Task record:**

```json
{
  "id": "20260929-2141-a3f",
  "repo": "/home/user/mightling",
  "base": "5484992…",
  "branch": "night/20260929-2141-a3f",
  "task": "Add type hints to dreamference/hardware/model_spec.py",
  "test": null,
  "model_at_add": "Intel/Qwen3.5-122B-A10B-int4-AutoRound",
  "status": "queued",
  "history": [{"at": "2026-09-29T21:41:07+01:00", "status": "queued"}],
  "attempts": 0,
  "result": null
}
```

**Statuses:**

| Status | Meaning |
|---|---|
| `queued` | Waiting for a night run. |
| `running` | A night run is working on it. |
| `done` | Finished, with changes and test results. |
| `no-change` | Finished, but made no changes. |
| `stalled` | Still no action after the nudges (§5.3). |
| `failed` | An error prevented the run. |
| `interrupted` | Cut off by the end of the window; kept for the next night. |
| `cancel-requested` | Dropped while running; the runner stops it at its next step and writes `cancelled`. A runner status never overwrites it. |
| `cancelled` | Dropped. |

**Ordering:** tasks run first in, first out within a repository. Across repositories they round-robin, so one long list cannot starve another.

---

## 5. The night run

### 5.1. Trigger

**Enabling.** `puffin-admin night enable --window 01:00-07:00` installs a systemd **user** timer and service, `puffin-night.{timer,service}`, which run `puffin-admin night run` at the window's start. `night disable` removes them, and `night status` shows the timer, the window and the queue across repositories.

**Where the pieces live.** The runner is Python, in `puffin-admin`, because host safety, vLLM health and the memory checks already live there. The slash command and the queue format belong to the launcher, and the runner reads the same JSON files.

### 5.2. Admission

The runner takes `runner.lock` and then checks, in order, stopping with a reason in the report at the first failure:
1. **vLLM is healthy** at the configured host. The runner never starts or stops the model server.
2. **The host is safe:** `check_host_safety()` passes, and MemAvailable is at least 8 GiB, which leaves room above earlyoom's 5% line.
3. **Nothing else heavy is running:**
   - the puffin build lock (`CodexBrandedBuilder`'s flock) is free;
   - no `puffin-admin index` is running;
   - no model load is in progress.
4. **Nobody is working interactively:**
   - no `puffin` TUI process is running outside the night run;
   - vLLM has had no running request for 10 minutes (`vllm:num_requests_running` on `/metrics`).

**Parallelism.** `N = min(night.max_parallel (default 3), floor(KV pool tokens / max_model_len))`. The KV pool comes from `/metrics` (`vllm:cache_config_info`: `num_gpu_blocks × block_size`), and `max_model_len` from `/v1/models`.
- **Why not the 8-sequence setting:** today's default has room for about three full 32k contexts before preemption. The one `CUBLAS_STATUS_INTERNAL_ERROR` crash came with two requests running and the KV cache at 92%.

### 5.3. One task

1. **Worktree.** `git worktree add ~/.puffin/night/worktrees/<id> -b night/<id> <base>`. If the base commit is gone, the task is marked `failed`.
2. **Run.** `puffin exec -C <worktree> -s workspace-write --json -o <last-message> "<prompt>"`, inside a transient systemd user scope:
   - `MemoryMax=8G` and `MemorySwapMax=0`, so a runaway test build is killed without reaching vLLM;
   - `CPUQuota=400%`, so the test commands of several tasks cannot starve vLLM's host threads.

   The prompt is the task text plus a fixed preamble: work only in this repository, make the change rather than describing it, run the test command, and end by listing the files changed.
3. **Stall check.** Once `exec` returns, `git status --porcelain` in the worktree decides. With no change, and a last message that announces work ("I'll now…", "Next I will…"):
   - the runner nudges with `puffin exec resume <session> "Go ahead and make the change now."`;
   - it nudges at most twice;
   - if there is still no change, the task is `stalled`, and its log is kept.

   This is the failure the live `/init` tests showed twice.
4. **Limits.** A task is stopped after `night.task_timeout` (default 90 minutes), or at the window's end:
   - SIGTERM, then SIGKILL after 30 s;
   - it is marked `interrupted`, and its worktree and branch are kept, so the next night resumes the recorded session rather than starting over.
5. **Tests.** The test command runs in the worktree under the same scope, with its own timeout (default 20 minutes). The exit code and the last 200 lines are recorded.
6. **Commit.**
   - **With changes:** they are committed on `night/<id>` as `night: <first line of task>`, using the repository's configured git identity and no tool attribution line. The worktree is then removed and the branch kept.
   - **With no changes:** the task is `no-change` and the branch is deleted.
7. **Cancellation.** Before each step the runner rereads the task record, so `cancel-requested` takes effect at the next step.

### 5.4. Test command detection

The first match wins, and the report says which one was chosen:
1. the task's `--test`;
2. `night.test` in the repository's `dreamference.toml`;
3. `pyproject.toml`, `pytest.ini` or `setup.py` (this repository has only the last) with a `tests/` directory: `python -m pytest -q`, using the repository's `.venv/bin/python` if present;
4. `Cargo.toml`: `cargo test`;
5. `package.json` with a `test` script: `npm test`;
6. otherwise none, and the report says the result is untested.

### 5.5. Stopping for interactive use

The runner stops starting new tasks when a `puffin` TUI starts or an outside request reaches vLLM. Running tasks finish their current attempt. The same check decides whether the next task may start.

### 5.6. The morning report

`reports/<date>.md` has one section per repository and one row per task. Each row gives:
- the status;
- the branch;
- `git diff --stat base..branch`;
- the test command and its result;
- the number of attempts and nudges;
- the wall time;
- for `stalled`, `failed` and `interrupted` tasks, the last message.

It ends with the review commands (`git diff <base>..night/<id>`, `git worktree list`) and a note of anything the admission checks skipped.

---

## 6. Safety

1. **Only the night branch is written.**
   - Nothing touches the user's checkout: the agent's sandbox allows writes only inside its worktree and `/tmp`.
   - The runner's own git commands name the worktree explicitly.
   - Nothing is merged, pushed or rebased.
   - **Nothing the agent wrote runs with the user's rights.** The runner's own test run executes the task's test command, and with it the agent's code, so it goes through the agent's sandbox too (`puffin sandbox`, workspace-write): writes reach the worktree and `/tmp` only, and the network follows the task's `/airgapped` level (§11.1). Until 2026-10-02 it ran as a plain `bash -c` inside the memory-capped scope. What still runs outside the sandbox is the runner's own code: its `git add` and `git commit`, and the index refresh, which has its own bubblewrap sandbox. `[night] test_sandbox = false` switches the test sandbox off, for the user to choose (§7).
2. **The model server is never at risk.**
   - It is never loaded, restarted or stopped by the runner.
   - Every task runs under a memory cap, and admission requires headroom.
   - While the runner holds its lock, `puffin-admin index`, `codex build` and `server start` refuse to run, with a message naming the night run. Since 2026-10-01 the lock file records who holds it (`NightShiftQueue.runner_lock(holder=…)`), because a SWE-bench run takes the same lock ([PUFFIN_SWE_BENCH §5.5](./DREAMFERENCE_PUFFIN_SWE_BENCH.md)), and the refusal names that holder. This is the rule that today's earlyoom kill of vLLM (an index run beside the server) made explicit.
3. **Interactive use wins** (§5.5).
4. **Network.** The night run uses the same channels as an interactive session. Once `/airgapped` exists, a night run follows the configured level, and `[night] airgapped` may set a stricter one ([PUFFIN_AIRGAPPED §7](./DREAMFERENCE_PUFFIN_AIRGAPPED.md)); this replaces the earlier plan to put night runs in the egress airlock by default.

---

## 7. Configuration

In the `night` table of `dreamference.toml`, resolved like every other setting ([CLI](./DREAMFERENCE_CLI.md)):

| Key | Default | Meaning |
|---|---|---|
| `window` | `01:00-07:00` | When the timer may run. |
| `max_parallel` | `3` | Upper bound on concurrent tasks; the KV pool may lower it (§5.2). |
| `task_timeout` | `90m` | Per-task wall clock, nudges included. |
| `test_timeout` | `20m` | Per test run. |
| `task_memory` | `8G` | `MemoryMax` of each task's scope. |
| `nudges` | `2` | Nudges before `stalled`. |
| `test` | *(detected)* | Default test command for the repository. |
| `test_sandbox` | `true` | Run the test command in the agent's sandbox (§6, §11.1). `false` runs it with the user's rights, for a test command that must reach Docker or write outside the worktree; at `/airgapped on` the tests are then not run at all, because nothing would keep them off the network. Read from the user's configuration, never from the worktree. |
| `airgapped` | *(none)* | A stricter `/airgapped` level for night runs alone ([PUFFIN_AIRGAPPED §7](./DREAMFERENCE_PUFFIN_AIRGAPPED.md)); a looser one than the configured level is ignored (§11.1). |
| `prompt` | *(none)* | The system prompt each task's `puffin exec` starts with, passed as `DREAMFERENCE_PUFFIN_PROMPT` ([PUFFIN_PROMPT §7](./DREAMFERENCE_PUFFIN_PROMPT.md)): `high-swe`, or any installed prompt. Absent: the configured one (`default` unless `puffin_prompt` says otherwise). A task resumed on a later night keeps the prompt its session started with. |
| `refine` | *(absent)* | Refine mode for night tasks ([PUFFIN_REFINE §5.3](./DREAMFERENCE_PUFFIN_REFINE.md)): `true` studies each new task in a session of its own first, puts the worktree back, and gives the task the refined description and a fresh `task_timeout` (within the window). Absent: the configured `puffin_refine` (off by default). A task resumed on a later night is not studied again. |
| `task_context` | `65536` | The smallest KV budget a concurrent task may be given; the run splits the pool between as many tasks as can each get this much (§11.1). |
| `compact_at` | *(absent)* | Each task's compaction limit, passed to every `puffin exec` of the task (`-c model_auto_compact_token_limit=<n>`). Absent: the task's share of the KV pool (§11.1), which is what holds the tasks of a night to the pool together. A number: that limit, and only as many tasks at once as fit at it. `0`: no limit is passed, the launcher's own (60% of the pool) applies, and nothing holds the tasks to the pool. |
| `idle_minutes` | `10` | How long the model must have been idle before a night starts (§5.2). |
| `index` | `true` | Refresh each repository's code index before its tasks start (§11.1). |
| `index_timeout` | `20m` | The most one repository's refresh may take; never more than half of what is left of the window. |

---

## 8. Tests

- **Launcher unit tests:**
  - argument parsing for every `/night` form;
  - atomic queue writes;
  - id generation;
  - repository and `HEAD` resolution;
  - uncommitted-change warning;
  - test detection;
  - report formatting.
- **Python unit tests for the runner:** a fake `puffin` binary scripted to change files, to stall, to stall and then act after a nudge, or to hang. Each admission check is tested separately, as are cancellation, `interrupted` and resume, and parallelism from a mocked `/metrics`.
- **Live test (skips without a server):** a throwaway repository with one failing test, one task, a window of "now". Expected results:
  - a `night/<id>` branch whose test passes;
  - the report row;
  - the user checkout's `git status` unchanged;
  - earlyoom's journal free of kills during the run.
- **Slash suite:** `/night add`, `/night list`, `/night drop` in the TUI.

---

## 9. Acceptance criteria

- **Speed:** `/night add` returns in under 100 ms and makes no model request.
- **One night on this repository** with three tasks:
  - produces a branch or an honest status for each;
  - writes a report;
  - leaves the working tree, `main` and `origin` untouched.
- **Safety:** vLLM stays up through the night, and earlyoom logs no kill.
- **Stalls:** a scripted announce-only reply is caught and nudged, then marked `stalled` if it never acts.
- **Patch size:** `0018` fits the patch budget (§3), or the budget change is explicit.

---

## 10. Open questions

- **Waiting on vLLM:** should admission wait, rather than skip, when vLLM is down at window start? For now it skips and reports.
- **Tasks needing input:** Codex's own `/goal` mode already frames long-running work. Should a night task be a goal, so that a question the agent would ask is recorded in the report instead of ending the turn? That needs measuring against the stall check.
- **Drafter tuning:** it needs vLLM stopped ([SELF_SPEEDING §5](./DREAMFERENCE_SELF_SPEEDING.md)), so the two cannot overlap. The proposal is that tuning takes the tail of the window, only after the night queue is empty.

---

## 11. As built (2026-10-01)

### 11.1 Departures from the design, each for a measured reason

- **Parallelism uses a per-task budget, not the full context, and the budget is enforced.** §5.2's formula, `floor(KV pool / max_model_len)`, gives **0** on the default model: SGLang's pool is 156,907 tokens (`sglang:max_total_num_tokens`; 144,870 before the server's restart on 2026-10-01) and the served context is 262,144, so not even one full context fits. A night run instead divides 90% of the pool between as many tasks as can each get `task_context` (default 65,536): `N = max(1, min(max_parallel, floor(0.9 × pool / task_context)))`, and gives each task `min(0.9 × pool / N, 60% of the pool)` as the compaction limit of every `puffin exec` it runs (`NightShiftHost.task_budget`). So `N × limit ≤ 0.9 × pool`: the tasks fit in the pool together, which until 2026-10-02 was assumed and not checked; three tasks with the launcher's own 94,144-token limit could ask for 282K of a 157K pool. Today that is **2 tasks of 70,608 tokens**. The 10% left over is for Codex compacting after the turn that crosses the limit, not before it. `task_context` was 49,152 until then; enforced as a compaction limit, that size cost the task in the compaction measurement (an hour without finishing its own tests, where the same task with no limit passed in 14-18 minutes and peaked at 49,241 and 79,909 tokens), so the floor was raised to 65,536. Whether 70,608 costs anything is unmeasured: on that task the 80K run would compact about once. With the pool unknown, one task runs at a time. `[night] compact_at` overrides the limit (and the parallelism follows it), and `compact_at = 0` restores the old, unenforced behaviour; the report's first note says which applied. The pool is read under both engines' names: `sglang:max_total_num_tokens`, or `num_gpu_blocks × block_size` from `vllm:cache_config_info`.
- **Idle is a counter, not a gauge.** "No running request for 10 minutes" cannot be read from a gauge sampled once. The runner samples `/metrics` every 30 s and requires the running and queued gauges (`sglang:num_running_reqs` + `sglang:num_queue_reqs`, or `vllm:num_requests_running` + `vllm:num_requests_waiting`) to be zero **and** the prompt-token counter (`sglang:prompt_tokens_total` / `vllm:prompt_tokens_total`) unchanged for `idle_minutes`. If the window closes first, the night is skipped and the report says so.
- **"An outside request" is defined.** Once night tasks are running, requests are expected. An outside request is one beyond the night's own: the engine's running plus queued requests exceed the number of night tasks currently waiting on the model. A `puffin` TUI is found by process: the installed binary, `argv[0]` `puffin` or `codex` (not Codex's sandbox re-executions), no non-interactive subcommand, and not carrying `PUFFIN_NIGHT_RUN=1`, which the runner sets on every process it starts.
- **Memory is admitted per task, not once.** Before each start: `MemAvailable` minus what the running tasks may still grow into (`task_memory` minus each scope's `MemoryCurrent`) must be at least the 8 GiB reserve plus one more `task_memory`. The night-wide check of §5.2 still runs first.
- **Test detection lives in the runner only**, in the worktree at the task's base, so there is one implementation; `/night add` says the command is detected when the task runs, and the report and `/night show` print the command and where it came from. A worktree has no `.venv` of its own, so the main checkout's is used for pytest.
- **The model id recorded at `add`** comes from `$CODEX_HOME/model_catalog.json` (`models[0].slug`), which the launcher writes at every start: the launcher module has no network code.
- **The window shown by `/night list`** is the installed timer's (`night enable` writes it on a `# Night Shift window:` line of the timer unit), falling back to `[night] window` with a note that Night Shift is not enabled.
- **The timer's service names everything absolutely.** A user service has neither `~/.local/bin` nor the virtualenv on its PATH: `ExecStart` is this environment's `puffin-admin`, `PATH` adds `~/.local/bin` (for `puffin-search`/`puffin-fetch`) and `~/.cargo/bin`, and the runner runs the installed `puffin` by path. `night enable` warns when lingering is off, because a user timer stops at logout.
- **Every `puffin exec` is told the model server** the night was admitted against (`DREAMFERENCE_VLLM_HOST`), since 2026-10-02: the launcher otherwise looks for a Puffin node, which from a worktree could mean a browse of the network ([PUFFIN_NODE §18.2](./DREAMFERENCE_PUFFIN_NODE.md)). On a machine that is not a node, `/night add` is refused: nothing there would run the task.
- **Every `puffin exec` gets `stdin` from `/dev/null`.** Without it, exec prints "Reading additional input from stdin..." and, under a service with no terminal, waits.
- **Each task's processes run under `choom -n 500`** inside the scope, so that if memory runs out anyway earlyoom picks them before the model server.
- **`puffin-admin night run --ignore-open-sessions`** skips the TUI check (requests from open sessions still pause the run). It exists for testing beside an open session; the timer never passes it.
- **The runner's test run is sandboxed, with a policy the runner fixes** (2026-10-02). The command is `puffin sandbox -c 'sandbox_mode="workspace-write"' -c 'sandbox_workspace_write.writable_roots=[]' -c 'sandbox_workspace_write.network_access=<true|false>' -- bash -c <test>`, run with the worktree as its working directory inside the task's scope. Three things were found by running it:
  - `-C <dir>` is refused without `--permission-profile`, so the working directory is the process's own;
  - the user's `~/.puffin/config.toml` lists `~/.puffin/skills` as writable (the skill installer needs it), and a test run that inherited that could leave instructions behind for every later session, so `writable_roots` is set to empty: only the `/airgapped` level comes from outside the runner;
  - Codex's sandbox helper applies `/airgapped` to `puffin sandbox` as it does to the agent's commands (patch `0019`; `DREAMFERENCE_PUFFIN_AIRGAPPED=on` with `network_access` left true still had no network), so at `on` the seal is applied twice.
  The cost is the agent's own: a test command that writes outside the worktree and `/tmp`, needs the Docker socket, or needs Cargo or npm to download into `$HOME` fails in the sandbox with "Read-only file system", exactly as it did when the agent ran it. `[night] test_sandbox = false` is the way out, and the report names the sandbox beside each test result.
- **A task's `/airgapped` level is fixed once, before the agent runs.** It is the strictest of: the level resolved as the launcher resolves it for the worktree (session file, `DREAMFERENCE_PUFFIN_AIRGAPPED`, then the configuration files), the main checkout's `dreamference.toml` (usually untracked, so the worktree has no copy and the sandbox helper alone would not see it), `[night] airgapped`, and the level an earlier night recorded for the task. The runner records it on the task and sets `DREAMFERENCE_PUFFIN_AIRGAPPED` to it for every command of the task, each `puffin exec` and the test run. Read again before the test run, it would be whatever the agent had by then written into the worktree's `dreamference.toml`.
- **The code index is refreshed before the tasks start** ([CODE_INDEX §6.3](./DREAMFERENCE_PUFFIN_CODE_INDEX.md)). After admission and before the first task, the runner calls `puffin-code index --exact --wait` once per repository with queued tasks, so tasks begin with a fresh index and the executing indexers (Rust, Java, .NET, in a trusted repository) run when nobody is waiting. `puffin-code` admits and sandboxes its own runs. The outcome is a note of the morning report (`Code index of <repo>: 7 ok, 1 deferred (…)`). A refresh that passes `index_timeout` is stopped, and with it **every** scope of `puffin-index.slice`: admission refuses to start a night while an index scope is live, so whatever is in the slice then is the night's own. Without an installed `puffin-code` nothing runs and nothing is said.
- **A night task's `puffin exec` also starts `puffin-code session`.** It maps the worktree onto the main checkout's index (code-index spec §4.1), and that index's session lock lets one session process own the repository, so parallel tasks do not each start an index run.

### 11.2 Where the code is

| Piece | Path |
|---|---|
| `/night`, `puffin night …`, startup line | `puffin-rs/src/night.rs` |
| TUI hooks | `codex-patches/0018-night-slash-command.patch` |
| Queue (shared format, per-task locks, `runner.lock`) | `dreamference/night_shift/night_shift_queue.py` |
| Settings (`[night]`) | `dreamference/night_shift/night_shift_settings.py` |
| Host probes (model server, memory, sessions, heavy jobs) | `dreamference/night_shift/night_shift_host.py` |
| One task (worktree, exec, nudges, tests, commit) | `dreamference/night_shift/night_shift_task_run.py` |
| Admission and scheduling | `dreamference/night_shift/night_shift_runner.py` |
| Morning report | `dreamference/night_shift/night_shift_report.py` |
| Timer | `dreamference/night_shift/night_shift_scheduler.py` |
| Code index refresh before the tasks | `dreamference/night_shift/night_shift_index.py` |
| Refusals during a night run | `DreamferenceCLIController._refuse_during_night_run` (`index`, `codex build`, `server start`) |

### 11.3 Tests

- **Launcher** (`cargo test -p puffin-launcher` in the export): parsing of every `/night` form and of `puffin night …`; add, list, show and drop round trip, drop by id suffix, drop of a running task; uncommitted-change warning; refusal outside a repository; a linked worktree's tasks belong to the main checkout; 200 ids without a collision; the report section and the newest report; the startup line announced once; the window from the timer, then the config.
- **Runner** (`tests/test_night_shift.py`, a scripted stand-in for `puffin`): a change committed on `night/<id>` with the user's checkout untouched; a failing test recorded; an announce-only reply nudged twice and then `stalled`; a nudge that works; `no-change`; an exec error; an interrupted task keeping its worktree and resuming its session the next time; cancellation before a start and while running; a vanished base; test detection in its order; metrics under both engines' names; parallelism never 0; TUI command lines; each admission check on its own; the idle wait counted from the last change; the window closing while waiting; round-robin; an outside request or session blocking a start; memory blocking a start; the test run sent through `puffin sandbox` with the runner's fixed policy and the task's session; no network for it at `/airgapped on`; the level fixed before the agent runs, so an agent that rewrites the worktree's config cannot loosen it, and kept by a resumed task; the main checkout's untracked level reaching its tasks; the launcher's resolution order; unsandboxed tests as an explicit choice, refused at `on`; a whole night of three tasks with its report; a refused admission keeping the queue; the index refresh (once per repository, before the first task; off; not installed; half the remaining window); a second runner refused; `codex build` refused while a night run holds the lock; the queue format shared with the launcher; the timer units; the report's rows.
- **Not covered by the slash suite.** `tests/test_puffin_slash_commands.py` enumerates the slash commands of unpatched Codex, so a command a patch adds is invisible to it, as `/cavemode` was. `/night` was checked in the TUI itself instead, driven through tmux (§11.4).
- **Codex's own TUI snapshots** that list the slash-command popup change again with `/night` in it, as they did with `/cavemode`; they need new snapshots reviewed by hand.

### 11.4 Measured runs

One task, run for real on 2026-10-01 against the default model (Qwen3.8-27B on SGLang), with the rebuilt `puffin` (16 patches):

- **Queued from a shell.** In a throwaway repository (`calc.py` whose `add` subtracts, one failing test), `puffin night add "The test tests/test_calc.py::test_add fails. Find the bug in calc.py and fix it."` printed the id, the branch and the not-enabled note; `puffin night list` showed it `queued`; an unknown verb printed the usage line and exited 2.
- **Run.** `puffin-admin night run --minutes 20 --idle-minutes 1`: admission passed (model answering, host-safety checks, memory, no heavy job, one idle minute), parallelism 3. The task took 7 s: `running` at 18:42:36, `done` at 18:42:43, no nudge. The whole command took 1 min 15 s, one minute of it the idle wait.
- **Result.** Branch `night/20261001-1841-4fc`, one commit by the repository's own git identity, `return a - b` → `return a + b`; detected test command `python3 -m pytest -q`, passed (2 tests). The checkout stayed on `master` at its commit, the worktree was removed, and `puffin night show`, `puffin night report` and the report file agreed.
- **Found and fixed.** The commit also carried `__pycache__/*.pyc`, which the test run had written in a repository with no `.gitignore`. The runner now stages the agent's changes *before* its own test run and commits what was staged; a test covers it. This removes only what the runner's test run writes: the agent is told to run the test command too, and what its own commands leave in a repository without a `.gitignore` is still committed with its changes.
- **Not run live on 2026-10-01:** a stall and its nudges, an interrupted task resumed on a second night, two tasks in parallel, and the installed timer firing at 01:00. The second and third ran on 2026-10-02 (below); a stall has still not been provoked live, and the timer has not yet fired.
- **In the TUI** (a real `puffin` session in tmux, same repository): `/nig` shows `/night  queue a task for the overnight run` in the popup; `/night list`, `/night add --test "python3 -m pytest -q" …`, `/night show 4fc` (id suffix) and `/night report` each print at once, with no model turn; the quoted `--test` command is recorded verbatim.
- **Closed on 2026-10-02: the runner's test run is sandboxed.** It used to execute the task's test command, and so code the agent wrote, with the user's full rights. Measured through the real path (`_prepare_worktree` and `_run_tests`, the real scope and binary, a probe as the test command):

  | The test command tried to | `/airgapped off` | `/airgapped on` |
  |---|---|---|
  | write in the worktree | written | written |
  | write in `/tmp` | written | written |
  | write beside the worktree, in the user's checkout, in its `.git` | refused | refused |
  | write in the home folder, `~/.puffin/skills`, beside `~/.bashrc` | refused | refused |
  | reach the internet (`https://example.com`) | 200 | no connection |
  | reach the model server | 200 | no connection |

  The probe's exit status (7) came back as the test result, and a test run cut off by `test_timeout` (6 s against `sleep 987`) left no process behind. `on` gave the same result whether it came from the environment variable or only from the main checkout's untracked `dreamference.toml`. One task then ran end to end against the model in 35 s: the agent fixed `calc.py`, the sandboxed `python3 -m pytest -q` passed, including a test that passes only if the home folder cannot be written, and the fix was committed on its branch. This repository's own tests pass inside the sandbox (57 of 57 in the two files tried) once `tests/conftest.py` stopped following the `CODEX_HOME` that `puffin` exports to every command.
- **Not run:** `cargo test` and `npm test` inside the sandbox. A Rust test run needs its crates already in `~/.cargo/registry`, and `npm test` needs `node_modules` in the worktree, which a fresh worktree does not have.

**Resumed and parallel, run for real on 2026-10-02** against the default model, the 11:59 `puffin` build (17 patches), three throwaway repositories with a neutral git identity:

- **The night run gives way, as designed.** Three other tasks kept the model server busy all day (6 to 8 running and 2 to 20 queued requests). Unmodified, `night run` first refused because a `puffin` session was open, and with `--ignore-open-sessions` waited out its two-minute window ("the model was in use until the window closed"). To exercise the rest, the runs below used the real `NightShiftRunner` with one change, made in a driver script and not in the code: the host's metrics read the running gauge and the prompt-token counter as idle. Admission, host safety, memory, scopes, the sandboxed test run, commits and the report were the shipped code.
- **Interrupted, then resumed, then finished.** One task asked for a twelve-function module with three tests per function. Night 1 (`--minutes 2`): `interrupted` at the window's end after one command, worktree and branch kept, session id recorded. Night 2 (`--minutes 40`): the code-index refresh waited 19 min 54 s (another task's index run held the host-wide index lock while waiting for an idle model), then the task restarted as `puffin exec … resume <the same session> "This task was cut off by the end of last night's window. Continue it where you left off and finish it."` with `-c model_auto_compact_token_limit=49152`. In 20 minutes it wrote `textstats.py` and its tests (13 passing) and was `interrupted` again, the work left uncommitted in the kept worktree; the stream once hit "idle timeout waiting for SSE" with 20 requests queued. Night 3: `done` in 16 min 4 s, one commit (3 files, +319), the sandboxed `python3 -m pytest -q` passed 42 tests, worktree removed.
- **Parallel, and the memory gate.** Night 3 also ran two one-line tasks in two other repositories (`max_parallel` 3, parallelism 3 from the KV pool). Two started together at 13:42:18. The third waited: with about 27 GiB available and two tasks' 8 GiB allowances outstanding, 11.6 to 11.8 GiB was left against the 16 needed, and it started at 13:43:34, when the first finished. All three `done`; the report listed each with its branch, diff and sandboxed test result.
- **Found and fixed:** the report noted that one wait eight times in a minute, because the free-memory figure in the reason changed on every poll. A wait is now noted once per kind of reason (`reason_kind`), with a test.
- **Found, not fixed** (the code index's area): each task's systemd scope outlived its task, holding a `puffin-code supervise` process that the agent's session had started to index the worktree and that was still waiting for the index lock. The scopes used about 1 MiB each and were stopped by hand. Until `puffin-code session` takes its supervisor down with it, a night leaves one such scope per task.
- **Index refresh as a cost.** With another index run holding the lock, the refresh before tasks spent its whole budget (half the remaining window, capped at `index_timeout`) waiting. On a shared machine `[night] index = false` saves that wait; the runs above that needed it used it after the refresh had been exercised once.
- **Not provoked:** a stall and its nudges. No task announced work without doing it.

### 11.5 Several nodes (2026-10-03)

Two additions from [PUFFIN_NODE §18.8](./DREAMFERENCE_PUFFIN_NODE.md), both inactive on a machine with no paired node:
- **Replica lanes.** A paired node serving the same model is a second model server for the run (`[night] nodes`, default `"paired"`). Tasks still run here; each lane holds as many as its own KV pool allows, and an open `puffin` session here holds up this machine's lane only. The report says which tasks used another node's server.
- **`/night add --on <node>`.** The task is handed to that node at night, worked by that node's own runner against its own model inside the job sandbox, and its branch is fetched back; meanwhile it is `sent` here. Waiting for it does not hold the runner lock.


### 11.6 The sandbox from the timer (2026-10-03)

Every task is a sandboxed `puffin exec` and its tests run under `puffin sandbox`, and on Ubuntu 24.04 (`kernel.apparmor_restrict_unprivileged_userns=1`) `bwrap` is refused a user namespace from a systemd unit unless an AppArmor profile allows it, so until 2026-10-03 a run started by the timer would have had every sandboxed command fail on this machine; runs by hand had passed only because their shells inherited the PyCharm snap's AppArmor label. `SandboxPrerequisite` (`vllm_server/sandbox_prerequisite.py`, SETUP §3.3) now checks `bwrap` from a throwaway user unit on every `puffin-admin` run: `night run` and `night enable` are refused while it fails and nobody is at a terminal (the timer), or while the user has chosen "turn off" (which also removes the timer, remembered in `~/.config/dreamference/sandbox.json`). `puffin-admin host setup` installs the profile (`/etc/apparmor.d/puffin-bwrap`); it was loaded on this machine on 2026-10-03, after which `bwrap` and `puffin sandbox -- true` succeed from a unit. A night run started by the timer with the profile in place has not yet been watched.
