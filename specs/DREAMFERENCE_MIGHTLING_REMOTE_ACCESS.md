# Mightling from outside the LAN: remote access (MVP)

**Status:** proposed 2026-10-10, the user's design; the build was decided on 2026-10-20 (§13, phases) and Phase 0 was done at the desk that day (§14: pins, what the source confirms, the open points decided). Nothing else built yet. This document is the MVP: a client on a hotspot or a hotel network reaches its node by name, through an overlay the node runs itself and one rented machine that can see nothing. Phones are documented, not built. §12 lists what was checked against NetBird's own documentation on 2026-10-10 and what remains open.

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

1. **The node is the control plane.** NetBird's management service, its signal service and its private DNS run on the primary node, beside the model. They hold the network map, the peers' public keys and the enrolment keys, and they never leave the user's machine. NetBird is used as it is (its own client, its own protocol); Mightling writes no VPN and no protocol of its own.
2. **One rented machine, and it is untrusted.** The only thing outside the user's walls is a small cloud box that does two jobs: it runs NetBird's relay, for the moments two peers cannot reach each other directly, and it passes the public port 443 through, as plain TCP, to the node. It terminates no TLS, holds no key of the network, and is deployed and removed by one command (§3). If it is seized, wiped or replaced, the attacker gets ciphertext and a chance to cause an outage (§1).
3. **TLS ends on the node, under the node's own certificate authority.** The node makes a private CA once and issues its own server certificates from it. A client trusts that CA, and nothing else about the box's name is trusted. No public CA, no Let's Encrypt and no account anywhere is required for the control plane.
4. **Enrolment happens on the trusted LAN, or not at all.** A client is admitted to the overlay only while it stands next to the node: `ling node remote join` refuses off the LAN (§4). After enrolment the client's WireGuard key is its identity; nothing it types later lets a stranger in.
5. **A client finds its node by the overlay name, never by an address.** The locator gains one tier (§5): the node's NetBird DNS name, learnt at enrolment and recorded in `node.json`. No address is stored anywhere in this design, as [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md) §6.2 already requires on the LAN. Two names, two jobs: the **box's public DNS name** is the control plane's (the client reaches management and signal at `https://<dns-name>` through the pass-through, before the overlay exists); the **node's overlay name** is the data path's (the model server, `ling web`, Matrix, once the overlay is up).
6. **Nothing changes for a user who never runs the setup.** No installer step turns this on, no port opens, the audit's verdict is the same as today. `ling-admin remote setup` is the only way in and it says what it changes before any sudo.
7. **Matrix rides the same overlay.** The phone messenger's Tailscale leg ([MIGHTLING_CHAT](./DREAMFERENCE_MIGHTLING_CHAT.md)) becomes the remote-access overlay, and `tailscale serve` is replaced by the node's own TLS (§8). One overlay, one trust root, one set of commands.

---

## 1. Threat model: what the box can see and do

The box is a rented virtual machine that someone else administers, images, snapshots and can be compelled to hand over. The design treats it as hostile from the first minute.

| | The box |
|---|---|
| **Sees** | Ciphertext: WireGuard packets between peers that could not connect directly (relayed), and TLS records between a client and the node's management and signal services (passed through). Byte counts, timing, and the TLS Server Name Indication of each connection, which names the node's overlay host, not a user or a document. The relay authenticates peers with a shared secret, so it also sees which peer public keys ask for relaying. |
| **Can do** | Drop or delay traffic: a denial of service that costs the user remote access until the box is replaced (`remote setup` on another box, §3). Count and time connections. |
| **Cannot do** | Read a prompt, a reply, a file or a message: WireGuard's keys are the peers', generated on each peer and never sent; the management and signal TLS sessions end on the node with the node's certificate, which the box never holds. Join the overlay: the box has no setup key and no WireGuard key the node knows; the relay's secret lets it relay, not peer. Impersonate the node: a client trusts the node's CA, and the box cannot sign with it. Enrol a client: enrolment needs the LAN (§4). |

What the design does **not** protect against: a compromised client (its WireGuard key is its identity; the node revokes it, §4.4); a compromised node (everything is there already); the metadata a network observer gets anyway (that this client talks to that box, when, and how much).

---

## 2. Components and where each one runs

NetBird has four components: the client on every machine, and three services. Their placement here:

