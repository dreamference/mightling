# Mightling Prompts — `/prompt`

**Status:** Phase 1 implemented on 2026-10-03 (§13 records what was built and where it departs from the design); Phase 0 is built in part (§6.1 item 1, the writable `/testbed`, fixed in the runner on 2026-10-03), Phase 2 is not built, and the runs of §6.2 have not been made. §1 is read from the pinned Codex source and checked against the requests `ling` actually sends (a stub endpoint recorded them; no model was involved); §6.4 is a small pilot against the live model on 2026-10-02. §12 separates what was checked from what is assumed.
**Goal:** `ling` can run under more than one system prompt, chosen by name. Two ship: `default`, today's prompt byte for byte, and `high-swe`, a prompt written to resolve as many SWE-bench tasks as the local model can. `/prompt` shows and switches them.
**Builds on:**
- the launcher, which already composes the system prompt and writes it to the model catalog (`ling-rs/src/lib.rs`, `base_instructions()` and `model_catalog()`);
- the configuration chain `/cavemode` and `/airgapped` use (a `DREAMFERENCE_*` variable, then the TOML file, then the built-in default), and their slash-command pattern ([MIGHTLING_CAVE_MODE](./DREAMFERENCE_MIGHTLING_CAVE_MODE.md), [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md));
- `ling-admin swe-bench` ([MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md)), which is how a prompt is measured, and its first runs, which are where `high-swe` comes from.

**Three things to know before reading further:**
1. **Codex fixes the system prompt when a session starts.** A resumed session keeps the prompt it started with. So Phase 1 chooses the prompt for *new* sessions and needs no Codex patch; switching the prompt of the session you are in (Phase 2) needs one hook in Codex's core, and costs a full re-read of the conversation.
2. **`high-swe` replaces the prompt, it does not add to it.** Most of its expected gain comes from what it leaves out: 65% of today's prompt is about voice, formatting and front-end design.
3. **Nothing here shows that `high-swe` scores higher.** It is designed from eleven analysed failures and from what the published scaffolds share; the pilot (§6.4) only shows that the model follows it. Two identical benchmark arms already differ by four instances in 24, so the claim needs the runs of §6, after the harness defects of §6.1 are fixed.

---

## 1. What shapes the design

### 1.1 What the model is sent today

Captured from `ling exec` against a stub endpoint (first request of a new session, this machine, 2026-10-02):

| Part | Size | Where it comes from |
|---|---|---|
| `instructions` (the system prompt) | 24,289 chars | the launcher: Codex's longest bundled template with "Codex" renamed, then `# Web access`, `# Email access` (an account is connected), `# Code navigation` (`ling-code` is installed) |
| tool schemas | 30,399 chars | Codex: `exec`, `wait`, `exec_command`, `write_stdin`, `request_user_input`, `view_image`, `multi_agent_v1` (13,382 alone), three goal tools, `web_search` |
| one developer message | 7,749 chars | Codex and the launcher: the skills list, the permissions text, the cave-mode rules |

These three are what a fresh request carries; the server counted 11,210 prompt tokens for one on 2026-10-01. `/prompt` governs the first row only (§5.5 comes back to the second).

### 1.2 Codex pins the prompt to the session

`codex-rs/core/src/session/mod.rs` resolves the prompt once, when a session is created, in this order: a configured override (`model_instructions_file`), then the prompt recorded in the session being resumed or forked, then the model's template from the catalog. The launcher uses the third.

Checked against the stub (the `instructions` field of each request):

| Launch | What was sent |
|---|---|
| a new session, catalog as the launcher writes it | the 24,289-char prompt |
| a new session with `-c model_catalog_json="<another catalog>"` | that catalog's prompt |
| `exec resume` of that session, with the ordinary catalog back | still the other catalog's prompt: **the session kept its own** |
| the same resume with `-c model_instructions_file="<file>"` | the file: an override beats the recorded prompt |
| a new session with `-c model_instructions_file="<file>"` | the file and nothing else: no web, email or code-navigation block |

So "which prompt" is a property of a session, decided at its first request, and Codex has no operation that changes it afterwards. One function renders the prompt for every request, compaction included: `Session::get_prompt_base_instructions()`, whose comment reads "Render the request copy without changing instructions persisted or inherited by forks". That is where Phase 2 hooks (§4.3).

### 1.3 The default prompt was written for another model and another job

By section, of 24,289 chars (a 151-char opening sentence is the remainder):

