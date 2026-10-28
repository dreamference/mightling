"""`ling-admin remote setup|status|remove|code|peers|revoke` (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §3, §4).

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

import base64
import ipaddress
import json
import os
import platform
import secrets
import socket
import time
from pathlib import Path
from typing import Any

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

STORE_KEY_BYTES = 32
RENEW_DAYS = 30
ACTIVE = "active"
WITHDRAWN = "withdrawn"
SECRET_BYTES = 32
DOT = "."
ERROR = "error"
YES = "yes"
ED25519 = "ssh-ed25519 "
PACKAGE_TOOLS = ("apt", "dnf")


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
    def _node_id(cls) -> str | None:
        from dreamference.node.node_identity import NodeIdentity

        return NodeIdentity.read()

    @classmethod
    def _advertise(cls, overlay_name: str | None) -> None:
        from dreamference.node.node_service_file import NodeServiceFile

        NodeServiceFile.update(remote=overlay_name)

    # -- setup ------------------------------------------------------------------------------------

    @classmethod
    def ssh_destination_problem(cls, ssh_to: str) -> str | None:
        """Why `--ssh` cannot name the box, if it cannot.

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
        return (
            f"{ssh_to!r} is an address, and remote access writes no address down. "
            "Give the box a Host alias in ~/.ssh/config and name that."
        )

    @classmethod
    def plan_lines(
        cls,
        dns_name: str,
        overlay_name: str,
        ssh_to: str | None = None,
        box_only: bool = False,
        scope: str | None = None,
    ) -> list[str]:
        """What `setup` says before it asks.

        Args:
            dns_name: The box's public DNS name.
            overlay_name: The node's overlay name.
            ssh_to: The user's SSH destination for the box, when not the DNS name.
            box_only: Only the box (`setup --box-only`).
            scope: The domain a new CA would be constrained to; the box's name when not given.

        Returns:
            List[str]: What `setup` will change, said before anything is done.
        """
        over = f"over your SSH ({'ssh ' + (ssh_to or dns_name)}, root or passwordless sudo)"
        node = (
            [
                "  On this node, files only (nothing runs here, no sudo):",
                "    - the node's certificate authority (name-constrained to "
                f"{scope or dns_name} and {OVERLAY_DOMAIN}) if there is none, and certificates from it",
                "    - the relay's shared secret and the tunnel's SSH key",
            ]
            if box_only
            else [
                "  On this node:",
                "    - make the node's certificate authority (name-constrained to "
                f"{scope or dns_name} and {OVERLAY_DOMAIN}) if there is none, and certificates from it",
                "    - run NetBird's management and signal as the container dreamference-remote, on loopback",
                f"    - keep an SSH tunnel to {dns_name} (user unit {TUNNEL_UNIT})",
                "    - install NetBird's client and the CA (one sudo) and join the overlay as "
                f"{overlay_name}",
            ]
        )
        return [
            f"Remote access through {dns_name} (NetBird {NETBIRD_VERSION}){', the box only' if box_only else ''}. "
            "This will:",
            *node,
            f"  On {dns_name}, {over}:",
            "    - install haproxy (the only package) on 443, TCP only, passing the box's own name to the tunnel",
            f"    - install NetBird's relay with STUN (ports {RELAY_PORT} TCP/UDP and {STUN_PORT} UDP), a pinned "
            "binary",
            "    - add the account mightling-tunnel, which may only hold the tunnel's listener",
            f"    - open {BOX_PUBLIC_PORT}/tcp, {RELAY_PORT}/tcp+udp and {STUN_PORT}/udp in its firewall if ufw or "
            "firewalld runs",
            "  Nothing else is installed on the box, and it never holds a key of the overlay.",
        ]

    @classmethod
    def setup(
        cls,
        dns_name: str,
        yes: bool = False,
        ssh_to: str | None = None,
        box_only: bool = False,
        ca_scope: str | None = None,
    ) -> int:
        """Sets up remote access, or brings it up to date.

        Args:
            dns_name: The box's public DNS name; `ssh <dns-name>` must work, or `ssh <ssh_to>`.
            yes: Do not ask before starting, and never wait for a sudo password.
            ssh_to: The user's SSH destination for the box (a `Host` alias) when it is not the DNS
                name; remembered in `remote.json` for `status` and `remove`.
            box_only: Deploy the box and stop: nothing runs on the node.
            ca_scope: The domain a new CA may sign under; by default the parent of the box's name
                (`remote.dreamference.ai` → `dreamference.ai`). Ignored once the CA exists.

        Returns:
            int: The exit status.
        """
        run, code = cls._setup_inputs(dns_name, ssh_to, ca_scope)
        if run is None:
            return code
        run["yes"] = yes
        for line in cls.plan_lines(
            run["dns_name"], run["overlay_name"], run["ssh_to"], box_only, run["scope"]
        ):
            print(line)
        if not yes and not cls._confirm("Go ahead?"):
            print("Nothing changed.")
            return 1
        if not cls._stage_box_facts(run) or not cls._stage_certificates(run):
            return 1
        if box_only:
            return cls._setup_box_only(run)
        stages = (cls._stage_control_plane, cls._stage_box, cls._stage_tunnel, cls._stage_node_peer)
        return 0 if all(stage(run) for stage in stages) else 1

    @classmethod
    def _setup_inputs(
        cls, dns_name: str, ssh_to: str | None, ca_scope: str | None
    ) -> tuple[dict[str, Any] | None, int]:
        """Checks what `setup` was given against this machine and what is set up already.

        Args:
            dns_name: The box's public DNS name, as typed.
            ssh_to: `--ssh`, or None.
            ca_scope: `--ca-scope`, or None.

        Returns:
            tuple[dict[str, Any] | None, int]: The run's facts so far, or None and the exit status
            (the reason printed).
        """
        dns_name = dns_name.strip().lower().rstrip(".")
        scope = (ca_scope or RemoteSettings.default_ca_scope(dns_name)).strip().lower().strip(".")
        if not dns_name or DOT not in dns_name or any(c in dns_name for c in "/: @"):
            print(
                f"❌ {dns_name!r} is not a DNS name. Give the box's public name, e.g. remote.example.org."
            )
            return None, 2
        if not RemoteSettings.in_scope(dns_name, scope):
            print(f"❌ {dns_name} is not under the CA scope {scope}.")
            return None, 2
        node_id = cls._node_id()
        if not node_id:
            print(
                "❌ This machine is not a Mightling node yet (it has no node id). "
                "Start its model server once: ling-admin server start"
            )
            return None, 1
        existing = RemoteSettings.read()
        if (
            existing
            and existing["dns_name"] != dns_name
            and RemoteCertificateAuthority.ca_certificate().is_file()
            and not RemoteCertificateAuthority.permits(dns_name)
        ):
            print(
                f"❌ Remote access is set up through {existing['dns_name']}, and the node's CA may not sign "
                f"{dns_name}.\n   A box under another name needs a new CA and a new enrolment of every client:\n"
                f"   ling-admin remote remove --purge, then ling-admin remote setup {dns_name}"
            )
            return None, 1
        if ssh_to is None and existing and existing["dns_name"] == dns_name:
            ssh_to = existing.get("ssh")
        ssh_to = (ssh_to or "").strip() or None
        if ssh_to == dns_name:
            ssh_to = None
        problem = cls.ssh_destination_problem(ssh_to) if ssh_to is not None else None
        if problem:
            print(f"❌ {problem}")
            return None, 2
        run: dict[str, Any] = {
            "dns_name": dns_name, "scope": scope, "ssh_to": ssh_to, "node_id": node_id,
            "existing": existing, "overlay_name": RemoteSettings.overlay_name(node_id),
        }  # fmt: skip
        return run, 0

    @classmethod
    def _stage_box_facts(cls, run: dict[str, Any]) -> bool:
        """Asks the box what it is: reachable, root, its architecture, packages and host key.

        Args:
            run: The run's facts, given `facts` here.

        Returns:
            bool: Whether the box can take the setup (the reason printed when not).
        """
        dns_name = run["dns_name"]
        destination = run["ssh_to"] or dns_name
        facts = RemoteBox.probe(dns_name, ssh_to=run["ssh_to"])
        run["facts"] = facts
        if ERROR in facts:
            problem = (
                f"`ssh {destination}` did not work: {facts[ERROR]}\n   Set it up so that `ssh {destination}` "
                "logs in with your key (or name a Host alias with --ssh), then run this again."
            )
        elif facts.get("root") != YES:
            problem = f"On {dns_name} you are neither root nor allowed passwordless sudo; the setup needs one."
        elif not facts.get("arch"):
            problem = (
                f"{dns_name} is neither amd64 nor arm64; NetBird's relay is pinned for those two."
            )
        elif facts.get("packages") not in PACKAGE_TOOLS:
            problem = f"{dns_name} has neither apt nor dnf to install haproxy from."
        elif not facts.get("host_key", "").startswith(ED25519):
            problem = f"{dns_name} has no ed25519 host key for the tunnel to check."
        else:
            return True
        print(f"❌ {problem}")
        return False

    @classmethod
    def _stage_certificates(cls, run: dict[str, Any]) -> bool:
        """The CA (once, under its scope), the two certificates and the relay's secret.

        Args:
            run: The run's facts, given `control`, `relay` and `secret` here.

        Returns:
            bool: Whether all exist afterwards.
        """
        print("🔐 Certificates…")
        if not RemoteCertificateAuthority.ensure(run["scope"]):
            return False
        run["control"] = RemoteCertificateAuthority.issue("control-plane", [run["dns_name"]])
        run["relay"] = RemoteCertificateAuthority.issue("relay", [run["dns_name"]])
        if run["control"] is None or run["relay"] is None:
            return False
        run["secret"] = RemoteSettings.read_private("relay-secret") or secrets.token_hex(
            SECRET_BYTES
        )
        RemoteSettings.write_private("relay-secret", run["secret"] + "\n")
        return True

    @classmethod
    def _stage_control_plane(cls, run: dict[str, Any]) -> bool:
        """The control plane's files and container, and a valid management token.

        Args:
            run: The run's facts, given `token` here.

        Returns:
            bool: Whether the control plane answers with a token.
        """
        print("🧭 Control plane…")
        store_key = (
            RemoteSettings.read_private("store-key")
            or base64.b64encode(secrets.token_bytes(STORE_KEY_BYTES)).decode()
        )
        RemoteSettings.write_private("store-key", store_key + "\n")
        control = run["control"]
        RemoteControlPlane.write_files(
            run["dns_name"],
            run["secret"],
            store_key,
            control[0].read_text(),
            control[1].read_text(),
        )
        token = cls._control_plane_token(run["dns_name"], run["node_id"])
        if not token:
            print(
                "❌ No management token: the control plane was set up before, and its token is lost.\n"
                "   ling-admin remote remove --purge starts over."
            )
            return False
        RemoteSettings.write_private("admin-token", token + "\n")
        run["token"] = token
        return True

    @classmethod
    def _stage_box(cls, run: dict[str, Any]) -> bool:
        """Deploys the box's half and pins its host key for the tunnel.

        Args:
            run: The run's facts.

        Returns:
            bool: Whether the box is deployed.
        """
        dns_name, facts = run["dns_name"], run["facts"]
        print(f"📦 The box ({dns_name}, {facts['arch']})…")
        tunnel_key = RemoteTunnel.public_key()
        if tunnel_key is None:
            print("❌ ssh-keygen could not make the tunnel's key.")
            return False
        relay = run["relay"]
        if not RemoteBox.deploy(
            dns_name, facts["arch"], facts["packages"], run["secret"], relay[0], relay[1], tunnel_key,
            ssh_to=run["ssh_to"],
        ):  # fmt: skip
            return False
        RemoteTunnel.write_known_host(dns_name, facts["host_key"])
        return True

    @classmethod
    def _stage_tunnel(cls, run: dict[str, Any]) -> bool:
        """Starts the tunnel's user unit.

        Args:
            run: The run's facts.

        Returns:
            bool: Whether systemd started it.
        """
        print("🚇 Tunnel…")
        if not RemoteTunnel.install(run["dns_name"]):
            print(f"❌ systemd did not start {TUNNEL_UNIT}: journalctl --user -u {TUNNEL_UNIT}")
            return False
        return True

    @classmethod
    def _stage_node_peer(cls, run: dict[str, Any]) -> bool:
        """Enrols the node as a peer (once), checks its name and writes `remote.json`.

        Args:
            run: The run's facts.

        Returns:
            bool: Whether the node is a peer under its own name.
        """
        dns_name, overlay_name, token = run["dns_name"], run["overlay_name"], run["token"]
        record: dict[str, Any] = {
            "dns_name": dns_name, "overlay_name": overlay_name, "overlay_domain": OVERLAY_DOMAIN,
            "netbird_version": NETBIRD_VERSION, "ca_scope": run["scope"],
            "set_up": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }  # fmt: skip
        if run["ssh_to"]:
            record["ssh"] = run["ssh_to"]
        if not (run["existing"] or {}).get("node_peer") and not cls._join_node(run):
            RemoteSettings.write(record)
            print(
                "⚠️  The box and the tunnel are up, but this node did not join the overlay. "
                f"Run ling-admin remote setup {dns_name} again."
            )
            return False
        label = cls.node_label(dns_name, token, RemoteSettings.node_peer_name(run["node_id"]))
        if label != overlay_name:
            RemoteSettings.write(record)
            print(
                f"❌ Management names this node {label or 'nothing'}, not {overlay_name}; clients find the node "
                "only by the latter. `ling-admin remote peers` shows the peers; remove a stale one with "
                f"`remote revoke`, then run ling-admin remote setup {dns_name} again."
            )
            return False
        record["node_peer"] = True
        RemoteSettings.write(record)
        cls._advertise(overlay_name)
        print(f"✅ Remote access is up. This node is {overlay_name} in the overlay.")
        print("   To enrol a laptop: on the node's LAN, run `ling-admin remote code` here,")
        print("   then `ling node remote join --code <the 8 digits>` on the laptop.")
        return True

    @classmethod
    def _join_node(cls, run: dict[str, Any]) -> bool:
        """Mints the node's one-time key and brings its client up with it.

        Args:
            run: The run's facts.

        Returns:
            bool: Whether `netbird up` succeeded.
        """
        print("🌐 This node joins the overlay…")
        key = RemoteControlPlane.setup_key(run["dns_name"], run["token"], f"node {run['node_id']}")
        return key is not None and RemoteNodePeer.enrol(
            RemoteCertificateAuthority.ca_certificate(),
            RemoteSettings.management_url(run["dns_name"]),
            key,
            run["node_id"],
            run["yes"],
        )

    @classmethod
    def _control_plane_token(cls, dns_name: str, node_id: str) -> str | None:
        """Starts the control plane and returns a valid token.

        Made once, on the start that alone allows `/api/setup` to give one, else renewed.

        Args:
            dns_name: The box's public DNS name.
            node_id: The node's id.

        Returns:
            str | None: The token, or None (the reason printed).
        """
        if not RemoteControlPlane.start(setup_pat=False):
            return None
        needs_setup = RemoteControlPlane.wait_ready(dns_name)
        if needs_setup is None:
            print(
                "❌ The control plane did not answer on loopback. `docker logs dreamference-remote` says why."
            )
            return None
        if not needs_setup:
            token = RemoteSettings.read_private("admin-token")
            return (RemoteControlPlane.renew_token(dns_name, token) or token) if token else None
        # The one start that may hand out an owner's token, on loopback, before the tunnel exists.
        if (
            not RemoteControlPlane.start(setup_pat=True)
            or RemoteControlPlane.wait_ready(dns_name) is None
        ):
            return None
        token = RemoteControlPlane.bootstrap(dns_name, RemoteSettings.node_peer_name(node_id))
        if (
            not RemoteControlPlane.start(setup_pat=False)
            or RemoteControlPlane.wait_ready(dns_name) is None
        ):
            return None
        return token

    @classmethod
    def _setup_box_only(cls, run: dict[str, Any]) -> int:
        """`setup --box-only` after the checks and the certificates.

        The tunnel's key, the box, the tunnel's known-hosts file and `remote.json`. Nothing starts
        on the node.

        Args:
            run: The run's facts (`setup`'s stages so far).

        Returns:
            int: The exit status.
        """
        dns_name, ssh_to, facts = run["dns_name"], run["ssh_to"], run["facts"]
        secret, relay, existing = run["secret"], run["relay"], run["existing"]
        overlay_name = run["overlay_name"]
        print(f"📦 The box ({dns_name}, {facts['arch']})…")
        tunnel_key = RemoteTunnel.public_key()
        if tunnel_key is None:
            print("❌ ssh-keygen could not make the tunnel's key.")
            return 1
        if not RemoteBox.deploy(
            dns_name,
            facts["arch"],
            facts["packages"],
            secret,
            relay[0],
            relay[1],
            tunnel_key,
            ssh_to=ssh_to,
        ):
            return 1
        RemoteTunnel.write_known_host(dns_name, facts["host_key"])
        # A setup that already went further keeps its record: only the box was redeployed.
        record: dict[str, Any] = (
            dict(existing)
            if existing and existing.get("node_peer")
            else {
                "dns_name": dns_name,
                "overlay_name": overlay_name,
                "overlay_domain": OVERLAY_DOMAIN,
                "netbird_version": NETBIRD_VERSION,
                "box_only": True,
            }
        )
        record["set_up"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        record.pop("ssh", None)
        if ssh_to:
            record["ssh"] = ssh_to
        RemoteSettings.write(record)
        again = f"ling-admin remote setup {dns_name}" + (f" --ssh {ssh_to}" if ssh_to else "")
        print(
            f"✅ The box is deployed: haproxy on {BOX_PUBLIC_PORT} passes {dns_name} to the tunnel's listener, "
            f"the relay listens on {RELAY_PORT} and STUN on {STUN_PORT}."
        )
        if record.get("box_only"):
            print(
                "   Nothing runs on this node yet. To finish (the control plane, the tunnel, this node's peer):"
            )
            print(f"   {again}")
        return 0

    @classmethod
    def node_label(cls, dns_name: str, token: str, peer_name: str, seconds: int = 30) -> str | None:
        """Asks management which overlay name it gave the node.

        It may change a name (a suffix for a duplicate), and clients find the node only by
        `mightling-<id>.<domain>`.

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
            labels = [
                str(peer.get("dns_label", "")).lower()
                for peer in peers
                if str(peer.get("name", "")).lower() == peer_name
            ]
            if labels:
                return labels[0]
            if time.monotonic() >= deadline:
                return None
            time.sleep(2)

    # -- status, peers, revoke --------------------------------------------------------------------

    @classmethod
    def status(cls) -> int:
        """Prints the tunnel's state, the box's units, the CA's expiry and the peers.

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
        expiry = RemoteCertificateAuthority.expiry(
            RemoteSettings.path("certs", "control-plane.crt")
        )
        if expiry is not None:
            days = int((expiry.timestamp() - time.time()) // 86400)
            print(
                f"Certificates: valid {days} more days"
                + ("  ⚠️ run `ling-admin remote setup` to reissue" if days < RENEW_DAYS else "")
            )
        if record.get("box_only"):
            print("This node:  not set up yet (--box-only): no control plane, no tunnel, no peer.")
            print(
                f"            ling-admin remote setup {dns_name}"
                + (f" --ssh {ssh_to}" if ssh_to else "")
                + " finishes it."
            )
            return 1
        print(f"This node:  {record.get('overlay_name')} (overlay {record.get('overlay_domain')})")
        tunnel = RemoteTunnel.state()
        healthy &= tunnel == ACTIVE
        print(f"Tunnel:     {tunnel} ({TUNNEL_UNIT})")
        print(
            f"Control:    {'running' if RemoteControlPlane.running() else 'NOT running'} (dreamference-remote)"
        )
        healthy &= RemoteControlPlane.running()
        token = RemoteSettings.read_private("admin-token")
        peers = RemoteControlPlane.peers(dns_name, token) if token else None
        if peers is None:
            healthy = False
            print("Peers:      management did not answer")
        else:
            connected = sum(1 for peer in peers if peer.get("connected"))
            print(
                f"Peers:      {len(peers)} enrolled, {connected} connected (ling-admin remote peers)"
            )
        return 0 if healthy else 1

    @classmethod
    def peer_lines(cls, peers: list[dict[str, Any]]) -> list[str]:
        """The peers as `remote peers` prints them.

        Args:
            peers: Management's peers.

        Returns:
            List[str]: One line per peer: name, overlay name, connected or last seen, system.
        """
        lines = []
        for peer in sorted(peers, key=lambda entry: str(entry.get("name", ""))):
            seen = (
                "connected" if peer.get("connected") else f"last seen {peer.get('last_seen', '?')}"
            )
            lines.append(
                f"  {peer.get('name', '?'):<28} {peer.get('dns_label', ''):<52} {seen:<34} {peer.get('os', '')}"
            )
        return lines

    @classmethod
    def peers(cls) -> int:
        """Lists the enrolled peers.

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
        """Deletes a peer.

        Its key is forgotten and its overlay address released at once; it can be enrolled again only
        on the LAN.

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
        matches = [
            peer
            for peer in peers
            if str(peer.get("name", "")).lower().startswith(wanted)
            or str(peer.get("dns_label", "")).lower().startswith(wanted)
        ]
        exact = [
            peer
            for peer in matches
            if wanted in (str(peer.get("name", "")).lower(), str(peer.get("dns_label", "")).lower())
        ]
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
        print(
            f"✅ {peer.get('name')} is revoked: its key is forgotten; it can rejoin only on the LAN with a new code."
        )
        return 0

    # -- enrolment --------------------------------------------------------------------------------

    @classmethod
    def bundle(
        cls,
        record: dict[str, Any],
        token: str,
        node_id: str,
        client: str,
        ephemeral: bool = False,
    ) -> dict[str, Any] | None:
        """Makes one client's bundle, minting its setup key.

        Args:
            record: `remote.json`.
            token: The management token.
            node_id: The node's id.
            client: The client's host name, for the key's name.
            ephemeral: Whether the key enrols an ephemeral peer.

        Returns:
            Optional[Dict[str, Any]]: The bundle, or None.
        """
        key = RemoteControlPlane.setup_key(
            record["dns_name"], token, f"client {client}", ephemeral=ephemeral
        )
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
    def bundle_file(cls, path: str) -> int:
        """Writes one ephemeral peer's bundle to a new 0600 file, with no code and no listener.

        For a test runner off the LAN (the daily off-LAN acceptance): the bundle is the one
        `code` would hand a client, but its key enrols an **ephemeral** peer (management deletes
        it ten minutes after it goes offline), still one use and ten minutes. Whoever can run this
        already holds the node's management token, so the file gives nothing new away; it holds a
        live key until it is spent or expires, so the caller deletes it.

        Args:
            path: The file to create; an existing file is refused, never overwritten.

        Returns:
            int: The exit status.
        """
        record = RemoteSettings.read()
        token = RemoteSettings.read_private("admin-token")
        node_id = cls._node_id()
        if record is None or not token or not node_id:
            print("Remote access is not set up (ling-admin remote setup <box's DNS name>).")
            return 3
        target = Path(path).expanduser()
        if target.exists() or not target.parent.is_dir():
            print(f"❌ {target} exists or its folder does not; nothing was minted.")
            return 1
        bundle = cls.bundle(record, token, node_id, "bundle file", ephemeral=True)
        if bundle is None:
            return 1
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as error:
            print(f"❌ Could not create {target}: {error}")
            return 1
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(bundle, handle, sort_keys=True)
        print(
            f"✅ A bundle for one ephemeral peer (one use, valid {CODE_MINUTES} minutes) is in {target} (0600)."
        )
        print("   It holds a live setup key: delete the file once it is spent.")
        return 0

    @classmethod
    def code(cls) -> int:
        """Prints a code and waits, on the LAN, for one client to enrol with it.

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
        print(
            f"🔢 Code: {code[:4]} {code[4:]}   (valid {CODE_MINUTES} minutes, for one client, on this LAN)"
        )
        print(f"   On the client, on this LAN:  ling node remote join --code {code}")
        print(f"   Waiting on port {ENROL_PORT}… (Ctrl-C withdraws it)")
        try:
            outcome = RemoteEnrolment.serve(
                code, lambda client: cls.bundle(record, token, node_id, client)
            )
        except OSError as error:
            print(f"❌ Could not listen on port {ENROL_PORT}: {error}")
            return 1
        except KeyboardInterrupt:
            print("\nCode withdrawn.")
            return 1
        if outcome.startswith("enrolled:"):
            print(
                f"✅ {outcome.split(':', 1)[1]} has its bundle and is joining the overlay (ling-admin remote peers)."
            )
            return 0
        print(
            "Code withdrawn after ten wrong attempts."
            if outcome == WITHDRAWN
            else "Code expired; nobody used it."
        )
        return 1

    # -- remove -----------------------------------------------------------------------------------

    @classmethod
    def remove(cls, purge: bool = False, yes: bool = False) -> int:
        """Removes the box's units and files and the tunnel.

        With `purge`, also the control plane, the CA, the peers and the node's own peer.

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
            ", the control plane, the node's CA and every enrolment" if purge else ""
        )
        if not yes and not cls._confirm(f"Remove {what}?"):
            print("Nothing changed.")
            return 1
        status = 0
        if dns_name and not RemoteBox.remove(dns_name, ssh_to=(record or {}).get("ssh")):
            status = 1
        RemoteTunnel.remove()
        if purge:
            RemoteControlPlane.stop()
            RemoteControlPlane.remove_proxy()
            if (record or {}).get("node_peer"):
                RemoteNodePeer.leave(yes)
            import shutil

            shutil.rmtree(RemoteSettings.folder(), ignore_errors=True)
            cls._advertise(None)
            print("✅ Remote access removed, CA and enrolments included.")
        else:
            print(
                "✅ The box and the tunnel are removed. The control plane, the CA and the enrolments are kept: "
                "`ling-admin remote setup <name>` on a new box under the same name brings everyone back."
            )
        return status

    @classmethod
    def summary(cls) -> dict[str, Any]:
        """Remote access in brief, for `ling-admin status` and the audit.

        Returns:
            Dict[str, Any]: For `ling-admin status` and the audit: whether it is set up, the box,
            the overlay name and the tunnel's state.
        """
        record = RemoteSettings.read()
        if record is None:
            return {"set_up": False}
        return {
            "set_up": True,
            "dns_name": record["dns_name"],
            "overlay_name": record.get("overlay_name"),
            "box_only": bool(record.get("box_only")),
            "tunnel": RemoteTunnel.state(),
            "system": platform.system(),
        }
