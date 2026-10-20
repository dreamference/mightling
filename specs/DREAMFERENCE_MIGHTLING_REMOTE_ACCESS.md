# Mightling from outside the LAN: remote access (MVP)

**Status:** the user's design (2026-10-10); **merged into main on 2026-10-20** as `remote/overlay`, with the fixes and the owner's decisions of that day (§14: one box name, `remote.dreamference.ai`, under a CA scoped to `dreamference.ai`; the store's encryption key fixed; no route out for the control plane; setup keys in files); the migration of the 10-13 deployment and Matrix on either overlay are §14.4 and §14.5; the open questions answered by the user the same evening (§11); **built** on branch `remote/vpn-mvp` (§13), tested offline and in a local rehearsal on Docker (§12.2). **Phase 0's first half is done on a real box** (2026-10-12, §12.3): the box deployed by `remote setup --box-only` and its port 443 checked from the node. **Phase 0's second half is done on the node** (2026-10-13, §12.4): control plane, tunnel and the node's peer up, `remote status` all green, the relay reachable. The live acceptance with a client (§9, §13.3 from step 2) waits for the laptop. This document is the MVP: a client on a hotspot or a hotel network reaches its node by name, through an overlay the node runs itself and one rented machine that can see nothing. MVP clients are Linux, macOS, Windows and a phone (§4).

**Names:** post-rename (`ling`, `ling-admin`, `~/.mightling`, `ling web` on port 3100).

**Builds on:**
- [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md): the node, its advertisement (§4), the locator's tiers (§6.1), `node.json` (§6.2) and the rule that a client remembers a node by id and resolves it at use time, never from a stored address.
- [MIGHTLING_ASK](./DREAMFERENCE_MIGHTLING_ASK.md) §18.2: pairing by QR code and the order in which addresses are offered.
- [MIGHTLING_CHAT](./DREAMFERENCE_MIGHTLING_CHAT.md) §2, §5: the Matrix homeserver that reaches the phone over Tailscale today.
- [MIGHTLING_SIGNAL](./DREAMFERENCE_MIGHTLING_SIGNAL.md) §10: the pattern of a declared exception in the egress audit.
- [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md) and [MIGHTLING_AIRGAPPED](./DREAMFERENCE_MIGHTLING_AIRGAPPED.md): what the audit promises and what `/airgapped on` closes.
- [MIGHTLING_SWE_BENCH](./DREAMFERENCE_MIGHTLING_SWE_BENCH.md) §18: the model gate and the networks it admits.
- [SECURITY_REVIEW_2026-10](./DREAMFERENCE_SECURITY_REVIEW_2026-10.md): a sandboxed command can read the user's home folder.

---

## 0. Decisions, stated first

1. **The node is the control plane.** NetBird's management service, its signal service and its private DNS run on the primary node, beside the model, as NetBird's combined `netbird-server` container. They hold the network map, the peers' public keys and the enrolment keys, and they never leave the user's machine. NetBird is used as it is (its own client, its own protocol, version 0.80.0 pinned); Mightling writes no VPN and no protocol of its own.
2. **One rented machine, and it is untrusted.** The only thing outside the user's walls is a small cloud box that does two jobs: it runs NetBird's relay with its embedded STUN, for the moments two peers cannot reach each other directly, and it passes the public port 443 through, as plain TCP routed by server name, to the node. It terminates no TLS, holds no key of the network, and is deployed and removed by one command (§3). If it is seized, wiped or replaced, the attacker gets ciphertext and a chance to cause an outage (§1).
3. **TLS ends on the node, under the node's own certificate authority.** The node makes a private CA once and issues the control plane's and the relay's certificates from it. A client trusts that CA, and nothing else about the box's name is trusted. No public CA, no Let's Encrypt and no account anywhere is required. **The CA is name-constrained** to the box's DNS name and the overlay's domain: installed as a root on every client, it can vouch for nothing else, so a stolen CA key cannot impersonate another site to an enrolled machine.
4. **Enrolment happens on the trusted LAN, or not at all.** A client is admitted to the overlay only while it stands next to the node: the node's enrolment listener refuses any address that is not on a private network or that is in the overlay's own range (§4.1). After enrolment the client's WireGuard key is its identity; nothing it types later lets a stranger in.
5. **A client finds its node by the overlay name, never by an address.** The node's peer name is `mightling-<node id>`, so its overlay name, `mightling-<node id>.netbird.selfhosted`, is the node's own and carries its id. The locator gains one tier (§5). No address is stored anywhere in this design. Two names, two jobs: the **box's public DNS name** is the control plane's (management and signal at `https://<dns-name>:443`, through the pass-through, before the overlay exists, and the relay at `rels://<dns-name>:33080`); the **node's overlay name** is the data path's (the model server, `ling web`, Matrix, once the overlay is up).
6. **Nothing changes for a user who never runs the setup.** No installer step turns this on, no port opens, no unit, container or advertisement key appears, the audit's verdict is the same as today. `ling-admin remote setup` is the only way in and it says what it changes before anything is done and before any sudo.
7. **Matrix rides the same overlay.** The phone messenger's Tailscale leg ([MIGHTLING_CHAT](./DREAMFERENCE_MIGHTLING_CHAT.md)) becomes the remote-access overlay, and `tailscale serve` is replaced by the node's own TLS (§8). One overlay, one trust root, one set of commands.

---

## 1. Threat model: what the box can see and do

The box is a rented virtual machine that someone else administers, images, snapshots and can be compelled to hand over. The design treats it as hostile from the first minute.

| | The box |
|---|---|
| **Sees** | Ciphertext: WireGuard packets between peers that could not connect directly (relayed), and TLS records between a peer and the node's management and signal (passed through). Byte counts, timing, and the TLS server name of each connection, which is the box's own name. The relay authenticates peers with a shared secret, so it also sees which peer public keys ask for relaying. |
| **Can do** | Drop or delay traffic: a denial of service that costs the user remote access until the box is replaced (`remote setup` on another box under the same name, §3). Count and time connections. |
| **Cannot do** | Read a prompt, a reply, a file or a message: WireGuard's keys are the peers', generated on each peer and never sent; the management and signal TLS sessions end on the node with a certificate the box never holds. Join the overlay: the box has no setup key and no WireGuard key the node knows; the relay's secret lets it relay, not peer. Impersonate the node: a client trusts the node's CA, and the box cannot sign with it. Enrol a client: enrolment needs the LAN (§4). Reach the node's other services: the tunnel's account may only hold one loopback listener, which carries the control plane alone. |

What the design does **not** protect against: a compromised client (its WireGuard key is its identity; the node revokes it, §4.4); a compromised node (everything is there already); the metadata a network observer gets anyway (that this client talks to that box, when, and how much). Nor, on the node, an agent's sandboxed command: it can read the user's home ([SECURITY_REVIEW_2026-10](./DREAMFERENCE_SECURITY_REVIEW_2026-10.md)), so it can read the management token (`admin-token`, 0600) and reach the control plane on loopback, and could mint a setup key or delete a peer. That is the same class of exposure as the node's pairing key today, and closes with it.

---

## 2. Components and where each one runs

| Component | Where | Why |
|---|---|---|
| **Management and signal** (network map, peer keys, setup keys, DNS configuration; the rendezvous, end-to-end encrypted between peers) | The node: NetBird's combined server, container `dreamference-remote` (`netbirdio/netbird-server:0.80.0`, pinned by digest), published on `127.0.0.1:33443` only, memory capped at 512 MB. | The network's root of trust stays home. |
| **Private DNS** (each peer gets a name under `netbird.selfhosted`) | Management on the node; answered by every client's embedded resolver. | The locator's new tier resolves this name (§5). |
| **Relay and STUN** | The box: NetBird's relay binary, taken from the relay image's per-architecture manifest (pinned by digest, checked by SHA-256 on the node and again on the box), as the system unit `mightling-relay.service` under its own unprivileged account, on port 33080 (TCP for WebSocket, UDP for QUIC) and STUN on 3478/UDP. It also serves Prometheus metrics on 9090 on every interface (its flags choose that port, not the address): counts and byte totals, no key. The box's firewall must leave 9090 closed; `setup` opens only the four ports above in ufw or firewalld, and on a box with neither the provider's firewall is the only cover (§12.3). | It must be reachable from anywhere; it sees only ciphertext. |
| **TURN** (`coturn`) | Not deployed. | NetBird's relay alone (the user's decision); added only on evidence that a client of the user's fails without it. |
| **The pass-through** | The box: the distribution's haproxy (the only package added), dropping root after it binds, TCP mode on 443, routing a connection whose TLS server name is the box's own name to the reverse tunnel's loopback listener and refusing any other name. | Peers reach the control plane from anywhere while TLS still ends on the node. |
| **The reverse tunnel** | From the node to the box, over SSH, kept up by the user unit `mightling-remote-tunnel.service` (`ssh -N -R 127.0.0.1:8443:127.0.0.1:33443`, `ExitOnForwardFailure`, `Restart=always`). The box's account `mightling-tunnel` may only listen on `127.0.0.1:8443` (`restrict,port-forwarding,permitlisten=…` in `authorized_keys`, `AllowTcpForwarding remote` in an `sshd` drop-in). The tunnel has its own key, its own known-hosts file, filled from the box's host key read over the user's SSH at setup (never by a first-use scan), and no SSH configuration (`-F /dev/null`): it reaches the box **by its DNS name on port 22**, whatever alias the user's own configuration gives it. | The node has no public address and opens no port; it dials out. |
| **The node's own peer** | NetBird's client on the node (pinned archive, SHA-256), joined as `mightling-<node id>`. | Clients reach the node's services over the overlay. |
| **The CA** | The node: `~/.config/dreamference/remote/ca/`, EC P-256, key 0600 in a 0700 folder, made once by `remote setup` with `openssl`. Name-constrained (`permitted;DNS:<dns-name>`, `permitted;DNS:netbird.selfhosted`), `pathlen:0`. | Issues the control plane's and the relay's certificates (825 days). |

