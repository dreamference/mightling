# Night Shift

Developer notes behind the Night Shift line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md`, whose §11 records what was built.

Night Shift is two halves that share only files:

- **Queueing.** `/night add <task>` (patch `0018`, logic in `ling-rs/src/night.rs`; also `ling night …` from a shell) queues a task under `$CODEX_HOME/night/tasks/` and never calls the model.
- **Running.** `ling-admin night run` (`dreamference/night_shift/`, started by the `mightling-night.timer` user unit that `night enable` installs) works through the queue.

## How a task runs

- Each task runs `ling exec` in its own git worktree on `night/<id>`, inside a memory-capped systemd scope; the runner, not the agent, commits, and nothing is merged, pushed or rebased.
- The runner's own test run executes what the agent wrote, so it goes through `ling sandbox` with a policy the runner fixes (workspace-write, no extra writable roots: the user's config makes `~/.mightling/skills` writable) and the task's `/airgapped` level, which is fixed once before the agent runs and exported to every command of the task. `[night] test_sandbox = false` is the user's opt-out.
- Both sides change a task only under `tasks/<id>.lock` (flock) and replace the JSON whole.
- The run never starts or loads the model server, gives way to an open `ling` session or an outside request, and `server start`, `codex build` and `index` refuse while it holds its lock.
- Before the first task the run refreshes each repository's code index (`ling-code index --exact --wait`, `night_shift_index.py`; `[night] index = false` switches it off).
- The desktop app's bundled `ling` is recognised as an app-server by name and `app-server` in its command line (`NightShiftHost._is_app_server`); see [desktop.md](desktop.md).
- `[night] nodes` and `/night add --on <node>` use paired nodes; see [node.md](node.md).
- The sandbox needs the AppArmor profile when the run comes from the timer; see [host-safety.md](host-safety.md#bubblewrap-needs-an-apparmor-profile-and-this-machine-now-has-one).

## Tests

`tests/test_night_shift.py` uses a scripted stand-in for `ling` and never creates a real scope or unit.
