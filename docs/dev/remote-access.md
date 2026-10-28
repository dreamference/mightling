# Remote access: the NetBird overlay

Developer notes behind the remote-access line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md` (the owner's design of 2026-10-10, built that week on `remote/vpn-mvp`, reconciled with main on 2026-10-20 as `remote/overlay`; spec §14).

## What it is

A client off the LAN reaches its node **by name** over a NetBird overlay whose management, signal and private DNS run on the node, under the node's own name-constrained CA. One rented box runs only NetBird's relay (with STUN) and haproxy on port 443, which routes the box's own server name, unopened, to an SSH tunnel the node dials out. The box terminates no TLS for the control plane and holds no key of the overlay.

- **The box name:** `remote.dreamference.ai` (one name). Control plane at `https://remote.dreamference.ai:443` through haproxy and the tunnel; relay at `rels://remote.dreamference.ai:33080` (TCP and UDP), STUN on 3478/udp.
- **The node's name in the overlay:** `mightling-<node id>.netbird.selfhosted`, so the name carries the node's id.
- **Pins:** NetBird 0.80.0, the release Phase 0 ran live on (2026-10-12/13). The 0.81.3 values read on 2026-10-20 are in spec §14.3 for the next bump, which also needs one relay extraction on the node to pin the binary's SHA-256.

## The code

- **Node** (`dreamference/remote/`): `RemoteSettings` (paths, pins, `remote.json`), `RemoteCertificateAuthority` (`openssl`, name-constrained to the CA scope and `netbird.selfhosted`), `RemoteControlPlane` (the container, its configuration, the loopback proxy, the management API), `RemoteBox` (probe, relay extraction, haproxy and the relay over SSH, the port-443 check), `RemoteTunnel`, `RemoteNodePeer`, `RemoteEnrolment` (the code, HMAC both ways, the LAN listener on 3190), `RemoteAccess` (the commands). Only `RemoteAccess` prints (a per-file `T201` ignore, console-facing); the others report failures through their module logger, which prints the bare line to standard error.
- **Launcher** (`ling-rs/`): `remote_join.rs` (`ling node remote join`), the overlay tier in `node.rs`, the overlay name in `NO_PROXY`, and the `remote` field in both byte-identical locator copies.
- **Commands:** `ling-admin remote setup <dns-name> [--ssh <alias>] [--ca-scope <domain>] [--box-only] [--yes]`, `status`, `remove [--purge]`, `code`, `code --bundle-file <path>` (one ephemeral peer's bundle, one-use 10-minute key, written to a new 0600 file with no code and no LAN listener: for the off-LAN acceptance runner), `peers`, `revoke <peer>`.

## Fixed on 2026-10-20 (ported from the Phase 1 that was reverted)

- **`server.store.encryptionKey`** is generated once (`remote/store-key`, 0600) and written into the configuration. The 10-13 build left it out; the server then made a new key at every start, and after the node's restart of 2026-10-14 management answered the saved token with `401 token invalid`.
- **`NB_SETUP_PAT_ENABLED`** is set only for the one start that creates the owner (`_control_plane_token`); the container is started plainly, restarted with it for `/api/setup`, and plainly again.
- **The owner's password** is kept (`remote/owner-password`, 0600): the way back into the API if the token is ever lost. The 10-13 build discarded it, so today's lockout had no recovery.
- **The first start fetches the geolocation databases** (found in the migration, 2026-10-27): NetBird's server will not start without `GeoLite2-City_*.mmdb` and `geonames_*.db` in its data folder, and with no route out it cannot fetch them (a restart loop). When they are missing, `RemoteControlPlane.start` runs the pinned image once on Docker's default bridge, no port published, no setup token, until NetBird has downloaded them and checked them against the SHA-256 it publishes beside them; then it removes that container and starts the real one on the internal network. That is the only start with a route out.
- **The node's root script resets a running client** (found in the migration, 2026-10-27): a NetBird daemon that was already running when the CA changed keeps the old roots (Go loads the system pool once per process), so its login to the new management failed (`LoginFailed`), and a profile from the earlier control plane made `netbird up` answer "Already connected". `node-peer.sh` now installs the CA only when it differs, restarts the daemon when it did, and runs `netbird down` before `up` (the script runs only while the node is not yet a peer of this control plane). The client's `ling node remote join` has the same gap for a client whose NetBird daemon is already running; not fixed yet.
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

## Live-test plan after the migration (needs a client)

1. Enrol the laptop on the LAN: `ling-admin remote code` on the node, `ling node remote join --code …` on the laptop; `node.json` gains `remote` and no address.
2. Acceptance 1: the laptop on a phone hotspot, `ling exec "Reply with exactly one word: pong"`.
3. Acceptance 2: `tcpdump` on the box during step 2 (TLS on 443 under `remote.dreamference.ai`, WireGuard if relayed, SSH).
4. Acceptance 3: `remote revoke <laptop>`; the laptop's next `ling exec` fails; `join` off the LAN refused.
5. Acceptance 4: `ling-admin audit egress` with the declared exception named.
6. The Mac, then phones by hand (spec §4.2).
7. Matrix on either overlay (the last phase): NetBird by default for a new homeserver, Tailscale kept as the option.
