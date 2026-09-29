# Puffin Night Shift — `/night`

**Status:** proposed. Nothing in this spec is implemented yet.
**Target:** the `puffin` terminal agent, and the GB10 it runs on overnight.
**Builds on:**
- the launcher in `puffin-rs/`;
- the patch series in `codex-patches/` ([PUFFIN_CODEX](./DREAMFERENCE_PUFFIN_CODEX.md));
- `puffin exec` and its `resume`;
- git worktrees;
- `VLLMServerManager.check_host_safety()` ([INFERENCE](./DREAMFERENCE_INFERENCE.md));
- the admission-control pattern in [PUFFIN_CODE_INDEX §5.5](./DREAMFERENCE_PUFFIN_CODE_INDEX.md);
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
| `/night drop <id>` | Cancels a queued task. A running task is marked `cancel-requested` and stopped at its next check (§5.3). |
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

**Patch `0017-night-slash-command`**, modelled on `0011-usage-token-stats`, adds to `codex-rs/tui`:
- the variant `SlashCommand::Night` (strum's kebab-case gives `night`), placed after `Goal` so it sits near the long-running-task commands in the popup;
- its description, "queue a task for the overnight run";
- membership in `supports_inline_args()` and `available_during_task()`;
- one arm in `dispatch_command` and one in `dispatch_command_with_args`. Both call `puffin_launcher::night::command(&args, &cwd)`, which returns the lines to print, and add them with `add_plain_history_lines`, as `/usage` does.

**Budget.** The patch series is capped at 20,000 bytes (`test_the_patches_stay_small`) and stands at 19,173 bytes since `0016-doctor-offline`. That leaves 827 bytes, which `0017` is unlikely to fit.
- Adding a variant needs an arm in every exhaustive `match` over `SlashCommand`. Across the three files that is about six places, judging by where `Goal` appears.
- Where `_ =>` defaults already exist, rely on them.
- If `0017` cannot fit, raise the cap in the same commit and say why. Do not trim the other patches.

**Launcher module `puffin-rs/src/night.rs`:**
- argument parsing;
- queue reads and writes (§4);
- `git rev-parse` for the repository root and `HEAD`;
- test-command detection;
- report formatting.

It has no network code and no knowledge of vLLM. Its unit tests run with the other launcher tests in the export directory.

**Tests:**
- `tests/test_puffin_slash_commands.py` enumerates slash commands from the source, so a visible `/night` gets a case there: `add` then `list` then `drop`, with no model call.

---

## 4. The queue

**Layout.** The queue lives under `$CODEX_HOME/night/`, i.e. `~/.puffin/night/`, one file per task so that concurrent `puffin` sessions never contend:

```
~/.puffin/night/
  tasks/<id>.json        one task; written atomically (write + rename)
  worktrees/<id>/        the task's git worktree while it exists
  logs/<id>.jsonl        `puffin exec --json` events of every attempt
  reports/<date>.md      the morning report
  runner.lock            flock held by the runner for the whole night
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
3. `pyproject.toml` or `pytest.ini` with a `tests/` directory: `python -m pytest -q`, using the repository's `.venv/bin/python` if present;
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
2. **The model server is never at risk.**
   - It is never loaded, restarted or stopped by the runner.
   - Every task runs under a memory cap, and admission requires headroom.
   - While the runner holds its lock, `puffin-admin index`, `codex build` and `server start` refuse to run, with a message naming the night run. This is the rule that today's earlyoom kill of vLLM (an index run beside the server) made explicit.
3. **Interactive use wins** (§5.5).
4. **Network.** The night run uses the same channels as an interactive session. Once the egress airlock exists ([PUFFIN_EGRESS](./DREAMFERENCE_PUFFIN_EGRESS.md)), night runs use it by default.

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
- **Patch size:** `0017` fits the patch budget (§3), or the budget change is explicit.

---

## 10. Open questions

- **Waiting on vLLM:** should admission wait, rather than skip, when vLLM is down at window start? For now it skips and reports.
- **Tasks needing input:** Codex's own `/goal` mode already frames long-running work. Should a night task be a goal, so that a question the agent would ask is recorded in the report instead of ending the turn? That needs measuring against the stall check.
- **Drafter tuning:** it needs vLLM stopped ([SELF_SPEEDING §5](./DREAMFERENCE_SELF_SPEEDING.md)), so the two cannot overlap. The proposal is that tuning takes the tail of the window, only after the night queue is empty.
