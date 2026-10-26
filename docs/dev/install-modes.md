# The two install modes

Developer notes behind the "two install modes" rule in `AGENTS.md`. Spec: `specs/DREAMFERENCE_SETUP.md` §3.2–§3.3, which lists what was verified (a scratch home here) and what was not (a second machine).

## A checkout and a release install

There are two ways Mightling gets onto a machine, and the package must work in both.

- A **checkout** (`pip install -e .`, `ling-admin codex build`) has `codex/`, `codex-patches/` and the crates beside the package.
- A **release install** (`install.sh` at the repository root, attached to each release) has the wheel in a virtualenv of its own and the release's prebuilt binaries in `~/.local/share/dreamference/mightling/bin`, with nothing beside the package.

`CodexBrandedBuilder.has_source()` and `DesktopRunner.has_source()` tell them apart: without source, installed binaries count as current and `codex build`/`desktop build` say what to do instead. Before 2026-10-02 nothing had ever been installed from a release, and `ling-admin run` on one installed rustup and then died on the missing `ling-web-rs/`. Anything new that reaches for a path under `REPO_ROOT` needs the same guard.

## `install.sh`

`install.sh` downloads exactly what `ling update` does (`ling-rs/src/update.rs`: same asset names, same checksum file), so the two stay one mechanism. On a GB10 it also installs `ling-admin` and, unattended (one sudo password at most, at the start; SETUP §3.2.1), applies `host setup --yes`, the docker group, lingering, `node enable --yes`, the model download and `server start`, offering system updates as its one question.

On a Mac it also installs the desktop app from the release's dmg, and `install.ps1` on Windows runs the release's NSIS installer silently (`--no-app` / `-NoApp` leaves it out; SETUP §3.2, the owner's decision of 2026-10-26). Both are fetched by the script, so they carry no quarantine mark or Mark of the Web and the unsigned builds open without Gatekeeper's or SmartScreen's dialog. `ling update` updates the binaries only; running the installer again updates the app.

A GB10 is recognised by arm64 and `nvidia-smi` naming it, or the GPU's PCI id `10de:2e12` where no driver answers yet; never the vendor, since each of the eight GB10 machines names itself differently in DMI and this one says `GX10` (SETUP §3.5).

## `HostSafetySetup`

`HostSafetySetup` (`vllm_server/host_safety_setup.py`) applies what `check_host_safety()` only printed, taking every reading from the check's own helpers; it resizes swap only when it is the single `/swap.img`, and its sudo path is covered by tests alone, since this machine already passes. See [host-safety.md](host-safety.md).

## Old release names

Mightling was called Puffin until 1.5 (renamed 2026-10-07; `specs/DREAMFERENCE_RENAME_MIGHTLING.md`). Two things carry the old name on purpose: releases still publish the `puffin-<target>.gz` / `puffin-<target>.sha256sums` asset set (built from the same binaries) so 1.4.x installs can `puffin update` into Mightling, and a one-time migration (`ling-rs/src/rename.rs` in the launcher, `dreamference/cli/legacy_name_migration.py` in `ling-admin`) moves an old installation's folders, links, keys, units and settings.

Internal names kept unchanged: the build caches `~/.cache/dreamference/puffin-codex` (and `puffin-web`, `puffin-code-build`), the AppArmor profile `/etc/apparmor.d/puffin-bwrap`, and SWE-bench's stored `puffin_version` / `puffin_code_calls` keys.
