# Mightling on SWE-bench — `ling-admin swe-bench`

**Status:** Phase 1 implemented on 2026-10-01 in `dreamference/swe_bench/`, with `ling-admin swe-bench {setup,smoke,run,eval,report,status,clean}`. §1–§11 are the design as specified; **§12 records what was built, what Phase 0 measured, and where the build departs from the design**, and wins where the two disagree. Not built: local image builds (impossible on arm64 as upstream ships them), per-repository image cycling for a full run, the mini-SWE-agent baseline column.
**Target:** the `ling` terminal agent and the model it is served by, measured on the GB10 itself.
**Command:** `ling-admin swe-bench {setup,smoke,run,eval,report,status,clean}`. It lives in `ling-admin`, not in the `ling` binary (§2 says why).
**Builds on:**
- `ling exec --json` ([MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md));
- Night Shift's host probes, admission, memory-capped scopes and runner lock ([MIGHTLING_NIGHT_SHIFT §5, §11](./DREAMFERENCE_MIGHTLING_NIGHT_SHIFT.md));
- the per-task compaction limit ([MIGHTLING_COMPACTION](./DREAMFERENCE_MIGHTLING_COMPACTION.md));
- `VLLMServerManager.check_host_safety()` ([INFERENCE](./DREAMFERENCE_INFERENCE.md));
- the upstream harness, `swebench` 5.0.2 (PyPI, 2026-10-01), and its task repository `SWE-bench/swe-bench-tasks`.

---

## 1. Goal

One command runs `ling` over SWE-bench instances on this machine and says how many it resolved:

- the agent gets an issue and a repository, and produces a patch;
- the upstream harness applies the patch and runs the instance's tests;
- a report gives the resolved rate, with everything needed to reproduce it.

**What the number is for.** Comparisons *on this machine*: one model against another, a prompt change, cave mode on and off ([MIGHTLING_CAVE_MODE](./DREAMFERENCE_MIGHTLING_CAVE_MODE.md)), a retrained drafter ([SELF_SPEEDING](./DREAMFERENCE_SELF_SPEEDING.md)), a Codex bump. `benchmark_server` measures tokens per second; nothing today measures whether the agent's work is right.

**What the number is not.** A leaderboard score. §8 lists why, and the report prints those reasons beside every figure.

**Non-goals:**
- **Submitting to a leaderboard.**
- **Other benchmarks** (SWE-bench Pro, SWE-bench-Live, Multimodal). The layout of §4 leaves room for a second dataset; none is specified.
- **Running during interactive use.** A run gives way to it, as Night Shift does.
- **Starting, stopping or loading the model server.** A run measures whatever is being served.

---

## 2. Where the command lives

**`ling-admin swe-bench`, in Python.** Three reasons:

1. **Long-running Docker orchestration already lives there:** `benchmark_server`, `night run`, `codex test`. The host probes, scopes and locks a benchmark run needs are Python classes (`NightShiftHost`, `NightShiftQueue.runner_lock`).
2. **`ling` is Codex.** A subcommand of the binary is either a Codex patch, and the series stands at 26,933 of its 27,500 bytes, or launcher code that would have to reimplement those probes in Rust.
3. **The harness is a Python package** with heavy dependencies (`datasets`, `docker`), which belongs in a virtualenv of its own beside `ling-admin`, not in a Rust binary.

**Open (§10.1):** the user asked for a "ling command line command". The launcher already handles `ling night …`, `ling app` and `ling update` before Codex parses its arguments, so `ling swe-bench …` could exec `ling-admin swe-bench …` from `ling-rs/` at no patch cost. It is not specified here because it adds nothing but a second spelling.

---

## 3. The commands

| Command | What it does | Needs the model |
|---|---|---|
| `swe-bench setup` | Creates the harness virtualenv, clones the task repository, downloads the dataset, checks Docker, disk and architecture. Builds or pulls no instance image. | no |
| `swe-bench smoke` | Proves the whole pipeline on a handful of instances (§7.1). `run` refuses until a smoke has passed on this machine with the current harness version. | yes |
| `swe-bench run [--dataset verified] [--instances …] [--limit N] [--name <run>]` | The agent phase: one `ling exec` per instance, producing `predictions.jsonl`. Resumes a run of the same name. | yes |
| `swe-bench eval <run>` | The grading phase: the upstream harness applies each patch and runs the tests. | no |
| `swe-bench report [<run>] [--against <run>]` | Prints the resolved rate and the manifest; with `--against`, the per-instance difference between two runs. | no |
| `swe-bench status` | Runs, their progress, images and disk used. | no |
| `swe-bench clean [<run>] [--images]` | Removes a run's containers and scratch; with `--images`, the instance images. | no |

**Why `run` and `eval` are separate:**
- `eval` uses no model, so it can run beside the server with different parallelism, or with the server stopped.
- A run can be graded again, after a harness fix or with a different image source, without paying for the agent a second time.
- An interrupted `run` loses nothing that was graded, and an interrupted `eval` loses no prediction.

`swe-bench run --eval` does both in order, for the unattended case.

**Datasets.** `--dataset verified` (500 instances, human-validated, all Python) is the default because it is the one most numbers elsewhere are quoted on. `verified` and `full` (2,294) are the harness's own aliases. `lite` (300) is not one: this command maps it to the dataset id `SWE-bench/SWE-bench_Lite`, which the harness passes through. Multimodal and Multilingual are out of scope.

**Selecting instances.** `--instances a,b,c`, `--limit N` (the first N of a fixed order: sorted by `instance_id`, so two runs with the same limit cover the same instances), or `--subset <file>` of ids. The default for `run` is every instance the machine can evaluate (§5.3).

---

## 4. Files

Everything is under `~/.local/share/dreamference/swe-bench/` (results) and `~/.cache/dreamference/swe-bench/` (rebuildable):

```text
~/.cache/dreamference/swe-bench/
  venv/                     the harness, pinned (swebench==5.0.2)
  swe-bench-tasks/          the task repository, at a pinned commit
  datasets/                 the dataset snapshot, at a pinned revision
  validated-<arch>.json     which instances pass their gold patch here (§5.3)

~/.local/share/dreamference/swe-bench/runs/<run>/
  manifest.json             what was measured (§6.4)
  instances/<id>.json       one instance's state, replaced whole
  logs/<id>.jsonl           ling exec's events
  predictions.jsonl         appended, one line per finished instance
  eval/<n>/                 the harness's logs/evaluation/<run_id>/ for grading n
  report.md                 written by `report`
```

`predictions.jsonl` is the harness's own format: `instance_id`, `model_name_or_path`, `model_patch`. An instance whose agent run failed or produced nothing is written with an empty patch, so it counts as unresolved and is never silently missing from the denominator.

**`model_name_or_path`** is `mightling-<codex tag>-<patch series hash, 8 hex>/<served model id>`, so a result stays attributable to the build and the model that produced it.

---

## 5. The agent phase (`run`)

### 5.1. One instance

The agent runs **inside the instance's own image**, not in a checkout on the host:

- SWE-bench repositories need their pinned environment to run their tests, and an agent that cannot run tests is not the agent being measured. Night Shift's answer, the main checkout's `.venv`, has no analogue here.
- The image has the repository at `/testbed`, at the instance's `base_commit`, with its dependencies installed.

Steps:

1. **Start a container** from the instance image (§5.3):
   - network: `mightling-swe-bench`, an internal Docker network (§5.2);
   - mounts: the installed `ling` binaries read-only at `/opt/ling`, a fresh `CODEX_HOME` per instance, the instance's log directory;
   - limits: `--memory` and `--memory-swap` equal to `[swe_bench] task_memory`, `--cpus 4`, `--pids-limit`;
   - environment: `DREAMFERENCE_VLLM_HOST` pointing at the network's gateway, and `MIGHTLING_NIGHT_RUN=1` so the run's own sessions are never taken for someone working.
2. **Remove what gives the answer away.** In `/testbed`: confirm `HEAD` is `base_commit`, delete every other ref and the reflog, and run `git gc --prune=now`, so the fixing commit cannot be found in the object store.
3. **Run the agent:** `ling exec --json --dangerously-bypass-approvals-and-sandbox -C /testbed -c model_auto_compact_token_limit=<task_context> "<prompt>"`, with `stdin` from `/dev/null`.
   - **No Codex sandbox, deliberately.** Inside a container Codex's Linux sandbox cannot start (measured, §9: "bubblewrap … is missing here", and every shell command fails). The container is the sandbox: no network but the model, no mount but its own scratch.
   - `--skip-git-repo-check` is not needed: `/testbed` is a repository.
   - **Which user** runs the agent in the container is a Phase 0 question (§9): the images own `/testbed` and the conda environment as root, so the host's uid probably cannot write there, while root inside leaves root-owned logs in the mounted directories and needs `safe.directory` for git.
   - The prompt is §5.4.
