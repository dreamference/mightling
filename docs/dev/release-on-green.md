# Release on green

Owner's decision, 2026-10-28: a release goes out whenever main is green and holds something users
would notice. Releases were already automatic once started (built, signed and published by
`release.yml` with no approval); this adds the start.

## Where it runs

`.github/workflows/release-on-green.yml`, one job, on `workflow_run` completion of **Release dry
run** or **Release** on main (plus a hand `workflow_dispatch`). It runs `scripts/release_on_green.py`,
which reads the repository state afresh each time (what triggered it does not matter) and either
prints `skip: <reason>` (a green run) or dispatches `release.yml` on `main` with the repository's
`GITHUB_TOKEN` (`actions: write`; a `workflow_dispatch` made with that token does start the
workflow). The job never tags, signs or publishes; `release.yml` does, as for a manual dispatch.
It has no `pull_request` trigger. GitHub's `schedule` trigger never fires in this repository, so
none is used: a skipped start is picked up by the next completion of either workflow.

## What "green" means

This repository has no per-push test workflow: the suite runs in the pre-push hook before a merge,
and the full build and test pass on main is the nightly **Release dry run** (02:07 UTC, started by
gx10's timer). Green is therefore: no run on main's head commit failed, was cancelled or is still
going (Release and Release on green runs excluded), **and** a Release dry run succeeded on that same
commit. Consequence: a merge reaches a release after the next dry run (nightly), or sooner after a
hand-dispatched dry run on main. `release.yml` still runs the tests itself (`run_tests`).

## The rules, in order

1. `RELEASE_ON_GREEN` (repository variable) must be exactly `true`; unset, `false` or anything else
   is off, and nothing else is read.
2. No Release run queued, waiting or running (serialization). The next completion of the Release or
   the dry run re-evaluates and takes the latest green main.
3. The newest Release run, any outcome and a manual one too, started at least 3 hours ago. A failed
   release therefore retries at most every 3 h, and only on a new evaluation.
4. Green, as above, for main's head. Main is re-read just before the dispatch; if it moved, skip.
5. A path changed between the newest stable tag (`vX.Y.Z`, pre-releases ignored) and the head
   outside `docs/`, `specs/`, `tests/`, `website/`, `paper/`, `images/`, `.github/`, `codex-tests/`,
   `scripts/qa/`, `mkdocs.yml` and root-level `*.md`. Otherwise skip.
6. Version: that tag's patch plus one. Minor and major releases stay manual dispatches. The tag
   must be free.

Notes: if `specs/DREAMFERENCE_RELEASE_<version>.md` is on main the release uses it, else it is
dispatched with `generated_notes=true` (new input of `release.yml`: publish with GitHub's generated
notes instead of failing on the missing spec). Dispatches use the defaults for everything else
(tests on, desktop, ling, Windows on; the clients and Mac dmgs off), `prerelease=false`,
`draft=false`.

The qualification rules are unchanged and live outside this trigger: an engine pin changes on main
only after a qualifying night, so releasing main never ships an unqualified pin; the release
benchmark entry (`docs/releases/<version>.json`) stays optional and, being under `docs/`, never
starts a release by itself.

Residual race: `workflow_dispatch` takes a branch, not a commit, so a merge landing between the last
check and the release's checkout is released although not yet checked; the check-then-dispatch gap
is seconds. The release's own test gate is the second check.

## Switching it on and off

```bash
gh variable set RELEASE_ON_GREEN --body true     # on
gh variable set RELEASE_ON_GREEN --body false    # off (a release already running finishes)
gh workflow run release-on-green.yml --ref main  # evaluate now (prints skip: or starts one)
```

To see what it would do without the switch: run `GITHUB_REPOSITORY=<owner/repo> RELEASE_ON_GREEN=true
python3 scripts/release_on_green.py --dry` in a clone with `gh` signed in.

Tests: `tests/test_release_on_green.py` (every `gh` and `git` call faked).
