# Puffin on SWE-bench — `puffin-admin swe-bench`

**Status:** Phase 1 implemented on 2026-10-01 in `dreamference/swe_bench/`, with `puffin-admin swe-bench {setup,smoke,run,eval,report,status,clean}`. §1–§11 are the design as specified; **§12 records what was built, what Phase 0 measured, and where the build departs from the design**, and wins where the two disagree. Not built: local image builds (impossible on arm64 as upstream ships them), per-repository image cycling for a full run, the mini-SWE-agent baseline column.
**Target:** the `puffin` terminal agent and the model it is served by, measured on the GB10 itself.
**Command:** `puffin-admin swe-bench {setup,smoke,run,eval,report,status,clean}`. It lives in `puffin-admin`, not in the `puffin` binary (§2 says why).
**Builds on:**
- `puffin exec --json` ([PUFFIN_CODEX](./DREAMFERENCE_PUFFIN_CODEX.md));
- Night Shift's host probes, admission, memory-capped scopes and runner lock ([PUFFIN_NIGHT_SHIFT §5, §11](./DREAMFERENCE_PUFFIN_NIGHT_SHIFT.md));
- the per-task compaction limit ([PUFFIN_COMPACTION](./DREAMFERENCE_PUFFIN_COMPACTION.md));
- `VLLMServerManager.check_host_safety()` ([INFERENCE](./DREAMFERENCE_INFERENCE.md));
- the upstream harness, `swebench` 5.0.2 (PyPI, 2026-10-01), and its task repository `SWE-bench/swe-bench-tasks`.

---

## 1. Goal

One command runs `puffin` over SWE-bench instances on this machine and says how many it resolved:

- the agent gets an issue and a repository, and produces a patch;
- the upstream harness applies the patch and runs the instance's tests;
- a report gives the resolved rate, with everything needed to reproduce it.

**What the number is for.** Comparisons *on this machine*: one model against another, a prompt change, cave mode on and off ([PUFFIN_CAVE_MODE](./DREAMFERENCE_PUFFIN_CAVE_MODE.md)), a retrained drafter ([SELF_SPEEDING](./DREAMFERENCE_SELF_SPEEDING.md)), a Codex bump. `benchmark_server` measures tokens per second; nothing today measures whether the agent's work is right.

**What the number is not.** A leaderboard score. §8 lists why, and the report prints those reasons beside every figure.

**Non-goals:**
- **Submitting to a leaderboard.**
- **Other benchmarks** (SWE-bench Pro, SWE-bench-Live, Multimodal). The layout of §4 leaves room for a second dataset; none is specified.
- **Running during interactive use.** A run gives way to it, as Night Shift does.
- **Starting, stopping or loading the model server.** A run measures whatever is being served.

---

## 2. Where the command lives

**`puffin-admin swe-bench`, in Python.** Three reasons:

1. **Long-running Docker orchestration already lives there:** `benchmark_server`, `night run`, `codex test`. The host probes, scopes and locks a benchmark run needs are Python classes (`NightShiftHost`, `NightShiftQueue.runner_lock`).
2. **`puffin` is Codex.** A subcommand of the binary is either a Codex patch, and the series stands at 26,933 of its 27,500 bytes, or launcher code that would have to reimplement those probes in Rust.
3. **The harness is a Python package** with heavy dependencies (`datasets`, `docker`), which belongs in a virtualenv of its own beside `puffin-admin`, not in a Rust binary.

**Open (§10.1):** the user asked for a "puffin command line command". The launcher already handles `puffin night …`, `puffin app` and `puffin update` before Codex parses its arguments, so `puffin swe-bench …` could exec `puffin-admin swe-bench …` from `puffin-rs/` at no patch cost. It is not specified here because it adds nothing but a second spelling.

---

## 3. The commands

| Command | What it does | Needs the model |
|---|---|---|
| `swe-bench setup` | Creates the harness virtualenv, clones the task repository, downloads the dataset, checks Docker, disk and architecture. Builds or pulls no instance image. | no |
| `swe-bench smoke` | Proves the whole pipeline on a handful of instances (§7.1). `run` refuses until a smoke has passed on this machine with the current harness version. | yes |
| `swe-bench run [--dataset verified] [--instances …] [--limit N] [--name <run>]` | The agent phase: one `puffin exec` per instance, producing `predictions.jsonl`. Resumes a run of the same name. | yes |
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
  logs/<id>.jsonl           puffin exec's events
  predictions.jsonl         appended, one line per finished instance
  eval/<n>/                 the harness's logs/evaluation/<run_id>/ for grading n
  report.md                 written by `report`
