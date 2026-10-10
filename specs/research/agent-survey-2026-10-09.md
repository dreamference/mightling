# Agent survey: papers and issue trackers for `ling` (2026-10-09)

**Status:** research survey, nothing built. Read-only: no agent was run, no model server was called, nothing was written to any tracker.
**Question:** what in the last twelve months of research and of the trackers of upstream Codex, Cline, Continue, OpenHands and Aider could raise `ling`'s resolve rate on SWE-bench Verified or help its users, given a 27B model served locally on one GB10.
**Reads with:** [MIGHTLING_SWE_BENCH_FAILURES §9](../DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md) (the ranked fix list this survey maps onto) and [MIGHTLING_SWE_BENCH §15–§19](../DREAMFERENCE_MIGHTLING_SWE_BENCH.md) (`tests-v2`, `issue-v1`, the fresh lists, the model gate, `--review-turn`).

**Short answer.** The strongest outside evidence is for making the agent's checks *enforced* rather than *asked for*. A paper that put an evidence gate in front of upstream Codex through its hook mechanism gained 5 to 10 points on Verified. In the same paper, a plain-language version of the same conditions did worse than no guidance at all. A study of 25,000 agent rules found that negative constraints help and positive directives hurt, and that most of the gain of a rule file is priming, not content. That changes how night 3 (`issue-v1`) should be read and suggests a placebo arm. Both mechanisms `ling` needs, `PreToolUse` with `deny` and `Stop` with `block`, are already in the pinned Codex (`codex-rs/hooks/schema/generated/`), so none of the top items needs a patch. Second come design changes to arms that already exist: a backward check in the review turn, a claim check in refine, a restart instead of a third compaction, and grading the intermediate patches to see how often the agent had the fix and lost it. Two findings explain local nulls rather than add work. Retrieval tuning shows no effect once the agent may read files freely, which matches the code index's null. And compaction policy matters mainly when the context budget is tight, which matches masking's 16 against 17. Three findings warn against things not yet tried: a "write a reproduction test first" rule (a model of this class wrote tests that *lowered* its resolve rate), self-review that re-reads under the same interpretation, and reading any one-night difference of under about 13 points on 50 tasks. A rerun of the same harness with Qwen3.8-27B flips 13% of hard tasks.

**Upstream Codex.** The next bump needs work. Since the pin, tool namespaces are sent to every provider, so patch `0020` must be re-hooked and must flatten more. Base instructions move into the request's input. And the bwrap selection changed in the one file patch `0019` hooks. Open upstream, a local provider's context overflow is not recoverable, which could end a long unattended task. Each has a check in §4.

---

## 1. Method and counts

| Source | Window | Screened | Read in depth |
|---|---|---|---|
| arXiv (API, 14 queries, four of them over several pages; scope list below) | submitted 2025-10-09 to 2026-10-09 | 1,241 unique papers returned; 848 passed a relevance filter (abstract or title names SWE-bench, a coding or code agent, GitHub issues, issue resolution, repository-level work or program repair) and all 848 titles were read; 75 abstracts read | 40 full texts (arXiv HTML: abstract, conclusion, and the method, ablation and model passages); 2 more at abstract level where no HTML exists |
| Upstream Codex issues and PRs | 2025-10-09 to 2026-10-09; changes since the pin `rust-v0.158.0` (2026-09-28) to `rust-v0.162.0` (2026-10-08) | 34,517 items created in the window (31,476 issues, 3,041 PRs); 521 commits between the pin and the latest release | 31 (18 issues, 13 PRs; 30 of them linked in §4) |
| Cline, Continue, OpenHands, Aider issues | created 2025-10-09 to 2026-10-09 | 7,676 issues (2,321 / 2,910 / 1,699 / 746), of which 1,721 match a local-model keyword set and 928 name a local runtime | 21 issues and 7 PRs |
| **In all** | | | **99 items**: 40 papers, 31 upstream Codex items, 21 issues and 7 PRs of the other agents (more than the roughly 40 planned, because the two tracker surveys ran in parallel) |

**arXiv queries.** The API was queried with `abs:` terms plus `submittedDate:[202510090000 TO 202610092359]`, 3.5 s apart: SWE-bench; software-engineering agents with open, small or local models; coding agents and repositories; issue and bug reproduction; test generation for issues; verifiers, critics and self-review on SWE tasks; patch selection, ranking and test-time scaling; compaction, context management and observation masking; tool design and agent-computer interfaces; code retrieval and code graphs; trajectory and failure analysis; memorization and contamination; fault localization; SWE work with 27B- to 32B-class and open-weight models. §7 gives them as saved queries.

**GitHub.** Everything was screened through the REST API's issue listing, filtered locally, and not through the search API, whose quota other work on this machine was using. Every issue and PR linked below was opened (`gh issue view` / `gh pr view` with comments, or the API object). Every arXiv link is a paper whose text was fetched for this survey.

**How items are weighed.** Each item is scored against FAILURES §9.1's patterns, not against the paper's headline:

- the run's own output contradicted it and it carried on (9 of 32);
- it found the right place and talked itself out of it (6 of 32);
- a fix was recalled that does not exist (8 of 32);
- compaction harm (3 of 32);
- **U**, where the hidden test pins a choice the issue does not imply (10 of 32, which no prompt reaches).

Where a paper's claim collides with a local measurement, the local measurement wins and the item says what would tell the two apart. Effects are **estimates** in §9.3's currency (tasks on the 100 / on a 50-task fresh list), from §9.1's confidences where an item targets named tasks. Every one of them is below the roughly 7 discordant tasks a McNemar test needs on 50, so every "how to test" names a behaviour measure first.

**What "applies" means here.** The model is Qwen3.8-27B NVFP4 on SGLang. The roughly 4 GB of host headroom matters only for things run on the GB10. It runs at 25 to 50 tokens/s single-stream, four concurrent tasks are practical, and no second model is available (ADVISOR_NODE is on hold). A method that needs training, a second model or eight samples is marked as such.

---

## 2. The ranked table

Ranked by expected gain per cost for SWE-bench resolved, with user impact as the tie-breaker. "Est." marks an estimate. Rank numbers in the last column refer to FAILURES §9.3.

