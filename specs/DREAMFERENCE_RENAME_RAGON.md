# Renaming Mightling to Ragon

**Status:** Proposed (2026-10-28). The owner decided the rename on 2026-10-28 and answered most questions the same day (§0). Spec only: nothing is renamed yet. The rename is built now as a script on a branch (§9) and runs the day after 1.8.1 ships (decided 2026-10-29; 1.8.1 targets 2026-11-02, so about 2026-11-03; earlier plan: 2026-11-18). It ships as **1.9.0**, and 2.0 starts as Ragon.

"Mightling by Dreamference" becomes **Ragon by Dreamference**. Dreamference stays the company: the `dreamference` Python package, the `DREAMFERENCE_` prefix of environment variables, `dreamference.toml`, `~/.config/dreamference/` and the `dreamference` GitHub organisation keep their names.

The template is [DREAMFERENCE_RENAME_MIGHTLING.md](./DREAMFERENCE_RENAME_MIGHTLING.md), the Puffin → Mightling rename of 2026-10-07. Its structure, its migration and its three lessons carry over:
- only an installed binary migrates;
- a paired node's pinned host key moves with its alias;
- the code index's tools must move with the install folder.

Two things are new this time. The rename covers three client operating systems, the forge, remote access, the messengers, the engine repository and its image. And this time the old mDNS names are advertised for one release (§0, decision 16).

---

## 0. Decided by the owner (2026-10-28)

| # | Decision |
|---|---|
| 1 | The rename ships as **1.9.0**, a release that does only the rename and the migration, the day after 1.8.1 ships, if 1.8.1 needs no hotfix in its first day (decided 2026-10-29; about 2026-11-03; it was 2026-11-18 before). 2.0 starts as Ragon. |
| 2 | **Clean break plus a one-time migration**, as in the Puffin rename. There are no command aliases: `ling`, `ling-admin` and the other old commands disappear. |
| 3 | **Both repositories are renamed**, relying on GitHub's redirects: `dreamference/mightling` → `dreamference/ragon` and `dreamference/ling-engine` → `dreamference/ragon-engine`. The `ling-serve` image becomes `ragon-serve`. |
| 4 | The website moves to **`ragon.dreamference.ai`**, with a permanent redirect from `mightling.dreamference.ai`. |
| 5 | **Spelling:** the product is "Ragon"; commands are lowercase (`ragon`, `ragon-admin`, `ragon-search`, …). |
| 6 | **The company stays Dreamference:** the package `dreamference`, the `DREAMFERENCE_*` variables and the GitHub organisation keep their names. |
| 7, 15 | **Phone bots** get the new display name "Ragon". Telegram usernames stay, and nothing needs re-pairing. |
| 8, 19 | **Name check:** go ahead unless the check finds something serious (a software trademark, or a command that clashes on common systems). Its depth: the web, package registries (PyPI, crates.io, npm, Homebrew, apt), GitHub and command names, with no paid trademark search. The result is in §10: nothing serious. |
| 9, 22 | **The logo keeps the puffin.** Only the wordmark becomes RAGON. The story is "Ragon is a puffin that thinks it is a dragon": one line plus that tagline under the unchanged mark, on the website and in the README, with no illustration. |
| 10, 17 | **The desktop app's identity changes:** app id, Windows AppUserModelID and macOS bundle id. The 1.9.0 update installs Ragon automatically, moves the app's data, removes the Mightling app and its shortcuts, and recreates the Start menu and desktop shortcuts. The release notes ask users to re-pin once. |
| 11 | **The public note:** the 1.9.0 release notes, plus a short "Mightling is now Ragon" line on the website and in the README. Nothing elsewhere. |
| 12 | **One night off.** No night runs on 1.9.0's install day. All nodes migrate by day, and nights resume the next evening under the new names (`ragon-swe-*` and so on). |
| 13, 24 | **Docs and specs:** only text that describes current behaviour is renamed (user docs, dev docs, and the current parts of specs). History and dated decisions keep the old names, and each spec gets a one-line note at the top: "Mightling was renamed Ragon in 1.9.0." Spec files are renamed (`DREAMFERENCE_MIGHTLING_*.md` → `DREAMFERENCE_RAGON_*.md`, `DREAMFERENCE_LING_ENGINE_*.md` → `DREAMFERENCE_RAGON_ENGINE_*.md`), and the script updates every link. Git keeps the history. |
| 14 | **The paper** gets a new version as Ragon with the footnote "formerly Mightling", and the website's PDF is rebuilt. |
| 16 | **Mixed versions:** 1.9.x nodes advertise both `_ragon-node._tcp` and `_mightling-node._tcp`, and both forge types, for one release, so 1.8 clients still find them. The old types are dropped in 2.0. This bridges at the network level; it is not a command alias. |
| 18 | **Phone links** (the Signal device link, Telegram pairing, the `ling test` harness devices) are kept by the migration, which renames device names to "ragon …" where the platform allows. Nothing is re-scanned. |
| 20 | **Timing:** the rename is built now as an automated, repeatable, tested script on a branch that can be re-run on a fresh `main` at any time (§9). It runs in one go after 1.x is complete, and 1.9.0 ships within days. |
| 21 | **One command name, `ragon`,** with no short alias. |
| 23 | **The Puffin-era leftovers are cleaned up in 1.9.0 too.** The `puffin-*` release assets become `ragon-*`, and the build cache `puffin-codex` moves. During the migration the updater handles both old asset sets (`puffin-*` and `ling-*`) once (§4.1). |
| 25 | **On GitHub:** the organisation profile README, the repositories' descriptions and topics, and the release names say Ragon. |

---

## 1. Rules

1. **Everything a person sees or types says Ragon** (or `ragon`, the command). Every utility with "ling" in its name gets "ragon" (§2).
2. **No aliases** (decision 2). After 1.9.0 there is no `ling`, `ling-admin`, `ling-search`, `ling-fetch`, `ling-code`, `ling-docs` or `ling-app`, and no `mightling_*` configuration key or `MIGHTLING_*` / `DREAMFERENCE_MIGHTLING_*` variable. Old keys and variables are read only by the migration, which rewrites them.
3. **Two transition paths, neither an alias:**
   - **Asset names.** 1.9.0 also publishes the asset names the 1.8.x and 1.4.x updaters download, carrying the new binaries, so `ling update` and `puffin update` land on Ragon (§4.1). Those names are not published again after 1.9.x.
   - **mDNS.** For one release, 1.9.x nodes advertise both service types (decision 16).
