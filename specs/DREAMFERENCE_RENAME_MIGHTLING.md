# Renaming Puffin to Mightling

**Status:** decided by the user on 2026-10-07; built on branch `rename/mightling`. The product
"Puffin by Dreamference" becomes **Mightling by Dreamference**, because an existing AI assistant
sells under the name Puffin (puffin.bot) and the name could not be cleared for software. The
repository is already `github.com/dreamference/mightling`; its earlier names
(`dgxcoder/dgxcoder`, `dreamference/puffin`, `dreamference/puffin-ai`, `dreamference/dgx-lunny`)
redirect.

Dreamference stays the company: the `dreamference` Python package, the `DREAMFERENCE_` prefix of
environment variables and `dreamference.toml` keep their names.

## 1. Rules

1. **Everything a person sees or types says Mightling** (or `mling`, the command).
2. **No aliases.** The user ruled that old spellings stop working: there is no `puffin`,
   `puffin-admin`, `puffin-search`, `puffin-fetch`, `puffin-code` or `puffin-app` command after the
   rename, no `puffin_*` configuration key and no `DREAMFERENCE_PUFFIN_*` variable.
3. **One transition path, which is not an alias.** Installations of 1.4.x update themselves with
   `puffin update`, which downloads assets by name from the repository (now redirected). The first
   Mightling release therefore also publishes those names, carrying the new binaries, and the new
   binary migrates an old installation once, the first time it runs (§4).
4. **Data that already exists on users' machines under an old internal name is migrated or left
   alone, never silently broken** (§3).

## 2. Inventory and decisions

| Item | Was | Becomes | Note |
|---|---|---|---|
| The agent | `puffin` | **`mling`** | Cargo still builds `codex`; the builder installs it as `mling` |
| Web commands | `puffin-search`, `puffin-fetch` | `mling-search`, `mling-fetch` | |
| Code index router | `puffin-code` | `mling-code` | its MCP server becomes `mling_code` |
| Desktop app | `puffin-app`, id `dev.dreamference.puffin`, product "Puffin" | `mling-app`, id `dev.dreamference.mightling`, product "Mightling" | the webview data dir is moved once (§4.3) |
| Admin CLI | `puffin-admin` | **`mling-admin`** | the wheel's only console script |
| Its web-chat group | `puffin-admin puffin …` (alias `onyx`) | `mling-admin chat …` (alias `onyx`, which predates the rename and stays) | `mling-admin mightling start` would read as nonsense |
| Code Mode host | `codex-code-mode-host` | unchanged | internal: Codex finds it by this name next to its executable |
| Install dir | `~/.local/share/dreamference/puffin` | `~/.local/share/dreamference/mightling` | |
| PATH links | `~/.local/bin/puffin*` | `~/.local/bin/mling*` | old links removed by the migration |
| Agent home (`CODEX_HOME`) | `~/.puffin` | `~/.mightling` | an explicit `CODEX_HOME` is still respected |
| Config keys | `puffin_airgapped`, `puffin_gmail`, `puffin_prompt`, `puffin_cave_mode`, `puffin_mask`, … | `mightling_*` | rewritten in place by the migration |
| Variables | `DREAMFERENCE_PUFFIN_*`, `PUFFIN_NODE`, `PUFFIN_RELEASE_REPO`, `PUFFIN_VERSION`, `PUFFIN_CODE_*`, `PUFFIN_INSTALL_DIR`, `PUFFIN_VENV` | `DREAMFERENCE_MIGHTLING_*`, `MIGHTLING_*` | read only under the new names |
| systemd units | `puffin-night.{service,timer}`, `puffin-index.slice`, `puffin-job-*`, … | `mightling-*` | replaced by `mling-admin` on a node (§4.2) |
| mDNS service | `_puffin-node._tcp`, Avahi file `puffin-node.service` | `_mightling-node._tcp`, `mightling-node.service` | §5 |
| Pairing key | `~/.ssh/puffin-node_ed25519`, `authorized_keys` marker `puffin-node` | `mightling-node_ed25519`, marker `mightling-node` | renamed and rewritten by the migration |
| Web chat assistant | persona "Puffin" | persona "Mightling" | the existing persona is renamed on the next `chat configure` |
| Prompt identity | "You are Puffin…" | "You are Mightling…" | compiled prompts and `help.rs` rebranding |
| Patch strings | 0001's "Puffin" names | "Mightling" | patches regenerated; see §6 |
| Crates and folders | `puffin-rs/`, `puffin-web-rs/`, `puffin-code-rs/`, crates `puffin-launcher`, `puffin-airgapped`, … | `mling-rs/`, `mling-web-rs/`, `mling-code-rs/`, crates `mling-*` | the repository is public, so folder names are seen; `mling` keeps the patches' added lines no longer than before |
| Release assets | `puffin-<target>.gz`, `puffin-search-…`, `puffin-fetch-…`, `puffin-code-…`, `puffin-<target>.sha256sums` | `mling-…` equivalents, **plus** the old names carrying the new binaries (§4.1) | the old names are dropped in a later release, announced in its notes |
| Specs | `DREAMFERENCE_PUFFIN_*.md` | `DREAMFERENCE_MIGHTLING_*.md` | prose renamed; release notes of 1.4.x stay as history |
| Logos | `images/puffin-*.svg`, `docs/assets/puffin.svg`, the hero card | `mightling-*`, the same bird with the word MIGHTLING | outlined copies for the README |

