# Remote access: the NetBird overlay

Developer notes behind the remote-access line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md` (the owner's design of 2026-10-10, built that week on `remote/vpn-mvp`, reconciled with main on 2026-10-20 as `remote/overlay`; spec §14).

## What it is

A client off the LAN reaches its node **by name** over a NetBird overlay whose management, signal and private DNS run on the node, under the node's own name-constrained CA, except that the control plane presents a public Let's Encrypt certificate since 2026-10-28 (below). One rented box runs only NetBird's relay (with STUN) and haproxy on port 443, which routes the box's own server name, unopened, to an SSH tunnel the node dials out. The box terminates no TLS for the control plane and holds no key of the overlay.

- **The box name:** `remote.dreamference.ai` (one name). Control plane at `https://remote.dreamference.ai:443` through haproxy and the tunnel; relay at `rels://remote.dreamference.ai:33080` (TCP and UDP), STUN on 3478/udp.
- **The node's name in the overlay:** `mightling-<node id>.netbird.selfhosted`, so the name carries the node's id.
- **Pins:** NetBird 0.80.0, the release Phase 0 ran live on (2026-10-12/13). The 0.81.3 values read on 2026-10-20 are in spec §14.3 for the next bump, which also needs one relay extraction on the node to pin the binary's SHA-256.

## The code

- **Node** (`dreamference/remote/`): `RemoteSettings` (paths, pins, `remote.json`), `RemoteCertificateAuthority` (`openssl`, name-constrained to the CA scope and `netbird.selfhosted`), `RemoteControlPlane` (the container, its configuration, the loopback proxy, the management API), `RemoteBox` (probe, relay extraction, haproxy and the relay over SSH, the port-443 check), `RemoteTunnel`, `RemoteNodePeer`, `RemoteEnrolment` (the code, HMAC both ways, the LAN listener on 3190), `RemoteAccess` (the commands). Only `RemoteAccess` prints (a per-file `T201` ignore, console-facing); the others report failures through their module logger, which prints the bare line to standard error.
- **Launcher** (`ling-rs/`): `remote_join.rs` (`ling node remote join`), the overlay tier in `node.rs`, the overlay name in `NO_PROXY`, and the `remote` field in both byte-identical locator copies.
- **The public certificate** (`RemotePublicCertificate`, spec §15): lego, CAA, the copy into `server/` and the container restart, and the renewal timer.
- **Commands:** `ling-admin remote setup <dns-name> [--ssh <alias>] [--ca-scope <domain>] [--box-only] [--yes]`, `status`, `remove [--purge]`, `code`, `code --bundle-file <path>` (one ephemeral peer's bundle, one-use 10-minute key, written to a new 0600 file with no code and no LAN listener: for the off-LAN acceptance runner), `peers`, `revoke <peer>`, `certificate issue [--staging]|renew|off`.

## Fixed on 2026-10-20 (ported from the Phase 1 that was reverted)

- **`server.store.encryptionKey`** is generated once (`remote/store-key`, 0600) and written into the configuration. The 10-13 build left it out; the server then made a new key at every start, and after the node's restart of 2026-10-14 management answered the saved token with `401 token invalid`.
- **`NB_SETUP_PAT_ENABLED`** is set only for the one start that creates the owner (`_control_plane_token`); the container is started plainly, restarted with it for `/api/setup`, and plainly again.
- **The owner's password** is kept (`remote/owner-password`, 0600): the way back into the API if the token is ever lost. The 10-13 build discarded it, so today's lockout had no recovery.
- **The first start fetches the geolocation databases** (found in the migration, 2026-10-27): NetBird's server will not start without `GeoLite2-City_*.mmdb` and `geonames_*.db` in its data folder, and with no route out it cannot fetch them (a restart loop). When they are missing, `RemoteControlPlane.start` runs the pinned image once on Docker's default bridge, no port published, no setup token, until NetBird has downloaded them and checked them against the SHA-256 it publishes beside them; then it removes that container and starts the real one on the internal network. That is the only start with a route out.
- **The node's root script resets a running client** (found in the migration, 2026-10-27): a NetBird daemon that was already running when the CA changed keeps the old roots (Go loads the system pool once per process), so its login to the new management failed (`LoginFailed`), and a profile from the earlier control plane made `netbird up` answer "Already connected". `node-peer.sh` now installs the CA only when it differs, restarts the daemon when it did, and runs `netbird down` before `up` (the script runs only while the node is not yet a peer of this control plane). The client's `ling node remote join` has the same gap for a client whose NetBird daemon is already running; not fixed yet.
- **`ling web` accepts the overlay name** (found in acceptance 1, 2026-10-27): its Host allow-list held the interface IPv4s taken at start (stale after an overlay address change) and the host name, so a request by the overlay name got 421. `ling web --lan` now also accepts the overlay name from `remote/remote.json` once `node_peer` is true.
- **No route out:** the container is on the internal Docker network `dreamference-remote` (172.31.232.10), reached through the user units `mightling-remote-control.socket`/`.service` (`systemd-socket-proxyd`) on `127.0.0.1:33443`, the port the tunnel already forwards to; the tunnel unit is unchanged.
- **Setup keys in files:** the node's `netbird up` and the client's use `--setup-key-file` (0600, deleted after), never `--setup-key` on a command line.
- **The CA scope is a setting:** `--ca-scope`, by default the parent of the box's name (`remote.dreamference.ai` → `dreamference.ai`), recorded in `remote.json`. A later box under the same domain needs no new CA.

## DNS the owner creates (Cloudflare, zone `dreamference.ai`)

| Name | Type | Content | Proxy |
|---|---|---|---|
| `remote.dreamference.ai` | A | the box's external IPv4 address (the provider's console) | **DNS only** (grey cloud) |