```

`predictions.jsonl` is the harness's own format: `instance_id`, `model_name_or_path`, `model_patch`. An instance whose agent run failed or produced nothing is written with an empty patch, so it counts as unresolved and is never silently missing from the denominator.

**`model_name_or_path`** is `puffin-<codex tag>-<patch series hash, 8 hex>/<served model id>`, so a result stays attributable to the build and the model that produced it.

---

## 5. The agent phase (`run`)

### 5.1. One instance

The agent runs **inside the instance's own image**, not in a checkout on the host:

- SWE-bench repositories need their pinned environment to run their tests, and an agent that cannot run tests is not the agent being measured. Night Shift's answer, the main checkout's `.venv`, has no analogue here.
- The image has the repository at `/testbed`, at the instance's `base_commit`, with its dependencies installed.

Steps:

1. **Start a container** from the instance image (§5.3):
   - network: `puffin-swe-bench`, an internal Docker network (§5.2);
   - mounts: the installed `puffin` binaries read-only at `/opt/puffin`, a fresh `CODEX_HOME` per instance, the instance's log directory;
   - limits: `--memory` and `--memory-swap` equal to `[swe_bench] task_memory`, `--cpus 4`, `--pids-limit`;
   - environment: `DREAMFERENCE_VLLM_HOST` pointing at the network's gateway, and `PUFFIN_NIGHT_RUN=1` so the run's own sessions are never taken for someone working.
2. **Remove what gives the answer away.** In `/testbed`: confirm `HEAD` is `base_commit`, delete every other ref and the reflog, and run `git gc --prune=now`, so the fixing commit cannot be found in the object store.
3. **Run the agent:** `puffin exec --json --dangerously-bypass-approvals-and-sandbox -C /testbed -c model_auto_compact_token_limit=<task_context> "<prompt>"`, with `stdin` from `/dev/null`.
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

**The agent has no network except the model server.** With one, it can find the real fix: every instance is a public GitHub issue with a merged pull request, and `puffin`'s prompt tells the model to run `puffin-search` and `puffin-fetch`.

- **Mechanism:** `docker network create --internal puffin-swe-bench`. The model server listens on every interface by design, so the container reaches it at the network's gateway address and reaches nothing else. Measured (§9): from such a network `http://<gateway>:8000/v1/models` answers 200, and both `https://1.1.1.1` and `https://github.com` fail.
- **Cost:** the prompt still names the web commands, and they fail. The model may spend turns finding that out until `/airgapped on` exists ([PUFFIN_AIRGAPPED](./DREAMFERENCE_PUFFIN_AIRGAPPED.md)), which a run then sets.
- **No package installs:** an instance whose tests need a dependency the image lacks cannot fetch it. That is the benchmark's rule too.
- The web binaries and `puffin-admin` are **not** mounted into the container.

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
- **Interactive use wins:** an open `puffin` session or an outside request stops new instances from starting; running ones finish.
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
puffin-0.158.0-3fa9c21e / RadixArk/Qwen3.8-27B-NVFP4       run 2026-10-03-a
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
- **Optional baseline column:** `swebench infer` (mini-SWE-agent against the same endpoint) measures the model without `puffin`. It is not run by default.

### 6.4. The manifest

Written when a run starts, never edited: harness version, task-repository commit, dataset revision, image source and the digests of the images used, `puffin` version, Codex tag and patch-series hash, served model id and registry alias, the launch recipe's context length and speculative settings, cave-mode level, `task_context`, timeouts, nudges, parallelism, the instance list, the start time, and the git commit of this repository. `report --against` refuses to compare two runs whose datasets or validated sets differ, and lists every other field that differs.

---

## 7. Tests

### 7.1. The smoke set

`swe-bench smoke` is the gate for everything else:

1. **Gold:** the harness's own installation check, `swebench eval verified --gold -i sympy__sympy-20590 --task-repo …`, extended to five instances from five repositories. Every one must resolve. This proves images build and grade correctly on arm64.
2. **Empty:** the same five with an empty patch must all be unresolved. This proves the grader can fail.
3. **Agent:** one instance, chosen for a small fix, run through §5.1 in full, and graded. It need not resolve; it must produce a prediction, a log and a graded result.

