# The two install modes

Developer notes behind the "two install modes" rule in `AGENTS.md`. Spec: `specs/DREAMFERENCE_SETUP.md` §3.2–§3.3, which lists what was verified (a scratch home here) and what was not (a second machine).

## A checkout and a release install

There are two ways Mightling gets onto a machine, and the package must work in both.

- A **checkout** (`pip install -e .`, `ling-admin codex build`) has `codex/`, `codex-patches/` and the crates beside the package.
- A **release install** (`install.sh` at the repository root, attached to each release) has the wheel in a virtualenv of its own and the release's prebuilt binaries in `~/.local/share/dreamference/mightling/bin`, with nothing beside the package.

`CodexBrandedBuilder.has_source()` and `DesktopRunner.has_source()` tell them apart: without source, installed binaries count as current and `codex build`/`desktop build` say what to do instead. Before 2026-10-02 nothing had ever been installed from a release, and `ling-admin run` on one installed rustup and then died on the missing `ling-web-rs/`. Anything new that reaches for a path under `REPO_ROOT` needs the same guard.

## `install.sh`

`install.sh` downloads exactly what `ling update` does (`ling-rs/src/update.rs`: same asset names, same checksum file), so the two stay one mechanism. On a GB10 it also installs `ling-admin` and, unattended (one sudo password at most, at the start; SETUP §3.2.1), applies `host setup --yes`, the docker group, lingering, `node enable --yes`, the model download and `server start`, offering system updates as its one question.

A GB10 is recognised by arm64 and `nvidia-smi` naming it, or the GPU's PCI id `10de:2e12` where no driver answers yet; never the vendor, since each of the eight GB10 machines names itself differently in DMI and this one says `GX10` (SETUP §3.5).

## `HostSafetySetup`

`HostSafetySetup` (`vllm_server/host_safety_setup.py`) applies what `check_host_safety()` only printed, taking every reading from the check's own helpers; it resizes swap only when it is the single `/swap.img`, and its sudo path is covered by tests alone, since this machine already passes. See [host-safety.md](host-safety.md).

## Old release names

Releases still publish the `puffin-<target>.gz` / `puffin-<target>.sha256sums` asset set (built from the same binaries) so 1.4.x installs can `puffin update` into Mightling; see `specs/DREAMFERENCE_RENAME_MIGHTLING.md`.
