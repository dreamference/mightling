# Cutting 1.8.1

The 1.8.1 cut is a short list because everything before it is already on `prep/1.8.1`. The release notes are
`specs/DREAMFERENCE_RELEASE_1.8.1.md`; its "Plan" section says what the 2026-10-31 night decides.

## Before the night

- The candidate image is built from ling-engine main once it has P4d (paging off by default), fair pairing (off by
  default) and image input, tagged `candidate-181-<short commit>`, never a release tag. Its digest and commit are
  in the prep report. `scripts/build_ling_serve_image.sh` builds it (the engine's development machine), the same way
  as 1.8.0's, then `docker push`.
- Pin the candidate on `prep/1.8.1` so the night's arms run it:

  ```bash
  .venv/bin/python scripts/pin_ling_serve_image.py COMMIT sha256:DIGEST --features kv-pages,sparse-head,paging-switch,fair-pairing --check
  .venv/bin/python scripts/pin_ling_serve_image.py COMMIT sha256:DIGEST --features kv-pages,sparse-head,paging-switch,fair-pairing
  ```

  Then by hand: `grep -n '<old short commit>\|engine_features' tests/`, the prose above the constants in
  `model_matrix_registry.py` (machines by role only), and add `LING_ENGINE_PAGING_SWITCH_SINCE` and
  `LING_ENGINE_FAIR_PAIRING_SINCE` (the engine commits that introduced `--no-kv-pages` and `--fair-pairing`) with a
  test beside `test_a_ling_engine_recipe_lists_kv_pages_exactly_when_its_commit_has_the_page_pool`.
  `test_ling_serve_plan_runs_plan_only...` asks the plan of the recipe as it launches: with `kv_pages` off the plan has
  no pool, so check what it asserts.
- An arm sets `DREAMFERENCE_LING_PAGING` and `DREAMFERENCE_LING_FAIR_PAIRING` (`0` or `1`) in the environment of
  `ling-admin server start`; `server status` shows `KV cache` and `Fair pairing`, and `docker inspect` the flags.

## After the verdict (the verdict names the two defaults)

1. In `model_matrix_registry.py`, `QWEN38_LING_RECIPE`: `"kv_pages"` to `True` only if the paging default is on,
   `"fair_pairing"` to `True` only if the fair-pairing default is on. `"vision"` is `True` (image input).
   Update `test_paging_and_fair_pairing_are_off_in_the_recipe_and_switchable_per_launch` to the new defaults.
2. The pin: if the night ran on the candidate image and it passed, the candidate commit and digest become the pin
   as they are (same `pin_ling_serve_image.py` call, no rebuild; retag the digest as the release image only after
   the cut). If a fix landed in the engine since, rebuild and re-run the arm that touches it.
3. Rebase `prep/1.8.1` onto main (1.8.0 is cut by then). Notes: replace each `{{TBD` in the specs file with the
   measured text (images, fair pairing, adapters if merged; delete the adapters paragraph if LoRA is 1.8.2), and fill
   "Known issues" and "Upgrading from 1.8.0". Remove the STUB status line.
4. Tests: `.venv/bin/python -m pytest tests/ -q` (full suite, in `mightling-batch.slice` at nice 10), including
   `tests/test_code_quality.py` and `tests/test_typing.py`.
5. Dry run: `python3 scripts/release_on_green.py --dry` in a clone with `gh` signed in; it prints the dispatch
   (version = newest stable tag's patch plus one: `v1.8.1`) and starts nothing.
6. Merge to main (the owner's say). Releases are automatic once main is green and a Release dry run has
   succeeded on that commit (`docs/dev/release-on-green.md`); to go sooner, dispatch the Release dry run on main by
   hand. The tag and the publish are `release.yml`'s, never done by hand.
7. Ragon 1.9.0 is cut the day after, if 1.8.1 needs no hotfix in its first day.

## State of the candidate (2026-10-30)

- The image is the 1.8.0 one, reused: ling-engine 4853a93 (fair pairing and the head-major page pool merged, both
  off by default, image input in), `ling-serve@sha256:311325fe9f57768ad3ed920cba1580ef359a73c4c9246b308bcde2f506f27338`.
  The SINCE constants and the feature list are in; the recipe's `kv_pages` and `fair_pairing` are off, `vision` on.
- Open items (from the vision checks; they belong to the paging-on variant `prep/1.8.1-paging-on`):
  - the paged image checks still owed before paging ships: prefix, resume and batch;
  - with paging on, the harness's pool gauge (`max_total_num_tokens`) reads below sessions x context, so the harness
    admits three conversations, not four (tests/server test_sessions, test_regression);
  - the flat launch with vision on plans 1.7 GB more (99.3 GB available needed against 97.6 GB without); a
    desktop-capped host with 85-97 GB available refuses it, flat or not.
