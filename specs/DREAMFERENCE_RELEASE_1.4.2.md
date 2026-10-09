# Puffin 1.4.2 — release notes

*Never released: 1.4.2 was prepared on 2026-10-07 and superseded the same day; everything below shipped in 1.5.1 (2026-10-08), the first Mightling release (1.5.0 was built but never published). Kept as the record of that preparation.*

**Status:** draft, 2026-10-07, on branch `release/1.4.2`, from `git log v1.4.1..main` (main at `4de1174`). Not built, tagged or published. The text between the two rules is the GitHub release's description; the sections marked *(if merged)* are added or dropped by the checklist at the end.

**Version.** setup.py, `dreamference.__version__`, the MCP server's `serverInfo`, `tauri.conf.json` and `puffin-app`'s Cargo manifest and lock say `1.4.2`; the release workflow stamps the version it is given into setup.py, `tauri.conf.json` and the `puffin` binary in its own checkout.

**Upgrade path, checked 2026-10-07.**
- A real 1.4.1 client install in a scratch home: its `install.sh` names `dreamference/puffin`, fetched through that old address, installed v1.4.1. The installed binary's `puffin update`, whose `RELEASE_REPO` is still `dreamference/puffin`, reached the release API through GitHub's redirect and answered "Puffin 1.4.1 is the latest release".
- `api.github.com/repos/{dreamference/puffin, dgxcoder/dgxcoder}/releases/latest` both answer 301 to `repositories/1323116878`, which serves the latest published release, with asset URLs under `dreamference/puffin-ai`; `releases/latest/download/install.sh` under both old names redirects to `puffin-ai`.
- `decide("1.4.1", "1.4.2")` installs (semver). So once 1.4.2 is published as Latest, `puffin update` on 1.4.0 and 1.4.1 finds it, provided the asset names stay as below.

---

Puffin now serves one model, Qwen3.8-27B, and lives at **github.com/dreamference/puffin-ai**.

**Install** on a GB10 (DGX OS 7, or Ubuntu 24.04 with Docker and the NVIDIA Container Toolkit):

```bash
curl -fsSL https://github.com/dreamference/puffin-ai/releases/latest/download/install.sh | bash
```

Already on 1.4.0 or 1.4.1: `puffin update`, and `pip install -U` of the wheel for `puffin-admin`.

## ⚠️ Breaking: one model

Puffin serves **`qwen3.8-27b-nvfp4-dflash2`** (Qwen3.8-27B in NVFP4 with the DFlash2 drafter, on SGLang) and nothing else. The Qwen 3.5 122B-A10B models (`qwen3.5-122b-a10b-hybrid-dflash`, `qwen3.5-122b-a10b-int4-dflash`, `qwen3.5-122b-a10b-nvfp4`), the Qwen 3.6 35B-A3B (`qwen3.6-35b-a3b-nvfp4`) and the 122B drafter are removed, with the Dockerfiles that built their vLLM images.

If your configuration names one of them, `puffin-admin server start`, `model download` and `main-model set` stop before doing anything and say so:

```text
❌ 'qwen3.5-122b-a10b-hybrid-dflash' was removed in Puffin 1.4.2 (it is the model in your Puffin configuration): Puffin serves one model, qwen3.8-27b-nvfp4-dflash2.
   Switch to it: puffin-admin main-model set qwen3.8-27b-nvfp4-dflash2
```

Run that command once. The old weights stay in your HuggingFace cache. To free the space, delete these folders from its `hub/` directory:
- `models--Intel--Qwen3.5-122B-A10B-int4-AutoRound`
- `models--nvidia--Qwen3.5-122B-A10B-NVFP4`
- `models--nvidia--Qwen3.6-35B-A3B-NVFP4`
- `models--z-lab--Qwen3.5-122B-A10B-DFlash`

Don't use `puffin-admin clear model-cache` for this: it removes every model, the one Puffin serves included.

## The repository moved

