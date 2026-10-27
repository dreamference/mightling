# The forge

Developer notes behind the forge line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_FORGEJO.md`. Code: `dreamference/forge/`.

Every node runs Forgejo (pinned `codeberg.org/forgejo/forgejo:15.0.11-rootless` by digest, the long-term-support line), set up by `ling-admin node enable` (`--no-forge` skips it, `[forge] enabled = false` in `~/.config/dreamference/config.toml` or `DREAMFERENCE_FORGE=0` turns it off). The first node set up is the **forge of record**; every node that finds one on the network (`_mightling-forge._tcp`, `role=record`) is a **read-only mirror**. The role is decided once and kept in `~/.config/dreamference/forge/state.json`: a browse that misses the record never promotes a mirror.

## What runs

- `mightling-forge.service` (user unit) runs `ling-admin forge run`, which writes `app.ini`, resolves the addresses and `exec`s `docker run --rm` in the foreground: container `mightling-forge`, network `mightling-forge` (a user-defined bridge, so no other container reaches it), `--memory 1024m --memory-swap 1024m --cpu-shares 256`, data in `~/.local/share/dreamference/forge/data` (the container's `/var/lib/gitea`; the rootless image runs as uid 1000, the node's user).
- **Binding:** one `-p <address>:3200:3000` (and `:2222`) per advertised LAN address plus loopback (`ForgeBind`): never a bare `-p`, never a Docker bridge, `veth`, VPN or the overlay (`wt0`, Tailscale); Avahi's `allow-interfaces`/`deny-interfaces` narrow it further. Resolved at every start, never stored.
- **Timers:** `mightling-forge-tick.timer` (10 min: storage budget, push-mirror sweep, hooks, the GitHub mirror's sweep; hourly the mirror plan; on a mirror, a restart when the record's address changed), `mightling-forge-backup.timer` (06:30, the record only), `mightling-forge-check.timer` (monthly `restore --check`), `mightling-forge-mirror.path` (a push's post-receive hook touches `data/mightling/pushed`; the GitHub mirror runs seconds later).
- **The advertisement** is a second `<service>` in the node's own Avahi file (`NodeServiceFile.render(forge=(role, 3200))`); `NodeServiceFile.parse` reads only the node's block, `update` keeps the forge's.
- **Memory:** `VLLMServerManager.side_service_allowance()` adds the forge's 1 GiB cap to what the pre-load check requires while its container runs, and the refusal names it. A machine whose forge was never set up asks docker nothing.

## Accounts and secrets

`forge-admin` (administrator, used through its token by `ling-admin` only) is created first, then `owner` (not an administrator), both with `forgejo admin user create --random-password`, so no password is ever on a command line. The owner's password is printed once at the end of `node enable` when stdout is a terminal, otherwise only its location; `forge password --show` prints it again on the node's own terminal. On the record a `mirror` account's read token is what mirrors pull with. Secrets live in `~/.config/dreamference/forge/` (0700, files 0600), `app.ini` (0600) keeps its own secrets across rewrites.

`app.ini` (generated at every start, `ForgeAppIni`): SQLite, registration off, sign-in required to view anything, new repositories private, update checker, mail, federation, packages and Gravatar off. Reverse-proxy sign-in is **not** on yet: nothing sets the header until `ling web`'s `/forge/` proxy exists (spec P0's second half), and Docker's proxy is not loopback.

## Private repositories never leave

- **Forgejo's own push mirror is never used:** it pushes with `--mirror`, which deletes on GitHub, and **its API accepts a push mirror even with `DISABLE_NEW_PUSH = true`** (measured on 15.0.11 by `tests/test_forge_private.py`). So the record runs with `[mirror] ENABLED = false` (it needs no pull mirror) and every tick deletes any push mirror it finds, loudly when on a private repository.
- **The GitHub mirror** (`ForgeGithubMirror`, `forge github-mirror --all-branches`) is a host job on the record: for each repository with a GitHub destination in `repositories.json` it skips private and never-mirror ones (`archive-pre-rewrite`, `memos`; a missing visibility counts as private), stops the whole run when it cannot list visibility, fetches both sides into `~/.local/share/dreamference/forge/github-work/`, re-runs the anonymity check on every commit GitHub lacks, pushes what is new or ahead **without force and without prune**, and brings back what is only or further on GitHub (release tags, a branch pushed there directly). A branch diverged both ways is reported, never forced. At `/airgapped on` (`mightling_airgapped = "on"` in `~/.mightling/config.toml`) it sends nothing.
- **Credentials:** git's credential helper asks `gh auth token --user <account>` at the moment of the push, so no token is copied into a file, URL or log. If `gh`'s keyring is locked (a reboot with nobody logged in), the run fails and `forge status` says so.
- `tests/test_forge_private.py` proves it: mocked in the suite, and against a throwaway Forgejo with `MIGHTLING_FORGE_CONTAINER_TESTS=1` (marker `forge_container`; conftest lets such a test run docker only on `mightling-forge-test-*`).

## The anonymity hook

A `pre-receive.d/mightling-anonymity` hook (bash, inside the container, which has no Python) scans what a push **adds**: every new commit's added lines prefixed with their path, the paths, authors, committers and messages, with `git grep -P` against `data/mightling/anonymity-<scope>.pcre`, written from the node's denylist (`~/.config/dreamference/anonymity-denylist.txt`) and the generic checks of `tests/test_anonymity.py` (`tests/test_forge.py` checks the constants and the matches agree). Scopes per repository: `full`, `denylist` (a fork whose upstream is full of other people's paths), `off` (the product default; the anonymity rules are this project's). Exempt paths (`tests/test_anonymity.py`) are not scanned. The same hook refuses a push bringing a root commit the repository lacks (a branch of a rewritten history) and every push while `data/mightling/frozen` exists (a `move`).

## Backups, move, promote

- `forge backup` (06:30 nightly): `forgejo dump` plus a consistent SQLite snapshot (the backup API), a manifest of every ref, the secrets (0600) and a `git bundle --all` per repository, as `~/.local/share/dreamference/forge/dumps/forge-<UTC>/` (only the latest kept on the record), streamed over the pairing (`forge-backup-receive`) to two paired nodes, which keep 7 daily and 4 weekly sets in `…/forge/backups/` (0700), bundles in the newest only.
- **Restoring** rebuilds the database from the snapshot, or from the dump's SQL, whose `unistr()` calls an older SQLite lacks (`ForgeBackup.unistr` stands in), keeps the dump's `app.ini` secrets, and compares every ref with the manifest. The replaced data folder is moved aside, never deleted.
- `forge move <node>` (on the record, paired with the target): freeze, back up, send to the target and one more node, the target restores and checks every ref (`forge-restore`), then this node's data and secrets are moved aside and it starts again as a mirror; On a mirror paired with the record (but not the other way round), `forge move here` or `move --back` pulls the role instead: the record freezes and streams a fresh set back (`forge-hand-over`), this node restores and checks it, and the record becomes its mirror (`forge-become-mirror`; `forge-unfreeze` if anything fails). The denylist travels in the set's secrets, so a moved forge checks what the old one did. `forge promote [<node>]` restores the newest set a node holds when the record is lost.

## Storage budget

`forge status` reports use by kind. Below the node's floor plus 10 GB (150 GB, 175 GB while a `ling-swe-*` unit runs) the tick prunes artefacts, then logs, then the local dump, oldest first; repositories never. Artefacts and logs expire after 7 days on the record (`[actions]`).

## Mirrors

`forge mirrors sync` asks the record for its plan (`forge-mirror-plan`: node id, name, read token, every shared repository) and hands it to every paired node (`forge-mirror-apply`), which creates Forgejo pull mirrors (10 min) of `http://forge-record:3200/<repo>.git`; `forge-record` is an `--add-host` entry resolved by node id when the mirror's container starts (containers cannot resolve `.local`). The record's tick does it hourly for the nodes it is paired with. A mirror node's `[migrations]` allow only that alias.

## Keep when touching this

- No password or token on a command line, in a URL git could print, or in a log.
- Tests never run docker except the marked throwaway container; `ForgeContainer`'s name, network and ports are class attributes for that.
- The egress audit's declared exceptions name the forge and each mirror destination (`EgressAudit.forge_lines`).