Cloudflare's proxy must stay off: it would terminate TLS (the node's CA would never be seen) and it does not carry 33080 or UDP. No second name is needed while the relay is on its own port; `relay.remote.dreamference.ai` would only matter if the relay ever moved to 443 under a name of its own. The address must be a **reserved static** address at the provider, or a stop and start of the VM moves it and the record points nowhere (an ephemeral address can be promoted to static in place, keeping its value).

## Hard limits

- No agent changes the box or the node's networking without the owner's go. On the box, services that are not remote access's are never touched; remote access owns only `/opt/mightling-relay`, `mightling-relay.service`, haproxy's configuration (and the package if its manifest says it installed it), the sshd drop-in and the account `mightling-tunnel`.
- No address is stored for a node: the overlay's name is the point. The last-address fallback of `node.json` stays (the owner kept it).
- The suite stays offline: SSH, sudo, Docker and systemd are behind `_run` seams, conftest refuses the real ones and a real `netbird`.

## Migration of the 10-13 deployment (approved to run automatically, 2026-10-20)

The live state on the node and the box dates from 10-13: CA constrained to the provider's name for the box, the control plane's store encrypted with a key that is gone, no client enrolled. Moving to `remote.dreamference.ai` therefore costs nothing but the node's own peer. Conditions: the merged build passes its gate and is on main; no SWE-bench night runs on gx10-1a2b (`systemctl --user list-units 'ling-swe-*'` empty); the DNS record exists and resolves to the box; pins stay 0.80.0. Steps, each reported to main:

1. **Back up:** on the node, `~/.config/dreamference/remote/` and the tunnel unit into a dated 0600 archive under `~/.local/state/dreamference/remote-backups/`; on the box, read as root into a dated archive in `/root/`: `/opt/mightling-relay/`, `/etc/haproxy/haproxy.cfg`, `/etc/systemd/system/mightling-relay.service`, `/etc/ssh/sshd_config.d/mightling-tunnel.conf`, `~mightling-tunnel/.ssh/`.
2. **Node, start afresh:** stop the tunnel unit; the node's peer leaves (`netbird down`); the old control plane container and its data are removed; the old remote folder is moved into the backup (nothing deleted). The old CA leaves the system trust store with `remote setup`'s replacement in step 3.
3. **`ling-admin remote setup remote.dreamference.ai --ssh <box> --yes`** from main: a new CA scoped to `dreamference.ai`, the control plane on its internal network, the box reconciled under the new name (haproxy's server name, the relay's certificate and exposed name), the tunnel, the node as `mightling-<id>` again. The node's half needs root once (the new CA into the system trust store, NetBird's client brought up by the root script), and sudo on gx10-1a2b asks for a password (checked 2026-10-20), so this step is the owner's to run at a terminal, or `setup --yes` stops at the node's peer and says so. Everything before it (backups, the box, the control plane, the tunnel) needs no sudo.
4. **Check:** `ling-admin remote status` all green (box units, port 443 under the new name, certificates, tunnel, control plane, peers 1/1); `openssl s_client` on 443 and 33080 under the new CA.

## The off-LAN acceptance (daily, 2026-10-28)

The check from outside the LAN runs on GitHub-hosted runners, not a phone, so nobody switches a network by hand.

