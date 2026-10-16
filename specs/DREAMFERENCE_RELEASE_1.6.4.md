# Mightling 1.6.4 — release notes

**Status:** prepared 2026-10-15 on the branch `release/1.6.4` (origin/main at `49aee37`, 1.6.3 as
published, plus three commits and the version set to 1.6.4 in `setup.py`, `dreamference/__init__.py`,
the MCP server's `serverInfo` and `desktop/electron/package.json`, and these notes); not yet
published. It carries what was prepared on 2026-10-14 as 1.6.2 on the branch `release/1.6.2` and
never released: 1.6.3 was cut from `main` the next night, without that branch, so the number 1.6.2
is skipped and its content ships here, rebased onto 1.6.3 (the SGLang pin `84b9c16` as `63b448e`,
the Windows default `b352101` as `60c325a`; the branch's version commit `69632b6` is dropped). The
Code Mode `wait` fix 1.6.2 was to carry already shipped in 1.6.3. Built from
`git log v1.6.3..release/1.6.4`. The text between the two rules is the GitHub release's
description: the release workflow publishes it as it stands (`scripts/release_notes.py`), and
refuses to while a `{{TBD` is left in it.

**Version.** Set to 1.6.4 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo`
and `desktop/electron/package.json`. The release workflow stamps it into the binaries.

**What 1.6.4 carries beyond 1.6.3** (on this branch):

| Commit | What |
|---|---|
| 63b448e (first 84b9c16) | the SGLang image pin moved from v0.5.19 to v0.5.21, `--cuda-graph-max-bs` under its new name, and the probe that decided it (`docs/dev/sglang-probe-2026-10-14.md`) |
| 60c325a (first b352101) | the release workflow builds the Windows client again: `build_windows` defaults to on |
| 13070b7 | a launcher test (`docs_index`) that failed on Windows only: it expected the `ling-docs` path as a basic TOML string, and a Windows path is written as a literal one; test only |

**Not in 1.6.4:** `--num-continuous-decode-steps` stays on the launch line (dead code upstream, an
owner decision to drop it); drop-first compaction stays off; the branches `signal/multi-account`,
`chat/onyx-phase-c`, `bench/apply-patch-function-v2`, `remote/vpn-mvp-box`,
`security/sandbox-home` and `swe-bench/night10-harness` are not merged.

**Checklist before publishing:**
- **The suite:** `.venv/bin/python -m pytest tests/ -q` passes offline on this branch; nothing in it
  spawns the installed `ling` or reaches port 8000.
- **The launcher's Rust tests** pass in the build's export (`cargo test --release -p ling-launcher`,
  never in `codex/`), and the Windows workflow (`windows.yml`) passes on this branch for both
  targets, the launcher tests included.
- **The egress audit** (`ling-admin audit egress`, and `ling-admin audit egress --docs`) passes on
  the release build against this machine's model server.
- **The new image starts and answers:** `ling-admin server start` on the v0.5.21 pin loads the
  model, passes the NVFP4 and tool-call canaries, and the probe's answers are right (done on
  2026-10-14, `docs/dev/sglang-probe-2026-10-14.md`).

---

**The model server runs SGLang v0.5.21.** The default model's recipe pins `lmsysorg/sglang` by
digest; the pin moves from v0.5.19 to v0.5.21. The old image skipped a save of the recurrent state
when accepted draft tokens crossed a checkpoint, so a later prefix-cache hit could pair the new
attention cache with a stale state (SGLang #37817/#37818, fixed in v0.5.20). Before the move, both
images were probed on a GB10 for wrong answers with the same greedy requests and seeds: 48 at one
stream, 200 at two, 304 at eight, and 32 two-turn conversations over a long prefix; 0 wrong on
both, and the new image answered a few percent faster (67.6 / 121.8 / 297.7 tokens per second
over all streams at 1 / 2 / 8, against 63.7 / 112.7 / 269.1). One launch flag changed name with
the image (`--cuda-graph-max-bs` became `--cuda-graph-max-bs-decode`; the value is the same);
nothing else in the launch line changes. A node that updates pulls the new image (33.7 GB) at its
next `ling-admin server start`, before the running server is replaced; until then it serves on the
old one.

**The Windows client is back.** `install.ps1` installs `ling.exe` and its commands on Windows 11
(Arm64 and x86-64), as 1.5.1 did; 1.6.0, 1.6.1 and 1.6.3 shipped without them, only because the
release workflow left the Windows build off by default. The assets are the names `install.ps1` asks
for: `ling-<target>.gz`, `ling-<target>.sha256sums`, `codex-code-mode-host-<target>.gz`, and the
optional `ling-search`, `ling-fetch`, `ling-code`, `codex-windows-sandbox-setup` and
`codex-command-runner`, for `aarch64-pc-windows-msvc` and `x86_64-pc-windows-msvc`. Still a
preview: the programs are not code-signed, so Smart App Control must be off to run them. The
release's signed checksum list covers the Windows files like every other, and `ling update` checks
it on Windows too; `install.ps1` checks the per-target sums but not that signature yet (`install.sh`
and `ling update` do), a gap left for a later release.

---

## Upgrading from 1.6.3

`ling update` on a client or a node (a Windows client of 1.5.1 too; a Windows machine without one
installs with `install.ps1`). On a node, the next `ling-admin server start` pulls the v0.5.21 image
(33.7 GB) and serves on it; nothing else changes.
