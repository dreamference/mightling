# The 1.8.0 cut: ling-serve becomes the default engine

1.8.0 serves the default model with ling-engine's `ling-serve`, four conversations at once at 196,608
tokens each (paged memory, four at the full 262,144, comes in a later 1.8.x), with SGLang one flag away. Mightling's side is in the tree; the switch (step 6) is
made on the branch `prep/1.8.0` (2026-10-29), which is merged only once the bar below has passed on the
pinned engine. This is the checklist, for the engine's paged memory (P4b-P4d,
ling-engine `specs/DREAMFERENCE_LING_ENGINE_BATCHING.md` §5.3).

## What is already in the tree (1.7.x, inert)

- `QWEN38_LING_RECIPE` carries `sessions: 4` and `batch: 4` (`QWEN38_LING_SESSIONS`, `QWEN38_LING_BATCH`).
  `LingServeLaunchBuilder.server_args` passes `--sessions 4 --batch 4 --kv-pages` **only when the recipe's
  `engine_features` lists `"kv-pages"`**. At the 1.7.2 pin (8d112d0, no features) nothing changes: the engine's
  own sessions, no flag it may not parse. **Owner decision 2026-10-29: 1.8.0 ships with paging OFF.** The pin
  (02704d4, P4d merged, features `kv-pages` and `sparse-head`) is flat by default, and the recipe's `kv_pages`
  (`QWEN38_LING_KV_PAGES`) is `False`: the launch passes no `--sessions`, `--batch` or `--kv-pages` (still
  `--vision off`) except what the flat layout needs: `--max-context 196608 --sessions 4 --batch 4`, four flat
  slots of 196,608 tokens each (the engine's auto-size gave only 3 x 262,144 on the node-b figures). Paged
  memory, four at the full 262,144, comes in a later 1.8.x after its own night.
- `LING_ENGINE_KV_PAGES_SINCE` is ling-engine's last P4b commit (`f180b44`). `tests/test_hardware.py` checks, in a
  checkout with the submodule, that a recipe lists `"kv-pages"` exactly when its `engine_commit` descends from it
  (the same rule as the sparse head).
- The pre-load check already follows the engine's plan: `VLLMServerManager.ling_serve_plan` runs the image with
  the launch's own arguments plus `--plan-only` (ling-engine d4adbd5), so the plan is for 4 sessions when the
  launch asks for 4. An image older than the flag gives no plan and the check falls back to the recipe's 0.50
  arena with a warning.
- `server status` shows the sessions, the batch and `KV cache: Paged` of a running ling-serve.
- `QWEN38_DEFAULT_ENGINE` (model_matrix_registry.py) is the switch; `scripts/pin_ling_serve_image.py` moves the pin.

## The bar (the owner's, 2026-10-28)

All three, on the engine commit that will be pinned, before the switch:

1. The combined decode rate of four conversations is at least 34.6 tok/s (SGLang's measured pooled rate; four
   warm agent conversations, as BATCHING §5.5's measurement).
2. Identical output: greedy 16/16 per turn, paged against flat at 1, 2 and 4 streams, warm and cold, at
   `--sessions 4 --max-context 262144`; `ling-run --spec-check` on the long prompt; the engine's own checks with
   `--kv-pages` (BATCHING §5.3).
3. A night with no loss (a SWE-bench or Night Shift night on the new default, no regression against the same night
   on SGLang). Nothing ships before it.

The other conditions the owner set on 2026-10-26 and that the switch also needs: images read and adapters served
were moved to 1.8.1 (decided 2026-10-28/29): 1.8.0 starts ling-serve with `--vision off` (the recipe's
`"vision": False`) and serves adapters only on SGLang (a start without `--engine` moves to SGLang while an adapter
is installed; `--engine ling` is refused), both named in the release notes' known issues; and the
live long-context test of [models-and-engines.md](models-and-engines.md#ling-serve-beside-sglang-opt-in-172)
(a ~205K-token needle, decode at that length, the start inside the container's cap) repeated with four sessions.
Eight sessions only after the engine's eight-session reproduction and the owner's say.

## Steps

1. **Engine side (not here).** P4b-P4d merged into ling-engine main with their gates; the commit to ship chosen
   (a branch like `release/1.8.0-engine`); `ling-serve --help` read once for the flags (`--sessions`, `--batch`,
   `--kv-pages`, `--auto-size`, `--plan-only`; main.cpp's header comment is the reference). If P4d makes
   `--kv-pages` the default, the explicit flag stays harmless; if a flag is renamed, change
   `LingServeLaunchBuilder` and `test_the_paged_launch_uses_only_flags_ling_serve_parses`.
2. **Build and publish the image** on the engine's development machine:
   `scripts/build_ling_serve_image.sh COMMIT` (builds under neutral paths, runs the anonymity check, tags
   `ghcr.io/dreamference/ling-serve:<first 12 of COMMIT>`), then `docker push` and note the digest GHCR prints.
   Never replace a pushed tag; a rebuild is a new push and a new digest. Add the vision libraries to
   `docker/ling-serve/Dockerfile` if the engine has vision.
3. **Move the pin** in one commit on a branch (`models/pin-ling-serve-<commit12>`):
   `scripts/pin_ling_serve_image.py COMMIT sha256:DIGEST --features kv-pages,sparse-head` (`--check` shows the diff first;
   add `sparse-head` if it was already listed). Then by hand: the comments above the constants in
   model_matrix_registry.py (the commit's story, the binary's sha256, the registry's earlier digests),
   `LING_ENGINE_KV_PAGES_SINCE` if P4c/P4d should be what gates the flags (change it to their last commit and
   re-run `tests/test_hardware.py` with the submodule fetched), `tests/test_ling_serve_engine.py`
   (`startswith("02704d4")`, `engine_features == ["kv-pages", "sparse-head"]`, `kv_pages is False`), and the measured paragraph of
   models-and-engines.md.
4. **Live checks on the node** (nothing mocked): `ling-admin server start --engine ling`, then
   the NVFP4 canary and the tool call that `server start` runs, `server status` (the engine commit, `Sessions 4
   (batch 4)`, `KV cache Paged`), host memory before and after (the cgroup does not count device memory: the plan
   and the PSI watchdog bound the load), then the three bars above.
5. **The night.** Run it on the pinned image with the default switched on a branch; compare with the same night
   on SGLang. A loss stops the cut: leave the switch off and ship the rest.
6. **The switch** (one line), **done on `prep/1.8.0` (e2fdb895, 2026-10-29)**: in
   `dreamference/hardware/model_matrix_registry.py`

   ```python
   QWEN38_DEFAULT_ENGINE: Final[str] = ENGINE_LING   # was ENGINE_SGLANG
   ```

   `launch_overrides` becomes ling-serve's recipe and `engine_recipes` SGLang's (`split_engine_recipes`);
   `ModelMatrixRegistry.engines()` lists `ling` first, `server start` without `--engine` serves ling-serve,
   `--engine sglang` serves SGLang with its runtime patches, template patches and adapters unchanged.
   Then update `test_sglang_stays_the_default_and_ling_serve_is_opt_in` and the notes in the matrix entry
   (the tests that name the default engine), and the user docs (`docs/`) that say SGLang is the default
   (done: the tests in e2fdb895, `docs/models.md` and `docs/architecture.md` restored from `docs/1.8.0`).
7. **Cut.** Version bump and release notes as every release (releases are automatic once main is green). The
   release notes (`specs/DREAMFERENCE_RELEASE_1.8.0.md`) say: the default engine changed, four sessions, the
   rollback below, images and adapters on SGLang until 1.8.1, fair pairing only if the pinned image has it
   (off by default); `specs/DREAMFERENCE_RELEASE_1.8.1.md` holds what moves to 1.8.1.

## Rollback

- Per start: `ling-admin server stop && ling-admin server start --engine sglang`.
- Persistent: `server_engine = "sglang"` in `~/.config/dreamference/config.toml` (or
  `DREAMFERENCE_SERVER_ENGINE=sglang`). `server start` prints this when ling-serve fails a start check
  (`_suggest_engine_fallback`) and never switches by itself.
- For everyone: put `QWEN38_DEFAULT_ENGINE` back to `ENGINE_SGLANG` and release a patch. The SGLang recipe, its
  image and its two runtime patches stay in the registry untouched throughout, so this is the whole rollback.
- A bad pin alone (the engine, not the default): `scripts/pin_ling_serve_image.py` with the previous commit and
  digest (the registry's comment lists them); images are never deleted from GHCR except for a path leak.

## Tests that cover this

`tests/test_ling_serve_engine.py` (the flags by feature, the plan-only arguments, the swapped matrix with
SGLang one flag away, the pin script) and `tests/test_hardware.py` (the feature against the submodule's history).
