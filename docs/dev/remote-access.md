# Remote access: the NetBird overlay

Developer notes behind the remote-access line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md` (the owner's design of 2026-10-10; the build began on 2026-10-20). §13 of the spec is the phase plan, §14 the desk verification that stands in for Phase 0.

## What it is

A client off the LAN reaches its node **by name** over a NetBird overlay whose management, signal and private DNS run on the node, under the node's own certificate authority. One rented box runs only NetBird's relay and a pinned SNI router on port 443: the box's own name passes through, as plain TCP, to the node's control plane over an SSH tunnel the node dials out; `relay.<dns-name>` goes to the relay. The box terminates no TLS for the control plane and holds no key of the network.

**Pieces and pins** (spec §14.1): NetBird 0.81.3. On the node, the combined `netbirdio/netbird-server` image (management, signal; pinned by index digest) on loopback, and NetBird's client from the release archive; on the box, the relay's static binary taken out of `netbirdio/relay` pinned by per-architecture digest (the release ships no relay archive), and gost 3.3.0 as the SNI router. No Docker and no package manager on the box.

**Traps found at the desk** (spec §14.2): the admin token exists only through `POST /api/setup` while no account does, so it is called on loopback before the tunnel is up; setup keys live at least a day, so the enrolment code revokes its unused key itself; `server.stuns` must name the box and `server.store.encryptionKey` must be set before the first start; NetBird's client trusts only the system store; an ephemeral key renames the peer.

## Status

- **Phase 0 (desk, 2026-10-20):** done on branch `remote/phase0-verification`; NetBird's documentation and source read, the pins chosen, the design conflicts of the spec decided (spec §14). The live half of Phase 0 (a scratch box, NetBird on real machines) waits for the owner: see *Live-test plan* below.
- **Phases 1 to 5:** not built. Spec §13: 1 the node, 2 the box (code and tests only), 3 the client, 4 the audit and `/airgapped`, 5 Matrix on either overlay (NetBird by default once it exists, Tailscale kept as the option).

## Hard limits while this is built

- Nothing is deployed to a rented box by an agent. `relay-box` (the owner's GCP relay) runs the owner's own services and is never touched; which box the relay goes on is the owner's decision.
- NetBird is not started and no networking is changed on gx10-1a2b, node-b, the Mac or the MSI desktop until the owner says go.
- No address is stored for a node: the overlay's DNS name is the point. The last-address fallback of `node.json` stays as it is (the owner kept it).
- The suite stays offline: SSH, sudo, Docker and systemd are behind `_run` seams the tests replace, conftest refuses the real ones, and NetBird's client in a test is a stand-in `netbird` under `tmp_path`.

## What the rented box needs (for the owner's decision)

Spec §14.4. In short: a small Linux VM (amd64 or arm64) with systemd and OpenSSH; two DNS names the owner controls pointing at it, `<dns-name>` and `relay.<dns-name>`; TCP 443, UDP 443 and UDP 3478 open inbound at the provider; `ssh <dns-name>` working from the node with the owner's key as root or a passwordless sudoer. Nothing else of the owner's should run on it: `remote remove` takes away what `remote setup` installed, but the box is treated as hostile and disposable.

## Live-test plan (waits for the owner's go)

Run in this order; each step needs the one before it, and each can be stopped and undone with `ling-admin remote remove` on the node.

1. **Phase 0, live, on the chosen box** (spec §14.5): after Phases 1 and 2, `ling-admin remote setup <dns-name>` on gx10-1a2b (it prints its plan before any sudo or SSH change), then one Linux client off the LAN (a phone hotspot) enrolled by hand with `netbird up --management-url https://<dns-name> --setup-key …`, the CA in its system store only; then the same from the Mac. Record: management and signal on one port through the router and the SSH tunnel; macOS's daemon trusting the CA from the System keychain; peer names through macOS's resolver; QUIC through the UDP redirect or the WebSocket fallback.
2. **The setup's own checks:** the units on the box (`systemctl status mightling-relay mightling-sni` over SSH); `ling-admin remote status`; on the node, nothing new listening beyond loopback (`ss -ltnu`); `POST /api/setup` refused once the account exists.
3. **Enrol the laptop on the LAN:** `ling-admin node remote code` on the node, `ling node remote join --code …` on the laptop; `node.json` gains `remote_name` and `remote_domain` and no address.
4. **Acceptance 1 (by name, from outside):** the laptop on a phone hotspot runs `ling exec "Reply with exactly one word: pong"` with nothing configured.
5. **Acceptance 2 (the box reads nothing):** `tcpdump -w` on the box during step 4, read afterwards: only TLS on 443 (server name = the box's name), relayed WireGuard if any, SSH on the tunnel.
6. **Acceptance 3 (revocation):** `ling-admin node remote revoke <laptop>`; the laptop's next `ling exec` fails within NetBird's keepalive; `ling node remote join` off the LAN is refused.
7. **Acceptance 4 (the audit):** `ling-admin audit egress` on the node passes with the declared exception named; `audit egress --remote` passes.
8. **Acceptance 6 (nothing for the others):** node-b, never set up, shows no new unit, no `remote` TXT key, the same audit verdict.
9. **Matrix on the overlay** (the last phase): a fresh homeserver created with the NetBird overlay, Element X on a phone enrolled by hand, sign-in at `https://<overlay name>`; an existing Tailscale homeserver keeps working unchanged.

A SWE-bench night must not be running during steps 2 to 8 on the node that serves it (the gate admits the run's network only; spec §6).