The five ids are fixed in the code, chosen in Phase 0 from instances that validate here. A smoke takes minutes after the first image build, and its pass is recorded with the harness version; a harness upgrade invalidates it.

### 7.2. Offline tests

`tests/test_swe_bench.py`, with a scripted stand-in for `puffin` and for `docker`, as `tests/test_night_shift.py` has for `puffin`: nothing in the suite starts a container, pulls an image or reaches the network (`tests/conftest.py` already fails any real `docker` command that changes something). The cache and results directories of §4 are module-level constants, because the fixture that gives each test its own home re-points module attributes; a path computed inside a function would escape it and a test could write the user's real cache.

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
5. No connection from an agent container to anything but the model server (checked with the trace method of [PUFFIN_EGRESS](./DREAMFERENCE_PUFFIN_EGRESS.md) on one instance).

---

## 8. What the number means, and what it does not

Printed in short form on every report, and to be repeated wherever a figure from this command is quoted:

1. **The benchmark is contaminated.** the upstream vendor stopped reporting SWE-bench Verified in early 2026: frontier models could reproduce gold patches verbatim, and many of the hardest unsolved tasks had flawed tests. Its replacement recommendation, SWE-bench Pro, was itself withdrawn in July 2026 after an audit estimated about 30% of its tasks broken. An open-weights model has very likely seen these repositories and their fixes.
2. **The images are not the leaderboard's.** Scores elsewhere are graded in the official x86_64 images. These are arm64 images built locally or by a third party, and a subset: the denominator is what validates here, not 500.
3. **The agent is restricted** in ways others may not be (no network), and the model is a quantised build (NVFP4) with a speculative drafter.
4. **One run is one sample.**

