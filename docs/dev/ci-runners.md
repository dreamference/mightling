# Self-hosted CI runners

Two jobs of the release pipeline can run on our own machines instead of GitHub's hosted runners,
each switched by a repository variable holding a JSON `runs-on` value, with a workflow input that
forces GitHub's runner for one run:

| Job | Variable | Input (`github` = hosted) | Hosted fallback |
| --- | --- | --- | --- |
| windows.yml, x86-64 `ling` and tools | `WINDOWS_X64_RUNNER` | `windows_x64_runner` | `windows-latest` |
| build-clients.yml, `aarch64-apple-darwin` | `MACOS_RUNNER` | `macos_runner` | `macos-15` |

Unset the variable when the machine is offline: a job waiting for a self-hosted runner queues for a
day. The x86-64 macOS client and the dmg jobs (mac-preview.yml, which opens a window) stay on
GitHub's runners.

## Never from a pull request

The repository is public, and a fork's pull request runs the workflow as the fork wrote it. None
of the workflows that reach a self-hosted runner has a `pull_request` or `pull_request_target`
trigger, and each runner's job-started hook refuses every event but push, workflow_dispatch and
schedule, any other repository, any ref but a branch, and any workflow but its own few
(`tests/test_macos_ci_runner.py` runs the macOS hook's cases). Both runners are registered to
dreamference/mightling only, run as a non-administrator account, and take a lock that a hand build
on the same machine shares.

## The macOS runner

`scripts/macos/ci-runner/setup.sh` sets a Mac up in two steps, idempotent, so moving the runner to
another Mac is the same two commands (its header has them): `--stage` as the administrator
registers the runner while the token is fresh, then `sudo bash /Users/Shared/mac-runner/ci-runner/setup.sh`
creates the hidden `ghrunner` account, installs the root-owned hooks and `.env`, puts Python 3.12
in the tool cache and rustup in the account's home, and runs the runner as a LaunchDaemon.
`CARGO_BUILD_JOBS` is one per 3 GB of memory. Labels `self-hosted, macOS, ARM64, mac-mini`: a
replacement with the same labels takes over without changing the variable.

The 8 GB Mac mini (M1, macOS 12.3) was tried on 2026-10-28 and is too small: the `ling` build
thrashed (swap grew to 2.7 GB, `codex_tui` alone took two hours) and ran for over four hours,
against 74 to 81 minutes on `macos-15`. `MACOS_RUNNER` stays unset until the 64 GB M5 Pro mini
arrives. The release's longest job is the x86-64 macOS client on `macos-15-intel` (83 to 130
minutes), so moving only the Apple silicon build does not shorten a release: that needs the
x86-64 build cross-compiled on the Mac, which the builder cannot do yet (it takes its target and
its prebuilt V8 from the host).