| Section | Chars | Use on a repository task |
|---|---|---|
| Personality ("vivid inner life … playful, curious") | 1,725 | none |
| Frontend guidance and design instructions | 7,109 | none |
| Working with the user, formatting, final answer, intermediary updates | 7,031 | little: it shapes messages nobody reads in an unattended run |
| General, engineering judgment, editing constraints, special requests, autonomy | 5,432 | yes |
| Web, email, code navigation (the launcher's blocks) | 2,841 | see below |

- **It names tools this model does not have.** "Use `multi_tool_use.parallel`", "the `commentary` channel", "the `final` channel": none is in the tool list of §1.1. "Use `apply_patch` for manual code edits" is half true: there is no `apply_patch` tool, only a shell command of that name on the PATH Codex gives each command.
- **It says nothing about how to resolve a bug**: no word on reproducing, on finding the code that owns a behaviour, on running the tests that already exist, or on what belongs in the final diff.
- **In the benchmark container the web block is false.** It says "Do not say you cannot browse the web"; the task says "There is no network". Commands naming `ling-search` or `ling-fetch` appear 20 times in 7 of the 24 baseline instances: the model tried, and looked for the programs when they failed.
- **The code-navigation block was never acted on**: 0 queries in 2,824 commands (SWE-bench spec §13). That is being worked on separately; this spec takes the block as it ships.

### 1.4 What the first benchmark runs showed

Run `acc-25` (24 validated instances, default prompt, cave mode `ultra`, 2026-10-01): 13 resolved. The 11 unresolved, by what the grading reports and by comparing each patch with the reference:

| Class | Instances | What happened |
|---|---|---|
| Fixed in the wrong place | django-15957, django-16502, sympy-13031, sympy-13877 | The patch edits a file the reference fix does not touch (`query.py` for `related_descriptors.py`, `wsgi.py` for `basehttp.py`, `common.py` for `sparse.py`, `exprtools.py` for `matrices.py`); every target test still fails |
| Incomplete | django-13512, django-15563, sympy-17318 | The reference fix changes two files, the patch one of them (13512, 17318), or one of two target tests passes (15563) |
| Broke something that worked | django-12774, django-16454 | The target tests pass; one or two tests that passed before now fail |
| Invented behaviour the issue did not ask for | sympy-13798, scikit-learn-25747 | Right file, different behaviour: the separator padded with spaces where the test expects it verbatim; a length condition added where the reference removes the override |

Three more things in the same trajectories:

- **Edits go through Python.** 23 of 24 instances rewrote files with `python - <<…` scripts; `apply_patch` was tried in one and failed (next point).
- **Junk in the patch.** Stray files (`probe.txt`, two `.write_test`), a release-notes edit, and edited test files or fixtures in 6 of the 11 failures.
- **Outcomes are noisy.** The second run (`acc-25-index`) differed only by an unused tool and its 890-char prompt block. It also resolved 13, but not the same 13: two instances flipped each way.

### 1.5 What is published

- **The strong scaffolds use short prompts and one workflow.** mini-swe-agent (above 74% on SWE-bench Verified with frontier models) has a one-line system prompt and puts a five-step workflow in the task: read the relevant code, write a script that reproduces the issue, fix, re-run the script, test edge cases. Anthropic's SWE-bench prompt for Claude 3.5 Sonnet is the same five steps, with "make the minimal changes to non-tests files". mini-swe-agent adds "in a way that is general and consistent with the codebase" and forbids changing tests and configuration.
- **A little more tool use and testing helps a little.** Anthropic's Sonnet 4.6 system card reports 79.6% → 80.2% from adding "use tools as much as possible … implement your own tests first".
- **Long prompts hurt weaker models.** A twelve-variant prompt ablation (SWE-Bench Mobile, GLM 4.6 under Claude Code) found the detailed, checklist and "comprehensive" prompts worst (task success 10% → 4%) and prompts about code quality better than prompts about process.
- **The model matters more than the prompt.** A study of 9,374 trajectories found that agents sharing a model agree more than agents sharing a framework, that gathering context before editing and validating afterwards go with success, and that a framework's prompt matters less the stronger the model. Ours is a 27B model, so the prompt should matter more than it does on a leaderboard, but it will not turn 54% into 74%.

---

## 2. Prompts

A **prompt** has a name, a core text, and the list of launcher blocks appended to it. The blocks are the three the launcher already writes: `web`, `email` (only when an account is connected) and `code` (only when `ling-code` is installed).

| Name | Core | Blocks | For |
|---|---|---|---|
| `default` | Codex's bundled template, renamed (today's `base_instructions()`) | web, email, code | everyday interactive work. **The default.** |
| `high-swe` | Appendix A, 4,229 chars | code | resolving a defined task in a repository: unattended runs, Night Shift, the benchmark |

- **`default` is byte-identical to what `ling` sends today**, and a test holds it there (§10). Choosing nothing changes nothing.
- **`high-swe` leaves web and email out on purpose.** A benchmark container has no network, a block that says "do not say you cannot browse" is false there, and each block is text the model reads on every task for a capability a repository fix rarely needs. A session that needs both the method and the web runs `default`.
- **Custom prompts.** A file `$CODEX_HOME/system-prompts/<name>.md` is a prompt of that name. Its text is the whole core. Its first line may be `<!-- ling: blocks=web,email,code -->` (any subset); without it no block is appended. Names are lowercase letters, digits and hyphens; `default` and `high-swe` cannot be shadowed. This is also how a candidate text is benchmarked without a rebuild (§6).
- **Where prompts cannot come from.** Never from the repository, and never from text the model wrote: `$CODEX_HOME` is outside the workspace-write sandbox's writable folders (only `skills/` was opened, in `77b9471`), and a test keeps `system-prompts/` out of them. A repository's `dreamference.toml` may *name* an installed prompt (§4.1), as it may name a cave level; it cannot supply one.

---

## 3. The command

### 3.1 From a shell (Phase 1, no patch)

| Command | Effect |
|---|---|
| `ling prompt` or `ling prompt list` | Lists the prompts, one line each, marks the one new sessions get and says where that choice comes from |
| `ling prompt show [<name>]` | Prints the composed text exactly as a session would receive it, blocks included, and its size |
| `ling prompt use <name>` | Writes `mightling_prompt = "<name>"` to the configuration file, so new sessions start with it. Says so if `DREAMFERENCE_MIGHTLING_PROMPT` is set and still wins |
| `DREAMFERENCE_MIGHTLING_PROMPT=<name> ling …` | One launch under that prompt: `ling exec`, a script, a single interactive session |

The launcher intercepts `prompt` as it does `night` and `node`, before Codex parses the command line; it never reaches Codex, and it needs no model server. The price is the one those two already pay: a session cannot be started with the single word `prompt` as its message.

### 3.2 In the TUI (Phase 2, patch `0020`)

| Form | Effect |
|---|---|
| `/prompt` | Shows this session's prompt and the list |
| `/prompt <name>` | Switches **this session** at its next model request. Prints the cost first (§4.3): the server re-reads the whole conversation once |
| `/prompt use <name>` | Sets it for new sessions, as `ling prompt use` does, and leaves this session alone. (Not `/prompt default <name>`, cave mode's form: `default` is itself a prompt's name here) |
| anything else | The usage line. Nothing changes |

What `/prompt` prints:

```
Prompt: default (this session started with it)
  default   Codex's own prompt: conversational, web and email      ← in force
  high-swe  repository tasks: reproduce, fix at the root, verify; no web or email
Switch this session: /prompt high-swe (re-reads the conversation once, about 30 s at 50K tokens).
New sessions: /prompt use high-swe.
```

### 3.3 Rules