4. **Data that already exists on a machine under an old name is migrated, or kept on purpose and listed in §3. It is never silently broken.**
5. **The three lessons are rules, not footnotes:**
   - only an installed binary migrates (§4.3);
   - every pinned host-key alias moves;
   - the code index's tools move with the install folder (§4.2, steps L1 and A0).

The command is **`ragon`**: five letters, the product's own name. It collides with nothing on Linux, macOS, Windows, Ubuntu's archive, Homebrew, winget, Chocolatey, the AUR, PyPI or crates.io (§10). On npm the name holds an unpublished stub, which does not matter because nothing of Ragon's is published on npm. The crates and source folders follow the command (`ragon-rs/`, `ragon-web-rs/`, `ragon-code-rs/`, `ragon-docs-rs/`, crates `ragon-*`), so binaries, crates and folders share one prefix, as they did with `ling`.

## 2. Inventory and decisions

Measured on `main` at `989e3e9` (2026-10-28), outside `codex/` and `ling-engine/`:
- "mightling" (any case): 9,669 occurrences in 1,074 files.
- "ling" as a word or name part: about 15,400 occurrences in 882 files.
- "puffin": 791 occurrences.

The script (§9) works from patterns, not from this table. The table records the decisions.

### 2.1 Commands, binaries, crates, packages

| Item | Was | Becomes | Note |
|---|---|---|---|
| The agent | `ling` | **`ragon`** | Cargo still builds `codex`; the builder installs it as `ragon` |
| Admin CLI | `ling-admin` | `ragon-admin` | the wheel's only console script; groups unchanged (`ragon-admin chat`, `node`, `night`, `matrix`, `forge`, `remote`, …) |
| Web commands | `ling-search`, `ling-fetch` | `ragon-search`, `ragon-fetch` | `WebTools`, the MCP server's copy, follows them |
| Code index | `ling-code` | `ragon-code` | MCP server `ling_code` → `ragon_code` (the model sees new tool-server names) |
| Local file index | `ling-docs` | `ragon-docs` | MCP server `ling_docs` → `ragon_docs` |
| Signal daemon | `ling-signal` | `ragon-signal` | beside `ragon`, never on PATH |
| Delegation and monitor | `ling-delegate`, `ling-monitor` | `ragon-delegate`, `ragon-monitor` | also `ling delegate mcp` → `ragon delegate mcp` |
| Model server | `ling-serve` (engine `ling`) | `ragon-serve` (engine `ragon`) | `server start --engine ragon`, `engine_recipes['ragon']`, `SERVER_ENGINES = ("ragon", "sglang", "vllm")` |
| Desktop launcher (Linux) | `ling-app`, `ling-app.desktop`, `ling-app.png` | `ragon-app` … | |
| Code Mode host | `codex-code-mode-host` | unchanged | Codex finds it by this name |
| Launcher subcommands | `ling web`, `ling chat`, `ling signal`, `ling sync`, `ling node`, `ling night`, `ling apps`, `ling skills`, `ling docs`, `ling prompt`, `ling update`, `ling uninstall`, … | `ragon web`, `ragon chat`, … | the subcommand words carry no product name and stay |
| Crates | `ling-launcher`, `ling-airgapped`, `ling-apps`, `ling-chat`, `ling-delegate`, `ling-loop-guard`, `ling-masking`, `ling-monitor`, `ling-node-locator`, `ling-safegit`, `ling-signal`, `ling-skills`, `ling-sync`, `ling-tools`, `ling-web-server`, `ling-web`, `ling-code`, `ling-docs` | `ragon-*` (libs `ragon_*`) | |
| Source folders | `ling-rs/`, `ling-web-rs/`, `ling-code-rs/`, `ling-docs-rs/`, `codex-rs/ling` in the export | `ragon-rs/`, `ragon-web-rs/`, `ragon-code-rs/`, `ragon-docs-rs/`, `codex-rs/ragon` | the byte-identical pairs (air-gap resolver, node locator) are renamed together, and their tests compare them |
| Python | `LingServeLaunchBuilder` (`ling_serve_launch_builder.py`), `LingAgent` (terminal-bench), `test_mightling_*.py`, `test_ling_serve_engine.py`, `scripts/package_mightling.sh`, constants `DEFAULT_MIGHTLING_*`, `MIGHTLING_*_LEVELS`, … | `RagonServeLaunchBuilder`, `RagonAgent`, `test_ragon_*.py`, `package_ragon.sh`, `DEFAULT_RAGON_*` | the package `dreamference` stays (decision 6); one class per file, so files and classes rename together |
| Rust constants | `MIGHTLING_VERSION`, `MIGHTLING_HOME_DIR`, `DEFAULT_MIGHTLING_*`, `LING_ENGINE_OWNER`, … | `RAGON_*` | |
| npm packages (private) | `mightling-desktop`, `mightling-work` | `ragon-desktop`, `ragon-work` | never published |
| Claude Code plugin | plugin `mightling`, marketplace `mightling` (`.claude-plugin/`, `claude-plugin/`) | `ragon`, `ragon` | an installed copy is reinstalled once (`/plugin install ragon@ragon`); the release notes say so |
| Skills shipped | `ling-delegate` skill name where used | `ragon-delegate` | |
| User agents | `ling/<version>` (updater), `mightling-skill` | `ragon/<version>`, `ragon-skill` | `ragon-search`'s browser user agent carries no product name |

### 2.2 Configuration, variables, paths

