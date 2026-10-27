# Mightling Forge: a Forgejo instance on each GB10 node

**Status:** proposed 2026-10-27; nothing built. The owner asked for "a spec for running Forgejo on each puffin node" (the GB10 machines of the development fleet). The open choices are in §0.1.

**Names:** post-rename (`ling`, `ling-admin`, `~/.mightling`). Machines are written with the repository's placeholders: `gx10-1a2b` (the primary development node), `node-b` (the engine development box), `node-c` and `node-d` (the two newer GB10s). The real names and addresses are never written here; nodes are resolved by name at use time, as everywhere else ([NODE](./DREAMFERENCE_MIGHTLING_NODE.md)).

**Builds on:**
- [FLEET](./DREAMFERENCE_MIGHTLING_FLEET.md): provisioning a GB10 from an existing node, over SSH, one forced command;
- [NODE](./DREAMFERENCE_MIGHTLING_NODE.md): name resolution, the trusted-LAN decision, jobs in capped user units;
- [SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) §30 and the night scheduler: nights own a machine's GPU and disk;
- [ORGANISATION](./DREAMFERENCE_ORGANISATION.md): the 15 team roles and their bots;
- `docs/dev/host-safety.md`: anything running beside a resident model server bounds its memory;
- the anonymity rules (`tests/test_anonymity.py`, the local denylist, the pre-push guard).

**Why now.** Three things happened in one week:
- **An identifier leaked.** An agent's working branch put a real host name into a spec on public `main`; the history rewrite of 2026-10-26 had just removed such names.
- **Agents' branches clutter GitHub.** After that rewrite, about 200 local branches still pointed at the old history and had to be moved by hand.
- **A release download was refused.** GitHub's download limit stopped a release download on a test box, and the release runs found Windows failures one run at a time, because no CI machine has a GB10 or a GPU.

A forge at home, on the machines that already run day and night, addresses all three: work in progress stays private, only reviewed merges reach GitHub, clones and fetches stay on the LAN, and GB10s can run CI jobs no hosted runner can.

---

## 0. Decisions, stated first

1. **Forgejo, pinned.** Forgejo (the community fork of Gitea: one Go binary, a web UI, git over SSH and HTTP, issues, pull requests, mirrors, its own Actions runner) runs in Docker from `codeberg.org/forgejo/forgejo`, pinned by digest like every image the project runs ([DOCKER](./DREAMFERENCE_DOCKER.md)). The release line is the current long-term-support one at build time, checked on forgejo.org. On 2026-10-27 the latest stable is v15 (released 2026-04-16); stable lines get three months of fixes and the yearly first-quarter line gets long-term support.
2. **One forge of record, an instance on every node.** A single instance, on `node-c`, holds the repositories agents push to, the issues and the pull requests: the forge of record. Every other GB10 runs its own instance as a **read replica**: pull mirrors of the forge of record (and of GitHub), so clones, fetches and CI checkouts are local to the machine, and a replica can be promoted if the forge of record is lost (§6). Two writable instances never accept pushes to the same repository; Forgejo has no multi-writer replication, and this spec does not invent one.
3. **GitHub stays the public face and gets only reviewed merges.** Agents push working branches to the forge of record. A merge to `main` is pushed to GitHub by the merging step, through the existing pre-push anonymity guard, exactly as today. No push mirror sends branches to GitHub. The forge pulls GitHub's `main`, tags and releases every 10 minutes, so it never falls behind changes made on GitHub (release merges, the dry-run workflow's issues).
4. **Private repositories stay private.** The forge holds what must never reach GitHub: the pre-rewrite history (today only in bundle files under `~/backups/`), the memos folder, and scratch repositories. These have no push mirror at all, and the forge refuses to create one for them (§5.3).
5. **LAN only, no new exposure.** The web UI listens on the LAN (port 3000) and git over SSH on port 2222. Nothing is published through the remote-access relay unless the owner turns that on (§0.1, O5). The trusted-LAN decision of 2026-09-30 applies, so there is no TLS on the LAN. Every account has a password and SSH keys, and anonymous access is off.
6. **Bounded beside the model server.** Each instance runs in a container capped at 1 GiB of memory and 1 CPU share. It uses SQLite rather than a database server, since the forge has a handful of users, and keeps its data under `~/forge/` on the node. A runner job never starts a model server and never takes the GPU without the GPU lock (§4.3).
7. **Runners on every GB10, registered with the forge of record.** Each node runs a Forgejo Runner with labels for what it has (`gb10`, `arm64`, `gpu`, `cuda-13`, `docker`). CPU jobs come first (§4.2): the pytest gate, cargo tests in an export and the desktop npm tests, which today all run on `gx10-1a2b` and load it. GPU jobs come in a later phase and obey the GPU lock and the nights (§4.3).
8. **Team accounts, not GitHub Apps.** The forge has one user per team role (ORGANISATION's 15 roles) plus an admin. Agents commit and push as the team that owns the change, with no GitHub App registration needed. On the forge the bots exist the moment they are created. What reaches GitHub is still committed as GitHub sees fit today (dgxcoder, or a team's GitHub App once registered).