**Why the relay is not on 443.** haproxy routes by server name, and the control plane is the box's own name on 443. The relay would need a second DNS name to share the port; on its own port (33080) it needs none, and QUIC (UDP) never passes through haproxy anyway.

**TLS, once.** The combined server serves TLS itself (`server.tls.certFile/keyFile` in its `config.yaml`) with the node CA's certificate for `<dns-name>`. The box never speaks HTTP and never holds that certificate. NetBird's requirement that a proxy in front of management support HTTP/2 and gRPC does not apply, because no proxy terminates anything: gRPC and HTTP/2 pass through as TLS records (verified, §12.2).

**The management token.** The combined server's identity provider is configured (it is required) and never used: at the first start the node calls the unauthenticated `/api/setup` once (the server runs with `NB_SETUP_PAT_ENABLED=true`), which makes the owner with a random, discarded password and a one-day token; that token makes the node a 365-day token, kept at `~/.config/dreamference/remote/admin-token` (0600). `remote setup` renews it when run again and deletes the node's older tokens and the setup token, so one is left. A second `/api/setup` is refused by the server, so the endpoint is open only for the minutes between the container's first start and the node's own call, on loopback.

**Naming.** The box has a public DNS name the user owns (`<dns-name>`, the argument of `remote setup`). The overlay's domain is NetBird's default, `netbird.selfhosted`. No address of any kind is written down by Mightling: not the box's, not the node's overlay address, not a client's.