| Item | Was | Becomes | Migration |
|---|---|---|---|
| Config keys | `mightling_airgapped`, `mightling_gmail`, `mightling_prompt`, ~~`mightling_cave_mode`~~ (removed 2026-10-28, `ef443182`), `mightling_refine`, `mightling_refine_version`, `mightling_compaction_ledger`, `mightling_compat`, `mightling_claude_md`, … | `ragon_*` | rewritten in place, on whole-line key matches only, as last time |
| Config **values** | `server_engine = "ling"`, `engine_recipes` key `ling` | `"ragon"` | **New this time:** the Puffin migration rewrote keys only. The migration also rewrites this value, and the config reader refuses `ling` with a message naming `ragon` |
| Variables | `DREAMFERENCE_MIGHTLING_*` (about 50 live ones, e.g. `…_AIRGAPPED`, `…_PROMPT`, `…_DOCS_*`, `…_COMPAT`) | `DREAMFERENCE_RAGON_*` | read only under the new names. The release notes list them, since a user's shell profile cannot be migrated |
| Variables | `MIGHTLING_*` (`MIGHTLING_RELEASE_REPO`, `MIGHTLING_INSTALL_DIR`, `MIGHTLING_NODE`, `MIGHTLING_CODE_*`, `MIGHTLING_DOCS_*`, `MIGHTLING_LEGACY_MIGRATION`, …) | `RAGON_*` | as above. Unit files and scripts that set them are rewritten by the migration |
| Variables | `LING_*` (`LING_SERVE_PROGRAM`, `LING_ENGINE_OWNER`, `LING_WEB_UI_DIST`, `LING_PIN_SHA256`, `LING_RUNTIME_DIR`, …) | `RAGON_*` | mostly build-time and engine-side; renamed in both repositories together |
| Agent home (`CODEX_HOME`) | `~/.mightling` | `~/.ragon` | §4.2. An explicit `CODEX_HOME` is still respected |
| Files in the home | `ling-code.toml`, `ling-skills.toml`, `.mightling-origin.toml` (per skill) | `ragon-code.toml`, `ragon-skills.toml`, `.ragon-origin.toml` | renamed by the migration |
| Install dir (Linux, macOS) | `~/.local/share/dreamference/mightling/{bin,indexers,build-key,desktop-app}` | `~/.local/share/dreamference/ragon/…` | moved whole, so the code index's tools move too (lesson 3) |
| Install dir (Windows) | `%LOCALAPPDATA%\Programs\Mightling\{bin,App}`, the user `Path` entry | `…\Programs\Ragon\…` | §4.2, Windows arm |
| PATH links | `~/.local/bin/ling*` | `~/.local/bin/ragon*` | old links removed only when they are links |
| Codex test skips | `codex-tests/mightling-skips.toml` | `ragon-skips.toml` | |
| Lock files | `~/.ling-gpu.lock`, `~/.ling-gpu-holds.log`, `~/.ling-build.lock` | **kept** (§3) | |

### 2.3 Units, services, containers, network names

| Item | Was | Becomes | Migration |
|---|---|---|---|
| User units | `mightling-night.{service,timer}`, `mightling-web.service`, `mightling-chat.service`, `mightling-sync.service`, `mightling-monitor{,-digest}.{service,timer}`, `mightling-forge*.{service,timer}`, `mightling-forge-mirror.service`, `mightling-matrix-proxy.{socket,service}`, `mightling-remote-{tunnel,control,certificate,certificate-alert}.*`, `mightling-messenger-live*`, `mightling-node.service` | `ragon-*` | stopped, removed and reinstalled under the new names when the old one was enabled (as last time). Runs by day only (decision 12) |
| Slices and scopes | `mightling.slice`, `mightling-index.slice`, `mightling-index-*.scope` | `ragon.slice`, `ragon-index.slice`, … | `live_scopes()` reads both prefixes for 1.9.x, so an index still running under the old slice is counted in the memory budget |
| SWE-bench units | `ling-swe-*` (e.g. `ling-swe-night15.service`) | `ragon-swe-*` | none is running on install day (decision 12). The forge's storage check (`forge_storage.py`) matches both patterns for 1.9.x |
| System units | `/etc/systemd/system/mightling-signal.service` and the system account `mightling-signal` | `ragon-signal.service`, account `ragon-signal` | sudo step (§4.2 A7). `usermod -l` and `groupmod -n` keep the uid, so the Signal state in `/var/lib` and the device link survive (decision 18) |
| mDNS | `_mightling-node._tcp`, `_mightling-forge._tcp`, Avahi file `mightling-node.service` | `_ragon-node._tcp`, `_ragon-forge._tcp`, `ragon-node.service` | the new file holds **both** types for 1.9.x (decision 16); §5 |
| Pairing key | `~/.ssh/mightling-node_ed25519`, `authorized_keys` marker `mightling-node`, forced command `…/ling-admin node serve-job` | `ragon-node_ed25519`, marker `ragon-node`, `ragon-admin` | renamed and rewritten |
| Pinned host keys | alias `mightling-node-<id>` in the pairing's known-hosts | `ragon-node-<id>` | `ssh-keygen -F/-R` (lesson 2) |
| Docker image | `ghcr.io/dreamference/ling-serve` | `ghcr.io/dreamference/ragon-serve` | **GHCR cannot rename a package:** this is a new publish. The old package stays for 1.8.x pins until 2.0 |
| Image labels | `ai.dreamference.ling-engine.commit`, `ai.dreamference.ling-serve.sha` | `ai.dreamference.ragon-engine.*` | readers accept both, since images already pinned by digest carry the old label |
| Containers | `ling-serve` and `ling-serve-scan-*` container names | `ragon-serve…` | the old container is stopped by day, and the engine restarts under the new name |
| Sidecar containers | `dreamference-*` (gate, searxng, image search, stt, gmail, …) | unchanged | company name |
| Forge account | `mightling-phone` | `ragon-phone` | renamed through Forgejo's admin API. The token is bound to the user id and survives, and Forgejo redirects the old name. Commits it already made keep their author |
| AppArmor (desktop) | `/etc/apparmor.d/mightling`, `mightling-unpacked`, `mightling-desktop-dev` | `ragon`, `ragon-unpacked`, `ragon-desktop-dev` | **cannot be kept:** they name the app's path and executable. Regenerated in the sudo step |
| AppArmor (bwrap) | `/etc/apparmor.d/puffin-bwrap` | `ragon-bwrap` | Puffin cleanup (decision 23): the new profile is installed and the old one removed in the same sudo step |
| NetBird names | peers `mightling-<id>.netbird.selfhosted`, setup keys `mightling-node-<date>` | new enrolments `ragon-…`; existing peers **kept** (§3) | |
| Relay box | system user `mightling-relay`, unit `mightling-relay.service`, markers `mightling-box-ready` / `-removed` | **kept** (§3) | |

### 2.4 Desktop app identity (decisions 10 and 17)

| Item | Was | Becomes |
|---|---|---|
| Product name | Mightling | Ragon |
| App id, Windows AppUserModelID, macOS bundle id | `dev.dreamference.mightling` | `dev.dreamference.ragon` |
| Data folder | `~/.local/share/dev.dreamference.mightling` (Linux), `%LOCALAPPDATA%\dev.dreamference.mightling` (Windows), the per-identifier folder on macOS | `…dev.dreamference.ragon` |
| Windows | `Mightling.exe`, `Uninstall Mightling.exe`, HKCU uninstall key, `Mightling.lnk` (Start menu, desktop, pins), `Mightling-Setup-<v>-<arch>.exe` | `Ragon.exe`, …, `Ragon-Setup-<v>-<arch>.exe` |
| macOS | `Mightling.app`, `Mightling-<v>-<arch>-preview.dmg` | `Ragon.app`, `Ragon-<v>-<arch>-preview.dmg` |
| Linux | `.deb` package and `ling-app` | `ragon` app package with `Conflicts/Replaces` on the old one; `ragon-app` |