### 0.1 For the owner to decide

| # | Decision | Proposed |
|---|---|---|
| O1 | Instances on **every** GB10 (replicas, §2), or the forge of record alone with runners on the others | Every node, as asked: a replica is cheap (~300 MB RAM, a few GB of disk) and gives local checkouts and a standby |
| O2 | Which node holds the forge of record | `node-c`: 800 GB free, no production model server, already the night machine; git barely touches CPU or GPU |
| O3 | What agents push to the forge instead of GitHub | All working branches from now on. GitHub receives `main`, release branches and tags only |
| O4 | Whether CI moves from GitHub to forge runners | CPU gates for agent branches move to the forge, so gx10's CPU is freed. GitHub Actions keep the release pipeline and the nightly dry run, for the Mac, Windows and x86 platforms the GB10s don't have |
| O5 | Reachable outside the house through remote access | Not now; revisit with remote access |
| O6 | Pre-rewrite history imported into a private repository | Yes, as a private repository with no mirror, readable by the admin only |

---

## 1. What runs where

| Node | Forgejo | Runner labels | Notes |
|---|---|---|---|
| `node-c` | **forge of record** | `gb10 arm64 docker gpu cuda-13` | Night machine by night; engine test windows by day; the forge itself runs around the clock |
| `node-d` | replica | `gb10 arm64 docker gpu cuda-13` | From its provisioning (next week) |
| `node-b` | replica | `gb10 arm64 docker gpu cuda-13` | Engine development box: GPU jobs here only through the GPU lock |
| `gx10-1a2b` | replica | `gb10 arm64 docker` (CPU only by default) | Serves the owner's production model; GPU jobs off here unless the owner says otherwise |

The AMD box and the Mac mini can be added as runners later (`windows`, `x86_64`, `macos`), which would bring the platforms GitHub provides today in-house. That is out of scope here.

## 2. The instance on each node

### 2.1 Container

- **Image and identity:** `codeberg.org/forgejo/forgejo@sha256:<pinned>`, running as the node's user via `USER_UID`/`USER_GID`, so files under `~/forge/` belong to that user.
- **Mounts:** `~/forge/data:/data` and nothing else: no Docker socket, no home directory.
- **Ports:** `3000/tcp` (web) and `2222/tcp` (SSH), bound on the LAN interface only. The interface is resolved at start from the node's route, as `node-advertise` already does, never a stored address.
- **Limits:** `--memory 1g --cpus 1 --pids-limit 512`, plus `--restart unless-stopped`.
- **Discovery:** advertised on mDNS as `_forge._tcp` with the node id in its TXT record, like `ling web`. A client finds the forge of record by browsing for the record whose TXT says `role=record`.

### 2.2 Configuration (`app.ini`, generated)

- `[database] DB_TYPE = sqlite3`;
- `[server] ROOT_URL = http://<node>.local:3000/`, `SSH_PORT = 2222`, `START_SSH_SERVER = true` (Forgejo's built-in SSH; the host's sshd is untouched);
- `[service] DISABLE_REGISTRATION = true`, `REQUIRE_SIGNIN_VIEW = true`;
- `[repository] DEFAULT_PRIVATE = private`;
- `[mirror] DEFAULT_INTERVAL = 10m`;
- `[actions] ENABLED = true` on the forge of record only;
- outgoing mail off, no telemetry: Forgejo sends none, and the update checker is disabled (`[cron.update_checker] ENABLED = false`);
- `[security] INSTALL_LOCK = true`, secrets generated at setup into `~/forge/data/` (mode 0600). They are never written into this repository or into memory notes.

### 2.3 Accounts and keys

- **Admin:** `forge-admin`, with a random password written to `~/.config/dreamference/forge-admin` (0600) on the forge of record only.
- **Team users:** the 15 role names from ORGANISATION (`engine`, `models`, `agent`, `desktop`, …) plus `owner`. Each agent's SSH key is added to the team user it commits as.
- **Machine users:** `mirror` on each replica, with a read token for the forge of record, and the runners' registration tokens. All live in `~/.config/dreamference/forge/` (0600).

