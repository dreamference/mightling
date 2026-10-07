# Cave-mode benchmark

The measurements behind [specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md](../../specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md) §1.1, kept so they can be re-run when the default model changes. It drives the real `mling` against the model server on this machine, so it is not part of the test suite (`conftest.py` keeps pytest out of `tasks/`, whose workspaces contain a deliberately failing test).

```bash
cd scripts/cave_mode_bench
../../.venv/bin/python run.py --reps 1,2,3,4 --levels off,lite,full,ultra --out results/new.jsonl
../../.venv/bin/python drift.py --levels off,ultra,ultra+R --rep 1 --out results/drift-new.jsonl
../../.venv/bin/python analyze.py results/new.jsonl
../../.venv/bin/python analyze.py --drift results/drift-new.jsonl
```

- **Runs one at a time.** A run is `mling exec -s workspace-write` in a fresh git workspace with a fresh `CODEX_HOME`, so no memory or session leaks between runs. Levels and tasks are interleaved per repetition, because the server's speed drifts with whatever else it serves.
- **A level is passed as `developer_instructions`**, the text the `cave_mode` World State fragment would add. `+R` in `drift.py` adds the level's one-line reminder before each later user message, which is where the per-turn reminder lands.
- **Tokens are counted with the served model's tokenizer** (`tok.py` finds Qwen3.8's in the Hugging Face cache; `CAVE_TOKENIZER` overrides it).
- **Each task has an automatic check** (`tasks/<task>/check.py <workspace> <final-answer-file>`). Four are coding tasks checked by tests; five are questions checked for the facts the answer must contain. `safety` also checks that the agent did not run the destructive command.
- **Workspaces and sessions** go to `/tmp/cave_mode_bench_runs` (`--runs`, `CAVE_RUNS`), not into the repository.
- **Compare a level only with `off` from the same results file** (see `analyze.py`).
- **`mling`'s own cave mode is switched off for every run** (`DREAMFERENCE_MIGHTLING_CAVE_MODE=off`, since 2026-10-02), so the level under test is the only one the model sees.
- **A level name resolves to `levels/<name>.txt`, else `levels/history/<name>.txt`**, so an older or candidate text (`ultra_v4`, `ultra_v5`) runs without being copied over the shipped one.
- **`wrote_files` and `pass_strict`** (since batch 6): the files a run created or changed, not counting what the task's setup leaves uncommitted, and a pass that also wrote nothing on a question task. `analyze.py` prints `strict` when a file has them.
- **`drift.py` asks nine turns** (the ninth, "Give me the full explanation of your last answer.", tests the way out). `+RS` sends the reminder like `+R` but skips it on a turn whose message asks for more detail.
- **Pin the binary for a long batch:** copy `mling` and `codex-code-mode-host` aside and put that directory first on `PATH`, so a rebuild during the batch cannot change what is measured.

## Results of 2026-09-30/10-01

Qwen3.8-27B NVFP4 on SGLang, thinking off.

| File | Raw variant name | Level text | Notes |
|---|---|---|---|
| `results/batch1.jsonl` | `off` | none | |
| | `A_full` | `levels/history/full_v1.txt` | appended to the unchanged system prompt, as the design does |
| | `B_off`, `B_lite`, `B_full`, `B_ultra` | `levels/history/off_voice_moved_v1.txt`, `lite_v1`, `full_v1`, `ultra_v1` | **rejected design:** Codex's voice cut out of the system prompt with a modified model catalog, which this harness no longer builds; raw numbers kept. One `debug` record carries `pass_strict: false`: it was re-scored after the check was widened to accept "iterat…" as naming the cause |
| `results/batch2.jsonl` | `off`, `A_full_v2`, `A_ultra_v2` | `full_v2`, `ultra_v2` | v2 made the grammar rules permissive ("may go") and dropped the example |
| `results/batch3.jsonl` | `off`, `A_lite_v3`, `A_full_v3`, `A_ultra_v3` | `lite_v3`, `full_v3`, `ultra_v3` | directives and the example restored; "answer every question" and the why-rule added |
| `results/batch4.jsonl` | `off`, `A_full_v4` (questions only), `A_ultra_v4` (`debug` only) | `full_v4`, `ultra_v4` | v4 adds "a fix shows the code or command" (both) and a five-sentence budget (full) |
| `results/batch5.jsonl` | `off`, `A_ultra_v4` | `ultra_v4` | confirmation of the shipping text on all nine tasks |
| `results/drift.jsonl` | `off`, `A_full_v3`, `A_ultra_v3`, `A_full_v3+R`, `A_ultra_v3+R`, `A_full_v4+R`, `A_ultra_v4+R` | as above | eight turns (nine in rep 4, which adds "Give me the full explanation of your last answer.") |

## Results of 2026-10-02 (ultra v5, rejected)

Same model; several other jobs on the server (median `off` run 152.8 s). Spec §1.3 has the verdict.

| File | Variants | Notes |
|---|---|---|
| `results/batch6.jsonl` | `off`, `ultra_v5` | nine tasks × 4; the first batch with `wrote_files`/`pass_strict` (recomputed after the run for `safety`, whose setup leaves `app.py` uncommitted) |
| `results/batch6-openq.jsonl` | `ultra_v4`, `ultra_v5` | the open how-to only, 8 more runs each |
| `results/drift6.jsonl` | `off`, `ultra_v4+R`, `ultra_v5+R`, `ultra_v5+RS` | three nine-turn sessions each (reps 5–7) |
| `results/drift7.jsonl` | `off`, `ultra_v5t+R` | v5's text with v4's reminder, three sessions each (reps 8–10) |

`levels/lite.txt`, `full.txt` and `ultra.txt` are the shipping texts (`lite_v3`, `full_v4`, `ultra_v4`), with their reminders in `*.reminder.txt`.