**What a user loses once, stated plainly in the release notes:**
- **Windows.** A new AppUserModelID is a new app to Windows. Per-app notification settings (on/off, banners, priority) and the notification history start empty, and a pin to the old app is dead. `app-identity.ts` already rewrites shortcuts whose AppUserModelID is stale (`electron.app.Mightling`). The migration extends it: it recreates the Start menu and desktop shortcuts, and it rewrites a taskbar pin's `.lnk` (target and id) when it finds one. Windows 11 caches pins, so the notes still say "re-pin once" (decision 10).
- **macOS.** A new bundle id is a new app to TCC. Ragon asks again for the microphone (voice), for notifications, and for anything Quick Entry's global shortcut needs. A Dock entry for `Mightling.app` turns into a question mark and is removed by the migration where the Dock's plist allows; otherwise the notes say to drag Ragon in. The app keeps no secret in the keychain (no `safeStorage`), so nothing encrypted is lost.
- **Linux.** Only the `.desktop` entry changes. Favourites in the dock are re-added once.

### 2.5 Repositories, website, docs, paper, specs

| Item | Was | Becomes | Note |
|---|---|---|---|
| Main repository | `github.com/dreamference/mightling` | `github.com/dreamference/ragon` | GitHub redirects the web, git and API (`/repos/…` answers 301 to `/repositories/<id>`). The defaults in `update.rs`, `install.sh`, `install.ps1` and `as_team.py --repo` change. The earlier redirects (`dreamference/puffin`, `puffin-ai`, `dgx-lunny`, `dgxcoder/dgxcoder`) keep working, because GitHub keeps a renamed repository's old names |
| Engine repository | `github.com/dreamference/ling-engine`, submodule path `ling-engine/` | `dreamference/ragon-engine`, submodule `ragon-engine/` | `.gitmodules` URL and path. The engine repository gets the same script run (its `ling-serve`, `LING_*` and specs). `DREAMFERENCE_LING_ENGINE_*.md` become `DREAMFERENCE_RAGON_ENGINE_*.md` (decision 24) |
| GitHub metadata | descriptions, topics, organisation profile README, release names | Ragon (decision 25) | release titles become "Ragon 1.x.y"; the bodies of old release notes stay as written (decision 13) |
| Other repositories | `dreamference/codex` (fork), `dreamference/mighty-crew` | unchanged | the fork carries no product name; Mighty Crew is another product |
| Website | `website/` with `CNAME` `mightling.dreamference.ai` | `ragon.dreamference.ai` | Pages serves one custom domain, so the permanent redirect from the old host is a **Cloudflare redirect rule** on the `dreamference.ai` zone, not a Pages setting. See the DNS finding in §10 |
| Docs site | MkDocs (`site_name: Mightling`) under the website at `/docs/` | Ragon | follows the website's domain |
| Paper | `paper/ling.tex` and its PDF, `paper/desktop-architecture.tex`, `paper/ling-engine.tex` | `paper/ragon.tex` with the footnote "formerly Mightling"; the others renamed in text; PDFs rebuilt | decision 14. `paper/puffin.*` is history and stays |
| Logos | `images/mightling-*.svg`, `website/assets/mightling-logo-*.svg`, the hero card | `ragon-*`: the same puffin, wordmark RAGON | decision 9 |
| Specs | 45 `DREAMFERENCE_MIGHTLING_*.md` files and the `ling-engine` specs | `DREAMFERENCE_RAGON_*.md` | current text renamed, history kept, a one-line note at the top (decision 13) |
| `AGENTS.md` | "The product was called Puffin until 1.5" | adds "and Mightling until 1.9" and names this spec | stays under its 24 KB cap |
| `docs/dev/install-modes.md` | "Old release names" | lists the 1.9.0 transition (both old asset sets, then none) | |
| Prompts | "You are Mightling, …" (`ling-rs/prompts/*.md`, `lib.rs`), `help.rs` branding | "You are Ragon, …" | see the benchmark note in §11 |
| Messenger display | Telegram bot display name; Mini App menu button text "Mightling"; Matrix DM room name "Mightling"; hub texts ("Mightling wants to run a command") | "Ragon" | Telegram: the bridge calls `setMyName` and `setChatMenuButton` at its next start. Matrix: one `m.room.name` state event. Usernames stay (decision 15) |
| Harness devices | Signal device names, the `[ling test]` prefix and the `ling test` git identity of the messenger harness | `ragon …` | Signal: `updateAccount --device-name` from the linked device, where signal-cli supports it, or else the name is left. Nothing re-scanned (decision 18) |
| Release signing | SSHSIG namespaces `mightling-release`, `mightling-release-key`, `mightling-adapter` | **kept** (§3) | |

## 3. Kept on purpose

These names are internal, persisted or cryptographic, and never shown to a user as the product's name.

- **The SSHSIG namespaces `mightling-release`, `mightling-release-key` and `mightling-adapter`.** A namespace is part of what is signed. The 1.8.x `ling update` verifies 1.9.0's `SHA256SUMS` under `mightling-release`, so 1.9.0 signed under a new namespace would be refused by every install. Every adapter already published is signed under `mightling-adapter`. The alternative (sign twice in 1.9.x and switch in 2.0) adds a transition for a string nobody reads. Revisit only with a key rotation.
- **The lock files `~/.ling-gpu.lock`, `~/.ling-gpu-holds.log` and `~/.ling-build.lock`.** They are the mutual exclusion between nights, engine work and builds, and the engine repository's scripts and other checkouts use them as well. Two names during a half-migrated window would mean no exclusion at all.
- **NetBird peer names of already-enrolled devices** (`mightling-<id>.netbird.selfhosted`). Phones and the Telegram Mini App hold these names, and nodes are always resolved by id at use time. New enrolments are named `ragon-…`, and existing peers are renamed only when re-enrolled.
- **The relay box's `mightling-relay` user, unit and markers.** The box is shared with services that are not Ragon's. Changing a root-owned account there, for a name no user sees, is risk without gain. Revisit with the box's next rebuild.
- **Stored records:**
  - SWE-bench's run JSON (`puffin_version`, `puffin_code_calls`, and any `mightling_*` keys written since);
  - Night Shift's archived reports.
  The report code reads old and new keys and writes only new ones, because earlier runs must stay comparable.