## 3. Repositories

| Repository | On the forge of record | Mirrors |
|---|---|---|
| `mightling` | Writable; agents push branches here | Pull mirror of GitHub `dreamference/mightling` (`main`, release branches, tags) every 10 min; each replica pull-mirrors the forge of record |
| `ling-engine` | Writable | Pull mirror of GitHub `dreamference/ling-engine`; replicas as above |
| `codex` | Read-only mirror | Pull mirror of GitHub `dreamference/codex` |
| `archive-pre-rewrite` (private, admin only) | Imported once from the bundles in `~/backups/rewrite-2026-10-26/` and `-10-08` | **None, ever** |
| `memos` (private) | The owner's memos folder | **None** |

### 3.1 Two sources of `main`

A writable `mightling` on the forge and a pull mirror of GitHub's `main` must not fight. The forge keeps GitHub's `main` as the branch `github/main`, refreshed by a pull of that ref every 10 minutes, a fetch-only remote inside the repository. The forge's own `main` is updated only by the merge step:
1. a merge is made on a fresh `github/main`;
2. it is pushed to GitHub `main`, through the pre-push guard;
3. on success, the same commit is pushed to the forge's `main`.

If GitHub refuses, the forge's `main` is not moved, so it never contains anything GitHub doesn't. A server-side `pre-receive` hook on the forge refuses any push to `main` whose commit is not already an ancestor-or-equal of `github/main` once refreshed. That makes the rule mechanical: `main` follows GitHub, and only branches are the forge's own.

### 3.2 Branch hygiene

- Agents' branches live on the forge as `<team>/<topic>`.
- A branch merged into `main` is deleted after 7 days. An unmerged branch idle for 30 days is listed in a weekly issue, never deleted automatically.
- The forge's history is never rewritten. GitHub's might be again, and a rewrite then only touches GitHub, while the forge keeps the old ids for reference in its archive.

## 4. Runners

### 4.1 Install

- Forgejo Runner in its own container, pinned by digest, registered with the forge of record with the node's labels (§1).
- It runs jobs in Docker (the `docker` executor) with the node's NVIDIA runtime available but not attached by default.
- Each job container is capped by the runner's config (memory, CPUs) per label.
- The runner's own container gets the Docker socket, which is what Forgejo Runner needs. This is the one privileged piece, and it is why the runner is configured to accept jobs **only from repositories on the forge of record** and **only from team users**, never from forks or other users.

### 4.2 CPU jobs (Phase 2)

A workflow in `.forgejo/workflows/gate.yml` runs on every push of an agent branch:
- `pytest` (the full suite);
- the ruff and mypy ratchet;
- the launcher, web and chat cargo tests in an export (never in `codex/`, as AGENTS.md says), through sccache;
- the desktop `npm test`, with e2e under Xvfb in the job image.

It runs on any node with the label `gb10` whose load allows it. The result is a commit status on the forge, and the merge step requires it green.

This moves the gates that agents now run one after another on `gx10-1a2b`, where they compete with builds and the model server, onto the four GB10s in parallel.

### 4.3 GPU jobs (Phase 3)

A job with `runs-on: [gb10, gpu]`:
- takes `flock ~/.ling-gpu.lock` on its node before touching the GPU, logs a line in `~/.ling-gpu-holds.log`, and stops and restarts the node's production model server around itself where one exists, as the engine agents' windows do;
- is limited to 45 minutes;
- is refused while a night holds the node: the runner reads the night scheduler's state (`ling-swe-*` active, or a claimed night after its `not_before`) and puts the job back in the queue.

This turns the engine's ad-hoc "windows" into ordinary CI jobs: exactness checks, agentlong identity and the vision gates, with logs and artefacts kept on the forge.

### 4.4 What stays on GitHub

- The release pipeline (`release.yml`) and the nightly dry run need the Mac, Windows and x86 runners GitHub provides, and the release key lives in GitHub's `release` environment. They stay on GitHub.
- The forge's gate is in addition to them, not a replacement.

## 5. Safety

### 5.1 Host safety

- A Forgejo instance and its runner idle at a few hundred MB.
- The caps in §2.1 and §4.1 bound them below what `check_host_safety()` assumes for side services.
- Runner job containers are capped per label and placed in the runner's own cgroup, so the PSI watchdog's kill path is unchanged.
- On `gx10-1a2b`, whose model serves the owner, the runner takes CPU jobs only, at a lower CPU weight than the model server's container.

### 5.2 Disk

