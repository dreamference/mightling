# Mightling on SWE-bench: why 32 of 100 tasks failed (`im100-default`)

**Status:** analysis, 2026-10-08. §6 proposes four changes, each to be measured as an A/B on 50 tasks that are not among these 100. The harness defects of §5, Proposals 1, 2 and 4, and the scripts for the first A/B night are built (§8); nothing is measured yet. §9 (2026-10-09) reads every failure more closely, finds the moment each run went wrong and ranks the candidate fixes for night 3 onwards. Where §9 and §6.5 disagree on order, §9 is current.
**Data:** the round `im100-default` in `~/.local/share/dreamference/swe-bench/runs/im100-default/`, read only: `manifest.json`, `predictions.jsonl`, `instances/<id>.json`, `logs/<id>.jsonl` (the agent's event stream), `scratch/<id>/codex-home/sessions/` (the session rollouts), `eval/1/grading.json` and the harness's per-task `report.json` and `test_output.txt`. Each task's difficulty comes from [MIGHTLING_SWE_BENCH_COMPARISON](./DREAMFERENCE_MIGHTLING_SWE_BENCH_COMPARISON.md)'s per-task table (175 published submissions, 78 of them at 60% or more overall).
**Builds on:** [MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) (the harness), [MIGHTLING_PROMPT](./DREAMFERENCE_MIGHTLING_PROMPT.md) §1.4 and §5.2 (the first failure classes, from 11 failures on 24 tasks) and [MIGHTLING_REFINE](./DREAMFERENCE_MIGHTLING_REFINE.md) (the study-then-fix arm, whose 100-task round `im100-refine` started on the evening of 2026-10-08).

---

## Conclusion

- **The easy losses come from how the agent treats tests, not from finding the bug.** We call a task *easy* when at least 75% of the strong submissions (60% or more overall) solve it. The agent failed 7 easy tasks. In 4 of them the source change went to the right place and the loss came from tests: a change broke an existing test and the agent rewrote that test to pass (django 16100, sphinx 11445), a condition was broader than the issue asked and broke a hidden existing test (django 12774), or the agent's edit to a test file stopped the benchmark's own tests from being applied (django 13837). Wrong-file failures appear only in the medium and hard tiers.
- **The hard failures look like the hard failures of everyone else.** 19 of the 32 failures are tasks that fewer than 40% of strong submissions solve, and 7 are tasks that at most 2.6% of them solve. Among the hard tasks, the agent missed a second code path in 5 and fixed the wrong file in 4.
- **The categories, for all 32:** a second code path or file missed 8; wrong file 5; right place but wrong detail 5; behaviour the issue did not ask for 5; the fix broke other tests 4; the test patch blocked by the agent's own test edits 2; stopped mid-work 2; ran out of time 1. No failure came from an empty patch, a tool or environment error, or a malformed tool call.
- **Two harness defects** (§5): the report's "test patch failed" count misses `git apply`'s "already exists in working directory" (django 16877); and the stall nudge cannot fire once the tree has changed, so it fired once in 100 tasks, although 2 runs stopped in mid-sentence.
- **The ranked proposals** (§6), each measured as an A/B on 50 fresh tasks: (1) three sentences of test discipline in the task prompt, (2) remove test-file hunks from the collected patch, measured by a second grading of the same predictions with no new agent run, (3) an issue-contract check in the task prompt, measured on top of whichever arm the `im100` refine pair favours, and (4) a completion nudge in the runner. The expected gains are small (§6.5): one 50-task pair can confirm only a large effect, so each arm is judged first by the behaviour it targets.

---

## 1. The round

| | |
|---|---|
| Tasks | 100 SWE-bench Verified tasks (the validated arm64 set at the time, which includes the 24-task sample) |
| Agent | `ling` built on Codex `rust-v0.158.0` (recorded in the run under its old name, `puffin 0.158.0`), prompt `default`, cave mode `ultra`, `--code-index universal`, no refine, masking off |
| Model | Qwen3.8-27B NVFP4 on SGLang (`RadixArk/Qwen3.8-27B-NVFP4`), served context 262,144 |
| Limits | `task_context` 49,152 (the compaction limit), 45 min per task, 8 GiB, 2 nudges, 2 tasks at once |
| Result | **68 of 100 resolved**, about 69% on all of Verified by the comparison's Rasch estimate (95% back-test band 64% to 73%) |

Every one of the 32 failures produced a patch, and every patch applied. One task timed out (django 15252); the 2 other timeouts were resolved with their partial patches.

## 2. The 32 failures

*Strong* is the share of the 78 submissions at 60% or more overall that solve the task; *all* is the share of all 175 submissions. Tiers: easy is 75% or more strong, medium is 40% to 75%, hard is below 40%. The categories are defined in §3.

| Task | Strong | All | Tier | Category | Evidence |
|---|---|---|---|---|---|
| django-13837 | 98.7% | 76.6% | easy | Test patch blocked | The agent edited `tests/utils_tests/test_autoreload.py` (42 lines removed). The harness's `git checkout <base> -- …` aborted with `pathspec 'tests/utils_tests/test_module/__main__.py' did not match`, so that edit was not reverted, and then `tests/utils_tests/test_autoreload.py: patch does not apply`. The source change uses `__main__.__spec__.parent`, the same rule as the reference fix |
| django-16100 | 88.5% | 68.0% | easy | Broke other tests | The agent wrapped all of `changelist_view` in `transaction.atomic`; the reference wraps only the save loop. The agent saw `FAIL: test_changelist_view_list_editable_changed_objects_uses_filter` (item 32) and replaced that test's `captured_queries[4]` assertion with a filter over all queries. With the original test restored, it fails: `'WHERE' not found in 'SELECT COUNT(*) …'` |
| django-12774 | 88.5% | 50.3% | easy | Broke other tests | The agent accepted a field that appears in *any* total `UniqueConstraint` (`field.name in constraint.fields`), so a field in a multi-field constraint passed. The hidden existing test `test_in_bulk_non_unique_meta_constaint` expects `ValueError` and failed |
| sphinx-11445 | 87.2% | 46.9% | easy | Broke other tests | The agent narrowed `docinfo_re` to a fixed list of docinfo names, which broke `test_prepend_prolog` (it uses `:title:`). The agent saw the failure (item 126) and rewrote the test's `:title:` lines |
| matplotlib-14623 | 85.9% | 51.4% | easy | Second path missed | The `_base.py` half equals the reference; the `LogLocator.nonsingular` half in `ticker.py` is missing. `test_inverted_limits` failed with `assert (-5.0, 4.0) == (4, -5)`, and the existing `test_inverted_cla` now fails too. The agent ran `test_axes.py` piped through `tail -6`, never saw that test's name, and called the remaining failures "pre-existing FreeType/environment issues" |
| django-15037 | 84.6% | 52.6% | easy | Wrong detail | The agent appended `, to_field=…` straight after the model name, which emits `ForeignKey('X', to_field='y', models.DO_NOTHING)`: generated code with a keyword argument before a positional one. The agent's own new test asserted that same prefix and passed |
| sympy-13615 | 80.8% | 42.3% | easy | Not asked for | The issue gives the expected result, `{x, y} \ [-10,10]`. The patch returns `{x, y}`, the output the issue reports as wrong, and edits `Interval._complement` where the reference changes `Set._complement`. No test runner ran (`No module named pytest`, after which only ad-hoc checks) |
| django-16877 | 71.8% | 48.0% | medium | Test patch blocked | The agent created `tests/template_tests/filter_tests/test_escapeseq.py`, the file the test patch adds: `error: … already exists in working directory`, so its own tests ran in place of the benchmark's. The source would also have failed 2 of the 4 hidden tests: it uses `escape` where `conditional_escape` keeps `mark_safe` items |
| sympy-18211 | 65.4% | 41.1% | medium | Wrong detail | The right `try/except` is in the right function, but the fallback is `solveset(…)` where the issue names the result, `ConditionSet(n, Eq(…), Reals)`; `test_issue_18188` fails on a second equation |
| django-12193 | 62.8% | 52.6% | medium | Wrong file | The agent copied the attributes in `SplitArrayWidget` (postgres) where the fault is `CheckboxInput.get_context` mutating its argument, as the issue itself says; `test_get_context_does_not_mutate_attrs` fails |
| django-13512 | 48.7% | 30.3% | medium | Second path missed | The patch changes the form field only. The issue is about the admin; `display_for_field` (`admin_utils`) is not changed, and `test_json_display_for_field` fails |
| sympy-23413 | 48.7% | 21.7% | medium | Wrong detail | The agent rewrote the HNF pivot loop. The issue's matrix works, but `test_hermite_normal` fails on other shapes (140 commands, 2 compactions) |
| django-14376 | 44.9% | 22.9% | medium | Second path missed | `base.py` was fixed. The agent's last message says that `client.py` "still uses `db`/`passwd` … out of scope"; all three `dbshell.test_mysql` targets fail |
| django-16950 | 38.5% | 17.1% | hard | Broke other tests | The agent rewrote inline-formset key handling across two classes; 3 existing `test_uuid` tests fail, and the agent changed 5 of their assertions to match (404 commands, 6 compactions) |
| django-15563 | 24.4% | 13.1% | hard | Stopped mid-work | A one-line filter change in `subqueries.py`; the reference changes `compiler.py` too. No test runner was ever run, and the last message ends "Wait, let me re-read" with status `done` |
| pylint-4970 | 20.5% | 16.0% | hard | Wrong file | The agent guarded the checker class; the hidden test drives the stand-alone `similar.Run`, which still prints `TOTAL lines=62 …` |
| django-11885 | 20.5% | 9.7% | hard | Stopped mid-work | The agent refactored fast deletes into a per-model list of `where` nodes; its last four test runs ended `FAILED (errors=8)`, and its last message is mid-debugging ("`WhereNode` doesn't have `__or__`. Let me check …"). 34 of 43 existing tests fail |
| sympy-13974 | 19.2% | 14.9% | hard | Second path missed | Handles positive-integer powers only; the hidden test asks for `TP(A, B)**x` with a symbolic `x` |
| django-15957 | 17.9% | 8.0% | hard | Wrong file | Re-applies the slice once overall in `query.py`; the hidden tests need a limit per parent (a window function in `related_descriptors.py`). Refine resolved it on the 24 (REFINE §2) |
| django-11790 | 16.7% | 21.1% | hard | Wrong detail | Right place; `widget_attrs` sets `maxlength` as the string `'254'`, and the test asserts the integer: `AssertionError: '254' != 254` |
| django-12325 | 14.1% | 10.9% | hard | Second path missed | Made the parent-link choice independent of field order, but kept the `ImproperlyConfigured` check the reference removes; both targets still raise `Add parent_link=True to …` |
| sklearn-25747 | 12.8% | 9.7% | hard | Not asked for | Added length conditions around the index override where the reference removes the override (the same pattern as in PROMPT §1.4 and REFINE §2) |
| sympy-17318 | 10.3% | 8.0% | hard | Wrong file | Guarded `split_surds` against an empty list; the hidden test asserts `_sqrt_match(4 + I) == []` |
| sympy-13798 | 7.7% | 4.6% | hard | Not asked for | Pads a custom `mul_symbol` with spaces; the test expects it verbatim. The final message even offers "the raw string with no added whitespace" as an alternative |
| sklearn-26194 | 3.8% | 2.9% | hard | Not asked for | Clipped the extra threshold to `max + eps` where the reference uses `inf`, and changed two existing expectations from `2.0` to match |
| django-12406 | 2.6% | 1.1% | hard | Wrong detail | Sets `empty_label=None` in `ForeignKey.formfield`; the hidden test passes a new `blank=` argument to `ModelChoiceField`: `unexpected keyword argument 'blank'` |
| sympy-22080 | 2.6% | 1.1% | hard | Second path missed | Added `Mod` to the precedence table only; `Mod(-x, y)` must print `(-x) % y`, which needs the unary-minus change in `codeprinter.py` |
| django-15252 | 1.3% | 0.6% | hard | Ran out of time | Timed out at 2,702 s (310 commands, 5 compactions), with an approach unlike the reference's (soft-applied detection in the executor; the reference skips `ensure_schema` for an empty plan) |
| django-13212 | 0.0% | 0.0% | hard | Second path missed | All of `validators.py` done; the `DecimalField`/`FileField` validators in `forms/fields.py` are not, and the agent called the 2 failures it saw "pre-existing (URL test data on Python 3.6)" |
| django-15629 | 0.0% | 0.0% | hard | Second path missed | Collation added to the FK's `db_parameters`; the schema editor's FK drop-and-recreate (3 more files in the reference) is not |
| django-16502 | 0.0% | 0.0% | hard | Wrong file | Strips the body in `WSGIHandler` (`handlers/wsgi.py`); the test drives runserver's `basehttp` handler. Refine misplaced it the same way (REFINE §2) |
| pylint-4661 | 0.0% | 0.0% | hard | Not asked for | Chose `$XDG_DATA_HOME/pylint`; the reference uses `appdirs.user_cache_dir`. The patch also adds a stray `classes.dot` |

## 3. Counts

| Category | Definition | Easy (7) | Medium (6) | Hard (19) | All |
|---|---|---|---|---|---|
| Second path missed | Right file or function; a second code path, file or case the issue implies is not changed | 1 | 2 | 5 | **8** |
| Wrong file | The change is in a file the reference fix does not touch, and the targets still fail | 0 | 1 | 4 | **5** |
| Wrong detail | Right place and the right idea; an implementation detail is wrong (type, argument order, fallback, algorithm) | 1 | 2 | 2 | **5** |
| Not asked for | Behaviour the issue does not ask for, or a different answer to an open design choice than the reference | 1 | 0 | 4 | **5** |
| Broke other tests | The targets pass (or would), and existing tests fail | 3 | 0 | 1 | **4** |
| Test patch blocked | The agent's test edits stopped the benchmark's test patch from applying | 1 | 1 | 0 | **2** |
| Stopped mid-work | Ended with a changed tree, status `done`, and a last message that is mid-reasoning | 0 | 0 | 2 | **2** |
| Ran out of time | Timed out at 45 min | 0 | 0 | 1 | **1** |
| Empty patch; tool or environment error; malformed tool call | | 0 | 0 | 0 | **0** |

Where the 32 failures stand against the leaderboard:

- **The easy and medium tiers (13 failures) are where gains are realistic.** Seven of those 13 are about tests or the issue's literal statement: test discipline (16100, 11445, 12774, 13837, 16877), the expected output the issue states (13615), and the result type the issue names (18211). Only one (12193) is a wrong file.
- **The hard tier (19) is mostly out of reach for a single attempt.** 7 of the 19 are solved by at most 2.6% of strong submissions, 4 of them by none. What remains there is mostly "second path missed" and "wrong file", the classes the refine arm was built for (REFINE §2).

Some patterns run across the categories. Each was counted over the 32 failures and, where it says so, over all 100 tasks.

- **Editing tests is not the problem; editing an existing assertion is.** 62% of failing patches (20 of 32) and 62% of resolved patches (42 of 68) change a test file, and 53% and 50% change a file the benchmark's test patch also changes. The harmful pattern is narrower: in 3 failures (16100, 11445, 16950) the agent ran an existing test, saw it fail because of its change, and changed the test's assertion; in a fourth (15037) its own new test encoded its own bug.
- **"Pre-existing" is said without comparing names.** Four final messages dismiss failures as pre-existing (14623, 13212, 4970, 4661). In 14623 a test it had broken was among them; the run piped the suite through `tail -6`, which shows a count and no names.
- **The stall nudge never fired.** `nudges` is 0 in 99 of 100 instances (1 in a resolved one). It needs an unchanged tree (§5.2), and both mid-work stops had changed the tree.
- **The environment costs turns but did not decide outcomes.** All 18 sympy images have no `pytest`, and 37 of the 100 tasks hit `No module named pytest` at least once; most went on to `bin/test` or `sympy.test(...)`, but sympy 13615 ran no test runner at all. The web commands the system prompt names were invoked 94 times in 41 tasks, with no network to reach. `code_show` failed 14 times in 10 tasks, always called without its required `name` (`file`/`path` plus `offset`/`limit`, as a file-region read: `Mcp error: -32601: an empty name`, `ling-code-rs/src/router.rs`); each was followed by a `sed -n` read.
- **Failures run longer.** Median wall time is 524 s against 349 s for resolved tasks; compaction happened in 15 of 32 failures against 25 of 68 resolved tasks. Both follow difficulty, so they are effects more than causes.

## 4. What the refine arm does and does not reach

Refine was built for "second path missed", "wrong file" and "not asked for" (REFINE §2), which cover 18 of these 32 failures, and `im100-refine` measures it on the same 100 tasks. On the 24-task sample it already resolved 13512 and 15957, the first of which fails here, and misled 16502, 25747 and 13798 the way the one-session agent fails them here. It does nothing for the 7 failures that come from tests, the 2 mid-work stops or the harness defects, which is why §6's proposals 1, 2 and 4 are independent of how that pair comes out.

## 5. Harness defects

### 5.1 The "test patch failed" count misses a `git apply` error

`SweBenchHarness.test_patch_failed` (`dreamference/swe_bench/swe_bench_harness.py`) looks for `error: patch failed` or `patch does not apply` in `test_output.txt`. In django 16877, `git apply` refused with `error: tests/template_tests/filter_tests/test_escapeseq.py: already exists in working directory`, so `grading.json` records `test_patch_failed: false` for a task whose test patch never applied. **Fix:** treat any `error:` line between the eval script's `+ git apply -v -` and `>>>>> Start Test Output` as a failure, and add the 16877 line as a test fixture.

### 5.2 A partial checkout leaves the agent's test edits in place

This is a property of the upstream harness rather than a bug in ours, but it decides tasks here. The eval script reverts the test patch's files with one command, `git checkout <base> <file>…`. When the test patch *creates* a file, that path does not exist at the base commit, git aborts the whole command, and none of the files are reverted. The agent's edits to the same test files then stay, and the test patch may not apply. In this round the abort appears in 5 tasks: in 13837 and 16877 it decided the outcome, and in 16454, 10673 and 9711 (all resolved) it did no harm. MIGHTLING_SWE_BENCH §5.1 keeps test files in the patch on purpose, matching mini-SWE-agent. Proposal 2 measures what that choice costs, and the report should count every task whose test output contains `did not match any file(s) known to git` together with a model patch that touches a test-patch file, whatever the grading's flag says.

### 5.3 The nudge cannot fire once the tree has changed

`SweBenchInstanceRun` resumes with Night Shift's nudge only when `/testbed` is unchanged *and* the last message announces work (`announces_work`). That catches an agent that never started. It cannot catch one that edited, ran into failures and ended its turn mid-reasoning (11885: "Let me check …"; 15563: "Wait, let me re-read"). Proposal 4.

### 5.4 Not defects, but recorded

- The matplotlib image's baseline already fails dozens of image comparisons (FreeType); the benchmark excludes them from PASS_TO_PASS, but the agent cannot tell them apart from a regression without comparing test names (14623).
- Only 20 validated tasks lie outside these 100 (`validated-arm64.json` holds 120; 9 rejected). The fresh 50 of §6 need about 35 more validated first (§6.5).

## 6. Proposals, ranked by expected gain per effort

Each proposal is a named arm, recorded in the manifest, on the same build, model and settings as `im100-default`.

### 6.1 Proposal 1: test discipline in the task prompt (prompt; smallest change)

**Targets:** 16100, 11445, 16950 (assertions rewritten), 14623 (a regression hidden in a count), 15037 (a self-confirming test), and indirectly 13837 and 16877 (test edits colliding with the test patch). That is 3 to 5 of the 7 easy losses.

**Change:** add three lines to `PROMPT` in `dreamference/swe_bench/swe_bench_instance_run.py`, behind a new `--task-rules tests` option (recorded as `task_rules` in the manifest; `report --against` lists it):

```text
- Never change an existing test. If a test that passed before your change fails after it, your change is wrong: fix the source.
- Put any test or script of your own in /tmp, not in the repository.
- Before you stop, run the test files of every module you changed, with and without your change (git stash), and compare the failing tests by name.
```

They go in the task prompt and not the system prompt, as `CODE_INDEX_HINT` does. MIGHTLING_PROMPT §1.5 found that long prompts cost a weak model, and the long `test-first` system prompt resolved 14 of 24 (the comparison's table), fewer than any default arm on the same sample (15 to 17). The second line gives up the agent's own regression tests in the patch. They are reverted at grading anyway, except in the §5.2 case, where they do harm.

**Measures, before the resolve rate:** patches that remove lines from an existing test (here 21 of 100 tasks); patches that touch a test path (62 of 100); final messages that call a failure pre-existing; whether the last test run before stopping covered the changed modules.

### 6.2 Proposal 2: drop test-file hunks from the collected patch (harness; no agent run)

**Targets:** 13837, 16877, and any future §5.2 collision. On the evidence here, about 1 resolved task per 100.

**Change:** a `--strip-tests` option to `swe-bench eval` that grades a copy of the predictions with every hunk removed whose file is under a `tests/` or `testing/` directory or is named `test_*.py`, `*_test.py`, `tests.py` or `conftest.py`. It never uses the dataset's `test_patch`: the rule must be one a real submission could apply. Because it changes only the patch, it is measured by **grading the same predictions twice**, both the baseline arm and Proposal 1's arm, with no new agent run. Fix §5.1 in the same change, so the unstripped grading counts collisions correctly.

**Decision rule:** whether it becomes the default is the user's call, since MIGHTLING_SWE_BENCH §5.1 chose the reference agent's behaviour on purpose. The measurement says how much that choice costs.

### 6.3 Proposal 3: an issue-contract check in the task prompt (prompt)

**Targets:** 13615 (the issue's expected output not reproduced), 18211 (the issue's named result type), 13512 and 14376 (a second path the issue names: "Admin doesn't display", the dbshell client), 14623 (log scale is the issue's case), and on the hard side 13212, 22080 and 12193. Plausibly 2 to 4 of the 13 easy and medium failures.

**Change:** two more lines under the same option (`--task-rules tests,contract`):

```text
- If the issue shows an example with its expected result, run that example last and check the output matches the issue exactly.
- Search the whole package for every other place that implements the behaviour you changed (callers, sibling classes, other backends, the admin) and fix each of them.
```

**Overlap:** this is a one-session cousin of refine's "every code path" and "acceptance checks" sections. Run it on top of whichever arm `im100-refine` favours: on the default arm if refine does not win, and as an extra line in the fix prompt if it does. **Measures:** the share of failures in "second path missed" and "not asked for", read from the reference patch after grading, the same way as in §2.

### 6.4 Proposal 4: a completion nudge (runner; small code)

**Targets:** 11885 and 15563, both hard. Expect 0 or 1 resolved; the point is that a run should not end with an announcement.

**Change:** in `SweBenchInstanceRun`, after the agent stops and while nudges remain, resume with a completion nudge when the last message announces work (`announces_work`) or ends without a summary, **whether or not the tree changed**:

```text
You stopped in the middle of your work. Finish the fix, run the tests for the modules you changed, and end with a short summary.
```

It stays within the existing `nudges` budget and the task timeout, and the instance state records which nudge fired. **Measures:** final messages that announce work (2 of 100 here); nudges fired; tasks resolved after a nudge.

### 6.5 How to run the A/B on 50 fresh tasks

1. **Validate first.** Run `ling-admin swe-bench setup --validate` on about 60 more arm64 tasks, so that at least 50 validated tasks lie outside these 100 (the 24-task sample is inside them). None of the 50 may come from tasks these proposals were written from.
2. **Fix the list before any arm runs.** Draw 50 tasks with a recorded seed, stratified to the same difficulty mix as these 100 (the comparison script gives each task's strong solve rate), and commit the list.
3. **Arms**, one after another on one build, interleaved so drift falls on both sides: baseline (`default`, as here), baseline again (the in-session noise floor, MIGHTLING_SWE_BENCH §13.7), Proposal 1, and Proposals 1 and 3 together. Proposal 4 runs in the arms that carry it, with its firings counted. Proposal 2 regrades the predictions of every arm.
4. **Statistics.** Two identical arms differed on 3 and 4 of 24 tasks in earlier sessions, about 15%. On 50 tasks that is 6 to 8 discordant tasks from noise alone, so a McNemar test needs a net gain of about 7 tasks to reach p < 0.05, more than any single proposal here is expected to give. Each arm is therefore judged first by its behaviour measure (§6.1 to §6.4), which moves far more than the resolve count. A resolve-count claim needs the pair repeated, or the 50 tasks pooled with a second 50.

### 6.6 Not proposed

- **`high-swe` as the fix.** It was at parity on 24 tasks (16 against 15 and 16, MIGHTLING_PROMPT §6.5), and its rules overlap with Proposals 1 and 3; the short task-prompt lines isolate the rules this round points at.
- **A longer time limit.** One failure timed out, and its approach was wrong; the 2 resolved timeouts were resolved on their partial patches.
- **A test-runner hint per repository.** Sympy's missing `pytest` cost turns in all 18 sympy tasks, but decided at most one outcome (13615), and naming each repository's runner is a benchmark-only crutch. The `ling` prompt's web block, wrong in a container with no network, is already open in MIGHTLING_SWE_BENCH §12.3.
- **Fixing `code_show`'s file-region read** (`name` optional when `path` and `offset` are given) is worth doing for the agent in general (MIGHTLING_CONTEXT_BUDGET already proposes `code_show` paging); it decided no outcome here.

## 7. Limits of this analysis

- Each failure was classified from its logs, the grading output and a comparison with the reference patch. No task was re-run, so the claim for 13837 (that the source fix alone would have resolved it) rests on the change being the reference's rule, not on a test run.
- One category per task. Several have a second (13615 is also a wrong file; 15252 is also a wrong approach; 16877 would also have failed on its source change).
- The tiers come from published submissions on x86 images; our arm64 images and validation filter can change what is hard (MIGHTLING_SWE_BENCH_COMPARISON §5).

## 8. As built (2026-10-08)

Branch `swe-bench/night1`. Tests in `tests/test_swe_bench.py`; nothing below has run on a real instance yet.

| | What was built | Where |
|---|---|---|
| §5.1 | "Test patch failed" counts any `error:` line between the eval script's `+ git apply` of the test patch and `>>>>> Start Test Output`, so `already exists in working directory` (16877) and `while searching for:` (13837) count, and the reset's own `error: pathspec …` lines, before and after that window, do not. The two outputs are fixtures, verbatim. | `SweBenchHarness.test_patch_rejected` |
| §5.2 | The reset is made per file. The eval script is a column of the dataset file each harness call gets, so no harness code is patched: both `git checkout <base> <files>` lines become a loop that checks out each file the base commit has and removes each one it does not. The agent's edits to the test patch's files are always undone, and a file the test patch adds always applies. The grader records `eval_reset: per-file`, so **the next `eval` of a run graded before this starts a new grading of every instance**. | `SweBenchHarness.per_file_reset`, `write_dataset_file` |
| §5.3, §6.4 | The completion nudge. After a turn that ended normally, with nudges left: an unchanged tree and a last message that announces work gets Night Shift's nudge, as before (`stall`); a changed tree and a last message whose final 400 characters announce a next step gets §6.4's text (`completion`). Only the end of the message is read, because both mid-work stops ended cut off mid-sentence a few lines after their last "Let me …", and "let me know" does not count. Read back over six earlier rounds (219 finished instances), the rule fires 6 times, each on a message that ends mid-work (11885 and 15563 among them), and on no summary. The state records `nudge_kinds`; `report` prints a `Nudges` line. | `SweBenchInstanceRun.stopped_mid_work`, `_nudge_kind` |
| §6.1 | `swe-bench run --task-rules tests` adds §6.1's three lines to the task prompt (and to the refine arm's fixing prompt), recorded as `task_rules` in the manifest and listed by `report --against`. One change to the text: the stash step reads "(git stash, then git stash pop)", so a comparison cannot end with the change stashed and an empty patch. Without the option the prompt is byte for byte the old one; the product's prompts are untouched. | `TASK_RULES`, `SweBenchRunner.run(task_rules=…)` |
| §6.2 | `swe-bench eval <run> --drop-test-hunks` grades the same predictions again with every file section dropped whose path has a `tests` or `testing` directory or is named `test_*.py`, `*_test.py`, `tests.py` or `conftest.py` (by path alone, never the dataset's `test_patch`), as a grading series of its own (`eval-drop-test-hunks/<n>/`, harness run id `<run>-drop-test-hunks-<n>`); the plain grading and the predictions are untouched. A patch with nothing dropped takes the plain grading's verdict by the same grader, so the two gradings differ only where a patch changed; one left empty is unresolved and never handed to the harness. `report <run> --drop-test-hunks --against <run>` says what dropping cost. `eval --remove-images` grades one repository at a time and removes the images that grading pulled. | `SweBenchPatchFilter`, `SweBenchEvaluator.grade(variant=…)` |
| Disk | With `run --eval --remove-images` the code-index pass removes each image it pulled once the repository is indexed (the instance pulls it again when it starts). Before, every image of a run was on disk before the first agent started: about 115 GB for 50 fresh tasks, past the 100 GB reserve with the 116 GB free on 2026-10-08. | `SweBenchRunner.run` |

**The first A/B night** (§6.5 steps 1 to 3 for Proposals 1, 2 and 4):

1. `scripts/swe_bench_fresh.py validate --count 60` validates arm64 Verified tasks outside `sample-100.txt` that have no validation result yet (271 such tasks on 2026-10-08), in `sha256(instance_id)` order, until 60 have a result: at most ten at a time and never more images than the disk above the reserve holds (`--disk-reserve`, default the configured 100G), removing each batch's images afterwards and trying a task skipped for disk or a failed pull once more. It refuses while another benchmark run holds the runner lock or `puffin-swe-im100-refine` is active.
2. `scripts/swe_bench_fresh.py draw --count 50 --seed <seed>` draws 50 of the validated tasks outside `sample-100.txt` (20 before step 1), stratified to `sample-100.txt`'s mix by the comparison data (59 easy, 16 medium, 25 hard of 100), into `~/.cache/dreamference/swe-bench/fresh-50.txt` with the seed and the mix in its header.
3. `scripts/swe_bench_night1.sh start` checks that the 100-task unit has finished, that the list exists and that the largest repository's images (about 3 GB each) fit beside the reserve (`RESERVE_GB`, default 100), writes a config with `max_parallel = 2` and that reserve as `disk_reserve`, and starts the user unit `ling-swe-night1`: `n1-default`, then `n1-tests` (`--task-rules tests`), each `--code-index universal --mask off --prompt default --eval --remove-images`; then `eval --drop-test-hunks --remove-images` on both, and the reports in `~/.local/share/dreamference/swe-bench/night1/`.

The two arms run one after the other, not interleaved: a run's instance list is fixed in its manifest. Two identical arms differed on 15% of tasks in earlier sessions (§6.5 item 4), so night 1 is read by its behaviour measures first.

## 9. Candidate fixes, ranked (2026-10-09)

§2 says what went wrong in each failure. This section reads every failed run from start to end and finds the first item where its trajectory left a path that would have resolved the task. It then names the change most likely to have prevented that, and checks each change against the 68 resolved runs to see whether it would also have changed what they did. The data is the same round, read only. `[n]` is the n-th completed item in `logs/<id>.jsonl`. The agent sees only the issue, never `hints_text`, so a fix that only the hints give counts as unrecoverable here.

### 9.1 The first wrong moment in each failure

Levers: **contract** is reading the issue literally: run its example last and match the expected output exactly, use the function or result type it names, and make the smallest change that does what it asks without broadening a condition. **sweep** means changing every sibling implementation and the stand-alone entry point, and never ruling one "out of scope". **tests** means not editing an existing test to make it pass. **review** is a final turn that re-reads the diff against the issue. **harness** means the built grading fixes of §8. **nudge** is the built completion nudge. **context** is a larger `task_context`. **U** means the hidden test checks a choice the issue does not imply. Confidence is the chance the lever alone flips the task: high above 60%, medium 25% to 60%, low below 25%.

| Task | First wrong moment | Lever | Conf. |
|---|---|---|---|
| django-13837 | [37] an old test fails, so it rewrites 6 existing autoreload tests; the source rule is the reference's | harness (tests) | high |
| django-16100 | [25] wraps the whole view; [39]-[40] rewrites a `captured_queries[4]` assertion its own stash run [38] showed passing at base | tests (contract: narrow the block) | high |
| matplotlib-14623 | [63]-[65] the issue's example prints `linear ylim: (1.0, 100000.0)` and it reports success; its swap-back **broke linear inversion** | contract (tests: compare by name) | high |
| sympy-13615 | [8]-[9] takes `{x, y}`, the output the issue calls wrong, as the target; `code_show` [4] had shown `Set._complement` | contract | high |
| django-12193 | [20] drops the fix it had named at [14] (`CheckboxInput.get_context`, which the issue names) for a remembered "upstream fix" in postgres | contract | high |
| django-16877 | [18] `escape` not `conditional_escape`; at [43] sees `mark_safe` items double-escaped and keeps it. [31] creates the test patch's file | sweep (mirror the sibling filter) + harness | medium |
| sympy-18211 | [49] falls back to `solveset` after planning at [54] to return the `ConditionSet` the issue names | contract | medium |
| django-12774 | [15] accepts a field in *any* total constraint; its own check [105] tries only a single-field one | contract (no broadening) | medium |
| django-15037 | [44] keyword before positional; its repro [81] and own test [96] print invalid code, unnoticed | review (contract) | medium |
| django-13512 | [12]-[13] greps `JSONField` in two admin files and never opens `admin/utils.py display_for_field`, although the title says "Admin" | sweep (refine) | medium |
| django-14376 | [51] its grep at [6] listed `client.py`; it rules it "not part of this issue" | sweep | medium |
| pylint-4970 | [23] guards the checker after [13]-[14] showed the tests drive the stand-alone `similar.Run` | sweep | medium |
| sphinx-11445 | [20] hard-codes a list of docinfo names; at [151] rewrites `test_prepend_prolog` after seeing it fail [127] | tests | medium |
| django-16950 | [224] 4 `test_uuid` failures describe the reference's condition; [262] reverts the near-correct fix, [438] rewrites 5 assertions; a constraint seen at [377] is lost in the compaction at [379] | tests (context) | medium |
| django-15957 | [148] names the per-category need at [147], then picks a global slice; [287] claims that is "what Django upstream implemented" | refine (contract) | medium |
| django-13212 | [44] leaves out `DecimalValidator` "consistent with upstream", having listed every raise site at [14] | sweep | low |
| django-15629 | [31] collation added to `db_parameters` only; the schema editor's FK rebuild is never read | sweep | low |
| django-16502 | [60] fixes `WSGIHandler` though the issue names runserver and it read `basehttp` at [10]; HEAD also needs header changes | sweep / contract | low |
| sympy-23413 | [21] wrong loop bound; [96] calls new failures "pre-existing"; [112] `git checkout` loses the work; [166] accepts a mismatch "up to row order" | contract (tests: compare by name) | low |
| django-15563 | [19] blames the filter column; the repro at [59] still shows the bug; ends mid-thought | nudge (refine) | low |
| django-11885 | [21]-[53] per-model `where` merge; first test run only at [133]; ends mid-sentence | nudge (refine) | low |
| django-12325 | [22] keeps a fallback that makes a plain OneToOne the parent link; 2 compactions, then trusts the handoff [208] | refine | low |
| django-11790 | [48] `maxlength` becomes the string `'254'`; rendered HTML is right, the test checks the type | U | low |
| django-12406 | [37] fixes `ForeignKey.formfield`; the test passes a new `blank=` argument | U | low |
| django-15252 | [46] does what the issue proposes (router gate); the test wants no `ensure_schema` for an empty plan | U | low |
| sympy-13798 | [26]-[29] pads the symbol, following the issue's own example; the test wants it verbatim | U | low |
| sympy-13974 | [80] integer powers only; the test uses a symbolic exponent | U | low |
| sympy-17318 | [80] guards the callee; the test asserts an internal return value | U | low |
| sympy-22080 | [190] lowers Mod's precedence instead of fixing unary minus; the test changes existing `ccode` outputs | U | low |
| pylint-4661 | [26] XDG path as the issue says; the test expects `appdirs`, named only in the hints | U | low |
| sklearn-25747 | [23]-[25] guards the override; the test needs it removed, which the issue does not imply | U | low |
| sklearn-26194 | [19] `max + eps` as the issue suggests; the test wants `inf` | U | low |

**Corrections to §2 and §3.** Matplotlib 14623 is worse than "second path missed": the patch broke linear-axis inversion, and the issue's own example showed it. Django prints the next test's name on the line of a failing subTest, and the log parser then marks that next test failed too, so `test_clean_model_instance` (12406), `test_in_bulk_with_field` (12774) and `test_label_for_field` (13512) in the grading are parser artifacts, not regressions. No outcome changes. 12406's test edit did not leak.

**Patterns the classification did not show:**
- **The run's own output contradicted it and it carried on, in 9 of 32:** 14623, 13615, 15037, 15563, 15957, 16877, 16950, 16100 (it rewrote an assertion its own stash run had shown passing) and 23413. This is the strongest pattern. It is cheaper to act on than wrong-file failures, because the evidence was already on screen.
- **It found the right place and talked itself out of it, in 6 of 32:** 12193 [14], 14376 [6], 4970 [13], 16502 [10], 13615 [4] and 18211 [54].
- **Remembered "upstream fixes" in 8 of 32:** 12193, 13212, 11790, 12325, 12406, 11885, 15957 and 16100. A fix that does not exist was cited and then built to. It decided 12193 and 13212. The web commands were called 94 times in 41 of 100 tasks (15 of 32 failed, 26 of 68 resolved), and every call failed for lack of a network. The prompt's web block asks for them.
- **The "pre-existing" dismissals the deep read followed up were mostly checked properly.** They were compared by name with `git stash` in 13212, 15037, 15252, 16100, 4970, 4661, 26194, 13798, 17318 and 22080. The exceptions are 14623 (`tail -6`, 2 of 183 names compared) and 23413. Comparing by name would flip at most those two.
- **Compaction did harm in 3 runs:** a constraint was lost (16950), work was reverted and rebuilt from the summary (23413), and a handoff was trusted (12325).
- **10 of 32 are U**: the hidden test pins a choice the issue does not imply. No prompt rule reaches them.

### 9.2 What the 68 resolved runs say about risk

| Behaviour a candidate would change | Resolved (68) | Failed (32) | Consequence |
|---|---|---|---|
| Patch removes lines from an existing test | 13 | 8 | In 11 of the 13 resolved runs (11276, 11848, 15315, 20154, 9698, 8265, 13028, 13236, 25102, 16938, 11206) the reference test patch changes the same assertions. These issues *ask* for the behaviour change that an old test pinned, as the issue texts of 11276, 11848, 15315, 20154 and 9698 show. |
| Test patch changes existing assertions (the correct fix makes an old test fail) | 19 | 15 | The night-1 rule says "If a test that passed before your change fails after it, your change is wrong". That is false for these 19, and it could talk the agent out of a correct fix. The failures 26194, 4661, 25747, 12325 and 23413 are the same shape. |
| Changes more source files than the reference | 8 | 4 | This is the exposure of a sweep rule: a sibling changed that should not be can break PASS_TO_PASS. |
| Changes fewer source files than the reference | 1 (sphinx 10673) | 10 | A missed second file is almost only a failure. |
| Messages mention "upstream" | 33 | 22 | 14539 was resolved by applying a remembered "canonical fix", so banning memory is a risk. Only the web calls are pure waste. |
| Any test run piped through `tail`/`head`/`grep` | 68 | 31 | It is universal, and it decided one task (14623). Forbidding it is not worth the context. |
| `code_callers`/`code_impact`/`code_refs` used | 57 | 22 | In no failure did these tools move the agent to a new file. In 13615 and 15957 they showed the reference's code and the agent did not act on it. 12193's `code_impact` returned 0. |
| One or more compactions / three or more | 25 / 4 | 15 / 4 | Compaction follows difficulty. It harmed 3 failures. |

### 9.3 The candidates, ranked

*Expected* sums the per-task confidences of §9.1 (high 0.65, medium 0.4, low 0.15; a secondary lever counts half), then halves for a 50-task fresh list of the same mix. Every gain here is below the about 7 discordant tasks a McNemar test needs on 50 (§6.5 item 4). Each arm is therefore decided by its **behaviour measure** first and its resolve count second.

| Rank | Candidate | Targets (from §9.1) | Expected on 100 / on 50 | Exposure in the 68 | Arm and what it needs | Exists? |
|---|---|---|---|---|---|---|
| 1 | **Contract and sweep task rules.** Run the issue's example last and match its expected output exactly. Use the function or result type the issue names. Do not widen a condition beyond the case asked for. Change every sibling implementation and the stand-alone entry point of the behaviour you changed, and never rule one out of scope | contract: 13615, 14623, 12193, 18211, 12774, 15037, 15957, 23413; sweep: 13512, 14376, 4970, 16877, 13212, 15629, 16502 | about 5.3 / **2.5-3** | Low for contract: running an example costs a turn. 13798 is the counterexample, where the issue's example was itself wrong. The sweep's exposure is the 8 runs that already edit extra files | `--task-rules <record>,contract` on the record arm. Measures: the issue's example run after the last edit; final patch ∩ the sibling set of §9.1; failures classed "second path"/"not asked for" | **Yes since 2026-10-09**, as `--task-rules issue-v1` (not `contract`): see below the table |
| 2 | **`tests-v2`.** Never edit an existing test to make it pass. If one fails after your change, decide from the issue whether the issue asks for the behaviour that test checks. If it does, leave the test and say so; if not, fix the source. Compare failing tests by name with and without your change (`git stash`) | 16100, 11445, 16950 (and 13837 with the harness) | about 1.5 / **0.75**, the same as v1 | v2 removes v1's exposure, the 19 resolved runs whose correct fix fails an old test | `--task-rules tests-v2` against `tests`. Measures: assertion edits where the issue did not ask for the change; correct fixes reverted after an old test failed | **No**: a second `TASK_RULES` entry. Gated on night 1 (§9.4) |
| 3 | **A review turn.** One resume after the agent stops: "Re-read the issue and `git diff`. Does the issue's example now print what the issue expects? Is every place that implements this behaviour changed? Then stop with a summary." | 15037, plus half of 12193, 12774, 4970, 13615, 13974 | about 1.0-1.5 / **0.5-0.75** after rank 1 | All 68: a second look can undo a correct fix. Plus about 1-2 minutes per task | `[swe_bench] review = true`. Measures: patches changed by the review turn; resolved turned unresolved | **Yes** (2026-10-09): `swe-bench run --review-turn`, one resume outside the `nudges` budget, with the user's review-and-test prompt (re-read the issue, read the diff, run the changed modules' tests, fix what does not hold); [MIGHTLING_SWE_BENCH §19](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) |
| 4 | **Grading fixes (drop test hunks, per-file reset)** | 13837 | about 0.65 / 0.3 | None (grading only) | Already in night 1: `eval --drop-test-hunks` regrades every arm with no agent run | **Yes** (§8) |
| 5 | **Offline system prompt.** `default` without the web and email blocks, which describe commands that cannot work in the container | 13212, 11790 (half); turns saved in 41 tasks | about 0.15 / **0-0.1** | Lowest of all: the web calls fail anyway | `--prompt offline`, from `$CODEX_HOME/system-prompts/offline.md`. Measure: web calls (94 in 41 tasks here) go to 0; median turns and wall time | Mechanism yes (custom prompts); the file **no** |
| 6 | **Larger context budget.** `task_context` 49,152 → 98,304 | 16950, 23413, 15957 (half each) | about 0.5 / 0.25 | Low for correctness. Fewer tasks run at once, because the host sizes parallelism from it, so a night holds about one arm | `[swe_bench] task_context = 98304`. Measure: compactions per task; constraints lost after one | **Yes** (config only) |
| — | Completion nudge | 11885, 15563 | about 0.3 | Fires on 6 of 219 earlier runs, none a summary | In every night-1 arm; read its firings | **Yes** |
| — | Refine on fresh tasks | 15957, 12325, 13512, 11885, 15563, 15629 | about 1 by this reading; +3 of 24 measured on the sample | About 4× the agent time (one arm per night) | `--refine` | **Yes**. Whether it gets a night depends on `im100-refine`, which runs on the 100 and so is analysis, not a measurement |

**Rank 1 is built as `issue-v1` (2026-10-09).** One `TASK_RULES` entry combines the contract and the sweep: before editing, read the whole issue and work out exactly what behaviour it asks for, including the function, type or path and the edge cases it names, and do not widen a condition; find the code nearby that does the same thing and follow its pattern, fixing a sibling with the same defect; and run the issue's example after the last edit and check its output. The sweep is worded as "follow the sibling's pattern; fix a sibling with the same defect" rather than "change every sibling", because of its exposure in §9.2 (8 resolved runs already change more files than the reference). It stacks with `tests-v2` (`--task-rules tests-v2,issue-v1`, `issue-v1`'s lines first). Text and naming: [MIGHTLING_SWE_BENCH §15](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md).

**Rank 1 strengthened: enforced, not only asked (2026-10-09).** The agent survey's first item ([research/agent-survey-2026-10-09.md §3.1](./research/agent-survey-2026-10-09.md)) found that the same conditions as a paragraph did worse than no guidance, and enforced through upstream Codex's hooks did better than either. `swe-bench run --hooks issue-v1` adds that enforcement to two of `issue-v1`'s lines: the first edit is denied once until the files and functions the issue names have been read, and the first stop is blocked once if the issue shows an example and no command since the last edit has run it. Conditions are built by rule from the issue and resolved against the repository when the task starts (what does not resolve is dropped); each hold fires at most once per task, so a wrong condition (13798's wrong example) costs a turn and cannot trap the run. The targets of §9.1 it is meant for are those where the agent had the evidence: example not run (13615, 14623, 23413), named and then dropped (12193, 18211). Measures, per task and in the report: whether each hook fired, what it held, whether the agent then complied, the named targets read before the first edit, and the example run after the last edit. Arm: `--task-rules issue-v1` against `--task-rules issue-v1 --hooks issue-v1`. [MIGHTLING_SWE_BENCH §20](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md).

**Not proposed, on this evidence:**
- **A rule against recalled upstream fixes.** It decided 12193 and 13212, but 14539 was resolved by one. Removing the web block (rank 5) and the contract line on what the issue names (rank 1) cover the harm without the ban.
- **More code-index use.** The tools never led a failed run to a file it was not already reading, and in 13615 and 15957 `code_show` showed the reference's code and the agent went elsewhere. The problem is acting on what was found, which rank 1 targets.
- **"Don't pipe test output."** 99 of 100 runs pipe; one task turned on it, and compare-by-name in `tests-v2` covers that one.
- **A longer time limit or a sympy runner hint.** As §6.6 says: one timeout, with a wrong approach; `pytest` missing decided no outcome.

### 9.4 Order of the nights

Night 2 is the 4-bit model A/B, and its winner becomes the model of every later arm. Every night below runs on `fresh-50.txt`. That is clean, because every candidate here comes from the 100, and comparable, because each night uses the same list. Each night carries its own *record arm*: the configuration of record re-run that night, which absorbs drift between nights and, from night 3 on, gives the in-session noise floor of §6.5 item 3.

| Night | Arm A (record) | Arm B | Decided by |
|---|---|---|---|
| 1 (prepared) | `default` | `--task-rules tests` | assertion edits, test-patch collisions, nudges fired; both regraded with `--drop-test-hunks` |
| 2 (reserved) | current model | all-4-bit model | resolve rate, speed |
| **3** | record (night 1's winner on night 2's model) | record + `contract` (rank 1) | example run after the last edit; second-path misses |
| **4** | record | record with `tests-v2` in place of `tests` (rank 2), *if* night 1's `tests` arm lost a task whose issue asked for an assertion change, or reverted a correct fix after an old test failed. Otherwise the review turn (rank 3) | assertion edits not asked for, and fixes reverted / patches the review changed |
| **5** | record | the one of rank 2 or 3 not run on night 4 | as above |
| **6** | record | `--prompt offline` (rank 5), or `--refine` if `im100-refine` favours it (one arm takes the night, so night 6's record is night 5's arm A) | web calls, wall time / resolve rate |

Rank 6 (context) goes last, or is folded into a night that already runs one arm, because it roughly halves throughput.

**Changed on 2026-10-09:** night 1's arm B is `--task-rules tests-v2`, not `tests` (runs `n1-default` and `n1-tests-v2`; [MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md), "Night 1 runs `tests-v2`"), so rank 2 is measured on night 1 instead of night 4. The table above is as it was written before that change.

**Night 3's arms are decided after night 1 (2026-10-09).** Rank 1 is available as `--task-rules issue-v1`. Whichever arm of night 1 wins is night 3's record arm: if `default` wins, night 3 is `default` against `--task-rules issue-v1`; if `tests-v2` wins, it is `--task-rules tests-v2` against `--task-rules tests-v2,issue-v1` (runs `n3-tests-v2` and `n3-tests-v2-issue-v1`). Either way on night 2's winning model.

**The enforced form of rank 1 is available for a later night (2026-10-09).** `--hooks issue-v1` holds the first edit and the first stop once each (§9.3, below the table). Its test is the record arm with `--task-rules issue-v1` against the same with `--hooks issue-v1` added, decided by the behaviour measures the report prints (holds fired and complied, named targets read before the first edit, the example run after the last edit) before the resolve count.

**The list stays fresh only while nobody designs a rule from its transcripts.** Once night 1's or a later night's failures on `fresh-50.txt` are analysed, later measurements need a second list. `scripts/swe_bench_fresh.py draw --exclude docs/dev/swe-bench/fresh-50.txt` (built 2026-10-10; repeatable) draws it from the validated tasks outside `sample-100.txt` and every list named, and the header names each excluded list. There are 120 validated tasks, 100 of them in the sample. Night 1's validation adds about 60, and about 211 arm64 tasks would still be unvalidated.
