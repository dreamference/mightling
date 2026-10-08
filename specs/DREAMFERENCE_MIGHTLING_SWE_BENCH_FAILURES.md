# Mightling on SWE-bench: why 32 of 100 tasks failed (`im100-default`)

**Status:** analysis, 2026-10-08. Nothing here is built. §6 proposes four changes, each to be measured as an A/B on 50 tasks that are not among these 100.
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
