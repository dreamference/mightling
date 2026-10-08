# Mightling Refine Mode — study first, then do

**Status:** Built on 2026-10-07 for `ling exec`, the interactive session and Night Shift, and **off by default** (§3). No Codex patch: 0 bytes against the patch budget (§5.5). Measured only on SWE-bench, by the benchmark's own `--refine` arm (§2); the product's three surfaces are tested with stand-ins for `ling` and the model, and §8 says what was and was not run against the real binary.
**Goal:** every new task Mightling is given is first studied by a session that changes nothing and writes a refined description of it, then done by a fresh session that has the task and that description. The person sees only the result.
**Builds on:**
- SWE-bench's `--refine` arm ([MIGHTLING_SWE_BENCH §14](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md)), which measured the idea and whose study and fix texts the product now shares (§5.1);
- the launcher (`ling-rs/src/lib.rs`), which already rewrites the command line before Codex parses it, and its hooks in `config.toml` with their trust entries (`compaction.rs`, the ledger and the notice);
- Night Shift's runner ([MIGHTLING_NIGHT_SHIFT](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md)), which owns each task's worktree and session.

**Three things to know before reading further:**
1. **It is built and switched off.** It becomes the default only if the 100-task SWE-bench pair confirms the 24-task result; switching it on is one line on each side (`DEFAULT` in `ling-rs/src/refine.rs`, `DEFAULT_MIGHTLING_REFINE` in `dreamference/config/dreamference_config.py`; a test keeps them equal).
2. **It costs time.** On SWE-bench an instance took a median of 16 min 22 s with it against 4 min 7 s without. The study step has no time limit of its own in the product (the user's decision); the benchmark's has one (15 minutes), which matters for what the 100-task pair measures (§6).
3. **A wrong description is followed.** In three of the 24 instances the study step wrote down the wrong thing, and the fix followed it although it was told the task is authoritative (§2).

---

## 1. What it does

A task goes through two sessions instead of one:

1. **Study.** A session that may read the code, run it and run the tests, but changes nothing, answers with a description of the task in six sections: the intent (from the use case, not only the example), the requirements as observable results, every code path that produces the behaviour (found by following callers and references), edge cases, what must not change, and acceptance checks with their expected output. It is a session of its own, never resumed and never shown as the person's.
2. **Do.** A fresh session gets the task verbatim, then three rules (the task is authoritative where the description disagrees; fix the cause, not the symptom; run every acceptance check before stopping) and the description, marked as possibly incomplete or wrong.

Both run automatically, one after the other, with no stop between them (the user's decision, 2026-10-07): there is nothing to approve and nothing to read in between.

## 2. Why: what was measured

From the benchmark's `im-refine` round (2026-10-07, the 24-instance sample, code index on, prompt `default`, masking off; [MIGHTLING_SWE_BENCH §14](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) has the detail):

| | Resolved | Agent time | Median per instance | Tokens in / out |
|---|---|---|---|---|
| `im-refine` (study, then fix) | **20 of 24** | 6 h 15 min | 16 min 22 s | 52.0 M / 443 K |
| `im-index-on` (same configuration, one session) | 17 of 24 | 3 h 55 min | 4 min 7 s | 46.7 M / 290 K |
| the two index-off rounds | 16 and 17 of 24 | | | |

- It resolved every instance the one-session arm did, plus three (django 13512, 15957, 16454). McNemar exact p = 0.25 against the same configuration without it (0.125 and 0.375 against the index-off rounds), so **one 24-instance pair does not settle it.** Twenty is the highest of the twelve rounds run on this sample (14 to 17 for the eleven others), and two identical arms have differed by up to four instances here, which is this benchmark's noise floor.
- **The study step changed the tree in 0 of 24** instances (told not to; measured), and named every file the reference patch changes in 22 of 24.
- **Why it was tried.** In the one-session round the agent edited a file the reference patch edits in 6 of its 7 failures: what failed was the change (the example fixed instead of the use case, direct parents where grandparents also apply, a guard instead of the rule, a second code path never looked at). The six sections answer those one by one.
- **Where it misled.** In django 16502 the description placed the fix in the wrong handler and the fix followed it; in scikit-learn 25747 it wrote the symptom guard down as a requirement; in sympy 13798 it answered an open design question the other way from the reference. The "the task is authoritative" rule did not prevent any of them.
- **Cost.** The study step took 3 h 35 min of the 6 h 15 min (median 7 min 33 s); 5 of 24 study steps hit the benchmark's 15-minute limit (4 of them had written their description by then), and their tokens are not in the count, so the token cost is understated.

## 3. The decision, and the switch

Decided by the user on 2026-10-07:

- Refine mode applies **everywhere**: the interactive `ling` session, `ling exec`, and Night Shift.
- Interactively it runs **automatically, with no stop**: the study, then the fresh session, as in the benchmark.
- **No time limit** on the study step.
- **Built now, off by default.** It becomes the default only if the 100-task benchmark pair confirms it.

The switch is `DEFAULT: bool = false` in `ling-rs/src/refine.rs` and `DEFAULT_MIGHTLING_REFINE: Final[bool] = False` in `dreamference_config.py`; `tests/test_refine.py` fails if the two differ, and a launcher test fails if the launcher's is changed without its comment being read (`off_unless_switched_on_and_the_variable_wins`, which asserts the current value and must be edited with it).

What the 100-task pair should show before the switch (a proposal, not decided): more instances resolved with refine than without, by more than the floor two identical arms show on the same sample, and a McNemar exact p below 0.05; plus the time cost per instance, so the default is a known trade. The two arms should differ only by `--refine`.

## 4. The setting

| Tier | Form | Notes |
|---|---|---|
| 1. Command line | `ling --refine …`, `ling --no-refine …` (also after `exec`) | The last one before a `--` wins. Codex does not know the flags: the launcher turns them into tier 2 first thing in `main()` (`home::use_mightling_home` calls `refine::export_flag`, before any thread exists, where setting a variable is sound) and takes them off the command line. So every process below this one, the study step and Codex's hooks included, reads one setting. |
| 2. Environment | `DREAMFERENCE_MIGHTLING_REFINE=on\|off` (`1`/`true`/`yes` and their opposites) | An empty value is unset. |
| 3. Config file | `mightling_refine = true\|false` in `dreamference.toml` (`DREAMFERENCE_CONFIG_PATH`, then `./dreamference.toml`, then `~/.config/dreamference/config.toml`) | The launcher's usual resolution (`config_file()`); `DreamferenceConfig.mightling_refine` in Python, saved only when it differs from the default. |
| 4. Default | off | §3. |
| Night Shift only | `[night] refine = true\|false` | Absent: tiers 2 to 4, read by the runner. |

`ling refine` prints the setting in force and where it came from. `--refine` is not in `ling --help`: the help text is Codex's clap tree, and adding an option to it would need a patch.

## 5. Design, and what is built

### 5.1 One text

The study and fix texts are in `ling-rs/prompts/refine.md`, one piece per `<!-- name -->` marker: the study's opening, the six sections, the code-index sentence, the product's study template, the fix rules, the description's heading, the no-description line, and the product's fix block. `{subject}` is "task" in the product and "issue" in the benchmark. The launcher composes from the file (`include_str!`); `dreamference/night_shift/refine_prompt.py` carries the same pieces for Night Shift and SWE-bench (a release install has no `ling-rs/` beside the Python package), and `tests/test_refine.py` compares the two byte for byte.

SWE-bench's `REFINE_PROMPT` and `FIX_PROMPT` are now built from those pieces, and are **byte for byte the prompts the `im-refine` round was measured with**: the test pins the SHA-256 of each, with and without the code index and with an empty description, as computed from `bench/refine` at `360ad99` before the refactor. A change to a shared piece therefore fails that test, which is the point: it would change what the 100-task pair measures.

The product's study prompt differs from the benchmark's in its framing only: it says "task", it does not name `/testbed`, it asks for the description as the session's final message rather than in a file (so the step needs to write nothing, and can run read-only), and it says what writing does in that surface.

### 5.2 `ling exec`: two processes, run by the launcher

`refine::around_exec`, the launcher's last step before Codex parses the command line:

1. **Only a new task.** `exec` with a prompt (or with the prompt on stdin). `exec resume`, `exec fork` and `exec review` continue or review work and pass through.
2. **The task text** is what Codex would have read: the prompt, stdin for `-` or no prompt, and a piped stdin appended as Codex appends it (`<stdin>…</stdin>`). The launcher reads stdin itself, so both steps get the same text.
3. **The study step** is a child `ling exec` started from the user's own command line (before the launcher added anything, since the child's launcher adds it again), with `MIGHTLING_REFINE_STEP=study` and refine mode off. It keeps the user's options and drops those it sets itself: `--ephemeral` (it is never resumed, and stays out of `resume --last`), `-s read-only` (unless the user chose Full Access, which it keeps: there may be no sandbox to run on that machine), `-o <temporary file>` for its final message, no `--json` and no `--output-schema`, and the task on stdin (`-`), which keeps a long task off the argument list. The child's launcher sees `MIGHTLING_REFINE_STEP=study` and composes the study prompt itself, because only a launch knows whether its session has the code index's tools (with them, the prompt names `code_callers` and `code_refs`, as the benchmark's does).
4. **Output.** The child's stdout goes to the parent's stderr, so stdout carries the doing step's output alone, as without refine mode: `ling exec --json … | consumer` sees one `thread.started`, the doing session's, and `-o` gets the doing step's last message. The launcher prints three lines on stderr: that the study starts and why (the tier), how long it took and how long the description is, and that the doing session starts.
5. **Measured, not assumed.** A digest of the git tree (status with untracked files, both diffs) is taken before and after the study; a change is reported on stderr and kept (the tree is the person's: nothing is reverted). Under the read-only sandbox it cannot happen; under Full Access it is the only guard.
6. **The doing step** is Codex itself, in this process: the launcher replaces the prompt with the task, then the rules and the description (`fix_prompt`). A study step that wrote nothing, or could not start, leaves the task as it was, with a line saying so: the doing step then runs as it would have without refine mode.

### 5.3 Night Shift: two sessions, run by the runner

`NightShiftTaskRun._refine`, before a new task's first `ling exec`:

1. **The study step** is a `ling exec` in the task's own worktree with the product's study prompt, `workspace-write` like every night session, and its final message to `logs/<id>.refined.txt` (`refined.txt` in the task's own home for a task from another node). It is not the task's session: its thread id is recorded as `study_session`, never as `session`, so a later nudge or a resume the next night continues the doing step.
2. **The worktree is put back** (`git reset --hard`, `git clean -fd`): it is the runner's own, at the task's base commit, so nothing in it is the person's, and the study step can be told plainly that every change is discarded and scratch files go in `/tmp`. Whether it changed anything is recorded (`study_edited`). A study step cut off by the window leaves its changes behind, but the next night studies the task again from the start and puts the tree back after that study (point 5), so nothing a study step wrote ever reaches the doing step.
3. **The doing step** gets Night Shift's usual prompt, then the rules and the description, and **a fresh `task_timeout`** after the study (within the window), as the benchmark's fix step gets the full task budget: the study has no limit of its own beyond the task's.
4. **The record.** The task's result carries `refine`: `study_exit`, `study_s`, `study_session`, `refined_chars`, `study_edited`. The night report adds a line: `- Refine: studied first for 7 min 33 s, a 4,120-character description`.
5. **Cut off by the window during the study:** the task is `interrupted` with no session, so the next night studies it again from the start. A task resumed with a session is never studied again.

Every `ling exec` the runner starts, the study's included, gets `DREAMFERENCE_MIGHTLING_REFINE=off`: the runner decides, and the launcher must not turn one session into two. The setting is `[night] refine`, then the configured one (§4).

### 5.4 SWE-bench: unchanged, and kept out of the product's setting

The benchmark's `--refine` arm is as measured (`swe_bench_instance_run.py`, its prompts pinned, §5.1). The runner now passes `DREAMFERENCE_MIGHTLING_REFINE=off` into every container, in both arms: with the product's setting on, the container's launcher would otherwise add a study step of its own to every `ling exec`, and the arm without `--refine` would silently become a refine arm.

### 5.5 The interactive session: a hook, no patch

**What was considered.**

| Way | Why not, or why |
|---|---|
| The launcher, as for `exec` | It runs once, before Codex parses its arguments; a TUI session's tasks arrive later. |
| Orchestrating at the app server | The TUI talks to an app server inside the same process; sitting between them, or making the TUI open a second thread per task, is a change to Codex's TUI or core, well over the ~1.5 KB a one-line hook may take. |
| A patch that runs a study thread and opens a fresh thread per task | The cleanest form of "a fresh thread for each task", and the most code: thread creation, history, the status line and interrupt handling in the TUI. Not a one-line hook. |
| **A `UserPromptSubmit` hook (chosen)** | Codex runs it before each prompt is sent, waits for it, and adds what it returns as `additionalContext` to the model's input for that turn. No patch; registered the way the ledger and the notice are. |

**How it works.** At every interactive launch (`ling`, `ling resume`, `ling fork`) the launcher registers `<this binary> refine hook` as a `[[hooks.UserPromptSubmit]]` group in `config.toml` when refine mode is on, with its trust entry (`hooks.state."<config>:user_prompt_submit:<n>:0".trusted_hash`), and removes both when it is off. `ling exec` launches leave the registration alone, so an `exec` run with the mode off cannot take the hook from a session started with it on. Before each prompt, the hook:

1. **Decides whether the prompt is a new task.** Only the first prompt of an interactive thread is: the transcript Codex names (`transcript_path`) has `session_meta.source = "cli"` and no user message yet. Both were read from the source and the session files: a turn runs the hooks on its starting input before recording it (`run_hooks_and_record_inputs` with `PersistContext::TurnStart` in `core/src/session/turn.rs`), asking for the transcript path materialises a new thread's file, and interactive sessions on this machine record `"source":"cli"` (exec ones `"exec"`). A later prompt is conversation and passes through; so does a subagent's (`agent_id`), any `exec` session's (`source = "exec"`: `exec` refines in the launcher), and the study step's own (`MIGHTLING_REFINE_STEP`). A new thread in the same TUI (`/new`) is a new task.
2. **Runs the study step** exactly as `exec` does (§5.2), with the session's working directory, read-only, ephemeral, `--skip-git-repo-check`, its output and log in `$CODEX_HOME/refine/<session>.{md,log}` (kept 14 days). The hook's own stdout is Codex's channel for the answer, so the child writes nothing to it.
3. **Answers** with the rules and the description as `additionalContext`, and a `systemMessage` the TUI shows above the answer: `Refine: studied the task first (7 min 33 s); its 4,120-character description is in this session's context. Log: …`. No description: no context, and a line saying so; the turn goes on as without refine mode.

The thread that does the task is therefore fresh in the benchmark's sense: at the first prompt it holds nothing but the system prompt and its environment, and it receives the task and the description. What differs from the benchmark is the form: the description arrives as context beside the person's message, not inside one composed prompt.

**Registration details that matter.**
- **No time limit:** the hook's `timeout` is 2,592,000 s (30 days), Codex's form of none. Esc ends the turn, and Codex kills the hook's process group, the study step with it.
- **`additionalContextLimit = 0`:** Codex would otherwise move context over 2,500 tokens to a file and give the model a pointer; a description is often longer.
- **`statusMessage`:** "Refine: studying the task in a separate read-only session first", shown while it runs.
- **The trust hash** is Codex's (SHA-256 over the canonical JSON of the TOML form of the hook's identity). The launcher computed it for `SessionStart` only; it now covers any event, the status message and the context limit, leaving out what Codex leaves out (no matcher for `UserPromptSubmit`, a limit equal to the default). A launcher test builds the same identity from Codex's own types (`codex_config::HookHandlerConfig`, `MatcherGroup`, `version_for_toml`) and compares, for the refine hook, the ledger and the notice.
- **`hooks.json`:** a person who keeps hooks there gets none from the launcher (Codex warns about two representations in one folder), and with refine mode on the session says once that it cannot be refined.

**Patch bytes: 0.** The budget in `test_the_patches_stay_small` is untouched.

## 6. Limits and open questions

- **The benchmark's study step had a 15-minute limit; the product's has none.** Since 2026-10-07 (the user's decision of no cap) `REFINE_TIMEOUT_S` is `None`: the study is bounded only by the task's 45-minute limit, and the fix step still gets a fresh one. Before that: 5 of 24 study steps hit that limit (4 had written their description). The 100-task pair, as run today, measures the capped form. If the product's form is what is to be decided, the arm should run with `REFINE_TIMEOUT_S` raised; that is the user's call, and nothing here changes the benchmark.
- **The study step runs read-only in `exec` and the TUI.** A test suite that must write (caches, build directories, `/tmp` if the sandbox does not allow it) fails there; the prompt says that is expected. The benchmark's and Night Shift's study steps may write, because their trees are put back. Whether the read-only form studies as well is not measured.
- **Read-only also means no network** for the study step, whatever the `/airgapped` level.
- **Every first prompt is studied, a question included.** "What does this repository do?" as the first message costs a study step. The hook cannot tell a question from a task; `ling --no-refine` is the way out for such a session.
- **Full Access:** the study step keeps it and so has no sandbox; the tree digest is the only guard, and it reports rather than reverts.
- **The desktop app** (`ling-app`'s Work window, through `ling app-server`) is not covered: its threads' source is not `cli`. Covering it is one more source in `first_interactive_prompt`, once the app's turns have been checked against the hook.
- **The TUI's display of a long-running `UserPromptSubmit` hook** (status line, interrupt) is Codex's and was not exercised here (§8).
- **Cost.** About two and a half times the agent time per task on SWE-bench; on a GB10 that also holds the model for longer, which delays Night Shift's next task.

## 7. Files

| What | Where |
|---|---|
| The texts | `ling-rs/prompts/refine.md`; `dreamference/night_shift/refine_prompt.py` (`RefinePrompt`, the same pieces) |
| The launcher: setting, flags, `exec`, the hook, `ling refine` | `ling-rs/src/refine.rs`; one call each in `lib.rs` (`without_flags`, the `refine` subcommand, `register_hook`, `around_exec`) and `home.rs` (`export_flag`) |
| Hooks for any event | `ling-rs/src/compaction.rs` (`SessionHook` with `event`, `status_message`, `context_limit`) |
| The Python setting | `DreamferenceConfig.mightling_refine`, `DEFAULT_MIGHTLING_REFINE` |
| Night Shift | `night_shift_task_run.py` (`_refine`, `REFINE_ENV`), `night_shift_settings.py` (`[night] refine`), `night_shift_runner.py` (the window's end), `night_shift_report.py` (the line) |
| SWE-bench | `swe_bench_instance_run.py` (prompts from the shared pieces), `swe_bench_runner.py` (`DREAMFERENCE_MIGHTLING_REFINE=off`) |
| Tests | `tests/test_refine.py`, `tests/test_night_shift.py`, `tests/test_swe_bench.py`; `ling-rs/src/refine.rs` (launcher) |

## 8. Tested, run, and not run

**Launcher tests** (`cargo test --release -p ling-launcher` in a scratch export of `rust-v0.158.0` with this branch's crate, never in `codex/`): 171 passed, 1 ignored, among them 13 for refine mode: the tiers and the default; the flags read before a `--` and taken off the command line; `exec`'s prompt found after options that take values, and `exec resume`/`review` and the TUI left alone; the study step's command line (read-only, ephemeral, its own `-o`, the user's `-o`/`--json`/`-s` dropped, joined forms too, Full Access kept with no sandbox added); the prompts composed from the pieces, a task's own `{braces}` left as written; the transcript rule (first prompt of a `cli` thread only, both forms of a recorded user message); the hook's plan and answer; the registration under `UserPromptSubmit` beside the ledger, and removal of it alone; and **the trust hash compared with the one built from Codex's own types**, for the refine hook, the ledger and the notice. The ledger's hash pinned from a live session in 2026-10-02 still holds, so the generalisation changed nothing for `SessionStart`.

**Python tests** (`tests/test_refine.py`, and refine cases in `tests/test_night_shift.py` and `tests/test_swe_bench.py`): the default on both sides; the tiers and saving; the launcher's texts equal to Python's; the benchmark's six prompt variants equal to the measured ones by SHA-256; the product's prompts; a night task studied first in its own session, its scratch file put back, only the task's change committed, the study's session never the task's, the report line, `DREAMFERENCE_MIGHTLING_REFINE=off` on every `ling exec`; `[night] refine` over the configured setting; a resumed task not studied again; `DREAMFERENCE_MIGHTLING_REFINE=off` in every SWE-bench container in both arms with the host's setting on. The whole suite (without `test_mightling_slash_commands.py`): 780 passed, 10 skipped.

**One smoke run against the live model (2026-10-07),** with a binary built from a scratch export of this branch, a throwaway `HOME` and `CODEX_HOME`, the model server named in `DREAMFERENCE_VLLM_HOST`, and a one-file directory (`calc.py`, `add` returning `a - b`); no benchmark was running:
- **Registration.** `ling --refine` in a pseudo-terminal (stopped after 20 s) wrote the `[[hooks.UserPromptSubmit]]` group (`timeout = 2592000`, `statusMessage`, `additionalContextLimit = 0`) and its trust entry, and printed the `refine: on` start-up line.
- **`ling --refine exec --skip-git-repo-check -o out.txt "Fix add() …"`:** 70 s in all. stderr had the three `refine:` lines (the study took 35 s and wrote 2,569 characters); stdout and `out.txt` held the doing step's answer only; one rollout was written (`source: exec`; the study's was ephemeral), whose prompt is the task followed by the rules and the description; the temporary file was gone. **Both sessions printed `hook: UserPromptSubmit Completed`**: Codex ran the registered hook, so it accepted the launcher's trust hash (an untrusted hook is skipped silently), and the hook let both `exec` prompts through. The doing step could not edit `calc.py` because `ling exec`'s own default sandbox is read-only and the run did not ask for `-s workspace-write`; that is `exec`'s behaviour with or without refine mode, and the step said so and gave the fix.
- **`ling refine hook`** given the input Codex sends for a fresh interactive thread (a transcript with `source: cli` and no prompt): 43 s; it ran the study (42 s, 3,364 characters, read-only: `calc.py` unchanged), wrote its log and description under `$CODEX_HOME/refine/`, and answered with the `systemMessage` and the rules and description as `additionalContext`.

**Not run:** a whole interactive session in the TUI with the hook firing on a typed first prompt (Codex's display of a long `UserPromptSubmit` hook, Esc during it); Night Shift and SWE-bench with the real binary; anything measured in the product.

## 9. Not built

- The desktop app's Work window (§6).
- A way to see the study step's description from the TUI other than its log file and the context it adds.
- Refining a later prompt of a thread on request (for example a `/refine` slash command): it would need a patch, and was not asked for.

---

## Sources

- [MIGHTLING_SWE_BENCH §14](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md): the `--refine` arm, its measurement and its cost.
- Codex `rust-v0.158.0`: `codex-rs/hooks/src/events/user_prompt_submit.rs` (input, `additionalContext`), `hooks/src/engine/command_runner.rs` (the hook's process group, its timeout), `hooks/src/engine/discovery.rs` (the trust hash, `additionalContextLimit`, no matcher for `UserPromptSubmit`), `core/src/hook_runtime.rs` (the hook runs before the prompt is recorded; `hook_transcript_path` materialises the thread), `exec/src/cli.rs` and `exec/src/lib.rs` (`exec`'s options and how it reads a prompt from stdin), `protocol/src/protocol.rs` (`SessionSource`).
