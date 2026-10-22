# Where a session's edits land: in place, or a worktree (`/worktree`)

> **Status:** implemented 2026-10-22, for 1.7.2. The owner's decision of 2026-10-22: a slash command chooses whether a session edits the user's checkout in place or works in an isolated git worktree, and the default is Codex's, in place. Code: `ling-rs/src/worktree.rs`, `ling-rs/src/night.rs` (`/night apply`), `dreamference/night_shift/night_shift_report.py`, `desktop/ui/src/worktrees.ts`. Developer notes: [docs/dev/launcher.md](../docs/dev/launcher.md#where-edits-land-worktree), [docs/dev/night-shift.md](../docs/dev/night-shift.md).

## 1. Why

A draft paper said that "in Mightling's autonomous sessions (Work mode and Night Shift), all execution occurs in isolated, ephemeral Git worktrees", and that users assume the run failed because the edits never reach their branch. The complaint behind it is real for one surface and false for the others: "the agent said it edited `auth.py`, but my editor didn't change" happens when a session works in a worktree and nothing says so. §2 records what each surface does; §3 is the design that makes the choice explicit, visible and reversible.

## 2. What each surface does (measured 2026-10-22, before this change)

| Surface | Where edits land | Measured how |
|---|---|---|
| `ling` (TUI) | In place, in the folder it starts in (or `-C`). Codex's own `--worktree` flag and `/worktree` picker (Codex 0.158, feature `worktrees`, stable and on) move a session into a managed, detached worktree under `$CODEX_HOME/worktrees/<4 hex>/<repo>` only when asked | Code read: no launcher code moves a session; `ling --help` lists `--worktree` |
| `ling exec` | In place. `ling exec --worktree` makes a managed worktree (`~/.mightling/worktrees/0564/wtrepo`, detached HEAD) and the source is untouched | Run against the GB10 in a scratch repository: in place, `auth.py` changed and `git status` showed ` M auth.py`; with `--worktree`, the source unchanged and the edit in the worktree |
| Desktop app, Work | In place by default. **Local** is the default of the Local/Worktree toggle under the composer of a new thread in a git project (`App.tsx`, built 2026-10-16, DESKTOP_PARITY §2.6); **Worktree** makes a managed worktree on the vendor's contract, with Hand off back to the local checkout | Code read |
| `ling web` | In place: the same page, without the main process's git, so it offers no Worktree toggle | Code read (`bridge.git.available()` is false there) |
| Phone bridges (`chat` prompt) | The Ask folder (`~/.mightling/ask/…`), never a repository; there is no Work thread from the phone yet (SIGNAL §19, `/work` not built) | Code read (`thread/start` with `prompt: "chat"` and no `cwd`) |
| Night Shift | A worktree per task and per attempt, `$CODEX_HOME/night/worktrees/<id>[-a<k>]` on branch `night/<id>[-a<k>]` (git's metadata in `<repo>/.git/worktrees/<id>`); the runner commits the chosen attempt on `night/<id>`, removes every worktree and merges nothing | Code read (`night_shift_task_run.py`), NIGHT_SHIFT §5.3, §11.9 |

So the paper's sentence is true of Night Shift only. Work mode edits in place unless the user picks Worktree, and the worktree folder pattern `.git/worktrees/night-*` exists nowhere: Night Shift's are named by the task id.

## 3. Design

### 3.1 The setting and its default

- **Default: in place**, for every interactive surface, as in Codex's CLI and app. Worktree mode is opt-in.
- **Where it is kept:** `mightling-worktree-mode = "local" | "worktree"` in `[desktop]` of `$CODEX_HOME/config.toml`, the user's own file. The `[desktop]` table is where the vendor keeps the worktree settings the `codex-worktree` crate reads (`git-worktree-root`, …), so the desktop app reads the key with its existing `config/read` and the terminal and the app share one choice. Never in a repository: the launcher reads the file itself, not the merged configuration, so a repository's `.codex/config.toml` cannot move a terminal session. (The desktop's `config/read` does merge a trusted project's layer, so a trusted repository could preselect the app's toggle; the user still sees the toggle and can flip it.)
- **Per session:** `DREAMFERENCE_MIGHTLING_WORKTREE=on|off` overrides the file. The egress audit sets it `off`.
- **Which sessions:** a new interactive TUI session (`ling`, `ling "<prompt>"`). Never `exec`, `review`, `resume`, `fork` or `app-server`, and never when Codex's own `--worktree` or `--ignore-user-config` is given. Unattended callers (Night Shift, SWE-bench, the Codex test runner) use `exec` and are never moved.

### 3.2 The command

The name follows Codex (`--worktree`, `/worktree`, the app's Local/Worktree) and Claude Code (`--worktree`). Codex already owns `/worktree` alone, its picker; Mightling adds sub-commands and leaves the bare command to Codex.

| Command | What it does |
|---|---|
| `/worktree on`, `ling worktree on` | Saves worktree mode for new sessions (and the app's default toggle). The running session is not moved: Codex's picker can move a conversation now |
| `/worktree off`, `ling worktree off` | Saves in-place mode |
| `/worktree status`, `ling worktree [status]` | The setting and where it came from; where this session started (in place: folder and branch; worktree: folder, branch, source, base) and what is not yet applied (`--stat`) |
| `/worktree apply`, `ling worktree apply [<id>]` | Brings the session's changes into the user's checkout (§3.4) |
| `/worktree discard`, `ling worktree discard [<id>]` | Drops them (§3.5) |
| `ling worktree list` | The worktree sessions of this repository: running or finished, what each holds |
| `/worktree` | Codex's picker, unchanged |

**Built without a patch.** The patch series is at 57,523 of its 58,000 bytes, and a new slash command costs about 2 KB of patch (the variant, its description, its capability lists, its dispatch arms). Instead the composer's `Enter` hook, which patch `0029` already routes to `compat_keys::enter`, answers `/worktree <sub-command>` in every mode before Codex sees the line; outside the compatibility mode it answers nothing else. The answer is shown as an info line, as the compatibility mode's answers are.

### 3.3 A session in worktree mode

- **Made by the launcher**, which runs inside Codex's own process before Codex parses its arguments (patch `0002`): `git worktree add -b ling/<id> $CODEX_HOME/session-worktrees/<id>/<repo> HEAD`, the record `session.json` beside it (source, base commit, branch, pid, what was applied), then `set_current_dir` into the same sub-folder of the worktree and `-C` rewritten when the user gave one. Codex then starts there as if started there, so everything that reads the working folder follows: the sandbox's writable root, `AGENTS.md`, the code index (which reads the main worktree's project, CODE_INDEX §4.1) and the skills catalog.
- **Its own branch**, not detached HEAD as the vendor's managed worktrees are: `ling/<id>` names the work in `git branch`, `git log` and the footer.
- **Outside the vendor's pool** (`$CODEX_HOME/worktrees`), whose cleanup the desktop app runs (keep the newest 15): a terminal session's work is never removed by a window's cleanup.
- **No trust prompt:** Codex resolves a linked worktree's trust through its `.git` file to the main checkout.
- **What it starts from:** `HEAD` of the source. Uncommitted changes in the source are not carried over; the start notice says so when there are some. Ignored files (`.env`, build outputs) and submodules are not in the worktree either.
- **Git inside the sandbox:** as in place. Reads (`git status`, `git diff`) work; writes to the repository's `.git` (`git add`, `git commit`) fail under workspace-write, because Codex keeps `.git` read-only, and the worktree's index lives under the source's `.git/worktrees/`. Measured 2026-10-22: `git add` failed with "Read-only file system" in the worktree and in place alike.
- **Not nested:** a session started inside a session worktree works there, with a notice.
- **Leftovers:** at each new interactive start the launcher removes the worktrees, branches and folders of finished sessions that hold nothing (no change since their base or since their last apply; the session's pid gone). One that holds changes is kept, and a start in its repository says how many: `ling worktree list`.

### 3.4 Bringing changes back: `apply`

- **What is applied:** everything in the worktree now, committed by the agent or not, new files included and ignored ones left out. It is read as a tree written through a temporary index (the worktree's own index is not touched), and diffed from the base, or from the last tree applied, so a second `apply` brings only what is new.
- **How:** `git apply` in the user's checkout (the session's source, or the checkout `ling worktree apply <id>` runs in), **as uncommitted changes**: exactly what an in-place session would have left, for the user to review in their editor and commit. Nothing is committed on the user's branch.
- **All or nothing:** `git apply --check` first. When the changes do not apply cleanly (the user changed the same lines, or a new file already exists), nothing is changed and the answer names the files and says what to do: commit or stash those changes and apply again, or `git merge ling/<id>`.

### 3.5 `discard`

A snapshot commit of the session's last state is kept under `refs/mightling/snapshots/<id>`, the namespace the desktop app uses for its own. From inside the running session, the worktree is reset to its base (its folder is the session's working folder and must stay) and the start after the session removes it; from a shell, `ling worktree discard <id>` removes the worktree, its branch and its folder, and refuses while the session that owns it still runs.

### 3.6 Where edits land, always visible

- **The footer.** Codex's status line already shows `current-dir` by default. For a TUI session whose user has chosen no status line (`[tui] status_line` absent, no `-c tui.status_line`), the launcher sets Codex's default plus `git-branch`: `model · ~/src/app · main` in place, `model · ~/.mightling/session-worktrees/20261022-1430-1a2b/app · ling/20261022-1430-1a2b` in a worktree. A user's own status line is never overridden. No patch: the item is Codex's.
- **The start notice** (the launcher's `notice`, shown inside the TUI): in worktree mode, the folder, the branch, the source and how to apply or discard.
- **`/worktree status`** at any time. It reports where the session *started*; if Codex's picker has moved the conversation since, the footer's folder is the authority, and `status` says so.

### 3.7 The desktop app

Work keeps its Local/Worktree toggle (DESKTOP_PARITY §2.6), Local by default. The toggle's starting position now follows the shared key (`defaultCheckout` in `desktop/ui/src/worktrees.ts`), read through the existing `config/read`; no new IPC request, so `policy.json` is unchanged. Hand off back to the local checkout remains the app's way to bring a worktree thread's work home.

### 3.8 Night Shift

Night Shift keeps its worktrees: an unattended queue must not edit the user's checkout while they work, and best-of-N needs one worktree per attempt. What changes is that the result says where it is and how to bring it back:

- **`ling night apply <id>`** (and `/night apply <id>` in a session): the task's branch, from its base, applied to the checkout it is asked from as uncommitted changes, all or nothing, with the same refusal as §3.4. The task records when and where it was applied, and a second apply says so.
- **The report:** each task with a branch says `Branch: night/<id>, not merged; ling night apply <id> brings it into your checkout`, and the Review section says nothing was merged and how to apply.
- **The TUI's start line:** `Night Shift: 2 done — /night report; /night apply <id> brings a branch's changes here`.
- **The desktop app's inbox:** an opened finished task names its branch and the `ling night apply` command.

## 4. Not done, and why

- **Moving a running TUI session into a worktree from `/worktree on`.** It would need a patch to send Codex's `StartManagedWorktree` event, and the patch budget has no room; Codex's own `/worktree` picker does it today (into a detached managed worktree), and the footer shows where the session went.
- **Worktree mode for `ling exec`.** Scripts and unattended callers expect `exec` in place; `ling exec --worktree` is Codex's opt-in.
- **Copying `.worktreeinclude` files and `AGENTS.override.md`** into a terminal session's worktree, as the app does for its own: not needed for the decision; a later addition if users ask.
- **A Worktree toggle in `ling web`.** Its page has no main process to run git; Work there stays in place.

## 5. Tests

- `ling-rs/src/worktree.rs`: the setting kept in `[desktop]` with the rest of the file intact; a local session left alone; a worktree made on its own branch, its sub-folder found, `status`, `apply` (new files included), a second `apply` with nothing new, a conflicting `apply` refused whole, `discard` with a snapshot; clean finished sessions pruned and changed ones kept; `prepare` moving a new session and rewriting `-C`, and leaving `--worktree` and `exec` alone; every spelling of `-C`; the status line added only when the user chose none; the bare `/worktree` left to Codex.
- `ling-rs/src/night.rs`: `/night apply` refuses a queued task, applies a finished branch as uncommitted changes, and refuses a second apply over itself, saying it was applied before.
- `tests/test_night_shift_compaction_audit.py`: the report's apply lines.
- `desktop/ui/src/worktrees.test.ts`: the toggle's default.