- **A session keeps its prompt.** `ling resume` and a fork continue with the prompt the session had, whatever the default is now (§1.2 shows Codex doing this by itself). Only `/prompt <name>` typed in that session changes it.
- **The model cannot change it.** No tool or message selects a prompt; only the command and the configuration do.
- **`/prompt` never calls the model** and answers at once, mid-turn included. A switch typed mid-turn takes effect at the next request of that turn.
- **A subagent starts with the prompt its process was launched with**, not with a prompt switched to by `/prompt` in its parent. (Its instructions come from the parent's request text either way.)
- **Only the terminal agent.** The web chat has its own assistant prompt and is not covered.

---

## 4. How it is built

### 4.1 Where the choice comes from

For a new session, the first of:

1. `DREAMFERENCE_MIGHTLING_PROMPT`;
2. `mightling_prompt` in the configuration file the launcher already reads (`DREAMFERENCE_CONFIG_PATH`, `./dreamference.toml`, `~/.config/dreamference/config.toml`);
3. `default`.

An unknown name is skipped with one line on stderr naming it and the tier it came from, and the next tier is used: a typo must not start a session with an empty prompt.

### 4.2 Phase 1: one catalog per prompt, chosen on the command line

The launcher already writes `$CODEX_HOME/model_catalog.json` at every launch and injects `-c model_provider=…` in front of the user's arguments. Phase 1 adds:

- **`ling-rs/src/prompt.rs`**: the built-in cores (`include_str!` of `ling-rs/prompts/high-swe.md`; `default` stays the bundled template), the custom-prompt loader, the tier resolution of §4.1, `compose(name)` (core plus its blocks), and the `ling prompt` subcommand.
- **For `default`: nothing changes.** `model_catalog.json` is written as today, by the same code path.
- **`DreamferenceConfig.mightling_prompt`** on the Python side, with the same tiers, as `mightling_cave_mode` has: `save_config()` writes the flat keys it knows, so a key it does not know would be dropped from the file by `ling-admin main-model set`.
- **For any other prompt:** the launcher writes `$CODEX_HOME/model_catalog.<name>.json`, identical except for `base_instructions`, and injects `-c model_catalog_json="<that file>"`. The setting is per process, so a `high-swe` Night Shift task and a `default` interactive session started in the same second cannot read each other's catalog, which a single shared file would allow.
- **Not `model_instructions_file`.** It works (§1.2), but as an override it also replaces the prompt of every session *resumed* by that launch, which breaks the rule of §3.3. The catalog route gives a new session its prompt and leaves resumed ones alone, with no code of ours deciding it.

That is all Phase 1 needs: `ling exec`, Night Shift and the benchmark can each run under a named prompt.

### 4.3 Phase 2: switching the session you are in

Three pieces, the first two with no dependency beyond the standard library, as `ling-airgapped` is built:

- **`ling-rs/prompt/`** (crate `mightling-prompt`): `for_request(thread_id, current_text) -> Option<String>`. It reads `$CODEX_HOME/prompt/<thread_id>`, a one-line file naming a prompt, and returns the text in `$CODEX_HOME/prompt/texts/<name>.md`. No file, an unknown name or an unreadable text returns `None`: the session keeps its own prompt. The launcher writes `texts/` at every launch, one composed file per installed prompt, since composing needs the Gmail and code-index probes only it makes. The first time it sees a thread it also records which installed prompt `current_text` equals (`<thread_id>.started`), so `/prompt` can name the prompt a resumed session started with. **Switching back to that prompt removes the session's file instead of naming it**, so the session returns to its own recorded text: `texts/<name>.md` is composed afresh at every launch, and a session started yesterday with a mail account connected must not get today's `default` in its place.
- **The hook**, in `Session::get_prompt_base_instructions()`: if `mightling_prompt::for_request(…)` returns a text, use it for this request. One function serves the turn, compaction, the start-up prewarm and the reviewer, so one hook covers them. The prompt recorded in the session file on disk is not rewritten.
- **The command**, in the launcher crate: `prompt::command(thread_id, args)` writes or removes the session's file and returns the lines to print; the TUI hooks are the seven hunks every Mightling slash command has.

**What a live switch costs.** The system prompt is the first thing in every request, so changing it invalidates the server's cached prefix for the whole conversation. The next request re-reads everything: about 26 s per 40K tokens on this machine (measured for the prefix-cache check on 2026-10-01). Switching back within the cache's lifetime is cheap, since the old prefix is still there. `/prompt <name>` prints the estimate before it switches, from the session's last token count.

**What stays imprecise.** Codex's context-size estimate still counts the starting prompt, so after a switch from `default` to `high-swe` it overestimates by the difference between the two (about 19,000 chars) and compacts slightly early. Accepted.

### 4.4 Budget

The patch series is 17 patches and 31,175 bytes under a 31,500-byte cap (at `fa9a7a9`). Phase 1 costs nothing. Phase 2's patch `0020-prompt-command` is estimated at 3.0 KB: the seven TUI hunks cost 2.1 KB for `/night`, and the core hook is two hunks (the manifest line and the call). The cap is raised to 34,500 in the same commit, with the test's comment saying why, as each earlier hook did. If another patch lands first, the numbers move and the rule does not.

---

## 5. The `high-swe` prompt

The text is Appendix A. This section is why it reads as it does.

### 5.1 Rules it was written by

- **Short.** 4,229 chars against 21,448 for the default core. §1.5: long and checklist prompts cost a weaker model more than they give.
- **One method, in order,** the five steps every strong scaffold shares, with a sixth for the final diff.
- **Each rule earns its place from a failure we saw** (§5.2), or from a tool mismatch we measured (§1.3). Nothing is there because prompts usually have it.
- **It names only what exists**: the shell, `apply_patch` as the shell command it is (with its format, which the default prompt never shows), `rg` with its fallback. No channels, no parallel-tool wrapper.
- **It keeps what protects a user's repository**: no destructive git, never revert changes that were there first, do not commit. `high-swe` will be used on real checkouts, not only in containers.
- **It asks for the diagnosis in writing before the first edit.** The model runs with reasoning switched off, so what it does not write it has not thought through; Anthropic's prompt makes the same request ("your thinking should be thorough").

### 5.2 Which failure each rule answers

| Rule in the text | Failure class (§1.4) | Instances it is read against |
|---|---|---|
| Step 2: find where the behaviour lives; the line that raises is often not the fix; look for the same logic elsewhere | wrong place; incomplete | 15957, 16502, 13031, 13877; 13512, 15563, 17318 |
| Step 4: fix every place that implements the behaviour | incomplete | 13512, 15563, 17318 |
| Step 5: run the whole test file or package for the area; a newly failing test means the fix is wrong | broke something | 12774, 16454 |
| Step 1: implement what the task asks, exactly; add no conditions, padding or validation | invented behaviour | 13798, 25747 |
| Steps 3 and 6: scripts in `/tmp`; read `git status` and `git diff`; no edits to tests, fixtures, docs | junk in the patch | 6 of the 11 failures |
| The `apply_patch` block | edits through Python scripts | 23 of 24 instances |
| "Do not investigate the environment" | permission probing | about 100 of 2,288 commands |
| "If two attempts fail, go back to step 2" | long thrashing | 13877 (timed out at 240 commands), 15957 (296) |

A benchmark report under `high-swe` should be read class by class against this table, not only by its total.

### 5.3 What it leaves out, and what that risks

- **Personality, formatting, front-end guidance:** 15,865 chars. Risk: in interactive use the answers lose Codex's tone and its file-link formatting. That is the trade the name announces.
- **Web and email blocks.** Risk: a `high-swe` session cannot look up documentation. §2 says why this is still the default for the prompt, and it is an open question (§11).
- **Review mode, plan requests, "ask when ambiguous".** Risk: `high-swe` always acts. Published work on action bias (FixedBench) found agents change code in 35–65% of tasks that needed no change, and that telling them to reproduce first reduces it. Step 3 is that instruction; the text does not go further and tell the model it may decline, because on SWE-bench every task needs a change.

### 5.4 Cave mode

Cave mode is a separate switch and stays one. But `ultra`, the default, tells the model "Between tool calls, write nothing", and `high-swe` asks for a written diagnosis before the first edit. The cave text arrives later in the context than the system prompt.

- **Phase 1 decides nothing by fiat:** the benchmark runs `high-swe` under `ultra` and under `off` (§6.2), and the pilot ran both (§6.4).
- **If `off` wins by more than the noise floor,** a prompt gains one optional property, a cave level it prefers, which sits *below* the user's own choices in cave mode's chain (session, variable, file, **then the prompt's preference**, then `ultra`). `high-swe` would prefer `off`.

### 5.5 The tool list is a bigger lever than the text, and is not this command's

Disabling what a repository task never uses (`--disable multi_agent --disable goals -c web_search="disabled"`) cuts the tool schemas from 30,399 to 12,653 chars (checked against the stub). With `high-swe` the first request falls from about 62,400 chars to about 25,500. Whether a smaller request resolves more tasks is unmeasured; it is arm D of §6.2. If it does, the place for it is a set of launch options that travel with a prompt, which this spec leaves as an open question rather than smuggling settings into a command named `/prompt`.

---

## 6. Measuring it

### 6.1 Phase 0: defects that would be measured instead of the prompt

Found in the `acc-25` trajectories. They belong to the benchmark runner, and any prompt comparison made before they are fixed is a comparison of how well each prompt copes with them.

1. **The agent cannot write the files it is asked to fix.** In the instance images `/testbed`'s sources are `root:root 0644` and the agent runs as the host's user. Directories are writable, so the model eventually learns to write a new file and rename it over the old one, after `sed -i`, appending, `sudo`, `whoami` and `chmod` have failed. `apply_patch` answers "Failed to write file". The runner opens up `.git` and nothing else. Fix: `chmod -R a+rwX /testbed` in the root-run preparation step (git does not track the write bit, so the patch is unaffected). The pilot ran with this fix. *Fixed in the runner on 2026-10-03 (`a7e49af`): the scrub step opens up all of `/testbed`; checked in a running container (`setup.py` 777).*
2. **`rg` is not in the images**, and both prompts say to use it first. Either mount a static `rg` beside `ling`, or accept one failed command per instance.
3. **Everything the agent leaves behind is submitted**, test files included. The grader overwrites the test files it knows; an edited fixture next to them (`tests/lookup/models.py` in django-12774) stays and can fail tests that passed. Decide whether the runner drops changes under test directories from the prediction, as mini-swe-agent's submission rule does, or leaves that to the prompt. This spec assumes the prompt, and step 6 says so.
4. **A fresh baseline.** `acc-25` was measured before these fixes, on a `ling` build that has since changed. Every arm of §6.2 is run again on one build.

### 6.2 Arms

All on the same validated instances, the same `ling` build and the same model, each **three times**:

| Arm | Prompt | Cave | Tools |
|---|---|---|---|
| A | `default` | `ultra` | all (today's configuration) |
| B | `high-swe` | `ultra` | all |
| C | `high-swe` | `off` | all |
| D | `high-swe` | the better of B and C | repository-task set (§5.5) |

The runner gains `--prompt <name>`, recorded in the run's manifest and printed in the report; it sets `DREAMFERENCE_MIGHTLING_PROMPT` in the container and mounts `$CODEX_HOME/system-prompts/` read-only when the name is a custom one. `report --against` already compares two runs instance by instance.

### 6.3 Reading the result

- **The noise floor is ours, not a textbook's:** two runs of one configuration differed on 4 of 24 instances (§1.4). An arm is said to beat another only if, over the three repetitions, its mean resolved count is higher by more than the largest difference seen between repetitions of the *same* arm.
- **Per class.** For each row of §5.2, how many of its instances changed outcome. A higher total with the regression class unchanged means the gain came from somewhere the prompt did not aim.
- **Behaviour, which is less noisy than outcome:** share of instances where the existing tests for the changed area ran after the last edit; share with a reproduction script; files in the patch outside the source tree; commands per instance; edits made with `apply_patch`.
- **24 instances cannot show a difference of a few points.** Validate more first: 28 are validated today, about 2.2 GB of image each, 332 GiB free with a 100 GB reserve. A hundred instances is within reach and is the size at which three repetitions can resolve a difference of about ten points.
- **What to expect.** A few points, possibly none. §1.5: the model is the main driver. `high-swe` is kept if it is not worse and its behaviour measures are better, because it is also a fifth of the size.

### 6.4 Pilot (2026-10-02)

A check that the model follows `high-swe` and of what it does differently, not a measurement of the score. Arms A, B and C of §6.2, once each, on six instances chosen from §1.4's classes (django-16454 regression, django-13512 incomplete, django-16502 wrong place, sympy-13798 invented behaviour, and two that `acc-25` resolved).

**What was the same as `acc-25`:** the `ling` build (the runtime's source hash, `9d107cf7…`, is `acc-25`'s `runtime_hash`), the model, the images and the task preamble.

**How it differed from `acc-25`, so arm A is not that run again:**
- the Phase 0 permission fix of §6.1 in every arm (`chmod -R a+rwX /testbed` before the agent starts);
- 25 minutes per instance instead of 45, 3 GB per container instead of 8, two at a time;
- arms B and C got the text through `-c model_instructions_file=` (§1.2), so no launcher block was appended. In the container that matches `high-swe` as specified, because neither `ling-code` nor a mail account is there; arm A's prompt in the container is the template plus the web block (about 22,700 chars), not the host's 24,289;
- **the model server was shared**: other tasks kept about six requests running throughout. Arm A on sympy-13798 got through 12 commands in 25 minutes; in `acc-25` the same instance took 45 commands and 196 s. Wall times and time-outs below measure the queue as much as the prompt;
- the agent phase was cut off after 10 of 18 runs by the job's own two-hour limit; the B and C runs of sympy-13798 were interrupted and not graded, and django-11880 and pytest-5631 never ran.

**What came out** (graded with the same harness; a time-out still submits whatever was in the tree):

| Instance | A `default`, ultra | B `high-swe`, ultra | C `high-swe`, off |
|---|---|---|---|
| django-16454 | resolved, 1,053 s | resolved, 598 s | not resolved (time-out; one previously passing test broken) |
| django-13512 | **not resolved**: 1 of 3 target tests; `forms/fields.py` only, as in `acc-25` | **resolved** (time-out): 2 files | **resolved** (time-out): `forms/fields.py` and `contrib/admin/utils.py`, the reference's two files |
| django-16502 | empty (time-out) | empty (time-out) | empty (time-out) |
| sympy-13798 | empty (time-out) | interrupted | interrupted |

**Behaviour, which is what the pilot can speak to** (the six finished-or-timed-out runs of the two Django instances that every arm completed):

| Per run | A | B | C |
|---|---|---|---|
| edits through `apply_patch` (all applied) | 0 | 2–4 | 2–7 |
| edits through Python or `sed -i` | 1–2 | 0–1 | 0 |
| scripts written to `/tmp` | 0–1 | 4–6 | 2–7 |
| `git status` / `git diff` read before stopping | 0 | 1–3 | 0–1 |
| files outside the source tree in the patch | 0–3 (django-16454: 3) | 0 | 0 |
| words written before the last edit | 146–244 | 223–502 | 1,119–1,552 |

**Reading it:**
- **The rules are followed.** `high-swe` arms edit with `apply_patch` and it works (the permission fix is part of why), write their scripts outside the repository, and submit no test or scratch files.
- **The "fix every place" rule did what it was written for, once.** On django-13512, from §1.4's *incomplete* class, both `high-swe` arms went looking for the same logic outside the forms module (arm B's third command was `grep -l JSONField … | grep -i admin`) and changed a second file: arm C the reference's `contrib/admin/utils.py`, arm B `db/models/fields/json.py` instead, and both passed all three target tests. Arm A changed one file, again. One instance, one run each: an example, not a rate.
- **`high-swe` runs are longer.** More scripts, more test runs, more reading of the diff. Under a 25-minute limit on a busy server that cost arm B one time-out the default did not have (and the time-out still resolved). The benchmark's 45 minutes is the right limit for §6.2; a shorter one penalises exactly the verification the prompt asks for.
- **Cave `off` made it worse here.** Arm C wrote four to six times as many words before its last edit and timed out on both Django instances, once leaving a previously passing test broken. That is the opposite of the reasoning in §5.4, on two instances; arm C stays in §6.2.

**Changes for the text that ships (v2), from reading these trajectories:**
- Step 6 says "Delete scratch files"; arm B spent its last turns deleting scripts in `/tmp`, which is never collected. v2: "Remove anything you created inside the repository; scratch files in /tmp can stay."
- Nothing says when verification is enough. v2 adds to step 5: "When your script shows the fix and the area's tests pass, stop: do not keep adding checks."

### 6.5 First benchmark A/B (2026-10-03)

Three arms of §6.2's A and B, one repetition each, run one after the other on the night of 2026-10-03 (18:44 to 23:54), interleaved so drift falls on both `default` arms: `ab-default-a`, `ab-highswe`, `ab-default-b`. Same 24 validated instances (the sample of SWE_BENCH §12.5, `sphinx-8056` excluded), same `ling` build (`runtime_hash` `39b8a92b6775`, the build of `258c3b5`; `high-swe` is v2, compiled in), same `ling-code` (`cd4d6b91…`), code index on (`--code-index universal`) in every arm, cave `ultra`, `task_context` 44,000 (the KV pool was 133,308 tokens), three at a time, 45-minute timeout, 8 GiB per container, two nudges, the writable `/testbed` of §6.1 item 1. No context masking: the build predates patch `0021`. The model server was shared: five times an instance waited for a request that was not the run's.

| | `default` (a) | `default` (b) | `high-swe` |
|---|---|---|---|
| Resolved | **16** (66.7%) | **15** (62.5%) | **16** (66.7%) |
| Timeouts / empty patches | 2 / 0 | 1 / 0 | 0 / 1 |
| Agent time (without timeouts) | 5 h 9 min (3 h 39 min) | 4 h 25 min (3 h 41 min) | 4 h 9 min (4 h 10 min) |
| Median wall per instance | 7 min 43 s | 7 min 29 s | 6 min 54 s |
| Input / output tokens | 49.1 M / 289 K | 64.1 M / 296 K | 62.8 M / 357 K |
| Commands | 2,503 | 2,665 | 2,364 |
| Compactions (instances) | 29 (14) | 26 (11) | 27 (12) |
| `ling-code` calls (instances) | 328 (23) | 415 (24) | 334 (24) |

**The noise floor, measured in the same session:** the two `default` arms differ on 3 of 24 instances (`django-15563` and `sympy-14711` only in a, `django-12774` only in b), McNemar p = 1.0. `high-swe` against a differs on 2 (`django-16454` only `high-swe`, `sympy-13031` only `default`), p = 1.0; against b on 5 (3 to 2 for `high-swe`), p = 1.0. Every difference is inside the floor.

**Reading it by §6.3's rule:** one repetition per arm, so the rule cannot declare a winner. `high-swe` is **not worse** on outcome (16 against 15 and 16). On behaviour it is mixed:
- **Better:** no timeout (the `default` arms had 2 and 1; `django-15563` timed out in a and resolved in 618 s under `high-swe`), the fewest commands, and the shortest median.
- **Worse:** 21–23% more output tokens, about what §6.4 predicted from the extra verification, and the one empty patch of the night: `sympy-13031` stopped at 118 s with nothing written, the same failure the plain arm of SWE_BENCH §13.6 had on that instance. Without timeouts its agent time is the longest.
- **Not distinguishable:** input tokens and compactions sit between the two `default` arms.

**Not measured:** arms C and D, the three repetitions §6.2 asks for, and the per-class reading of §6.3 (the eleven-failure classes of §5.2 have too few members in 24 instances to count). **Context only, not a comparison:** `default` with the index resolved 13 of 24 on 2026-10-01 (SWE_BENCH §12.5) and 16 and 15 here; the binary, the tools, the `/testbed` fix and the timeout handling all changed in between.

---

## 7. Where else a prompt is chosen

- **Night Shift.** `[night] prompt = "<name>"` in the configuration sets `DREAMFERENCE_MIGHTLING_PROMPT` for each task's `ling exec`. It stays unset (so `default`) until §6 says otherwise; an interrupted task resumed the next night keeps the prompt it started with (§3.3).
- **The benchmark.** `--prompt` (§6.2).
- **`/airgapped`.** The level's message to the model arrives as a later message and is independent of the prompt. `default`'s web block already defers to it ("unless a later message says web access is off"); `high-swe` has no web block to contradict.
- **Skills.** The skills list is a developer message, not part of the prompt; both prompts see it.
- **Compaction.** The compaction request goes through the same function as a turn (§1.2), so a session compacts under the prompt it runs under.
- **`ling update` and the release.** The built-in texts are in the binary; nothing new is downloaded or installed.

---

## 8. Alternatives considered

- **A layer on top of the default, as cave mode is** (a World State section carrying `high-swe`'s rules). No patch to core and it switches live for free. Rejected as the mechanism: a layer can add rules but cannot remove 16 KB of prompt, and §5 argues the removal is most of the point. It is also where the cave-mode conflict of §5.4 comes from: two late messages telling the model opposite things about writing.
- **Codex's own `model_instructions_file`, or a `[profiles.high-swe]` table in `config.toml` chosen with `ling -p high-swe`.** No code at all, and it is what the pilot used. Rejected as the product mechanism for two reasons measured in §1.2: it drops the launcher's blocks unless the launcher writes the file anyway, and as an override it replaces the prompt of sessions it resumes.
- **Rewriting the session's recorded prompt** (a new operation in core that changes `session_configuration`). It would make the context estimate exact, but it is a larger patch, and it changes what forks and resumed sessions inherit, which is exactly what Codex's comment on the hooked function says not to do.
- **Codex's `personality` feature** (off in this build) re-renders one templated paragraph of the prompt when the personality changes. It swaps a paragraph, not a prompt.
- **Improving the default for everyone.** The default is Codex's maintained text; a submodule bump updates it for free, and interactive users chose it by using Mightling. `high-swe` earns the default place for unattended runs, if at all, through §6.
- **Putting the method in the task instead of the system prompt**, as mini-swe-agent does. That is right for a benchmark runner and wrong for a product: Night Shift tasks and interactive requests have no fixed task template. The runner's preamble stays as it is so that the arms differ in one thing.

---

## 9. Phases

**Phase 0: make the measurement mean something.** The four items of §6.1, in the benchmark runner. No prompt work depends on them, every prompt *claim* does.

**Phase 1: named prompts, no patch.** `prompt.rs`, the two built-in prompts, custom prompts, the tiers, `ling prompt list|show|use`, the per-prompt catalog, `swe-bench run --prompt`, `[night] prompt`. Then the runs of §6.2. Output: §6.4 extended with real numbers, and either "`high-swe` is the prompt for unattended runs" or a revised text and another round.

**Phase 2: `/prompt` in the TUI and the live switch.** The `mightling-prompt` crate, the core hook, the command, patch `0020` and the cap. Checks before it merges: the hook compiles without a dependency cycle; a switched session's next request carries the new text and its compaction does too (the stub endpoint shows both); `ling resume` of a switched session stays switched; a session with no file is byte-identical to today.

**Phase 3, only if §6 supports it:** a prompt's preferred cave level (§5.4); launch options that travel with a prompt (§5.5).

---

## 10. Tests

- **Launcher (Rust, in the export):**
  - `default` composes to exactly what `base_instructions()` plus the blocks produce today;
  - the shipped `high-swe` is byte-identical to the file the benchmark arm used (cave mode's rule for its level texts);
  - `high-swe` stays under 5,000 chars and names no tool that is not in the tool list (`multi_tool_use`, `commentary`, `final`);
  - tier order, and an unknown name falling through with a message;
  - custom prompts: the blocks line, the name rules, no shadowing of built-ins;
  - `default` adds no argument and writes no extra catalog;
  - another prompt writes its catalog and injects `model_catalog_json`, and a user's own `-c model_catalog_json=` is left alone;
  - `ling prompt list|show|use` output, and `use` keeping the rest of the TOML file as written.
- **Against the stub endpoint (Python, offline):** the five rows of §1.2 as assertions on the `instructions` field, plus Phase 2's rows: switched, switched then compacted, switched then resumed, never switched.
- **`mightling-prompt` crate:** missing file, unknown name, unreadable text and a thread id that is not a file name all return `None`; the `.started` record names the right prompt.
- **Sandbox:** `$CODEX_HOME/system-prompts/` and `$CODEX_HOME/prompt/` are not writable under workspace-write.
- **Not covered:** `tests/test_mightling_slash_commands.py` enumerates unpatched Codex's commands, so `/prompt` is checked in the TUI through tmux, as `/night` was. Codex's popup snapshots change once more.

---

## 11. Risks and open questions

1. **Does `high-swe` resolve more?** Unknown until §6. The design is argued, not measured.
2. **Should `high-swe` carry the web block?** Left out (§2). A user who fixes bugs interactively under `high-swe` loses search. A variant `high-swe` plus web is one line as a custom prompt; whether it should be the built-in is the user's call.
3. **Options that travel with a prompt** (cave level, disabled tools, compaction limit). Useful if arms C and D win; it turns "a prompt" into "a profile", and Codex already has a thing called a profile. Decide after §6.
4. **The code-navigation block's wording** is being changed by other work. `high-swe` takes whatever the launcher's `code` block is; if that work moves navigation into a tool, the block may disappear and step 2 of the text should then name the tool.
5. **Mid-turn switching.** Allowed, like `/cavemode`. A switch during a long tool loop stalls that turn for the re-read; nothing is lost. If it confuses in practice, restrict the switch to between turns.
6. **Hidden-test wording.** Step 5 tells the model to assume unseen tests through the public interface. That is true of SWE-bench and of any repository with CI, but it is the one sentence written with the benchmark in mind.
7. **Subagents under a switched prompt** start with the launch prompt (§3.3). If `high-swe` sessions use subagents at all (no benchmark trajectory contains a subagent call, in 48 instance runs), inheritance needs the parent's thread id at the hook.
8. **Overfitting.** The text was written after reading 11 failures out of 24 instances, and will be measured on a set that contains them. §6.3's larger set is the guard; a prompt tuned further on the same 24 is not evidence.

---

## 12. Checked here, and assumed

**Checked on this machine (2026-10-02):**
- the request's three parts and their sizes (§1.1), by capturing `ling exec` at a stub endpoint;
- the five rows of §1.2, the same way: the catalog route, the resumed session keeping its prompt, the override beating it and dropping the blocks;
- `apply_patch` as a shell command, by standard input and by argument, in a scratch directory;
- the tool list after disabling `multi_agent`, `goals` and `web_search` (§5.5);
- the failure classes of §1.4, from the grading reports, the predictions and the reference patches of `acc-25`;
- the permission defect of §6.1, from the trajectories ("Permission denied", "Failed to write file") and in a pilot container (`-rw-r--r-- root` before the fix, `-rw-rw-rw-` after);
- no existing `/prompt` slash command and no `prompt` subcommand in the pinned Codex;
- the pilot of §6.4: ten runs against the live model on a shared server, graded with the benchmark's harness.

**Read from the source, not run:** that `get_prompt_base_instructions()` is the only place a request's prompt is rendered (its callers: the turn, both compaction paths, the prewarm, world state, the reviewer); that `mightling-prompt` as a standard-library crate creates no cycle, by analogy with `ling-airgapped`.

**Assumed:** the patch size; the 30-second figure for a switch at 50K tokens (scaled from one cold read of 39.5K tokens); that a hundred instances validate on arm64.

---

## 13. What was built (Phase 1, 2026-10-03)

**In the launcher** (`ling-rs/src/prompt.rs`, no Codex patch):
- The two built-in prompts: `default` (`Core::Codex`, all three blocks) and `high-swe` (`ling-rs/prompts/high-swe.md`, v2: Appendix A with §6.4's two edits, 4,381 chars, the `code` block only). Custom prompts from `$CODEX_HOME/system-prompts/<name>.md` with the optional `<!-- ling: blocks=… -->` first line; a file with a bad name, no text, or a built-in's name is passed over with a note in `ling prompt list`, and an unknown block name is ignored with one.
- The tiers of §4.1: `DREAMFERENCE_MIGHTLING_PROMPT`, `mightling_prompt` in the configuration file, `default`. An unknown name prints `⚠️  prompt "<name>" from <tier> is not installed (installed: …); skipped.` at launch and the next tier is used. An interactive session started under another prompt than `default` prints `Prompt: <name> (<tier>).` once.
- `ling prompt` / `list`, `show [<name>]` and `use <name>`, intercepted before Codex like `night` and `node`. `show` prints the text on stdout and its size and blocks on stderr; `use` writes the key with `toml_edit` and says when the variable still wins.
- The catalog route of §4.2: `model_catalog.json` is written as before and always carries `default`; another prompt is written to `model_catalog.<name>.json` and named with `-c model_catalog_json="…"` in front of the user's arguments, unless the user passed their own `model_catalog_json`.

**Elsewhere:** `DreamferenceConfig.mightling_prompt` (`DEFAULT_MIGHTLING_PROMPT`, a test keeps it equal to the launcher's), `[night] prompt` (passed as `DREAMFERENCE_MIGHTLING_PROMPT` to every command of the task), `ling-admin swe-bench run --prompt <name>` (§6.2), and `prompt` among the subcommands Night Shift does not count as an open session.

**Added 2026-10-10:** a third built-in beside `ask` (§ASK), `offline`: `Core::Codex` with the `code` block only, `default` for a session with no network, where the web and email blocks would name commands that cannot work. It is the SWE-bench arm `run --prompt offline` of the failures analysis (FAILURES §9.3, rank 5), which no longer needs a file mounted into the instance's home. One launcher test composes it against `default`.

**Where it departs from the design above:**
- **`model_catalog.json` always holds `default`,** not the chosen prompt: `config.toml` names it, and `ling skill` and `/night` read the served model's id and window from it.
- **The skills glossary follows every prompt.** It is not one of the three blocks: the skills list is a developer message both prompts see (§7), so the glossary that explains it goes wherever the list goes. It is off by default.
- **Codex's search sentence is rewritten only for a prompt that carries the `code` block,** because the rewritten sentence points at the Code navigation section. `high-swe` has no such sentence; its own says `rg`, then `grep -rn`.
- **The benchmark mounts the one file** (`<host>/system-prompts/<name>.md` at the container's `$CODEX_HOME/system-prompts/<name>.md`, read-only), not the folder, and the manifest records its SHA-256 as `prompt_sha256`; a built-in prompt is covered by `runtime_hash`. A manifest written before this has no `prompt` and is compared as `default`.
- **The Python side checks the name's form only** (lowercase letters, digits, hyphens): which prompts are installed is the launcher's to say.

**Tested:** 10 launcher tests (`cargo test --release -p ling-launcher` in a scratch export: 131 passed, 1 ignored), among them that `default` composes byte for byte to what the launcher sent before, for the combinations of email, code block, glossary and `rg`; and the Python tests in `tests/test_mightling_prompt.py`, `tests/test_night_shift.py` and `tests/test_swe_bench.py`, among them that the shipped text is Appendix A with §6.4's two edits and nothing else.

**Run in the build of `258c3b5` (2026-10-03),** against the live model rather than a stub endpoint:
- `DREAMFERENCE_MIGHTLING_PROMPT=high-swe ling exec …` recorded the `high-swe` text as the rollout's `base_instructions` (`You are Mightling, a coding agent. You work in a software repository through a shell, …`), and the launcher wrote `model_catalog.high-swe.json` beside `model_catalog.json`.
- That session resumed with the variable unset (`ling exec resume <id>`) and asked to quote the first two sentences of its instructions quoted `high-swe`'s; a fresh session under `default`, asked the same, quoted `default`'s (`… You and the user share one workspace, …`). So a resumed session keeps its prompt.
- `ling prompt list` from a shell listed `default` (chosen) and `high-swe`.

**Not run:** Phase 0 (§6.1), the runs of §6.2, and Phase 2 (`/prompt` in the TUI, the live switch and its patch) are not built.

---

## Sources

- [mini-swe-agent, SWE-bench configuration](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/config/benchmarks/swebench.yaml): the one-line system prompt, the five-step workflow, the submission rules (read 2026-10-02)
- [Anthropic: Raising the bar on SWE-bench Verified with Claude 3.5 Sonnet](https://www.anthropic.com/engineering/swe-bench-sonnet): the prompt text and the tool-design lessons
- [Claude Sonnet 4.6 system card](https://www.anthropic.com/claude-sonnet-4-6-system-card): the prompt addition worth 0.6 points (quoted from a search summary, not read in full)
- [SWE-Bench Mobile (arXiv 2602.09540)](https://arxiv.org/html/2602.09540v1): the twelve-variant prompt ablation
- [Beyond Resolution Rates: Behavioral Drivers of Coding Agent Success and Failure (arXiv 2604.02547)](https://arxiv.org/abs/2604.02547): 9,374 trajectories; abstract only
- [Coding Agents Don't Know When to Act (arXiv 2605.07769)](https://arxiv.org/abs/2605.07769): action bias and the effect of reproduction instructions; abstract only

---

## Appendix A. The `high-swe` text (v1)

The candidate the pilot of §6.4 ran, verbatim (4,229 chars). Phase 1 ships v2, which is this text with the two edits §6.4 lists, as `ling-rs/prompts/high-swe.md`; the runs of §6.2 measure v2; and once they are recorded a test pins the shipped file to the text of the most recent recorded run, as cave mode's test does for its levels.

````markdown
You are Mightling, a coding agent. You work in a software repository through a shell, and your job is to resolve the task you are given by changing the repository's source code, correctly and completely, without breaking anything that worked before.

# How to resolve a task

Work in this order. Most of the work is reading and checking; do not rush to the edit.

1. Read the task as a specification. Note every behaviour it asks for, and every exact name, option, message and expected output it gives. Implement what it asks for, exactly as written: do not add conditions, padding, validation or formatting it did not ask for, and do not solve a neighbouring problem instead.
2. Find where the behaviour lives before you change anything. Search for the names in the task, read the code that produces the wrong result, and read its callers. The line where an error is raised is often not the place to fix it: follow the wrong value back to where it is produced. Check whether the same logic exists in more than one place (another class, another backend, a sibling function, a second module that formats or validates the same thing).
3. Reproduce the problem. Write a short script in /tmp (never inside the repository) that shows the behaviour the task describes, run it, and confirm you see the failure. If you cannot reproduce it, say so and work from the code.
4. Before the first edit, state in two or three sentences what the root cause is and what you will change. Then make the smallest change that makes the behaviour correct in general, not only for the example in the task, in the style of the code around it. Fix every place that implements the behaviour, not just the first one you found. Do not change public signatures, defaults or unrelated code.
5. Verify. Run your script again. Then run the project's existing tests for the area you changed: the whole test file or test package for that module, not a single test. A test that passed before your change and fails after it means your change is wrong: change your fix, not the test. Assume your change will also be checked by tests you have not seen, which exercise the behaviour through the public interface.
6. Clean up. Run `git status` and `git diff` and read them: the diff must contain only the source changes the task needs. Delete scratch files. Do not edit existing tests, test fixtures, documentation or configuration unless the task asks for it, and do not commit.

# Working in the shell

- Run commands non-interactively and keep their output small (`head`, `tail`, `sed -n '120,180p' file`, `grep -n`). Search with `rg`; if it is not installed, use `grep -rn`.
- Edit files with `apply_patch`, a shell command that reads a patch from standard input:

      apply_patch <<'EOF'
      *** Begin Patch
      *** Update File: path/to/file.py
      @@
       unchanged line before
      -line to remove
      +line to add
       unchanged line after
      *** End Patch
      EOF

  Context lines start with a space and must match the file exactly. `*** Add File: path` creates a file whose lines all start with `+`. If a patch does not apply, read the file again and correct the context; do not fall back to rewriting the whole file.
- If a command fails for a reason that has nothing to do with the task (a missing tool, a permission), use the next simplest way and move on; do not investigate the environment.
- Never run destructive git commands (`git reset --hard`, `git checkout -- <file>`, `git clean`) on changes you did not make, and never revert changes that were in the tree before you started.

# Staying on the task

- Finish in one go. Do not stop at an analysis or a plan, and never end a message by announcing what you will do next: do it. If nobody can answer questions, make the reasonable choice and say which you made.
- If two attempts at a fix fail, stop editing and go back to step 2: the cause is probably somewhere else.
- When the conversation is compacted you may see a summary instead of the full history. Continue from it; do not start again.

# The final message

Say what was wrong, which files you changed, and how you verified it (the commands you ran and what they showed). Name anything you could not verify. Keep it short.
````

## Appendix B. The eleven failures

| Instance | Target tests passed / failed | Previously passing tests broken | Reference fix touches | The patch touched |
|---|---|---|---|---|
| django-12774 | 1 / 0 | 2 | `db/models/query.py` | the same, plus `tests/lookup/models.py`, `tests/lookup/tests.py` |
| django-13512 | 1 / 2 | 0 | `contrib/admin/utils.py`, `forms/fields.py` | `forms/fields.py`, a test file |
| django-15563 | 1 / 1 | 0 | `sql/compiler.py`, `sql/subqueries.py` | both, plus two test files |
| django-15957 | 0 / 4 | 1 | `fields/related_descriptors.py` | `db/models/query.py` |
| django-16454 | 1 / 0 | 1 | `core/management/base.py` | the same |
| django-16502 | 0 / 1 | 0 | `core/servers/basehttp.py` | `core/handlers/wsgi.py`, release notes, a test file |
| scikit-learn-25747 | 0 / 1 | 0 | `utils/_set_output.py` | the same, a test file, `.write_test` |
| sympy-13031 | 0 / 1 | 0 | `matrices/sparse.py` | `matrices/common.py`, `.write_test` |
| sympy-13798 | 0 / 1 | 0 | `printing/latex.py` | the same |
| sympy-13877 | 0 / 1 | 0 | `matrices/matrices.py`, `utilities/randtest.py` | `core/exprtools.py` (timed out) |
| sympy-17318 | 0 / 1 | 0 | `simplify/radsimp.py`, `simplify/sqrtdenest.py` | `radsimp.py`, a test file, `probe.txt` |

## 14. A third built-in prompt, `ask` (2026-10-07)

`ling prompt list` now shows three built-in prompts: `default`, `high-swe` and **`ask`** (`ling-rs/prompts/ask.md`, all three blocks: web, email, code), written for questions and research in a scratch folder with cited sources. It is what the web UI's Ask threads use: the UI asks for it by name and `ling web`'s bridge policy sets it from `ling prompt show ask --composed` ([MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md) §3.2). It is chosen like any other (`ling prompt use ask`, `DREAMFERENCE_MIGHTLING_PROMPT=ask`). Refine mode's two texts are in `ling-rs/prompts/refine.md` but are not a named prompt: `refine.rs` composes them around the task ([MIGHTLING_REFINE](./DREAMFERENCE_MIGHTLING_REFINE.md)).