4. **Nudge on a stall**, exactly as Night Shift does: if `/testbed` has no change and the last message announces work, resume the session with the nudge, at most `nudges` times.
5. **Collect the patch:** `git -C /testbed add -A`, then `git diff --cached <base_commit>` limited to text files, taken by the runner from outside the agent's session.
   - **Binary files are left out.** A repository whose `.gitignore` misses `__pycache__` (Night Shift's live run hit exactly this) yields "Binary files differ" stubs, which `git apply` rejects, failing the *whole* patch for a reason unrelated to the model. Files that `git diff --cached --numstat` reports as binary (`-	-`) are excluded from the diff, and their names are recorded in the instance state.
   - **Test files stay in the patch.** The harness applies `model_patch` and then the instance's `test_patch`; a model patch that edits the same test files can make `test_patch` fail to apply, and the instance is then unresolved. Stripping test hunks would hide that, and the reference agent (mini-SWE-agent) does not strip them, so neither does this. The report counts such instances separately (§6.3).
6. **Record and remove.** Append the prediction, write the instance state (`done`, `empty`, `stalled`, `timeout`, `error`), remove the container.

**Limits per instance:** `task_timeout` wall clock (default 45 min, nudges included), enforced by the runner with SIGTERM and then SIGKILL to the container; and the compaction limit above, which keeps one instance inside the KV budget parallelism was computed from.

### 5.2. Network

**The agent has no network except the model server.** With one, it can find the real fix: every instance is a public GitHub issue with a merged pull request, and `ling`'s prompt tells the model to run `ling-search` and `ling-fetch`.

- **Mechanism:** `docker network create --internal mightling-swe-bench`. The model server listens on every interface by design, so the container reaches it at the network's gateway address and reaches nothing else. Measured (§9): from such a network `http://<gateway>:8000/v1/models` answers 200, and both `https://1.1.1.1` and `https://github.com` fail.
- **Cost:** the prompt still names the web commands, and they fail. The model may spend turns finding that out until `/airgapped on` exists ([MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md)), which a run then sets.
- **No package installs:** an instance whose tests need a dependency the image lacks cannot fetch it. That is the benchmark's rule too.
- The web binaries and `ling-admin` are **not** mounted into the container.

### 5.3. Images, and the architecture problem

The GB10 is aarch64. The official instance images are x86_64 only, and this machine has no x86 emulation registered (§9). Three sources, in the order `setup` and `run` try them:

| Source | What | State |
|---|---|---|
| **Local build** | The v5 harness builds images from `swe-bench-tasks` with Buildx (`--task-repo`), which upstream calls "experimental" on arm64. | Supported path upstream. How many of the 500 build here is unmeasured. |
| **Community arm64 images** | `greynewell/swe-bench-arm64` on Docker Hub: 1,721 tags, arm64, about 0.9 GB each as listed. | Third-party, unaudited. Its author reports 1,798 of 2,294 full-set instances building natively and the rest needing x86 emulation. Used only with `--images community`, and recorded in the manifest. |
| **x86 emulation** | The official images under QEMU. | Not available here without installing `qemu-user-static`; reported about six times slower. Out of scope. |

**Only validated instances are run.** An image that builds is not evidence that the instance works on arm64: a test can fail for reasons that have nothing to do with the patch, or pass without one. An instance is **validated** when both hold here:

- the reference patch resolves it (the harness with `--gold`);
- an empty patch does not. An instance whose `FAIL_TO_PASS` tests already pass on arm64 would otherwise count as resolved for every run.

So:

- `swe-bench setup --validate` (and `smoke`, for its handful) grades each instance twice, with the gold patch and with an empty one, and writes the instances that pass both to `validated-<arch>.json`, with the image source and digest. The second pass doubles the test time of validation; the image builds, which dominate, are shared.
- `run` defaults to that list. An instance outside it is skipped and reported as **excluded**, never as unresolved.
- The report's denominator is the validated set, and it prints both numbers: "resolved R of V validated (500 in the dataset, 500 − V not evaluable on arm64)".

Validating all 500 is itself a long job (image builds plus two test runs each). It is resumable, runs with the model server up, and is admitted like `eval` (§6.2).

### 5.4. The prompt

```text
This is an unattended task in the repository at /testbed. Nobody will answer questions:
where something is unclear, make the reasonable choice.

- Fix the issue below by changing the repository's source files.
- You may run the repository's tests. There is no network.
- Do not commit. Your changes are collected when you stop.

Issue:
<problem_statement>
```

**The prompt contains `problem_statement` and nothing else from the dataset row.** Never `hints_text`, `patch`, `test_patch`, `FAIL_TO_PASS` or `PASS_TO_PASS`; the dataset row carries all of them. A test asserts that the composed prompt and the container's files contain none of those fields' text.

Cave mode stays at whatever the configuration says, since that is the agent as shipped, and the level is recorded in the manifest; comparing levels is one of the uses of §1.

### 5.5. Coexistence with the model server

Reused from Night Shift wherever the mechanism is the same, with the one difference named below:

- **Admission** (`NightShiftRunner.admit`): the server answers, host-safety checks pass, the memory reserve holds, no heavy job is running, and the model has been idle for `idle_minutes`. A SWE-bench run adds one check: free disk above `disk_reserve` (§6.2).
- **One runner at a time:** a SWE-bench run takes `runner.lock`, so it and a night run exclude each other, and `server start`, `codex build` and `index` refuse while it holds the lock. `_refuse_during_night_run` prints a fixed "A Night Shift run is in progress" today; the lock file must record who holds it, and the message must name the holder.
- **Parallelism:** `max(1, min(max_parallel, floor(KV pool / task_context)))`, 3 today.
- **Interactive use wins:** an open `ling` session or an outside request stops new instances from starting; running ones finish. **Since 2026-10-09 the benchmark wins instead (§18):** behind the model gate, other requests are refused while a run lasts, and this rule applies only while the gate is paused or absent.
- **Memory:** each agent container is capped by Docker (`task_memory`), and a start is admitted only if `MemAvailable`, less what the running containers may still grow into, leaves the 8 GiB reserve plus one more cap. Night Shift's probe reads a systemd scope's `MemoryCurrent`, which says nothing about a container: a container's processes belong to dockerd's cgroup, not to the scope of the `docker run` client. This run reads the container's own cgroup (`docker stats --no-stream`, or `memory.current` under the container's cgroup path). That probe is new; the admission arithmetic around it is the shared one.

The shared pieces move out of `dreamference/night_shift/` into a neutral module both import, in the same change; the spec does not allow a second copy of the admission logic.

**An unattended run fits the night.** `swe-bench run --until 07:00` stops starting instances at that time and resumes the next time it is called. Queueing a run through `/night` is not specified.

---

## 6. The grading phase (`eval`)

### 6.1. The harness

`eval` calls the upstream harness from its own virtualenv, never a reimplementation:

```text
swebench eval <dataset> -p runs/<run>/predictions.jsonl --run-id <run>-<n> -j <workers> --task-repo <tasks>
```

- **`--run-id` is `<run>-<n>`, and `n` names a grading, not a set of patches.** Within a run a prediction never changes: `predictions.jsonl` is append-only and a resumed `run` skips finished instances. What can change is the grader: `n` increases when the harness version, the task-repository commit or the image source or digests differ from those grading `n` recorded, and a new grading then covers every instance again. This matters because the harness caches by `run_id` and `instance_id` only and would otherwise return the old result.
- **`-i`** limits a call to the instances not yet graded under the current `n`, which is what makes `eval` resumable and lets it follow a `run` that is still producing predictions.
- The harness's `logs/evaluation/<run_id>/` is moved into the run's `eval/<n>/`. `report` reads `results.json` of the highest `n` only, and names `n` and what it was graded with.

### 6.2. Resources

Grading uses no model, but it runs beside one:

- **Workers:** `eval_workers`, default 4. Upstream's guidance is fewer than `min(0.75 × cores, 24)`, 15 on this machine's 20 cores; memory, not cores, is the limit here, with roughly 27–40 GB available while the server is resident.
- **Memory:** whether the harness caps its containers is unverified. Until it is, `eval` runs its workers under one systemd scope with `MemoryMax`, and admission requires the 8 GiB reserve on top.
- **Disk is the scarce resource.** 388 GB are free, shared with the model caches. Docker Hub lists the community images at about 0.9 GB each, which is the **compressed** size: 500 of them are 450 GB before unpacking, and unpacked layers are commonly two to three times larger. Layers shared within a repository pull the other way, by an amount Phase 0 has to measure (`docker system df` after ten instances of one repository). Until then the whole set must be assumed not to fit, so the rules below are what makes a full run possible, not a precaution:
  - admission for `setup --validate`, `run` and `eval` requires `disk_reserve` (default 100 GB) free, and each stops cleanly, resumable, when the reserve is reached;
  - images are built and removed **per repository**: the instances of one repository are run and graded together, then their instance images are removed and the shared base and environment layers kept;
  - `swe-bench status` prints `docker system df` for the benchmark's images.

### 6.3. The report

`report.md`, and the same on the terminal. The figures below show the layout only; nothing has been run:

```text
mightling-0.158.0-3fa9c21e / RadixArk/Qwen3.8-27B-NVFP4       run 2026-10-03-a
SWE-bench Verified, arm64, local images

Resolved            212 / 431 validated   49.2%
Not evaluable        69 of 500 (no arm64 image, or gold patch fails here)
Empty patch          31     Stalled 12     Timeout 9     Agent error 2
Patch broke test_patch application     4
Median wall time    6 min 40 s per instance, 3 at once; 17 h 10 min in all

Not comparable with published SWE-bench scores: see §8 of the spec.
```

- **Per repository:** resolved over validated, since the validated set is not spread evenly.
- **`--against <run>`:** instances resolved by one run and not the other, and the difference with a paired confidence interval (McNemar on the instances both runs attempted). A difference inside the interval is reported as "no measurable difference", in those words.
- **Variance:** a single run is one sample. The spec's acceptance criteria (§7.3) include running the smoke set three times to see how much the same configuration varies before any A/B is read.
- **Optional baseline column:** `swebench infer` (mini-SWE-agent against the same endpoint) measures the model without `ling`. It is not run by default.

### 6.4. The manifest

Written when a run starts, never edited: harness version, task-repository commit, dataset revision, image source and the digests of the images used, `ling` version, Codex tag and patch-series hash, served model id and registry alias, the launch recipe's context length and speculative settings, cave-mode level, `task_context`, timeouts, nudges, parallelism, the instance list, the start time, and the git commit of this repository. `report --against` refuses to compare two runs whose datasets or validated sets differ, and lists every other field that differs.

---

## 7. Tests

### 7.1. The smoke set

`swe-bench smoke` is the gate for everything else:

1. **Gold:** the harness's own installation check, `swebench eval verified --gold -i sympy__sympy-20590 --task-repo …`, extended to five instances from five repositories. Every one must resolve. This proves images build and grade correctly on arm64.
2. **Empty:** the same five with an empty patch must all be unresolved. This proves the grader can fail.
3. **Agent:** one instance, chosen for a small fix, run through §5.1 in full, and graded. It need not resolve; it must produce a prediction, a log and a graded result.

The five ids are fixed in the code, chosen in Phase 0 from instances that validate here. A smoke takes minutes after the first image build, and its pass is recorded with the harness version; a harness upgrade invalidates it.

### 7.2. Offline tests

`tests/test_swe_bench.py`, with a scripted stand-in for `ling` and for `docker`, as `tests/test_night_shift.py` has for `ling`: nothing in the suite starts a container, pulls an image or reaches the network (`tests/conftest.py` already fails any real `docker` command that changes something). The cache and results directories of §4 are module-level constants, because the fixture that gives each test its own home re-points module attributes; a path computed inside a function would escape it and a test could write the user's real cache.

- the prompt holds the issue and none of the forbidden fields;
- an agent failure writes an empty prediction, not a missing one;
- a resumed `run` skips finished instances and appends;
- `eval` raises `n` when a graded prediction changed, and passes `-i` for the ungraded;
- excluded instances are counted as excluded;
- binary files are left out of a collected patch and named in the instance state;
- an instance whose empty patch resolves is not validated;
- admission refuses on the disk reserve;
- `run` refuses without a passed smoke, and while a night run holds the lock;
- the manifest's fields, and `--against` refusing mismatched sets.

### 7.3. Acceptance

