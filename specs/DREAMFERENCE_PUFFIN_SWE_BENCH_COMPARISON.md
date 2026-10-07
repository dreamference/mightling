# Mightling on SWE-bench Verified, against the published leaderboard on the same tasks

**Status:** analysis, 2026-10-07. Data: the local rounds in `~/.local/share/dreamference/swe-bench/runs/` and the per-instance results of every SWE-bench Verified submission published in `SWE-bench/experiments` at commit `40f164d5b8f1d249bf95a6df8b74b577fd8e519d` (2026-09-03). Script: `scripts/swe_bench_compare.py` (§6), which reproduces every table here and is meant to be rerun on tonight's 100-task result.
**Builds on:** [PUFFIN_SWE_BENCH](./DREAMFERENCE_PUFFIN_SWE_BENCH.md) (the harness, the arm64 images, the validation filter, and why a local number is not a leaderboard score).

---

## Conclusion

- **The 24 tasks we have been running are harder than Verified as a whole.** Across the 175 published submissions, systems resolve on average 5.9 points fewer of these 24 than of all 500 (6.4 among systems at 40% or more, 5.7 among those at 60% or more). A raw 16/24 (66.7%) therefore understates the matching overall score.
- **The default configuration (median of six default-mode rounds, 16/24, range 15 to 17) corresponds to about 71% on all of Verified** (Rasch estimate 70.9%, regression 70.3%). The empirical 95% band is about **60% to 81%**: 24 tasks are few. The six rounds' spread (15 to 17) alone moves the estimate between 68% and 74%.
- **`--refine` (one round, 20/24) corresponds to about 82%** (95% band about 72% to 93%). This is one sample, and McNemar's exact test against the matching default round (`im-index-on`, 17) gives p = 0.25 (recorded in PUFFIN_SWE_BENCH on branch `bench/refine`), so expect a repeat to come in lower.
- **On exactly these 24 tasks**, 16/24 is what OpenHands with Claude Opus 4.5 (77.6% overall), mini-SWE-agent with Gemini 3 Flash (75.8%), OpenHands with Qwen3-Coder-480B-A35B (69.6%) and GLM-4.6 (68.2%) scored. The 14 submissions at exactly 16/24 range from 66.6% to 77.6% overall, with a median of 72.3%. **No published submission resolved more than 20 of the 24**, and only one reached 20 (`20250804_epam-ai-run-claude-4-sonnet`, more than one attempt). The best single-attempt result is 18 (`20260217_mini-v2.0.0_claude-4-6-opus` and `20250524_openhands_claude_4_sonnet`).
- **Against models in the same weight class (20B to 40B)**, the best published result on these 24 is 15/24 (`20250901_entroPO_R2E_QwenCoder30BA3B_tts`, more than one attempt), and the best single-attempt result is 14/24 (OpenHands with Qwen3-Coder-30B-A3B, 51.6% overall). Those are 2025 models. The experiments repository has no submission for any Qwen 3.5, 3.6 or 3.8 model.
- **The 100-task sample planned for tonight is close to representative**: published systems score 1.0 to 1.5 points lower on it than on all 500, and with 100 tasks the empirical 95% band narrows to about ±5 points (§4.5). Neither the arm64 image pool nor the validation filter favours easy tasks: published systems score 0.4 to 0.7 points lower on the 400 instances with community arm64 images, and 0.9 to 1.0 lower on the 120 validated so far, than on all 500.
- **This is an estimate of what this agent, model and machine would score on all 500 under these conditions, not a leaderboard result.** §5 lists what it does not account for: contamination, quantisation, our images and validation filter, one sample per configuration, and how leaderboard entries differ from each other.

---

## 1. Method

**Our side.** Each round's per-instance outcome comes from `eval/1/grading.json`. An empty patch is an unresolved entry there, so the denominator is always the manifest's 24 task IDs, which are identical in every round used (checked). `grading.json` is also the file the project's own report reads. In one round, `acc-25`, the harness's own report JSON says 12 where `grading.json` and `report.md` say 13: the harness JSON lists `astropy__astropy-13453` as unresolved, while `grading.json` records it as resolved. This document follows `grading.json` and uses 13.

| round | configuration | resolved |
|---|---|---|
| `im-index-on` | default prompt, code index universal | 17 |
| `im-index-off` | default prompt, no code index | 16 |
| `im-index-off-b` | the same, repeated | 17 |
| `im-scip-only` | default prompt, code index exact (scip only) | 15 |
| `ab-default-a` | default prompt, code index universal (2026-10-03 build) | 16 |
| `ab-default-b` | the same, repeated | 15 |
| `im-index-mask` | code index, observation masking on | 16 |
| `im-test-first` | test-first prompt | 14 |
| `im-strip-index-on` | issue text with names stripped, code index | 14 |
| `im-strip-index-off` | issue text with names stripped, no code index | 17 |
| `im-refine` | `--refine`: a first session writes a refined description, a fresh session fixes it; one patch, one attempt | 20 |
| `acc-25`, `acc-25-index` | the first rounds (2026-10-01/02, earlier build, no prompt selection) | 13, 13 |
| `ab-highswe` | `high-swe` prompt | 16 |

The **default** figure is the median of the six default-mode rounds (default prompt, the full issue text, no masking, no refine): `im-index-on`, `im-index-off`, `im-index-off-b`, `im-scip-only`, `ab-default-a` and `ab-default-b`, which resolved 17, 16, 17, 15, 16 and 15. The median is 16, and stays 16 without `im-scip-only`. `im-refine` is a single attempt in the leaderboard's sense: it yields one patch and nothing is chosen by running tests.

**Published side.** `evaluation/verified/<submission>/results/results.json` (`resolved` list) or, for the mini-SWE-agent entries, `per_instance_details.json` (one record per task with `resolved`), plus `metadata.yaml` (or `metadata.yml`) for the model, `os_model` (open weights), and `system.attempts`. Of 182 submission folders, 175 publish per-instance results. The 7 without them are listed in the appendix. Only those files were fetched (a blobless sparse clone, about 8 MB), never the trajectories or logs. Overall scores are resolved/500 from the same files.

**Difficulty index.** For each submission, compare its resolve rate on the subset with its rate on all 500, and average over submissions. A ratio below 1, or a negative gap, means the subset is harder than Verified as a whole.

**From a subset score to an overall score.** There are two estimators, both back-tested on the published submissions themselves: each submission's overall score is predicted from its 24 (or 100) subset results alone, and the prediction is compared with its real overall score.

- *Regression*: overall score against subset score, fitted on the 137 submissions at 40% or more overall.
- *Rasch (one-parameter item response)*: every Verified task gets a difficulty and every submission an ability, fitted jointly on the 175 x 500 matrix with a weak prior that keeps always-solved and never-solved tasks finite. A local round's ability is the one whose expected count on the subset's tasks equals its resolved count. The estimate is that ability's expected score on all 500. Unlike the regression, this uses which tasks are in the subset, not only how many.

On the 24 tasks the back-test RMSE is 5.3 points for Rasch and 5.1 for the regression, with a mean bias of -0.5 points. On the 100 it is 2.4 points for both. The **95% back-test band** quoted throughout is the Rasch estimate ±1.96 x that RMSE. It is the error actually observed when a system of known score is judged on these tasks alone, so it already contains the task-sampling noise that n = 24 implies. The **Wilson** column puts the binomial interval for k/n through the same mapping. It ignores what is known about each task and is a little wider, so read it as the conservative bound.

---

## 2. The 24 tasks

### 2.1 Difficulty index

