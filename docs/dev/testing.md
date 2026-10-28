# Tests never touch the real machine

Developer notes behind the first hard rule in `AGENTS.md`.

## The user's files

`tests/conftest.py` gives every test its own `HOME`. Before it existed, the suite rewrote the real `~/.continue/config.json` on every run, and a step of the (since retired) Onyx web chat's `configure()` once rewrote the real Onyx `.env` and recreated the live nginx container.

HOME alone does not reach paths a module computed at import (`VLLM_CACHE_HOME`, the ling install paths), so the same fixture re-points every string/`Path` attribute of every `dreamference` module that lies under the real home (outside the checkout) into the test's home. Before that, a test stamped the real compile-signature file and the next real `server start` discarded the torch.compile cache.

## Running containers

Until 2026-09-29 every offline run of the `configure` tests recreated the live Gmail sidecar, joined SearXNG and the speech-to-text sidecar to Onyx's network, copied test logos into the web server, rewrote its bundle and patched the API server through `docker exec`; once HOME was isolated, the recreated Gmail sidecar carried a temp folder and test secret and Gmail search answered "unauthorised".

conftest now fails any real `docker` command that changes something (reads like `docker info` stay allowed), stubs `GoogleService.start` and `RetiredWebChat.offer` (the one-time offer to remove Onyx's leftovers); a test of one of them restores it from the `REAL_*` names in conftest. The sidecar tests (`test_image_search_sidecar.py`, `test_retired_web_chat.py`) put a recorder in place of `subprocess.run`. A test needing a real path must opt in explicitly.

## The same rule elsewhere

- Tests never write `/etc/avahi` or run sudo (conftest points `NodeServiceFile.service_path` at scratch; [node.md](node.md)).
- Tests never read or change this machine's Wi-Fi (since 2026-10-12): conftest stubs `WifiPowerSave._read`, so whether this machine is on Wi-Fi with power saving on decides no test of `host check`, `host setup`, `node enable` or `node prepare`, and fails any `nmcli` or `iw` command carrying a word that changes something (`modify`, `set`, `up`, `down`, `connect`, …; `_changes_network`). A test of the Wi-Fi check sets its own `_read` (`tests/test_wifi_power_save.py`); the launcher's Rust tests of the same reading take the commands' output as strings and run nothing ([node.md](node.md#advertising-a-node)).
- Night Shift's tests use a scripted stand-in for `ling` and never create a real scope or unit ([night-shift.md](night-shift.md)).
- SWE-bench's tests never start a container or touch the real cache ([swe-bench.md](swe-bench.md)).
- `ling-docs` tests never run with the real `HOME` ([local-file-index.md](local-file-index.md)).
- **A harness that runs the real `ling` with a scratch `CODEX_HOME` sets `DREAMFERENCE_MIGHTLING_DOCS=0`** (the egress audit, a Night Shift task, `ling-admin codex test`; do the same in any ad-hoc check). On 2026-10-26 two checks run right after `codex build`, each with a fresh `CODEX_HOME` beside the real `HOME`, had the launcher's `ling-docs session` add `~/Documents` and `~/Downloads` to the empty home and embed all of `~/Downloads` from nothing (2 h 10 min of CPU in 32 minutes), which held up that night's SWE-bench run. `ling-docs` now indexes on its own only in `~/.mightling` (LOCAL_INDEX §9), but the variable keeps the session from starting at all. A harness that gives the session its own `HOME` as well (`audit egress --docs`, `--sync`, the desktop e2e) is safe as it is.
- The vLLM launcher is tested against test-only recipes (`vllm_recipes` in `tests/conftest.py`; [models-and-engines.md](models-and-engines.md)).

## The built binary and the model server

The suite is offline by construction, not by habit. Since 2026-10-10 `tests/conftest.py` fails any
test that spawns a binary of the install (the links in `~/.local/bin`, the install directory
`~/.local/share/dreamference`, the build cache) or connects to the model server's ports (8000, and
18000, the engine behind the gate): the message names the path or the address. Before that, a
suite run during a benchmark started the installed `ling`, which waited behind the model gate, and
the only way to know was to watch the process list. A test that needs an agent writes a scripted
stand-in under its own `tmp_path` and passes its path (`FAKE_MIGHTLING` in `test_night_shift.py`,
the `strace` stand-ins in `test_egress_audit.py`); a test that needs a server starts one on a free
port. The one opt-out is the marker `installed_binary` (registered in `pyproject.toml`): it is set
on the whole of `test_mightling_slash_commands.py`, whose point is the built `ling`, and whose live
tests skip on their own while no server answers or the gate is closed. Mark nothing else with it
without a reason of the same kind.

Since 2026-10-27 no test reaches the network: conftest stubs `ReleaseAssetDownloader.ensure` (answers "complete", so `server start` proceeds) and makes `download` and `_fetch` fail the test, and the socket guard above refuses any connection to an address that is not loopback. Before that, `server start` tests downloaded the ScaleSearch drafter's release assets from GitHub for real and the suite hung. A test of the downloader restores the three from the `REAL_RELEASE_*` names (`_release_server` in `test_hardware.py`).