| Component | Where | Why |
|---|---|---|
| **Management** (network map, peer keys, setup keys, access rules, DNS configuration) | The node, as a container beside the model server, on loopback. | It is the network's root of trust; it stays home. |
| **Signal** (the rendezvous that lets two peers exchange connection candidates; end-to-end encrypted between the peers, it stores nothing) | The node, beside management. | Same reason; it is tiny. |
| **Private DNS** (each peer gets a name under the overlay's domain) | Management on the node; answered by every client's embedded resolver. | The locator's new tier resolves this name (§5). |
| **Relay** (carries WireGuard traffic between peers that cannot connect directly; authenticates with a shared secret) | The box. | It must be reachable from anywhere; it sees only ciphertext. |
| **STUN** (lets a peer learn its public address for a direct connection) | The box, the relay's embedded STUN server. | Needs a public address. |
| **TURN** (`coturn`) | Not deployed. | NetBird's own relay replaced it for current clients; it is added only if a client proves to need it (§11). |
| **The pass-through** | The box: its public port 443 forwards, as plain TCP with the Server Name Indication left intact, to the node's management and signal. | So that clients reach the control plane from anywhere while TLS still ends on the node. |
| **The reverse tunnel** | From the node to the box, over SSH, kept up by a unit on the node. | The node has no public address and opens no port; it dials out. |
| **The CA** | The node: `~/.config/dreamference/remote/ca/`, key 0600, made once by `remote setup`. | Issues the management, signal and relay-facing certificates. |

**TLS, once.** Management and signal serve TLS themselves with certificates from the node's CA (NetBird's `NB_CERT_FILE` and `NB_CERT_KEY` settings, §12). The box never speaks HTTP: a connection arrives on its 443, the pass-through reads the server name from the TLS hello, and hands the bytes to the tunnel. NetBird's requirement that a proxy in front of management support HTTP/2 and gRPC does not apply, because there is no proxy in the HTTP sense. **The clients' management URL is the box's name,** `https://<dns-name>`: off the LAN the overlay name resolves only once the overlay is up, and a client needs management first. The certificates list `<dns-name>` as a subject alternative name for that reason.

**One port, one or two server names.** A pass-through routes by server name, so management and signal need either two names at the box (`mgmt.<dns-name>` and `signal.<dns-name>`, both resolving to the box, both in the certificate) or one listener on the node that serves both. The recommendation is the second: the combined `netbird-server` container of recent releases multiplexes management, signal and relay on one port (§12), which makes the pass-through a single rule and removes the h2c question at the same time; the relay part of it is switched off on the node, since the relay runs on the box. Open question 1 records what Phase 0 must confirm about it.

**The relay's certificate.** The relay terminates its own TLS (NetBird's documentation says so, and advises against a proxy in front of it since that costs QUIC). Its certificate comes from the node's CA too, issued for the box's relay name (`relay.<dns-name>`, since the control plane holds `<dns-name>` on the same port: §14.3, item 1) and copied to the box by `remote setup`; clients trust the CA, so no public certificate is needed. Whether NetBird's client accepts a relay certificate from a private CA installed in the system trust store is verified for the relay (§12) and assumed for management and signal (§11).

**Naming.** The box has a public DNS name the user owns (`<dns-name>`, the argument of `remote setup`). The overlay's domain is NetBird's `netbird.selfhosted` by default; the node's name in it is derived from its hostname, as NetBird does. No address of any kind is written down by Mightling: not the box's, not the node's overlay address, not a client's.

---

## 3. `ling-admin remote`: the box, deployed over SSH