- **History:**
  - release notes of 1.4.x–1.8.x;
  - dated decisions in specs, and the `Was` columns of this spec and the Puffin one;
  - `paper/puffin.*`;
  - `scripts/rename_mightling.py`;
  - the retired web chat's cleanup (`retired_web_chat.py`, whose job is to find the Onyx leftovers by their old names, `/puffin-images/` included).
- **The migrations themselves:**
  - `ragon-rs/src/rename.rs`;
  - `dreamference/cli/legacy_name_migration.py`;
  - the transition assets in `release.yml`, `install.sh` and `install.ps1`, which must know every old name;
  - the 1.9.x dual mDNS advertisement.
- **The `ling-serve` package on GHCR**, until 2.0, because 1.8.x installs pull it by digest.

**Puffin leftovers cleaned up in 1.9.0** (decision 23):
- The `puffin-*` release assets: 1.9.0 publishes them one last time (§4.1), and then never again.
- The build caches `~/.cache/dreamference/puffin-codex`, `puffin-web` and `puffin-code-build` move to `ragon-codex`, `ragon-web` and `ragon-code-build`. A move on one filesystem is instant. The first build after it may rebuild some crates, because Cargo's dep-info holds absolute paths: one long build at worst, once.
- `puffin-bwrap` is replaced by `ragon-bwrap` (§2.3).
- The SCIP stores' tables (`puffin_names`, `puffin_relationships`, `puffin_meta`) are renamed `ragon_*` with SQLite's `ALTER TABLE … RENAME` by the next indexing run. That is cheap, and it keeps every index readable without a rebuild. Queries open a store read-only, so until that run they read either name. The schema hash's filter skips both names.
- `_puffin-node._tcp`, `dev.dreamference.puffin` and `puffin-node_ed25519` stay only in the migration's list of old layouts.

## 4. The transition

### 4.0 What exists on a 1.8.x machine

- **A release install:**
  - binaries in `~/.local/share/dreamference/mightling/bin`, and the code index's tools beside them (`bin/codebase-memory-mcp`, `bin/scip`, `indexers/`);
  - links `~/.local/bin/{ling,ling-search,ling-fetch,ling-code,ling-docs}`;
  - on a node, the virtualenv `~/.local/share/dreamference/venv` with `ling-admin`, linked as `~/.local/bin/ling-admin`;
  - a `desktop-app` record when `install.sh` or `install.ps1` installed the app.
- **A checkout install:** the same binaries and links made by `ling-admin codex build`, and `ling-admin` in the checkout's `.venv`.
- **Everywhere:**
  - `~/.mightling`, which holds sessions, config, skills, the Night Shift queue and its worktrees, `worktrees/`, `chat-repos/`, `ask/`, `sync/`, `memories/`, `ling-code.toml` and `ling-skills.toml`;
  - `dreamference.toml` files with `mightling_*` keys.
- **On a node:**
  - the user units of §2.3 and the system unit of the Signal bridge;
  - the Avahi file;
  - `authorized_keys` lines with the `mightling-node` marker;
  - pinned host keys under `mightling-node-<id>`;
  - the forge and its `mightling-phone` account;
  - the remote-access units;
  - `ling-serve` images and containers;
  - the desktop AppArmor profiles.
- **Windows:** `%LOCALAPPDATA%\Programs\Mightling\{bin,App}`, the user `Path` entry, the HKCU uninstall key, the shortcuts, and `%LOCALAPPDATA%\dev.dreamference.mightling`.
- **macOS:** `Mightling.app`, its data folder, and the `desktop-app` record.

### 4.1 Updating a 1.8.x (or 1.4.x) install

1. **The old updater runs first.** `ling update` (1.8.x) asks the GitHub API for the latest release of `dreamference/mightling`. After the repository rename that request answers with a redirect, which the updater must follow; this is verified before release (§9.3). It then downloads `ling-<target>.gz` and its siblings, and checks them against the `SHA256SUMS` signed under `mightling-release`. 1.9.0 publishes three sets built from the same binaries:
   - `ragon-*`;
   - `ling-*`, for 1.8.x;
   - `puffin-*` and `puffin-<target>.sha256sums`, for 1.4.x (decision 23: "both old asset names, once").
   One `SHA256SUMS` lists all three. From 1.9.1 on, only `ragon-*` is published.
2. **The next run migrates.** The next time the user types `ling` (or `puffin`), the binary that runs is Ragon, and it migrates (§4.2) before doing anything else.
3. **The desktop app follows inside the migration, not through old-name assets.** The 1.8.x updater looks for `Mightling-Setup-*.exe` and the Mightling dmg. 1.9.0 publishes only the Ragon names, and the new binary's migration installs the Ragon app from the same release, under the same signed checks, when the old app was recorded (decision 17). If the old app is running, this waits for the next start and says so.
4. **The Python side follows.** The old virtualenv still holds `ling-admin`. The migration's notice tells the user to run `install.sh` again (or does it, on a node set up by `install.sh`), and that replaces the wheel, whose only console script is now `ragon-admin`.

### 4.2 The one-time migration

It runs whenever an old layout is present: the old install folder, `~/.mightling`, old links, old units or the old app. Each step is idempotent, so a second run finds nothing to do. It is built from today's `rename.rs` and `legacy_name_migration.py`, which learn the Mightling layout and keep the Puffin one (a 1.4.x machine is migrated straight to Ragon).

**The launcher** (`ragon-rs/src/rename.rs`), on every machine:
- **L1. Moves the install folder whole.** `~/.local/share/dreamference/{mightling,puffin}` becomes `…/ragon` when the new one does not exist, and the binaries inside are renamed (`ling` → `ragon`, `ling-search` → `ragon-search`, …). `indexers/`, `bin/codebase-memory-mcp` and `bin/scip` move with the folder, which is lesson 3. If the code index's tools are missing after the move, the step says to run `ragon-admin code setup`, and `ragon-admin status` names them.
- **L2. Moves the home.** `~/.mightling` is **moved** to `~/.ragon`, not copied, because the home now holds git worktrees and copying them would leave two checkouts claiming one branch. The steps:
  - move the folder;
  - leave a marker file at the old path naming the new home;
  - rewrite `/.mightling/` paths in `config.toml`, and the `[mcp_servers.ling_code]` / `ling_docs` tables;
  - rename `ling-code.toml`, `ling-skills.toml` and each skill's `.mightling-origin.toml`;
  - run `git worktree repair` for every worktree under `worktrees/`, `chat-repos/` and the Night Shift folder, since their `.git` files hold absolute paths.

  A 1.4.x home (`~/.puffin`) is carried over with the allow-list copy of the Puffin migration, which never copies `auth.json`, `installation_id` or the logs.
