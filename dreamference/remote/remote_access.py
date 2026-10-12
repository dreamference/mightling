"""
`ling-admin remote setup|status|remove|code|peers|revoke` (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §3, §4).

Remote access lets a client off the LAN reach its node by name, through a NetBird overlay whose
control plane runs on the node and one rented box that sees only ciphertext. Nothing changes for a
user who never runs `remote setup`: no unit, no container, no advertisement key, no port.

`setup` is idempotent: run again, it keeps the CA, the secret, the token and the peers, reissues
nothing that is still valid, and redeploys the box (which is how a replaced box is set up). `remove`
undoes the box and the tunnel and keeps the node's CA and peers, so a new box under the same name
needs no re-enrolment; `--purge` deletes them too and takes the node out of the overlay.

`setup --box-only` is the first half on its own: the box is deployed and the node's CA, certificates
and tunnel key are made, but nothing runs on the node (no container, no tunnel, no NetBird client,
no sudo). It lets the box be checked before the node is touched; `setup` without it completes the rest.
"""

import ipaddress

import platform
import secrets
import socket
import time
from typing import Any, Dict, List, Optional

from dreamference.remote.remote_box import RemoteBox
from dreamference.remote.remote_certificate_authority import RemoteCertificateAuthority
from dreamference.remote.remote_control_plane import RemoteControlPlane
from dreamference.remote.remote_enrolment import RemoteEnrolment
from dreamference.remote.remote_node_peer import RemoteNodePeer
from dreamference.remote.remote_settings import (
    BOX_PUBLIC_PORT,
    CODE_MINUTES,
    ENROL_PORT,
    NETBIRD_VERSION,
    OVERLAY_DOMAIN,
    RELAY_PORT,
    STUN_PORT,
    TUNNEL_UNIT,
    RemoteSettings,
)
from dreamference.remote.remote_tunnel import RemoteTunnel


