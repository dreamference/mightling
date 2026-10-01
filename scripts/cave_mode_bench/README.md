# Cave-mode benchmark

The measurements behind [specs/DREAMFERENCE_PUFFIN_CAVE_MODE.md](../../specs/DREAMFERENCE_PUFFIN_CAVE_MODE.md) §1.1, kept so they can be re-run when the default model changes. It drives the real `puffin` against the model server on this machine, so it is not part of the test suite (`conftest.py` keeps pytest out of `tasks/`, whose workspaces contain a deliberately failing test).

```bash
cd scripts/cave_mode_bench
../../.venv/bin/python run.py --reps 1,2,3,4 --levels off,lite,full,ultra --out results/new.jsonl
../../.venv/bin/python drift.py --levels off,ultra,ultra+R --rep 1 --out results/drift-new.jsonl
../../.venv/bin/python analyze.py results/new.jsonl
../../.venv/bin/python analyze.py --drift results/drift-new.jsonl
```

- **Runs one at a time.** A run is `puffin exec -s workspace-write` in a fresh git workspace with a fresh `CODEX_HOME`, so no memory or session leaks between runs. Levels and tasks are interleaved per repetition, because the server's speed drifts with whatever else it serves.
- **A level is passed as `developer_instructions`**, the text the `cave_mode` World State fragment would add. `+R` in `drift.py` adds the level's one-line reminder before each later user message, which is where the per-turn reminder lands.
- **Tokens are counted with the served model's tokenizer** (`tok.py` finds Qwen3.8's in the Hugging Face cache; `CAVE_TOKENIZER` overrides it).
- **Each task has an automatic check** (`tasks/<task>/check.py <workspace> <final-answer-file>`). Four are coding tasks checked by tests; five are questions checked for the facts the answer must contain. `safety` also checks that the agent did not run the destructive command.
- **Workspaces and sessions** go to `/tmp/cave_mode_bench_runs` (`--runs`, `CAVE_RUNS`), not into the repository.
- **Compare a level only with `off` from the same results file** (see `analyze.py`).

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

`levels/lite.txt`, `full.txt` and `ultra.txt` are the shipping texts (`lite_v3`, `full_v4`, `ultra_v4`), with their reminders in `*.reminder.txt`.