**Kept on purpose (internal, persisted, never shown as a name):**
- **Build caches** `~/.cache/dreamference/puffin-codex`, `puffin-web`, `puffin-code-build`: renaming them discards hours of compiled dependencies.
- **The web chat's image route** `/puffin-images/` and its nginx markers: chat history already stores URLs under that path.
- **SWE-bench's stored JSON keys** (`puffin_version`, `puffin_code_calls`): earlier runs are read by the same report code.
- **The AppArmor profile** `/etc/apparmor.d/puffin-bwrap`: root installed it once on every node, and a new name would make each `mling-admin` run ask for sudo to fix a sandbox that works.
- **The SCIP stores' own tables** (`puffin_names`, `puffin_relationships`, `puffin_meta`, and the schema filter that skips them): every index already built carries them, and a rename would make each unreadable until rebuilt (minutes to an hour per repository).

Found while testing the rename: the desktop runner's list of earlier launcher names, and each installed skill's `.puffin-origin.toml` (renamed by the migration, §4.2), hold old names on purpose.

## 3. What exists on a 1.4.x machine

- **A release install** (from `install.sh`):
  - binaries in `~/.local/share/dreamference/puffin/bin`;
  - links `~/.local/bin/{puffin,puffin-search,puffin-fetch,puffin-code}`;
  - on a node, a virtualenv `~/.local/share/dreamference/venv` with `puffin-admin`, linked as `~/.local/bin/puffin-admin`.
- **A checkout install:** the same binaries and links made by `puffin-admin codex build`, and `puffin-admin` in the checkout's `.venv`.
- **Everywhere:**
  - `~/.puffin` (sessions, config, skills, Night Shift queue, `puffin-code.toml`);
  - `dreamference.toml` files with `puffin_*` keys.
- **On a node:**
  - the Night Shift timer;
  - the Avahi service file;
  - `authorized_keys` lines with the `puffin-node` marker whose forced command is `…/puffin-admin node serve-job`;
  - the desktop app's webview data under `~/.local/share/dev.dreamference.puffin`.

## 4. The transition

### 4.1 Updating a 1.4.x install

1. **The old updater runs first:** `puffin update` (1.4.x) downloads `puffin-<target>.gz`, `codex-code-mode-host-<target>.gz` and, when present, `puffin-search`, `puffin-fetch` and `puffin-code`, checked against `puffin-<target>.sha256sums`. The first Mightling release publishes exactly those names, built from the same binaries as the `mling-*` assets, with a second checksum file listing the old names.
2. **The next run migrates:** the next time the user types `puffin`, the binary that runs is Mightling, and it migrates (§4.2) before doing anything else.
3. **The Python side follows:** the old virtualenv still holds `puffin-admin`. Running `install.sh` again (the migration says so) replaces the wheel, whose only console script is `mling-admin`. pip removes `puffin-admin` with the old distribution's files.

### 4.2 The one-time migration

Mightling runs it whenever an old layout is present (the old install dir, `~/.puffin`, old links, old units). Each step is idempotent, so a second run finds nothing to do.