- The forge of record keeps the repositories (a few GB), job logs and artefacts (expiring after 14 days) and the backups (§6).
- Each replica keeps mirrors only.
- Job workspaces are deleted after each job.
- The runner refuses jobs while the node is below its night disk floor: 175 GB on `gx10-1a2b`, 150 GB elsewhere.

### 5.3 Anonymity

- The forge is private, but what leaves it goes through the same guard as today: the merge step pushes to GitHub with the pre-push hook that runs `tests/test_anonymity.py` against the local denylist.
- A second, server-side check on the forge (a `pre-receive` hook running the same test) flags a branch that would fail the guard the moment it is pushed, days before its merge, so a leak is fixed while it is still private.
- Push mirrors are disabled forge-wide by configuration (`[mirror] DISABLE_NEW_PUSH = true`), so no repository can be set to copy itself out.
- The archive and memos repositories are private, admin-only, and listed in the forge's config as never-mirror.

### 5.4 Egress

- The forge's only outbound connections are its pull mirrors to GitHub (HTTPS, `github.com` and `codeload.github.com`) and the runners' image pulls from the registries the project already uses.
- It is not part of a `ling` session, so the egress audit's verdict is unaffected. The audit lists the forge as a declared node service, like the Signal bridge, so a reader of the audit knows it is there.

## 6. Backups and failover

- **Nightly backup:** `forgejo dump` on the forge of record at 06:30 UTC (after the nights' grading, before the engine's day windows), written to `~/forge/backups/` and copied to `gx10-1a2b` and `node-b` by name over SSH. Each repository is also kept as a `git bundle --all`. Seven daily and four weekly copies are kept.
- **Restore test:** monthly, a replica restores the latest dump into a scratch instance and compares the repository refs.
- **Failover:** if the forge of record is lost, a replica is promoted with `ling-admin forge promote <node>`:
  - it restores the latest dump;
  - turns its mirrors into writable repositories;
  - takes the mDNS `role=record`;
  - re-registers the runners.

  Work pushed after the last dump is lost from the forge, but not from the agents' worktrees, which push it again.

## 7. Commands

`ling-admin forge`, beside `node` and `night`, all over the existing SSH forced command for nodes other than the one running it:
- `setup <node> [--record]`: pull the pinned image, generate `app.ini` and the secrets, create the admin and team users, the mirrors and the runner;
- `status`: instances, roles, mirror freshness, runner health, last backup;
- `promote <node>`: failover (§6);
- `backup` and `restore --check`;
- `remove <node>`: stop and delete the instance, keeping its data folder unless `--purge`.

Agents use the forge through plain git (`git push forge <team>/<topic>`) and the Forgejo API with a team token for statuses and pull requests. A short section in AGENTS.md (at most two lines) points to `docs/dev/forge.md`.

## 8. For Mightling users later

The same commands could give a Mightling user a forge on their own node: private repositories on the GB10, Night Shift branches arriving as pull requests in a web UI, and the GPU as a CI runner. That would be a product decision with its own spec, covering the egress-audit wording, the default off state and the setup UX. This spec covers only the project's own fleet.

## 9. Phases

| Phase | Delivers | Gate |
|---|---|---|
| **P0** | The forge of record on `node-c`: pinned image, `app.ini`, admin and team users, pull mirrors of the three GitHub repositories, the nightly backup with a restore test | The mirrors are fresh within 10 min; a restore reproduces every ref; the memory cap holds under a clone storm (20 parallel clones) |
| **P1** | Agents' working branches on the forge; the `main` rule (§3.1) and the server-side anonymity check (§5.3); the private archive imported | One week of agent work with no branch pushed to GitHub except merges; the pre-receive hook catches a seeded denylist entry |
| **P2** | Runners on every GB10 with CPU gates (§4.2); replicas on the other nodes; `ling-admin forge status` | The gate's results match a local run on three sample branches; gx10's CPU load during the day measurably drops |
| **P3** | GPU jobs with the lock and night awareness (§4.3); the engine's windows as jobs | A GPU job never overlaps a night or another lock holder in a week of use; job logs replace the ad-hoc holds log as the record |
| **P4** | `promote` tested end to end; optional Windows, Mac and x86 runners | A drill: the forge of record is stopped, a replica is promoted, and agents continue within 15 min |

## 10. Not done here

- Multi-writer replication between instances. Forgejo has none, and none is needed with one forge of record.
- Moving releases off GitHub.
- Exposing the forge outside the LAN (O5).
- Forgejo's package registry. Images stay on GHCR, models wherever the hosting decision puts them.
