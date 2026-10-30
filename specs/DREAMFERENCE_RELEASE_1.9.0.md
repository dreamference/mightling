# Ragon 1.9.0 — release notes

**Status: DRAFT, not for release.** Prepared 2026-10-29 on the branch `rename/ragon-release`, ahead of the
cut. The description between the two rules holds one `{{TBD` placeholder, which is what keeps
`scripts/release_notes.py` from publishing it: remove it only on cut day, after the rename has run on `main`
and the checks of `specs/DREAMFERENCE_RENAME_RAGON.md` §7 have passed (the checklist after the second rule).
This file keeps its path and its old-name wording through the rename script on purpose.

---

{{TBD cut day: delete this line (nothing replaces it) after runbook steps 1 to 9 passed and 1.8.1 has been out for a day without a hotfix; the facts that release it do not exist before then.}}

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

---

## Cut-day runbook

**When.** Ragon 1.9.0 is cut the day after 1.8.1 ships (1.8.1 targets 2026-11-02, so the cut is about
2026-11-03), if 1.8.1 needed no hotfix in its first day. A 1.8.1 hotfix moves the cut by a day each time.
The rename script and its checks are rehearsed on every push to `main` by the CI workflow
`ragon-rename-rehearsal.yml`, which never gates a release, so the cut is a replay of a rehearsal.
Total: about 7 hours of work on the day, steps 1 to 9; step 10 runs beside the later steps and the
machines take the rest of the day.

Commands run from the checkout of `main`, in a worktree of its own, with the rename script and its overlay
taken from `rename/ragon-script` and this branch's notes and test from `rename/ragon-release`. "Agent" is
a background engineering agent; "Owner" needs the owner's account or hand.

| # | Step | Command | Who | Time |
|---|---|---|---|---|
| 0 | Go/no-go: 1.8.1 out a day, no hotfix, CI green on `main`, the last rehearsal green | `gh run list --branch main -L 3`; `gh run list --workflow ragon-rename-rehearsal.yml -L 3` | Owner decides | 10 min |
| 1 | Freeze `main`: merge or park open branches in both repositories; no merges until step 9 | `git fetch && git status` in a fresh worktree of `origin/main` | Owner (branches), Agent | 20 min |
| 2 | Run the script on `main` (and, in the engine checkout, `--repo engine`) | `python scripts/rename_ragon.py --root . --report rename-report.txt` | Agent | 5 min |
| 3 | Leftover check: 0 hits outside the exempt files; read the report's ambiguous lines | `python scripts/rename_ragon.py --root . --check` | Agent | 10 min |
| 4 | Suite and builds: the Python suite, `cargo test --locked` per crate, `npm test`, `codex build` | `python scripts/rename_ragon.py --root . --verify` (runs the leftover check, pytest, the ruff and mypy ratchet, the three crates, `npm test`, `codex build`, the audit) | Agent | 60 to 90 min |
| 5 | Egress audit on the built `ragon` (loopback only, no DNS query leaving) | `ragon-admin audit egress` (step 4 runs it; run by hand after any rebuild) | Agent | 10 min |
| 6 | Migration on a throwaway HOME: a 1.8.x tree migrated by the renamed launcher and by `ragon-admin`; a second run changes nothing | `python -m pytest tests/test_legacy_name_migration.py tests/test_release_transition.py -q`; `cargo test --release -p ragon-launcher` in the build export, not in `codex/` | Agent | 15 min |
| 7 | Mixed-version mDNS: a 1.8.x client binary against a 1.9 node, each in a network namespace with its own Avahi daemon, both directions, and two 1.9 peers see each other once | the node-locator tests of the renamed tree (`cargo test` in `ragon-rs/node-locator`) plus the namespace test of §9.2 item 5 | Agent | 15 min |
| 8 | Real `ling update` from 1.8.x against a throwaway repository, renamed, with a signed test release: the redirect is followed and `SHA256SUMS` verifies under `mightling-release` (§7) | the throwaway-repository procedure of §7 | Owner (creates and renames the repository), Agent | 45 min |
| 9 | Commit the rename as dgxcoder, delete `{{TBD`, set version 1.9.0, check the notes print: `python scripts/release_notes.py 1.9.0` | `git commit`; `python scripts/release_notes.py 1.9.0` | Agent | 10 min |
| 10 | Push to `main` (docs.yml deploys Pages on push: do step 13 first if the custom domain must already answer) | `git push origin main` | Owner | 5 min |
| 11 | Rename the repositories (`mightling` to `ragon`, `ling-engine` to `ragon-engine`; update the submodule URL the same day); descriptions, topics, social preview, organisation README; the release-signing environment still bound | GitHub settings | Owner | 20 min |
| 12 | Publish `ragon-serve` to GHCR at the pinned digest and pin it in the registry before the tag (keep `ling-serve` until 2.0) | `scripts/pin_ling_serve_image.py` in its renamed form, after the image build script | Owner (registry login), Agent | 40 min |
| 13 | Website and docs: the `ragon` DNS record, Pages custom domain `ragon.dreamference.ai` with HTTPS, a 301 rule from `mightling.dreamference.ai`; the snippet, README story, logos, `website/CNAME`; check `/docs/` builds | Cloudflare, GitHub Pages; the prepared text is on `rename/ragon-assets` | Owner (DNS), Agent | 45 min |
| 14 | Dry run of the release build on the renamed `main` | `gh workflow run release-dry-run.yml --ref main` | Agent | 40 min |
| 15 | Tag 1.9.0: dispatch the release workflow with version `1.9.0` (the older-assets step runs only for exactly `1.9.0`). Title "Ragon 1.9.0" | `gh workflow run release.yml -f version=1.9.0` | Owner | 45 min |
| 16 | Check the three asset sets: `ragon-*`, `ling-*` and the five `puffin-*` names (Linux), one signed `SHA256SUMS`, Windows, macOS and `.deb` under Ragon names | `gh release view v1.9.0 --json assets` | Agent | 10 min |
| 17 | Smoke: `install.sh` from the new release URL on a clean box; `ling update` from a 1.8.x box lands on Ragon and prints the notice once; Windows, macOS and `.deb` updates | the clean box, then each machine | Agent, Owner (Windows, Mac) | 60 min |
| 18 | The owner's machines, by day, no Night Shift that evening: the main node, then the second and third (`ling update`, then `ragon-admin`, one sudo line); pairing, forge, messengers, engine container, code index; the release dry-run timer unit moves with the unit migration, confirm | per node | Owner (sudo), Agent | 90 min |
| 19 | Aftercare: Telegram bot names, Claude Code plugin reinstall, the paper PDF, delete the rehearsal workflow; 1.9.1 publishes only `ragon-*` | | Owner, Agent | days 2 to 4 |

Steps 2 to 9 are one unbroken stretch on a frozen `main`; if one fails, fix the rule on
`rename/ragon-script`, reset the worktree and run it again from step 2: nothing is pushed before step 10.
Do not start step 10 with a failed step behind it. Step 15 cannot be undone, so steps 14 and 16 are there.
Ragon-day order in short: freeze, script, checks, push, rename repositories, image, website, dry run, tag,
smoke, machines.