- **Where:** the private repository `dreamference/remote-acceptance` (private because its logs carry timings and a run's shape; the public repository's logs are public). Its workflows are `workflow_dispatch` only: `linux.yml` (ubuntu-latest, x64; input `relay` forces the box's relay with `NB_FORCE_RELAY`) and `android.yml` (an Android 14 emulator through `ReactiveCircus/android-emulator-runner`, pinned by commit, NetBird's app by release and SHA-256).
- **The driver, on the node:** `scripts/remote_acceptance/run.sh [--workflow linux|android] [--relay] [--notify]` revokes any runner peer left from an earlier run, mints a bundle with `ling-admin remote code --bundle-file` (one-use key, ten minutes, ephemeral peer) into a 0600 file, sets it as the repository secret `ACCEPTANCE_BUNDLE`, deletes the file, dispatches, deletes the secret as soon as the job's first step has read it, polls until the run completes, prints its `ACCEPT` lines, revokes the runner's peer and checks that it is gone. A peer enrolled during the run under any other name is named, never revoked. Runner peers are recognised by name: `offlan-runner-linux` (the Linux runner's `--hostname`), `sdk_gphone…` (the app names a peer after `Build.MODEL`).
- **Daily:** the user units `mightling-remote-acceptance.timer` (13:20 UTC; GitHub's own schedule does not fire for us) and `.service` (`mightling-batch.slice`, `Nice=10`), printed by `run.sh units`; `--notify` sends `owner-notify` on failure only.
- **The logs:** pass/fail lines and numbers only. Every field of the bundle, the node's overlay label, the runner's own overlay address and name and the node's overlay address are masked with `::add-mask::` before anything could print them, as a second guard to printing none of them.
- **What the runner may do:** the overlay's ACL lets every peer reach the node's ports, so the workflows do the checks above and nothing else, and leave the overlay at the end.
- **Results (2026-10-28):** Linux passed, P2P at about 152 ms and relayed at about 164 ms (spec §13.3, acceptance 2). Android, with the node's CA as a user certificate: enrolment fails on the certificate, and a control arm with the CA in the system store connects (spec §4.2). That led to the public certificate. `android.yml` now takes `ca_mode` (`none` by default: no CA of ours on the emulator; `user`: the old test). Android is not part of the daily run.
- **Found while building it:** `gh run watch` can return before the run ends; the driver polls the run's status instead, since nothing may be revoked while the runner could still join.

## The public certificate (2026-10-28, spec §15)

The owner decided that Android's app, which trusts only the system store, should not need our CA, so the control plane presents a Let's Encrypt certificate for `remote.dreamference.ai`. The relay and everything else keep the node CA.

- **Issue:** `ling-admin remote certificate issue` (`--staging` first to test: Let's Encrypt's staging CA, nothing served). lego 5.5.2 is pinned by SHA-256 in `RemoteSettings` (`LEGO_ARCHIVES`) and downloaded into `remote/acme/bin/`. DNS-01 runs in Cloudflare with `~/.config/dreamference/cloudflare-token`, passed to lego as `CF_DNS_API_TOKEN_FILE` and never printed. The account and certificate live in `remote/acme/` (0600/0700); the staging copies are in `remote/acme-staging/`.
- **CAA:** before every issue or renewal, the box's name carries `issue "letsencrypt.org; validationmethods=dns-01; accounturi=<account>"` for each local account, and `issuewild ";"`. Without it the box, which answers for the name on 443, could get a trusted certificate of its own. The record is rewritten whenever the accounts change (a `remove --purge` and a new account, say). `--purge` leaves it in DNS.
- **Serving:** the certificate and key are copied into `server/tls.crt` and `server/tls.key`, then `docker restart dreamference-remote`, because NetBird's combined server reads the files only at start. The command waits until loopback 33443 presents the new leaf. Peers' tunnels carry on: in a 0.2 s ping over the overlay across the switch, none was lost.
- **Renewal:** `mightling-remote-certificate.timer` runs daily, `.service` in `mightling-batch.slice`, `ExecStart=%h/.local/bin/ling-admin remote certificate renew`. It restarts the control plane only when lego got a new certificate (renewal under 30 days, or earlier when the CA's renewal information asks).
- **Status:** `remote status` prints "Public cert: <issuer>, valid N more days" and "Renewal: <timer state>", and checks 443 against the system store **and** the installed leaf's SHA-256.
- **Rollback:** `ling-admin remote certificate off` serves the node CA's certificate again and disables the timer. Linux and macOS clients that installed the CA keep working; Android loses management.
- **Seen on the switch:** the node's own calls to management (`RemoteControlPlane._request`) used to trust only the node CA, so the old code's `remote code`, `peers` and `revoke` fail against the public certificate. They now trust the system store and the node CA. Any checkout running `ling-admin` must have the new code before the switch.
- **The relay** keeps the node CA's certificate, and NetBird's relay client also verifies against the system store, so Android has no relayed path (spec §15.4). On the emulator, with no CA of ours (run of 2026-10-28), the app enrolled and showed Connected, but pings to the node got 0/5 replies: both relay transports failed with `x509: certificate signed by unknown authority`, and peer to peer did not come up from behind the emulator's NAT.

## Live-test plan after the migration (needs a client)

1. Enrol the laptop on the LAN: `ling-admin remote code` on the node, `ling node remote join --code …` on the laptop; `node.json` gains `remote` and no address.
2. Acceptance 1: the laptop on a phone hotspot, `ling exec "Reply with exactly one word: pong"`.
3. Acceptance 2: `tcpdump` on the box during step 2 (TLS on 443 under `remote.dreamference.ai`, WireGuard if relayed, SSH).
4. Acceptance 3: `remote revoke <laptop>`; the laptop's next `ling exec` fails; `join` off the LAN refused.
5. Acceptance 4: `ling-admin audit egress` with the declared exception named.
6. The Mac, then phones by hand (spec §4.2).
7. Matrix on either overlay (the last phase): NetBird by default for a new homeserver, Tailscale kept as the option.