| population | submissions | mean on all 500 | mean on subset | ratio | mean gap (pp) |
|---|---|---|---|---|---|
| all submissions | 175 | 52.9% | 47.0% | 0.89 | -5.9 |
| overall >= 40% | 137 | 61.2% | 54.8% | 0.90 | -6.4 |
| overall >= 60% | 78 | 70.0% | 64.3% | 0.92 | -5.7 |

### 2.2 Top 15 submissions by overall score, on the 24

`attempts` is the submission's own `system.attempts` metadata: `1` is pass@1, `2+` means the system generated more than one candidate and chose between them, and `?` means not stated.

| submission | date | model | attempts | overall (500) | subset | subset % | subset - overall (pp) |
|---|---|---|---|---|---|---|---|
| `20251205_sonar-foundation-agent_claude-opus-4-5` | 2025-12-05 | Claude 4.5 Opus | 1 | 79.2% | 17/24 | 70.8% | -8.4 |
| `20251215_livesweagent_claude-opus-4-5` | 2025-12-15 | Claude 4.5 Opus | 1 | 79.2% | 17/24 | 70.8% | -8.4 |
| `20250928_trae_doubao_seed_code` | 2025-09-28 | Doubao-Seed-Code | 2+ | 78.8% | 19/24 | 79.2% | +0.4 |
| `20251127_openhands_claude-opus-4-5` | 2025-11-27 | Claude Opus 4.5 | ? | 77.6% | 16/24 | 66.7% | -10.9 |
| `20251120_livesweagent_gemini-3-pro-preview` | 2025-11-20 | Gemini 3 Pro Preview | 1 | 77.4% | 17/24 | 70.8% | -6.6 |
| `20250804_epam-ai-run-claude-4-sonnet` | 2025-08-04 | Claude 4 Sonnet | 2+ | 76.8% | 20/24 | 83.3% | +6.5 |
| `20250902_atlassian-rovo-dev` | 2025-09-02 | Multiple | 2 | 76.8% | 15/24 | 62.5% | -14.3 |
| `20260217_mini-v2.0.0_claude-4-5-opus-high` | 2026-02-17 | Claude 4.5 Opus | 1 | 76.8% | 14/24 | 58.3% | -18.5 |
| `20250819_ACoder` | 2025-08-19 | Multiple | 2+ | 76.4% | 18/24 | 75.0% | -1.4 |
| `20260217_mini-v2.0.0_gemini-3-flash-high` | 2026-02-17 | Gemini 3 Flash | 1 | 75.8% | 16/24 | 66.7% | -9.1 |
| `20260217_mini-v2.0.0_minimax-2-5-high` | 2026-02-17 | MiniMax M2.5 | 1 | 75.8% | 17/24 | 70.8% | -5.0 |
| `20250901_warp` | 2025-09-01 | Multiple | 2+ | 75.6% | 17/24 | 70.8% | -4.8 |
| `20260217_mini-v2.0.0_claude-4-6-opus` | 2026-02-17 | Claude 4.6 Opus | 1 | 75.6% | 18/24 | 75.0% | -0.6 |
| `20250612_trae` | 2025-06-12 | Multiple | 2+ | 75.2% | 17/24 | 70.8% | -4.4 |
| `20250731_harness_ai` | 2025-07-31 | Claude Sonnet 4 | 1 | 74.8% | 16/24 | 66.7% | -8.1 |

### 2.3 Best open-weight-model submissions, on the 24

Open weights as the submission's metadata records them (`os_model: true`). The model is the submission's `model_display`.

| submission | date | model | attempts | overall (500) | subset | subset % | subset - overall (pp) |
|---|---|---|---|---|---|---|---|
| `20260217_mini-v2.0.0_minimax-2-5-high` | 2026-02-17 | MiniMax M2.5 | 1 | 75.8% | 17/24 | 70.8% | -5.0 |
| `20260217_mini-v2.0.0_glm-5-high` | 2026-02-17 | GLM 5 | 1 | 72.8% | 15/24 | 62.5% | -10.3 |
| `20251014_Lingxi_kimi_k2` | 2025-10-14 | Kimi K2 | 1 | 71.2% | 15/24 | 62.5% | -8.7 |
| `20260217_mini-v2.0.0_kimi-k2-5-high` | 2026-02-17 | Kimi K2.5 | 1 | 70.8% | 14/24 | 58.3% | -12.5 |
| `20260217_mini-v2.0.0_deepseek-3-2-high` | 2026-02-17 | DeepSeek V3.2 | 1 | 70.0% | 15/24 | 62.5% | -7.5 |
| `20250805_openhands-Qwen3-Coder-480B-A35B-Instruct` | 2025-08-05 | Qwen3-Coder-480B-A35B-Instruct | 1 | 69.6% | 16/24 | 66.7% | -2.9 |
| `20250930_zai_glm4-6` | 2025-09-30 | GLM-4.6 | 1 | 68.2% | 16/24 | 66.7% | -1.5 |
| `20250716_openhands_kimi_k2` | 2025-07-16 | Kimi K2 | 1 | 65.4% | 15/24 | 62.5% | -2.9 |
| `20250728_zai_glm4-5` | 2025-07-28 | GLM-4.5 | 1 | 64.2% | 14/24 | 58.3% | -5.9 |
| `20251210_mini-v1.17.2_kimi-k2-thinking` | 2025-12-10 | Kimi K2 Thinking | 1 | 63.4% | 14/24 | 58.3% | -5.1 |
| `20251124_mini-v1.17.0_minimax-m2` | 2025-11-24 | MiniMax M2 | 1 | 61.0% | 13/24 | 54.2% | -6.8 |
| `20250901_entroPO_R2E_QwenCoder30BA3B_tts` | 2025-09-01 | Qwen3-Coder-30B-A3B-Instruct | 2+ | 60.4% | 15/24 | 62.5% | +2.1 |
| `20251201_mini-v1.17.1_deepseek-v3.2-reasoner` | 2025-12-01 | DeepSeek V3.2 Reasoner | 1 | 60.0% | 13/24 | 54.2% | -5.8 |
| `20250629_deepswerl_r2eagent_tts` | 2025-06-29 | TTS(Bo16) | 2+ | 58.8% | 12/24 | 50.0% | -8.8 |
| `20251209_mini-v1.17.2_devstral-small-2512` | 2025-12-09 | Devstral Small (2512) | 1 | 56.4% | 11/24 | 45.8% | -10.6 |

### 2.4 Submissions built on 20-40B models, on the 24

The metadata has no size field, so these are matched by name against model cards (`MID_SIZE_MODELS` in the script). Every one of these submissions dates from 2025.