1. `setup` on a clean machine leaves a working harness virtualenv and changes nothing in `.venv`.
2. `smoke` passes on the GB10 with the model server resident, and earlyoom stays silent.
3. The smoke set's agent step, run three times, gives the spread of one configuration.
4. A 25-instance `run` and `eval` complete unattended, are interrupted once on purpose and resume, and the report's counts add up to 25.
5. No connection from an agent container to anything but the model server (checked with the trace method of [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md) on one instance).

---

## 8. What the number means, and what it does not

Printed in short form on every report, and to be repeated wherever a figure from this command is quoted:

1. **The benchmark is contaminated.** the upstream vendor stopped reporting SWE-bench Verified in early 2026: frontier models could reproduce gold patches verbatim, and many of the hardest unsolved tasks had flawed tests. Its replacement recommendation, SWE-bench Pro, was itself withdrawn in July 2026 after an audit estimated about 30% of its tasks broken. An open-weights model has very likely seen these repositories and their fixes.
2. **The images are not the leaderboard's.** Scores elsewhere are graded in the official x86_64 images. These are arm64 images built locally or by a third party, and a subset: the denominator is what validates here, not 500.
3. **The agent is restricted** in ways others may not be (no network), and the model is a quantised build (NVFP4) with a speculative drafter.
4. **One run is one sample.**

So the number supports "configuration A resolves more of these instances than configuration B on this machine". It does not support "Mightling scores N% on SWE-bench Verified" without all four qualifications, and the report says so in its last line.

---

## 9. Checked on this machine (2026-10-01), and not

**Checked:**

