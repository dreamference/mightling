Drop-in section for `specs/DREAMFERENCE_RELEASE_1.9.0.md`, between its first two `---` lines (see
`scripts/release_notes.py`). The text below the rule is the section.

---

## Mightling is now Ragon

Mightling is now **Ragon**. Ragon is a puffin that thinks it is a dragon: the same small, determined bird, and the same private AI on your own GB10, now with a name that fits its ambitions. The logo keeps the puffin; only the wordmark changes. Dreamference is still the company.

This release does the rename and nothing else. Your threads, settings, skills, paired nodes, phone links and model stay where they are: the first run moves them.

### What to do

1. Run `ling update` once, as usual. The next time you type `ling` it prints "Mightling is now Ragon: run `ragon`" and migrates your install before doing anything else.
2. From then on the command is `ragon`. There is no `ling` alias.
3. On a node, run `ragon-admin` once after updating (any command). It moves the node's services over and prints one `sudo` line for the parts that need it. If `ragon-admin` is not found, run the installer again: `curl -fsSL https://github.com/dreamference/ragon/releases/latest/download/install.sh | bash`.
4. The desktop app updates itself to Ragon and removes the Mightling app. If the old app is running, this happens at its next start.

### What changed

| Was | Is now |
|---|---|
| `ling`, `ling-admin` | `ragon`, `ragon-admin` |
| `ling-search`, `ling-fetch`, `ling-code`, `ling-docs`, `ling-app` | `ragon-search`, `ragon-fetch`, `ragon-code`, `ragon-docs`, `ragon-app` |
| `~/.mightling` | `~/.ragon` (moved, with its worktrees repaired) |
| `ling-serve` (engine `ling`) | `ragon-serve` (engine `ragon`) |
| `ghcr.io/dreamference/ling-serve` | `ghcr.io/dreamference/ragon-serve` |
| `github.com/dreamference/mightling` | `github.com/dreamference/ragon` (the old address redirects) |
| `mightling.dreamference.ai` | `ragon.dreamference.ai` (the old address redirects) |
| Claude Code plugin `mightling` | `ragon`: reinstall once with `/plugin install ragon@ragon` |

Unchanged on purpose: the company (Dreamference), the `dreamference` Python package, `DREAMFERENCE_*` variables that do not name the product, `dreamference.toml`, `~/.config/dreamference/`, the `dreamference` GitHub organisation, and the signing of releases (so every 1.x updater still verifies this release).

### Environment variables and configuration

Configuration files are rewritten for you. **Your shell profile is not**, so edit it yourself:

- `DREAMFERENCE_MIGHTLING_*` becomes `DREAMFERENCE_RAGON_*`.
- `MIGHTLING_*` becomes `RAGON_*` (for example `MIGHTLING_INSTALL_DIR`, `MIGHTLING_RELEASE_REPO`, `MIGHTLING_NODE`).
- `LING_*` becomes `RAGON_*`.
- In `dreamference.toml`, `mightling_*` keys become `ragon_*` and `server_engine = "ling"` becomes `"ragon"`. The migration does this; a file it cannot reach is refused with a message naming the new value.

The old names are no longer read, except `MIGHTLING_LEGACY_MIGRATION=0`, which still means "never migrate".

### Re-pin once

- **Windows.** The app has a new identity, so Windows treats it as a new app. Notification settings (on/off, banners, priority) and the notification history start empty, and a taskbar pin of Mightling stops working. The update recreates the Start menu and desktop shortcuts and repoints a pin where it can find one; Windows 11 caches pins, so **unpin Mightling and pin Ragon once**.
- **macOS.** The app has a new bundle id, so macOS asks again for the microphone (voice) and for notifications, and for whatever Quick Entry's global shortcut needs. A Dock icon for Mightling turns into a question mark: remove it and drag Ragon in.
- **Linux.** The launcher entry is now Ragon. Re-add it to your dock favourites once. The `.deb` replaces the old package.

The app keeps no secrets in the system keychain, so nothing encrypted is lost. The app's data (window layout, preferences) moves with it.

### Phones and nodes

- Signal, Telegram and Matrix links are kept; nothing needs re-pairing. The bots' display names change to Ragon, their usernames stay.
- Paired nodes keep working. For this release a node advertises both the Ragon and the Mightling service names, so a node still on 1.8.x finds a 1.9 node and the reverse. A 1.9 client that sees a node still on 1.8.x says so: run `ling update` there. The old service names go in 2.0.
- No Night Shift runs on the day you update; nights resume the next evening under the `ragon-*` unit names.

### Updating from older versions

1.9.0 also publishes the release asset names that 1.8.x (`ling-*`) and 1.4.x (`puffin-*`) updaters download, carrying the Ragon binaries, so `ling update` and `puffin update` land on Ragon. From 1.9.1 only the `ragon-*` assets are published, so update through 1.9.0 first.

### Still named Mightling, on purpose

Signed release and adapter namespaces, the GPU and build lock files in your home, existing NetBird peer names, the relay box's account, stored benchmark records and past release notes. These are internal and no one is asked to change them.