| model size | submission | date | model | attempts | overall (500) | subset | subset % | subset - overall (pp) |
|---|---|---|---|---|---|---|---|---|
| Qwen3-Coder-30B-A3B (30B MoE, 3B active) | `20250901_entroPO_R2E_QwenCoder30BA3B_tts` | 2025-09-01 | Qwen3-Coder-30B-A3B-Instruct | 2+ | 60.4% | 15/24 | 62.5% | +2.1 |
| DeepSWE-Preview (Qwen3-32B fine-tune) | `20250629_deepswerl_r2eagent_tts` | 2025-06-29 | TTS(Bo16) | 2+ | 58.8% | 12/24 | 50.0% | -8.8 |
| Devstral Small (24B dense) | `20251209_mini-v1.17.2_devstral-small-2512` | 2025-12-09 | Devstral Small (2512) | 1 | 56.4% | 11/24 | 45.8% | -10.6 |
| Qwen3-Coder-30B-A3B (30B MoE, 3B active) | `20250901_entroPO_R2E_QwenCoder30BA3B` | 2025-09-01 | Qwen3-Coder-30B-A3B-Instruct | 1 | 52.2% | 13/24 | 54.2% | +2.0 |
| Qwen3-Coder-30B-A3B (30B MoE, 3B active) | `20250805_openhands-Qwen3-Coder-30B-A3B-Instruct` | 2025-08-05 | Qwen3-Coder-30B-A3B-Instruct | 1 | 51.6% | 14/24 | 58.3% | +6.7 |
| Skywork-SWE-32B (Qwen2.5-Coder-32B fine-tune) | `20250616_Skywork-SWE-32B+TTS_Bo8` | 2025-06-16 | TTS(Bo8) | 2+ | 47.0% | 13/24 | 54.2% | +7.2 |
| Devstral Small (24B dense) | `20250520_openhands_devstral_small` | 2025-05-20 | DevStral Small 2505 | 1 | 46.8% | 12/24 | 50.0% | +3.2 |
| DeepSWE-Preview (Qwen3-32B fine-tune) | `20250629_deepswerl_r2eagent` | 2025-06-29 | DeepSWE-Preview | 1 | 42.2% | 8/24 | 33.3% | -8.9 |
| SWE-agent-LM-32B (Qwen2.5-Coder-32B fine-tune) | `20250511_sweagent_lm_32b` | 2025-05-11 | SWE-agent-LM-32B | 1 | 40.2% | 7/24 | 29.2% | -11.0 |
| Skywork-SWE-32B (Qwen2.5-Coder-32B fine-tune) | `20250616_Skywork-SWE-32B` | 2025-06-16 | Qwen2.5 Coder 32B Instruct | 1 | 38.0% | 7/24 | 29.2% | -8.8 |
| Devstral Small (24B dense) | `20250725_sweagent_devstral_small_2507` | 2025-07-25 | DevStral Small 2507 | 1 | 38.0% | 6/24 | 25.0% | -13.0 |
| Qwen2.5-Coder-32B-Instruct | `20250803_mini-v1.0.0_qwen2-5-coder-32b-instruct` | 2025-08-03 | Qwen2.5-Coder 32B Instruct | 1 | 9.0% | 1/24 | 4.2% | -4.8 |

### 2.5 Per task

Fraction of all submissions, and of those at or above 60% overall, that resolved each task; Rasch difficulty (0 is the Verified average, higher is harder); then each local run (`y` resolved, `.` not).

| task | all subs | >=60% subs | difficulty | im-index-on | im-index-off | im-index-off-b | im-scip-only | ab-default-a | ab-default-b | im-index-mask | im-test-first | im-strip-index-on | im-strip-index-off | im-refine | acc-25 | acc-25-index | ab-highswe |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `django__django-11880` | 92.0% | 100.0% | -3.90 | y | y | y | y | y | y | y | y | y | y | y | y | y | y |
| `django__django-15731` | 86.9% | 98.7% | -2.93 | y | y | y | y | y | y | y | y | y | y | y | y | y | y |
| `django__django-15315` | 85.1% | 98.7% | -2.69 | y | y | y | y | y | y | y | y | y | y | y | y | y | y |
| `sympy__sympy-14711` | 82.9% | 96.2% | -2.41 | y | y | y | y | y | . | y | y | y | y | y | y | y | y |
| `sphinx-doc__sphinx-9711` | 78.9% | 87.2% | -1.98 | y | y | y | y | y | y | y | y | y | y | y | y | y | y |
| `django__django-14787` | 77.7% | 98.7% | -1.87 | y | y | y | y | y | y | y | y | y | y | y | y | y | y |
| `django__django-13809` | 76.6% | 96.2% | -1.77 | y | y | y | y | y | y | y | y | . | y | y | y | y | y |
| `pytest-dev__pytest-5631` | 75.4% | 98.7% | -1.67 | y | y | y | y | y | y | y | y | y | y | y | y | y | y |
| `django__django-16100` | 68.0% | 88.5% | -1.07 | y | y | y | y | y | y | y | y | y | y | y | y | . | y |
| `django__django-11740` | 59.4% | 96.2% | -0.48 | y | y | y | y | y | y | y | y | y | y | y | y | y | y |
| `astropy__astropy-13453` | 55.4% | 94.9% | -0.22 | y | y | y | y | y | y | y | y | y | y | y | y | y | y |
| `django__django-12774` | 50.3% | 88.5% | +0.11 | y | y | y | y | . | y | y | y | y | y | y | . | . | . |
| `sphinx-doc__sphinx-9230` | 41.1% | 80.8% | +0.67 | y | y | y | y | y | y | y | y | . | . | y | y | y | y |
| `sympy__sympy-18211` | 41.1% | 65.4% | +0.67 | y | . | . | . | y | y | y | y | y | y | y | y | . | y |
| `django__django-13512` | 30.3% | 48.7% | +1.36 | . | . | . | . | . | . | . | . | . | . | y | . | . | . |
| `django__django-16454` | 28.6% | 43.6% | +1.48 | . | . | y | . | . | . | . | . | y | . | y | . | . | y |
| `sympy__sympy-13031` | 27.4% | 51.3% | +1.55 | y | y | y | y | y | y | . | . | . | y | y | . | . | . |
| `sympy__sympy-13877` | 27.4% | 38.5% | +1.55 | y | y | . | y | y | y | y | . | y | y | y | . | y | y |
| `django__django-15563` | 13.1% | 24.4% | +2.71 | . | . | y | . | y | . | y | . | . | y | . | . | y | y |
| `scikit-learn__scikit-learn-25747` | 9.7% | 12.8% | +3.10 | . | . | . | . | . | . | . | . | . | . | . | . | . | . |
| `django__django-15957` | 8.0% | 17.9% | +3.33 | . | . | y | . | . | . | . | . | . | . | y | . | . | . |
| `sympy__sympy-17318` | 8.0% | 10.3% | +3.33 | y | y | . | . | . | . | . | . | . | . | y | . | . | . |
| `sympy__sympy-13798` | 4.6% | 7.7% | +3.95 | . | . | . | . | . | . | . | . | . | y | . | . | . | . |
| `django__django-16502` | 0.0% | 0.0% | +6.52 | . | . | . | . | . | . | . | . | . | . | . | . | . | . |
| **resolved** |  |  |  | **17** | **16** | **17** | **15** | **16** | **15** | **16** | **14** | **14** | **17** | **20** | **13** | **13** | **16** |

`sympy__sympy-13031` is resolved in 8 of the 14 local rounds, including all six default rounds, by only 27% of published systems, so it is an easy one for this agent specifically. `django__django-16502` is resolved by no published submission and by no local round. `scikit-learn__scikit-learn-25747` and `sympy__sympy-13798` are near that level. With 1 or 2 of the 24 effectively out of reach for everyone, 24/24 is not a realistic ceiling: the best any system has managed is 20.

---

## 3. Our rounds as overall Verified scores

