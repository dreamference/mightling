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
