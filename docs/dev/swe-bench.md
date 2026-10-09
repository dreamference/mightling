# SWE-bench

Developer notes behind the SWE-bench line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md`, whose §12 records what was built and wins over the design above it.

SWE-bench runs `ling` inside each instance's own container (`dreamference/swe_bench/`, `ling-admin swe-bench`). The number it prints compares configurations on this machine and is not a leaderboard score; the report says why every time.

## Found the hard way

1. The upstream task repository's Dockerfiles are pinned to `linux/amd64`, so the **only arm64 images are the third-party `greynewell/swe-bench-arm64`** (400 of Verified's 500), and the harness is pointed at them with a local dataset `.jsonl` whose `image` column is rewritten, not with fake tags.
2. An image existing proves nothing, so an instance is run only once it is **validated** here (its gold patch resolves and a no-op patch does not; the harness never runs an *empty* patch, which is why the no-op exists).
3. The installed `ling` needs glibc 2.39 and the images have 2.35, so `SweBenchRuntime` builds a **patchelf'd copy with the host's loader and libc** mounted at `/opt/ling`, stamped by the binaries' hash.
4. Codex's sandbox cannot start in a container, so the agent runs with the bypass flag and **the container is the sandbox** (an internal Docker network that reaches the model server at its gateway and nothing else), which is also why `/airgapped on` cannot be set there (`ling` refuses `on` with the bypass flag).
5. The image's `PATH` puts conda's base environment first, so the container's `PATH` leads with the `testbed` environment.
6. `HEAD` is not `base_commit` in those images, so the patch is collected against the tree the agent started from.
7. The upstream eval script resets the test patch's files with one `git checkout <base> <files>`, which resets nothing when the test patch adds a file. The dataset file each harness call gets rewrites that line to reset file by file (`SweBenchHarness.per_file_reset`), so the agent's own test edits never survive into grading (`specs/DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md` §5.2, §8).

## Arms and regrades from the failure analysis

`run --task-rules tests` adds three lines of test discipline to the task prompt, and `tests-v2` the same three with the issue, not the old test, deciding whether a change that fails an old test is wrong. `issue-v1` adds three lines about the issue and the code around the fix: work out exactly what the issue asks for (including the edge cases it names) before editing, follow the pattern of the sibling code that does the same thing and fix a sibling with the same defect, and run the issue's example after the last edit. Rules stack (`--task-rules tests-v2,issue-v1`), each block once, always in `TASK_RULES`' order (`issue-v1` first); an arm with several rules is named by joining them with `-` (`n3-tests-v2-issue-v1`), since a run's name may hold only letters, digits, `.`, `_` and `-` (the grader names Docker containers after it). `eval --drop-test-hunks` grades a run's predictions again with their test files left out, in a grading series of its own (`eval-drop-test-hunks/`). Fresh tasks outside `sample-100.txt` are validated and drawn with `scripts/swe_bench_fresh.py`, and `scripts/swe_bench_night1.sh` runs the first pair, default against `tests-v2`. See the failure analysis's §8.

## Night 2: production against a candidate checkpoint

`scripts/swe_bench_night2.sh` compares two models instead of two harness arms. It runs production's arm, swaps the model server to the candidate `qwen3.8-27b-minima-nvfp4-dflash2` by the normal `server stop` / `server start --model` path, runs the candidate's arm, and serves production again whatever happens: an EXIT trap restores it on an error, and a separate unit restores it on a stop or a signal. It times both checkpoints with `scripts/decode_speed.py`. `check` changes nothing, and `DRY_RUN=1 … run` prints the night without executing it. Plan, fairness and decision rule: `specs/DREAMFERENCE_MODELS.md` §2.2.

## The code index in a run

`--code-index universal` indexes each instance's `/testbed` on the host (copied out of the image) and mounts the index read-only with a relocated `ling-code`. In the first with/without pair (24 instances, 13 resolved in each) **the agent never queried it**, so that pair says nothing about the index, and the report says so whenever that happens.

## Shared with Night Shift

Admission, the start checks and the runner lock are Night Shift's, imported: a benchmark run and a night run exclude each other, and the lock file names its holder. A paired node serving the same model can be an extra lane (`[swe_bench] nodes`), reached through a relay on the run's network gateway; see [node.md](node.md).

## The model gate: the benchmark first

Since 2026-10-09 a run has priority over the model server (spec §18). `server start` puts the engine on `127.0.0.1:<port + 10000>` and starts `dreamference-gate-<port>`, a small streaming proxy in the engine's image (`vllm_server/model_gate_service.py`, standard library only), on the public port. While a run holds it (`SweBenchGateHold`: `~/.local/state/dreamference/model-gate/run.json`, heartbeat every 15 s, ignored after 180 s), a request from outside the run's network subnet is answered 503 with the run, its progress and time left, and `Retry-After: 0` (the agent retries 5xx about 30 times and honours `Retry-After` without a cap). The run then neither waits for open sessions nor for other requests. `ling-admin night pause|resume` lets others through; the run waits for them as before and records the interval in `gate.json`, which the report prints. Keep when touching this: one request per connection (`Connection: close` towards the engine), read-only `GET`s always pass (the launcher reads `/v1/models`), the gate's log never contains the loading monitor's readiness words, and tests never probe a real gate (conftest stubs `ModelGate.probe`; restore it from `REAL_GATE_PROBE`).

## The review turn

`run --review-turn` (an arm, recorded as `review_turn` in the manifest, off by default; spec §19) resumes the agent's session once more after it stops and before the patch is collected: re-read the issue, read the diff (`git status`, `git diff`), run the tests of the changed modules, fix what does not hold, stop (`REVIEW_PROMPT` in `swe_bench_instance_run.py`). It is one `ling exec … resume` in the same container, so the model gate passes it, and it sits outside the `nudges` budget. Keep when touching this: it runs only on a changed tree after a turn that ended normally; the task's deadline is not reset, and a review cut off by it leaves the status `done` with `review.exec = timeout`; the patch before the turn is kept as `scratch/<id>/patch-before-review.diff`, and `COLLECT_SCRIPT` leaves the index at `HEAD` so the agent's `git diff` still shows its change; the per-instance `review` record (lines added and removed, time, tokens) feeds the report's `Review turn` line, which every report prints. Without a recorded session it falls back to a fresh session given the issue and the diff.

## Tests

Every `docker` call goes through `SweBenchDocker`, and the cache and results directories are module-level constants, so the tests (`tests/test_swe_bench.py`, where a container is a scratch git repository) never start a container or touch the real cache.

SWE-bench's stored `puffin_version` / `puffin_code_calls` keys keep their old names on purpose.