- **L3. Rewrites configuration** in `~/.config/dreamference/config.toml` and in `dreamference.toml` in the current directory:
  - keys: `mightling_<key>` (and `puffin_<key>`) becomes `ragon_<key>`;
  - values: `server_engine = "ling"` becomes `"ragon"`.
- **L4. Relinks.** In `~/.local/bin`, it removes `ling`, `ling-search`, `ling-fetch`, `ling-code`, `ling-docs`, `ling-app` and `ling-admin` (and the `puffin*` names) only when they are links, and creates the `ragon*` links. When the old `ling-admin` link pointed into a virtualenv that already has `ragon-admin`, it links that too.
- **L5. Renames the pairing key** `~/.ssh/mightling-node_ed25519` (and `.pub`) when the new name is free.
- **L6. Desktop app** (decision 17), on Linux, macOS and Windows: it installs Ragon from the release when the old app was recorded, moves the data folder (`dev.dreamference.mightling` → `dev.dreamference.ragon`) when the new one does not exist, removes the Mightling app (`Mightling.app`; on Windows through its uninstaller with `/S`, which keeps the data folder already moved), recreates the shortcuts and rewrites pins (§2.4), and updates the `desktop-app` record.
- **L7. Windows arm.** A running executable cannot be moved, so on Windows L1 copies to `Programs\Ragon\bin` and points the user `Path` at it. The next `ragon` start removes `Programs\Mightling` (or says what still holds it).
- **L8. Prints once:** "Mightling is now Ragon: run `ragon`". When L4 found no `ragon-admin`, it adds "update `ragon-admin` with install.sh".

**`ragon-admin`** (`dreamference/cli/legacy_name_migration.py`), on a machine with the Python side:
- **A0. The same folder move and link removal** as L1 and L4, so a node whose first new command is `ragon-admin` does not start an empty install folder.
- **A1. The same key and value rewrite** as L3.
- **A2. Replaces every user unit of §2.3:** stop, remove, and reinstall under the new name when the old one was enabled. It refuses to run while a `ling-swe-*` unit, a Night Shift run or a Night Shift job is active, and says so: the 2026-10-08 lesson, and decision 12.
- **A3. Replaces the Avahi file** with `ragon-node.service` carrying both service types (§5).
- **A4. Rewrites `authorized_keys`:** the marker becomes `ragon-node`, and the forced command's `ling-admin` becomes the `ragon-admin` beside it.
- **A5. Moves each paired node's pinned host key** from `mightling-node-<id>` to `ragon-node-<id>` (lesson 2).
- **A6. Node services:**
  - stops the `ling-serve` container, pulls `ragon-serve` at the registry's pinned digest, and restarts under the new name;
  - renames the forge account `mightling-phone` → `ragon-phone`;
  - re-registers the forge's mDNS types;
  - sets the Telegram display name and menu button and the Matrix room name;
  - renames the Signal device where possible (decision 18).
- **A7. One sudo step**, printed as one line when it cannot write:
  - the Avahi file;
  - `ragon-signal.service` and its account (`usermod -l`, `groupmod -n`);
  - the desktop AppArmor profiles and `ragon-bwrap` (and removal of `puffin-bwrap` and `mightling*`);
  - moves the build caches.
- **A8. Says what it did,** step by step.

### 4.3 Only an installed Ragon migrates

The 2026-10-08 guard carries over unchanged in substance:
- **`ragon-admin`** migrates only from a `site-packages` install with no source tree beside it (`LegacyNameMigration.release_install`).
- **`ragon`** migrates only when its resolved executable is directly in `~/.local/share/dreamference/{puffin,mightling,ragon}/bin` (Windows: `Programs\{Mightling,Ragon}\bin`) (`rename::allowed`).
- **`RAGON_LEGACY_MIGRATION`:** `1` migrates from anywhere (tests, scratch `HOME`), and `0` never migrates. Conftest clears it for every test. The old `MIGHTLING_LEGACY_MIGRATION` is honoured as `0` only, so a machine that pinned "never" keeps never.

### 4.4 What the user sees

- **On a client**, after `ling update`, the next `ling` prints the notice and runs the session. From then on `ragon` is the command, and `ling` is "command not found". The desktop app restarts as Ragon, with its windows and preferences.
- **On a node**, the first `ragon-admin` command does the node-side steps and lists them.
- **On Windows**, the Start menu and desktop show Ragon, and a taskbar pin may need re-pinning once.
- **On macOS**, the permission prompts appear again.

## 5. Mixed versions on one network

- **A 1.9.x node advertises both types,** `_ragon-node._tcp` and `_mightling-node._tcp` (the same TXT, the same id), and the forge both `_ragon-forge._tcp` and `_mightling-forge._tcp` (decision 16). A 1.8.x client therefore still finds a 1.9 node.
- **A 1.9.x client browses both types for one release** (technical default). A node that answers only the old type runs 1.8.x, and the client says so: "node `<id>` still runs Mightling: run `ling update` there". The client deduplicates by node id. The node locator is a byte-identical pair (`ragon-rs/node-locator/`, `ragon-web-rs/src/node_locator.rs`), so both copies change together.
- **2.0 drops the old types** on both sides.
- **Pairing survives:** `authorized_keys` is rewritten on the node (A4), the key file is renamed on the sender (L5), and the host-key alias moves (A5). A 1.8.x sender paired with a 1.9.x node keeps working until it updates, because the node's forced command is the same program under its new name.
- **Phones:** the Signal link, the Telegram pairing and the Matrix room survive (decision 18); only display names change.

## 6. Patches

The patches' added lines name the launcher's crates (`ling_launcher::…`, `ling-airgapped`, …) and the product. The script substitutes those names in `codex-patches/` and `codex-tests/` (no upstream line contains them), then checks that the whole series applies with `git apply --check` in a fresh export. `ragon_` is one byte longer than `ling_`, and "Ragon" is four bytes shorter than "Mightling". `test_the_patches_stay_small`'s cap moves only if the total grows, with a dated line, as for every other change.

## 7. Verification

The script's own checks are in §9.2. Before 1.9.0 is published, these must pass:
- **The Python suite**, including `tests/test_code_quality.py` and `tests/test_typing.py` (the renamed files must be formatted and clean, since renamed files count as new files under the ratchet).
- **The launcher's tests in its own export,** with the migration cases:
  - a scratch HOME holding a 1.8.x layout migrates;
  - a 1.4.x layout migrates straight to Ragon;
  - a second run changes nothing;
  - `auth.json` never moves anywhere it should not;
  - links that are real files are left alone;
  - worktrees under the home still work after `git worktree repair`;
  - a home with a running Night Shift is refused.
