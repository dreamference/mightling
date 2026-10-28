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
- **Commands:** `ling-admin remote setup <dns-name> [--ssh <alias>] [--ca-scope <domain>] [--box-only] [--yes]`, `status`, `remove [--purge]`, `code`, `code --bundle-file <path>` (one ephemeral peer's bundle, one-use 10-minute key, written to a new 0600 file with no code and no LAN listener: for the off-LAN acceptance runner; with `--ttl 2h` an ordinary peer's key that waits up to 24 h, still one use; with `--join` the one-line install-and-join for a machine at another site, see the section at the end), `peers`, `revoke <peer>`.

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
- **Results (2026-10-28):** Linux passed, P2P at about 152 ms and relayed at about 164 ms (spec §13.3, acceptance 2). Android, with the node's CA as a user certificate: enrolment fails on the certificate, and a control arm with the CA in the system store connects (spec §4.2). Android is not part of the daily run.
- **Found while building it:** `gh run watch` can return before the run ends; the driver polls the run's status instead, since nothing may be revoked while the runner could still join.

## Live-test plan after the migration (needs a client)

1. Enrol the laptop on the LAN: `ling-admin remote code` on the node, `ling node remote join --code …` on the laptop; `node.json` gains `remote` and no address.
2. Acceptance 1: the laptop on a phone hotspot, `ling exec "Reply with exactly one word: pong"`.
3. Acceptance 2: `tcpdump` on the box during step 2 (TLS on 443 under `remote.dreamference.ai`, WireGuard if relayed, SSH).
4. Acceptance 3: `remote revoke <laptop>`; the laptop's next `ling exec` fails; `join` off the LAN refused.
5. Acceptance 4: `ling-admin audit egress` with the declared exception named.
6. The Mac, then phones by hand (spec §4.2).
7. Matrix on either overlay (the last phase): NetBird by default for a new homeserver, Tailscale kept as the option.

## A new machine at another site (the owner's country house, from 2026-10-29)

New GB10s and a laptop that are not on the node's LAN and have never had `ling`. The LAN enrolment (`ling-admin remote code`, port 3190) cannot reach them, so each joins with **one line** and the coordinator then provisions the GB10s over the overlay. Spec §4.5.

### What the coordinator does

