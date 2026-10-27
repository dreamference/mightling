# Mightling Forge: a Forgejo forge on every node

**Status:** proposed 2026-10-27. **Decided by the owner the same day (§0.1, O1–O7):**
- Forgejo is a **product feature of the node install, on by default**.
- The first node set up is the forge of record, with a `move` command to hand the role over, and every other node is a read-only mirror.
- For this project's own fleet, every branch is push-mirrored to GitHub through the anonymity check.
- CPU gates move to the forge's runners.
- LAN only.
- The pre-rewrite history goes into a private repository that a server-side rule and a test keep off GitHub.

**Building waits:** this is the spec only.

**Names:** post-rename (`ling`, `ling-admin`, `~/.mightling`). Machines are written with the repository's placeholders: `gx10-1a2b` (this project's main node), `node-b`, `node-c`, `node-d` (the other GB10s). Real names and addresses are never written here; nodes are resolved by name at use time ([NODE](./DREAMFERENCE_MIGHTLING_NODE.md)).

**Builds on:**
- [NODE](./DREAMFERENCE_MIGHTLING_NODE.md): the node install, the advertisement on the LAN (§3), pairing (§13.2, §18.6), jobs in capped user units, the trusted-LAN decision;
- [FLEET](./DREAMFERENCE_MIGHTLING_FLEET.md): provisioning further GB10s from an existing node;
- [ASK](./DREAMFERENCE_MIGHTLING_ASK.md): `ling web` and its device pairing; [DESKTOP](./DREAMFERENCE_MIGHTLING_DESKTOP.md): the app;
- [EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md): the audit and its declared node services;
- [SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) §30 and the night scheduler: nights own a machine's GPU and disk;
- [ORGANISATION](./DREAMFERENCE_ORGANISATION.md): the 15 team roles;
- `docs/dev/host-safety.md`: anything beside a resident model server bounds its memory;
- the anonymity rules (`tests/test_anonymity.py`, the local denylist, the pre-push guard).

**Why.** Three things happened in one week:
- **An identifier leaked:** an agent's working branch put a real host name into a spec on public `main`.
- **Branches went stale:** after the history rewrite of 2026-10-26, about 200 local branches still pointed at the old history.
- **CI ran out of room:** GitHub's download limit refused a release download on a test box, and every CI gate ran on one machine (`gx10-1a2b`), competing with the model server.

A forge on the nodes that already run day and night fixes all three for this project, and gives every Mightling owner the same thing: their code on their own node, Night Shift's branches as pull requests in a web UI, and their GB10 as a CI machine.

---

## 0. Decisions

1. **Forgejo, part of the node install, on by default.**
   - `ling-admin node` install and enable start a Forgejo instance on every node, in Docker from `codeberg.org/forgejo/forgejo`, pinned by digest in the release like every image Mightling runs ([DOCKER](./DREAMFERENCE_DOCKER.md)).
   - The release line is the current long-term-support one when a Mightling release is cut. On 2026-10-27 the latest stable is v15 (2026-04-16). Stable lines get three months of fixes; the first-quarter line each year gets long-term support.
   - The off switch is in §2.5.