| # | Item | What it says | Applies to a 27B local model on one GB10? | Expected effect (est.) | Cost | How to test |
|---|---|---|---|---|---|---|
| 1 | **Evidence gate before edits and before stopping.** ECLoop, [arXiv 2607.28815](https://arxiv.org/abs/2607.28815) | Compile, from the issue and repository, what must be observed before an edit or a submission; track it from the trajectory; hold an action whose conditions are unmet. +4.8 to +11.8 points on all 500 Verified, including **+5.0 and +10.4 inside upstream Codex through its hook mechanism**. Ablation (100 tasks): the same conditions given as a natural-language summary scored 58, below no guidance (63), against 68 with the gate. Post-hoc self-refine *lowered* scores | Yes: hooks exist at the pin (`PreToolUse` `deny`, `Stop` `block`). Conditions can be built by rule from the issue text (named paths, functions, the example block), with no extra model call. Tested on mid-size API models, not a 27B | +1 to +2 on 100 over `issue-v1` as a prompt (est.); 0.5 to 1 on 50 | Small to medium: one hook script in the runner, no patch | Night arm `issue-v1` against `issue-v1` + gate. Measures: the issue's example run after the last edit (target: every task that has one), edits held, named files opened before the first edit |
| 2 | **Rules at the moment they apply.** LivePlan, [arXiv 2608.06701](https://arxiv.org/abs/2608.06701); Cadence, [arXiv 2610.12269](https://arxiv.org/abs/2610.12269) | A deterministic monitor over the trajectory decides *when* to intervene, and only then is advice given. +9.9% on average over SWE-agent, with few regressions. Cadence (Lite, adaptive inspection) reports larger gains | Yes, as a `PostToolUse` hook returning `additionalContext`. The advice can be fixed text (`tests-v2`'s sentences), so no advisor model is needed | +1 to +1.5 on 100 (est.), on 16100, 11445, 16950, 14623 and 23413 | Small: same hook script | Arm: `tests-v2` up front against `tests-v2` delivered when an existing test file is edited after a failing run. Measures: assertion edits the issue did not ask for; "pre-existing" claims made without a by-name comparison |
| 3 | **Negative wording and a placebo arm.** [arXiv 2604.11088](https://arxiv.org/abs/2604.11088) (rules study); [arXiv 2602.11988](https://arxiv.org/abs/2602.11988) (context files); [arXiv 2607.28887](https://arxiv.org/abs/2607.28887) (deletion avoidance) | 25,532 rules, 5,000+ runs. Every individually helpful rule was a prohibition and every harmful one a positive directive. Random rule files gained as much as curated ones (+13.8 points on a discriminative subset), so the gain is mostly priming. Context files did not raise success and added about 20% cost. Separately, 29% of passing Verified patches wrap the faulty code in a guard instead of changing or removing it | Partly: measured on one frontier model in a vendor's agent. The mechanism (priming, polarity) is untested on Qwen | 0 to +1 on 100 (est.); its main value is reading night 3 right | Trivial: `TASK_RULES` entries | A placebo block of `issue-v1`'s length beside `issue-v1`; an `issue-v2` worded as prohibitions (below). Measures: as #1, plus patches that add a guard and remove nothing |
| 4 | **Refine with a claim check.** TrajSpec, [arXiv 2607.07882](https://arxiv.org/abs/2607.07882); [arXiv 2607.09553](https://arxiv.org/abs/2607.09553) | A trajectory-derived refined issue, then a repository-based review that deletes unsupported claims and revises uncertain ones. Lite: 41.0 → 59.7 (GPT-5-mini), 54.7 → 64.3 (MiniMax-M2.5). Without the review step, 59.7 → 48.0. Separately: localization cues and suggested fixes in an issue predict success; reproduction steps matter least | Yes: `--refine` exists; the check is one more read-only pass | +1 to +2 on 100 over `--refine` (est.); targets the recalled-fix pattern (12193, 13212) and refine's misplacements (16502) | Small; adds about 20% to refine's 4× agent time (est.) | `--refine` against `--refine` + check on a fresh list. Measures: claims removed; "upstream" assertions left in the refined text; second-path misses |
| 5 | **A backward check in the review turn.** RETRACE, [arXiv 2608.08950](https://arxiv.org/abs/2608.08950); [arXiv 2610.01023](https://arxiv.org/abs/2610.01023); [arXiv 2607.24604](https://arxiv.org/abs/2607.24604) | From the patch alone, *without the issue*, write what problem it fixes, then reconcile that with the issue. +7.0 (GPT-5-mini) and +3.6 (MiniMax-M2.5) on Verified. A review that re-reads under the same interpretation catches little. Weak reviewers are reliable only with decisive evidence. Forced revision of correct code harms it (0.82 → 0.67 correct after a second revision, 14B models) | Yes: a fresh `ling exec` with the diff only, about a minute | +0.5 to +1.5 on 100 over the plain review turn (est.) | Small: one short fresh session per task | `--review-turn` against `--review-turn` with the backward description. Measures: patches the review changed; resolved-before / unresolved-after (grade `patch-before-review.diff`) |
| 6 | **Checkpoint every edit; grade the checkpoints.** Coherence collapse, [arXiv 2603.24631](https://arxiv.org/abs/2603.24631) | In 60 to 69% of failures (SWE-agent, OpenHands) the agent reached and edited the right functions. 5 runs held a patch bit-identical to the reference mid-run and destroyed it later; a checkpoint recovered all 5 | Yes: snapshots are `git` objects in the container; grading needs no agent | Diagnostic first. If "ever resolved, finally not" is 3 or more on 100, a checkpoint-selection rule is worth an arm (est.) | Small: a `PostToolUse` hook or runner step; grading time on the harness | Offline over night 1's runs: grade the last checkpoint at which the issue's example passed. Measure: ever-resolved minus final-resolved |
| 7 | **Restart instead of a third compaction.** FailFast-RestartSmart, [arXiv 2608.03222](https://arxiv.org/abs/2608.03222); handoff debt, [arXiv 2606.02875](https://arxiv.org/abs/2606.02875) | On **Qwen3.6-27B**, a monitor's alarm plus a fresh rollout offered the interrupted diff as an overlay: 66.6 → 71.8 on Verified. A cold restart reached only 66.8. Structured handoff notes cut a successor's events by 20 to 59% | Yes, with a rule trigger in place of the trained 0.6B monitor (no second model is available) | +0.5 to +1 on 100 (est.); targets 16950, 23413, 12325, 15252 | Medium: a runner feature, reusing the review turn's fresh-session path | First a replay count of how many failed and resolved runs would trigger (third compaction: 4 / 4 in the 100). Then an arm. Measures: restarts; resolved after a restart |
| 8 | **Mark misaligned tasks in the lists.** PAIChecker, [arXiv 2607.28587](https://arxiv.org/abs/2607.28587); [arXiv 2605.12270](https://arxiv.org/abs/2605.12270) (abstract read) | 13.6% of Verified has a PR that does not match its issue (5 patterns). 41.2% of the instances no leaderboard agent resolves are misaligned. Harnesses sometimes misjudge correct patches | Yes: this concerns the list, not the model | No resolve change; an A/B arm stops spending power on unrecoverable tasks (U: 10 of the 32) | Low if the authors' labels cover our IDs, otherwise one read-only session per task | Cross the paper's labels with our 10 U tasks; if they overlap, stratify `swe_bench_fresh.py draw` on them |
| 9 | **Measure at this model's noise floor.** [arXiv 2610.04433](https://arxiv.org/abs/2610.04433); [arXiv 2602.07150](https://arxiv.org/abs/2602.07150); [arXiv 2607.09691](https://arxiv.org/abs/2607.09691) | With **Qwen3.8-27B** (bf16, vLLM): 347 / 353 / 312 of 444 tasks in three harnesses, the two best equivalent. Reruns flip 13% of hard tasks, as often as swapping harness, and 45 tasks catch a 13-point gap only half the time. Every harness effect that cleared the noise was a *loss*: an output cap, a hard stop at the context window, a step cap, a search tool that cannot run offline. Elsewhere: 2.2 to 6.0 points of spread between single runs; 9% of outcomes flip at temperature 0 | Directly: the same model family and size | None by itself; it decides how the other arms are read | Agent time for reruns | Keep §9.4's record arm. Report per-task flips between record arms, and pass^2 where an arm is run twice. Look for losses first: timeouts, mid-sentence stops, web calls |
| 10 | **No "write a reproduction test first" rule.** ExecCritic, [arXiv 2609.09133](https://arxiv.org/abs/2609.09133); CoHarden, [arXiv 2607.19843](https://arxiv.org/abs/2607.19843); SWE-Doctor, [arXiv 2607.00990](https://arxiv.org/abs/2607.00990); [arXiv 2602.07900](https://arxiv.org/abs/2602.07900); [arXiv 2610.03984](https://arxiv.org/abs/2610.03984); [arXiv 2609.38812](https://arxiv.org/abs/2609.38812) | With Qwen-3.5-35B-A3B, the base model's own tests **lowered** resolve from 61.2% to 57.3%, and a frontier model's tests raised it to 65.3%. Fail-to-pass tests are often lax and admit wrong patches. Fail-to-fail tests mislead. Prompting for more or fewer tests did not change outcomes. An agent's own test for its own patch accepted most wrong ones (verifier precision 26.8%). Terminal agents detect 61% of their wrong candidates and repair 49% of those | Directly: a model of the same class | Avoids a loss of about 2 to 4 on 100 if such a rule behaved as in ExecCritic (est.) | None | Not an arm. If one is ever tried, use the issue's own example as the check, and count lax tests with mutated patches as CoHarden does |
| 11 | Validated or re-anchored compaction. Slipstream, [arXiv 2605.08580](https://arxiv.org/abs/2605.08580); [arXiv 2609.20804](https://arxiv.org/abs/2609.20804); [arXiv 2609.32961](https://arxiv.org/abs/2609.32961) | Asynchronous compaction validated against the agent's next steps: up to +8.8 points. Context management pays mostly under a tight budget, by preventing overflow. Rule-based elision before an LLM summary is the most efficient, and recoverable elision goes unused. The same policy helps some tasks and hurts others; fewer tokens can mean a slower run | Async compaction needs a Codex change. Re-injecting the issue verbatim after compaction does not: the ledger hook exists | +0.3 on 100 (est.), on 16950 and 23413 | Low (re-injection) to high (async) | Add the issue text and the agent's stated constraints to the post-compaction ledger. Measures: constraints lost after a compaction (read as in §9.1) |
| 12 | Retrieval: the recall trap. [arXiv 2608.14838](https://arxiv.org/abs/2608.14838) (abstract read); [arXiv 2607.09691](https://arxiv.org/abs/2607.09691) | A higher-recall retriever setting *lowered* single-shot resolve (Qwen3.6-27B: −3.6 points). The effect was **not detected when the agent could read files freely** (a powered null). Given the right location, what an agent needs is the code it edits: summaries and skeletons of the rest add nothing | Explains the local nulls (§9.2: the index never moved a failed run to a new file; `--strip-names` 14 against 17) | None for SWE-bench; keep the index for users | None | No new arm. Stop A/B-ing index variants on SWE-bench |
| 13 | Memorized benchmarks. [arXiv 2512.10218](https://arxiv.org/abs/2512.10218); [arXiv 2609.27891](https://arxiv.org/abs/2609.27891) | Models find Verified's edited files from the issue text alone 6× more often than on comparable newer sets. Semantics-preserving renaming and reordering of the repository drops resolve by 6 to 14 points | Yes: Qwen3.8 was trained after Verified was public | None. It is why "remembered upstream fixes" occur (8 of 32) and why Verified scores overstate | Low: `--strip-names` already does the issue-side half | Read the 8 recalled-fix runs against what the model "remembers"; report Verified results as optimistic |
| 14 | Test-time scaling with pairwise selection. [arXiv 2603.04304](https://arxiv.org/abs/2603.04304); SWE-Replay, [arXiv 2601.22129](https://arxiv.org/abs/2601.22129); [arXiv 2603.24631](https://arxiv.org/abs/2603.24631) (consensus) | Pairwise self-verification beats pointwise scoring (up to +10% Pass@1). Replaying archived trajectories cuts cost by 17% at equal score. Consensus across samples gave +3.0 points (p = 0.08) | Only at k ≤ 4 (four tasks at once fit); selection without hidden tests is weak (#10) | Unknown; +1 to +3 at k = 4 (est., low confidence) | 4× agent time, the same as refine | Not before #1 to #7. If tried: k = 2 on 50, pairwise choice by a fresh session |
| 15 | Tool architecture. [arXiv 2608.11386](https://arxiv.org/abs/2608.11386) | Structured low-level tools raised consistency across attempts by up to 4.7×. Natural-language search reached 11% more relevant files. Scratchpad "thinking" tools did nothing | Partly: `ling` is Codex's shell and `apply_patch` | Small, unmeasured | Medium | None now |
| 16 | Self-verification distillation. [arXiv 2609.38812](https://arxiv.org/abs/2609.38812) | A teacher's verification and recovery, distilled on the student's own candidates, gave +9.7 to +16.9 points on TerminalBench 2.1 for Qwen3.5 backbones up to **27B**, without the out-of-distribution loss of full-trajectory distillation on Verified | Needs training and a teacher | Possibly large; outside this survey's horizon | High | Note for a future fine-tune (SELF_SPEEDING, ling-engine) |
| 17 | Cost of habits. [arXiv 2609.30725](https://arxiv.org/abs/2609.30725) | Re-retrieving what was already read, near-identical scripts and re-running the same tests affect 79 to 98% of tasks and up to 23% of cost. Hand-written skills cut cost by up to 42%; skills the agent writes itself do much less | Yes, for users' wall time | Time, not resolve | Low: one skill | A `ling` skill on test re-runs; measure wall time on Night Shift |

Papers read in depth but not ranked:

- **Agents ignore what they find.** [arXiv 2604.17609](https://arxiv.org/abs/2604.17609): an injected solution is discovered in about 80% of runs and used in 37 to 50%. This is FAILURES §9.1's "found it and talked itself out", and it supports #1's gate over more prompting.
- **Silent semantic failures.** [arXiv 2603.25764](https://arxiv.org/abs/2603.25764): repeated runs repeat the same wrong fix, and pre-edit prompts did not close the gap. This supports #9's per-task view.
- **Agentic review.** [arXiv 2607.06065](https://arxiv.org/abs/2607.06065): an agentic reviewer that explores and runs the reproducer beats single-turn review. That is the review turn's design already.
- **Context contamination on retry.** [arXiv 2605.08563](https://arxiv.org/abs/2605.08563): a theoretical model with one SWE-bench fit. It supports #5's and #7's fresh sessions, but its evidence is thin.

---

## 3. The top ten, in depth

### 3.1 An evidence gate before edits and before stopping (strengthens rank 1, `issue-v1`)

**Evidence.** ECLoop (2607.28815) is the one paper here that measured a gate *in upstream Codex* (v0.144.4), through hooks and without touching the model or tools. The ablation answers the question night 3 asks. The same conditions as a natural-language paragraph scored 58 of 100, below no guidance (63); enforced, they scored 68, against 47 for the bare agent. Its regressions (9 to 16 of 500) came from the fallback that releases a held action after three holds. The rules study (2604.11088) and the "agents ignore" study (2604.17609) point the same way: what helps is a constraint the run cannot pass by, not advice. In the latter, agents discovered an injected solution and then did not use it.

**What `ling` would do.** A hook script the SWE-bench runner registers. It works by rule, with no model call:

- **Before the first `apply_patch` to a source file:** every repository path and every identifier in the issue that resolves to a definition must have been opened (`cat`, `sed -n`, `rg` output that covers its definition, or `code_show`). If not, `deny` with the list of what is still unread.
- **Before stopping** (`Stop`, `decision: block`): if the issue has a code block that runs, some command after the last edit must have run it, so the stop is blocked once with that instruction. A second stop always passes, so a wrong example (13798) cannot trap the run.
- **Optional, for the sweep:** before the first edit to a function, a search for its name across the repository must have run. That is the cheap form of ECLoop's "related implementations" condition.

**Mapped to §9.1.** It is meant for the cases where the agent had the evidence:

- **example not run or not compared:** 13615, 14623, 23413;
- **named and then dropped:** 12193, 18211;
- **seen and ruled out of scope:** 14376, 4970, 16502, 13512;
- 15037 is not reached (it ran its example and misread it).

It adds enforcement to `issue-v1`; it does not replace it.

**Risk and exposure.** Held edits cost turns. Conditions built from issue text can name things that do not exist; ECLoop drops conditions it cannot resolve, and so must this. It needs checking that hooks are registered in the benchmark container (the launcher registers them in `config.toml` with trust entries, as for the ledger and refine).

**Test.** Night arm: the record arm against the record arm plus the gate, on the same fresh list.

- **Behaviour first:** the example run after the last edit in every task that has one, named definitions opened before the first edit, and holds per task.
- **Then resolve:** a gain of 2 or more on 50 together with the behaviour change is a pass. A gain with no behaviour change is noise.

### 3.2 Rules at the moment they apply (strengthens rank 2, `tests-v2`)

**Evidence.** LivePlan (2608.06701) separates *judging*, which a deterministic monitor does over the trajectory, from *advising*, and intervenes only on a detected drift: +9.9% on average across three executors, with few regressions. Cadence (2610.12269) adapts how often it inspects. Both say an always-on instruction is weaker than one delivered when the situation arises.

**What `ling` would do.** A `PostToolUse` hook adds `tests-v2`'s two sentences as `additionalContext` at the moment they apply, and not before:

- (a) `apply_patch` touches a file under a tests directory that existed at base, after a test run in the same session failed;
- (b) the agent's message says "pre-existing" or "unrelated" without a `git stash` comparison in the session;
- (c) the same failing command is run three times.

**Mapped to §9.1.**

- (a) is 16100, 11445 and 16950, the three runs that rewrote an assertion after seeing it fail;
- (b) is 14623 and 23413;
- (c) is the cheap part of LivePlan's loop detection.

This is the "own output contradicted it" pattern (9 of 32) at the moment it happens. It is new as a delivery mechanism; the content is rank 2's.

**Test.** An arm with `tests-v2` up front against `tests-v2` just in time.

- **Measures:** the assertion edits of §9.2 the issue did not ask for, and "pre-existing" claims without a by-name comparison.
- **Also check:** the 19 resolved tasks whose correct fix fails an old test must not lose their fix, which is read from the transcripts.

### 3.3 Negative wording and a placebo arm (qualifies rank 1)

**Evidence.** 2604.11088 made over 5,000 agent runs with 25,532 scraped rules.

- **Polarity:** every individually helpful rule was a prohibition ("do not refactor unrelated code") and every harmful one a positive directive ("follow code style").
- **Content:** random, shuffled and off-topic rule files gained as much as curated ones (+13.8 points on a discriminative subset), and pass rates held from 0 to 50 rules.

2602.11988 found that context files do not raise success and cost about 20% more. 2607.28887 found that 29% of passing Verified patches wrap the faulty code in a guard where the developer removed or changed it, a pattern it calls Guard-and-Go.

**Why it matters here.** `issue-v1`'s three lines are positive directives (read, find and follow, run). If the rules study holds for Qwen, night 3 cannot tell `issue-v1`'s content from the priming effect of any added block. Three of the "not asked for" or "wrong file" failures are Guard-and-Go: 25747 guards the override, 17318 guards the callee, and 4970 guards the checker. 16100 is related: it wraps the whole view.

**Proposal.** Two `TASK_RULES` entries, no code:

- **`placebo-v1`:** a block of `issue-v1`'s length about neutral matters, such as keeping commands short and quoting paths.
- **`issue-v2`:** `issue-v1` reworded as prohibitions:
  - "Do not stop before running the issue's example after your last edit."
  - "Do not rule a sibling implementation out of scope without opening it."
  - "Do not widen a condition beyond the case the issue names."
  - "Do not wrap the faulty code in a guard, `try` or fallback when the fix is to change or remove it."

**Mapped to §9.1.** It is the same set as rank 1 plus 25747 and 17318, which are U, so a low confidence.

**Test.** On the night after night 3: the record arm against `placebo-v1` and against `issue-v2` (two nights, or one night with two B arms if the night holds three arms).

- **Measures:** as #1, plus patches that add a guard and remove no line.
- **How to read night 3:** if `placebo-v1` matches `issue-v1`, night 3's gain is priming and the content is free to change.

### 3.4 Refine with a claim check (strengthens "Refine on fresh tasks")

**Evidence.** TrajSpec (2607.07882) is the closest published analogue of `--refine`. It collects a trajectory, organizes it into an interpretation, diagnostic findings and observations, drafts a refined report, and then reviews every claim against the pre-fix repository, deleting unsupported ones and revising uncertain ones. On all 300 Lite tasks: 41.0 → 59.7 with GPT-5-mini and 54.7 → 64.3 with MiniMax-M2.5. **Without the review step: 59.7 → 48.0**, a third of the gain. A related study (2607.09553) says what to keep: localization cues and suggested fixes are what agents use, and reproduction steps the least.

**What `ling` would do.** After refine's study session writes its six sections, a second read-only pass gets the refined text and the repository. For each claim it checks whether it can be confirmed in the repository by a command or a file read, and deletes it or marks it uncertain if not. A claim about an upstream fix or a later version counts as unsupported unless the code shows it.

**Mapped to §9.1.**

- **recalled fixes:** 12193 and 13212, which the check deletes;
- **refine's own misplacements:** 16502 ("Refine misplaced it the same way");
- **context harms:** 15957 and 12325 are refine targets in §9.3.

**Cost.** One more read-only session. The study step already runs about 4× the agent time.

**Test.** `--refine` against `--refine` with the check, on the same fresh list (one night).

- **Measures:** claims deleted per task; "upstream" or "later version" assertions in the refined text before and after.
- **Then:** second-path and wrong-file classes among failures.

### 3.5 A backward check in the review turn (strengthens rank 3, `--review-turn`)

**Evidence.**

- **RETRACE (2608.08950).** A forward pass writes the repair rationale from the issue and trajectory. A backward pass infers, *from the patch alone and without the issue*, the problem it addresses, then compares that with the issue. A reconciliation step decides between submitting and a targeted revision. Verified: 56.2 → 63.2 (GPT-5-mini) and 75.8 → 79.4 (MiniMax-M2.5). Both directions contribute.
- **Weak reviewers (2610.01023).** A smaller reviewer is reliable only when given decisive evidence. Unchecked structured evidence raised over-rejection along with catch.
- **Forced revision (2607.24604).** Forced revision harms correct code. With stale traces, 34 of 135 correct starts were harmed, against 4 of 135 with current traces.
- **ECLoop.** Self-refine lowered scores.

**Why it matters here.** The review turn as built resumes the same session, which is the "same interpretation" RETRACE says catches little. Its prompt already guards the 19 old-test cases.

**Proposal.** Before the review turn, a fresh `ling exec` is given only `git diff` and the names of the changed tests, and answers in at most five sentences: what behaviour did this change, and in which cases. The review turn's prompt gets that answer under "An independent reading of your diff says:" and is asked whether it matches the issue.

**Mapped to §9.1.** 15037 (its repro printed invalid code), 13615 (the output the issue calls wrong), and half of 12774 and 4970: rank 3's targets.

**Test.** `--review-turn` against `--review-turn` with the backward description.

- **Measures:** patches the review changed, and among them resolved-before / unresolved-after. The second needs `patch-before-review.diff` graded, which §19 lists as not built and which this needs first.

### 3.6 Checkpoint every edit and grade the checkpoints (new; diagnostic for ranks 3 and 6)

**Evidence.** Coherence collapse (2603.24631) splits 16,758 trajectories into reference-aligned search, read and edit stages. 60 to 69% of failures on SWE-agent and OpenHands reached and edited the right functions; the largest theme within them is reaching correct code and then overwriting it. In five runs a patch bit-identical to the reference existed mid-run and was destroyed, and an edit-commit checkpoint recovered all five. 2607.24604 shows the same on small repairs: "ever correct" rises with revisions while "currently correct" falls.

**Mapped to §9.1.** 16950 reverted a near-correct fix at [262], and 23413 lost its work to a `git checkout` at [112]. Both are this, and the review turn is exposed to it.

**Proposal.** After each `apply_patch`, the runner (or a `PostToolUse` hook) records `git stash create` as a ref in the container and keeps its diff in `scratch/<id>/`. Offline, grade the last checkpoint after which the issue's example still printed what the issue expects; if none, grade the checkpoint before the first compaction. No agent run is needed.

**Test.** On night 1's two arms. The measure is ever-resolved minus finally-resolved. At 3 or more on 100 (est.), a "return to the best checkpoint" rule earns an arm; below that, the review turn's exposure is small and needs no checkpointing.

### 3.7 Restart instead of a third compaction (new; targets the compaction harms)

**Evidence.** FailFast-RestartSmart (2608.03222) is measured on **Qwen3.6-27B**. A 0.6B monitor raises an alarm on a failing prefix, and the controller starts a fresh rollout with no prompt history and offers the interrupted diff as an overlay the agent may inspect, apply or discard. At a 25% false-positive target: 66.6 → 71.8. A cold restart without the overlay: 66.8. Handoff debt (2606.02875) adds that a successor given structured notes needs 20 to 59% fewer events than one given the repository only, with smaller and model-dependent effects on solving.

**What `ling` would do.** No monitor model is available, so the trigger is a rule: the third compaction, or a turn count beyond the 90th percentile of the 68 resolved runs. The runner stops the session and starts a fresh one in the same container. The new session gets the issue, the current `git diff` as a file it may apply or discard (not applied for it), and the ledger as notes. The review turn's fresh-session path (`REVIEW_FRESH_PROMPT`) is most of the code.

**Mapped to §9.1.** Compaction harmed 16950 (a constraint lost), 23413 (work reverted and rebuilt) and 12325 (a handoff trusted); 15252 ran out of time after 5 compactions. In the 100, three or more compactions happened in 4 failed and 4 resolved runs, so the exposure is 4 resolved runs that would be restarted.

**Test.**

- **First, a replay count:** how many runs in night 1 cross each trigger, failed against resolved.
- **Then an arm:** `--restart-after-compactions 3`. Measures: restarts, and resolved among restarted tasks against the same tasks in the record arm.

**Built (2026-10-10, branch `bench/restart-after-compactions`, [MIGHTLING_SWE_BENCH §21](../DREAMFERENCE_MIGHTLING_SWE_BENCH.md)):** the arm as `--restart-after-compactions N`, counting the fix session's compactions from its rollouts, with the diff handed over as a file (not applied) and one fresh session; the ledger as notes is v2. "Instead of a third compaction" is `N = 2`. The replay count comes first, from night 1's run directory (`scripts/context_budget_replay.py compactions`).

### 3.8 Mark misaligned tasks in the lists (new; harness, the U class)

**Evidence.** PAIChecker (2607.28587) read all 500 Verified instances. 13.6% have a PR that does not match the issue (five patterns, eleven scenarios), and 41.2% of the instances no leaderboard agent resolves are misaligned. 2605.12270's failure taxonomy (abstract read) adds that harnesses sometimes misjudge correct patches.

**Why it matters here.** 10 of our 32 failures are U, where the hidden test pins a choice the issue does not imply. No task rule reaches them, and on a 50-task list each U task is a slot that cannot move in either arm, so it costs power.

**Proposal.** Cross the paper's labels with our 10 U tasks. The paper says its data are public; this survey did not check that per-instance labels for Verified are included. If they agree, `scripts/swe_bench_fresh.py draw` gains a stratum (or an exclusion) for misaligned tasks, and every report counts them separately. If no labels are published, the cheaper check is the one §9.1 already did by hand, done once for the remaining validated tasks.

**Test.** Agreement between their misaligned set and our U set. No agent run.

### 3.9 Measure at this model's noise floor (strengthens §9.3's rule and rank 5)

**Evidence.** 2610.04433 ran **Qwen3.8-27B** (bf16, vLLM 0.27.1) through three harnesses on SWE-bench Verified, offline: 347, 353 and 312 resolved of 444. The first two are equivalent by McNemar; the third is worse because of an output cap. On 45 hard tasks, rerunning the same harness flipped 13% of tasks, as many as swapping harness, and the tasks a harness won in one run were not the ones it won in the next. At the observed discordance, 45 tasks catch a 13-point gap half the time. Every harness effect that cleared the noise was a way to *lose* a task: an output cap with no recovery, a hard stop at the context window, a crash at the step cap, a search tool that could not run offline. 2602.07150 (60,000 trajectories) finds 2.2 to 6.0 points between single runs, with divergence in the first 1% of tokens. 2607.09691 finds 9% of outcomes flip at temperature 0.

**Why it matters here.** It confirms §9.3 independently and with this model: one night's resolve count on 50 decides nothing below about 13 points. It also says where cheap gains are, in losses. Locally, those are:

- the 94 web calls in 41 tasks (rank 5, the offline prompt), which is their offline search tool;
- the 2 mid-sentence stops (the nudge);
- the 1 timeout;
- and one not yet seen here but open upstream: a context overflow a local provider cannot recover from (#48870 and #37138, §4.1), which would end a long task outright.

The reference figure (about 79% on 444 non-hard Verified tasks in a minimal harness, bf16) is not comparable with the 68 of 100 here (NVFP4, different tasks), but it says the model has headroom on this benchmark.

**Proposal.** Keep the record arm every night (already §9.4). Add to the report the per-task flips between consecutive record arms, and where an arm runs twice, pass^2. Order the losses list (timeouts, stops, web calls, `apply_patch` errors) before any new arm.

### 3.10 No "write a reproduction test first" rule (strengthens rank 1's example run; guards rank 2)

**Evidence.**

- **ExecCritic (2609.09133).** Used Qwen-3.5-35B-A3B, the closest model class here. The base model's own fixed tests *lowered* the repair agent from 61.2% to 57.3%; a frontier model's tests raised it to 65.3%. Only after role-specific training did the model's own tests help.
- **CoHarden (2607.19843).** Many fail-to-pass reproduction tests are "lax" and admit wrong patches, and co-generated tests share the patch's errors.
- **SWE-Doctor (2607.00990).** Fail-to-fail tests mislead and fail-to-pass tests lead to partial patches.
- **Prompting for tests (2602.07900).** Prompting for more or fewer agent tests changed cost, not outcomes.
- **Own-test verifiers (2610.03984).** An agent's test for its own patch accepted most wrong patches (precision 26.8%).
- **Self-verification (2609.38812).** Agents check nearly always, detect 61% of their wrong candidates and repair 49% of those.

**Why it matters here.** It would be natural to add "write a failing test first" next to `tests-v2`. On this evidence, with a model of this class, that is more likely to lose tasks than win them. The issue's own example is a check the issue's author wrote, not the agent, and that is the right granularity, which is `issue-v1`'s third line.

**Proposal.** Record this as not proposed in FAILURES §9.3's list. If test generation is ever tried, its tests are judged by mutation (CoHarden's lax/rigorous split), not by fail-to-pass alone.

---

## 4. Upstream Codex: what could bite `ling`, and what to take

Screened: 34,517 issues and PRs of the Codex project created in the window (31,476 issues, 3,041 PRs, 2,665 of them merged), filtered locally. The filters found 410 issues about local or third-party providers over the year; since July they found 75 about the Linux sandbox, 443 about compaction and context, and 705 about tool calls and exec. Since the pin, `rust-v0.158.0...rust-v0.162.0` holds 521 commits. Each change to a file a patch in `codex-patches/` touches was mapped to the first release that has it. Read in depth: 31 items (18 issues, 13 PRs), 30 of them linked below. The links go to the Codex project's tracker. "Open" means not fixed as of 2026-10-09, so `ling` at the pin has the bug.

### 4.1 Bugs that could bite `ling` now

| Item | What it says | Why it matters to `ling` | Check or test |
|---|---|---|---|
| Codex #48870 (open) | A local server's HTTP 400 `context_length_exceeded` is treated as an invalid request, so auto-compaction never takes its trim-and-retry path and the thread cannot recover | One large tool output near the window ends an unattended SWE-bench or Night Shift task: one of the "losses" of §3.9 | A `ling exec` scenario against a scratch server that cats a file over 100 KB near the limit; see what SGLang returns for an over-long prompt and whether the turn survives |
| Codex #37138 (open) | A `response.completed` without `usage` silently skips token totals, so auto-compaction never sees the growth | If SGLang's `/v1/responses` stream omits `usage`, #48870 follows on every long run | Capture one SGLang SSE stream (offline, with the benchmark not running) and assert `usage.input_tokens` is present; a launcher test over a recorded stream |
| Codex #47723 (open) | Qwen3.8-27B on vLLM with the Responses wire API: after a very large tool output, every later request body is cut mid-JSON until a new thread. Codex PR #49675 (in 0.161.0) reorders the request's fields; it may be related, and is not a stated fix | Our model and wire API; seen on Windows | The #48870 scenario with the request body captured |
| Codex #39767 (open) | When the server does not say reasoning is already counted, the client adds an estimate for past reasoning again, and compaction comes early | Qwen's template probably drops past reasoning, so the estimate may double-count (inference, unchecked) | Compare the client's estimate with SGLang's logged `prompt_tokens` over one long session |
| Codex #47733 (open) | Replayed reasoning items carry `"content": null`, which strict servers reject | A second turn could fail on a stricter server | Capture a second-turn request; if SGLang ever rejects it, a one-line serialization patch |
| Codex #45393 (open; Codex #45401 is its duplicate) | Resuming a thread compacted on the vendor's backend fails on any other provider (`compaction` item not supported); reproduced with `--oss` on 0.161.0 | The launcher copies sessions from the old Codex home on first run, so a user resuming an imported, vendor-compacted thread hits it | `ling exec resume` on an imported compacted rollout; and `exec resume` after a local compaction inside a SWE-bench container (the nudge and review-turn path) |
| Codex #36642 (open) | Auto-compaction sometimes leaves a replacement history with no summary, losing everything; reports are mostly remote compaction | Would look like the "constraint lost" harm of 16950 | Scan `$CODEX_HOME/sessions` rollouts for `compacted` records with an empty summary |
| Codex #36586 (open) | With multi-agent v2, the subagent's task travels encrypted, so third-party models receive no task | Subagents would silently do nothing | Check what `model_catalog.json` implies for `multi_agent_version`; `ling exec "spawn a subagent that replies exactly PONG"` |
| Codex #42088 (open) | A `function_call_output` can be replayed without `call_id`, and strict servers reject the whole request | A whole turn lost on a strict server | A test over captured requests: every output has a matching `call_id` |
| Codex #42717, Codex #51615 (open) | Interrupting a turn leaves the unified-exec process running; on Linux `codex-linux-sandbox` trees end up under `systemd --user` and accumulate | Leftover `pytest` trees eat the unified-memory headroom the model server needs | After a `turn/interrupt` in the app-server or desktop app: `ps -eo pid,ppid,etimes,cmd` for sandbox processes whose parent is the user manager |
| Codex #38909 (open) | A fork chain inside bwrap exhausted kernel memory and froze the host; nothing caps processes | A whole-host freeze is what the host-safety layer exists to prevent | Confirm Night Shift's scope sets `TasksMax` as well as a memory cap; consider `--pids-limit` on SWE-bench containers and a tasks cap on interactive sessions |
| Codex #46110 (open) | The sandbox rejects snapd `nsfs` mount roots | The main case is handled at the pin (only mounts on the socket's device are parsed); residual cases remain, and this GB10 runs snapd | Re-run `ling sandbox -- true` from a user unit after the bump |
| Codex #51175 (open) | Linux 0.160.x: unified exec gets the cwd as a `file://` URI and every command fails; a maintainer disputes the diagnosis | A possible regression waiting at the bump | Right after the bump: `ling exec "run pwd"` in workspace-write and in a SWE-bench container |
| Codex #47538 (open) | On third-party providers the TUI can show assistant text twice after a mid-stream disconnect; `exec --json` is clean | Visible in the TUI if SGLang drops a stream | Kill the connection mid-turn with the TUI open |
| Codex #46252 (open) | app-server `thread/start` with an explicit `sandbox` silently discards the configured permission profile | The desktop app's threads, and how `/airgapped on` is enforced for them | An app-server test: start a thread with `sandbox` set under `on`, and confirm the command's network namespace is still empty |

### 4.2 Changes since the pin that need rework at the next bump

| Change | What it does | Effect on `ling` | Check |
|---|---|---|---|
| Codex PR #50447 (0.162.0) | Removes the provider capability gate for tool namespaces: namespaces, the multi-agent collaboration namespace included, are now sent to every provider; tool search is gated on `supports_search_tool` only. The upstream request to flatten MCP tools for other providers, Codex #26234, is still open | Patch `0020` must be re-hooked in `core/src/client.rs` and must now flatten collaboration tools too | Capture one request after the bump and assert no tool has `"type":"namespace"`; keep `supports_search_tool` false in the catalog so MCP tools are not deferred |
| Codex PR #51156 (0.162.0) | Base instructions move from `instructions` into a `developer` message in `input` | The prompt from `model_catalog.json` changes shape on the wire; masking (`0021`, `history.rs`) sees an instructions item; SGLang and the patched Qwen template must accept role `developer` | A one-turn `ling exec` against SGLang, and `exec resume` of a session made before the bump |
| Codex PR #51211 (0.162.0) | The only post-pin change to `linux_run_main.rs` (patch `0019`): sandbox-writable bwrap binaries on `PATH` are rejected, with a fallback to the bundled bwrap | The AppArmor profile grants user namespaces to `/usr/bin/bwrap` alone; a fallback to the bundled binary would break the sandbox from units | From a throwaway user unit: `ling sandbox -- true`, with `execve` traced to see which bwrap ran |
| Codex PR #50402 (0.162.0) | `stdout`, `stderr` and `formatted_output` removed from command items; only `aggregated_output` remains | Breaks any consumer of app-server or `exec --json` events: the desktop app, `ling web`, and any parser in SWE-bench, Night Shift or the egress audit | Grep the consumers for the removed fields before the bump |
| Codex PR #49807 (0.161.0) | API-key model discovery on by default | Must reach only SGLang's `/v1/models` | The egress audit, both modes, after the bump |
| Codex PR #51812 (0.162.0) | Instant interrupt on by default: new input preempts a running response | TUI and app-server behaviour changes | Interrupt during a command and repeat #42717's leftover check |

**Post-pin commits on patched files** (first release in brackets):

- `cli/src/main.rs` (`0002`, `0014` and others): 4, the last two a managed daemon [0.162.0]. Re-check that `CODEX_HOME` is still set first in `main()`.
- `core/src/client.rs` (`0020`): 10.
- `core/src/tools/router.rs`: 2.
- `core/src/context_manager/history.rs` (`0021`): 7.
- `core/src/config/mod.rs` (`0023`): 15.
- `analytics/src/client.rs` (`0013`): 2.
- `core-plugins/src/manager.rs` (`0015`): 3.
- `cli/src/doctor.rs` (`0016`): 3.
- `tui/src/slash_command.rs`: 3.
- `tui/src/chatwidget/slash_dispatch.rs`: 7.
- `permissions_menu.rs`: 2, now reading the server's permission catalog, so the Full Access hook at `on` needs re-checking.
- `windows-sandbox-rs`: 15.
- None on `app-server/src/extensions.rs`, `apps_processor.rs`, `otel/src/config.rs` or `remote_legacy.rs`.

The series stands at 42,741 of its 43,000-byte cap (SWE_BENCH §18.1). A bump that needs `0020` re-hooked and new flattening for collaboration tools will likely need the cap raised.

**New channels to put through the egress audit after the bump** (seen as titles, not read): a managed daemon, rendezvous diagnostics, relay connections, renewable HTTP auth, an execution-environment proxy, browser-extension headers, and the model discovery above.

### 4.3 Worth taking at the next bump

- Codex PR #50459 (0.162.0): per-provider capability overrides, including `remote_compaction = "unsupported"` and `external_web_access = false`. The launcher can say "local compaction, no hosted search" explicitly. Test: a launcher test that both keys are written.
- Codex PR #51117 (0.162.0): compaction's replacement history carries the full context (refreshed instructions and skills catalog) ahead of the summary, and usage is recomputed. It is the counterpart of #36642, and of the compaction harms in §3.7. Test: compact, `exec resume`, and confirm the custom prompt and skills are present.
- Codex PR #49135 (0.160.0): an explicit model catalog is authoritative, matched by exact id. A served id missing from `model_catalog.json` should then fail clearly instead of borrowing bundled metadata.
- Codex PR #51203 (0.162.0): `apply_patch` preserves CRLF line endings. Some SWE-bench repositories have CRLF files.
- Codex PR #50059 (0.162.0): bwrap starts correctly with several denied files. It matters only if `ling` adopts `[permissions]` deny rules.
- Seen as titles in 0.162.0:
  - persisted command output capped at 64 KiB;
  - MCP results truncated with JSON overhead counted;
  - `Retry-After` honoured. The model gate's `Retry-After: 0` (SWE_BENCH §18.4) relies on this behaviour and should be re-checked.

### 4.4 The recurring local-provider theme

The 410 provider issues share one shape: the Codex project's vendor-only extensions to the Responses API, sent to every provider. Each works on the vendor's backend and gets a 400 from a strict server:

- namespace tools (Codex #26234, 35 comments; upstream went the other way in #50447);
- encrypted compaction items and encrypted subagent payloads;
- outputs without `call_id`;
- `content: null` reasoning;
- `oneOf`/`null` in tool schemas.

The others are token accounting (missing `usage`, double-counted reasoning), the model catalog, streams that disconnect, and features gated on the vendor's sign-in. Almost none of the reports come from Linux CLI users of vLLM or SGLang; #47723 is the one with this model. For `ling` the lesson is a **request-shape test at every bump**: capture the requests of a scripted two-turn session with one compaction and one resume against a recording stand-in, and assert no namespace, no encrypted items, every output paired with its call, and `usage` read back.

---

## 5. What users of local agents ask for and run into

From 7,676 issues created in the window in the trackers of Cline, Continue, OpenHands and Aider. 1,721 match local-model keywords (928 name a local runtime). Counts are rough keyword matches and an issue can match several themes. Continue's stale bot now says the repository is no longer actively maintained, which is why many of its local-model issues closed unresolved.

| Theme | Rough count (Cline / Continue / OpenHands / Aider) | What users report | What `ling` should do or check |
|---|---|---|---|
| **Configuration of the endpoint** (largest) | 216 / 182 / 111 / 32 | Context length not passed to the server, so the model reloads every turn ([aider#4764](https://github.com/Aider-AI/aider/issues/4764)); model names that need provider prefixes and a universal compatible-endpoint mode ([OpenHands#14525](https://github.com/OpenHands/OpenHands/issues/14525)); switching models on one llama.cpp-compatible server ([OpenHands#13518](https://github.com/OpenHands/OpenHands/issues/13518)) | Already solved by design: the launcher reads the model id and `max_model_len` from `/v1/models`. Keep users from ever having to set a context length; add a "test the endpoint" line to `ling-admin status` if it is missing |
| **Tool-call reliability and loops** | calls 93 / 97 / 24 / 5; loops 51 / 13 / 20 / 5 | Qwen 3.5/3.6 and Gemma 4 emitting tool calls inside the reasoning stream, with missing or misnamed arguments ([continue#12131](https://github.com/continuedev/continue/issues/12131)); JSON calls where the client expects XML, endless "no tools used" loops and HTTP 400 on models without tool support ([cline#11263](https://github.com/cline/cline/issues/11263)); a release changing the tool syntax and breaking Ollama + Qwen3.6 ([cline#13008](https://github.com/cline/cline/issues/13008), which also turned out partly to be a ROCm backend fault); a recommended model missing from an allowlist ([OpenHands#14807](https://github.com/OpenHands/OpenHands/issues/14807)); small models omitting a required field and breaking the conversation ([OpenHands#12228](https://github.com/OpenHands/OpenHands/issues/12228)) | Codex uses native calls, so the server side carries the load: SGLang's tool parser, the patched chat template, and `reasoning_content` kept apart from `content`. Add to `server start`'s canary a tool call emitted after some reasoning, and keep required tool-schema fields minimal (MCP tools are already flattened) |
| **Edit format and reasoning in files** | edit 16 / 15 / 3 / 29; leakage 18 / 34 / 4 / 1 | A valid diff silently discarded under an auto-selected edit format ([aider#5486](https://github.com/Aider-AI/aider/issues/5486)); local Qwen models asking the user to paste edits ([aider#5118](https://github.com/Aider-AI/aider/issues/5118)); reasoning text written into the file ([continue#8990](https://github.com/continuedev/continue/issues/8990)); a tracking issue of local-model edit failures ([continue#11667](https://github.com/continuedev/continue/issues/11667)) | An `apply_patch` that fails to parse must surface as an error with the raw text kept, never as an empty turn; the SWE-bench report already counts "malformed tool call" (0 in the 100) and should keep doing so |
| **Long sessions and compaction** | 83 / 53 / 15 / 15 | A compaction request capped at 1,024 output tokens, so the summary is truncated and compaction is skipped; commenters hit it with **Qwen3.8-27B on llama.cpp** ([cline#13127](https://github.com/cline/cline/issues/13127)); compaction aborted by a 60 s client timeout that the server then completes ([OpenHands#17621](https://github.com/OpenHands/OpenHands/issues/17621)); old instructions re-executed after condensation ([OpenHands#11910](https://github.com/OpenHands/OpenHands/issues/11910)); no pruning until the server refuses ([continue#9797](https://github.com/continuedev/continue/issues/9797)); a tool argument dropped near 90K tokens ([cline#7965](https://github.com/cline/cline/issues/7965)) | Check Codex's compaction request at 25 tokens/s: its output limit and its timeout (§4); make an empty or truncated summary visible; check whether the summary marks finished requests as done (the ledger could carry "done" items) |
| **Speed and timeouts** | 70 / 20 / 43 / 4 (prompt caching named only about 12 times) | A hidden 5-minute cap on long prefills, whatever the configured timeout ([cline#9328](https://github.com/cline/cline/issues/9328)); background summarizer calls queued behind the main turn on a single-slot server ([aider#5471](https://github.com/Aider-AI/aider/issues/5471)); one global lock serializing all model calls ([OpenHands#16459](https://github.com/OpenHands/OpenHands/issues/16459)) | A 100–128K prompt at 1,000–1,700 tokens/s is 60–120 s before the first token: confirm Codex's stream idle timeout and SGLang's request timeout exceed the worst prefill plus a compaction summary (§4). If masking is ever turned on, measure the prefix-cache hit rate, because rewriting old outputs can force a full re-prefill |
| **Offline and air-gapped** | 22 / 6 / 14 / 2 | "Fails offline" turned out to be a missing `host.docker.internal`, then an `HTTP_PROXY` intercepting a loopback health check, then a too-short sandbox grace period ([OpenHands#14941](https://github.com/OpenHands/OpenHands/issues/14941)) | Set or clear `NO_PROXY` for loopback and the model server in the launcher, and check it in `ling-admin host check` |
| **Hardware and backend** | 60 / 7 / 5 / 1 (noisy) | A backend's numerical fault that looked like a tool-call regression (end of [cline#13008](https://github.com/cline/cline/issues/13008)) | The NVFP4 canary in `server start` is the right instinct; a tool-call canary belongs beside it |
| **Feature requests** | (screened lists) | A text/XML tool mode for weak models; documented tool syntax; compaction that evicts stale files; model switching on one server; a "test the endpoint" check; memory across sessions | Most are covered (native calls, the ledger, one served model by design); "evict stale files" is what masking does, which measured no difference (§2 #11) |

**Fixes other projects shipped that `ling` could copy.**

- Continue strengthened its apply prompt for local models with labelled sections and an "output only code" instruction ([continue#10486](https://github.com/continuedev/continue/pull/10486), merged 2026-03-20).
- Continue maps reasoning deltas to `reasoning_content` ([continue#11847](https://github.com/continuedev/continue/pull/11847), merged 2026-03-25). It is worth confirming that SGLang's reasoning parser always does the same for Qwen3.8.
- OpenHands removed a process-wide lock around model calls ([software-agent-sdk#4473](https://github.com/OpenHands/software-agent-sdk/pull/4473), merged 2026-08-12). This matters wherever several sessions share one server, as Night Shift and SWE-bench do.
- Open and not merged:
  - an XML tool-calling mode translated at a gateway ([cline#12835](https://github.com/cline/cline/pull/12835)), a fallback if SGLang's parser ever fails on a model;
  - a JSON-text tool-call fallback ([cline#11272](https://github.com/cline/cline/pull/11272));
  - an explicit compaction timeout ([OpenHands#17738](https://github.com/OpenHands/OpenHands/pull/17738));
  - an HTTP timeout override (opened shortly before the window and closed unmerged: [cline#6554](https://github.com/cline/cline/pull/6554)).

---

## 6. Collisions with local measurements

| Outside claim | Local measurement | Reading | What would tell them apart |
|---|---|---|---|
| Retrieval and code graphs help agents (several papers, among them 2603.27277 on Codebase-Memory, the name of the graph behind `ling-code`'s universal layer) | The index never moved a failed run to a new file (§9.2); `--strip-names` 14 with the index against 17 without | 2608.14838 finds retrieval settings matter only when the agent cannot read freely, and 2607.09691 that the edited code is what matters. Both are consistent with the local null | Nothing further for SWE-bench; a user-facing measure (time to first relevant file in Night Shift) would test the index where it may help |
| Context management raises success (2605.08580 up to +8.8; 2512.22087) | `--mask on` 16 against 17 on the 24 | 2609.20804: context management pays mostly under a tight budget, by preventing overflow; 49,152 tokens with compaction is not tight for most tasks | Masking only at `task_context` 32,768, where overflow would bind |
| Rule files help (+13.8, 2604.11088) | Night 1/3 not yet run | The gain may be priming, not content | The placebo arm (#3) |
| Self-review helps (2607.06065) | Not yet measured | Same-session review catches little (2608.08950); self-refine lowered scores (2607.28815); forced revision harms correct code (2607.24604) | `patch-before-review.diff` graded (#5) |

---

## 7. Saved queries for the weekly digest's "Agent" section

Replace `START` and `END` with the digest's week as `YYYYMMDDHHMM` (arXiv), and `SINCE` with its first day as `YYYY-MM-DD` (GitHub). Keep 3 s or more between arXiv calls. GitHub screening goes through the REST listing, not the search API, and is filtered locally.

**arXiv (API).** Each line is a `search_query` value for `https://export.arxiv.org/api/query?search_query=<value>&sortBy=submittedDate&sortOrder=descending&max_results=200`:

```text
abs:"SWE-bench" AND submittedDate:[START TO END]
(abs:"coding agent" AND abs:repository) AND submittedDate:[START TO END]
(abs:verifier OR abs:"self-verification" OR abs:critic OR abs:"self-review") AND abs:"SWE" AND submittedDate:[START TO END]
(abs:compaction OR abs:"context management" OR abs:"context compression" OR abs:"observation masking") AND abs:agent AND abs:coding AND submittedDate:[START TO END]
(abs:"issue reproduction" OR abs:"reproduction test" OR abs:"bug reproduction") AND abs:LLM AND submittedDate:[START TO END]
(abs:"patch selection" OR abs:"test-time scaling" OR abs:reranking) AND abs:"software engineering" AND submittedDate:[START TO END]
(abs:"tool design" OR abs:"agent-computer interface" OR abs:harness) AND abs:agent AND abs:code AND submittedDate:[START TO END]
(abs:memorization OR abs:contamination) AND abs:"SWE-bench" AND submittedDate:[START TO END]
abs:"SWE" AND (abs:"27B" OR abs:"32B" OR abs:"open-weight") AND submittedDate:[START TO END]
```

Screen: keep a paper if its abstract names SWE-bench, a coding agent, issue resolution or program repair. Read in full only those that measure on Verified or Terminal-Bench with an open-weight model of 14B to 35B, or that propose a hook-sized change.

**Upstream Codex (REST).** Changes since the pin, and new issues:

```text
gh api repos/<upstream-codex>/compare/<pinned-tag>...<latest-tag> --jq '.files[].filename'
gh api 'repos/<upstream-codex>/releases?per_page=10' --jq '.[] | [.tag_name, .published_at] | @tsv'
gh api --paginate 'repos/<upstream-codex>/issues?state=all&since=SINCET00:00:00Z&per_page=100'
```

Filter the issue list locally on `created_at` and on titles and bodies matching `sandbox|bwrap|landlock|seccomp|app-server|resume|compact|context window|tool call|apply_patch|unified exec|oss|ollama|lmstudio|custom provider|responses|reasoning|hook`. Flag every compare file under a path a patch in `codex-patches/` touches.

**Other agents (REST).** For each of `cline/cline`, `continuedev/continue`, `OpenHands/OpenHands`, `Aider-AI/aider`:

```text
gh api 'repos/<repo>/issues?state=all&since=SINCET00:00:00Z&per_page=100&page=N'
```

Keep items without `pull_request`, created in the week. Filter on `ollama|lm ?studio|llama\.cpp|llama-server|vllm|sglang|local model|qwen|deepseek|devstral|gpt-oss|glm|tool call|function call|context (window|length)|condens|compact|summar|timeout|offline|air-?gap`. Read the three with the most comments plus reactions.

---

## 8. Limits

- **Paper evidence is mostly on other models.** Most papers measure frontier or mid-size API models. Only some use the 27B to 35B open-weight class (#7, #9, #10, #12, #16 do), and none uses NVFP4. Effects are estimates for that reason.
- **Some of the newest papers are unreviewed preprints.** Several posted in the last month are among them (2610.04433, 2610.01023, 2609.38812), and their numbers may change.
- **Depth of reading.** "Read in depth" means the full text was fetched and its abstract, conclusion, method and ablation passages read, not every table.
- **Unchecked mechanics.** That hooks run inside the SWE-bench container (they need the launcher's trust entries there), and PAIChecker's per-instance labels, are noted where they matter and were not checked.