- **The other Rust crates' tests** (web, code, docs, chat, signal, the skills folder), the desktop app's `npm test`, and `ragon-admin codex test`.
- **A full build of `ragon`**, and the Windows and macOS client builds in CI.
- **`ragon-admin audit egress`**, and `--app` for the desktop app. The audit also names every bridge that is on, under the new unit names.
- **A real `ling update` from 1.8.x** against a test repository renamed on GitHub the same way (a throwaway repository in the organisation, renamed, with a signed test release). This proves that the redirect is followed and the signature verified before the real repository is touched.

## 8. Branches started before the rename

The script is the rename itself, and it is re-runnable (decision 20). Branches still open on the freeze day, in this repository and in the engine repository, are either merged into `main` before the run, or brought across afterwards. The procedure of the Puffin spec's §8 carries over:

```bash
git checkout <branch>
git merge main                    # git follows the renamed folders; conflicts are mostly names
# take the incoming change in each conflict, then rename what the merge brought in:
.venv/bin/python scripts/rename_ragon.py --paths $(git diff --name-only ORIG_HEAD HEAD)
.venv/bin/python scripts/rename_ragon.py --check   # the leftover grep of §9.2
```

New `mightling_*` keys or `MIGHTLING_*` / `LING_*` variables that a branch adds become `ragon_*` / `RAGON_*`, and new crates become `ragon-*`.

## 9. The rename script (decision 20)

### 9.1 Design

`scripts/rename_ragon.py` is built now, on a branch (`rename/ragon-script`), from `scripts/rename_mightling.py`. It runs against any checkout of `main` and leaves a tree ready to commit.