2. **The main node is the forge of record; every other node is a read-only mirror.**
   - The main node is **the first node set up** (in this project's fleet, `gx10-1a2b`). It holds the writable repositories, issues, pull requests and the Actions queue.
   - **The role moves by command:** `ling-admin forge move <node>` hands it to another node in a planned way (§6.1): it copies the repositories, flips the mirrors and keeps a backup. `promote` is the unplanned case.
   - Every other node's instance pull-mirrors it, set up automatically when that node is installed or enabled, so clones, fetches and CI checkouts are local everywhere.
   - `ling-admin forge promote <node>` turns a mirror into the forge of record if the main node is lost (§6).
   - Two writable instances never serve the same repository: Forgejo has no multi-writer replication, and this spec doesn't invent one.
3. **LAN only.** The forge listens only where the node already advertises itself, on the interfaces `node-advertise` publishes (`~/.config/dreamference/node-advertise.json`), never on Docker bridges or the remote-access overlay. There's no TLS on the LAN, per the trusted-LAN decision. Anonymous access is off, and every account has a password and SSH keys.
4. **Bounded beside the model server.** One container per node, capped at 1 GiB of memory and one CPU share, using SQLite. Data lives under `~/.local/share/dreamference/forge/`. A runner job never starts a model server, and never touches a GPU without that node's GPU lock (§4.3).
5. **Runners on every node, CPU first.** Each node runs a Forgejo Runner registered with the forge of record, with labels for what the node has. CPU gates (pytest, cargo tests in an export, npm) move there and spread across the nodes. GPU jobs follow under the GPU lock and outside nights (§4.3).
6. **GitHub mirroring is a per-repository choice in the product, off by default.** For this project's own fleet, it's on for every branch (§3.2). Private repositories can never be mirrored out, by a server-side rule with a test (§5.3).
7. **Upgrades follow Mightling releases.** Each release pins the Forgejo and runner digests. `ling update` (or `ling-admin node update`) pulls the new image, takes a `forgejo dump` first, then restarts. It never skips a Forgejo major version without running that version's migration path, and it refuses a downgrade.

### 0.1 Decided by the owner (2026-10-27)

| # | Decision | Answer |
|---|---|---|
| O1 | Instances on every node | **Yes, as a product feature on by default:** the main node holds the forge of record, and every other node runs a read-only mirror, set up automatically by `ling-admin node` install and enable; `promote` for failover |
| O2 | Which node is the forge of record | **The first node set up** (here `gx10-1a2b`), with `ling-admin forge move <node>` to hand the role to another node: it copies the repositories, flips the mirrors and keeps a backup (§6.1) |
| O7 | Storage on the forge of record | **Sized for a tight disk:** `gx10-1a2b` runs near its 150–175 GB floor, so the forge keeps a 10 GB budget there, with logs, artefacts and dumps capped and dumps stored off-node (§5.2) |
| O3 | What reaches GitHub | **Everything, for this project's fleet:** every working branch, abandoned ones included, push-mirrored to GitHub to maximise the public commit count. Every push to the forge goes through the anonymity check first. Private repositories are never mirrored |
| O4 | CI | **CPU gates move to the forge's runners across the nodes;** the release pipeline stays on GitHub |
| O5 | Reach | **LAN only for now** |
| O6 | Pre-rewrite history | **A private repository on the forge of record,** excluded from any GitHub mirror by a server-side rule, with a test proving it can't be pushed out |
| — | When | **Spec now; building later** |

---

## 1. What runs where

| Node | Forgejo | Runner labels | Notes |
|---|---|---|---|
| Main node: the first node set up (`gx10-1a2b` in this project's fleet) | **forge of record** | `gb10 arm64 docker` (CPU jobs) | Serves the owner's model; GPU jobs here only if the owner allows (§4.3) |
| Every other node (`node-b`, `node-c`, `node-d`) | read-only mirror | `gb10 arm64 docker gpu cuda-13` | Nights by night, engine windows by day; GPU jobs under the lock |

The forge of record is the first node set up. In this project that is `gx10-1a2b`, which has the tightest disk in the fleet (§5.2). `ling-admin forge move <node>` hands the role to another node at any time (§6.1).

Clients (laptops, the AMD box, the Mac) run no forge; they use the main node's.

## 2. The product: what a Mightling owner gets

### 2.1 On install

`ling-admin node install` (and `enable` on an existing node):
- pulls the pinned image and writes `app.ini` (§2.3);
- starts the container as a user unit, `mightling-forge.service`;
- advertises the forge on mDNS as `_mightling-forge._tcp`, with the node id and `role=record` or `role=mirror` in its TXT record.

On a mirror node, it finds the forge of record by that record, creates a `mirror` account there with a read token, and sets up pull mirrors of every non-private repository (every 10 minutes, plus on a webhook from the record).

### 2.2 The first account, with no default password

- **The owner's account is created at install** as `owner`, with a generated 24-character password. It's shown **once**, in the terminal at the end of `node install`, and stored on the node at `~/.config/dreamference/forge/owner-password` (0600) so `ling web` can show it again to a paired device. It's never shown on the LAN without a paired session.
- **Paired devices sign in without the password.** A device paired with the node through `ling web` (a one-time code) gets a forge session through `ling web`: the forge sits behind `ling web`'s reverse proxy at `/forge/`, using Forgejo's reverse-proxy authentication header, which only `ling web` can set. So the desktop app and the phone page open the forge already signed in as `owner`.
- **Direct access:** the port is reachable on the LAN for git over HTTP and SSH. A browser reaching it directly still sees Forgejo's sign-in page, and needs the password or an SSH key.
- **SSH keys:** `ling-admin forge key add <file>` adds a key to `owner`. A paired client's `ling` adds its own key at pairing time, so `git push forge` works from a laptop without setup.
- **Admin account:** a separate `forge-admin` with its own generated password, stored like the owner's. It's used only by `ling-admin` for maintenance, never shown in the UI.
- **Team accounts:** this project's fleet adds the 15 ORGANISATION team users, so agents commit and push as the team that owns the change. The product adds none by default.

### 2.3 Configuration (generated, not edited by hand)

- **Database and server:** `DB_TYPE = sqlite3`; `ROOT_URL` is the node's `.local` name plus `/forge/` through `ling web`, with the direct port as a second clone URL; `HTTP_PORT = 3200`; `START_SSH_SERVER = true` on port 2222 (Forgejo's built-in SSH; the host's sshd is untouched).
- **Bind address:** `HTTP_ADDR` and `SSH_LISTEN_HOST` are each advertised interface's address, resolved at start, never stored. They're regenerated when the advertisement changes, the same moment `ling web` re-binds.
- **Access:** `DISABLE_REGISTRATION = true`, `REQUIRE_SIGNIN_VIEW = true`, `DEFAULT_PRIVATE = private`.
- **Reverse-proxy sign-in:** `[service] ENABLE_REVERSE_PROXY_AUTHENTICATION = true`, trusting only loopback, which is where `ling web`'s proxy connects from.
- **Updates and mail off:** `[cron.update_checker] ENABLED = false`; mail off; no telemetry (Forgejo has none).
- **Mirrors:** `[mirror] DEFAULT_INTERVAL = 10m`. Push mirrors are allowed only for repositories that aren't private (§5.3).
- **Actions:** `[actions] ENABLED = true` on the forge of record.
- **Secrets:** generated at install into the data folder (0600). They never enter a repository, a log or a memory note.

### 2.4 What the owner sees

- **`ling web` and the desktop app:**
  - **A "Code" entry:** the forge's web UI inside the app's window, through the `/forge/` proxy, signed in. Navigation is locked to the forge's paths, the same lock-down as the app's other views.
  - **The node's status page** lists the forge: on or off, role (record or mirror), mirror freshness, runner health, last backup.
  - **Night Shift results** arrive as pull requests on the forge, alongside the existing branch and `ling night apply`. The morning report links to them.
- **`ling node list`** shows each node's forge role.
- **`ling-admin forge status`** gives the same information on the command line (§7).

### 2.5 The off switch

- **`ling-admin forge off`** stops the unit, removes the mDNS record and keeps the data. **`on`** brings it back.
- **The setting** is `[forge] enabled = false` in the node's config (`~/.config/dreamference/config.toml`, never the repository), with `DREAMFERENCE_FORGE=0` as an environment override. `node install --no-forge` installs without it.
- **Off on the main node** means the mirrors stop syncing and show "forge of record is off". Nothing is deleted.
- **At `/airgapped on`:** the forge keeps running on the LAN, but its outbound mirrors to GitHub stop (§5.4).

### 2.6 Memory beside the model server

- The forge's container (1 GiB cap) and the runner's container (256 MiB, plus per-job caps, §4.1) are added to the side-service allowance `check_host_safety()` uses before a model load. The pre-load refusal names them when they're why a load doesn't fit.
- The PSI watchdog's kill path is unchanged: the forge and runner are ordinary capped containers, and the model server keeps priority.
- The forge idles at about 150–300 MB on SQLite; the cap is headroom for large pushes and repository indexing.

## 3. Repositories

### 3.1 On the forge of record

| Repository | Visibility | Mirrors |
|---|---|---|
| `mightling` | Writable; agents push every branch here | **Push mirror to GitHub, every branch and tag** (§3.2) |
| `ling-engine` | Writable | Push mirror to GitHub, every branch and tag |
| `codex` | Writable (the fork's branch) | Push mirror to GitHub |
| `archive-pre-rewrite` | **Private, admin and owner only** | **None, ever** (§5.3) |
| `memos` | **Private** | **None, ever** |

Every mirror node pull-mirrors every non-private repository from the forge of record. Private repositories reach mirror nodes only through the nightly backup (§6), never as browsable mirrors.

### 3.2 Everything to GitHub (this project's fleet, O3)

- **The forge is the origin; GitHub is its public copy.** Agents push only to the forge, and the forge's push mirror sends every branch and tag to GitHub within a minute (push-on-commit plus a 10-minute sweep). Abandoned branches go too, to maximise the public commit count.
- **Nothing is deleted on GitHub.** The mirror pushes without pruning: forge-side branch deletions don't reach GitHub. That keeps abandoned branches and their commits public, and keeps GitHub-only refs alive. Forgejo's push mirror uses `--mirror`, which prunes, so this project's mirror is a small forge Actions job (`.forgejo/workflows/github-mirror.yml`) that runs `git push github 'refs/heads/*:refs/heads/*' 'refs/tags/*:refs/tags/*'` without `--prune` on every push, triggered by the forge's push event.
- **Refs GitHub creates itself:** release tags made by `release.yml`, and the merge of a release branch back into `main`. Releases are tagged on the forge first, and `release.yml` takes an existing tag instead of creating one. A ref that still appears only on GitHub (for example a hand-made tag) is fetched back into the forge by a 10-minute pull of GitHub's tags, so the two never diverge silently.
- **Every push is checked before it exists on the forge.** A server-side `pre-receive` hook on the forge of record runs the anonymity check (`tests/test_anonymity.py`'s checks against the denylist kept on the main node, never in a repository) on every ref of every push to a non-private repository. A failing push is refused, with the matching file and line, so a leak never enters the forge, and therefore never reaches GitHub.
- **The mirror re-checks every ref before sending,** as a second layer, and stops the whole mirror run on any failure, with an issue on the forge. The existing client-side pre-push guard stays on the developer machines.
- **Rewrite rule:** the forge's history is never rewritten. If GitHub's is (as on 2026-10-26), the mirror is paused, the forge is rewritten with the same tool and the same id map, and the mirror resumes with one forced push per ref, after the owner's OK.

In the product, push mirroring is per repository, off by default, and never on for a private repository. The every-branch, no-prune policy above is this project's configuration, set by `ling-admin forge github-mirror --all-branches` on this fleet only.

### 3.3 Branch hygiene

- Agents push `<team>/<topic>` branches.
- Nothing is deleted automatically, since branches are wanted on GitHub.
- A weekly forge issue lists branches idle for 30 days, for the owner's information only.

## 4. Runners

### 4.1 Install

- Forgejo Runner in its own container on every node, pinned by digest, registered with the forge of record at node install, with the labels in §1.
- Jobs run in Docker with per-label caps (CPU jobs: 8 GiB, 8 CPUs by default), with the NVIDIA runtime available only to `gpu` jobs.
- The runner needs the Docker socket. That is its one privileged part, so it accepts jobs **only from repositories on the forge of record pushed by the owner's or the team accounts**, never from forks or other users.

### 4.2 CPU gates (O4)

- **The gate workflow:** `.forgejo/workflows/gate.yml` runs on every push of an agent branch, split into parallel jobs that land on different nodes:
  - pytest (the full suite);
  - the ruff and mypy ratchet;
  - the launcher, web and chat cargo tests in an export (never in `codex/`), with sccache;
  - the desktop `npm test`, plus e2e under Xvfb.
- **Merges:** the result is a commit status, and the merge step requires it green.
- **The effect:** gates that agents now run one after another on `gx10-1a2b` spread over four nodes, and the main node's CPU stays free for builds and the model server.
- **What stays on GitHub:** the release pipeline (`release.yml`) and the nightly release dry run, which need Mac, Windows and x86 runners and the release key in GitHub's `release` environment. The forge's gate comes in addition to them.

### 4.3 GPU jobs (later phase)

A job with `runs-on: [gb10, gpu]`:
- takes `flock ~/.ling-gpu.lock` on its node, logs a line in `~/.ling-gpu-holds.log`, and stops and restarts that node's production model server where one exists, as the engine windows do;
- runs at most 45 minutes;
- is put back in the queue while a night holds the node (`ling-swe-*` active, or a night claimed by the scheduler past its `not_before`).

On the main node, GPU jobs are off unless the owner turns them on, since its model serves the owner.

## 5. Safety

### 5.1 Host safety

See §2.6. Runner jobs inherit their label's caps and the runner's cgroup, and the main node's runner takes CPU jobs only, at a lower CPU weight than the model server.

### 5.2 Disk

**Measured, 2026-10-27:** the repositories are small.
- `mightling` packs to 24 MiB and `ling-engine` to 5 MiB.
- The `codex` fork is a few hundred MiB with upstream's full history.
- The pre-rewrite bundles are 44 MiB.
- `mightling` grew by about 1,500 commits in the last 30 days, a few MiB of pack.

What grows is everything around the repositories: job logs, artefacts, dumps, and Forgejo's own indexes.

**Budget on the forge of record: 10 GB in all**, because `gx10-1a2b` runs near its floor (150 GB, and 175 GB during a SWE-bench night):

| Item | Cap |
|---|---|
| Repositories and indexes | 2 GB (alarm at 1 GB, about 10× today) |
| Job logs and artefacts | 3 GB, expiring after 7 days on the forge of record, 14 elsewhere; logs only, no build outputs as artefacts |
| Local dumps | Only the latest, about 1 GB; the 7 daily and 4 weekly copies live on two other nodes, never on the forge of record |
| Headroom | The rest |

- **Enforcement:** `ling-admin forge status` reports the forge's own use. When the forge of record's node drops below its floor plus 10 GB, the forge prunes artefacts first and then logs, and refuses new artefacts until it's back above.
- **Mirror nodes:** they keep mirrors only.
- **If the budget stops fitting:** `forge move` is the remedy, to a node with room (§6.1).
- The runner refuses jobs while its node is below its floor: 150 GB, or 175 GB on a node running a SWE-bench night.
- Job workspaces are deleted after each job.

### 5.3 Private repositories can't leave (O6)

- **Server-side rule:** a private repository can't get a push mirror. The forge's config lists the never-mirror repositories (`archive-pre-rewrite`, `memos`, and any repository marked private), and a `pre-receive` hook on them refuses any push whose target remote isn't the forge itself. The GitHub mirror job (§3.2) skips them by name and by visibility, and fails closed if it can't read a repository's visibility.
- **The archive:** `archive-pre-rewrite` is imported once from the bundles in `~/backups/rewrite-2026-10-26/` and `-10-08/`, readable by `owner` and `forge-admin` only. It's never cloned by agents, and no runner can check it out: jobs for private repositories are refused. **One exception (the owner, 2026-10-27):** a checkout `ling web` makes at the owner's request from a phone chat, read-only for an archived repository (specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §5.4).
- **The test:** `tests/test_forge_private.py`, part of the suite, runs against a throwaway Forgejo in Docker (marked like other container tests, never the real forge). It:
  - creates a private repository;
  - tries to add a push mirror through the API, and asserts refusal;
  - runs the GitHub mirror job against a local bare "GitHub" stand-in, and asserts the private repository's refs never arrive;
  - flips the visibility check to fail, and asserts the job stops instead of pushing.

  A second check runs nightly on the real forge: it lists every repository's mirrors and fails loudly if a private repository has one.

### 5.4 Egress

- **Outbound connections:** the forge's only ones are the GitHub mirror (this project) or the push mirrors the owner configured (product), and image pulls at upgrade. With no mirror configured, the forge makes no outbound connection.
- **The audit:** `ling-admin audit egress` lists the forge as a declared node service, like the Signal bridge. It names each configured mirror's destination, so a reader of the audit sees exactly where code can go.
- **At `/airgapped on`:** outbound mirrors stop, and the audit says so.

## 6. Backups, moving the role, and failover

- **Nightly backup:** `forgejo dump` on the forge of record (06:30 local by default), copied by name over SSH to two other nodes. Each repository is also kept as a `git bundle --all`. Seven daily and four weekly copies are kept. Private repositories are in the dump; that's the only way they leave the main node, and only to the owner's own nodes.
- **Restore test:** monthly, a mirror node restores the latest dump into a scratch instance and compares every ref.
### 6.1 Moving the role (planned)

`ling-admin forge move <node>` hands the forge of record to another node while both are up:
1. Back up: a `forgejo dump` on the current record, copied to the target and to one more node.
2. Freeze writes: the current record goes read-only for pushes, with a banner in the UI, and in-flight Actions jobs are drained.
3. Copy: the target restores the dump into its instance, which until now was a mirror, and its pull mirrors become writable repositories. Private repositories arrive from the dump, never through a mirror.
4. Check: every ref on the target is compared with the frozen record. The move stops and unfreezes the old record if anything differs.
5. Flip: the target takes `role=record` on mDNS. The old record becomes a mirror of the new one; its data is kept, not deleted. The other nodes' mirrors, `ling web`'s `/forge/` proxy on every node, the runners' registration, the GitHub mirror job and the paired clients' `forge` remotes are re-pointed.
6. Keep: the pre-move dump stays on two nodes for 30 days, and `forge move --back` within that time reverses the move the same way.

The move never deletes data on either node. It takes minutes for repositories this size; pushes during the freeze are refused with a message to retry.

### 6.2 Failover (unplanned)

- **`ling-admin forge promote <node>`:**
  - restores the latest dump on that node;
  - turns its mirrors into writable repositories;
  - takes `role=record` on mDNS;
  - re-registers the runners;
  - re-points `ling web`'s proxy and the other nodes' mirrors.

  Pushes after the last dump are lost from the forge, but not from agents' worktrees, which push again.

## 7. Commands

`ling-admin forge`, beside `node`; other nodes are reached over the existing SSH forced command:
- `status`: role, mirror freshness, runner health, last backup, GitHub mirror state;
- `on` / `off` (§2.5);
- `move <node>` / `move --back` (§6.1), and `promote <node>` (§6.2);
- `backup`, and `restore --check`;
- `key add <file>`;
- `github-mirror --all-branches | --off` (this project's fleet: §3.2);
- `password --show`, which prints the owner's password again, on the node's own terminal only.

`ling-admin node install|enable` sets the forge up; `node install --no-forge` skips it. Agents use plain git (`git push forge <team>/<topic>`) and the Forgejo API with a team token for statuses and pull requests. AGENTS.md gets at most two lines pointing to `docs/dev/forge.md`.

## 8. Phases (building waits for the owner's go)

| Phase | Delivers | Gate |
|---|---|---|
| **P0** | Forge of record on the main node through `node install`: pinned image, generated accounts (password shown once), `ling web` proxy sign-in, LAN-only binding, off switch, the side-service memory allowance | Fresh install on a scratch node: the password is shown once, and the paired desktop opens the forge signed in; nothing listens on non-advertised interfaces; host-safety tests pass |
| **P1** | Mirror nodes set up automatically; `move` and `promote`; nightly dumps off-node and the restore test; the storage budget | A `move` there and `--back` keeps every ref; a promote drill finishes in 15 min; the budget prunes before the node's floor in a filled-disk test |
| **P2** | This project's GitHub mirror of every branch (§3.2); the server-side anonymity hook; `archive-pre-rewrite` and `memos` imported; `tests/test_forge_private.py` | A seeded denylist entry is refused at push; the private test passes; a week of agent work reaches GitHub with no leak and no deletion |
| **P3** | Runners on every node; CPU gates moved (§4.2) | Gate results match a local run on three sample branches; the main node's daytime CPU load drops measurably |
| **P4** | GPU jobs under the lock (§4.3); the Night Shift results-as-pull-requests view; the egress audit naming mirrors | No GPU job overlaps a night or another lock holder in a week |

## 9. Not done here

- Multi-writer replication.
- Moving releases off GitHub.
- Reaching the forge outside the LAN (O5): a later decision with remote access.
- Forgejo's package registry: images stay on GHCR.
