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

## The code index in a run

`--code-index universal` indexes each instance's `/testbed` on the host (copied out of the image) and mounts the index read-only with a relocated `ling-code`. In the first with/without pair (24 instances, 13 resolved in each) **the agent never queried it**, so that pair says nothing about the index, and the report says so whenever that happens.

## Shared with Night Shift

Admission, the start checks and the runner lock are Night Shift's, imported: a benchmark run and a night run exclude each other, and the lock file names its holder. A paired node serving the same model can be an extra lane (`[swe_bench] nodes`), reached through a relay on the run's network gateway; see [node.md](node.md).

## Tests

Every `docker` call goes through `SweBenchDocker`, and the cache and results directories are module-level constants, so the tests (`tests/test_swe_bench.py`, where a container is a scratch git repository) never start a container or touch the real cache.

SWE-bench's stored `puffin_version` / `puffin_code_calls` keys keep their old names on purpose.