So the number supports "configuration A resolves more of these instances than configuration B on this machine". It does not support "Puffin scores N% on SWE-bench Verified" without all four qualifications, and the report says so in its last line.

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
| `puffin` in a container | The installed binary, mounted read-only into `python:3-slim` on that network with a fresh `CODEX_HOME` (which must exist beforehand) and `DREAMFERENCE_VLLM_HOST` set to the gateway, completed a turn against the served model. With `-s workspace-write` every shell command failed (no bubblewrap in the container) and nothing was written; with `--dangerously-bypass-approvals-and-sandbox` the command ran and the file was created. With `stdin` from `/dev/null`, `exec` still printed "Reading additional input from stdin..." and went on. |
| Upstream arm64 work | Pull request 521 (arm64 support across the harness's build and evaluation) was closed unmerged on 2026-08-12, when v5 moved dataset-specific attributes into the task repository. |

**Not checked when the spec was written** (§12.1 answers the first six; wall time and the resolved rate are in §12.5):

- How many Verified instances build with `--task-repo` on this machine, and how many of those pass their gold patch.
- Whether the community images agree with local builds, and how their tag names map to instance ids (`sympy-sympy-22005` against `sympy__sympy-22005`).
- Disk actually used per repository after layer sharing.
- Whether the harness caps its containers' memory.
- Whether `puffin` runs in every instance image: the images are older distributions, and the binary needs a compatible glibc. `python:3-slim` is not evidence for an Ubuntu 22.04 conda image.
- Which user the agent must run as inside an instance image: the probe ran as the host's uid against an empty mounted directory, which says nothing about a root-owned `/testbed` and conda environment, or about git's "dubious ownership" check.
- Wall time per instance and for 500, the resolved rate, and its run-to-run spread.
- Whether removing refs and running `git gc` in `/testbed` is enough to hide the fix in every image, or whether some carry it elsewhere (a pip cache, a second clone).
- How many turns the model wastes on the web commands the prompt names.

---

## 10. Phases

- **Phase 0, measurements (no product code):** ten instances from ten repositories, validated (gold and empty) by local build and by community image; disk per repository, unpacked; harness memory behaviour; `puffin` started in each of the ten images, as root and as the host's uid; one full instance by hand. The results decide the default image source and replace the estimates in §5.3 and §6.2.
- **Phase 1:** `setup`, `smoke`, `run`, `eval`, `report`, `status`, `clean`; the shared admission module; the tests of §7.2.
- **Phase 2:** `--against` with its interval, the mini-SWE-agent baseline column, `--until`.

---

## 11. Open questions

1. **`puffin swe-bench` as a second spelling** (§2): add the launcher's pass-through or not?
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
| **The installed `puffin` does not start in an instance image**: it needs glibc 2.38/2.39, the images are Ubuntu 22.04 with 2.35. | `setup` builds a **relocated runtime** (`~/.cache/dreamference/swe-bench/runtime`): copies of `puffin` and `codex-code-mode-host` whose ELF interpreter and rpath point at copies of the host's loader, `libc`, `libm` and `libgcc_s`, mounted read-only at `/opt/puffin`. Only those two binaries use the copied libraries; the repository's Python uses the image's. The runtime is stamped with the hash of the binaries it was copied from, rebuilt when `codex build` replaces them, and the hash is in the manifest; a run refuses to resume with a different one. `puffin-code` is not in the runtime (it needs bubblewrap), so the agent in a container works **without the code index**. |
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
| The relocated `puffin` | `dreamference/swe_bench/swe_bench_runtime.py` |
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
- **`/airgapped on` cannot be used.** §5.2 planned to set it once it existed. As built, `puffin` refuses to start at `on` together with `--dangerously-bypass-approvals-and-sandbox` ("nothing would keep them off the network"), and inside a container the bypass flag is the only way to run. The run sets `DREAMFERENCE_PUFFIN_AIRGAPPED=off` explicitly: the internal Docker network is the enforcement, the web binaries are not mounted, and the task prompt says there is no network. The system prompt still names the web commands. Open for the airgapped module: a way to say "the network is absent by other means".
- **Validation happens when a run starts, for the instances it selected**, and the manifest's instance list and exclusions are then fixed. An instance that could not be validated because the disk reserve was reached is excluded from that run. `setup --validate` does the same ahead of time.
- **Per-repository image cycling is not built.** `run --eval --remove-images` removes a repository's images after grading it, but validation still pulls every selected image first. A run over all 400 instances therefore needs the instances validated repository by repository with `clean --images` in between, and does not fit as one command today. Runs of a few dozen instances do.
- **A timeout still submits what was changed.** §5.1 lists `timeout` among the states without saying what is predicted. The container is stopped, started again, and the partial patch collected; the status stays `timeout`.
- **An interrupted run (Ctrl-C or SIGTERM) writes no prediction for the instances it cut off**; their state is `interrupted` and they run again on resume. `--until` only stops new starts.
- **The grading number rises on a changed grader**, as §6.1 says, not on a changed prediction, as one line of §7.2 said: predictions never change within a run.
- **Cave mode is passed in explicitly.** The container has no `dreamference.toml`, so the level configured on the host is resolved by the runner, set as `DREAMFERENCE_PUFFIN_CAVE_MODE`, and recorded.
- **`--against` (Phase 2) is built**: per-instance differences, a 95% Wald interval for paired proportions and the exact McNemar p-value; "No measurable difference." when the interval contains zero. The mini-SWE-agent baseline column is not built.
- **`swe-bench eval` checks memory, not idleness:** `eval_workers × eval_memory` plus the 8 GiB reserve must be available.
- **Every agent log starts with a line that is not JSON** (`Reading additional input from stdin...`, printed by `puffin exec`); readers skip it.

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
- **Interrupted on purpose and resumed.** After four predictions, SIGTERM to the `puffin-admin` process: it exited in 4 s, left no container, four parseable predictions, and the two instances it cut off in state `interrupted` with no prediction. The same command with the same `--name` then ran the remaining 20, those two among them. (The first SIGTERM went to the wrong process, a shell wrapper, and did nothing; that was the test's mistake, not the command's.)
- **`eval acc-25` through the command** found everything graded and printed 24 graded, 13 resolved; the grading itself had run inside `run --eval`.
- **No turn spent on the web commands.** The 24 logs mention `puffin-search` once and `pip install` once (both inside quoted text), so §5.2's worry did not show at this size.
- **Not run as designed:** the run-to-run spread of §7.3 item 3. The pair of §13.5 turned out to be a repeat in effect, since the tool that distinguished the arms went unused, with one confound (the prompt block); it differed in two instances each way. Also not run: and the connection trace of item 5 (the internal network was checked by hand: the model server answers, `github.com` does not).

---

### 12.6 A replica's model server (2026-10-03)

A paired node serving the same model adds a lane (`[swe_bench] nodes`, default `"paired"`; [PUFFIN_NODE §18.8](./DREAMFERENCE_PUFFIN_NODE.md)). The containers still run here; the run listens on the internal network's gateway at a port of its own and relays each connection to that node's model port, so the network stays closed to everything else. Each instance's state notes which server answered it when it was not this machine's. With no paired node nothing changes. Not run with a second node; the relay was run live from a container on `puffin-swe-bench`.

## 13. The code index as an arm (2026-10-02)

`swe-bench run --code-index universal` gives the agent `puffin-code` ([PUFFIN_CODE_INDEX](./DREAMFERENCE_PUFFIN_CODE_INDEX.md)); the default, `off`, is the agent of §12, which navigates with `grep` and `find` because the runtime carries nothing of the index. The arm is recorded in the manifest (`code_index`), a resumed run keeps the arm it started with, and `report --against` lists it among the fields that differ.

### 13.1 How it works

1. **The repository is indexed on the host, before any agent starts.** For each instance, `/testbed` is copied out of the instance image (`docker create`, `docker cp`, no container ever runs), `puffin-code index --wait` is run in the copy with `PUFFIN_CODE_STATE_DIR` and `CBM_CACHE_DIR` pointing into `~/.cache/dreamference/swe-bench/index/<repo>@<base commit>/`, and the copy is deleted. The index run is admitted against the host's memory budget and runs in `puffin-index.slice`, like any other. All indexes are built before the first agent container starts: Night Shift's admission refuses to start beside an index run, and index time is not the agent's time. It is recorded per instance (`index.seconds`, `index.cached`) and reported separately.
2. **The index is mounted read-only**, with a relocated `puffin-code` (`runtime-code/`, the same loader-and-libc treatment as `puffin`, kept in a directory of its own so the `puffin` runtime's hash, which a manifest pins, does not move). Four variables tell `puffin-code` where things are: `PUFFIN_CODE_BIN` (the launcher then appends `puffin-code prompt-block` to the model's prompt by itself), `PUFFIN_CODE_STATE_DIR`, `PUFFIN_CODE_GRAPH_DB` and `PUFFIN_CODE_PROJECT` (the graph names its project after the host path it was built at). `puffin-code` is put first on the container's `PATH`.
3. **Queries only read**, which is the code-index spec's own rule. A file the agent edits is answered by text search and tagged `heuristic (text)`, as on the host. The launcher also starts `puffin-code session`, which cannot write its lock on the read-only mount and exits; nothing depends on it.
4. **A run whose index cannot be built does not start:** a run measures one arm.

### 13.2 Only the universal layer

The exact (SCIP) layer is left out, by pointing `PUFFIN_CODE_INDEXERS_DIR` at an empty directory so scip-python counts as not installed. Measured on `sympy__sympy-13480` with both layers: 1,022 s, scip-python at 1.6-1.7 GiB per directory, and the run for the `sympy/` package itself ended with Node's "JavaScript heap out of memory" at 2 GB, leaving exact data only for `bin/`, `doc/` and `examples/`. Every instance is a different commit, so nothing is shared between instances. The universal layer alone took 19-29 s per repository with the model idle (79 s for django with two agents running). So every answer the agent gets in this arm is tagged `heuristic`, and the result says nothing about the exact layer.

### 13.3 Checked

- A host-built index is accepted in the container: `status`, `refs`, `callers` and `prompt-block` answered there, in milliseconds, and after an edit the changed file's rows turned to `heuristic (text)`.
- The prompt block arrives: the with-arm's `model_catalog.json` contains `# Code navigation`; the without-arm's 24 logs and catalogs contain neither that heading nor one mention of `puffin-code`.
- `puffin` was not rebuilt between the arms: both manifests carry the same `runtime_hash`.

### 13.4 The report

`report` prints, for one run, the tokens, the command count and a `Code index` line: the arm, what the indexes cost, and in how many instances the agent called `puffin-code` at all (counted from `puffin exec`'s command events). `report <run> --against <other>` sets two runs side by side on the instances both graded: resolved in each, median and total agent time, input and output tokens, commands, `puffin-code` calls, instances that used it, index time, then how many were resolved in both, only one or neither, and one row per instance. If the agent never called `puffin-code`, the report says the comparison says nothing about the index; otherwise it says in how many instances it was used. The paired interval and "No measurable difference." of §6.3 apply unchanged.

### 13.5 With and without, measured

Two runs on the same 24 validated instances (the sample of §12.5), the same `puffin` build (`runtime_hash` `9d107cf700c4`), the same model, one after the other on the night of 2026-10-01: `acc-25` without the index, `acc-25-index` with the universal layer. One run per arm, so one sample of each.

| | With the index | Without |
|---|---|---|
| Resolved | 13 of 24 (54.2%) | 13 of 24 (54.2%) |
| Resolved in both / only this arm / neither | 11 / 2 / 9 | 11 / 2 / 9 |
| Median agent time per instance | 10 min 26 s | 5 min 49 s |
| Agent time in all | 5 h 1 min | 3 h 58 min |
| Input tokens (cached) | 78.4 M (75.8 M) | 50.7 M (49.3 M) |
| Output tokens | 503 K | 306 K |
| Commands | 2,824 | 2,288 |
| **`puffin-code` queries** | **0, in 0 of 24 instances** | 0 |
| Index time, outside the agent's | 7 min 57 s in all, median 19 s | none |
| Timeouts / empty patches | 0 / 1 | 1 / 0 |

Difference in resolved rate: 0.0 points, 95% interval −16.3 to +16.3, McNemar p = 1.0. Only with the index: `django__django-15563`, `sympy__sympy-13877`. Only without: `django__django-16100`, `sympy__sympy-18211`.

**What this does and does not show.**

- **It says nothing about whether the index helps, because the agent never used it.** All 24 prompts carried the `# Code navigation` block and `puffin-code` answered in the container, yet no command in the 24 logs asked it anything. The one command that named it was `ls /opt/puffin-code/bin`. The first version of the counter counted that as a call; it now counts only `puffin-code <query verb>`, and a test fixes the difference.
- **The two arms differ in four instances out of 24 with the model, build and instances unchanged and the tool unused.** That is this benchmark's run-to-run noise at this size, and the nearest thing to the spread measurement §7.3 asked for: a difference of two instances either way is not a finding.
- **The with-arm was slower and used more tokens, and the cause is not established.** The index cannot be it directly (no query was made). What differed: about 250 tokens of prompt block on every request; and whatever else used the model server that night, which was not recorded. A third run without the index would separate the two and was not made.
- **The isolation held where it was tested by the agent itself.** In `django__django-16100` the agent tried `puffin-fetch` on the upstream file at `raw.githubusercontent.com`, which would have shown it the fix; the command does not exist in the container and there is no route out. Its looking around for the web commands is what named `puffin-code`.
- **The manifest's `repository_commit` is `HEAD` when the run started, not proof of the code that ran:** both runs were made from a working tree with uncommitted changes, and `--against` lists the two commits as differing for that reason only.
- **Open, and the real result:** this model does not reach for `puffin-code` on its own in this setting. It does elsewhere: 25 recorded session files under `~/.puffin/sessions` contain `puffin-code` queries (not checked: how many of those were tests that asked for them), so the setting, a bare issue text and an unattended run, is the likelier cause than the model. Whether the task prompt should name it, or the block's wording should change, is a question for the code-index spec; an A/B where the tool is actually used needs one of those first.


### 13.6 With the tools, measured (2026-10-03)

After §13.5 the index became tools (`code_*`, CODE_INDEX §15). This pair is the first in which the agent used them. Branch `swe/index-arm`: `a7e49af`, `5e7a134`, `93386d5`, `6ffb18f`.

**What had to change first.**
- **The agent could not write `/testbed`** (PUFFIN_PROMPT §6.1 item 1): the scrub step now opens it all, in both arms.
- **The tools were missing under load.** In the first attempt (`idx14-on`, stopped after three instances and marked `INVALID.txt`), `code_search` came back `unsupported call: code_search`: Codex waits 1 s for an optional MCP server before the first request and leaves out the tools of one that is not up (CODE_INDEX §15.4). The runner now declares `puffin_code` itself with `required = true`.
- **The task prompt names the tools in this arm only** (`CODE_INDEX_HINT`, two sentences: `code_search`/`code_def` before grep, `code_impact`/`code_callers` before an edit). The system prompt's block alone had left the index unused (§13.5). A test pins the plain arm's prompt as it was.

**The pair.** `idx14b-on` then `idx14b-off`, back to back on one `puffin` (`runtime_hash` `bd978d3ede04`, the build installed on 2026-10-02 15:37, which predates that day's merges) and the installed `puffin-code` (`a2343be2…`). Chosen before either ran: the 14 instances of the §12.5 sample **not** resolved by both earlier arms (the 9 resolved by neither plus the 5 on which they differed; `astropy-13453` counted as differing, since the two runs' records disagree on it). The 10 others were resolved by both and say little about a difference. Departures from §12, the same in both arms: `task_context` 44,000 (the KV pool was 133,308 tokens, not 157K, and 49,152 allowed two at once), three at once, no idle wait, open sessions ignored (other tasks were using the machine), `--until 17:05`.

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

Difference in resolved rate: +14.3 points, 95% interval −12.7 to +41.3, McNemar exact p = 0.625; the report's own verdict is "No measurable difference". `--against` lists `repository_commit` as differing (`5e7a134` and `6ffb18f`): the worktree gained two commits between the two starts, and they change only the code-index arm's host side (`swe_bench_code_index.py`, the `puffin-code.sha256` record), `puffin-code-rs`, the launcher and tests; `swe_bench_instance_run.py`, the plain arm's whole path, is the same in both.

**How the tools were used** (295 calls in 14 of 14 instances): `code_show` 207, `code_search` 54, `code_impact` 17, `code_callers` 9, `code_refs` 4, `code_def` 4. 20 answers were empty: 3 searches because the model passed an absolute `path`, 10 `refs`/`impact` answers for methods the graph cannot see called (both fixed in `93386d5`, **not measured** here), 6 `show`s of a name not found and 1 other search. The run store's `puffin_code_calls` and a count from the session files agree for every instance.

**What this does and does not show.**

- **The tools are used now**: 295 calls in 14 of 14 instances, against none in 24 in §13.5. That, not the score, is the result of the fixes above.
- **The score is inside the noise.** Two arms with the tool unused differed on 4 of 24 instances (§13.5); here 4 of 14 differ, 3 one way and 1 the other. Two instances no earlier run had resolved were resolved here: `django-12774` in both arms (so more likely the write fix than the index) and `sympy-13031` only with the tools (without them it stopped at 140 s with an empty patch). That is one sample, an observation and not a finding.
- **The cost is mixed.** With the tools: 23% fewer input tokens and 28% fewer output tokens, the first efficiency number for the index; but 14% more agent time, 16% more commands, and both of the pair's timeouts (one of them resolved on its partial patch). `code_show` (207 calls) mostly replaced `sed -n` reads, which is why commands did not fall.
- **"Tests run after the last edit"** (a pattern match on the session's commands, not a measured behaviour): 9 of 14 with the tools, 8 without.
- **What remains unmeasured**: the two `puffin-code` fixes (`93386d5`) and the launcher's grace (`6ffb18f`; compiled and installed in the build of `258c3b5` on 2026-10-03, after the arm), since the arm ran the installed builds. The next pair should run on a rebuilt `puffin` and `puffin-code`, on the full 24 validated instances, with the plain arm run twice so the floor is measured in the same session.

### 13.7 Three arms on the full sample: the noise floor and the prompt (2026-10-03)

Run on the night of 2026-10-03 for PUFFIN_PROMPT §6.5, which has the full table: `ab-default-a`, `ab-highswe`, `ab-default-b`, all with the code index, on the 24 validated instances of §12.5 and one build (`runtime_hash` `39b8a92b6775`, `puffin-code` `cd4d6b91…`), `task_context` 44,000, three at a time.

- **Resolved: 16, 16 (`high-swe`), 15.** Two identical `default` arms differ on **3 of 24** instances in the same session (2 one way, 1 the other). With §13.5's 4 of 24 on another night, that is this benchmark's floor at 24 instances: a difference of up to about four instances between two arms is noise.
- **The index is used everywhere now:** 328, 415 and 334 `puffin-code` calls, in 23, 24 and 24 of 24 instances. The one instance without a call (`sympy-13031` in `ab-default-a`) resolved anyway.
- **Not an index result.** All three arms had the index; this night has no arm without it. The index-on against index-off comparison still rests on §13.6's 14-instance pair.
- **Timeouts still cost:** 2, 1 and 0 per arm, 45 minutes each. Without them the three arms' agent times are 3 h 39 min, 3 h 41 min and 4 h 10 min.
- **A runtime defect found and fixed on the way.** The build of 2026-10-03 links `liblzma.so.5`, which `SweBenchRuntime` refused ("puffin needs liblzma.so.5, which the runtime does not carry"), so no instance could start. It now copies optional libraries when `ldd` names them, and libc, libm and libgcc_s stay required (commit `7d5e6e8`, branch `swe/runtime-lzma`; the three arms ran from that branch's code). **Until it is merged, `puffin-admin swe-bench run` on `main` fails the same way against any build that links liblzma.**

---

## 14. Refine, then fix (`--refine`, 2026-10-07)

**Why.** In the index-on round of 2026-10-06 (17 of 24 resolved), the agent edited a file the reference patch edits in 6 of its 7 failures; what failed was the change. The fix covered the issue's example but not its stated use case (django 15957, a limit "from each category" applied once overall), stopped at direct parents where grandparents also apply (15563), guarded a symptom instead of fixing the rule (scikit-learn 25747, django 16454), or missed a second code path the hidden test checks (13512, the admin's read-only display). A first step that only studies the issue targets these.

**What it does.** `swe-bench run --refine` (a new run only, recorded as `refine` in the manifest) runs each instance in two `puffin exec` sessions in the same container:

1. **Refine** (`REFINE_PROMPT`, at most `REFINE_TIMEOUT_S`, 15 minutes): read, run and test, change nothing under `/testbed`, and write `/puffin-scratch/refined.md` in six sections: intent, requirements as observable results, every code path (by callers and references), edge cases, what must not change, acceptance checks. With the code index the prompt names `code_callers` and `code_refs` for the paths.
2. The runner records whether the step changed the tree (it is told not to; this is measured), then puts `/testbed` back to the tree the agent started from (`RESET_SCRIPT`: `git read-tree -u --reset` to the recorded base tree, `git clean -fd`), keeping the description.
3. **Fix** (`FIX_PROMPT`, a new session, the full task timeout): the issue verbatim, then the description, marked as possibly wrong. The issue is authoritative where they disagree; no guard that only hides the symptom; every acceptance check is run before stopping. Nudges apply to this session as before.

The instance's state keeps `refine`: the description (up to 40,000 characters), its size, both steps' times, the first step's outcome and session, whether it changed the tree, and the log offset where the fixing session starts, so the report counts each step's tokens apart. `report` prints a "Refine first" line, and `--against` lists `refine` among the differing fields.

**Measured:** pending (the `im-refine` round on the 24-instance sample, then the 100-instance sample with and without the option).

---

## Sources

Fetched on 2026-10-01.

- [SWE-bench README](https://github.com/SWE-bench/SWE-bench/blob/main/README.md): the v5 CLI, `--task-repo`, the resource guidance, "Support for `arm64` machines is experimental", result caching by `run_id`.
- [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/): the predictions format and the layout of `logs/evaluation/<run_id>/`.
- [swebench on PyPI](https://pypi.org/project/swebench/): version 5.0.2.
- [SWE-bench pull request 521](https://github.com/SWE-bench/SWE-bench/pull/521) and [issue 520](https://github.com/SWE-bench/SWE-bench/issues/520): arm64 support, closed unmerged on 2026-08-12, and the x86 assumptions it listed.
- [`greynewell/swe-bench-arm64` on Docker Hub](https://hub.docker.com/r/greynewell/swe-bench-arm64), with its author's [write-up](https://greynewell.com/blog/swe-bench-arm64-native-containers-6x-faster/) and [data](https://gist.github.com/greynewell/497005bb33641503f1a5874f16578088): 1,798 of 2,294 instances native on arm64, 11 instances compared against x86, about six times faster than emulation. These are the author's figures, not checked here.
- [princeton-nlp/SWE-bench_Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified): 500 rows and the field list.
- [the upstream vendor: Why SWE-bench Verified no longer measures frontier coding capabilities](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/): the contamination findings of §8.
- [SWE-Bench Pro Verified (arXiv 2609.08149)](https://arxiv.org/pdf/2609.08149): the July 2026 withdrawal of the SWE-bench Pro recommendation and the estimate of about 30% broken tasks, as reported by a web search summary; the paper itself was not read.