---

## 3. `ling-admin remote`: the commands on the node

The box is deployed and removed over the user's own SSH: `ssh <dns-name>` must log in with their key, as root or with passwordless sudo, on a box with apt or dnf, amd64 or arm64, and an ed25519 host key. When the user's SSH configuration knows the box by an alias instead, `--ssh <alias>` names it (remembered in `remote.json` as `ssh`, used by `status` and `remove`; an address is refused, since nothing here writes one down). The alias is only for the user's own connection: the tunnel still reaches the box by its DNS name with no SSH configuration (§2). The command never asks for a password and stores none.

- **`ling-admin remote setup <dns-name> [--ssh <alias>] [--box-only] [--yes]`**
  1. **Says what it will do** on the node and on the box, then asks once (`--yes` for scripts).
  2. **Checks the box** over SSH (architecture, root, package manager, host key) and stops before changing anything if it will not do.
  3. **On the node:** makes the CA if absent (an existing CA that does not permit the new name is refused: a box under another name needs `remove --purge`); issues the control plane's and the relay's certificates; keeps or makes the relay's shared secret; writes the combined server's configuration and starts `dreamference-remote`; makes or renews the management token.
  4. **On the box,** in two SSH calls: one archive is copied (the relay binary, its certificate, key and `relay.env`, haproxy's configuration, the relay's unit, the tunnel's `authorized_keys` line and the `sshd` drop-in), then one root script installs it: it checks the binary's SHA-256, installs haproxy if absent (recording whether it did), saves the existing haproxy configuration once, adds the two accounts, validates `sshd` and haproxy's configurations before reloading, opens 443/tcp, 33080/tcp+udp and 3478/udp if ufw or firewalld runs, and writes `/opt/mightling-relay/MANIFEST`. **Nothing else is installed on the box.** No Docker, no dashboard, no identity provider.
  5. **The tunnel:** writes the box's host key to the tunnel's known-hosts file and starts `mightling-remote-tunnel.service`.
  6. **The node joins** the overlay as `mightling-<node id>` with a setup key it mints and spends: one root script, shown before sudo asks, installs NetBird's client if absent (pinned archive), the CA into the system trust store, starts the client's service and runs `netbird up --hostname mightling-<node id>`. Then management is asked which overlay name it gave the node; anything but `mightling-<node id>.netbird.selfhosted` (a suffix for a duplicate, say) fails the setup loudly, since clients find the node by that name alone.
  7. **Records** `remote.json` (the box's name, the node's overlay name, the domain; no address), adds the `remote` key to the advertisement (§7), and prints the two lines that enrol a laptop.
  Run again, `setup` keeps the CA, the secret, the token and the peers, reissues the certificates, redeploys the box, and does not rejoin the node: that is how a replaced box is set up.
  **`--box-only`** does steps 1 to 4 without anything that runs on the node: the CA, the certificates, the relay's secret and the tunnel's key are made (files under `~/.config/dreamference/remote/`), the box is deployed and the tunnel's known-hosts file written; no container, no tunnel unit, no NetBird client, no sudo on the node. `remote.json` records `box_only`, and `setup` without the flag finishes the rest. It lets the box be checked before the node is touched (§12.3). On a setup that already went further it only redeploys the box and keeps the record.
- **`ling-admin remote status`:** the box's two units over SSH; **port 443 as a peer meets it**: a TLS handshake with the box's name must complete under the node's CA (the box passed it through the tunnel to the control plane) and one with another name must be closed by the box; the certificates' days left (a warning under 30); then the tunnel's state, the control plane's container, the peers enrolled and connected. After `--box-only` it says the node's half is missing and how to finish. Exit 0 only when all answer.
- **`ling-admin remote code`:** prints an eight-digit code and waits, on the LAN, for one client (§4.1).
- **`ling-admin remote peers`:** every peer: name, overlay name, connected or last seen, system.
- **`ling-admin remote revoke <peer>`:** deletes a peer by name or overlay name (an exact name wins over a prefix; an ambiguous prefix deletes nothing; the node itself is refused) (§4.4).
- **`ling-admin remote remove [--purge] [--yes]`:** undoes the box from its manifest (units, haproxy's configuration restored and the package purged if setup installed it, firewall rules, `sshd` drop-in, both accounts, `/opt/mightling-relay`) and the tunnel unit. The control plane, the CA and the enrolments are kept, so a replacement box under the same name needs no re-enrolment. `--purge` also stops the control plane, takes the node out of the overlay and its CA out of its trust store, deletes `~/.config/dreamference/remote/` and the advertisement key.

---

## 4. Enrolment: on the LAN, by hand, once per client

### 4.1 Laptops and desktops: `ling node remote join`

A launcher subcommand (`ling node remote join --code <8 digits> [--node <name>]`, `ling-rs/src/remote_join.rs`), run **on the client** while it is on the node's LAN, after `ling-admin remote code` on the node:

1. **Finds the node by a browse** (NODE §4). Off the LAN nothing answers and it stops there. With several nodes it takes the remembered one, the one coding node, or `--node`.
2. **Proves the code, both ways, without sending it.** The node listens on port 3190 for ten minutes, for one client. The exchange:

   ```text
   POST /mightling/enrol  {"nonce": n, "proof": HMAC(code, "mightling-enrol-client|" + n), "client": host name}
   ← {"bundle": text, "mac": HMAC(code, "mightling-enrol-server|" + n + "|" + text)}
   ```

   HMAC-SHA256 keyed with the code's eight ASCII digits, lowercase hex, `n` 16 random bytes in hex. A wrong proof costs one of ten attempts; the tenth withdraws the code. The listener refuses an address that is not private, link-local or loopback, and any address in the overlay's range (RFC 6598), so enrolment cannot be done from outside or through the overlay. The client refuses a bundle whose proof is wrong (someone other than the node answered), that names another node than the one browsed, whose overlay name is not `mightling-<that id>.<domain>`, whose management URL is not HTTPS on a plain host name, whose CA is not a certificate or whose setup key has odd characters.
   The bundle: the CA certificate, the management URL (`https://<dns-name>:443`), the relay's address, the overlay's domain, the node's overlay name and id, and a **one-time setup key** minted for this client (one use, ten minutes).
   **Stated plainly:** a device on the LAN that watches the exchange sees the setup key, which the client spends within seconds and which is worth nothing after ten minutes; it can learn the code from a proof only by trying all 10^8 codes, by which time the code is spent. That is the trusted-LAN assumption of decision 4, not a gap in it.
3. **Installs what the client needs,** each step printed before it runs:
   - the CA into the **system** trust store, the only one NetBird's client reads: Debian/Ubuntu `/usr/local/share/ca-certificates/mightling-node-<id8>.crt` + `update-ca-certificates`; Fedora `/etc/pki/ca-trust/source/anchors/` + `update-ca-trust extract`; macOS `security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain`; Windows `certutil -addstore -f Root` (an administrator terminal);
   - NetBird's client when it is missing: the release archive for the system, pinned by SHA-256 (Linux, macOS and Windows, amd64 and arm64), installed root-owned (`/usr/local/bin/netbird`; `%ProgramFiles%\Mightling\NetBird\netbird.exe` on Windows), its service installed and started;
   - `netbird up --management-url https://<dns-name>:443 --setup-key <key>`; no sudo, the daemon's socket is open to local users.
4. **Records** the node's overlay name in `$CODEX_HOME/node.json` as `remote` (§5), never an address.

The client's WireGuard key, generated by NetBird's client and never leaving the machine, is its identity from then on.

### 4.2 Phones

The MVP's phone is NetBird's mobile app plus Element X for Matrix (§8), both over the overlay. Enrolment is by hand on the LAN for now: `ling-admin remote code` serves the bundle, and the Devices page of `ling web` that shows it as a QR code (ASK §18.2's pattern: management URL, setup key, and the CA to install) is the next step of the phone work. The phone's OS installs the CA by hand:

- **iOS:** the CA as a configuration profile, then *Settings → General → About → Certificate Trust Settings → full trust*. NetBird's client is Go, whose verifier on Apple systems is the platform's, which honours a fully trusted profile root. To be confirmed on a device (§13.3).
- **Android:** the CA under *Settings → Security → Encryption & credentials → Install a certificate → CA certificate*. **Likely not enough.** NetBird's client (Go, `client/grpc/dialer.go`) uses `x509.SystemCertPool()`, which on Android reads `/system/etc/security/cacerts` and the legacy folder `/data/misc/keychain/certs-added`; user CAs on current Android live elsewhere and are not readable by apps. If a device confirms it, the user's decision 3 applies its fallback to the relay only, and the control plane, which must never get a public certificate, leaves Android phones out until the user decides otherwise (§11.3).

### 4.3 What a client does afterwards

Nothing. NetBird's client keeps the overlay up as a service; `ling` resolves the node by its overlay name (§5); `ling web`, paired as before, is reached at the node's overlay name over port 3100 with the same device cookie.

### 4.4 Revoking a client

`ling-admin remote revoke <peer>` deletes the peer in management: its WireGuard key is forgotten and its overlay address released at once; its name stops resolving; its daemon falls back to "needs login" (verified, §12.2). It can be enrolled again only on the LAN.

---

## 5. The locator: one more tier, by name

NODE §6.1's order of resolution becomes:

1. `DREAMFERENCE_VLLM_HOST`, then `vllm_host` in a configuration file.
2. `MIGHTLING_NODE=<name>`.
3. This machine is a node: `localhost`.
4. **New: the remembered node's overlay name.** `node.json`'s `remote` is used when it is that node's own name (`mightling-<its id>.` followed by a domain; anything else in the field is ignored, in the file and in an advert), NetBird's client reports management connected (`netbird status --json`; the daemon's socket is open to local users), and the name resolves. NetBird is asked first so that, with the overlay down, the name is never sent to the system's DNS servers. Nothing is browsed and nothing is written: the address NetBird returns is used for that run.
5. The remembered node by id, found on the LAN.
6. A browse.

The name carries the id, so the tier needs no separate identity check: a name that resolves in the overlay is that node's. When both the overlay and the LAN would answer they lead to the same node.

The other programs that read `node.json` (`ling-search`, `ling-fetch`, the indexes; the `ling-node-locator` crate and its byte-identical copy in `ling-web-rs/src/node_locator.rs`) use the LAN address and switch to the overlay name when it does not accept a connection within 300 ms **and** NetBird reports management connected; so for them too the name never reaches the system's resolver while the overlay is down. The launcher adds the overlay name to `NO_PROXY` beside the LAN address.

**`node.json`** gains one string field, `remote`, written only when there is one. **`ling node list`** shows "off the LAN: <name> (remote access)" under a node that advertises one, and "over the overlay" for the remembered node when only the overlay reaches it; `ling node forget` drops both.

---

## 6. The gate, the audit and the air gap

- **The model gate** (SWE_BENCH §18) does not change. The overlay is one more interface the model server already listens on, and the gate's refusal covers it: a remote client during a benchmark gets the same 503 with `Retry-After` as a LAN client, and `ling-admin night pause` lets it through like the others.
- **The egress audit** gains a **declared exception** in the SIGNAL §10 pattern: when `mightling-remote-tunnel.service` is enabled, the verdict adds "Remote access enabled: the node keeps an SSH tunnel to `<dns-name>`, and NetBird's client reaches its relay and the overlay's peers, outside this trace" (built). The ordinary `ling exec` trace still passes with no connection beyond loopback: the agent itself never touches the overlay. `audit egress --remote`, a one-minute trace of the tunnel and the client that passes only if every connection is the box on 22, 443, 33080 or 3478 or a peer's WireGuard endpoint, belongs with the Rust `ling audit egress` and is not built yet (§13.2).
- **`/airgapped on`** does nothing to the tunnel. The air gap closes the agent's commands' network, not the node's services; a remote client at `/airgapped on` still reaches the node (the model server is exempt at every level, AIRGAPPED spec).

---

## 7. The advertisement

The node's `_mightling-node._tcp` record (NODE §4) gains one TXT key, `remote`, the node's overlay name, present only while remote access is set up (`remote setup` adds it, `remove --purge` drops it; a state change or `node enable` keeps it). A LAN client reads it and stores it in `node.json`, so a client enrolled on an earlier visit keeps the name across re-pairing. The record carries a name, never an address, like every other key; a client ignores a value that is not `mightling-<the advert's id>.…`.

---

## 8. Matrix moves onto the overlay

CHAT §2 and §5 are rewritten when this is built: `ling-admin matrix start` uses the overlay when remote access is set up, the homeserver's server name is the node's overlay name, and `tailscale serve` is replaced by the node's own TLS from the same CA (a certificate for the overlay name; the CA's constraints permit `netbird.selfhosted`), terminating in front of the loopback proxy unit the homeserver already has. The phone, enrolled as in §4.2, signs in to `https://<overlay name>` in Element X, which must then trust the node's CA on the phone (the same per-OS question as §4.2). Tailscale stays as the path for a node without remote access. **Not built in this branch** (§13.2): the server name is permanent once chosen, so the switch for an existing homeserver needs the user's word.

---

## 9. Acceptance

1. **By name, from outside.** A laptop on a phone hotspot, enrolled on the LAN the day before, runs `ling exec "Reply with exactly one word: pong"` with nothing configured and gets `pong`; `ling node list` shows the node reached over the overlay; `node.json` holds no address but the LAN one it had.
2. **The box reads nothing.** A packet capture on the box during that session shows only TLS records on 443 (the server name being the box's name), relay traffic on 33080 and STUN on 3478 if the connection was relayed, and SSH on the tunnel; no plaintext HTTP, no DNS query for a peer name, and no connection from the box to anywhere but the node's tunnel.
3. **Revocation cuts.** `ling-admin remote revoke <laptop>` on the node; the laptop's next `ling exec` fails to resolve or connect, and `ling node remote join` from off the LAN is refused.
4. **The audit passes.** `ling-admin audit egress` on the node passes with the declared exception named; the ordinary `ling exec` trace shows no connection beyond loopback.
5. **The suite stays offline.** Tests mock SSH, Docker, systemd, sudo and HTTP through each class's seam (the existing guards refuse the real ones); no test opens a port beyond a scratch loopback listener or resolves a name. Met: `tests/test_remote_access.py` (62), the audit's test, the launcher's and the locator's tests.
6. **Nothing for the others.** A machine that never ran `remote setup` shows no new unit, no container, no TXT key, the same audit verdict, and `ling node list` unchanged.
7. **Each client kind,** Linux, macOS, Windows and a phone (§4.2), enrolled and reaching the node from outside, with the node's CA trusted by that system's own store.

---

## 10. Non-goals

- **No VPN or protocol of Mightling's own.** NetBird's client and services are used unmodified; this document only places them.
- **No public CA.** The node's CA signs everything; Let's Encrypt is not needed and not used. The relay alone may get a public certificate if a phone provably cannot trust the CA (decision 3, §11.3).
- **No identity provider in use.** Enrolment is by setup key on the LAN; NetBird's dashboard and OIDC login are not deployed (the combined server's built-in provider is configured because it must be, and nobody signs in through it).
- **No node-to-node jobs over the overlay.** The MVP is clients to the node; jobs between nodes stay on the SSH pairing of NODE §9.
- **No access policy beyond NetBird's default** (every peer may reach every peer). Clients-to-node only is a later refinement.
- **No change to what the agent may reach:** the overlay is the node's and the clients'; a sandboxed command on the node is not on it.

---

## 11. Decisions on the open questions (the user, 2026-10-10)

1. **The pass-through on the box:** the distribution's haproxy in TCP mode, routing by server name; the only package added to the box. Built (§2, §3); verified with haproxy 2.8 in the rehearsal (§12.2).
2. **Management and signal:** the combined `netbird-server` on the node, serving its own TLS behind the pass-through, to be confirmed in Phase 0; if it could not, the separate services under two names at the box (`mgmt.<box>`, `signal.<box>`; the CA's constraint on `<box>` already permits names under it). **Confirmed locally** (§12.2); Phase 0 on the real box repeats it across the internet.
3. **The relay's certificate:** from the node's CA. A public certificate for the relay only if a phone client provably cannot trust a private CA; the control plane never gets one. **Open point raised by the build:** on Android the control plane needs the same trust as the relay, and NetBird's Go client probably cannot use a user-installed CA there (§4.2). If Phase 0 confirms it, Android phones need the user's next decision; iOS is expected to work.
4. **TURN:** NetBird's relay alone; coturn only on evidence that one of the user's clients fails without it.
5. **The overlay's DNS domain:** NetBird's default, `netbird.selfhosted`.
6. **Node-to-node jobs over the overlay:** out of the MVP.
7. **MVP clients:** Linux (the Ubuntu laptop), macOS, a phone (NetBird's mobile app plus Element X over the overlay) and Windows; the node-CA trust specified for each (§4.1, §4.2) and tested on each (§13.3).
8. **The test box:** the user provides one with a public address and SSH and gives its SSH name later. Until then everything is built and tested offline; Phase 0 and the live acceptance run once the box exists.

---

## 12. Verified

### 12.1 Against NetBird's documentation and source (2026-10-10)

- **Components:** client, management, signal and relay; recent releases embed STUN in the relay and offer the combined `netbird-server` (management, signal, relay, STUN) configured by one `config.yaml` (`combined/config.yaml.example` at v0.80.0): `server.tls.certFile/keyFile` for its own TLS; `relays` and `stuns` naming external ones switch the embedded ones off.
- **Setup without a sign-in:** `/api/setup` creates the owner and, with `NB_SETUP_PAT_ENABLED=true`, a personal access token of 1 to 365 days; a second call is refused (`management/server/instance/setup_service.go`, `e2e/harness/bootstrap.go`).
- **The relay:** flags `--listen-address`, `--exposed-address`, `--tls-cert-file`, `--tls-key-file`, `--auth-secret` (or `NB_AUTH_SECRET`), `--enable-stun`, `--stun-ports`, `--health-listen-address` (`relay/cmd/root.go`); a static binary at `/go/bin/netbird-relay` in `netbirdio/relay` for amd64 and arm64.
- **The client's trust store:** `x509.SystemCertPool()`, falling back to embedded roots only when there is no system pool (`client/grpc/dialer.go`); the daemon's socket is mode 0666 (`client/cmd/service_socket.go`).
- **The client's command line and status:** `--hostname` (`-n`) is a persistent flag that sets the device's name (`client/cmd/root.go`, `up.go`); `netbird status --json` holds `management.connected`, a boolean, beside the peers' own `connected` count (`client/status/status.go`).
- **The address range:** NetBird allocates peer addresses from the carrier-grade NAT block (RFC 6598). Not written here as digits, by this document's rule.
- **Signal's privacy:** messages through signal are end-to-end encrypted between the peers.

### 12.2 The local rehearsal (2026-10-10, Docker on the node, no box)

One Docker network: `netbird-server` 0.80.0 with a certificate from a private CA for a test box name; a "box" container running the relay with STUN on 33080 and a certificate from the same CA, and in its network namespace first `socat` (a plain TCP forward) and then haproxy 2.8 with the configuration of §3 (the box's name to the server, any other name refused); two client containers with the CA added to their system store and `NB_FORCE_RELAY=true`. Results:

- Management and signal **connected through the plain TCP forward and through haproxy**, TLS served by the combined server from the private CA, on one name (question 2 confirmed; gRPC and HTTP through one port).
- `/api/setup` with a token, then one-off setup keys through the API; both clients enrolled; their names were `<host>.netbird.selfhosted`.
- A ping by overlay name went **relayed** (`rels://<box>:33080`, WebSocket), the relay's certificate from the private CA accepted.
- A request with another server name was refused by haproxy.
- Deleting a peer through the API: its name stopped resolving at the other peer within twenty seconds and its daemon fell back to "needs login".

A lesson from it: NetBird's client image runs `netbird up` from its entrypoint whatever command it is given, and with no management URL that is NetBird's public cloud; one container did so for seconds before it was removed (nothing was registered). The implementation never runs that image; the node's client is the release binary.

Not verified, and Phase 0's: the same across the internet with the real box and the SSH tunnel; the name-constrained CA in NetBird's client (Go enforces name constraints; the rehearsal's CA had none); macOS, Windows, iOS and Android trust (§4).

### 12.3 Phase 0, first half: the real box (2026-10-12)

The box: a 1 GB amd64 Debian 12 cloud VM with a static address and the provider's own DNS name for it, reached by the user's SSH alias, with services of its owner already running (a SOCKS proxy, a mail transfer agent, a service under another account). The provider's firewall opened 22 and 443 only. `ling-admin remote setup <name> --ssh <alias> --box-only --yes`, run from the node:

- **Deployed in one run, unchanged code paths:** the amd64 relay pulled by its manifest digest on the arm64 node, the binary's SHA-256 equal to the pin on the node and on the box; haproxy 2.6 (Debian 12's) installed and recorded in the manifest as `haproxy=installed`; both accounts; the `sshd` drop-in (the effective settings for other accounts unchanged: forwarding stays `yes`, a TTY stays allowed; for `mightling-tunnel`, `AllowTcpForwarding remote`, no TTY); no ufw or firewalld, so no rule was added. Listening afterwards: haproxy on 443/tcp, the relay on 33080 tcp+udp, STUN on 3478/udp, the relay's health on loopback 9000 and its metrics on 9090. The owner's services kept running, their ports untouched. Resident memory: relay about 22 MB, haproxy about 19 MB.
- **Port 443 from the node, through the provider's firewall:** with the box's name the connection was passed on (haproxy's log: backend `node/tunnel`, and with nothing on the tunnel's port yet, `SC`, closed); with another name, with no server name at all, and with plain HTTP, closed by haproxy (backend `refuse`, `PR`). With a throwaway TLS listener started for the test on the box's loopback 8443 (where the tunnel will land), a handshake with the box's name completed with **that listener's** certificate and an HTTPS request got its page: the TLS was passed through unopened. The listener and its files were removed after.
- **The tunnel account:** the node's tunnel key, with the tunnel's own known-hosts file (filled from the probe, never a first-use scan) and no SSH configuration, logged in to `mightling-tunnel` by the box's DNS name and got `This account is currently not available` (the forced `nologin`, no shell); the same key was refused for the user's account. No forwarding was attempted from the node.
- **`remote status`** showed the two units active, 443 "takes the connection and nothing answers behind it (the tunnel is not up); other names refused", the certificates' 824 days, and the missing node half; exit 1, as it should be.
- **Found:** the relay's metrics port (9090, every interface; §2). The provider's firewall closes it here; from the node 33080, 3478, 8443, 9000 and 9090 took no connection. **The relay is therefore unreachable from outside until the provider's firewall opens 33080/tcp+udp and 3478/udp**, a step in the provider's console that `setup` cannot do. The relay logged that the kernel's UDP receive buffer is below QUIC's wish (416 KiB of 7 MiB); harmless at this scale.
- **Not yet:** everything on the node (§13.3, step 1's second half), and `remote remove` against the real box.

Caveat of this box's name: it is the provider's name for the address, not one the user owns, and the node's CA is now constrained to it. Moving to an owned name later means `remote remove --purge` and enrolling the clients again (decision 3); before any client is enrolled that costs nothing.

### 12.4 Phase 0, second half: the node (2026-10-13)

The provider's firewall now opens 33080/tcp+udp and 3478/udp. The owner ran `ling-admin remote setup <box> --ssh <alias>` on the node (gx10-1a2b) from the branch, while SWE-bench night 5 held the model server; nothing of night 5, the model gate or the model server was touched. Checked from the node and the box, with no client:

- **Setup:** one run; the container `dreamference-remote` (86 MB of its 512 MB cap), the user unit `mightling-remote-tunnel.service` (active, no restart), NetBird's client 0.80.0 joined under exactly `mightling-<node id>.netbird.selfhosted`.
- **`remote status`:** exit 0. Box units active; port 443 "the box's name reaches this node's control plane (TLS under the node's CA); other names refused"; certificates 824 days; tunnel active; control plane running; peers 1 enrolled, 1 connected (the node).
- **From the node, by `openssl s_client` with the node's CA:** 443 with the box's name gives the control plane's certificate, verified; 443 with another name, no certificate (haproxy closes it); **33080/tcp gives the relay's certificate, verified**. NetBird's client on the node reports management and signal connected, `rels://<box>:33080` available **via QUIC** and STUN on 3478 available: the relay is reachable across the internet over UDP and TCP.
- **The overlay name resolves on the node** through NetBird's resolver on its interface (IPv4 and IPv6), not through the system's DNS servers.
- **On the box:** the tunnel lands on loopback 8443 only; relay and haproxy active, resident memory about 24 MB and 20 MB, about 490 MB of the box's 1 GB free.
- **Found and fixed:** the install script rewrote the box's `MANIFEST` on every run, so this second run recorded the haproxy that the first run had installed as `haproxy=present`, and `remote remove` would have left the package behind. A rerun now keeps `haproxy=installed` (test `test_a_rerun_keeps_haproxy_recorded_as_installed_by_us`); the box's manifest was corrected by hand, `dpkg.log` confirming the 2026-10-12 install.
- **Not yet, waiting for the laptop** (not on the LAN on 2026-10-13): enrolment, the overlay name and the model server from a client, the relay forced, and §9.1 to §9.3. The model server over the overlay was not probed from the node either: during night 5 only the gate answers, and the check belongs to the client. **§9.4 waits for night 5 to end:** `audit egress` runs a real `ling exec` against the model server, which would need `night pause`.

---

## 13. Implementation

### 13.1 Built (branch `remote/vpn-mvp`)

- **Node** (`dreamference/remote/`): `RemoteSettings` (paths, pins, `remote.json`), `RemoteCertificateAuthority` (the constrained CA, `openssl`), `RemoteControlPlane` (the container, `config.yaml`, the API: setup, tokens, setup keys, peers), `RemoteBox` (probe, relay extraction and check, the staged archive, the install and remove scripts, unit states, the check of port 443 from the node), `RemoteTunnel` (key, known hosts, user unit), `RemoteNodePeer` (the node's client, CA, `netbird up`, leave), `RemoteEnrolment` (code, proofs, the LAN listener), `RemoteAccess` (the six commands; `setup --ssh` and `--box-only`, 2026-10-12). The advertisement's `remote` key (`NodeServiceFile`, `NodeAdvertiser`), the audit's declared exception, `ling-admin remote …` in the CLI.
- **Launcher** (`ling-rs/`): `remote_join.rs` (`ling node remote join`), the overlay tier and listing in `node.rs`, the overlay name in `NO_PROXY` (`proxy.rs`), the audit's host for an overlay resolution (`audit.rs`), and in `ling-node-locator` (with its copy in `ling-web-rs`) the `remote` field, the own-name rule and the LAN-or-overlay choice.
- **Tests:** HMAC vectors shared by the Python and Rust halves (and RFC 4231 case 2), the CA's constraints with real `openssl`, the box's scripts through `bash -n`, the enrolment listener on a scratch loopback port, the 443 handshake's verdicts against scratch loopback TLS listeners (the node's CA, a stranger's certificate, a bare close, nothing), the commands with every seam faked.

### 13.2 Not built yet

- Matrix on the overlay (§8) and the `ling web` Devices page with the phone's QR code (§4.2).
- `audit egress --remote` (§6), with the Rust audit.
- A `ling-admin remote` line in `ling-admin status`, and docs on the user site.

### 13.3 Phase 0, then the live acceptance

0. **Done (§12.3):** `ling-admin remote setup <box> --ssh <alias> --box-only`; the box's units active, 443 passing the box's name and refusing others.
1. **Done (§12.4):** the provider's firewall opens 33080/tcp+udp and 3478/udp; `ling-admin remote setup <box> --ssh <alias>` on the node; `remote status` all green, its 443 line saying the box's name reaches the control plane under the node's CA.
2. From the Ubuntu laptop on the LAN: `ling` built from the branch (the installed 1.6.0 has no `ling node remote join`), then `ling-admin remote code` / `ling node remote join --code …`; on the laptop the node's overlay name resolves, the model server answers over the overlay (a 503 with `Retry-After` from the gate counts during a benchmark night), and again with `NB_FORCE_RELAY=true` on the laptop's NetBird service, which proves the box's relay since the LAN would otherwise win; then on a hotspot, §9.1.
3. On the box during that: `tcpdump` for §9.2.
4. `remote revoke` for §9.3; `audit egress` for §9.4.
5. A Mac and a Windows machine as in step 2; an iPhone and an Android phone by hand (§4.2), recording which trust the CA. Android's result goes to the user (§11.3).
6. `remote remove`, then `setup` again: no client re-enrols; `remove --purge` leaves the node and the box as before setup.

---

## 14. Reconciled with main (2026-10-20)

On 2026-10-20 a second build of this design was started on main without knowing of `remote/vpn-mvp` (its Phase 0 and 1, then reverted). The owner's decision that day: the branches of 2026-10-10 to 13 are the base, rebased onto main as `remote/overlay`, with the second build's fixes ported in; the deployed box layout stays (haproxy on 443 by server name, the relay on 33080 TCP and UDP, STUN on 3478, the provider's firewall).

### 14.1 Found on the live node

The control plane's container restarted with the node on 2026-10-14 08:02. Its configuration had no `server.store.encryptionKey`, so the server made a new key at that start (its log says so), and since then management answers the saved administrator token with `401 token invalid`: `remote status` shows "management did not answer", and `peers`, `revoke` and `code` cannot work. The owner's password had been discarded, so there was no way back into the API. The overlay itself kept running (the node's client: management and signal connected, relays 2/2).

### 14.2 Ported fixes

1. **The store's key** is made once (`remote/store-key`, 0600) and written into `config.yaml` before every start.
2. **`NB_SETUP_PAT_ENABLED`** only for the one start that creates the owner: started plainly; if management needs its first setup, restarted with it for `/api/setup` on loopback, then plainly again. A wiped store behind a live tunnel can no longer hand the world an owner's token.
3. **The owner's password** is kept beside the token (`remote/owner-password`, 0600), the way back if a token is lost.
4. **No route out:** the container is on the internal Docker network `dreamference-remote`, reached through a loopback proxy (`mightling-remote-control.socket`, `systemd-socket-proxyd`) on the port the tunnel already forwards to, `127.0.0.1:33443`; the tunnel unit is unchanged.
5. **Setup keys in files:** `netbird up --setup-key-file` on the node and on clients (0600, deleted after), never `--setup-key` on a command line. `--setup-key-file` is in 0.80.0's client (`client/cmd/root.go`), read by the CLI as the user who runs it.
6. **The CA scope is a setting:** `remote setup --ca-scope <domain>`, by default the parent of the box's name for a name of three labels or more; the CA is constrained to the scope and `netbird.selfhosted`, and the scope is recorded in `remote.json`.

The helpers report failures through their module loggers (the bare line on standard error), so that only the console-facing `RemoteAccess` prints (`T201`, the Python quality ratchet).

### 14.3 The owner's decisions

- **One name:** `remote.dreamference.ai`, a Cloudflare A record to the box, DNS only. The control plane at `https://remote.dreamference.ai:443`, the relay at `rels://remote.dreamference.ai:33080`. CA scope `dreamference.ai`.
- **Pins stay 0.80.0** for the first live acceptance. For the next bump, read on 2026-10-20 from NetBird's published checksums and Docker Hub: 0.81.3 client archives (linux amd64 `cbdea310…b63b1`, linux arm64 `980eef93…c22d`, darwin amd64 `95ef6a3f…cac1f`, darwin arm64 `bc37108e…e71`), `netbirdio/netbird-server:0.81.3` index `sha256:541ecef7…7f1d`, `netbirdio/relay:0.81.3` amd64 `sha256:de516151…0324`, arm64 `sha256:f5f7bba0…41c7`; the relay binary's SHA-256 needs one extraction on the node.
- **The box stays the one already set up**, with its other services untouched.

### 14.4 The migration of the 10-13 deployment

No client was ever enrolled, and the CA (constrained to the provider's name for the box) and the store (its key lost) cannot carry on, so the node starts its overlay afresh under the new name and the box is reconciled in place. The steps, conditions and backups are in `docs/dev/remote-access.md`. The node's half needs root once (the CA into the trust store, NetBird's client brought up), and sudo on the node asks for a password, so that step is the owner's at a terminal.

### 14.5 Matrix on either overlay

The last phase (owner's decision, 2026-10-20): the homeserver reachable by name on this overlay, the default for a new homeserver once it exists, Tailscale kept as the option for a user who already has it. A homeserver's server name is permanent, so an existing Tailscale homeserver keeps its name and its overlay until `matrix remove`. §8 is rewritten when it is built.