The suite's time is process start-up and the sleeps the stand-ins play out, not the machine: the
Night Shift and egress-audit tests run several stand-in processes each and take 1 to 4 s; the rest
is about 0.1 s a test. It was measured at 2 minutes for about 1,080 tests on 2026-10-10 (the
"8 s" the project guide used to claim dated from a tenth of the suite).

## The code-standard ratchet

The Python standard (`specs/DREAMFERENCE_PYTHON_QUALITY.md`) is checked inside the suite, not in
a CI job, by two tests built on 2026-10-20 (spec §11):

- `tests/test_code_quality.py` runs `ruff check` and `ruff format --check` over `dreamference/`
  and `tests/`, plus the project checks no linter knows (`PQ001` a second class in a file, `PQ002`
  an `__init__.py` without `__all__`, `PQ003` work at import time), and compares the counts per
  file and per rule with `tests/quality_baseline.json`. A file the formatter would change counts
  as one `ruff-format` finding: the files unformatted on 2026-10-20 are in the baseline, every
  other file must stay formatted.
- `tests/test_typing.py` runs mypy (`[tool.mypy]` in `pyproject.toml`) over `dreamference/` and
  compares its errors per file and error code with `tests/typing_baseline.json`.

A count above its baseline fails and names the file and the rule; **a file missing from the
baseline, a new one, counts from zero**, so it is formatted and clean from its first commit.
In code written in today's house style the findings are mostly ones the tools fix themselves:

```bash
.venv/bin/ruff format <file> && .venv/bin/ruff check --fix <file>   # then: ruff check <file>
.venv/bin/mypy                                                       # from the checkout's root
```

A count below its baseline passes with a warning in the summary; lock the cleanup in with the
same commit:

```bash
.venv/bin/python tests/test_code_quality.py --update-baseline
.venv/bin/python tests/test_typing.py --update-baseline
```

`--update-baseline` refuses to raise a count. `--allow-raise` writes anyway, for a reviewed
exception or a re-recording over others' merges, and the commit says why. Both tests fail (they
never skip) when ruff 0.16.10 or mypy 2.4.0 is missing or another version: the message gives
`.venv/bin/pip install -e .[dev]`. They add about 1.2 s to the suite; mypy's first run in a fresh
checkout or worktree fills `.mypy_cache/` and takes about 14 s more.

A branch opened before 2026-10-20 that adds Python files fails here after its rebase until those
files are formatted and fixed as above.

## Daily QA on the idle machines

Two machines that are otherwise idle run a QA pass every morning (owner's decision, 2026-10-28). Neither is part of the unit suite: they install real releases, talk to the real node and run on real hardware, which the rules above keep the suite from ever doing. Both are started by user timers on the node, never by GitHub's `schedule` trigger, which does not fire in this repository; the timers' commands run in `mightling-batch.slice` at nice 10. The units, the machine names and any address stay on the node, outside the repository.

- **Windows (06:30 UTC)**: `.github/workflows/windows-qa.yml` on the self-hosted x86-64 Windows runner, dispatched by `scripts/qa/dispatch_windows_qa.sh` (`gh workflow run windows-qa.yml --ref main`, then a wait for the result). Jobs, one after another on the box: the Python suite on Windows; the desktop app's unit tests and typecheck (`desktop/ui`, `desktop/electron`; the e2e suite is skipped with a note, since the runner is a service in session 0 with no display and the e2e stand-in for `ling` is a POSIX script); the release before the latest installed with `install.ps1`, `ling update` to the latest (binaries and app), `ling uninstall` and the app's uninstaller, as the runner's standard user; and last, the launcher's cargo tests after a build, as `windows.yml` runs them. It yields to release work: its first job, on GitHub's runner, waits while a `release.yml`, `release-dry-run.yml` or `windows.yml` run is queued or running (three hours, then the day is skipped), and between its box jobs a queued release job takes the runner first. The runner's job-started hook lists `windows-qa.yml` among the workflows it accepts. A failure keeps one issue, "Windows QA failing" (closed by the next green run on main), and the dispatcher alerts the owner.
- **x86-64 Linux desktop (07:00 UTC)**: `scripts/qa/run_desktop_qa.py --host <ssh alias>` runs `scripts/qa/desktop_linux_qa.sh` over SSH inside a transient user scope on the desktop (6 GB, four CPUs' worth, nice 19, idle I/O class). It runs `ling update` on the desktop's own release install, `install.sh --role client` from the latest release into a throwaway `HOME`, `ling --version`, one `ling exec "Reply with exactly one word: pong"` against the node over the LAN (the node's mDNS name) and over the remote-access overlay (its overlay name, while the desktop's overlay client is connected), `ling web` answering on both, and, from a fresh clone of main in a virtualenv (PyTorch from its CPU index), the wheel and the full test suite. A model gate answering 403 (a benchmark run) skips the `exec` legs. The node computes both of its names at run time and passes them in. The report is `~/.local/state/dreamference/qa/desktop/<time>.{json,md,log}` (`latest.*` the newest); the owner is alerted only when a check fails or the desktop cannot be reached.
- **Release dry run (02:07 UTC)**: `.github/workflows/release-dry-run.yml`, dispatched by gx10's `mightling-release-dry-run.timer` (`workflow_dispatch`). The workflow has no `schedule:` trigger of its own (removed 2026-10-28: with both it ran twice a day); the timer is the only scheduler. Pushes to `release-dry-run/**` and hand dispatches still run it.