1. **Mint one bundle per device**, just before the owner starts (a key waits at most 24 hours, one use each, and expires by itself if unused, which is how an unused key is revoked; nothing runs on the control plane but the management API's key creation). On the node, with the owner's account on the main machine for the `ssh` alias:

   ```bash
   ling-admin remote code --bundle-file ~/join-house-1.txt --join --ttl 12h --name house-1 --ssh-user <account on the main node>
   ling-admin remote code --bundle-file ~/join-house-2.txt --join --ttl 12h --name house-2 --ssh-user <account on the main node>
   ling-admin remote code --bundle-file ~/join-laptop.txt  --join --ttl 12h --name laptop-house --ssh-user <account on the main node>
   ```

   Each file is 0600 and holds one line: `sudo bash -c "$(printf %s '<base64 of the join script>' | base64 -d)"`. The script carries the management URL, the single-use key and (only while the control plane has no public certificate) the node's CA. It is secret until its key is spent or expires. `--join` implies an ordinary (not ephemeral) peer; without `--join`, `--ttl` writes the JSON bundle.
2. **Send each line to the owner over Signal's Note to Self** (end-to-end encrypted, on devices the owner controls), one message per device, named by device; then delete the files. Never put a line in a URL, an e-mail, a chat bridge's log, a ticket or the repository. The script never traces itself and passes the key to NetBird as `--setup-key-file`; it downloads only NetBird's own release archive.
3. After "tell the coordinator": `ling-admin remote peers` shows the device connected. From the main node: `ssh house-1.netbird.selfhosted` (the device's overlay name is `<name>.netbird.selfhosted`, resolved by the overlay's DNS, never an address).
4. **Provision each GB10** (the password is typed once; `--role night` leaves key-only SSH):

   ```bash
   ling-admin node provision house-1 --user <account> --role night
   ling-admin node provision house-2 --user <account> --role night
   ```

   A bare name is tried as `<name>.netbird.selfhosted` before `<name>.local`; the pairing record keeps `overlay_name`, so every later connection (`node run`, lanes, `sync-model`, Night Shift) resolves that name and never browses mDNS. A node that is on the LAN as well still falls back to the browse. Steps 1 to 10 of "Provisioning a node with a role" in [node.md](node.md) run unchanged: nothing in them needs the LAN (the advert `node enable` publishes goes to the house LAN and is harmless).
5. **Check the path before the big copy:** `netbird status -d` on the main node must show the new peer as `P2P`, not `Relayed`. A relayed path sends 24 GB through the rented box.

### The model copy over the internet

- **Size:** the default model (`qwen3.8-27b-nvfp4-dflash2` on SGLang) is **22.4 GiB** (the base weights, the draft model and the embedding model; `FleetModelPlan.bytes_needed`). The SGLang and SearXNG images are pulled by each box from the registry over the house line (not from here).
- **Time** is the main node's *upload* speed, 192 Gbit plus about 10 % of protocol overhead:

  | Upload from the main node | One box | Both boxes (copied twice) |
  |---|---|---|
  | 20 Mbit/s | about 2 h 50 | about 5 h 40 |
  | 50 Mbit/s | about 1 h 10 | about 2 h 20 |
  | 100 Mbit/s | about 35 min | about 1 h 10 |
  | 300 Mbit/s | about 12 min | about 24 min |

  Measure the real upload first (`speedtest-cli` or a 1 GB `rsync` test). With a slow upload, copy to one box and let the second take it from the first over the house LAN (`ling-admin node sync-model <other>` run there after `node add`), or `ling-admin model download` at the house, which uses the house's download speed and the pinned revision.
- **Resume:** the copy is `rsync -aH --partial --append-verify --timeout=120` with an ssh keep-alive (15 s, 4 misses). When the connection drops (rsync exits 10, 12, 30, 35 or ssh's 255) it is started again after 20 s, up to 30 times, carrying on from the partial file; any other exit is a real failure. If the whole run stops anyway, run the same `node provision` again: finished steps are skipped and the model carries on from the partial file. A folder counts as present once its `snapshots/` exists, which rsync sends after `blobs/`. `node sync-model` (a tar stream) has no resume: use `provision` for the big copy.

### Reaching the main node from the new machines

The join script adds, for the user who ran `sudo`, a block to `~/.ssh/config`: `Host gx10-remote`, `HostName` the main node's overlay name (`mightling-<id>.netbird.selfhosted`), and `User` when `--ssh-user` was given. The first `ssh gx10-remote` asks for the owner's password (the main node still allows it); then `ssh-copy-id gx10-remote` installs the key. Lock the main node's password login afterwards if the owner wants.

### Power-cycling a hung node from outside (smart plugs)

`ling-admin node power-cycle <node> [--off-seconds 20] [--wait-minutes 10] [--force]` switches the node's smart plug off, waits, switches it on and waits for the node to answer over its pairing. Code: `node_power_cycle.py`, `node_shelly_plug.py`, `node_power_settings.py`; tests `tests/test_node_power.py` (no socket, no ssh).

- **Which plug:** `~/.config/dreamference/node-power.json` (never in the repository), two tables. `plugs`: the plugs *this machine can reach*, by the owner's name (`kind`, `host`, `channel`, `generation`; the host a name such as `shellyplug-aabbcc.local`, never an address). `nodes`: per paired node, `{"plug": ..., "via": <paired node>}`. Set with `ling-admin node power-config plug <name> --host <name> [--generation 1|2]` and `power-config node <node> --plug <name> [--via <node>]`; `power-config show` prints both.
- **Reaching a plug at the other site, chosen design:** one GB10 acts for the other (`via`). The plug is on the house LAN, which the main node cannot reach. Each GB10 there is on that LAN and on the overlay, so the plug of house-1 is configured **on house-2** (`power-config plug house-1-plug --host ...` run there) and house-2's pairing answers a new forced-command operation, `power-plug <plug name> <on|off|status>`. It looks the *name* up in its own table: a sender can switch a plug the neighbour already knows and cannot make it contact any host. Each GB10's plug is therefore driven by the other, and a hung GB10 can be cycled while its twin is up. No control plane change, no new route, no stored address.
- **The alternative, not built:** a NetBird network route that makes one GB10 a routing peer for the house LAN's CIDR, so the main node reaches the plug directly (`plugs.host` in its own table, `via` unset). It needs IP forwarding and masquerading on that GB10, a route and a policy created in management, and the plug's address (or the house router's DNS for it) to be stable; it exposes the whole house LAN to every peer. Worth it only if more than plugs have to be reached.
- **Safety:** it refuses to cut power while the node still answers (`--force` overrides); it reads the plug before and after each command and stops if the plug did not change; once the plug is off, it retries `on` five times and says so loudly if it cannot (a plug left off by this command is worse than a hung node). The plug's local API has no authentication here: keep the plug on the house LAN behind its router, with Shelly's cloud and any access from outside switched off.
- **Before relying on it:** (1) set the plug's "power on default" to **on**, so it restores power after an outage; (2) test once, in person, that the GB10 starts by itself when mains returns (its firmware setting for power restore); if it does not, the plug alone cannot revive it; (3) one real `power-cycle --force` on a idle box.
- **Plugs to buy** (Gen2 or newer, local HTTP RPC with no cloud, one switch with power metering, 12 A or 16 A against the GB10's under 250 W): **Shelly Plug S Gen3** (or Shelly Plus Plug S) in the house's socket type (Schuko, UK, US variants exist; check the variant for the country), Wi-Fi 2.4 GHz. Two plugs, one per GB10. Second choice, not implemented: TP-Link Kasa (KP115/KP125 style, a local protocol that python-kasa speaks) and Tapo P110 (local KLAP protocol that needs the Tapo account's credentials); both are less open than Shelly's plain HTTP, so they would be new plug kinds in `NodePowerSettings.KINDS`. Avoid plugs that work only through a vendor cloud, and Zigbee/Z-Wave ones (they need a hub). Confirm exact model names and variants at purchase.

### The owner's steps for the first day

The last section below. The plain-language copy for the owner is kept by the coordinator.

## New machine at another site: what the owner does

For the laptop and each of the two new GB10s, in that order. The coordinator sends the owner one line per device in Signal's Note to Self, named by device ("laptop-house", "house-1", "house-2"). Each line works once and stops working after 12 hours. The laptop has Signal Desktop (linked to the owner's phone, Note to Self visible); the GB10s are reached from the laptop, so no long line is typed or carried between machines.

1. **First boot of each GB10:** connect power and Ethernet to the house router (Wi-Fi works too). In the first-boot setup choose the account name the coordinator gave you (the same on both), a password you can type, and the computer name `house-1` (the other `house-2`). Let it reach the login screen. The machine needs Internet.
2. **The laptop first.** Open a terminal. In Signal Desktop, *Note to Self*, copy the "laptop-house" message in full, paste it into the terminal (it begins with a space and `sudo`), press Enter and type the laptop's password when asked. It prints `Joined the overlay as laptop-house. Tell the coordinator.` after about a minute.
3. **Tell the coordinator** ("laptop is in"). From now on `ssh gx10-remote` on the laptop reaches the main machine (it asks for the owner's password there); run `ssh-copy-id gx10-remote` once so the next login needs no password.
4. **Each GB10, from the laptop:** `ssh <account>@house-1.local` (password from step 1; if the name does not resolve, use the machine's address from the router's device list). In that session paste the "house-1" line from Signal Desktop, press Enter, type the password for `sudo`. Wait for `Joined the overlay as house-1. Tell the coordinator.`, then `exit`. Repeat for `house-2`.
5. **Tell the coordinator** which machines joined. The coordinator provisions the GB10s over the Internet; the model copy takes hours, so leave both powered and online. Do not paste a line anywhere but into those terminals; one that already worked is spent.

If a line fails or is older than 12 hours, ask the coordinator for a new one.
