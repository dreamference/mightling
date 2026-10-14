# Chooser v3: the moonshot (research note, 2026-10-14)

The best-of-N chooser picks one patch among the patches several runs produced for one task. Version 1 (regression filter, then a pairwise judge over diffs) scored 33 of 44 against single runs of 29–31 and an oracle of 36; version 2 (R2E-Gym's hybrid: reproduction tests, filter, behaviour groups, judge last) is built and not yet run. This note asks what a *perfect* chooser would be, reads the published work on patch selection with that question, and proposes version 3. Numbers are quoted from the papers; "we" numbers are from `~/swe-bench-chooser-2026-10/summary.json` and the backlog (item 1).

## 1. The ceiling, and how much of it anyone reaches

A chooser can only pick a patch that exists. Its score is bounded by the oracle (any candidate resolves) and floored by a random pick. The fraction of that gap closed is the honest measure; the published systems sit in a narrow band:

| System (model, N) | Random | Chosen | Oracle | Gap closed |
|---|---|---|---|---|
| CodeMonkeys (Sonnet 3.5, 10 edits × 10 tests + selection state machine) | 45.8 | 57.4 | 69.8 | 48% |
| CodeMonkeys "Barrel of Monkeys" (5 leaderboard submissions, state machine only) | 60.9 | 66.2 | 80.8 | 27% |
| R2E-Gym (32B agent, hybrid verifier, Best@26) | 34.4 (Pass@1) | 51.0 | 64.4 | 55% |
| Agentic Rubrics (Qwen3-Coder-30B rollouts, K=16, Sonnet-4.5 rubric + GPT-5 judge) | 39.6 | 54.2 | 65.6 | 56% |
| DEI, one agent sampled 10× (Agentless) | 20.4 | 26.0 | 34.7 | 39% |
| LLM-as-a-Verifier (3 frontier candidates) | 76.1 | 78.2 | 84.4 | 25% |
| **Mightling v1** (27B, N=3, filter + judge) | 29.7 | 33 | 36 | **52%** |

Two readings. First, v1 already sits where the literature sits; v2 is expected to add a little, not a lot. Second, nobody reaches the oracle with *selection*: the only system near the ceiling, PatchFusion (96.2% of the candidate-reachable ceiling on SWE-bench Verified), does not select but *fuses* candidates, keeping edit atoms that recur across candidates and pruning unsupported ones. "Perfect" therefore has two parts: close most of the selection gap, then recover part of what no single candidate has.

Sources: CodeMonkeys ([arXiv 2501.14723](https://arxiv.org/html/2501.14723v2), §2.3, Table 2); R2E-Gym ([2504.07164](https://arxiv.org/html/2504.07164), Table 4); Agentic Rubrics ([2601.04171](https://arxiv.org/html/2601.04171v1), Fig. 2); DEI ([2408.07060](https://arxiv.org/html/2408.07060v1), Tables 1–3); LLM-as-a-Verifier ([2607.05391](https://arxiv.org/abs/2607.05391)); PatchFusion ([2607.01597](https://arxiv.org/abs/2607.01597)).

## 2. What each signal is worth, per the papers

**Existing regression tests** (cheap, no model call once selected): R2E-Gym's Best@K 42.9 → 47.4 from this alone; Agentless 25.7 → 27.0 (Table 4). Every strong system keeps this as the first filter, and TestPrune ([2510.18270](https://arxiv.org/abs/2510.18270)) shows that *choosing* the relevant subset of old tests (by the issue) is itself worth 8–13% relative on resolution when fed back to the agent. We already filter on PASS_TO_PASS from the grading logs; the chooser cannot see FAIL_TO_PASS, so nothing changes here.

**Generated reproduction tests**: Agentless +5 points (27.0 → 32.0) from one majority-voted test out of 40 samples; R2E-Gym +3.6 (47.4 → 51.0) with 7–10 tests from a testing agent; CodeMonkeys' voting with 10 tests is the core of its 48%. The quality problem is the same everywhere: "for the majority of problems, less than 20% of tests provide discriminative signal" (R2E-Gym §4.2); up to 10% of tests are *toxic* (rank a wrong patch above a right one); Agentless's own filter kept 213 of 300 tests that "reproduce" on the buggy code, yet only 94 of those pass with the ground-truth patch, so **more than half of the tests that reproduce the issue are wrong about the fix**. The best test generators from an issue alone reach a 63% fail-to-pass rate on TDD-Bench Verified (e-Otter++, [2508.06365](https://arxiv.org/abs/2508.06365)) and 49% on SWT-Bench Verified ([2406.12952](https://arxiv.org/html/2406.12952v2)), with frontier models and execution feedback. A 27B model writing tests blind will do worse; v2's "keep if it fails on base" filter is the Agentless filter and lets the same wrong tests through.

**Behaviour clustering**: AlphaCode, CodeT ([2207.10397](https://arxiv.org/pdf/2207.10397)), MBR-exec, Semantic Voting ([2605.08680](https://arxiv.org/pdf/2605.08680)) all agree that *agreement under execution* beats text majority, and Semantic Voting finds "the aggregation rule matters less than the execution signal itself". TRAE moved from text majority (which "declined as the sampling space increased") to AST clustering with comments stripped, then verification of the largest cluster by a selector agent with tools. Agentless normalises old/new code through the AST before voting. v2 groups by the pass/fail vector on the kept tests; it does not normalise diffs, so two runs that wrote the same fix differently are two groups.

**Judges without execution**: the trained execution-free verifier of R2E-Gym is 71.8% accurate and "disproportionately attends to agent thoughts" ("Great! The fix works" → YES), losing 5 points of Best@26 when the trajectory is withheld, which is the honest setting. DEI's committee (five written explanations in a fixed order, then a 1–10 score, ten votes averaged, relevant code spans beside the diff) gains +5.6 to +10.4 over the average of ten runs of one agent; removing the explanations costs 3 points. Agentic Rubrics beats a patch classifier by +3.5/+4.0 by having an agent *explore the repository* first and write a weighted checklist (4–8 file-change items, 3–6 each for spec alignment, integrity, runtime; weights 1–3; judge scores each item yes/no; the judge sees issue, patch, rubric and not the trajectory); a rubric written from the issue alone loses 1.4–4.0 points. Pairwise judges carry position bias that "varies significantly across judges and tasks" and is "strongly affected by the quality gap" ([2406.07791](https://arxiv.org/html/2406.07791v6)); non-transitivity follows, and the fix is a round-robin scored by Bradley–Terry, or Swiss rounds when N is large ([ICML 2025, non-transitivity in LLM-as-a-judge](https://object.cloud.sdsc.edu/v1/AUTH_da4962d3368042ac8337e2dfdd3e7bf3/ml-papers-txt/ICML/2025/investigating_nontransitivity_in_llmasajudge__9ea21fd5.txt)). v1's judge was right 7 of 10 times on diffs alone; v2 swaps order and takes a majority of three, which treats the position bias but not the transitivity.

**Trained verifiers**: generative yes/no scoring beats a regression head (SWE-Lego, [2601.01426](https://arxiv.org/html/2601.01426): 49.6 vs 46.8 at K=16, and the regressive one plateaus); an 8B generative verifier beats a 32B critic and R2E-Gym's 14B; SWE-RM ([2512.21919](https://arxiv.org/html/2512.21919v1)) lifts Qwen3-Coder-Flash 51.6 → 62.0 at RM@32 with AUC 0.78 and ECE 0.05, but a model trained on 500 examples has ECE 0.48, and TTS "improves up to about 25k examples". We have under a thousand labelled trajectories. Not for v3.

**Finding the difference, then judging it**: three independent lines converge on the same move. CodeMonkeys' selection state machine (the model writes a *new* test, runs it on every candidate and on the unedited code, sees the outputs, repeats or picks; temperature 0, ≤10 iterations) is what takes it from voting to 48% of the gap, under 10% of the cost. The Agentic Verifier for competitive coding ([2602.04254](https://arxiv.org/abs/2602.04254)) "searches for test inputs that expose behavioral discrepancies among candidate solutions" and reports +10–15 points of Best@K over execution-based baselines. PatchDiff ([ICSE 2026, Are "solved issues" really solved correctly?](https://software-lab.org/publications/icse2026_SWE-bench-correctness.pdf)) generates *differentiating* tests between a plausible patch and the oracle from the patch's call traces and finds that 29.6% of test-passing patches behave differently from the oracle and 28.6% of those are certainly wrong (an estimated 11% of "resolved" patches are incorrect). The lesson for a chooser: do not ask the judge which diff is right; ask it to find an input on which the candidates *disagree*, run it, and then ask which observed behaviour the issue wants. Judging an output against an issue is a much easier question than judging a diff, and it is the question a human reviewer asks.

**Fusion**: PatchFusion's result (426/500 on a fixed pool; +30 over the best single source; 96.2% of the reachable ceiling; the evidence-constrained fusion step alone +5 to +9 with no regression, deterministic, no tests at decision time) says the ceiling is not the oracle over whole patches but over *edit atoms*. It is a different kind of chooser and can sit after selection.

## 3. The design: a differential chooser

Version 3 keeps v2's skeleton and changes two things: the judge no longer judges diffs, and nothing is decided by a test that could not tell the candidates apart.

0. **Normalise and count.** Apply each candidate, parse the touched files before and after into ASTs, unparse without docstrings and comments, diff (Agentless/TRAE). Identical normalised diffs are one candidate with a sample count. Test-file hunks are dropped from the vote key (the cogeneration study found that patches carrying tests split into smaller clusters and lose to test-free patches, [2601.19066](https://arxiv.org/abs/2601.19066)).
1. **Regression filter** as v1/v2, from the grading logs.
2. **Reproduction tests with execution feedback.** As v2 (issue only, standalone scripts, fresh container, no network), with two changes: the writer gets a second round *with the output of its first scripts on the unpatched tree* (e-Otter++'s execution feedback, the step that lifts fail-to-pass from the 40s to 63%); and a kept test is **discriminating**, not merely failing on base: it is dropped if every candidate passes it or every candidate fails it (R2E-Gym's distinguishability, Eq. 3). A test that no candidate passes is the toxic case and is dropped rather than counted.
3. **Behaviour groups** by pass/fail vector, as v2, now over normalised candidates with sample weights.
4. **Differential search** (new; CodeMonkeys' state machine, PatchDiff's target). While at least two groups remain and the task's budget allows (≤4 rounds, ≤100 s per test run): the model is shown the issue, the two groups' representative diffs side by side, and the files they touch at the changed lines, and is asked to write one script whose output differs between them. The script runs on base and on one representative of every group, in the task's container. If its outputs are identical across groups, the round is void and the model is told so. If they differ, the model is shown the issue and **only the outputs** (per group, plus base) and names the behaviour the issue asks for; the groups whose output matches win the round. Three rounds of this question decide a pair; a pair the search cannot separate in four rounds is a tie.
5. **Judge last, properly.** Ties only. Before judging, one agentic pass per task writes a rubric from the repository (Agentic Rubrics: the model explores the files the candidates touch with the code index and the task container, writes 10–20 weighted yes/no items in the four axes; the rubric is written once and never sees a candidate). Each remaining candidate is scored against the rubric (issue, diff, rubric; no trajectory). Then a round-robin of pairwise calls with the order swapped (two presentations per pair) and DEI's explanation order before the verdict (issue → context → location → patch → conflict), scored by Bradley–Terry over all pairs. The rubric score breaks Bradley–Terry ties; sample count breaks the rest; the shorter diff breaks the last (CodeMonkeys' top-3 rule).
6. **Fusion, as a reported counterfactual only.** For every task, also compute the PatchFusion-style merge of the top group with edit atoms that recur in ≥2 other candidates, and grade it offline beside the pick. Not submitted; reported as "what fusion would have added", to decide whether a v4 builds it.

The chooser still never sees FAIL_TO_PASS, `resolved`, or the hidden tests, and the environment set-up is the grader's own up to the hidden tests' reset.

## 4. Why this should beat v2, and what it costs

- Stage 2's discriminating filter removes exactly the tests the papers call toxic or non-discriminative; v2 keeps them and lets them decide a group.
- Stage 4 answers the case v1 lost (3 of 10 judge calls wrong on diffs) with an easier question grounded in an observed output. CodeMonkeys attributes about half its selection gain to this stage; PatchDiff shows the differentiating input exists for ~30% of plausible-but-divergent pairs; the Agentic Verifier shows a model can be made to search for it.
- Stage 5 fixes the two known judge defects (position bias, non-transitivity) and adds the only execution-free signal with a measured gain over a plain classifier (the repository-grounded rubric, +3.5–4.0).
- Cost: for N=3 and a 13-task disagreement set, stage 4 is at most 13 × 4 × (1 writer + 1 verdict) = 104 calls plus 13 × 4 × 4 container runs; stage 5 at most 13 rubric passes (agentic, the expensive part, ~5 min each) and 13 × 3 × 2 judge calls. Within the 40-minute task cap of v2. For N=6 (nights 2–6 default arms), stage 4's rounds grow with the number of groups, not N, since a round separates every group at once.

## 5. Evaluation (offline, on disk, no agent time)

- **Pools.** The 44-task list has default-arm runs from nights 2–6 (six candidates; the chooser's N is a parameter), and nights 7–9 add three runs on each of two fresh 50-task lists. Run v3 with N = 3 (v1/v2's pool, same seed) for the comparison, then N = 6.
- **Metrics.** Resolved after choice vs random, filter alone, v1, v2, oracle; gap closed; which stage decided; accuracy of each stage's decision against the hidden verdict (stage 2's tests also graded against the *gold* patch offline, as R2E-Gym does, to measure test quality: F→P rate, discriminating fraction, toxic fraction); judge accuracy on diffs (stage 5) vs on outputs (stage 4) over the same pairs; the fusion counterfactual.
- **Acceptance.** v3 ≥ v2 + 1 on N=3 and closes ≥ 70% of the gap on N=6; stage 4 decides more pairs correctly than the diff judge did (≥ 8 of 10 on v1's set). The best-of-N Night Shift feature (backlog item 6) then takes v3.
- **Honesty checks.** No candidate carries a verdict (v1's test); the gold patch is used only in the offline test-quality metric, in a separate script that never writes to the chooser's output; every container has no network.

## 6. Not in v3, and why

- **A trained verifier**: under 1k labelled trajectories here; the papers need ~25k for calibration. Revisit if SWE-smith-style synthetic tasks are adopted.
- **Fusion as the pick**: reported first (§3.6), built only if the counterfactual shows ≥ +2 on the 44.
- **More than one model**: the owner runs one local model; V1's finding (the judge is the generator) and DEI's (gains come from diverse *candidates*, not diverse judges) say the loss is small if the candidates are diverse, which the nights' arms already are.

## 7. Milestones

1. Stage 0 + 2's discriminating filter + feedback round: one day, tests with the fake server.
2. Stage 4: two days (prompting, the output-only verdict, the round protocol, the container plumbing reused from v2).
3. Stage 5: one day (rubric pass over `ling exec` in the task container; Bradley–Terry).
4. Offline run on N=3 then N=6, report beside v1/v2: a server window of 3–5 hours each.
5. Fusion counterfactual: one day, after 4.
