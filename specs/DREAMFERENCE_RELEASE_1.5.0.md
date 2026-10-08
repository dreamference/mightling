# Mightling 1.5.0 — release notes

**Status:** draft, 2026-10-08, built from `integration/1.5.0` (every open branch of 2026-10-07 merged,
the rename applied, history rewritten). Not published: the release waits for the maintainer's
approval. 1.5.0 is the first release under the name Mightling and the first signed one. 1.4.2 was
prepared and never released; its changes are in this release (specs/DREAMFERENCE_RELEASE_1.4.2.md).
The text between the two rules is the GitHub release's description.

**Version.** 1.5.0 in `setup.py`, `dreamference/__init__.py`, the MCP server's `serverInfo` and
`desktop/electron/package.json`. The release workflow stamps it into the binaries.

**Checklist before publishing:**
- **Both asset sets:** the release carries both `ling-<target>.gz` and `puffin-<target>.gz`, both checksum files and `codex-code-mode-host`.
- **Signed:** `SHA256SUMS` and `SHA256SUMS.sig` are attached, and `ssh-keygen -Y verify` passes against `ling-rs/release-signing.pub` (specs/DREAMFERENCE_RELEASE_SIGNING.md §6).
- **Upgrade from a real 1.4.1 install** in a scratch HOME:
  1. `install.sh --version 1.4.1 --role client`;
  2. `puffin update`, which must install 1.5.0;
  3. `puffin` prints the notice, after which `ling --version` says 1.5.0, `puffin` is gone and `~/.mightling` holds the sessions.
- **The installer:** `install.sh` from 1.5.0 over a 1.4.1 install moves the folder and removes the old links.

---

**Puffin is now Mightling.** Same product, new name: an AI assistant already sells under the name
Puffin, and a confidential tool should not be confused with someone's cloud service. The bird stays.

**Updating from Puffin 1.4.x:**
1. Run `puffin update` one last time. It installs Mightling.
2. Run `puffin` once more. It moves your installation over and says so:
   - your sessions, history, settings and skills go to `~/.mightling`;
   - the binaries move to `~/.local/share/dreamference/mightling`;
   - the command links are replaced;
   - the `puffin_*` settings in `dreamference.toml` become `mightling_*`.

   No cloud sign-in or log is copied, and `~/.puffin` is left as it was.
3. From then on the command is **`ling`**. The old names are gone, not aliased.
4. **On a GB10 node,** run the installer again for **`ling-admin`**:
   `curl -fsSL https://github.com/dreamference/mightling/releases/latest/download/install.sh | bash`.
   Its first run moves the Night Shift timer, the network advertisement (which asks for sudo once) and paired machines' keys over to the new names.

| Before | Now |
|---|---|
| `puffin`, `puffin-search`, `puffin-fetch`, `puffin-code`, `puffin-app` | `ling`, `ling-search`, `ling-fetch`, `ling-code`, `ling-app` |
| `puffin-admin` | `ling-admin` |
| `puffin-admin puffin …` (the web chat) | `ling-admin chat …` (`onyx` still works) |
| `~/.puffin` | `~/.mightling` |
| `puffin_*` settings, `DREAMFERENCE_PUFFIN_*`, `PUFFIN_*` variables | `mightling_*`, `DREAMFERENCE_MIGHTLING_*`, `MIGHTLING_*` |
| Network service `_puffin-node._tcp` | `_mightling-node._tcp` |

**Mixed versions on one network:** a node and a client find each other only when both run
Mightling. A client that finds no node says so, and that a node still on Puffin needs
`puffin update` there.

**The desktop app** installs as Mightling and replaces the Puffin package.

## Also new in 1.5.0

**⚠️ One model.** Mightling serves **`qwen3.8-27b-nvfp4-dflash2`** (Qwen3.8-27B in NVFP4 with the DFlash2 drafter, on SGLang) and nothing else. The Qwen 3.5 122B-A10B and Qwen 3.6 35B-A3B models are gone. If your configuration names one of them, `ling-admin server start`, `model download` and `main-model set` stop before doing anything, say so, and give the command that switches: `ling-admin main-model set qwen3.8-27b-nvfp4-dflash2`. The old weights stay in your HuggingFace cache until you delete their folders (`models--Intel--Qwen3.5-122B-A10B-int4-AutoRound`, `models--nvidia--Qwen3.5-122B-A10B-NVFP4`, `models--nvidia--Qwen3.6-35B-A3B-NVFP4`, `models--z-lab--Qwen3.5-122B-A10B-DFlash`).

**Signed releases.** Every file of a release is listed in `SHA256SUMS`, which is signed with Mightling's Ed25519 release key. `ling update` and `install.sh` refuse a release from 1.5.0 on whose signature is missing or does not verify, and every file carries a GitHub build attestation (`gh attestation verify`). See `specs/DREAMFERENCE_RELEASE_SIGNING.md` to check a release by hand.

**A new desktop app.** `ling-app` is rebuilt on Electron, with `ling` inside it, the way the Codex desktop app is built: typing no longer lags on NVIDIA's Linux driver, where the old WebKitGTK window drew on the CPU. It installs as a `.deb` that replaces the Puffin package.

**`/node` inside `ling`.** `/node provision <host>`, `add`, `list`, `status` and `remove` run `ling-admin node …` in your terminal without leaving the session; password and SSH prompts work, and nothing reaches the model. It works at `/airgapped on`, because it talks to your local network only.

**Provisioning a second node.** `ling-admin node provision <host>` installs Mightling on another GB10 over SSH, from this machine's own build or a release (`install.sh --from <dir>`, no internet needed on the other node), applies its host settings and pairs it.

**`ling-search --read`.** One call searches and reads the top pages (3 by default, up to 5) as numbered sources, trimmed to fit the tool-output budget. The launcher also reads the served context length from more model servers (llama.cpp included).

**The web chat's admin password** is generated per install and kept in `~/.config/dreamference/chat-admin.json` (owner-only), instead of a published default. `ling-admin chat configure` moves an existing install over; the desktop app signs in with it.

**Security fixes from the October review:** a node advertising a remembered id from a new address is never followed silently; paired SSH keys are restricted; untrusted-content tags in mail and Drive text are defused; the Google service refuses state-changing requests from other pages; a GitHub token is sent to the API only. Details: `specs/DREAMFERENCE_SECURITY_REVIEW_2026-10.md`.

**Clients for Intel/AMD Linux and macOS** *(preview)*. `install.sh` installs the client (`ling` and its commands) on x86-64 Linux and on Macs (Apple silicon and Intel), pointed at your GB10 node. `/airgapped on` is enforced on macOS by its sandbox, fixed when `ling` starts. Not yet tried on a real Mac.

**Windows client** *(preview, unsigned; Arm64 and x86-64)*. `install.ps1` installs `ling` as a client of a GB10 node, sets up Windows' elevated sandbox, and `/airgapped on` takes the network away from sandboxed commands. Built and tested on GitHub's Windows machines, not yet on a laptop. Known gap: inside the sandbox, Windows' own TLS (curl.exe, PowerShell's web commands) cannot make https requests.

**Refine mode** *(off by default)*. `ling --refine` first runs a session that studies the task and writes a refined description without changing anything, then a fresh session that does the work. It also applies to `ling exec` and Night Shift (`[night] refine`). It solved 20 of 24 SWE-bench tasks against 16 in default mode on this machine, at about three times the time; a 100-task run decides whether it becomes the default.

**The code index can use SCIP alone** (`MIGHTLING_CODE_LAYERS=exact`): exact answers only, text search for files no index covers.

**A website,** at mightling.dreamference.ai, with the one-line install.

---
