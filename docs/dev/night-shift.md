# Night Shift

Developer notes behind the Night Shift line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md`, whose §11 records what was built.

Night Shift is two halves that share only files:

- **Queueing.** `/night add <task>` (patch `0018`, logic in `ling-rs/src/night.rs`; also `ling night …` from a shell) queues a task under `$CODEX_HOME/night/tasks/` and never calls the model.
- **Running.** `ling-admin night run` (`dreamference/night_shift/`, started by the `mightling-night.timer` user unit that `night enable` installs) works through the queue.

## How a task runs

- Each task runs `ling exec` in its own git worktree on `night/<id>`, inside a memory-capped systemd scope; the runner, not the agent, commits, and nothing is merged, pushed or rebased.
- The runner's own test run executes what the agent wrote, so it goes through `ling sandbox` with a policy the runner fixes (workspace-write, no extra writable roots: the user's config makes `~/.mightling/skills` writable) and the task's `/airgapped` level, which is fixed once before the agent runs and exported to every command of the task. `[night] test_sandbox = false` is the user's opt-out.
- **Best-of-N** (`[night] best_of`, **3 by default** since the owner's decision of 2026-10-19 after SWE-bench night 13, 31/50 against a single run's 22/50, p = 0.022; a task's own `best_of`, which `/night add --best-of N` writes, outranks it; `1` is a single attempt as before; refused with `--on`): a task is attempted N times, one after another in worktrees `<id>-a<k>`, and chooser v1 (`NightShiftChooser`, shared with `scripts/swe_bench_chooser_test.py`) picks the attempt the runner commits as `night/<id>` (`NightShiftBestOfN`; spec §11.9). Resumed and remote tasks run once; interactive `ling` is never affected.
  - The chooser: the regression filter drops attempts whose test run failed or timed out (all go on if all did), one survivor is chosen outright, several go to the pairwise judge, a direct `/v1/chat/completions` call to the night's model server with chooser v1's prompt (what nights 11 and 13 measured; not a `ling exec` session). Without a test command the judge alone decides and the report says so.
  - The window bounds it: an attempt after the first does not start when less of the window is left than the longest attempt took, a judge call waits at most until the window's end (60 s at the least) and none starts after it, and the record's `why` says why fewer than N ran.
  - Every attempt keeps `logs/<id>-a<k>.jsonl`, `.test.log`, `.diff` and `.last.txt`; the report's `Why` line and per-attempt lines name them. Every attempt's worktree is removed and only the chosen branch is kept.
  - The tests' `setup` fixture in `tests/test_night_shift.py` pins `DEFAULT_BEST_OF` to 1; best-of-N's own tests are `tests/test_night_shift_best_of_n.py`.
- Both sides change a task only under `tasks/<id>.lock` (flock) and replace the JSON whole.
- The run never starts or loads the model server, gives way to an open `ling` session or an outside request, and `server start`, `codex build` and `index` refuse while it holds its lock.
- The launcher's guards (the test guard and the finish check, `mightling_test_guard` and `mightling_finish_check` in the config file; SWE_BENCH spec §29) apply to a night's `ling exec` sessions like any other once switched on; they are off by default. Their hook, `ling guard`, is not a session (`NON_INTERACTIVE` in `night_shift_host.py`).
- Before the first task the run refreshes each repository's code index (`ling-code index --exact --wait`, `night_shift_index.py`; `[night] index = false` switches it off).
- The desktop app's bundled `ling` is recognised as an app-server by name and `app-server` in its command line (`NightShiftHost._is_app_server`); see [desktop.md](desktop.md).
- A `ling` whose binary a build has since replaced is still recognised: `/proc/<pid>/exe` then reads `<path> (deleted)`, and `NightShiftHost.executable_of` drops that trailing suffix before comparing; the exact comparison missed a busy app-server after a rebuild and deleted its marker (found 2026-10-19).
- **Repeating tasks** (the owner's decision 7 of DESKTOP_PARITY §9.5, 2026-10-21; `night_shift_recurring.py`): templates under `night/recurring/r<12 hex>.json` (repository, task, test, `daily` or `weekly` with its day), made by `ling-admin night every add --daily|--weekly <day> [--test cmd] -- <task>` in the repository, listed and dropped with `night every list|drop`. Each `night run` first queues the templates due that night through `ling night add` in their repository (one writer of task files, so best-of-N, the window and the report treat it as any task), at most once a day; a refusal is kept as `last_error` and tried the next night; what it did goes into the report's notes. The desktop app's Scheduled view repeats a task, lists and stops repeating ones, opens a finished run on its result (`logs/<id>[-a<chosen>].last.txt`), changes (that attempt's `.diff`, else `git diff base..branch`) and transcript (its `ling exec --json` log), opens the run's session as a thread, and gives a task queued from a thread (or `/night add` there) the thread's latest messages as context (`desktop/ui/src/tonight.ts`).
- `[night] nodes` and `/night add --on <node>` use paired nodes; see [node.md](node.md).
- The sandbox needs the AppArmor profile when the run comes from the timer; see [host-safety.md](host-safety.md#bubblewrap-needs-an-apparmor-profile-and-this-machine-now-has-one).

## Tests

`tests/test_night_shift.py` uses a scripted stand-in for `ling` and never creates a real scope or unit.