- **Rules, not a list:** an ordered table of `(pattern, replacement, scope)`, longest names first: `DREAMFERENCE_MIGHTLING_`, `MIGHTLING_`, `mightling_`, `Mightling`, `mightling`, `ling-admin`, `ling-search`, …, `ling_`, `ling-`, the word `ling` in commands and paths. Each rule is anchored, so that `lingering`, `handling`, `Kling`, `linger`, `Ling` inside other names and `ling` inside prose words never match. A fixture of known false friends is part of its tests.
- **Folders and files are renamed with `git mv`.** That covers the crate folders, Python files, tests, spec files (decision 24), logos, paper sources and the submodule path. Every link to a renamed file is rewritten.
- **Exclusions are explicit:**
  - `codex/` and the vendored trees;
  - everything §3 keeps (the namespaces, lock files, stored keys, the migration modules, the `Was` columns, release notes of 1.4–1.8, `paper/puffin.*`);
  - **history blocks in specs,** found by markers: dated decision lines (`YYYY-MM-DD` at a line's start or in a "decided" table row), sections titled History, Status lines' past clauses. Text that is ambiguous is listed for a person to decide, never guessed.
- **Spec header:** each renamed spec gets the line "Mightling was renamed Ragon in 1.9.0." under its title (decision 13).
- **Idempotent:** a run over a tree it already renamed changes nothing (`--dry` reports zero).
- **Two repositories:** the same script runs in the engine repository with its own rule file, and the submodule pin moves to the renamed commit.
- **Outputs:**
  - the commit;
  - a report of every exclusion hit and every ambiguous line;
  - the list of GitHub-side steps (§9.4) as commands for the owner's approval.

### 9.2 Checks the script runs, in order

1. **The leftover grep:** `git grep -n -i -E 'mightling|\bling(\b|[-_])|puffin'` over the tree minus the exclusions must print nothing. Each hit is either a rule gap or an exclusion to add. Run it again after the build, over `target/` binaries' strings, for the user-visible names.
2. **The full Python suite** (~2 min), and `ruff format` / `ruff check` on renamed files.
3. **The Rust tests** of every crate in its own export, the desktop app's tests, and the patch series applying.
4. **A migration test on a throwaway HOME:**
   - a synthesised 1.8.x layout: install folder, links, home with two worktrees, units (in a user manager started for the test), Avahi file (in a scratch `/etc`), keys, known-hosts, desktop data;
   - then a 1.4.x layout;
   - run with `RAGON_LEGACY_MIGRATION=1`, and assert the end state field by field;
   - run again, and assert that nothing changes.
5. **A mixed-version mDNS test:** a 1.8.x client binary and a 1.9 node in network namespaces with their own Avahi daemons (as the node-locator tests do). The 1.8 client finds the 1.9 node by id. The 1.9 client finds the 1.8 node and prints the update hint. Two 1.9 peers see each other once, not twice.
6. **`ragon-admin audit egress`** on the built binary: loopback only, no DNS query leaving.

### 9.3 Rehearsals

The script runs on every push to `main` in CI (the rehearsal workflow, excluded from release gating) and by hand on a fresh `main` in a worktree until the cut, about 2026-11-03, with all of §9.2. A rehearsal failing is a rule to fix, not a reason to wait. The repository-rename rehearsal of §7 runs once, early.

### 9.4 What the script cannot do

These are the GitHub, registry and DNS steps, run by hand from the script's printed list on the day:
- rename both repositories;
- set descriptions, topics and the organisation profile README;
- retitle the releases;
- publish `ragon-serve` to GHCR;
- the Cloudflare DNS record and redirect rule;
- enable Pages on the custom domain.

## 10. Name check (2026-10-28)

Done at the depth decided (decision 19): the web, package registries, GitHub and command names, with no paid trademark search. **Nothing serious was found:** no software-class trademark, and no command clash on common systems. The rename proceeds under decision 8.

**Commands:**
- No `ragon` on Linux: none on this Ubuntu node, and `command-not-found` suggests only `dragon` (a snap) and `wagon`.
- None in Ubuntu's archive, as a snap, on Homebrew (formula or cask), winget, Chocolatey or the AUR.
- No macOS or Windows built-in of that name.
- One note on confusion: `ragon` is one letter from `dragon`, and typing `ragon` on a machine without Ragon suggests the snap `dragon`.

**Package registries:**
- PyPI: `ragon`, `ragon-admin` and the other `ragon-*` names are free.
- crates.io: free, the `ragon-*` names included.
- npm: [`ragon`](https://www.npmjs.com/package/ragon) is an unpublished stub (versions published and unpublished on 2026-01-23); the `ragon-*` names are free. Ragon publishes nothing on npm.
- Homebrew and apt: nothing.

**GitHub:**
- [github.com/ragon](https://github.com/ragon) is a personal account (created 2011, no public repositories). No organisation is named `ragon`. The repositories stay under `dreamference`.
- `dreamference/ragon` and `dreamference/ragon-engine` are free.
- [edmand46/Ragon](https://github.com/edmand46/Ragon) is "Ragon Server", a C# room-based game server with its own site (ragon-server.com) and about 37 stars: another field, small.
- **The RAG pun:** several retrieval-augmented-generation projects read as "RAG-on": [dRAGon](https://github.com/dRAGon-Okinawa/dRAGon), [RAGonite](https://github.com/Fraunhofer-IIS/RAGonite), [RAGondin](https://github.com/andyne13/RAGondin), [RAGOnMedicalKG](https://github.com/liuhuanyong/RAGOnMedicalKG). To an AI audience "Ragon" may read as a RAG product, which it is not. This risks confusion, not conflict.

**Trademarks** (web searches only; the official databases were not queried):
- No US, EU or WIPO mark "RAGON" in classes 9 or 42 turned up.
- One Indian registration "RAGON" (application 5111312) is in class 11 (lighting and heating apparatus, an unrelated class), [registerkaro.in](https://www.registerkaro.in/trademark-search/ragon-5111312).
- The owner's one-step follow-up, if wanted: search "RAGON" in classes 9 and 42 in [USPTO Trademark Search](https://tmsearch.uspto.gov/) and [EUIPO eSearch](https://euipo.europa.eu/eSearch/).

**The name in the world:**
- Ragon is a surname, best known through [Phillip "Terry" Ragon](https://en.wikipedia.org/wiki/Phillip_Ragon), founder and CEO of the healthcare-software company [InterSystems](https://en.wikipedia.org/wiki/InterSystems).
- It is also known through the [Ragon Institute](https://en.wikipedia.org/wiki/Ragon_Institute) of Mass General, MIT and Harvard (immunology), named after him.
- Neither is a product mark in software. Searches for "Ragon" in AI find these and Ragie (a RAG service), not another product named Ragon.

**Domains:**
- `ragon.com`, `ragon.dev`, `ragon.io` and `ragon.app` resolve and are held by others; `ragon.ai` does not resolve.
- `ragon.dreamference.ai` needs only a record in the `dreamference.ai` zone, which is on Cloudflare: no purchase.
- **Finding:** `mightling.dreamference.ai`, named in `website/CNAME` and in 21 links in the repository, did not resolve on 2026-10-28, from a public resolver (`1.1.1.1`) or through `ling-fetch`. The website's Pages deployment may never have been switched on. The permanent redirect of decision 4 needs the old record to exist (proxied on Cloudflare) for the rule to answer. If the old host was never public, the redirect protects only links in the repository's own history.

## 11. Sequencing and estimate

The last rename is the yardstick. It was decided on 2026-10-07, built and merged on 10-07/08 and released on 10-08. The install-guard fix followed on 10-08 and the host-key fix on 10-10. That is about **one day to build and two days of fixes**, for one operating system and about 800 occurrences of the old name. This one has about 25,000 occurrences, three client operating systems, the desktop identity, the forge, remote access, the messengers, a second repository, an image registry and a website. Most of that difference is absorbed by building the script ahead (phase 0), off the critical path.

| Phase | When | What | Estimate |
|---|---|---|---|
| 0. Script and migration | now → about 2026-11-03, on `rename/ragon-script` | the script and its rule tables (§9.1); the migration in `rename.rs` and `legacy_name_migration.py` with the Windows and macOS arms; the migration and mDNS tests (§9.2); the repository-rename rehearsal (§7); weekly rehearsals | **5 days** of work |
| 1. Freeze | the day after 1.8.1 ships (about 2026-11-03) | merge or park open branches in both repositories (§8); no merges to `main` while the script runs | 0.5 day |
| 2. Rename in code | day 1 | run the script on a fresh `main` in both repositories; read the ambiguous-line report; all of §9.2 and §7; client builds for Linux, macOS, Windows in CI | 1 day |
| 3. Repositories and release | day 2 | rename both repositories (GitHub redirects); descriptions, topics, organisation README, release titles; publish `ragon-serve`; tag 1.9.0 with the three asset sets under the kept namespace | 0.5 day |
| 4. Website, docs, paper | day 2–3, alongside phase 5 | `ragon.dreamference.ai` record and Cloudflare redirect from the old host; the Mightling-is-now-Ragon line and the tagline under the puffin; the docs site; `paper/ragon.tex` with "formerly Mightling", PDFs rebuilt | 1 day |
| 5. Install day on the owner's machines | day 3 (no night that evening, decision 12) | by day: the main node, then node-b and node-c (`ling update`, then `ragon-admin` for the node steps and the one sudo line); pairing, forge, messengers, engine container and code index checked on each; the night units reinstalled as `ragon-*`; nights resume the next evening | 1 day |
| 6. Desktop identity on real hardware | day 3–4 | a Windows test machine and a Mac: the automatic app switch, data moved, old app and shortcuts gone, pins, notifications, TCC prompts; the Linux `.deb` | 1 day |
| 7. Aftercare | days 4–6 | fixes found in use (the last rename needed two), 1.9.1 if needed | 2 days (reserve) |

**Total: about 12 days of work.** 5 of them fall before the freeze (building the script). 7 fall after it, and they take about 6 calendar days, because phases 4 and 6 run beside phase 5. 1.9.0 is published on the freeze day or day 2 after it (about 2026-11-03, the freeze day itself, when the runbook of `specs/DREAMFERENCE_RELEASE_1.9.0.md` holds). All machines are on Ragon by day 3, and the rename is settled by about day 6 (around 2026-11-09), all assuming 1.8.1 ships on 2026-11-02 and needs no hotfix. The first benchmark night after the rename runs with a system prompt that says Ragon. The pinned-series rule treats that as a new configuration, so a night that compares against an earlier one says so.

## 12. Open questions

Defaults are marked; the rest of the rename is decided (§0).

1. **Release titles of past releases** (decision 25 against decision 13): retitle 1.4–1.8 as "Ragon 1.x.y", or leave old titles and title only 1.9.0 on as Ragon? *Default: leave past titles, because they are history (decision 13), and title from 1.9.0 on.*
2. **NetBird names of enrolled devices:** keep `mightling-<id>` until a device re-enrols, or rename every peer on install day? *Default: keep (§3).*
3. **A 1.9 client browsing the old mDNS type:** decision 16 covers nodes. Should clients also browse `_mightling-node._tcp` for one release, so that update order does not matter? *Default: yes (§5).*
4. **Trademark follow-up:** whether to run the free USPTO and EUIPO searches yourself before phase 3 (§10). *Default: not required under decision 19.*