**The launcher** (`mling-rs/src/rename.rs`), on every machine:
1. **Moves `~/.local/share/dreamference/puffin`** to `…/mightling` when the new one does not exist, and renames the binaries inside it: `puffin` → `mling`, `puffin-search` → `mling-search`, and so on.
2. **Creates the home.** If `~/.mightling` does not exist and `~/.puffin` does, it copies the same allow-list the first Codex migration used (`home.rs`), never `auth.json`, `installation_id` or the logs. Then it rewrites `/.puffin/` paths in the copied `config.toml`, renames `puffin-code.toml` to `mling-code.toml`, and leaves `~/.puffin` in place with a marker file naming the new home. It rewrites nothing in the old home, so the user can return to it by hand.
3. **Rewrites configuration keys** in `~/.config/dreamference/config.toml`, and in `dreamference.toml` in the current directory: `puffin_<key>` becomes `mightling_<key>`, on whole-line key matches only.
4. **Relinks.** In `~/.local/bin`, it removes `puffin`, `puffin-search`, `puffin-fetch`, `puffin-code`, `puffin-app` and `puffin-admin` **only when they are links** (a real file of that name belongs to someone else). It creates `mling`, `mling-search`, `mling-fetch` and `mling-code` pointing into the new install dir. When the old `puffin-admin` link pointed into a virtualenv that already has `mling-admin`, it links that too.
5. **Renames the pairing key** `~/.ssh/puffin-node_ed25519` (and `.pub`) when the new name is free.
6. **Prints once:** "Puffin is now Mightling: run `mling`", plus "update `mling-admin` with install.sh" when step 4 found no `mling-admin`.

**`mling-admin`** (`dreamference/rename_migration.py`), on a machine with the Python side:
1. **The same key rewrite** of the configuration files.
2. **Replaces the old Night Shift units** (stops and removes the old ones, then reinstalls under the new names when the old timer was enabled). Also any other `puffin-*` user units it installed.
3. **Replaces the Avahi service file** when it can write it, and otherwise prints the one `sudo` line that does.
4. **Rewrites the `authorized_keys` lines** carrying the old marker: the forced command's `puffin-admin` becomes the `mling-admin` beside it, and the marker becomes `mightling-node`.
5. **Moves the desktop app's data dir** when the new one does not exist.

### 4.3 What the user sees

On a client after `puffin update`, the next `puffin` prints the notice and runs the session. From then on `mling` is the command and `puffin` is "command not found". On a node, the first `mling-admin` command does the node-side steps and says what it did.

## 5. Mixed versions on one network

- **A Mightling client browses `_mightling-node._tcp` and never sees a node still running Puffin 1.4.x;** a 1.4.x client likewise never sees a Mightling node. A remembered node (`node.json`) is looked up by id through the same browse, so it is not found either.
- **When a Mightling client finds no node, its message says so:** a node still running Puffin needs `puffin update` there.
- **Pairing survives the rename:** the node's `authorized_keys` lines are rewritten by `mling-admin` on the node, and the sender's key file is renamed on the sender.

This is the no-alias choice: one release of advertising both service types would be an alias. Version skew lasts only until each machine runs its updater once.

## 6. Patches

The patches' added lines name the launcher's crates (`puffin_launcher::…`, `puffin-airgapped`, …) and the product name. The rename substitutes those names in the patch files, which is safe because no upstream line contains them. It checks that the whole series still applies with `git apply --check` in a fresh export. `mling_` is shorter than `puffin_`, and "Mightling" is longer than "Puffin"; the cap in `test_the_patches_stay_small` moves only if the total grows, with a dated line, as for every other change.

## 7. Verification

- **The Python suite.**
- **The launcher's tests in its own export**, including the migration tests:
  - a scratch HOME holding a 1.4.x layout migrates;
  - a second run changes nothing;
  - `auth.json` never reaches `~/.mightling`;
  - links that are real files are left alone.
- **The web and code crates' tests.**
- **A full build of `mling`** in a separate export and target directory.
- **A migration dry run** of the built binary in a scratch HOME.

## 8. Branches started before the rename

**The problem.** Work branched from `main` before the rename (the Windows client, the x86 and macOS
clients, the SCIP-only index, the refine-then-fix benchmark, the validation and names-stripped
runs) still says Puffin. `scripts/rename_mightling.py` is the rename itself, kept for exactly this.

**Merge those branches into `main` first, as they are,** then bring `main` into
`rename/mightling`:

```bash
git checkout rename/mightling
git merge main                      # git follows the renamed folders; conflicts are mostly names
# resolve each conflict by taking the incoming change, then rename what the merge brought in:
.venv/bin/python scripts/rename_mightling.py --paths $(git diff --name-only ORIG_HEAD HEAD)
git grep -n -i puffin -- ':!codex' ':!specs/DREAMFERENCE_RELEASE_1.4.*'   # read every hit
```

**What the script does and does not touch:**
- **A run over a file it already renamed changes nothing.** That's checked with `--dry` over the whole tree.
- **Lines about the rename itself are left alone,** as are the kept internal names (§2) and the files whose job is to know the old names:
  - the migration;
  - the transition assets in `release.yml` and `install.sh`;
  - this spec and the release notes.
- **New `puffin_*` keys or `PUFFIN_*` variables** an incoming branch adds become `mightling_*` / `MIGHTLING_*`. New crates become `mling-*`.

**Then run all of §7 again.**