class RemoteAccess:
    """The commands."""

    # -- seams the tests replace ------------------------------------------------------------------

    @classmethod
    def _confirm(cls, question: str) -> bool:
        try:
            return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            return False

    @classmethod
    def _node_id(cls) -> Optional[str]:
        from dreamference.node.node_identity import NodeIdentity
        return NodeIdentity.read()

    @classmethod
    def _advertise(cls, overlay_name: Optional[str]) -> None:
        from dreamference.node.node_service_file import NodeServiceFile
        NodeServiceFile.update(remote=overlay_name)

    # -- setup ------------------------------------------------------------------------------------

    @classmethod
    def ssh_destination_problem(cls, ssh_to: str) -> Optional[str]:
        """
        Args:
            ssh_to: What `--ssh` named.

        Returns:
            Optional[str]: Why it will not do, or None. An address is refused: nothing in remote
            access writes one down, so the user names the box by a `Host` alias instead.
        """
        if not ssh_to or any(c.isspace() for c in ssh_to) or ssh_to.startswith("-"):
            return f"{ssh_to!r} is not an SSH destination."
        host = ssh_to.rpartition("@")[2].strip("[]")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return None
        return (f"{ssh_to!r} is an address, and remote access writes no address down. "
                "Give the box a Host alias in ~/.ssh/config and name that.")

    @classmethod
    def plan_lines(cls, dns_name: str, overlay_name: str, ssh_to: Optional[str] = None,
                   box_only: bool = False) -> List[str]:
        """
        Args:
            dns_name: The box's public DNS name.
            overlay_name: The node's overlay name.
            ssh_to: The user's SSH destination for the box, when not the DNS name.
            box_only: Only the box (`setup --box-only`).

        Returns:
            List[str]: What `setup` will change, said before anything is done.
        """
        over = f"over your SSH ({'ssh ' + (ssh_to or dns_name)}, root or passwordless sudo)"
        node = ([
            "  On this node, files only (nothing runs here, no sudo):",
            "    - the node's certificate authority (name-constrained to "
            f"{dns_name} and {OVERLAY_DOMAIN}) if there is none, and certificates from it",
            "    - the relay's shared secret and the tunnel's SSH key",
        ] if box_only else [
            "  On this node:",
            "    - make the node's certificate authority (name-constrained to "
            f"{dns_name} and {OVERLAY_DOMAIN}) if there is none, and certificates from it",
            "    - run NetBird's management and signal as the container dreamference-remote, on loopback",
            f"    - keep an SSH tunnel to {dns_name} (user unit {TUNNEL_UNIT})",
            "    - install NetBird's client and the CA (one sudo) and join the overlay as "
            f"{overlay_name}",
        ])
        return [
            f"Remote access through {dns_name} (NetBird {NETBIRD_VERSION}){', the box only' if box_only else ''}. This will:",
            *node,
            f"  On {dns_name}, {over}:",
            "    - install haproxy (the only package) on 443, TCP only, passing the box's own name to the tunnel",
            f"    - install NetBird's relay with STUN (ports {RELAY_PORT} TCP/UDP and {STUN_PORT} UDP), a pinned binary",
            "    - add the account mightling-tunnel, which may only hold the tunnel's listener",
            f"    - open {BOX_PUBLIC_PORT}/tcp, {RELAY_PORT}/tcp+udp and {STUN_PORT}/udp in its firewall if ufw or firewalld runs",
            "  Nothing else is installed on the box, and it never holds a key of the overlay.",
        ]

    @classmethod
    def setup(cls, dns_name: str, yes: bool = False, ssh_to: Optional[str] = None, box_only: bool = False) -> int:
        """
        Sets up remote access, or brings it up to date.

        Args:
            dns_name: The box's public DNS name; `ssh <dns-name>` must work, or `ssh <ssh_to>`.
            yes: Do not ask before starting, and never wait for a sudo password.
            ssh_to: The user's SSH destination for the box (a `Host` alias) when it is not the DNS
                name; remembered in `remote.json` for `status` and `remove`.
            box_only: Deploy the box and stop: nothing runs on the node.

        Returns:
            int: The exit status.
        """
        dns_name = dns_name.strip().lower().rstrip(".")
        if not dns_name or "." not in dns_name or any(c in dns_name for c in "/: @"):
            print(f"❌ {dns_name!r} is not a DNS name. Give the box's public name, e.g. relay.example.org.")
            return 2
        node_id = cls._node_id()
        if not node_id:
            print("❌ This machine is not a Mightling node yet (it has no node id). "
                  "Start its model server once: ling-admin server start")
            return 1
        existing = RemoteSettings.read()
        if existing and existing["dns_name"] != dns_name and RemoteCertificateAuthority.ca_certificate().is_file() \
                and not RemoteCertificateAuthority.permits(dns_name):
            print(f"❌ Remote access is set up through {existing['dns_name']}, and the node's CA may only sign that name.\n"
                  f"   A box under another name needs a new CA and a new enrolment of every client:\n"
                  f"   ling-admin remote remove --purge, then ling-admin remote setup {dns_name}")
            return 1
        if ssh_to is None and existing and existing["dns_name"] == dns_name:
            ssh_to = existing.get("ssh")
        ssh_to = (ssh_to or "").strip() or None
        if ssh_to == dns_name:
            ssh_to = None
        if ssh_to is not None:
            problem = cls.ssh_destination_problem(ssh_to)
            if problem:
                print(f"❌ {problem}")
                return 2
        destination = ssh_to or dns_name
        overlay_name = RemoteSettings.overlay_name(node_id)
        for line in cls.plan_lines(dns_name, overlay_name, ssh_to, box_only):
            print(line)
        if not yes and not cls._confirm("Go ahead?"):
            print("Nothing changed.")
            return 1

        facts = RemoteBox.probe(dns_name, ssh_to=ssh_to)
        if "error" in facts:
            print(f"❌ `ssh {destination}` did not work: {facts['error']}\n"
                  f"   Set it up so that `ssh {destination}` logs in with your key (or name a Host alias with --ssh), "
                  "then run this again.")
            return 1
        if facts.get("root") != "yes":
            print(f"❌ On {dns_name} you are neither root nor allowed passwordless sudo; the box's setup needs one of them.")
            return 1
        if not facts.get("arch"):
            print(f"❌ {dns_name} is neither amd64 nor arm64; NetBird's relay is pinned for those two.")
            return 1
        if facts.get("packages") not in ("apt", "dnf"):
            print(f"❌ {dns_name} has neither apt nor dnf to install haproxy from.")
            return 1
        host_key = facts.get("host_key", "")
        if not host_key.startswith("ssh-ed25519 "):
            print(f"❌ {dns_name} has no ed25519 host key for the tunnel to check.")
            return 1

        print("🔐 Certificates…")
        if not RemoteCertificateAuthority.ensure(dns_name):
            return 1
        control = RemoteCertificateAuthority.issue("control-plane", [dns_name])
        relay = RemoteCertificateAuthority.issue("relay", [dns_name])
        if control is None or relay is None:
            return 1
        secret = RemoteSettings.read_private("relay-secret") or secrets.token_hex(32)
        RemoteSettings.write_private("relay-secret", secret + "\n")

        if box_only:
            return cls._setup_box_only(dns_name, ssh_to, facts, secret, relay, existing, overlay_name)

        print("🧭 Control plane…")
        RemoteControlPlane.write_files(dns_name, secret, control[0].read_text(), control[1].read_text())
        if not RemoteControlPlane.start():
            return 1
        needs_setup = RemoteControlPlane.wait_ready(dns_name)
        if needs_setup is None:
            print("❌ The control plane did not answer on loopback. `docker logs dreamference-remote` says why.")
            return 1
        token = RemoteSettings.read_private("admin-token")
        if needs_setup:
            token = RemoteControlPlane.bootstrap(dns_name, RemoteSettings.node_peer_name(node_id))
        elif token:
            token = RemoteControlPlane.renew_token(dns_name, token) or token
        if not token:
            print("❌ No management token: the control plane was set up before, and its token is lost.\n"
                  "   ling-admin remote remove --purge starts over.")
            return 1
        RemoteSettings.write_private("admin-token", token + "\n")

        print(f"📦 The box ({dns_name}, {facts['arch']})…")
        tunnel_key = RemoteTunnel.public_key()
        if tunnel_key is None:
            print("❌ ssh-keygen could not make the tunnel's key.")
            return 1
        if not RemoteBox.deploy(dns_name, facts["arch"], facts["packages"], secret, relay[0], relay[1], tunnel_key,
                                ssh_to=ssh_to):
            return 1
        RemoteTunnel.write_known_host(dns_name, host_key)

        print("🚇 Tunnel…")
        if not RemoteTunnel.install(dns_name):
            print(f"❌ systemd did not start {TUNNEL_UNIT}: journalctl --user -u {TUNNEL_UNIT}")
            return 1

        record: Dict[str, Any] = {
            "dns_name": dns_name,
            "overlay_name": overlay_name,
            "overlay_domain": OVERLAY_DOMAIN,
            "netbird_version": NETBIRD_VERSION,
            "set_up": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if ssh_to:
            record["ssh"] = ssh_to
        if not (existing or {}).get("node_peer"):
            print("🌐 This node joins the overlay…")
            key = RemoteControlPlane.setup_key(dns_name, token, f"node {node_id}")
            if key is None or not RemoteNodePeer.enrol(RemoteCertificateAuthority.ca_certificate(),
                                                       RemoteSettings.management_url(dns_name), key, node_id, yes):
                RemoteSettings.write(record)
                print("⚠️  The box and the tunnel are up, but this node did not join the overlay. "
                      f"Run ling-admin remote setup {dns_name} again.")
                return 1
        label = cls.node_label(dns_name, token, RemoteSettings.node_peer_name(node_id))
        if label != overlay_name:
            RemoteSettings.write(record)
            print(f"❌ Management names this node {label or 'nothing'}, not {overlay_name}; clients find the node only "
                  "by the latter. `ling-admin remote peers` shows the peers; remove a stale one with `remote revoke`, "
                  f"then run ling-admin remote setup {dns_name} again.")
            return 1
        record["node_peer"] = True
        RemoteSettings.write(record)
        cls._advertise(overlay_name)
        print(f"✅ Remote access is up. This node is {overlay_name} in the overlay.")
        print("   To enrol a laptop: on the node's LAN, run `ling-admin remote code` here,")
        print("   then `ling node remote join --code <the 8 digits>` on the laptop.")
        return 0

    @classmethod
    def _setup_box_only(cls, dns_name: str, ssh_to: Optional[str], facts: Dict[str, str], secret: str,
                        relay: Any, existing: Optional[Dict[str, Any]], overlay_name: str) -> int:
        """
        `setup --box-only` after the checks and the certificates: the tunnel's key, the box, the
        tunnel's known-hosts file and `remote.json`. Nothing starts on the node.

        Args:
            dns_name: The box's public DNS name.
            ssh_to: The user's SSH destination for the box, when not the DNS name.
            facts: The box's probe.
            secret: The relay's shared secret.
            relay: The relay's certificate and key.
            existing: `remote.json` before this run.
            overlay_name: The node's overlay name.

        Returns:
            int: The exit status.
        """
        print(f"📦 The box ({dns_name}, {facts['arch']})…")
        tunnel_key = RemoteTunnel.public_key()
        if tunnel_key is None:
            print("❌ ssh-keygen could not make the tunnel's key.")
            return 1
        if not RemoteBox.deploy(dns_name, facts["arch"], facts["packages"], secret, relay[0], relay[1], tunnel_key,
                                ssh_to=ssh_to):
            return 1
        RemoteTunnel.write_known_host(dns_name, facts["host_key"])
        # A setup that already went further keeps its record: only the box was redeployed.
        record: Dict[str, Any] = dict(existing) if existing and existing.get("node_peer") else {
            "dns_name": dns_name,
            "overlay_name": overlay_name,
            "overlay_domain": OVERLAY_DOMAIN,
            "netbird_version": NETBIRD_VERSION,
            "box_only": True,
        }
        record["set_up"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        record.pop("ssh", None)
        if ssh_to:
            record["ssh"] = ssh_to
        RemoteSettings.write(record)
        again = f"ling-admin remote setup {dns_name}" + (f" --ssh {ssh_to}" if ssh_to else "")
        print(f"✅ The box is deployed: haproxy on {BOX_PUBLIC_PORT} passes {dns_name} to the tunnel's listener, "
              f"the relay listens on {RELAY_PORT} and STUN on {STUN_PORT}.")
        if record.get("box_only"):
            print("   Nothing runs on this node yet. To finish (the control plane, the tunnel, this node's peer):")
            print(f"   {again}")
        return 0

    @classmethod
    def node_label(cls, dns_name: str, token: str, peer_name: str, seconds: int = 30) -> Optional[str]:
        """
        Asks management which overlay name it gave the node: it may change a name (a suffix for a
        duplicate), and clients find the node only by `mightling-<id>.<domain>`.

        Args:
            dns_name: The box's public DNS name.
            token: The management token.
            peer_name: The node's peer name.
            seconds: How long to wait for the peer to appear.

        Returns:
            Optional[str]: The node peer's overlay name, or None when it is not there.
        """
        deadline = time.monotonic() + seconds
        while True:
            peers = RemoteControlPlane.peers(dns_name, token) or []
            labels = [str(peer.get("dns_label", "")).lower() for peer in peers
                      if str(peer.get("name", "")).lower() == peer_name]
            if labels:
                return labels[0]
            if time.monotonic() >= deadline:
                return None
            time.sleep(2)

    # -- status, peers, revoke --------------------------------------------------------------------

    @classmethod
    def status(cls) -> int:
        """
        Prints the tunnel's state, the box's units, the CA's expiry and the peers.

        Returns:
            int: 0 when everything answers, 1 when something does not, 3 when not set up.
        """
        record = RemoteSettings.read()
        if record is None:
            print("Remote access is not set up (ling-admin remote setup <box's DNS name>).")
            return 3
        dns_name = record["dns_name"]
        ssh_to = record.get("ssh")
        healthy = True
        print(f"Box:        {dns_name}" + (f" (ssh {ssh_to})" if ssh_to else ""))
        units = RemoteBox.unit_states(dns_name, ssh_to=ssh_to)
        if units is None:
            healthy = False
            print(f"Box units:  `ssh {ssh_to or dns_name}` did not answer")
        else:
            healthy &= units == {"haproxy": "active", "relay": "active"}
            print(f"Box units:  haproxy {units['haproxy']}, relay {units['relay']}")
        port = RemoteBox.check_public_port(dns_name, RemoteCertificateAuthority.ca_certificate())
        healthy &= port == {"own": "verified", "other": "closed"}
        print(f"Port {BOX_PUBLIC_PORT}:   {RemoteBox.public_port_line(port)}")
        expiry = RemoteCertificateAuthority.expiry(RemoteSettings.path("certs", "control-plane.crt"))
        if expiry is not None:
            days = int((expiry.timestamp() - time.time()) // 86400)
            print(f"Certificates: valid {days} more days" + ("  ⚠️ run `ling-admin remote setup` to reissue" if days < 30 else ""))
        if record.get("box_only"):
            print("This node:  not set up yet (--box-only): no control plane, no tunnel, no peer.")
            print(f"            ling-admin remote setup {dns_name}" + (f" --ssh {ssh_to}" if ssh_to else "")
                  + " finishes it.")
            return 1
        print(f"This node:  {record.get('overlay_name')} (overlay {record.get('overlay_domain')})")
        tunnel = RemoteTunnel.state()
        healthy &= tunnel == "active"
        print(f"Tunnel:     {tunnel} ({TUNNEL_UNIT})")
        print(f"Control:    {'running' if RemoteControlPlane.running() else 'NOT running'} (dreamference-remote)")
        healthy &= RemoteControlPlane.running()
        token = RemoteSettings.read_private("admin-token")
        peers = RemoteControlPlane.peers(dns_name, token) if token else None
        if peers is None:
            healthy = False
            print("Peers:      management did not answer")
        else:
            connected = sum(1 for peer in peers if peer.get("connected"))
            print(f"Peers:      {len(peers)} enrolled, {connected} connected (ling-admin remote peers)")
        return 0 if healthy else 1

    @classmethod
    def peer_lines(cls, peers: List[Dict[str, Any]]) -> List[str]:
        """
        Args:
            peers: Management's peers.

        Returns:
            List[str]: One line per peer: name, overlay name, connected or last seen, system.
        """
        lines = []
        for peer in sorted(peers, key=lambda entry: str(entry.get("name", ""))):
            seen = "connected" if peer.get("connected") else f"last seen {peer.get('last_seen', '?')}"
            lines.append(f"  {peer.get('name', '?'):<28} {peer.get('dns_label', ''):<52} {seen:<34} {peer.get('os', '')}")
        return lines

    @classmethod
    def peers(cls) -> int:
        """
        Lists the enrolled peers.

        Returns:
            int: The exit status.
        """
        record = RemoteSettings.read()
        token = RemoteSettings.read_private("admin-token")
        if record is None or not token:
            print("Remote access is not set up.")
            return 3
        peers = RemoteControlPlane.peers(record["dns_name"], token)
        if peers is None:
            print("❌ Management did not answer (ling-admin remote status).")
            return 1
        for line in cls.peer_lines(peers):
            print(line)
        return 0

    @classmethod
    def revoke(cls, name: str) -> int:
        """
        Deletes a peer: its key is forgotten and its overlay address released at once; it can be
        enrolled again only on the LAN.

        Args:
            name: The peer's name or overlay name (or an unambiguous prefix of either).

        Returns:
            int: The exit status.
        """
        record = RemoteSettings.read()
        token = RemoteSettings.read_private("admin-token")
        if record is None or not token:
            print("Remote access is not set up.")
            return 3
        peers = RemoteControlPlane.peers(record["dns_name"], token)
        if peers is None:
            print("❌ Management did not answer (ling-admin remote status).")
            return 1
        wanted = name.strip().lower()
        matches = [peer for peer in peers
                   if str(peer.get("name", "")).lower().startswith(wanted) or str(peer.get("dns_label", "")).lower().startswith(wanted)]
        exact = [peer for peer in matches if wanted in (str(peer.get("name", "")).lower(), str(peer.get("dns_label", "")).lower())]
        matches = exact or matches
        if len(matches) != 1:
            print(f"❌ {'No peer' if not matches else 'Several peers'} match {name!r}:")
            for line in cls.peer_lines(matches or peers):
                print(line)
            return 1
        peer = matches[0]
        if str(peer.get("dns_label", "")).startswith(record.get("overlay_name", "\0")):
            print("❌ That is this node; `ling-admin remote remove --purge` takes it out.")
            return 1
        if not RemoteControlPlane.delete_peer(record["dns_name"], token, peer["id"]):
            print("❌ Management refused to delete it.")
            return 1
        print(f"✅ {peer.get('name')} is revoked: its key is forgotten; it can rejoin only on the LAN with a new code.")
        return 0

    # -- enrolment --------------------------------------------------------------------------------

    @classmethod
    def bundle(cls, record: Dict[str, Any], token: str, node_id: str, client: str) -> Optional[Dict[str, Any]]:
        """
        Makes one client's bundle, minting its setup key.

        Args:
            record: `remote.json`.
            token: The management token.
            node_id: The node's id.
            client: The client's host name, for the key's name.

        Returns:
            Optional[Dict[str, Any]]: The bundle, or None.
        """
        key = RemoteControlPlane.setup_key(record["dns_name"], token, f"client {client}")
        if key is None:
            return None
        return {
            "version": 1,
            "node": node_id,
            "name": socket.gethostname(),
            "ca": RemoteCertificateAuthority.ca_certificate().read_text(),
            "management_url": RemoteSettings.management_url(record["dns_name"]),
            "relay": RemoteSettings.relay_address(record["dns_name"]),
            "overlay_domain": record.get("overlay_domain", OVERLAY_DOMAIN),
            "overlay_name": record["overlay_name"],
            "netbird_version": NETBIRD_VERSION,
            "setup_key": key,
        }

    @classmethod
    def code(cls) -> int:
        """
        Prints a code and waits, on the LAN, for one client to enrol with it.

        Returns:
            int: 0 when a client enrolled, 1 otherwise.
        """
        record = RemoteSettings.read()
        token = RemoteSettings.read_private("admin-token")
        node_id = cls._node_id()
        if record is None or not token or not node_id:
            print("Remote access is not set up (ling-admin remote setup <box's DNS name>).")
            return 3
        code = RemoteEnrolment.new_code()
        print(f"🔢 Code: {code[:4]} {code[4:]}   (valid {CODE_MINUTES} minutes, for one client, on this LAN)")
        print(f"   On the client, on this LAN:  ling node remote join --code {code}")
        print(f"   Waiting on port {ENROL_PORT}… (Ctrl-C withdraws it)")
        try:
            outcome = RemoteEnrolment.serve(code, lambda client: cls.bundle(record, token, node_id, client))
        except OSError as error:
            print(f"❌ Could not listen on port {ENROL_PORT}: {error}")
            return 1
        except KeyboardInterrupt:
            print("\nCode withdrawn.")
            return 1
        if outcome.startswith("enrolled:"):
            print(f"✅ {outcome.split(':', 1)[1]} has its bundle and is joining the overlay (ling-admin remote peers).")
            return 0
        print("Code withdrawn after ten wrong attempts." if outcome == "withdrawn" else "Code expired; nobody used it.")
        return 1

    # -- remove -----------------------------------------------------------------------------------

    @classmethod
    def remove(cls, purge: bool = False, yes: bool = False) -> int:
        """
        Removes the box's units and files and the tunnel; with `purge`, also the control plane,
        the CA, the peers and the node's own peer.

        Args:
            purge: Delete the node's CA and the overlay too (every client must enrol again).
            yes: Do not ask.

        Returns:
            int: The exit status.
        """
        record = RemoteSettings.read()
        if record is None and not RemoteSettings.folder().exists():
            print("Remote access is not set up.")
            return 0
        dns_name = (record or {}).get("dns_name")
        what = "the box's relay and haproxy, the tunnel" + (
            ", the control plane, the node's CA and every enrolment" if purge else "")
        if not yes and not cls._confirm(f"Remove {what}?"):
            print("Nothing changed.")
            return 1
        status = 0
        if dns_name and not RemoteBox.remove(dns_name, ssh_to=(record or {}).get("ssh")):
            status = 1
        RemoteTunnel.remove()
        if purge:
            RemoteControlPlane.stop()
            if (record or {}).get("node_peer"):
                RemoteNodePeer.leave(yes)
            import shutil
            shutil.rmtree(RemoteSettings.folder(), ignore_errors=True)
            cls._advertise(None)
            print("✅ Remote access removed, CA and enrolments included.")
        else:
            print("✅ The box and the tunnel are removed. The control plane, the CA and the enrolments are kept: "
                  "`ling-admin remote setup <name>` on a new box under the same name brings everyone back.")
        return status

    @classmethod
    def summary(cls) -> Dict[str, Any]:
        """
        Returns:
            Dict[str, Any]: For `ling-admin status` and the audit: whether it is set up, the box,
            the overlay name and the tunnel's state.
        """
        record = RemoteSettings.read()
        if record is None:
            return {"set_up": False}
        return {"set_up": True, "dns_name": record["dns_name"], "overlay_name": record.get("overlay_name"),
                "box_only": bool(record.get("box_only")), "tunnel": RemoteTunnel.state(), "system": platform.system()}