Puffin is now **github.com/dreamference/puffin-ai**. The old addresses, `github.com/dgxcoder/dgxcoder` and `github.com/dreamference/puffin`, redirect there, so links, clones and `puffin update` on 1.4.0 and 1.4.1 keep working.

## A new front page

- The README and the documentation site lead with what Puffin is for: your code stays on your desk, it is fast with no rate limits or bill, and there is nothing to configure. The install is one line, and there are first prompts to try.
- `puffin-admin audit egress` is shown as the proof: a real session traced, every connection on `127.0.0.1`.
- New Puffin and Dreamference logos, light and dark.
- A Sponsor button.

## Windows on Arm: the plan

The Windows on Arm spec (`specs/DREAMFERENCE_PUFFIN_WINDOWS_ARM.md`) now records these decisions:
- a native client;
- the model served by llama.cpp, with no WSL;
- Qwen3.8-27B wherever it fits;
- unsigned preview builds for Arm and x86-64.

Nothing for Windows is in this release.

## *(if merged: the cleanup branch)* Leftovers of the fork removed

<!-- Describe what the branch changed that a user sees: `puffin --version`, `puffin prompt list`'s wording, and so on. Keep the asset names unchanged (see the checklist). -->

## *(if merged: `clients/x86-macos`)* Clients for Intel/AMD Linux and macOS

<!-- Which targets the release now carries, what install.sh does on each, and that they are clients of a GB10 node. -->

## *(if merged: `windows/phase-0-1`)* Windows client preview

<!-- The Windows client for Arm64 and x86-64: **preview and unsigned**. Smart App Control must be off to run it, and it is a client of a GB10 node on the network. Name install.ps1 and what is not yet verified on real hardware. -->

## *(if merged: `code-index/scip-only`)* The code index can use SCIP alone

<!-- `PUFFIN_CODE_LAYERS=exact` (default `all`), what it gives up, and the benchmark result if it is in. -->

## *(if merged: `bench/validate-strip`)* Benchmark tooling

<!-- Only if it changes something a user runs (`puffin-admin swe-bench ...`); otherwise leave it out of the description. -->

---

## Checklist before publishing

1. **Merge the branches that are ready, onto main:** `cleanup/no-openai`, `clients/x86-macos`, `windows/phase-0-1`, `code-index/scip-only` and `bench/validate-strip`, each after its own tests pass. Then rebase `release/1.4.2` onto main and merge it last, so the version bump is the final commit. For each branch merged, fill in its *(if merged)* section above, dropping the italic marker; for each not merged, delete its section.
2. **Keep the update asset names.** A 1.4.0 or 1.4.1 `puffin update` refuses a release without exactly:
   - `puffin-<target>.gz`
   - `codex-code-mode-host-<target>.gz`
   - `puffin-<target>.sha256sums`

   If `cleanup/no-openai` renames `codex-code-mode-host`, the release must still ship an asset under the old name, or older installs cannot update. Codex also looks for the helper next to its executable under that name.
3. **New targets don't break the old updater.** If `clients/x86-macos` or `windows/phase-0-1` adds targets, they only add assets; the aarch64 Linux names above stay.
4. **Windows stays preview.** If `windows/phase-0-1` is merged with its release jobs, the Windows assets are unsigned. The notes say Smart App Control must be off, as decided on 2026-10-07.
5. **Dispatch and check:** `gh workflow run release.yml -R dreamference/puffin-ai --ref main -f version=1.4.2 -f draft=true -f prerelease=false`. Every job must pass (validate, test, python-dist, puffin, desktop, release). The draft must carry the three update assets, the web commands, `puffin-code`, the wheel and sdist, `install.sh`, the `.deb` and AppImage, and Codex's LICENSE and NOTICE.
6. **Publish:** `gh release edit v1.4.2 -R dreamference/puffin-ai --notes-file <the text between the rules> --draft=false --latest`. Then confirm `releases/latest/download/install.sh` answers 200 under the new address and both old ones.
7. **Set this file's status line** to published, with the run number.