| What | Result |
|---|---|
| Harness | `swebench` 5.0.2 on PyPI, Python ≥ 3.10; the v5 CLI is `swebench eval <dataset> -p … --run-id … -j …`, with `--gold`, `-i` and `--task-repo`; the older `python -m swebench.harness.run_evaluation` form still works. |
| Dataset | `princeton-nlp/SWE-bench_Verified`: one split, `test`, 500 rows; fields `repo`, `instance_id`, `base_commit`, `patch`, `test_patch`, `problem_statement`, `hints_text`, `created_at`, `version`, `FAIL_TO_PASS`, `PASS_TO_PASS`, `environment_setup_commit`, `difficulty`. |
| Official images | `swebench/sweb.eval.x86_64.django_1776_django-11099` exists and is amd64 only; the `arm64` name does not exist. |
| Community images | `greynewell/swe-bench-arm64`: 1,721 tags, arm64, e.g. `sympy-sympy-22005` at 908 MB, last pushed 2026-03-07. |
| Emulation | `/proc/sys/fs/binfmt_misc` has no QEMU entry: x86 images cannot run here as installed. |
| Host | aarch64, 20 cores, Docker 29.2.1 with Buildx 0.31.1, 388 GB free on the one filesystem Docker and the model caches share. |
| Internal network | A container on `docker network create --internal` got 200 from `http://172.20.0.1:8000/v1/models` and no connection to `https://1.1.1.1` or `https://github.com`. |
| `ling` in a container | The installed binary, mounted read-only into `python:3-slim` on that network with a fresh `CODEX_HOME` (which must exist beforehand) and `DREAMFERENCE_VLLM_HOST` set to the gateway, completed a turn against the served model. With `-s workspace-write` every shell command failed (no bubblewrap in the container) and nothing was written; with `--dangerously-bypass-approvals-and-sandbox` the command ran and the file was created. With `stdin` from `/dev/null`, `exec` still printed "Reading additional input from stdin..." and went on. |
| Upstream arm64 work | Pull request 521 (arm64 support across the harness's build and evaluation) was closed unmerged on 2026-08-12, when v5 moved dataset-specific attributes into the task repository. |

**Not checked when the spec was written** (§12.1 answers the first six; wall time and the resolved rate are in §12.5):

- How many Verified instances build with `--task-repo` on this machine, and how many of those pass their gold patch.
- Whether the community images agree with local builds, and how their tag names map to instance ids (`sympy-sympy-22005` against `sympy__sympy-22005`).
- Disk actually used per repository after layer sharing.
- Whether the harness caps its containers' memory.
- Whether `ling` runs in every instance image: the images are older distributions, and the binary needs a compatible glibc. `python:3-slim` is not evidence for an Ubuntu 22.04 conda image.
- Which user the agent must run as inside an instance image: the probe ran as the host's uid against an empty mounted directory, which says nothing about a root-owned `/testbed` and conda environment, or about git's "dubious ownership" check.
- Wall time per instance and for 500, the resolved rate, and its run-to-run spread.
- Whether removing refs and running `git gc` in `/testbed` is enough to hide the fix in every image, or whether some carry it elsewhere (a pip cache, a second clone).
- How many turns the model wastes on the web commands the prompt names.

---

## 10. Phases

- **Phase 0, measurements (no product code):** ten instances from ten repositories, validated (gold and empty) by local build and by community image; disk per repository, unpacked; harness memory behaviour; `ling` started in each of the ten images, as root and as the host's uid; one full instance by hand. The results decide the default image source and replace the estimates in §5.3 and §6.2.
- **Phase 1:** `setup`, `smoke`, `run`, `eval`, `report`, `status`, `clean`; the shared admission module; the tests of §7.2.
- **Phase 2:** `--against` with its interval, the mini-SWE-agent baseline column, `--until`.

---

## 11. Open questions

1. **`ling swe-bench` as a second spelling** (§2): add the launcher's pass-through or not?
2. **Community images:** acceptable as a default if Phase 0 shows they agree with local builds, or opt-in for good because they are third-party binaries?
3. **Installing QEMU** to reach the instances with no arm64 path: the denominator becomes 500, at about six times the grading time for those instances and one more system package.
4. **Which dataset should be the default once Phase 0 is done.** Verified is the common reference and is known to be contaminated; a held-out or newer set would mean more and compare with less.
5. **Should a run be queueable through `/night`,** so the timer starts it in the window, or is `--until` enough?
6. **Reasoning effort and cave mode for the benchmark:** as configured (the agent as shipped), or pinned per run so results do not move when a default changes? The manifest records them either way.

---

## 12. As built (2026-10-01)

### 12.1 What Phase 0 found, and what it changed

| Finding | Consequence |
|---|---|
| **The task repository cannot build arm64 images.** Every `tasks/<id>/Dockerfile` starts `FROM --platform=linux/amd64` and installs `Miniconda3-…-Linux-x86_64.sh` with x86 conda builds pinned. | §5.3's "local build" source does not exist on this machine. `--task-repo` is not used; the only source is the community repository, and the manifest says so. The clone of `swe-bench-tasks` is not needed and `setup` does not make one. |
| **400 of the 500 Verified instances have a community arm64 image**, tagged with the instance id with `__` written `-`. Missing: matplotlib 33 of 34, scikit-learn 25 of 32, xarray 22 of 22, django 9, astropy 6, sympy 3, pytest 1, sphinx 1. Docker Hub's web API stops anonymous paging at 1,000 tags, so the list is read from the registry's own `tags/list`. | The denominator can be at most 400 before validation. |
| **The harness takes a local `.jsonl` as its dataset** and uses whatever image the row's `image` column names, pulling only if it is absent. | The dataset is downloaded once (`datasets/SWE-bench_Verified.jsonl`, revision `78f471bf655a`), and each harness call gets a file with `image` rewritten to the arm64 image. No fake `x86_64` tags, no patch to the harness, and `run` and `eval` need no network once the images are here. |
| **The harness never runs an empty patch**: it files it under "empty patches" without starting a container. | §5.3's "an empty patch must not resolve" would test nothing. Validation uses a **no-op patch** (one new unrelated file) instead: gold must resolve and the no-op must not. Empty predictions are recorded as unresolved by the runner and never handed to the harness. |
| **An image that exists is not an instance that works.** Of 30 instances tried, 28 validated; `sphinx-doc__sphinx-8721` and `sphinx-doc__sphinx-8056` fail their own gold patch in the community image (8721: `No module named 'roman'`). | Confirms the rule of §5.3. The smoke set uses `sphinx-doc__sphinx-9230`. |
| **`HEAD` is not `base_commit`** in these images: one commit named "SWE-bench" sits on top, with the same tree. | The runner checks tree equality (recorded as `base_tree_equal`), not the hash, and collects the patch against **the tree the agent started from** (the checked-out commit plus whatever the image build left untracked), which is what the harness's container also has. |
| **The object store already lacks the fix** in the image inspected (`git log --all -S` for the fixed line found nothing; scrubbing removed 16 objects of 211,142, all old tags). `.git` belongs to root. | The scrub of §5.1 step 2 still runs, as **root** (`docker exec -u 0`), because a repack as the agent's user cannot replace root's pack files; `.git` is then made writable again. It took under a second. |
| **The installed `ling` does not start in an instance image**: it needs glibc 2.38/2.39, the images are Ubuntu 22.04 with 2.35. | `setup` builds a **relocated runtime** (`~/.cache/dreamference/swe-bench/runtime`): copies of `ling` and `codex-code-mode-host` whose ELF interpreter and rpath point at copies of the host's loader, `libc`, `libm` and `libgcc_s`, mounted read-only at `/opt/ling`. Only those two binaries use the copied libraries; the repository's Python uses the image's. The runtime is stamped with the hash of the binaries it was copied from, rebuilt when `codex build` replaces them, and the hash is in the manifest; a run refuses to resume with a different one. `ling-code` is not in the runtime (it needs bubblewrap), so the agent in a container works **without the code index**. |
| **Which user:** `/testbed` is mode 0777 and the image has a `nonroot` user with uid 1000. | The agent and every collecting command run as the **host's uid**, so logs and scratch files belong to the user. Git's ownership check is answered with `GIT_CONFIG_COUNT/KEY_0/VALUE_0` (`safe.directory=/testbed`) in the container's environment, which writes nothing. |
| **The image's default `PATH` puts conda's base environment first**; `conda activate testbed` happens only in the grading script. | The container's `PATH` starts with `/opt/miniconda3/envs/testbed/bin`, or the agent's `python -m pytest` fails on imports. |
| **The harness sets no memory limit** on its containers, and they belong to dockerd's cgroup, so §6.2's systemd scope would not contain them. | While the harness runs, a thread puts `docker update --memory` (`eval_memory`, default 4G) on each `sweb.eval.*.<run id>` container as it appears. Measured peaks on ten containers: 28–160 MiB. |
| **Disk:** an image is about 0.9 GB to pull (33–38 s each here) and **2.1–2.4 GB unpacked**, with little shared between repositories: 32 images took about 70 GB. | 400 images would need roughly 900 GB; 344 GB were free. The whole set does not fit at once (§12.3). |

### 12.2 Where the code is

| Piece | Path |
|---|---|
| Subcommands, `setup`, `smoke`, `status`, `clean` | `dreamference/swe_bench/swe_bench_command.py` |
| Settings (`[swe_bench]`), directories, pins, the smoke set | `dreamference/swe_bench/swe_bench_settings.py` |
| Every `docker` call | `dreamference/swe_bench/swe_bench_docker.py` |
| The relocated `ling` | `dreamference/swe_bench/swe_bench_runtime.py` |
| The upstream harness: virtualenv, dataset snapshot, `swebench eval`, its reports | `dreamference/swe_bench/swe_bench_harness.py` |
| Image names, the validated list | `dreamference/swe_bench/swe_bench_images.py` |
| One instance: container, scrub, agent, nudges, patch | `dreamference/swe_bench/swe_bench_instance_run.py` |
| A run's files | `dreamference/swe_bench/swe_bench_run_store.py` |
| Admission, scheduling, manifest, resume | `dreamference/swe_bench/swe_bench_runner.py` |
| Validation and grading | `dreamference/swe_bench/swe_bench_evaluator.py` |
| Report and `--against` | `dreamference/swe_bench/swe_bench_report.py` |
| The shared lock now names its holder | `NightShiftQueue.runner_lock(holder=…)`, `runner_holder()`; `_refuse_during_night_run` prints it |

`[swe_bench]` in `dreamference.toml`: `max_parallel` 3, `task_timeout` 45m, `task_memory` 8G, `task_cpus` 4, `nudges` 2, `idle_minutes` 10, `task_context` 49152, `eval_workers` 4, `eval_memory` 4G, `eval_timeout` 30m, `disk_reserve` 100G.

### 12.3 Departures from the design

- **Admission is imported, not moved.** §5.5 asked for the shared pieces to move to a neutral module. `SweBenchRunner` calls `NightShiftRunner.admit` and `NightShiftRunner.start_blocker` and takes `NightShiftQueue.runner_lock`: one implementation, no copy, no new module. `start_blocker` reads a running task's systemd scope; an instance run has none (`current_unit` is `None`), so each running container is assumed to grow to its full `task_memory`, which is the conservative reading and needs no container probe. With 28 GiB available and 8G caps, that admits **two** containers at once although the KV pool allows three; the measured agent container peaked at 113 MiB, so the cap is far too generous and is left for a run with more data to retune.
- **`/airgapped on` cannot be used.** §5.2 planned to set it once it existed. As built, `ling` refuses to start at `on` together with `--dangerously-bypass-approvals-and-sandbox` ("nothing would keep them off the network"), and inside a container the bypass flag is the only way to run. The run sets `DREAMFERENCE_MIGHTLING_AIRGAPPED=off` explicitly: the internal Docker network is the enforcement, the web binaries are not mounted, and the task prompt says there is no network. The system prompt still names the web commands. Open for the airgapped module: a way to say "the network is absent by other means".
- **Validation happens when a run starts, for the instances it selected**, and the manifest's instance list and exclusions are then fixed. An instance that could not be validated because the disk reserve was reached is excluded from that run. `setup --validate` does the same ahead of time.
- **Per-repository image cycling is not built.** `run --eval --remove-images` removes a repository's images after grading it, but validation still pulls every selected image first. A run over all 400 instances therefore needs the instances validated repository by repository with `clean --images` in between, and does not fit as one command today. Runs of a few dozen instances do.
- **A timeout still submits what was changed.** §5.1 lists `timeout` among the states without saying what is predicted. The container is stopped, started again, and the partial patch collected; the status stays `timeout`.
- **An interrupted run (Ctrl-C or SIGTERM) writes no prediction for the instances it cut off**; their state is `interrupted` and they run again on resume. `--until` only stops new starts.
- **The grading number rises on a changed grader**, as §6.1 says, not on a changed prediction, as one line of §7.2 said: predictions never change within a run.
- **Cave mode is passed in explicitly.** The container has no `dreamference.toml`, so the level configured on the host is resolved by the runner, set as `DREAMFERENCE_MIGHTLING_CAVE_MODE`, and recorded.
- **`--against` (Phase 2) is built**: per-instance differences, a 95% Wald interval for paired proportions and the exact McNemar p-value; "No measurable difference." when the interval contains zero. The mini-SWE-agent baseline column is not built.
- **`swe-bench eval` checks memory, not idleness:** `eval_workers × eval_memory` plus the 8 GiB reserve must be available.
- **Every agent log starts with a line that is not JSON** (`Reading additional input from stdin...`, printed by `ling exec`); readers skip it.

### 12.4 Tests

`tests/test_swe_bench.py`, 41 tests. A stand-in plays `docker`: a container is a scratch git repository on the host, and the scripts the runner executes in a container (scrub, record the starting tree, detect a change, collect the patch) run for real against it with bash and git. Another stand-in plays the harness and writes the per-instance reports the real one writes. Covered: the prompt and everything handed to the container contain none of the answer-bearing fields; the container's limits, network, user and environment; refs removed before the agent starts, as root; a change, an error, an empty answer, a stall, a nudge that works, a timeout with its partial patch; binary files left out and named; the collected patch applying to a fresh checkout, with files the image left untracked kept out of it; resume, interruption, a torn predictions line, a resume under another model; exclusion and its counts; the no-op rule; validation not repeated; an unpullable image not recorded as rejected; grading only the ungraded, never an empty patch, a new grading for a new harness, an instance without a verdict staying ungraded; the smoke gate, the lock and its holder's name, the disk reserve, Night Shift's admission, `--until`; the manifest, the report, `--against`, the statistics; `smoke` passing and failing; the runtime's library list; selection; settings; the command line; `status` and `clean`.

### 12.5 Measured runs

All on 2026-10-01, on the GB10 with the default model resident (Qwen3.8-27B NVFP4 on SGLang, KV pool 156,907 tokens), cave mode `ultra`.

- **`setup`:** harness virtualenv, dataset snapshot (500 rows), tag list (400 images), runtime. Seconds once the virtualenv exists; the project's `.venv` is untouched.
- **`setup --validate` on 29 instances** (a 25-instance sample plus the smoke set): 898 s, most of it 24 image pulls; 28 validated, `sphinx-doc__sphinx-8056` rejected (its gold patch does not resolve in the community image). Grading the five smoke instances twice takes about 90 s once the images are here.
- **`smoke`:** passed in 3 min 35 s with a one-minute idle wait. The five resolve with their gold patch and none with the no-op; the agent fixed `sympy__sympy-13480` in 43 s (one-line fix, graded resolved); the agent container peaked at 113 MiB.
- **The sample.** `~/.cache/dreamference/swe-bench/sample-25.txt`: the 25 Verified instances with an arm64 image whose `sha256(instance_id)` is smallest, so it is fixed and not chosen by difficulty. One astropy, thirteen django, one pytest, one scikit-learn, three sphinx (one excluded), six sympy.

- **The 25-instance run (`acc-25`), without the code index.** 24 ran (one excluded by validation), **13 resolved, 54.2%**; one timeout (`sympy__sympy-13877`, 45 min), no empty patch, stall or agent error; the counts add up to 25 with the exclusion. Per repository: django 7 of 13, sympy 2 of 6, sphinx 2 of 2, astropy 1 of 1, pytest 1 of 1, scikit-learn 0 of 1. Median 5 min 49 s per instance, 3 h 58 min of agent time, about 2 h 15 min on the clock; 50.7 M input tokens (49.3 M of them cached), 306 K output tokens, 2,288 commands. Agent containers peaked at 88-184 MiB, grading containers at 122 MiB. earlyoom logged nothing but its periodic memory line, and the model server answered throughout.
- **Two at a time, not three.** With 8G caps and about 28 GiB available, Night Shift's start check admitted two containers (§12.3); the run said so each time it waited.
- **Interrupted on purpose and resumed.** After four predictions, SIGTERM to the `ling-admin` process: it exited in 4 s, left no container, four parseable predictions, and the two instances it cut off in state `interrupted` with no prediction. The same command with the same `--name` then ran the remaining 20, those two among them. (The first SIGTERM went to the wrong process, a shell wrapper, and did nothing; that was the test's mistake, not the command's.)
- **`eval acc-25` through the command** found everything graded and printed 24 graded, 13 resolved; the grading itself had run inside `run --eval`.
- **No turn spent on the web commands.** The 24 logs mention `ling-search` once and `pip install` once (both inside quoted text), so §5.2's worry did not show at this size.
- **Not run as designed:** the run-to-run spread of §7.3 item 3. The pair of §13.5 turned out to be a repeat in effect, since the tool that distinguished the arms went unused, with one confound (the prompt block); it differed in two instances each way. Also not run: and the connection trace of item 5 (the internal network was checked by hand: the model server answers, `github.com` does not).

---

### 12.6 A replica's model server (2026-10-03)

A paired node serving the same model adds a lane (`[swe_bench] nodes`, default `"paired"`; [MIGHTLING_NODE §18.8](./DREAMFERENCE_MIGHTLING_NODE.md)). The containers still run here; the run listens on the internal network's gateway at a port of its own and relays each connection to that node's model port, so the network stays closed to everything else. Each instance's state notes which server answered it when it was not this machine's. With no paired node nothing changes. Not run with a second node; the relay was run live from a container on `mightling-swe-bench`.

## 13. The code index as an arm (2026-10-02)

`swe-bench run --code-index universal` gives the agent `ling-code` ([MIGHTLING_CODE_INDEX](./DREAMFERENCE_MIGHTLING_CODE_INDEX.md)); the default, `off`, is the agent of §12, which navigates with `grep` and `find` because the runtime carries nothing of the index. The arm is recorded in the manifest (`code_index`), a resumed run keeps the arm it started with, and `report --against` lists it among the fields that differ.

### 13.1 How it works

1. **The repository is indexed on the host, before any agent starts.** For each instance, `/testbed` is copied out of the instance image (`docker create`, `docker cp`, no container ever runs), `ling-code index --wait` is run in the copy with `MIGHTLING_CODE_STATE_DIR` and `CBM_CACHE_DIR` pointing into `~/.cache/dreamference/swe-bench/index/<repo>@<base commit>/`, and the copy is deleted. The index run is admitted against the host's memory budget and runs in `mightling-index.slice`, like any other. All indexes are built before the first agent container starts: Night Shift's admission refuses to start beside an index run, and index time is not the agent's time. It is recorded per instance (`index.seconds`, `index.cached`) and reported separately.
2. **The index is mounted read-only**, with a relocated `ling-code` (`runtime-code/`, the same loader-and-libc treatment as `ling`, kept in a directory of its own so the `ling` runtime's hash, which a manifest pins, does not move). Four variables tell `ling-code` where things are: `MIGHTLING_CODE_BIN` (the launcher then appends `ling-code prompt-block` to the model's prompt by itself), `MIGHTLING_CODE_STATE_DIR`, `MIGHTLING_CODE_GRAPH_DB` and `MIGHTLING_CODE_PROJECT` (the graph names its project after the host path it was built at). `ling-code` is put first on the container's `PATH`.
3. **Queries only read**, which is the code-index spec's own rule. A file the agent edits is answered by text search and tagged `heuristic (text)`, as on the host. The launcher also starts `ling-code session`, which cannot write its lock on the read-only mount and exits; nothing depends on it.
4. **A run whose index cannot be built does not start:** a run measures one arm.

### 13.2 Only the universal layer

The exact (SCIP) layer is left out, by pointing `MIGHTLING_CODE_INDEXERS_DIR` at an empty directory so scip-python counts as not installed. Measured on `sympy__sympy-13480` with both layers: 1,022 s, scip-python at 1.6-1.7 GiB per directory, and the run for the `sympy/` package itself ended with Node's "JavaScript heap out of memory" at 2 GB, leaving exact data only for `bin/`, `doc/` and `examples/`. Every instance is a different commit, so nothing is shared between instances. The universal layer alone took 19-29 s per repository with the model idle (79 s for django with two agents running). So every answer the agent gets in this arm is tagged `heuristic`, and the result says nothing about the exact layer.

### 13.3 Checked

- A host-built index is accepted in the container: `status`, `refs`, `callers` and `prompt-block` answered there, in milliseconds, and after an edit the changed file's rows turned to `heuristic (text)`.
- The prompt block arrives: the with-arm's `model_catalog.json` contains `# Code navigation`; the without-arm's 24 logs and catalogs contain neither that heading nor one mention of `ling-code`.
- `ling` was not rebuilt between the arms: both manifests carry the same `runtime_hash`.

### 13.4 The report

`report` prints, for one run, the tokens, the command count and a `Code index` line: the arm, what the indexes cost, and in how many instances the agent called `ling-code` at all (counted from `ling exec`'s command events). `report <run> --against <other>` sets two runs side by side on the instances both graded: resolved in each, median and total agent time, input and output tokens, commands, `ling-code` calls, instances that used it, index time, then how many were resolved in both, only one or neither, and one row per instance. If the agent never called `ling-code`, the report says the comparison says nothing about the index; otherwise it says in how many instances it was used. The paired interval and "No measurable difference." of §6.3 apply unchanged.

### 13.5 With and without, measured

Two runs on the same 24 validated instances (the sample of §12.5), the same `ling` build (`runtime_hash` `9d107cf700c4`), the same model, one after the other on the night of 2026-10-01: `acc-25` without the index, `acc-25-index` with the universal layer. One run per arm, so one sample of each.

| | With the index | Without |
|---|---|---|
| Resolved | 13 of 24 (54.2%) | 13 of 24 (54.2%) |
| Resolved in both / only this arm / neither | 11 / 2 / 9 | 11 / 2 / 9 |
| Median agent time per instance | 10 min 26 s | 5 min 49 s |
| Agent time in all | 5 h 1 min | 3 h 58 min |
| Input tokens (cached) | 78.4 M (75.8 M) | 50.7 M (49.3 M) |
| Output tokens | 503 K | 306 K |
| Commands | 2,824 | 2,288 |
| **`ling-code` queries** | **0, in 0 of 24 instances** | 0 |
| Index time, outside the agent's | 7 min 57 s in all, median 19 s | none |
| Timeouts / empty patches | 0 / 1 | 1 / 0 |

Difference in resolved rate: 0.0 points, 95% interval −16.3 to +16.3, McNemar p = 1.0. Only with the index: `django__django-15563`, `sympy__sympy-13877`. Only without: `django__django-16100`, `sympy__sympy-18211`.

**What this does and does not show.**

- **It says nothing about whether the index helps, because the agent never used it.** All 24 prompts carried the `# Code navigation` block and `ling-code` answered in the container, yet no command in the 24 logs asked it anything. The one command that named it was `ls /opt/ling-code/bin`. The first version of the counter counted that as a call; it now counts only `ling-code <query verb>`, and a test fixes the difference.
- **The two arms differ in four instances out of 24 with the model, build and instances unchanged and the tool unused.** That is this benchmark's run-to-run noise at this size, and the nearest thing to the spread measurement §7.3 asked for: a difference of two instances either way is not a finding.
- **The with-arm was slower and used more tokens, and the cause is not established.** The index cannot be it directly (no query was made). What differed: about 250 tokens of prompt block on every request; and whatever else used the model server that night, which was not recorded. A third run without the index would separate the two and was not made.
- **The isolation held where it was tested by the agent itself.** In `django__django-16100` the agent tried `ling-fetch` on the upstream file at `raw.githubusercontent.com`, which would have shown it the fix; the command does not exist in the container and there is no route out. Its looking around for the web commands is what named `ling-code`.
- **The manifest's `repository_commit` is `HEAD` when the run started, not proof of the code that ran:** both runs were made from a working tree with uncommitted changes, and `--against` lists the two commits as differing for that reason only.
- **Open, and the real result:** this model does not reach for `ling-code` on its own in this setting. It does elsewhere: 25 recorded session files under `~/.mightling/sessions` contain `ling-code` queries (not checked: how many of those were tests that asked for them), so the setting, a bare issue text and an unattended run, is the likelier cause than the model. Whether the task prompt should name it, or the block's wording should change, is a question for the code-index spec; an A/B where the tool is actually used needs one of those first.


### 13.6 With the tools, measured (2026-10-03)

After §13.5 the index became tools (`code_*`, CODE_INDEX §15). This pair is the first in which the agent used them. Branch `swe/index-arm`: `a7e49af`, `5e7a134`, `93386d5`, `6ffb18f`.

**What had to change first.**
- **The agent could not write `/testbed`** (MIGHTLING_PROMPT §6.1 item 1): the scrub step now opens it all, in both arms.
- **The tools were missing under load.** In the first attempt (`idx14-on`, stopped after three instances and marked `INVALID.txt`), `code_search` came back `unsupported call: code_search`: Codex waits 1 s for an optional MCP server before the first request and leaves out the tools of one that is not up (CODE_INDEX §15.4). The runner now declares `ling_code` itself with `required = true`.
- **The task prompt names the tools in this arm only** (`CODE_INDEX_HINT`, two sentences: `code_search`/`code_def` before grep, `code_impact`/`code_callers` before an edit). The system prompt's block alone had left the index unused (§13.5). A test pins the plain arm's prompt as it was.

**The pair.** `idx14b-on` then `idx14b-off`, back to back on one `ling` (`runtime_hash` `bd978d3ede04`, the build installed on 2026-10-02 15:37, which predates that day's merges) and the installed `ling-code` (`a2343be2…`). Chosen before either ran: the 14 instances of the §12.5 sample **not** resolved by both earlier arms (the 9 resolved by neither plus the 5 on which they differed; `astropy-13453` counted as differing, since the two runs' records disagree on it). The 10 others were resolved by both and say little about a difference. Departures from §12, the same in both arms: `task_context` 44,000 (the KV pool was 133,308 tokens, not 157K, and 49,152 allowed two at once), three at once, no idle wait, open sessions ignored (other tasks were using the machine), `--until 17:05`.

| | With the tools (`idx14b-on`) | Without (`idx14b-off`) |
|---|---|---|
| Resolved | 6 of 14 (42.9%) | 4 of 14 (28.6%) |
| Resolved in both / only this arm / neither | 3 / 3 / 7 | 3 / 1 / 7 |
| Median wall per instance | 10 min 9 s | 10 min 1 s |
| Agent time in all | 4 h 11 min | 3 h 40 min |
| Input tokens / output tokens | 36.4 M / 202 K | 47.4 M / 279 K |
| Commands (tool calls included) | 1,999 | 1,716 |
| `code_*` calls | 295, in 14 of 14 instances | 0 |
| Timeouts / empty patches | 2 / 0 | 0 / 1 |
| Index time, outside the agent's | 5 min 0 s | none |

| Instance | With | Without |
|---|---|---|
| astropy-13453 | resolved, 592 s | resolved, 716 s |
| django-12774 | resolved, 461 s | resolved, 436 s |
| django-13512 | 228 s | 302 s |
| django-15563 | **resolved**, timeout 2,702 s (partial patch) | 2,032 s |
| django-15957 | timeout 2,701 s | 1,518 s |
| django-16100 | **resolved**, 495 s | 2,305 s |
| django-16454 | 1,607 s | 1,603 s |
| django-16502 | 921 s | 651 s |
| scikit-learn-25747 | 413 s | 1,663 s |
| sympy-13031 | **resolved**, 494 s | empty, 140 s |
| sympy-13798 | 328 s | 552 s |
| sympy-13877 | resolved, 2,040 s | resolved, 405 s |
| sympy-17318 | 627 s | 454 s |
| sympy-18211 | 1,471 s | **resolved**, 435 s |

Difference in resolved rate: +14.3 points, 95% interval −12.7 to +41.3, McNemar exact p = 0.625; the report's own verdict is "No measurable difference". `--against` lists `repository_commit` as differing (`5e7a134` and `6ffb18f`): the worktree gained two commits between the two starts, and they change only the code-index arm's host side (`swe_bench_code_index.py`, the `ling-code.sha256` record), `ling-code-rs`, the launcher and tests; `swe_bench_instance_run.py`, the plain arm's whole path, is the same in both.

**How the tools were used** (295 calls in 14 of 14 instances): `code_show` 207, `code_search` 54, `code_impact` 17, `code_callers` 9, `code_refs` 4, `code_def` 4. 20 answers were empty: 3 searches because the model passed an absolute `path`, 10 `refs`/`impact` answers for methods the graph cannot see called (both fixed in `93386d5`, **not measured** here), 6 `show`s of a name not found and 1 other search. The run store's `mightling_code_calls` and a count from the session files agree for every instance.

**What this does and does not show.**

- **The tools are used now**: 295 calls in 14 of 14 instances, against none in 24 in §13.5. That, not the score, is the result of the fixes above.
- **The score is inside the noise.** Two arms with the tool unused differed on 4 of 24 instances (§13.5); here 4 of 14 differ, 3 one way and 1 the other. Two instances no earlier run had resolved were resolved here: `django-12774` in both arms (so more likely the write fix than the index) and `sympy-13031` only with the tools (without them it stopped at 140 s with an empty patch). That is one sample, an observation and not a finding.
- **The cost is mixed.** With the tools: 23% fewer input tokens and 28% fewer output tokens, the first efficiency number for the index; but 14% more agent time, 16% more commands, and both of the pair's timeouts (one of them resolved on its partial patch). `code_show` (207 calls) mostly replaced `sed -n` reads, which is why commands did not fall.
- **"Tests run after the last edit"** (a pattern match on the session's commands, not a measured behaviour): 9 of 14 with the tools, 8 without.
- **What remains unmeasured**: the two `ling-code` fixes (`93386d5`) and the launcher's grace (`6ffb18f`; compiled and installed in the build of `258c3b5` on 2026-10-03, after the arm), since the arm ran the installed builds. The next pair should run on a rebuilt `ling` and `ling-code`, on the full 24 validated instances, with the plain arm run twice so the floor is measured in the same session.

### 13.7 Three arms on the full sample: the noise floor and the prompt (2026-10-03)

Run on the night of 2026-10-03 for MIGHTLING_PROMPT §6.5, which has the full table: `ab-default-a`, `ab-highswe`, `ab-default-b`, all with the code index, on the 24 validated instances of §12.5 and one build (`runtime_hash` `39b8a92b6775`, `ling-code` `cd4d6b91…`), `task_context` 44,000, three at a time.

- **Resolved: 16, 16 (`high-swe`), 15.** Two identical `default` arms differ on **3 of 24** instances in the same session (2 one way, 1 the other). With §13.5's 4 of 24 on another night, that is this benchmark's floor at 24 instances: a difference of up to about four instances between two arms is noise.
- **The index is used everywhere now:** 328, 415 and 334 `ling-code` calls, in 23, 24 and 24 of 24 instances. The one instance without a call (`sympy-13031` in `ab-default-a`) resolved anyway.
- **Not an index result.** All three arms had the index; this night has no arm without it. The index-on against index-off comparison still rests on §13.6's 14-instance pair.
- **Timeouts still cost:** 2, 1 and 0 per arm, 45 minutes each. Without them the three arms' agent times are 3 h 39 min, 3 h 41 min and 4 h 10 min.
- **A runtime defect found and fixed on the way.** The build of 2026-10-03 links `liblzma.so.5`, which `SweBenchRuntime` refused ("ling needs liblzma.so.5, which the runtime does not carry"), so no instance could start. It now copies optional libraries when `ldd` names them, and libc, libm and libgcc_s stay required (commit `7d5e6e8`, branch `swe/runtime-lzma`; the three arms ran from that branch's code). **Until it is merged, `ling-admin swe-bench run` on `main` fails the same way against any build that links liblzma.**

---

## 14. Refine, then fix (`--refine`, 2026-10-07)

**Why.** In the index-on round of 2026-10-06 (17 of 24 resolved), the agent edited a file the reference patch edits in 6 of its 7 failures; what failed was the change. The fix covered the issue's example but not its stated use case (django 15957, a limit "from each category" applied once overall), stopped at direct parents where grandparents also apply (15563), guarded a symptom instead of fixing the rule (scikit-learn 25747, django 16454), or missed a second code path the hidden test checks (13512, the admin's read-only display). A first step that only studies the issue targets these.

**What it does.** `swe-bench run --refine` (a new run only, recorded as `refine` in the manifest) runs each instance in two `ling exec` sessions in the same container:

1. **Refine** (`REFINE_PROMPT`, at most `REFINE_TIMEOUT_S`: 15 minutes in the 24-task round; since 2026-10-07 `None`, the task's own limit, to match the product's uncapped study): read, run and test, change nothing under `/testbed`, and write `/mightling-scratch/refined.md` in six sections: intent, requirements as observable results, every code path (by callers and references), edge cases, what must not change, acceptance checks. With the code index the prompt names `code_callers` and `code_refs` for the paths.
2. The runner records whether the step changed the tree (it is told not to; this is measured), then puts `/testbed` back to the tree the agent started from (`RESET_SCRIPT`: `git read-tree -u --reset` to the recorded base tree, `git clean -fd`), keeping the description.
3. **Fix** (`FIX_PROMPT`, a new session, the full task timeout): the issue verbatim, then the description, marked as possibly wrong. The issue is authoritative where they disagree; no guard that only hides the symptom; every acceptance check is run before stopping. Nudges apply to this session as before.

The instance's state keeps `refine`: the description (up to 40,000 characters), its size, both steps' times, the first step's outcome and session, whether it changed the tree, and the log offset where the fixing session starts, so the report counts each step's tokens apart. `report` prints a "Refine first" line, and `--against` lists `refine` among the differing fields.

**Measured (2026-10-07, `im-refine`, the 24-instance sample, code index universal, prompt default, masking off): 20 of 24 resolved**, against 17 for `im-index-on`, the same configuration without `--refine`. It resolved every instance `im-index-on` did, plus django 13512, 15957 and 16454. McNemar exact p = 0.250 (95% interval −0.7 to +25.7 points), so one 24-instance pair does not settle it. Against the two index-off rounds: 16 (p = 0.125) and 17 (p = 0.375). Against all eleven earlier rounds of this sample (14 to 17 resolved each), 20 is the highest. 13512 had been resolved in none of them, and 15957 in one.

- **Cost.** Agent time was 6 h 15 min against 3 h 55 min, and the median per instance 16 min 22 s against 4 min 7 s. The first step took 3 h 35 min in all (median 7 min 33 s); the second took 2 h 36 min, a third less than the whole of `im-index-on`. Tokens: 52.0 M in against 46.7 M, and 443 K out against 290 K. **The first step's tokens are undercounted**: 5 of the 24 first steps hit the 15-minute limit, and a stopped session writes no `turn.completed` event, so the log has no usage for them. 4 of those 5 had already written `refined.md`.
- **The first step changed the tree in 0 of 24.** It named every file the reference patch changes in 22 of 24 instances.
- **What the description did for the seven earlier failures:**
  - 15957: captured "a limit per parent" ("3 posts for EACH category, total 9"); resolved.
  - 16454: required that a user's own parser class be kept; resolved.
  - 13512: did not name the admin's read-only display. It named the model field's `get_prep_value` as a second path, for the stored JSON, and `display_for_field` calls that path, so it was resolved by that route.
  - 15563: captured multi-level parent chains; the fix changed the right files and still failed.
  - 16502: misled the fix. The description required Content-Length to stay, but called `WSGIHandler` "the ONLY WSGI path runserver uses" and placed the fix there; the hidden test drives `basehttp`'s handler with a plain WSGI app.
  - scikit-learn 25747: wrote down the symptom guard as a requirement ("matching length still overrides").
  - sympy 13798: raised the number-separator question and answered it the other way from the reference.

  So a wrong description is followed, and the "the issue is authoritative" sentence did not prevent it.

---

## Sources

Fetched on 2026-10-01.

- [SWE-bench README](https://github.com/SWE-bench/SWE-bench/blob/main/README.md): the v5 CLI, `--task-repo`, the resource guidance, "Support for `arm64` machines is experimental", result caching by `run_id`.
- [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/): the predictions format and the layout of `logs/evaluation/<run_id>/`.
- [swebench on PyPI](https://pypi.org/project/swebench/): version 5.0.2.
- [SWE-bench pull request 521](https://github.com/SWE-bench/SWE-bench/pull/521) and [issue 520](https://github.com/SWE-bench/SWE-bench/issues/520): arm64 support, closed unmerged on 2026-08-12, and the x86 assumptions it listed.
- [`greynewell/swe-bench-arm64` on Docker Hub](https://hub.docker.com/r/greynewell/swe-bench-arm64), with its author's [write-up](https://greynewell.com/blog/swe-bench-arm64-native-containers-6x-faster/) and [data](https://gist.github.com/greynewell/497005bb33641503f1a5874f16578088): 1,798 of 2,294 instances native on arm64, 11 instances compared against x86, about six times faster than emulation. These are the author's figures, not checked here.
- [princeton-nlp/SWE-bench_Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified): 500 rows and the field list.
- [Why SWE-bench Verified no longer measures frontier coding capabilities](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/) (the upstream vendor): the contamination findings of §8.
- [SWE-Bench Pro Verified (arXiv 2609.08149)](https://arxiv.org/pdf/2609.08149): the July 2026 withdrawal of the SWE-bench Pro recommendation and the estimate of about 30% broken tasks, as reported by a web search summary; the paper itself was not read.

### 13.8 The exact arm: `--code-index exact` (2026-10-07)

The third arm indexes each instance with the SCIP stores alone and runs the agent with `MIGHTLING_CODE_LAYERS=exact` (code-index spec §16). Nothing in it comes from the graph.

- **The index.** It is built on the host from the same copy of `/testbed` as the universal arm, with the real static indexers (scip-python; scip-typescript where a repository has TypeScript). It runs under the machine's own admission rules, with a 16 GiB memory cap per run, which gives Node a 12 GiB heap.
- **A busy model.** A run the model kept busy past `ling-code`'s wait is deferred, and the arm tries again, three attempts in all.
- **Caching.** The stores are cached per repository and commit under `index-exact/`, apart from the universal cache.
- **Failures.** An instance whose indexers did not all finish still runs. Its state records `stores`, `failed` and `peak_mb`, and the report counts such instances and those with no store at all.
- **A `ling-code` under test** (`DREAMFERENCE_SWE_BENCH_MIGHTLING_CODE`) is relocated into a runtime directory of its own, so preparing it never replaces the one a running arm has mounted.

## 15. Fixes from the failure analysis (2026-10-08)

[MIGHTLING_SWE_BENCH_FAILURES §8](./DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md) records them. The eval script's test-file reset is made per file in the dataset file each harness call gets, so a test patch that adds a file no longer leaves the agent's test edits in place (the grader records `eval_reset: per-file`, so a run graded before is graded again). "Test patch failed" counts every refusal of the test patch. A turn that changed the tree and stopped mid-work gets a completion nudge. `run --task-rules tests` is the test-discipline arm. `eval --drop-test-hunks` regrades a run's predictions with their test files left out, as a separate grading series, and `eval --remove-images` cycles images by repository. With `run --eval --remove-images` the code-index pass no longer keeps every image it pulled.

**Night 1 runs `tests-v2`, not `tests` (2026-10-09).** `scripts/swe_bench_night1.sh` compares `default` with `--task-rules tests-v2` (runs `n1-default` and `n1-tests-v2`). The failure analysis's read of every run ([MIGHTLING_SWE_BENCH_FAILURES §9](./DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md)) found that `tests`' "if a test that passed before your change fails after it, your change is wrong" is false for 19 of the 68 resolved tasks: their correct fix fails an old test because the issue asks for new behaviour, so the rule could talk the agent out of a right answer. `tests-v2` keeps the `/tmp` line and the comparison by name, forbids editing a test only to make it pass, and has the agent decide from the issue whether the old test or the change is wrong. This moves §9.4's night-4 arm to night 1; `tests` stays available, unchanged.

**`issue-v1`, the issue and the code around the fix (2026-10-09).** FAILURES §9.3's rank 1 (contract and sweep) is the task rule `issue-v1`, three lines in the task prompt: before editing, read the whole issue and work out exactly what behaviour it asks for (the result it expects, any function, type or code path it names, every edge case it mentions), and change no more than that; find the code nearby that does the same thing (sibling functions and classes, other backends and entry points) and follow its pattern, fixing a sibling that has the same defect; and if the issue shows an example with its expected output, run it after the last edit and check the output matches. The wording comes from the first wrong moments of §9.1, not from any one task. Rules stack: `--task-rules tests-v2,issue-v1` adds both blocks, each once, and the prompt always has them in `TASK_RULES`' order, whatever order the option names them in. `issue-v1` comes first there, because it is the order of the work (understand the issue, edit, then the test discipline before stopping); the manifest records the set sorted (`["issue-v1", "tests-v2"]`). An arm with several rules is named by joining them with `-`, the record arm's first (`n3-tests-v2-issue-v1`, reports `report-tests-v2-issue-v1.txt`). A new run's `--name` must now be letters, digits, `.`, `_` and `-`, because grading passes `<name>-<n>` to the harness as its run id and the harness names Docker containers after it, so a `+` or a `,` would have failed only at grading; a run already on disk under another name is still resumed. Night 3's arms are decided after night 1 (FAILURES §9.4).

## 16. The other arms, and fresh tasks (recorded 2026-10-09)

Two arms of `swe-bench run` had no section here; both are in the code and in [CLI §4.23](./DREAMFERENCE_CLI.md).

- **`--mask on`** sends the agent's requests with old tool outputs masked ([MIGHTLING_CONTEXT_BUDGET](./DREAMFERENCE_MIGHTLING_CONTEXT_BUDGET.md) §4.1); the runner passes `DREAMFERENCE_MIGHTLING_MASK` and the manifest records `masking` (a run made before the option existed counts as `off`). One round on the 24-instance sample, `im-index-mask`, resolved 16 against 17 for the same configuration unmasked (CONTEXT_BUDGET §8.1): no measurable difference.
- **`--strip-names`** gives the agent the issue with the fix's names taken out (`SweBenchNameStripper`): every file path, module, function and class the gold patch touches becomes a numbered neutral phrase (`[file 1]`, `[function 2]`), the same phrase for the same name throughout, with English words replaced only where they read as code. It measures the code index where the issue does not say where to look (13 of the 24 instances name the fix's file, 9 a function or class it touches). The run records the text its agent saw. Measured once on the 24 (2026-10-07): 14 with the code index (`im-strip-index-on`), 17 without (`im-strip-index-off`) ([SWE_BENCH_COMPARISON](./DREAMFERENCE_MIGHTLING_SWE_BENCH_COMPARISON.md) §1), so the index did not help even here.

**Fresh tasks.** The fixes proposed from the 100-task sample are measured on tasks outside it (FAILURES §6.5). `scripts/swe_bench_fresh.py` lists the candidates (Verified tasks with an arm64 image, not in `sample-100.txt`, not yet validated here, in `sha256(instance_id)` order), validates them as `setup --validate` does in batches that fit the disk reserve, removing each batch's images, and draws a list stratified to the 100's difficulty mix with a recorded seed. `scripts/swe_bench_night1.sh` runs the first night's A/B from that list as a systemd unit. Neither had run when this was written.

## 17. Night 2: production against Minima (prepared 2026-10-09, not yet run)

The first A/B of two models rather than two harness arms. The candidate is Minima, `qwen3.8-27b-minima-nvfp4-dflash2`: Qwen3.8-27B with every linear layer in NVFP4, served by production's recipe with only the checkpoint swapped. The two arms run on 50 fresh tasks drawn the way night 1's were, with seed 20261010. `scripts/swe_bench_night2.sh` swaps the model server between the arms by the normal `server stop` / `server start --model` path, times both checkpoints with `scripts/decode_speed.py`, and serves production again whatever happens. The findings (servable by the pinned SGLang as-is, the drafter applies, the fused-group scales checked), the fairness argument and the decision rule are in [MODELS §2.2](./DREAMFERENCE_MODELS.md).

## 18. The model gate: the benchmark first (2026-10-09)

**The user's decision.** During a benchmark run (the A/B nights, about 16 hours each) the benchmark has priority over the model server. Until now the run gave way instead (§5.5): whenever the server was serving a request that was not the run's, no instance started, and on one night the run waited fifteen times. Now one gate in front of the model server refuses, while a run lasts, every request that is not the run's: `ling` in a terminal, `ling web`, the desktop app, the messenger bridges, `ling-docs`, the Gmail and image-search sidecars, Onyx, anything. `ling-admin night pause` lets them through for a while.

### 18.1 Where the gate is, and why there

How the model server is reached today decides it. The engine (SGLang, from the registry) runs in `dreamference-vllm-<port>` with `--network host` and listens on every interface, and every client comes to that one port: `ling` and the host's tools on loopback, Onyx through Docker's bridge gateway, the benchmark's containers through the gateway of `mightling-swe-bench`, other machines over the LAN, and a replica lane through the run's relay, which connects to the other node's public port.

So `server start` now moves the engine to **loopback at the public port plus 10,000** (`127.0.0.1:18000` for 8000) and starts the gate on the public port, on every interface, as the engine served it. The container is still named after the public port, so the PSI watchdog, `server stop|logs` and every client address nothing new. Nothing in a container or on the LAN can reach a loopback port, so the gate is the one way in; a process on this machine that names the internal port deliberately is an override, not a leak.

The gate is a **container of its own**, `dreamference-gate-<port>`, started by `server start` (`ModelGate`, `dreamference/vllm_server/model_gate.py`) and stopped and removed with the engine:

- **Not the SWE-bench relay.** The relay exists only while a run lasts and only on the run's gateway; the gate must stand in front of every client all the time, so that a run can close it.
- **Not a host process or a user unit.** The engine comes back at boot through Docker's restart policy; a user unit would need lingering to do the same, and a process started by `server start` would not come back at all. The gate has the engine's policy (`unless-stopped`), so the two come back together.
- **Not inside the engine's container.** In a container of its own, a crashed gate is restarted by Docker in about a second without touching the engine; its log is not the engine's, which the model-loading monitor reads for its readiness words; and the engine's launch command changes only in `--host` and `--port`.
- **In the engine's image**, which carries a Python, so nothing is pulled for it (as the diffusion sidecar's service runs in the main model's image), with `--user` the user's, 256 MiB, two CPUs, and its state mounted read-only.
- **Python, standard library only** (`model_gate_service.py`, asyncio). It needs no change to `codex-patches/`, whose series stands at 42,741 of its 43,000 bytes. The file is copied beside the gate's state (`~/.local/state/dreamference/model-gate/`) at every start, so the container never mounts a path inside an installation an upgrade removes.

If the gate cannot start, or started but does not answer its probe within 10 seconds (the port held, say), it is removed and the engine serves the public port itself, as before, and `server start` says so; `--no-gate` does the same on purpose and removes a gate an earlier start left.

### 18.2 What passes

- **No run holds the gate, or a pause is in force:** everything, as it was.
- **A run holds it:** the run's own requests (§18.3); read-only probes that do not run the model (`GET` of `/v1/models`, `/metrics`, `/health`, `/get_model_info`, `/get_server_info`, `/server_info`, `/model_info`, `/version`, `/ping`), so `ling` still starts and says what the model is, and the run's admission and `ling-code` can read `/metrics`; and `GET /mightling-gate`, which the gate answers itself with what it is doing. Everything else is refused (§18.4), and the engine never sees it.
- **One request per connection.** The gate decides on the request's head, sends it to the engine with `Connection: close` in place of the client's connection headers, forwards that one request's body (by `Content-Length` or chunked), and never forwards anything the client sends after it. The engine's answer goes back with `Connection: close` too, whatever the engine said, so a client never pools a connection the gate let through. A kept-alive connection opened while the gate was open therefore cannot carry a later request past it once it closes; the client opens a new connection, which is decided anew.
- **Streaming is untouched.** The answer is copied as it arrives, never buffered. Measured on scratch ports against a stand-in that sent one SSE event every 400 ms: each arrived through the gate 3-4 ms after it was sent. 200 small `GET`s took 6.65 ms each directly and 7.02 ms through the gate (curl's own start included). When the client goes away, the gate closes the engine's connection, so the engine stops generating.

### 18.3 How the run's requests are known

**By their source address: the run's internal network's subnet.** The run reads it from `docker network inspect mightling-swe-bench` and writes it into its record. The gate is on the host's network, so it sees the instance container's own address: checked from a container on a scratch internal network, the gate passed it with that subnet recorded and refused it (logging `172.21.0.2`) with another. No other client can send from that subnet: a process on the host or a container elsewhere has another address, the LAN cannot route to it, and only the Docker socket (which can do anything anyway) could put a container on that network. A port, a path or a header would be guessable or visible to any client; no token is used.

A replica lane is reached through the relay, which connects to the replica's public port, so its requests meet the replica's gate, which is open unless the replica runs a benchmark of its own. The run then gives that lane no instance (it asks the replica's `/mightling-gate`). The gate protects the machine whose run it is.

### 18.4 The refusal, and what a refused user sees

HTTP **503** in the API's own error shape, `Content-Type: application/json`, `Connection: close`, **`Retry-After: 0`**:

```json
{"error": {"message": "The model is running a benchmark (night 1, default arm, 37/100 done, about 9 h left). Try later or run `ling-admin night pause`.", "type": "service_unavailable", "code": "benchmark_running", "param": null}}
```

The label is `swe-bench run --label` (the night script passes `night 1, <arm> arm`; default `SWE-bench run <name>`); the progress is the run's finished instances over its instances, and the time left is the instances left times the median instance's time over the parallelism, refreshed every 15 seconds.

**Why `Retry-After: 0`.** The agent retries a 5xx at two layers, five HTTP attempts with a backoff from 0.2 to 1.6 s and then five turn retries, about 30 requests and 25 seconds before the error shows, and it honours `Retry-After` with no upper bound. Zero makes those retries immediate, so the message shows in about a second; any real delay would hang the turn (an hour would hang it for more than a day). The code is not `server_is_overloaded` or `slow_down`, which the agent replaces with a generic message of its own.

**What each client shows.** Checked with the installed build against a scratch gate:

- **`ling exec`** prints `Reconnecting... 1/5` to `5/5`, then `ERROR: unexpected status 503 Service Unavailable: The model is running a benchmark (night 1, default arm, 37/100 done, about 9 h left). Try later or run `ling-admin night pause`., url: http://…/v1/responses`, 1.1 s after it started.
- **The TUI**, by reading the code: the same text in an error cell, after a `Reconnecting… n/5` status line.
- **The app-server** sends an `error` notification carrying that message; the **desktop app** and **`ling web`** (the same UI build) show the turn's `error.message`, and `ling web ask` prints the error's parameters, the message included.

No client swallows it, so no display was changed. `ling-admin run` with another agent (Cline, Continue, OpenHands) waits for the server with a one-token completion, which the gate refuses: it now prints the gate's message and stops instead of waiting out the run, and `ling-admin status` shows the server as not answering completions. The wrapper `unexpected status 503 Service Unavailable: …, url: …` is the agent's own and stays: removing it would need a patch. Onyx, the bridges and the sidecars show whatever their client libraries make of a 503 with that body; they were not checked one by one.

### 18.5 The run's side

`SweBenchGateHold` (`dreamference/swe_bench/swe_bench_gate_hold.py`) closes the gate once the run holds the runner lock and has its network, before admission, and opens it when the run ends:

- It writes `run.json` beside the gate's state: the run's name, a random id, the label, the subnet, progress and time left, its pid, and a heartbeat, refreshed every 15 seconds from a thread of its own. It removes the file at the end, only if the file is still its own.
- **While the gate is in force**, nobody else can reach the model, so admission skips the open-session check and the idle wait (it waits only for requests already in flight to finish), and an open session or another request no longer holds a start back. Memory and disk still do.
- **While paused, or with no gate answering** (a server started before the gate, or with `--no-gate`), everything is as before the gate: an open session or another request holds new starts back, and running instances finish. A run says when it finds no gate.
- **Why the run waits during a pause** rather than going on starting instances: an instance that shares the model with someone's work is not timed like the others, and a pause is meant to give the model back for a while.
- **The record:** `runs/<run>/gate.json` keeps, per run session, whether the gate was in force, and every pause interval clipped to the run. The report prints a `Model gate` line, and for pauses `Gate paused  N time(s), X in all (spans); K instance(s) ran during a pause … not comparable: <ids>`. `report --against` notes a differing gate and a paused run.

### 18.6 Pause and resume

`ling-admin night pause [--for DURATION]` (default one hour; `90m`, `2h`, `45s` or minutes) writes `pause.json` (`since`, `until`). A second `pause` while one is in force extends it from now and keeps its start, so a run records one interval. `ling-admin night resume` sets its end to now. A pause given with no run holding the gate also covers a run that starts before it ends. The gate reads the file on every request; the run, at every heartbeat. `night status` and `swe-bench status` say what the gate is doing.

### 18.7 Failure modes

- **The gate crashes:** Docker restarts it in about a second; the engine is untouched, and since its state is in files nothing is lost. Clients see a refused connection meanwhile.
- **The run dies without cleaning up** (SIGKILL, out of memory, a reboot): its heartbeat stops and the gate ignores a record older than 180 seconds, so it opens on its own. A missing or unreadable record means open. A run that ends normally removes its record at once.
- **A run hangs but its process lives:** the heartbeat thread keeps the gate closed. `ling-admin night pause --for 24h`, or stopping the run, opens it. Not detected automatically.
- **The engine is down or loading:** the gate answers 502 ("not answering behind its gate; it may still be loading"); readiness polls see a non-200, as they saw a refused connection before.
- **Egress:** the gate connects only to the engine on loopback. `ling` still connects to the model server's public port, so the egress audit's allowlist is unchanged.

### 18.8 At the switch to the new install

- The gate exists only once `ling-admin server start` of the new install has started the model server. The server running before was started without one and keeps the public port itself; a run against it prints that no gate answers and waits as before. Night 2 (§17) swaps models with `server stop` and `server start` from its checkout, so its first swap brings the gate; night 1 swaps nothing and needs the server restarted before it starts. Both scripts label their runs (`--label "night 1, <arm> arm"`, `"night 2, <run>"`).
- `server start` refuses while a run holds the runner lock under the new `CODEX_HOME`. A run of the old install holds its lock under the old home, which the new code does not see, so the model server must be restarted only once such a run has finished.

### 18.9 Tests

`tests/test_model_gate.py` (32): the rules (open without a live run, with a stale, unreadable or network-less record; closed by a live one; opened by a pause); the time-left wording; who passes a closed gate; the request head rewritten to one request per connection; the proxy live on loopback in front of a stand-in, streaming through without buffering (the stand-in sends its second event only once the client has read the first), refusing with the message, `Retry-After: 0` and the engine never seeing the request, passing the run's network and a pause, a chunked body reaching the engine whole, a kept-alive connection unable to carry a second request past a gate that closed, the answer's head rewritten to `Connection: close` after an interim `100 Continue` from an engine that wanted to keep the connection, 502 with the engine down, and the probe; the service file run on its own with `python3 -I` as the container runs it, its log free of the loading monitor's words; the container command; the copy at start and the probe that must answer, a gate that never answers removed; the readiness waiter of the other agents printing the message; pause, extension and resume; a hold removing only its own record; `server start` starting the gate after the old engine is removed and before the new one, falling back without it, and `--no-gate`; `night pause|resume` from the command line. `tests/test_swe_bench.py` (+7): a run writing its subnet and label into the record, admission waiting only for requests in flight, starts no longer held back, the gate open again after the run, the report line; the time left; a pause making the run wait and its report naming it; no subnet, no gate; the instances that ran during a pause; a replica with a closed gate getting no instance. `tests/test_night_shift.py` (+1): with priority only memory holds a start back. `tests/conftest.py` answers the gate probe with "no gate" for every test, since the real probe is an HTTP request to the configured server.

### 18.10 Not built

A per-run token as a second identification; removing the agent's `unexpected status …, url: …` wrapper (a patch); checking Onyx's and each bridge's display one by one; detecting a run that hangs while alive.

## 19. The review turn (`--review-turn`, 2026-10-09)

**Why.** [MIGHTLING_SWE_BENCH_FAILURES §9.3](./DREAMFERENCE_MIGHTLING_SWE_BENCH_FAILURES.md), rank 3: in 9 of the 32 failures of the 100-task round the run's own output contradicted the fix and the agent carried on. A second look at the finished diff targets those. The user chose *review and test*: re-read the issue, read the diff, run the tests of the changed modules, fix what does not hold. It is an arm for nights 4 and 5 (§9.4), off by default.

**What it does.** `swe-bench run --review-turn` (a new run only; the manifest records `review_turn`, and a resumed run keeps it). Once the agent's turns have ended (after its nudges) and before the patch is collected, the runner resumes the same session in the same container, `ling exec … resume <session> <prompt>`, as a nudge is resumed, with `REVIEW_PROMPT` (`swe_bench_instance_run.py`):

```text
Before you finish, review your work:
- Re-read the issue.
- Read your own diff: `git status`, then `git diff` (a file you added shows only in `git status`).
- Run the test files of every module you changed.
- If the diff does not do what the issue asks, or a test that passed before your change now
  fails, fix it.
Then stop with a short summary.
```

- **One turn, outside the `nudges` budget**, and no nudge after it. It is not counted among the nudges.
- **Only on a changed tree after a turn that ended normally.** With no change there is no diff to review: a turn there would be a second attempt, not a review, and its last message would decide between `empty` and `stalled` in place of the agent's. After an error or a timeout, or with no time left in the task, the turn is skipped too. The state says why (`review.skipped`).
- **The same container, so the model gate (§18) passes it**: the gate decides by the source subnet, and the turn comes from the instance's own container.
- **No session to resume** (`ling exec` reported no thread): a fresh session gets `REVIEW_FRESH_PROMPT`, the unattended preamble, the same rules, the issue and the diff as the agent's turns left it (cut at 40,000 characters). Recorded as `resumed: false`, and counted in the report.
- **The task's time limit covers it.** The deadline is not reset. If the limit is reached during the review, the container is stopped as for any timeout, started again, and the tree is collected as it stands; the instance's status is the one its patch earns (`done`), not `timeout`, because the agent itself finished. The state's `review.exec` is `timeout` and a note says the patch was collected as it stood.
- **The patch collected is the tree after the review turn.** The patch before it is kept as `scratch/<id>/patch-before-review.diff`, so the two can be graded apart (not built as a command). Collecting it stages everything, so `COLLECT_SCRIPT` now ends by putting the index back to `HEAD`, as the other scripts do: otherwise the agent's `git diff` would show nothing.

**The record.** Per instance, `state.review`: `resumed`, `exec` (`ok`, `error`, `timeout`), `seconds`, `tokens` (the turn's own, from the log between its offsets, counted as everything else is), `log_offset`, `patch_bytes_before`, and what the turn changed: `changed`, `added` and `removed` lines (`git diff --numstat` from the tree before the turn to the tree after it), `files` (up to 50). The report prints a `Review turn` line in every run, `off` when the arm was not on; with it on, in how many instances it ran and why not in the rest, in how many it changed the patch and by how many lines, how many reached the time limit, its time and tokens. `report --against` lists `review_turn` among the differing fields (a manifest written before the option counts as `false`), and the side-by-side table has a `review turn` row.

**Risk, as §9.2 says.** A second look can undo a correct fix, and "a test that passed before your change now fails" is the sentence §9.2 found false for 19 of the 68 resolved tasks, whose correct fix fails an old test. The measures are the patches the review changed and the instances resolved without it and unresolved with it; the second needs `patch-before-review.diff` graded.

**Tests** (`tests/test_swe_bench.py`, +9; the scripted agent checks, when reviewing, that the index is at `HEAD` and `git diff` shows its change): the prompts (terse, no benchmark words; the fresh one carries the issue, then the diff, cut when long); off by default and the report saying so; on, resuming the fix's session in the same container before collecting, with its tokens and the pre-review patch; a review that changes the diff, collected, with its lines counted; a review that reaches the time limit, its tree submitted, the status `done`, the note; no review with no time left, after no change or after an error, and the report's reasons; the fresh-session fallback; the arm kept by a resumed run, told apart by `--against`, `false` for an older manifest, and the option reaching the runner.

**Not checked without a real container:** that `ling exec resume` in a container finds the session it wrote there (the nudges make the same call); how long the turn takes and what it costs in tokens; whether the agent follows the prompt; and whether a resumed process's `turn.completed` usage counts only the new turn (the report sums it as it sums the nudges').