- Regression (submissions >= 40.0% overall, n=137): overall = 0.194 + 0.762 x subset; residual SD 5.2%.
- Rasch back-test on the same submissions (predict each one's overall score from its 24 subset tasks alone): RMSE 5.3% (regression: 5.1%).

Lookup: a local result of k/n corresponds to roughly this overall Verified score. "95% back-test" is the Rasch estimate +/- 1.96 x the back-test RMSE, the error actually seen when the published submissions are scored on these tasks alone; "95% Wilson" is the Rasch estimate at the ends of the Wilson interval for k/n, which ignores what is known about each task and is wider.

| k/n | subset % | Rasch estimate | regression estimate | 95% back-test | 95% Wilson |
|---|---|---|---|---|---|
| 7/24 | 29.2% | 36.8% | 41.7% | 26.4% - 47.3% | 22.0% - 56.6% |
| 8/24 | 33.3% | 41.1% | 44.9% | 30.6% - 51.6% | 25.2% - 60.3% |
| 9/24 | 37.5% | 45.3% | 48.0% | 34.9% - 55.8% | 28.6% - 63.7% |
| 10/24 | 41.7% | 49.5% | 51.2% | 39.0% - 60.0% | 32.0% - 66.8% |
| 11/24 | 45.8% | 53.5% | 54.4% | 43.0% - 64.0% | 35.5% - 69.7% |
| 12/24 | 50.0% | 57.4% | 57.6% | 46.9% - 67.9% | 39.2% - 72.3% |
| 13/24 | 54.2% | 61.1% | 60.7% | 50.6% - 71.6% | 42.9% - 74.7% |
| 14/24 | 58.3% | 64.5% | 63.9% | 54.1% - 75.0% | 46.7% - 77.0% |
| 15/24 | 62.5% | 67.8% | 67.1% | 57.3% - 78.3% | 50.5% - 79.2% |
| 16/24 | 66.7% | 70.9% | 70.3% | 60.4% - 81.4% | 54.3% - 81.2% |
| 17/24 | 70.8% | 73.9% | 73.5% | 63.4% - 84.3% | 58.1% - 83.1% |
| 18/24 | 75.0% | 76.7% | 76.6% | 66.2% - 87.1% | 61.9% - 85.0% |
| 19/24 | 79.2% | 79.4% | 79.8% | 68.9% - 89.8% | 65.5% - 86.9% |
| 20/24 | 83.3% | 82.0% | 83.0% | 71.5% - 92.5% | 69.1% - 88.9% |
| 21/24 | 87.5% | 84.7% | 86.2% | 74.2% - 95.2% | 72.6% - 90.9% |
| 22/24 | 91.7% | 87.6% | 89.3% | 77.1% - 98.1% | 76.1% - 93.0% |
| 23/24 | 95.8% | 91.1% | 92.5% | 80.6% - 100.0% | 79.7% - 94.7% |
| 24/24 | 100.0% | 95.6% | 95.7% | 85.1% - 100.0% | 83.8% - 95.6% |

| run | resolved | 95% Wilson (subset) | Rasch overall | regression overall | 95% back-test | 95% Wilson (overall) |
|---|---|---|---|---|---|---|
| im-index-on | 17/24 | 50.8% - 85.1% | 73.9% | 73.5% | 63.4% - 84.3% | 58.1% - 83.1% |
| im-index-off | 16/24 | 46.7% - 82.0% | 70.9% | 70.3% | 60.4% - 81.4% | 54.3% - 81.2% |
| im-index-off-b | 17/24 | 50.8% - 85.1% | 73.9% | 73.5% | 63.4% - 84.3% | 58.1% - 83.1% |
| im-scip-only | 15/24 | 42.7% - 78.8% | 67.8% | 67.1% | 57.3% - 78.3% | 50.5% - 79.2% |
| ab-default-a | 16/24 | 46.7% - 82.0% | 70.9% | 70.3% | 60.4% - 81.4% | 54.3% - 81.2% |
| ab-default-b | 15/24 | 42.7% - 78.8% | 67.8% | 67.1% | 57.3% - 78.3% | 50.5% - 79.2% |
| im-index-mask | 16/24 | 46.7% - 82.0% | 70.9% | 70.3% | 60.4% - 81.4% | 54.3% - 81.2% |
| im-test-first | 14/24 | 38.8% - 75.5% | 64.5% | 63.9% | 54.1% - 75.0% | 46.7% - 77.0% |
| im-strip-index-on | 14/24 | 38.8% - 75.5% | 64.5% | 63.9% | 54.1% - 75.0% | 46.7% - 77.0% |
| im-strip-index-off | 17/24 | 50.8% - 85.1% | 73.9% | 73.5% | 63.4% - 84.3% | 58.1% - 83.1% |
| im-refine | 20/24 | 64.1% - 93.3% | 82.0% | 83.0% | 71.5% - 92.5% | 69.1% - 88.9% |
| acc-25 | 13/24 | 35.1% - 72.1% | 61.1% | 60.7% | 50.6% - 71.6% | 42.9% - 74.7% |
| acc-25-index | 13/24 | 35.1% - 72.1% | 61.1% | 60.7% | 50.6% - 71.6% | 42.9% - 74.7% |
| ab-highswe | 16/24 | 46.7% - 82.0% | 70.9% | 70.3% | 60.4% - 81.4% | 54.3% - 81.2% |

Default-mode rounds (im-index-on, im-index-off, im-index-off-b, im-scip-only, ab-default-a, ab-default-b): resolved 17, 16, 17, 15, 16, 15; median **16/24** -> Rasch **70.9%**, regression 70.3%; run-to-run range 15-17 -> 67.8% - 73.9%.

Resolved by at least one local run (an any-of-14 figure, comparable only to multi-attempt submissions, never to pass@1): 22/24.

How to read it:

- **Default: about 71%** (16/24), back-test band 60% to 81%, Wilson 54% to 81%. Pooling the six default rounds (96 of 144 instance-runs) narrows the run-to-run part but not the task-sampling part, because all six ran on the same 24 tasks. The band is set by the 24, not by the number of rounds.
- **Refine: about 82%** (20/24), back-test band 72% to 93%. It is one round, and default rounds vary by 2 tasks from one draw to the next, so 20 may be a high draw. A refine round on the 100 is what would settle it.
- **The any-of-14 figure (22/24) is not pass@1** and is not compared with anything single-attempt.

---

## 4. The 100-task sample (`sample-100.txt`)

These are the 24 above plus 76 more validated instances, spread across repositories roughly in proportion to Verified. No round has run on it yet. When one has, run:

```bash
.venv/bin/python scripts/swe_bench_compare.py --subset-run <round> --run <round>
```

`--subset-run` takes the task IDs from the round's own manifest, so the round is scored even if an instance was excluded at run time; with `--subset sample-100.txt` instead, a round whose IDs differ from the file's is left out with a note. The script then adds the per-task columns and the round's estimate. Until then, the lookup table below answers the question as soon as the count is known.

### 4.1 Difficulty index

| population | submissions | mean on all 500 | mean on subset | ratio | mean gap (pp) |
|---|---|---|---|---|---|
| all submissions | 175 | 52.9% | 51.9% | 0.98 | -1.0 |
| overall >= 40% | 137 | 61.2% | 59.9% | 0.98 | -1.3 |
| overall >= 60% | 78 | 70.0% | 68.5% | 0.98 | -1.5 |

### 4.2 Top 15 submissions by overall score, on the 100

| submission | date | model | attempts | overall (500) | subset | subset % | subset - overall (pp) |
|---|---|---|---|---|---|---|---|
| `20251205_sonar-foundation-agent_claude-opus-4-5` | 2025-12-05 | Claude 4.5 Opus | 1 | 79.2% | 77/100 | 77.0% | -2.2 |
| `20251215_livesweagent_claude-opus-4-5` | 2025-12-15 | Claude 4.5 Opus | 1 | 79.2% | 80/100 | 80.0% | +0.8 |
| `20250928_trae_doubao_seed_code` | 2025-09-28 | Doubao-Seed-Code | 2+ | 78.8% | 76/100 | 76.0% | -2.8 |
| `20251127_openhands_claude-opus-4-5` | 2025-11-27 | Claude Opus 4.5 | ? | 77.6% | 80/100 | 80.0% | +2.4 |
| `20251120_livesweagent_gemini-3-pro-preview` | 2025-11-20 | Gemini 3 Pro Preview | 1 | 77.4% | 79/100 | 79.0% | +1.6 |
| `20250804_epam-ai-run-claude-4-sonnet` | 2025-08-04 | Claude 4 Sonnet | 2+ | 76.8% | 78/100 | 78.0% | +1.2 |
| `20250902_atlassian-rovo-dev` | 2025-09-02 | Multiple | 2 | 76.8% | 72/100 | 72.0% | -4.8 |
| `20260217_mini-v2.0.0_claude-4-5-opus-high` | 2026-02-17 | Claude 4.5 Opus | 1 | 76.8% | 79/100 | 79.0% | +2.2 |
| `20250819_ACoder` | 2025-08-19 | Multiple | 2+ | 76.4% | 78/100 | 78.0% | +1.6 |
| `20260217_mini-v2.0.0_gemini-3-flash-high` | 2026-02-17 | Gemini 3 Flash | 1 | 75.8% | 78/100 | 78.0% | +2.2 |
| `20260217_mini-v2.0.0_minimax-2-5-high` | 2026-02-17 | MiniMax M2.5 | 1 | 75.8% | 74/100 | 74.0% | -1.8 |
| `20250901_warp` | 2025-09-01 | Multiple | 2+ | 75.6% | 75/100 | 75.0% | -0.6 |
| `20260217_mini-v2.0.0_claude-4-6-opus` | 2026-02-17 | Claude 4.6 Opus | 1 | 75.6% | 78/100 | 78.0% | +2.4 |
| `20250612_trae` | 2025-06-12 | Multiple | 2+ | 75.2% | 71/100 | 71.0% | -4.2 |
| `20250731_harness_ai` | 2025-07-31 | Claude Sonnet 4 | 1 | 74.8% | 69/100 | 69.0% | -5.8 |

### 4.3 Best open-weight-model submissions, on the 100

| submission | date | model | attempts | overall (500) | subset | subset % | subset - overall (pp) |
|---|---|---|---|---|---|---|---|
| `20260217_mini-v2.0.0_minimax-2-5-high` | 2026-02-17 | MiniMax M2.5 | 1 | 75.8% | 74/100 | 74.0% | -1.8 |
| `20260217_mini-v2.0.0_glm-5-high` | 2026-02-17 | GLM 5 | 1 | 72.8% | 69/100 | 69.0% | -3.8 |
| `20251014_Lingxi_kimi_k2` | 2025-10-14 | Kimi K2 | 1 | 71.2% | 70/100 | 70.0% | -1.2 |
| `20260217_mini-v2.0.0_kimi-k2-5-high` | 2026-02-17 | Kimi K2.5 | 1 | 70.8% | 70/100 | 70.0% | -0.8 |
| `20260217_mini-v2.0.0_deepseek-3-2-high` | 2026-02-17 | DeepSeek V3.2 | 1 | 70.0% | 70/100 | 70.0% | +0.0 |
| `20250805_openhands-Qwen3-Coder-480B-A35B-Instruct` | 2025-08-05 | Qwen3-Coder-480B-A35B-Instruct | 1 | 69.6% | 68/100 | 68.0% | -1.6 |
| `20250930_zai_glm4-6` | 2025-09-30 | GLM-4.6 | 1 | 68.2% | 68/100 | 68.0% | -0.2 |
| `20250716_openhands_kimi_k2` | 2025-07-16 | Kimi K2 | 1 | 65.4% | 66/100 | 66.0% | +0.6 |
| `20250728_zai_glm4-5` | 2025-07-28 | GLM-4.5 | 1 | 64.2% | 62/100 | 62.0% | -2.2 |
| `20251210_mini-v1.17.2_kimi-k2-thinking` | 2025-12-10 | Kimi K2 Thinking | 1 | 63.4% | 62/100 | 62.0% | -1.4 |
| `20251124_mini-v1.17.0_minimax-m2` | 2025-11-24 | MiniMax M2 | 1 | 61.0% | 59/100 | 59.0% | -2.0 |
| `20250901_entroPO_R2E_QwenCoder30BA3B_tts` | 2025-09-01 | Qwen3-Coder-30B-A3B-Instruct | 2+ | 60.4% | 60/100 | 60.0% | -0.4 |
| `20251201_mini-v1.17.1_deepseek-v3.2-reasoner` | 2025-12-01 | DeepSeek V3.2 Reasoner | 1 | 60.0% | 56/100 | 56.0% | -4.0 |
| `20250629_deepswerl_r2eagent_tts` | 2025-06-29 | TTS(Bo16) | 2+ | 58.8% | 55/100 | 55.0% | -3.8 |
| `20251209_mini-v1.17.2_devstral-small-2512` | 2025-12-09 | Devstral Small (2512) | 1 | 56.4% | 57/100 | 57.0% | +0.6 |

### 4.4 Submissions built on 20-40B models, on the 100

| model size | submission | date | model | attempts | overall (500) | subset | subset % | subset - overall (pp) |
|---|---|---|---|---|---|---|---|---|
| Qwen3-Coder-30B-A3B (30B MoE, 3B active) | `20250901_entroPO_R2E_QwenCoder30BA3B_tts` | 2025-09-01 | Qwen3-Coder-30B-A3B-Instruct | 2+ | 60.4% | 60/100 | 60.0% | -0.4 |
| DeepSWE-Preview (Qwen3-32B fine-tune) | `20250629_deepswerl_r2eagent_tts` | 2025-06-29 | TTS(Bo16) | 2+ | 58.8% | 55/100 | 55.0% | -3.8 |
| Devstral Small (24B dense) | `20251209_mini-v1.17.2_devstral-small-2512` | 2025-12-09 | Devstral Small (2512) | 1 | 56.4% | 57/100 | 57.0% | +0.6 |
| Qwen3-Coder-30B-A3B (30B MoE, 3B active) | `20250901_entroPO_R2E_QwenCoder30BA3B` | 2025-09-01 | Qwen3-Coder-30B-A3B-Instruct | 1 | 52.2% | 56/100 | 56.0% | +3.8 |
| Qwen3-Coder-30B-A3B (30B MoE, 3B active) | `20250805_openhands-Qwen3-Coder-30B-A3B-Instruct` | 2025-08-05 | Qwen3-Coder-30B-A3B-Instruct | 1 | 51.6% | 52/100 | 52.0% | +0.4 |
| Skywork-SWE-32B (Qwen2.5-Coder-32B fine-tune) | `20250616_Skywork-SWE-32B+TTS_Bo8` | 2025-06-16 | TTS(Bo8) | 2+ | 47.0% | 48/100 | 48.0% | +1.0 |
| Devstral Small (24B dense) | `20250520_openhands_devstral_small` | 2025-05-20 | DevStral Small 2505 | 1 | 46.8% | 40/100 | 40.0% | -6.8 |
| DeepSWE-Preview (Qwen3-32B fine-tune) | `20250629_deepswerl_r2eagent` | 2025-06-29 | DeepSWE-Preview | 1 | 42.2% | 43/100 | 43.0% | +0.8 |
| SWE-agent-LM-32B (Qwen2.5-Coder-32B fine-tune) | `20250511_sweagent_lm_32b` | 2025-05-11 | SWE-agent-LM-32B | 1 | 40.2% | 36/100 | 36.0% | -4.2 |
| Skywork-SWE-32B (Qwen2.5-Coder-32B fine-tune) | `20250616_Skywork-SWE-32B` | 2025-06-16 | Qwen2.5 Coder 32B Instruct | 1 | 38.0% | 37/100 | 37.0% | -1.0 |
| Devstral Small (24B dense) | `20250725_sweagent_devstral_small_2507` | 2025-07-25 | DevStral Small 2507 | 1 | 38.0% | 31/100 | 31.0% | -7.0 |
| Qwen2.5-Coder-32B-Instruct | `20250803_mini-v1.0.0_qwen2-5-coder-32b-instruct` | 2025-08-03 | Qwen2.5-Coder 32B Instruct | 1 | 9.0% | 10/100 | 10.0% | +1.0 |

### 4.5 Lookup: a result of k/100 as an overall score

- Regression (submissions >= 40.0% overall, n=137): overall = 0.029 + 0.973 x subset; residual SD 2.4%.
- Rasch back-test on the same submissions (predict each one's overall score from its 100 subset tasks alone): RMSE 2.4% (regression: 2.4%).

Lookup: a local result of k/n corresponds to roughly this overall Verified score. "95% back-test" is the Rasch estimate +/- 1.96 x the back-test RMSE, the error actually seen when the published submissions are scored on these tasks alone; "95% Wilson" is the Rasch estimate at the ends of the Wilson interval for k/n, which ignores what is known about each task and is wider.

| k/n | subset % | Rasch estimate | regression estimate | 95% back-test | 95% Wilson |
|---|---|---|---|---|---|
| 30/100 | 30.0% | 31.7% | 32.1% | 26.9% - 36.5% | 23.5% - 41.3% |
| 35/100 | 35.0% | 36.7% | 37.0% | 31.9% - 41.5% | 28.0% - 46.5% |
| 40/100 | 40.0% | 41.7% | 41.8% | 36.9% - 46.5% | 32.7% - 51.5% |
| 45/100 | 45.0% | 46.7% | 46.7% | 41.9% - 51.5% | 37.3% - 56.3% |
| 50/100 | 50.0% | 51.7% | 51.6% | 46.9% - 56.4% | 42.1% - 61.0% |
| 55/100 | 55.0% | 56.6% | 56.4% | 51.8% - 61.3% | 47.0% - 65.4% |
| 60/100 | 60.0% | 61.3% | 61.3% | 56.6% - 66.1% | 51.9% - 69.6% |
| 65/100 | 65.0% | 66.0% | 66.1% | 61.2% - 70.8% | 56.8% - 73.5% |
| 70/100 | 70.0% | 70.4% | 71.0% | 65.6% - 75.2% | 61.7% - 77.2% |
| 75/100 | 75.0% | 74.7% | 75.9% | 69.9% - 79.5% | 66.6% - 80.8% |
| 80/100 | 80.0% | 78.8% | 80.7% | 74.0% - 83.6% | 71.4% - 84.3% |
| 85/100 | 85.0% | 82.9% | 85.6% | 78.1% - 87.7% | 76.1% - 88.0% |
| 90/100 | 90.0% | 87.4% | 90.5% | 82.6% - 92.1% | 80.9% - 92.1% |
| 95/100 | 95.0% | 92.7% | 95.3% | 87.9% - 97.5% | 86.2% - 96.2% |
| 100/100 | 100.0% | 98.8% | 100.0% | 94.0% - 100.0% | 94.3% - 98.8% |

On the 100 the Rasch and regression estimates agree to within a point up to about 75/100, and the back-test band is about ±5 points. The 24 tasks give about ±10. Tonight's figure will be a much better estimate than anything in §3. Expect it near 70/100 if the default is really at about 71%: the 100 is about 1.5 points harder than the whole set for systems at that level.

---

## 5. Caveats

- **Contamination.** Verified's issues and fixes have been public since 2024. Every model trained since has had the chance to see them, ours included (Qwen3.8 is 2026). Leaderboard entries share the problem in different degrees, depending on date, so the comparison is fairer against 2025-2026 entries than against older ones. Nothing here detects contamination. One sign of task-specific familiarity worth checking is `sympy__sympy-13031` (§2.5).
- **Our model is a quantised local build** (NVFP4 Qwen3.8-27B with the DFlash2 drafter, on SGLang), served with a 2,700-second task limit, a fixed context budget and cave mode `ultra`. Published entries mostly use full-precision models, many through an API, each with its own limits.
- **Our images and the validation filter.** We run on third-party arm64 images (`greynewell/swe-bench-arm64`, 400 of the 500) and only on instances whose gold patch resolves and whose no-op patch does not here. The Conclusion checks that neither the 400 nor the 120 validated so far are easier than the 500: they are 0.4 to 1.0 points harder. Published results are graded on the official x86_64 images. An instance that grades differently here, in either direction, shifts our count and not theirs. 9 instances are known to grade differently (the gold patch fails here). Among them is `sphinx-doc__sphinx-8056`, the one task of `sample-25.txt` dropped from the 24.
- **One sample per configuration.** Every round is one draw. Six default draws spread 15 to 17. The refine figure is one draw.
- **The agent has no network** (the container is the sandbox). Some published scaffolds browse or search.
- **Leaderboard entries are not like for like with each other.** Scaffolds differ (OpenHands, mini-SWE-agent, SWE-agent, proprietary agents), as do attempts (`2+` entries pick among several candidates, often with generated tests: compare our single-attempt rounds with `1` rows), reasoning-effort settings and dates (2023-10 to 2026-09). `checked` (verified by the SWE-bench team) is false or absent for most recent entries. The newest submission in the repository is from 2026-09-01. Entries after the experiments commit are not included.
- **The difficulty adjustment assumes one ability per system.** Real systems have strengths by repository and task type, and the 24 lean toward django and sympy (13 and 6 of 24). The back-test RMSE measures how much this matters in practice for published systems (5.3 points on the 24), but our agent's profile could differ from theirs.

---

## 6. Reproducing

```bash
# results files only: a blobless sparse clone of SWE-bench/experiments (~8 MB, no trajectories or logs)
.venv/bin/python scripts/swe_bench_compare.py --fetch

# the 24-task rounds, as in §2-§3
.venv/bin/python scripts/swe_bench_compare.py --subset-run im-index-on \
    --run im-index-on --run im-index-off --run im-index-off-b --run im-scip-only \
    --run ab-default-a --run ab-default-b --run im-index-mask --run im-test-first \
    --run im-strip-index-on --run im-strip-index-off --run im-refine \
    --run acc-25 --run acc-25-index --run ab-highswe \
    --default-runs im-index-on,im-index-off,im-index-off-b,im-scip-only,ab-default-a,ab-default-b

# the 100-task sample, as in §4; once a round is graded: --subset-run <round> --run <round>
.venv/bin/python scripts/swe_bench_compare.py --subset ~/.cache/dreamference/swe-bench/sample-100.txt
```

The clone defaults to `~/.cache/dreamference/swe-bench/experiments` (`--experiments` to change it). `--json` writes the numbers in machine-readable form. A round whose task IDs differ from the subset's is reported and left out, never scored on a partial match. The script reads only the runs' `manifest.json` and `eval/1/grading.json` (falling back to the harness report's `resolved_ids`) and needs no GPU, model server or Docker.

---

## 7. Appendix: submissions used

175 submissions with per-instance results, at experiments commit `40f164d5b8f1d249bf95a6df8b74b577fd8e519d` (2026-09-03), with overall Verified score as computed from their own files:

**2023** (4): `20231010_rag_claude2` (2023-10-10, 4.4%); `20231010_rag_gpt35` (2023-10-10, 0.4%); `20231010_rag_swellama13b` (2023-10-10, 1.2%); `20231010_rag_swellama7b` (2023-10-10, 1.4%).

**2024** (51): `20240402_rag_claude3opus` (2024-04-02, 7.0%); `20240402_rag_gpt4` (2024-04-02, 2.8%); `20240402_sweagent_claude3opus` (2024-04-02, 15.8%); `20240402_sweagent_gpt4` (2024-04-02, 22.4%); `20240509_amazon-q-developer-agent-20240430-dev` (2024-05-09, 25.6%); `20240612_MASAI_gpt4o` (2024-06-12, 32.6%); `20240615_appmap-navie_gpt4o` (2024-06-15, 26.2%); `20240617_factory_code_droid` (2024-06-17, 37.0%); `20240620_sweagent_claude3.5sonnet` (2024-06-20, 33.6%); `20240628_autocoderover-v20240620` (2024-06-28, 38.4%); `20240721_amazon-q-developer-agent-20240719-dev` (2024-07-21, 38.8%); `20240728_sweagent_gpt4o` (2024-07-28, 23.2%); `20240820_epam-ai-run-gpt-4o` (2024-08-20, 24.0%); `20240820_honeycomb` (2024-08-20, 40.6%); `20240824_gru` (2024-08-24, 45.2%); `20240918_lingma-agent_lingma-swe-gpt-72b` (2024-09-18, 25.0%); `20240918_lingma-agent_lingma-swe-gpt-7b` (2024-09-18, 10.2%); `20240920_solver` (2024-09-20, 43.6%); `20240924_solver` (2024-09-24, 45.4%); `20241001_nfactorial` (2024-10-01, 25.8%); `20241002_lingma-agent_lingma-swe-gpt-72b` (2024-10-02, 28.8%); `20241002_lingma-agent_lingma-swe-gpt-7b` (2024-10-02, 18.2%); `20241007_nfactorial` (2024-10-07, 31.6%); `20241016_composio_swekit` (2024-10-16, 40.6%); `20241016_epam-ai-run-gpt-4o` (2024-10-16, 27.0%); `20241022_tools_claude-3-5-haiku` (2024-10-22, 40.6%); `20241022_tools_claude-3-5-sonnet-updated` (2024-10-22, 49.0%); `20241023_emergent` (2024-10-23, 46.6%); `20241025_composio_swekit` (2024-10-25, 48.6%); `20241028_agentless-1.5_gpt4o` (2024-10-28, 38.8%); `20241028_solver` (2024-10-28, 50.0%); `20241029_OpenHands-CodeAct-2.1-sonnet-20241022` (2024-10-29, 53.0%); `20241029_epam-ai-run-claude-3-5-sonnet` (2024-10-29, 39.6%); `20241030_nfactorial` (2024-10-30, 41.6%); `20241105_nfactorial` (2024-11-05, 49.2%); `20241106_navie-2-gpt4o-sonnet` (2024-11-06, 47.2%); `20241108_autocoderover-v2.0-claude-3-5-sonnet-20241022` (2024-11-08, 46.2%); `20241108_devlo` (2024-11-08, 54.2%); `20241113_nebius-search-open-weight-models-11-24` (2024-11-13, 40.6%); `20241120_artemis_agent` (2024-11-20, 32.0%); `20241125_enginelabs` (2024-11-25, 51.8%); `20241125_marscode-agent-dev` (2024-11-25, 50.0%); `20241128_SWE-Fixer_Qwen2.5-7b-retriever_Qwen2.5-72b-editor_20241128` (2024-11-28, 30.2%); `20241202_agentless-1.5_claude-3.5-sonnet-20241022` (2024-12-02, 50.8%); `20241202_amazon-q-developer-agent-20241202-dev` (2024-12-02, 55.0%); `20241208_gru` (2024-12-08, 57.0%); `20241212_epam-ai-run-claude-3-5-sonnet` (2024-12-12, 55.4%); `20241212_google_jules_gemini_2.0_flash_experimental` (2024-12-12, 52.2%); `20241213_devlo` (2024-12-13, 58.2%); `20241221_codestory_midwit_claude-3-5-sonnet_swe-search` (2024-12-21, 62.2%); `20241223_emergent` (2024-12-23, 57.2%).

**2025** (107): `20250110_blackboxai_agent_v1.1` (2025-01-10, 62.8%); `20250110_learn_by_interact_claude3.5` (2025-01-10, 60.2%); `20250112_ugaiforge` (2025-01-12, 41.6%); `20250117_wandb_programmer_o1_crosscheck5` (2025-01-17, 64.6%); `20250118_codeshellagent_gemini_2.0_flash_experimental` (2025-01-18, 44.2%); `20250120_Bracket` (2025-01-20, 53.2%); `20250122_autocoderover-v2.1-claude-3-5-sonnet-20241022` (2025-01-22, 51.6%); `20250203_openhands_4x_scaled` (2025-02-03, 60.8%); `20250206_agentscope` (2025-02-06, 63.4%); `20250214_agentless_lite_o3_mini` (2025-02-14, 42.4%); `20250224_tools_claude-3-7-sonnet` (2025-02-24, 63.2%); `20250225_sweagent_claude-3-7-sonnet` (2025-02-25, 62.4%); `20250226_swerl_llama3_70b` (2025-02-26, 41.2%); `20250228_epam-ai-run-claude-3-5-sonnet` (2025-02-28, 62.8%); `20250306_SWE-Fixer_Qwen2.5-7b-retriever_Qwen2.5-72b-editor` (2025-03-06, 32.8%); `20250316_augment_agent_v0` (2025-03-16, 65.4%); `20250405_amazon-q-developer-agent-20250405-dev` (2025-04-05, 65.4%); `20250405_swe-rizzo_claude37` (2025-04-05, 56.6%); `20250410_cortexa` (2025-04-10, 58.2%); `20250415_openhands` (2025-04-15, 65.8%); `20250430_zencoder_ai` (2025-04-30, 70.0%); `20250503_patchpilot-v1.1-o4-mini` (2025-05-03, 64.6%); `20250511_sweagent_lm_32b` (2025-05-11, 40.2%); `20250514_aime_coder` (2025-05-14, 66.4%); `20250515_Refact_Agent` (2025-05-15, 70.4%); `20250516_cortexa_o3` (2025-05-16, 68.2%); `20250519_devlo` (2025-05-19, 70.2%); `20250519_trae` (2025-05-19, 70.6%); `20250520_openhands_devstral_small` (2025-05-20, 46.8%); `20250522_sweagent_claude-4-sonnet-20250514` (2025-05-22, 66.6%); `20250522_tools_claude-4-opus` (2025-05-22, 73.2%); `20250522_tools_claude-4-sonnet` (2025-05-22, 72.4%); `20250524_openhands_claude_4_sonnet` (2025-05-24, 70.4%); `20250527_amazon.nova-premier-v1.0` (2025-05-27, 42.4%); `20250528_patchpilot_Co-PatcheR` (2025-05-28, 46.0%); `20250603_Refact_Agent_claude-4-sonnet` (2025-06-03, 74.4%); `20250610_augment_agent_v1` (2025-06-10, 70.4%); `20250611_moatless_claude-4-sonnet-20250514` (2025-06-11, 70.8%); `20250612_trae` (2025-06-12, 75.2%); `20250616_Skywork-SWE-32B` (2025-06-16, 38.0%); `20250616_Skywork-SWE-32B+TTS_Bo8` (2025-06-16, 47.0%); `20250623_warp` (2025-06-23, 71.0%); `20250627_agentless_MCTS-Refine-7B` (2025-06-27, 23.2%); `20250629_deepswerl_r2eagent` (2025-06-29, 42.2%); `20250629_deepswerl_r2eagent_tts` (2025-06-29, 58.8%); `20250710_bloop` (2025-07-10, 71.2%); `20250715_qodo_command` (2025-07-15, 71.2%); `20250716_openhands_kimi_k2` (2025-07-16, 65.4%); `20250720_Lingxi-v1.5_claude-4-sonnet-20250514` (2025-07-20, 74.6%); `20250720_mini-v0.0.0-Llama-4-Maverick-17B-Instruct` (2025-07-20, 21.0%); `20250720_mini-v0.0.0-claude-3-7-sonnet-20250219` (2025-07-20, 10.2%); `20250725_sweagent_devstral_small_2507` (2025-07-25, 38.0%); `20250726_mini-v1.0.0_claude-sonnet-4-20250514` (2025-07-26, 64.8%); `20250726_mini-v1.0.0_gemini-2.5-pro` (2025-07-26, 53.6%); `20250726_mini-v1.0.0_o3-2025-04-16` (2025-07-26, 58.4%); `20250726_mini-v1.0.0_o4-mini-2025-04-16` (2025-07-26, 45.0%); `20250728_zai_glm4-5` (2025-07-28, 64.2%); `20250731_harness_ai` (2025-07-31, 74.8%); `20250802_mini-v1.0.0_claude-4-opus-20250514` (2025-08-02, 67.6%); `20250802_mini-v1.0.0_qwen3-coder-480b-a35b-instruct` (2025-08-02, 55.4%); `20250803_mini-v1.0.0_qwen2-5-coder-32b-instruct` (2025-08-03, 9.0%); `20250804_codesweep_sweagent_kimi_k2_instruct` (2025-08-04, 53.4%); `20250804_epam-ai-run-claude-4-sonnet` (2025-08-04, 76.8%); `20250805_openhands-Qwen3-Coder-30B-A3B-Instruct` (2025-08-05, 51.6%); `20250805_openhands-Qwen3-Coder-480B-A35B-Instruct` (2025-08-05, 69.6%); `20250806_SWE-Exp_DeepSeek-V3` (2025-08-06, 42.0%); `20250807_mini-v1.7.0_gpt-5` (2025-08-07, 65.0%); `20250807_mini-v1.7.0_gpt-5-mini` (2025-08-07, 59.8%); `20250807_mini-v1.7.0_gpt-5-nano` (2025-08-07, 34.8%); `20250807_mini-v1.7.0_gpt-oss-120b` (2025-08-07, 26.0%); `20250807_mini-v1.7.0_kimi-k2-instruct` (2025-08-07, 43.8%); `20250807_openhands_gpt5` (2025-08-07, 71.8%); `20250819_ACoder` (2025-08-19, 76.4%); `20250822_mini-v1.9.1_glm-4.5` (2025-08-22, 54.2%); `20250901_entroPO_R2E_QwenCoder30BA3B` (2025-09-01, 52.2%); `20250901_entroPO_R2E_QwenCoder30BA3B_tts` (2025-09-01, 60.4%); `20250901_warp` (2025-09-01, 75.6%); `20250902_atlassian-rovo-dev` (2025-09-02, 76.8%); `20250915_JoyCode` (2025-09-15, 74.6%); `20250924_artemis_agent_v2` (2025-09-24, 57.0%); `20250928_trae_doubao_seed_code` (2025-09-28, 78.8%); `20250929_Prometheus_v1.2_gpt5` (2025-09-29, 71.2%); `20250929_mini-v1.13.3_sonnet-4-5-20250929` (2025-09-29, 70.6%); `20250930_zai_glm4-6` (2025-09-30, 68.2%); `20251014_Lingxi_kimi_k2` (2025-10-14, 71.2%); `20251015_Prometheus_v1.2.1_gpt5` (2025-10-15, 74.4%); `20251021_SalesforceAIResearch_SAGE_bash_only` (2025-10-21, 73.0%); `20251103_SalesforceAIResearch_SAGE_OpenHands` (2025-11-03, 73.8%); `20251103_sonar-foundation-agent_claude-sonnet-4-5` (2025-11-03, 74.8%); `20251110_frogboss-32b` (2025-11-10, 53.6%); `20251110_frogmini-14b` (2025-11-10, 45.0%); `20251118_mini-v1.15.0_gemini-3-pro-preview-20251118` (2025-11-18, 74.2%); `20251120_livesweagent_gemini-3-pro-preview` (2025-11-20, 77.4%); `20251120_mini-v1.15.0_gpt-5.1-2025-11-13` (2025-11-20, 66.0%); `20251124_mini-v1.16.0_claude-opus-4-5-20251101` (2025-11-24, 74.4%); `20251124_mini-v1.16.0_gpt-5.1-codex` (2025-11-24, 66.0%); `20251124_mini-v1.17.0_minimax-m2` (2025-11-24, 61.0%); `20251127_openhands_claude-opus-4-5` (2025-11-27, 77.6%); `20251201_mini-v1.17.1_deepseek-v3.2-reasoner` (2025-12-01, 60.0%); `20251201_mini-v1.17.1_glm-4.6` (2025-12-01, 55.4%); `20251205_sonar-foundation-agent_claude-opus-4-5` (2025-12-05, 79.2%); `20251209_mini-v1.17.2_devstral-2512` (2025-12-09, 53.8%); `20251209_mini-v1.17.2_devstral-small-2512` (2025-12-09, 56.4%); `20251210_mini-v1.17.2_kimi-k2-thinking` (2025-12-10, 63.4%); `20251211_mini-v1.17.2_gpt-5.2-2025-12-11` (2025-12-11, 69.0%); `20251211_mini-v1.17.2_gpt-5.2-2025-12-11-high` (2025-12-11, 71.8%); `20251215_livesweagent_claude-opus-4-5` (2025-12-15, 79.2%).

**2026** (13): `20260217_mini-v2.0.0_claude-4-5-haiku-high` (2026-02-17, 66.6%); `20260217_mini-v2.0.0_claude-4-5-opus-high` (2026-02-17, 76.8%); `20260217_mini-v2.0.0_claude-4-5-sonnet-high` (2026-02-17, 71.4%); `20260217_mini-v2.0.0_claude-4-6-opus` (2026-02-17, 75.6%); `20260217_mini-v2.0.0_deepseek-3-2-high` (2026-02-17, 70.0%); `20260217_mini-v2.0.0_gemini-3-flash-high` (2026-02-17, 75.8%); `20260217_mini-v2.0.0_glm-5-high` (2026-02-17, 72.8%); `20260217_mini-v2.0.0_gpt-5-2-high` (2026-02-17, 72.8%); `20260217_mini-v2.0.0_gpt-5-mini` (2026-02-17, 56.2%); `20260217_mini-v2.0.0_kimi-k2-5-high` (2026-02-17, 70.8%); `20260217_mini-v2.0.0_minimax-2-5-high` (2026-02-17, 75.8%); `20260226_mini-v2.0.0_gemini-3-pro-high` (2026-02-26, 0.0%); `20260901_mini-v2.4.2_gemini-3-5-flash` (2026-09-01, 71.8%).

Skipped (folder present, no per-instance results published): `20250720_mini-v0.0.0-Llama-4-Scout-17B-Instruct`; `20250720_mini-v0.0.0-gpt-4o-2024-11-20`; `20250720_mini-v0.0.0_gpt-4.1-mini-2025-04-14`; `20250726_mini-v1.0.0_gemini-2.0-flash`; `20250726_mini-v1.0.0_gemini-2.5-flash`; `20250726_mini-v1.0.0_gpt-4.1-2025-04-14`; `20260219_mini-v2.0.0_gpt-5-2-codex`.