The node admin command deploys and removes the box. It needs `ssh <dns-name>` to work as root or a sudoer (the user's own key; the command never asks for a password it stores).

- **`ling-admin remote setup <dns-name>`**
  1. **Says what it will do** on the box and on the node, then asks once (`--yes` for scripts).
  2. **On the node:** makes the CA if absent; issues certificates for management, signal (the node's overlay name and `<dns-name>` as SANs) and the relay (`<dns-name>`); generates the relay's shared secret; starts the management and signal containers on loopback with those certificates and `relays.secret`; writes the node's own client configuration and enrols the node as the first peer with a setup key it mints and spends; installs the reverse-tunnel unit (`mightling-remote-tunnel.service`, a user unit, `autossh`-style retries without `autossh`: a loop in the unit).
  3. **On the box, over SSH:** downloads NetBird's relay release archive **pinned by version and SHA-256**, the way `ling signal setup` fetches signal-cli (SIGNAL §3); installs it under `/opt/mightling-relay/` with the certificate, the key and `relay.env` (readable by root only, as NetBird advises); installs the pass-through (a small SNI router, also a pinned release, or `haproxy` from the distribution with a four-line configuration; decided in §11) and two systemd units; opens 443 and the STUN port in the box's own firewall. **Nothing else is installed on the box.** No Docker, no dashboard, no identity provider.
  4. **Prints** the relay's DNS name, the overlay's domain and the node's overlay name, and the line to run on a client while on the LAN (§4).
- **`ling-admin remote status`:** the tunnel's state, the relay's health (its own health endpoint, over the tunnel), the box's unit states over SSH, the CA's expiry, the number of enrolled peers and which answered management in the last minute.
- **`ling-admin remote remove`:** stops and deletes the units and files on the box, closes its firewall ports, removes the tunnel unit and the relay's secret on the node; keeps the CA and the management state unless `--purge`, so a replacement box needs no re-enrolment.
- **Idempotent.** `setup` on an already set up box reconciles: a newer pinned release is installed, a changed certificate copied, nothing else touched. Changing the box means `remove` on the old one and `setup` on the new; clients learn the new relay from management at their next sync and need nothing.

**What the node admin never does:** no password is typed into the command, no key material leaves the node except the certificates and the relay's secret (which the box needs), and the box's SSH host key is pinned on first use and checked after.

---

## 4. Enrolment: on the LAN, by hand, once per client

### 4.1 Laptops and desktops: `ling node remote join`

A launcher subcommand (`prepare_args` intercepts it like `node`), run **on the client** while it is on the node's LAN:

1. **Refuses off the LAN.** The client must see the node's advertisement (NODE §4) in a browse and reach the node's management on the LAN address the advertisement resolved to; the overlay is not a route for enrolment. Off the LAN it prints why and stops.
2. **Asks the node for a bundle, with a pairing code.** A laptop has no SSH pairing with the node today (NODE §9's pairing is node to node, `ling-admin node add`; the laptop test of 2026-10-10 found its node with no pairing at all), so the bundle goes the way a phone pairs with `ling web` (ASK §18.2): on the node, `ling-admin node remote code` prints an eight-digit code, valid ten minutes, one use; on the client, `ling node remote join --code <8 digits>` presents it to management's LAN address. A wrong code ten times withdraws it. The bundle is: the CA certificate, the relay's DNS name, the management URL (`https://<dns-name>`, the box, §2), the overlay domain, the node's overlay name, and a **one-time setup key** management mints for this client, valid ten minutes, one use.
3. **Installs what the client needs:** NetBird's client if absent (pinned release, SHA-256 checked; the distribution package where one is signed), the CA certificate into the system trust store (this is the one step that needs sudo on the client, and the command says so before it asks), and runs NetBird's `up` with the management URL and the setup key.
4. **Records** the node's overlay name and the overlay domain in `$CODEX_HOME/node.json` (§5), never an address, and prints `ling node list` once the node answers over the overlay.

The client's WireGuard key, generated by NetBird's client and never leaving the machine, is its identity from then on. The setup key is spent; a stolen bundle after the ten minutes is worthless.

### 4.2 Phones: documented, not built

The Ask QR pairing (ASK §18.2) is the pattern: the Devices page on `ling web` shows a QR code the phone scans while on the LAN, carrying the same bundle as §4.1 minus the CA installation, which the phone's OS does by hand (the page says how). The address order of ASK §18.2 gains the overlay name after the LAN addresses and before the others. NetBird's mobile clients enrol with a setup key as the desktop one does. Not built in the MVP: the page, the mobile instructions, and the Signal and Telegram bridges' use of the overlay (they do not need it; they run on the node).

### 4.3 What a client does afterwards

Nothing. NetBird's client keeps the overlay up as a service; `ling` resolves the node by its overlay name when the LAN does not answer (§5); `ling web`, paired as before, is reached at the node's overlay name over port 3100 with the same device cookie.

### 4.4 Revoking a client

`ling-admin node remote revoke <peer>` deletes the peer in management (its WireGuard key is forgotten and its overlay address released at once, as NetBird documents); `remote peers` lists them with their last connection. A revoked client's next sync fails and its tunnel closes within NetBird's keepalive. It can be enrolled again only on the LAN.

---

## 5. The locator: one more tier, by name

NODE §6.1's order of resolution becomes:

1. `DREAMFERENCE_VLLM_HOST`.
2. `vllm_host` in a configuration file.
3. This machine is a node: `localhost`.
4. **New: the remembered node's overlay name, first.** `node.json` holds `remote_name` (the node's NetBird DNS name) and `remote_domain`, written at enrolment (§4.1). The locator asks the system resolver for `remote_name` (NetBird's client answers it when the overlay is up, and the query fails at once when it is down); if it resolves, the locator uses `http://<remote_name>:8000` after one `GET /v1/models` that confirms the node's id (the advertisement's `node` is also what management stores as the peer's name, §7). The address NetBird returns is used for that connection and never written down. No browse happens first.
5. The remembered node by id, found on the LAN (tier 4 today).
6. A browse (tier 5 today).

Why the name comes first: a resolver query is instant and fails immediately while the overlay is down, so a client on the LAN with the overlay off loses nothing and falls through to the browse; a client that left the LAN gets its answer without waiting out the browse; and when both would answer they lead to the same node, with the id check guarding the one case where they would not. Both `ling-rs/node-locator/` and its byte-identical copy in `ling-web-rs/src/node_locator.rs` change together, as the project guide requires.

**`node.json`** gains two string fields and nothing else; the schema stays NODE §6.2's. **`ling node list`** shows the overlay name beside the LAN name when one is recorded, and `ling node forget` drops both.

---

## 6. The gate, the audit and the air gap

- **The model gate** (SWE_BENCH §18) does not change. Its `networks` list is the admitted list during a run, the run's own network and nothing else; the overlay is one more interface the model server already listens on, and the gate's refusal covers it: a remote client during a benchmark gets the same 503 with `Retry-After` as a LAN client, not a dropped connection, and `ling-admin night pause` lets it through like the others.
- **The egress audit** gains a **declared exception** in the SIGNAL §10 pattern: when `mightling-remote-tunnel.service` is enabled, the verdict adds "Remote access enabled: the node keeps an SSH tunnel to `<dns-name>` and NetBird's client reaches its relay"; `audit egress --remote` traces the tunnel and the client for one minute and passes only if every connection is the box's name on 22 or 443, STUN on the box, or a peer's WireGuard endpoint. The ordinary `ling exec` trace still passes with no connection beyond loopback: the agent itself never touches the overlay.
- **`/airgapped on`** says what it does to the tunnel: nothing. The air gap closes the agent's commands' network, not the node's services; a remote client at `/airgapped on` still reaches the node (the model server is exempt at every level, AIRGAPPED spec), and the session's `/airgapped` line names the overlay as one more thing the level does not cover, beside MCP servers and the web chat.

---

## 7. The advertisement

The node's `_mightling-node._tcp` record (NODE §4) gains one TXT key, `remote`, the node's overlay name, present only after `remote setup`. A LAN client that pairs (`ling node add`, NODE §9) reads it and stores it in `node.json` before it ever leaves the LAN, so the name tier (§5) works for a client that enrolled through §4.1 on an earlier visit and re-paired since. The record carries a name, never an address, like every other key.

---

## 8. Matrix moves onto the overlay

CHAT §2 and §5 are rewritten when this is built: the Tailscale paragraphs become "the remote-access overlay", `ling-admin matrix start` checks for an enrolled overlay instead of a signed-in tailnet, the homeserver's server name is the node's overlay name, and `tailscale serve` is replaced by the node's own TLS from the same CA, terminating in the loopback proxy unit the homeserver already has. The phone, enrolled as in §4.2, signs in to `https://<overlay name>` in Element X. Tailscale is no longer named as a requirement; Headscale is no longer named as the self-hosted alternative, since this is one.

---

## 9. Acceptance

1. **By name, from outside.** A laptop on a phone hotspot, enrolled on the LAN the day before, runs `ling exec "Reply with exactly one word: pong"` with nothing configured and gets `pong`; `ling node list` shows the node reached by its overlay name; `node.json` holds no address.
2. **The box reads nothing.** A packet capture on the box during that session shows only TLS records on 443 (the server name being the box's name, as the clients' management URL), WireGuard datagrams if the connection was relayed, and SSH on the tunnel; no plaintext HTTP, no DNS query for a peer name, nothing on any other port, and no connection from the box to anywhere but the node's tunnel.
3. **Revocation cuts.** `ling-admin node remote revoke <laptop>` on the node; the laptop's next `ling exec` fails to resolve or connect within NetBird's keepalive, and `ling node remote join` from off the LAN is refused.
4. **The audit passes.** `ling-admin audit egress` on the node passes with the declared exception named; `audit egress --remote` passes; the ordinary `ling exec` trace shows no connection beyond loopback.
5. **The suite stays offline.** Tests mock SSH (the existing guard refuses a real one) and NetBird (a stand-in `netbird` binary in `tmp_path`, like the fake `ling` of the Night Shift tests); no test opens a port or resolves a name.
6. **Nothing for the others.** A machine that never ran `remote setup` shows no new unit, no new TXT key, the same audit verdict, and `ling node list` unchanged.

---

## 10. Non-goals

- **No VPN or protocol of Mightling's own.** NetBird's client and services are used unmodified; this document only places them.
- **No public CA required.** The node's CA signs everything; Let's Encrypt is not needed and not used. A user who wants a public certificate on the box's relay can put one there by hand.
- **No identity provider.** Enrolment is by setup key on the LAN; NetBird's dashboard and OIDC login are not deployed.
- **No multi-node mesh in the MVP.** One primary node runs the control plane; a second node is a peer like a laptop (its own `remote join`), and jobs between nodes stay on the SSH pairing of NODE §9.
- **Phones:** documented (§4.2), not built.
- **No change to what the agent may reach:** the overlay is the node's and the clients'; a sandboxed command on the node is not on it.

---

## 11. Open questions

*Answered at the desk on 2026-10-20 where the source can answer: §14. What is left is §14.5.*

1. **Management and signal behind a TCP pass-through with their own certificates, on one server name.** NetBird documents `NB_CERT_FILE`/`NB_CERT_KEY` for both services and documents proxies that terminate TLS for them; it does not document the combination used here (TLS served by the services themselves, a pass-through that terminates nothing). A pass-through routes by server name, so the two services need either two names at the box (`mgmt.<dns-name>`, `signal.<dns-name>`) or one listener serving both. **Recommendation:** the combined `netbird-server` container on the node, which multiplexes management and signal on one port, makes the pass-through a single rule and removes the h2c question (§2); Phase 0 confirms that it serves its own TLS from certificate files, with a `socat`-style pass-through on a scratch box, before the command is written. If it cannot, the MVP runs the separate services under two names.
2. **A private CA for management and signal in the client.** Verified for the relay (§12); the client's gRPC connections to management and signal are assumed to use the same system trust store. Phase 0 confirms it on Linux and macOS before §4.1's bundle is designed around it.
3. **The pass-through on the box:** a pinned release of a small SNI router, or the distribution's `haproxy`. The former keeps "nothing else on the box" literal; the latter is signed by the distribution. *Decided 2026-10-20: gost, pinned, with a second name for the relay (§14.3, item 1).*
4. **The relay's certificate:** from the node's CA (the design) or a public one on the box. If NetBird's mobile clients cannot be made to trust a private CA for the relay, the relay alone gets a public certificate; the control plane does not.
5. **TURN:** whether any client of the user's needs `coturn` beside NetBird's relay. Deployed only on evidence.
6. **The overlay's DNS domain:** NetBird's default or a Mightling one. The default is verified to work; a custom one costs one setting.
7. **Node-to-node jobs over the overlay** (NODE §9 today runs them over the SSH pairing on the LAN): out of the MVP; the question is whether a second node away from the LAN should run jobs at all.

---

## 12. Verified against NetBird's documentation (2026-10-10)

Read on the day from `docs.netbird.io` and NetBird's GitHub, not from memory:

- **Components:** client, management, signal and relay, with coturn as the older separate STUN/TURN service; recent releases embed STUN in the relay and offer a combined `netbird-server` container that merges management, signal and relay (external-reverse-proxy page; "How NetBird Works").
- **The address range:** NetBird allocates peer addresses from the carrier-grade NAT block (RFC 6598), a random /16 per account by default, and releases a deleted peer's address at once ("How NetBird Works"). Not written here as digits, by this document's rule.
- **Setup keys:** `netbird up --setup-key <key>` registers a machine without an interactive login; a configuration file can pre-populate the client (setup-keys page).
- **Relay authentication:** every relay shares one secret with the main server (`NB_AUTH_SECRET` on the relay, `relays.secret` in the server's configuration); a wrong character gives a relay that looks healthy and refuses every peer; `relay.env` is to be root-readable only (external-relays page).
- **Relay TLS:** the relay terminates TLS itself (`NB_LETSENCRYPT_*`, or its own certificate files); NetBird advises against adding a proxy just to terminate TLS on the relay host, since it costs QUIC; its exposed address is written `rels://<name>:443` (external-relays page).
- **The client's trust store for the relay:** the external-relays page states that the client uses the system trust store and has no setting for a separate CA; so a private CA must be installed in the system store on each client, which §4.1 does.
- **Management and signal certificates:** `NB_CERT_FILE` and `NB_CERT_KEY` exist for both services, and `NETBIRD_MGMT_DNS_DOMAIN` (default `netbird.selfhosted`) sets the peers' DNS domain (environment-variables page).
- **Proxies in front of management:** a reverse proxy that terminates TLS must support HTTP/2 and gRPC (h2c to the backend); the combined container listens on one port (external-reverse-proxy page). A pass-through that terminates nothing is outside what the page describes: open question 1.
- **Signal's privacy:** messages through signal are end-to-end encrypted between the peers; signal sees the two public keys and never the contents ("How NetBird Works").

Not verified, and marked open above: the pass-through combination (11.1), the private CA for management and signal in the client (11.2), the mobile clients' trust store (11.4).

---

## 13. Phases (decided 2026-10-20)

The owner's decision of 2026-10-20 was to build the MVP. Each phase is a branch off `main`, merged after the full gate (the Python suite; the launcher's `cargo test` in a scratch export, never in `codex/`; the locator's byte-identical copies changed together), and reported before the next starts. No phase deploys anything to a rented box or turns NetBird on on a real machine: the live tests wait for the owner (`docs/dev/remote-access.md`, *Live-test plan*).

- **Phase 0, desk (branch `remote/phase0-verification`):** NetBird's documentation and source read again on the day, the pins chosen from published checksum files, and the conflicts this document left open decided (§14). The live half of the original Phase 0 (a scratch box, §11.1 and §11.2 measured) is step 1 of the live-test plan.
- **Phase 1, the node:** the CA and the certificates (`openssl`, no new Python dependency); the control-plane container, pinned by digest, on loopback, memory-capped; the relay's secret; the node enrolled as the first peer; the tunnel's user unit; `ling-admin remote setup|status|remove` for the node's half; `ling-admin node remote code|peers|revoke`; the gate's probe answer carrying the node id (§14.3).
- **Phase 2, the box:** the box's half of `remote setup|status|remove` over SSH, as a printed plan of steps the tests check (the pattern of `ling signal setup`): the pinned relay, the pass-through, the units, the firewall, the host key pinned on first use, a tunnel account that can forward one port and nothing else. Built and tested, never run against a box by an agent.
- **Phase 3, the client:** `ling node remote join` in the launcher (`prepare_args` already intercepts `node`, so no Codex patch); the name tier of §5 in both copies of the locator; `node.json`'s `remote_name` and `remote_domain`, written only when set; `ling node list` and `forget`; the advertisement's `remote` TXT key (§7).
- **Phase 4, the audit and the air gap:** the declared exception, `audit egress --remote`, and the `/airgapped` line (§6).
- **Phase 5, Matrix on either overlay** (owner's decision of 2026-10-20, replacing §8's "Tailscale is no longer named"): the homeserver reachable by name on the NetBird overlay, which is the default once it is set up, with Tailscale kept as the option for a user who already has a tailnet; `ling-admin matrix start` chooses the overlay; MIGHTLING_CHAT §2 and §5 and `docs/dev/messengers.md` rewritten. A homeserver's server name is permanent (every account id ends in it), so the choice is made when a homeserver is created: an existing Tailscale homeserver keeps its name and its overlay until `matrix remove`.

---

## 14. Phase 0, at the desk (2026-10-20)

NetBird's documentation and its source at tag `v0.81.3` were read on the day (`docs.netbird.io`; `github.com/netbirdio/netbird`, the release's checksums file and Docker Hub's tag records), not from memory. Nothing was installed or run. What only a live test can show stays open and is step 1 of the live-test plan (`docs/dev/remote-access.md`).

### 14.1 Pins

NetBird **0.81.3** (released 2026-10-19, the latest stable on the day). Each value was read from the release's `netbird_0.81.3_checksums.txt` or from Docker Hub's record of the tag, and the client archives also match the GitHub release's own asset digests (linux arm64 from the checksums file only). Phase 1 may move to a later release; a pin is changed only with a value read from a published checksum or digest.

| What | Where it runs | Pinned file or image | SHA-256 |
|---|---|---|---|
| Client, linux amd64 | clients, the node | `netbird_0.81.3_linux_amd64.tar.gz` | `cbdea310068e35449f034544d5137fefee8f6d948fffd020883ffc8b3bab63b1` |
| Client, linux arm64 | the GB10 node, clients | `netbird_0.81.3_linux_arm64.tar.gz` | `980eef938177108bdfa7d802566b6309123592a58631349a78f11cd97c76c22d` |
| Client, macOS amd64 | clients | `netbird_0.81.3_darwin_amd64.tar.gz` | `95ef6a3f3c56d7d7d96e66fdf2523a6ee8c719b8beb959e9f15c6a101e4cac1f` |
| Client, macOS arm64 | clients | `netbird_0.81.3_darwin_arm64.tar.gz` | `bc37108ee88cbc0a998031231570a2c62f03c0937867a0114b8d137e7840fe71` |
| Combined server (management, signal) | the node, Docker | `netbirdio/netbird-server:0.81.3`, multi-architecture index | `541ecef7e98a66b17411d44e9d4dcfdb65913415f39b3b944b8e7e748f997f1d` |
| Relay | the box, as a binary (§14.2, item 3) | `netbirdio/relay:0.81.3`, index; amd64 manifest; arm64 manifest | `721a15a9de4817b142134b5e039bfc3559b0c80db8054408d3723bc52ccba4ec`; `de516151c3d90f8bdc8e55af34eb14a727dac6b167308cb6d771232a9a970324`; `f5f7bba04394051a67e5c6ed57c4ce5d4ee0b4fb77804ce317cb0b894cba41c7` |
| SNI router (gost) | the box | `gost_3.3.0_linux_amd64.tar.gz`; `gost_3.3.0_linux_arm64.tar.gz` (go-gost/gost v3.3.0, `checksums.txt`) | `676fb7f78d267b6ae73df719c0c7f2b565dde7147da935cfafbc1e1da558b6d5`; `d03699e3f385d4ff5dad68046712adfcc7515325a064d2ab046e0bece30f8f8f` |

### 14.2 Confirmed from the source

1. **The combined server serves its own TLS from files** (`server.tls.certFile`, `server.tls.keyFile` in its YAML configuration; `combined/cmd/root.go`), and one `listenAddress` carries management's gRPC, the REST API and signal (over WebSocket). §11.1's recommendation holds: one server name for the control plane, one route at the box. What remains for the live test is the path itself (gRPC and WebSocket through a TCP pass-through and an SSH tunnel), which nothing in the code argues against.
2. **The client trusts the system store and nothing else.** Management and signal (`client/grpc/dialer.go`), the relay's WebSocket (`shared/relay/client/dialer/ws/ws.go`) and its QUIC (`shared/relay/tls/client_prod.go`) all dial with `x509.SystemCertPool()`; there is no flag or variable for another CA. §11.2 is answered for the code: the CA goes into the system store (`update-ca-certificates` on Debian and Ubuntu; the System keychain, trusted for SSL, on macOS, where NetBird's daemon runs as root). macOS is still measured live.
3. **The relay ships only as an image.** The release has no relay or server archive (`.goreleaser.yaml` packages the client and some tools). The relay image's binary, `/go/bin/netbird-relay`, is built with `CGO_ENABLED=0`, so it is static. Since the box gets no Docker, `remote setup` takes the binary out of the image **pinned by its per-architecture manifest digest** on the node (`docker create`, `docker cp`), records its SHA-256, copies it to the box and checks it there. The digest is the pin; the binary's own SHA-256 is written into the code in Phase 2 from the first extraction.
4. **An administrator token without an identity provider or a dashboard.** With `NB_SETUP_PAT_ENABLED=true`, an unauthenticated `POST /api/setup` creates the owner account and returns a personal access token (`create_pat`, `pat_expire_in` at most 365 days), and works only while no account exists (`docs.netbird.io/selfhosted/automated-setup`). `remote setup` calls it **on loopback, before the tunnel exists**, so nothing outside the node can reach the endpoint in that window. The token is kept in the remote state (0600, never leaves the node), rotated by `remote setup` before it expires (`POST /api/users/{id}/tokens`), and `remote status` warns 30 days ahead. The calls: `POST /api/setup-keys`, `GET /api/peers`, `DELETE /api/peers/{id}`, header `Authorization: Token <token>`.
5. **Setup keys live at least a day.** `expires_in` is 86,400 s at the least, so §4.1's ten minutes is the enrolment code's, not the key's: `node remote code` mints a one-off, non-ephemeral key with `usage_limit` 1 and revokes it in management when its ten minutes end unused. (An ephemeral key would give the peer an address-derived name suffix, which this design avoids.)
6. **Peer names** are the hostname's first label, lowercased (`gx10-1a2b` → `gx10-1a2b.netbird.selfhosted`); a taken label gets a suffix derived from the peer's overlay address. The bundle therefore carries the node's label as management reports it, never one guessed from the hostname.
7. **The DNS domain is not a configuration key** of the combined server: a fresh account gets `netbird.selfhosted` and the API's account setting `dns_domain` changes it. §11.6: the default is kept.
8. **The server's STUN must be pointed at the box** (`server.stuns`), or peers are handed STUN on the node, where nothing forwards UDP; **`server.relays.addresses` switches the local relay off**; **`server.store.encryptionKey` must be set before the first start** (otherwise a new key is generated on every start). Relay variables: `NB_LISTEN_ADDRESS`, `NB_EXPOSED_ADDRESS`, `NB_AUTH_SECRET`, `NB_TLS_CERT_FILE`, `NB_TLS_KEY_FILE`, `NB_ENABLE_STUN`, `NB_STUN_PORTS` (3478), health at `/health` on `NB_HEALTH_LISTEN_ADDRESS` (`:9000`). The relay's WebSocket (TCP) and QUIC (UDP) bind the same address; QUIC only with TLS.
9. **Peer names on the client:** on Linux with systemd-resolved, NetBird's client configures resolved over D-Bus; on macOS 0.81.3 installs a scoped resolver for the peer zone (`host_darwin.go`), while the DNS page says peer names may not resolve on macOS without a nameserver group. The system resolver path (`getaddrinfo`) is what the locator's tier uses; macOS is measured live.

### 14.3 Decisions this document left open

1. **Port 443 under two names (§2, §11.3).** The design put the relay, which terminates its own TLS, at `rels://<dns-name>:443` *and* the pass-through on the same 443 with the same server name; a router reading the server name cannot tell the two apart. **Decided:** the box answers two names, both the user's: `<dns-name>` for the control plane (passed through to the node) and `relay.<dns-name>` for the relay (`remote setup --relay-name` names another). The SNI router is **gost 3.3.0, pinned** (§14.1), not the distribution's haproxy: it is one static binary like the relay, needs no package manager (so any distribution with systemd will do), and keeps "nothing else is installed" literal. The relay listens on port 8443, TCP and UDP (it binds one address for both); the box's firewall admits TCP 8443 only from the router on loopback and UDP only through a redirect of UDP 443, and the relay announces `rels://relay.<dns-name>:443`; STUN on 3478/udp. The certificates follow: management's lists `<dns-name>`, the relay's `relay.<dns-name>`. The fallback, if the owner wants one name only: the relay on a port of its own (TCP and UDP), the pass-through a plain forward with no router at all.
2. **The node id behind the name (§5).** `GET /v1/models` carries no node id, and no service of the node returns it over HTTP today. **Decided:** the gate's probe answer (`/mightling-gate`, answered even during a benchmark run, before the refusal) gains `node`, read from a file the host writes beside the gate's state; the locator's name tier reads it and uses the node only when it is the remembered id. The overlay name stays the hostname's label, readable on a phone (Matrix, `ling web`), rather than the id.
3. **Leaf certificates live 397 days, the CA ten years.** macOS refuses a server certificate valid for more than 825 days even under a private root it trusts; 397 keeps every browser quiet as well. `remote setup` reissues a leaf with less than 30 days left (§3's reconcile), and `remote status` says when.
4. **The tunnel account on the box** forwards one port and nothing else: `remote setup` makes a system user `mightling-tunnel` whose only key is one the node generated for the tunnel, with `restrict,port-forwarding,permitlisten="127.0.0.1:10443",command="/bin/false"`. The owner's own key is used for the setup over SSH and never by the unit.
5. **Matrix on either overlay** (§13, Phase 5): the homeserver's TLS on the NetBird overlay needs a TLS-terminating piece, which `systemd-socket-proxyd` is not; tuwunel's own TLS settings are the first candidate, decided in that phase.

### 14.4 What the rented box needs (the owner's decision)

- **A small Linux VM**, amd64 or arm64, with systemd and OpenSSH (any current Debian or Ubuntu; no Docker, no package installed by `remote setup`), about 256 MB of memory free. A dedicated box: `remote setup` changes its firewall (an nftables table of its own) and `remote remove` takes back only what setup installed.
- **Two DNS names the owner controls**, both pointing at it: `<dns-name>` and `relay.<dns-name>` (an A or AAAA record each, or a wildcard).
- **The provider's firewall open** for TCP 443, UDP 443 and UDP 3478 inbound, and TCP 22 from wherever the node is (the node dials out; nothing dials the node).
- **`ssh <dns-name>` working from the node** with the owner's key, as root or a passwordless sudoer, for `remote setup` and `remote remove` only.
- **The existing `relay-box` is not assumed.** It runs the owner's own services and nothing of this design is put there unless the owner decides so; the requirements above are what that decision needs to know (port 443 and UDP 443 would be taken by the relay and the router).

### 14.5 Still open, for the live test

- §11.1's path through a pass-through and an SSH tunnel (14.2, item 1).
- §11.2 on macOS: the CA in the System keychain trusted by NetBird's root daemon (14.2, item 2).
- §11.4: whether the mobile clients trust a private CA for the relay (phones are not built in the MVP).
- §11.5 (TURN): on evidence only, as before.
- Peer names through the system resolver on macOS (14.2, item 9).
- Whether a peer that dials `rels://…:443` uses QUIC through the UDP redirect, or falls back to the WebSocket (either works; the redirect is there so the first can).
