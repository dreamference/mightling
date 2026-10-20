"""`ling-admin remote setup|status|remove`: the node's half (REMOTE_ACCESS §3).

`setup <dns-name>` says what it will do on the node and on the box, asks once, then: makes the CA
and the certificates, writes the control plane's configuration with a fresh relay secret and a
fixed store key, starts the control plane on loopback, creates the owner account and its token
over loopback while nothing outside can reach the server yet, makes the tunnel's key, deploys the
box (REMOTE_ACCESS §3 step 3, `remote_box`), starts the tunnel, and enrols the node as the overlay's
first peer. Run again, it reconciles: certificates near expiry are reissued, a token near expiry
rotated, nothing else touched. `remove` takes the tunnel and the box's half away and keeps the CA
and the management state (so a replacement box needs no re-enrolment) unless `--purge`.

Nothing here prints: every line goes through `say`, and the CLI (`cli/remote_command.py`) prints
it. Every command runs through `RemoteExecutor`, every call to management through
`RemoteManagementApi`, so the tests replace two seams and see everything.
"""

import base64
import ipaddress
import re
import secrets
import socket
import time
from collections.abc import Callable
from pathlib import Path

from dreamference.node.node_identity import NodeIdentity
from dreamference.remote.remote_ca import RemoteCA
from dreamference.remote.remote_control_plane import RemoteControlPlane
from dreamference.remote.remote_executor import RemoteExecutor
from dreamference.remote.remote_management_api import RemoteManagementApi
from dreamference.remote.remote_node_peer import UP_TIMEOUT_S, RemoteNodePeer
from dreamference.remote.remote_state import RemoteState
from dreamference.remote.remote_step import RemoteStep
from dreamference.remote.remote_tunnel import BOX_TUNNEL_PORT, RemoteTunnel

Say = Callable[[str], None]
Confirm = Callable[[str], bool]

DNS_NAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$"
)
RELAY_LABEL = "relay"
START_WAIT_S = 120
DAY_S = 86400
TOKEN_RENEW_DAYS = 30
TOKEN_DAYS = 365
STORE_KEY_BYTES = 32
SECRET_BYTES = 32
PASSWORD_BYTES = 24
OWNER_NAME = "Mightling node"
EXIT_USAGE = 2
# The box's half (REMOTE_ACCESS §13, Phase 2). Until it is built, `setup` prints its plan and
# changes nothing.
BOX_HALF_BUILT = False
NOT_BUILT = "The box's half of `remote setup` is not built yet (REMOTE_ACCESS §13, Phase 2)"


class RemoteAccess:
    """The node's remote-access commands."""

    # -- names -------------------------------------------------------------------------------

    @classmethod
    def name_problem(cls, dns_name: str, relay_name: str) -> str:
        """Why the names cannot be used, if they cannot.

        Args:
            dns_name: The box's name.
            relay_name: The relay's name.

        Returns:
            str: The reason, or "" when both are DNS names.
        """
        for name in (dns_name, relay_name):
            try:
                ipaddress.ip_address(name)
            except ValueError:
                pass
            else:
                return f"{name} is an address. Remote access never stores or uses one: give the box's DNS name."
            if not DNS_NAME.match(name):
                return f"{name} is not a DNS name (lowercase letters, digits and hyphens, at least two labels)."
        if dns_name == relay_name:
            return "The relay needs a name of its own: the control plane holds the box's name on port 443."
        return ""

    @classmethod
    def hostname(cls) -> str:
        """This machine's short host name, from which management derives the node's overlay label.

        Returns:
            str: The first label of the host name, lowercased.
        """
        return socket.gethostname().split(".", 1)[0].lower()

    @classmethod
    def overlay_name(cls, state: dict[str, object]) -> str:
        """The node's name on the overlay, once it is enrolled.

        Args:
            state: The remote state.

        Returns:
            str: `<label>.<domain>`, or "" before the node is a peer.
        """
        peer = RemoteState.section(state, "node_peer")
        label = peer.get("dns_label")
        domain = RemoteState.text(state, "overlay_domain")
        return f"{label}.{domain}" if isinstance(label, str) and label and domain else ""

    # -- the plan ----------------------------------------------------------------------------

    @classmethod
    def plan_lines(cls, dns_name: str, relay_name: str, root_steps: list[RemoteStep]) -> list[str]:
        """What `setup` says before it asks.

        Args:
            dns_name: The box's name.
            relay_name: The relay's name.
            root_steps: The node's steps that run under sudo.

        Returns:
            list[str]: The lines.
        """
        lines = [
            f"Remote access through {dns_name} (control plane) and {relay_name} (relay).",
            "",
            "On this node:",
            f"  - the node's CA, if absent, under {RemoteState.ca_dir()} (name-constrained, key 0600),",
            "    and certificates for the control plane and the relay (397 days);",
            "  - NetBird's control plane (management and signal) as the container dreamference-remote,",
            "    pinned by digest, on an internal network with no route out, reached on 127.0.0.1:33073;",
            "  - the owner account and its token, made on loopback before anything outside can reach it;",
            f"  - the user unit mightling-remote-tunnel.service, an SSH tunnel to {dns_name} that forwards",
            f"    the box's 127.0.0.1:{BOX_TUNNEL_PORT} to the control plane;",
            "  - NetBird's client (pinned), the CA in the system trust store, and the node as the first peer.",
            "",
            f"On the box, over `ssh {dns_name}` (root or a sudoer):",
            "  - NetBird's relay (pinned) with STUN, gost (pinned) routing port 443 by server name,",
            "    a firewall table of its own, and the account mightling-tunnel, which can forward one port.",
            "  - Nothing else: no Docker, no dashboard, no identity provider, no key of the overlay.",
        ]
        if root_steps:
            lines += ["", "These steps on this node run under sudo:"]
            lines += [f"  {step.display()}" for step in root_steps if step.root]
        return lines

    @classmethod
    def root_steps(cls, work: Path) -> list[RemoteStep] | None:
        """The node's steps for its own client and trust store.

        Args:
            work: A folder for the download.

        Returns:
            list[RemoteStep] | None: The steps, or None when no client is pinned for this machine.
        """
        system, machine = (
            RemoteExecutor.run(["uname", flag]).stdout.strip() for flag in ("-s", "-m")
        )
        installs = RemoteNodePeer.install_steps(work, system or "Linux", machine)
        if installs is None:
            return None
        return installs + RemoteNodePeer.trust_steps(RemoteCA.cert_path())

    @classmethod
    def run_steps(cls, steps: list[RemoteStep], say: Say, timeout: int = UP_TIMEOUT_S) -> bool:
        """Runs steps in order, stopping at the first that fails.

        Args:
            steps: The steps.
            say: Where lines go.
            timeout: Seconds per step.

        Returns:
            bool: Whether every step succeeded.
        """
        for step in steps:
            if step.what:
                say(f"→ {step.what}")
            result = RemoteExecutor.run_step(step, timeout)
            if result.returncode != 0:
                say(f"❌ {step.display()} failed: {(result.stderr or result.stdout).strip()[:400]}")
                return False
        return True

    # -- setup -------------------------------------------------------------------------------

    @classmethod
    def setup(
        cls, dns_name: str, relay_name: str, yes: bool, dry_run: bool, say: Say, confirm: Confirm
    ) -> int:
        """`ling-admin remote setup <dns-name>`.

        Args:
            dns_name: The box's name.
            relay_name: The relay's name; "" for `relay.<dns-name>`.
            yes: Do not ask.
            dry_run: Print the plan and change nothing.
            say: Where lines go.
            confirm: Asks a yes/no question.

        Returns:
            int: The exit code.
        """
        dns_name = dns_name.lower().strip(".")
        relay_name = (relay_name or f"{RELAY_LABEL}.{dns_name}").lower().strip(".")
        problem, code = cls.name_problem(dns_name, relay_name), EXIT_USAGE
        if not problem:
            problem, code = cls.state_problem(dns_name, relay_name) or cls.machine_problem(), 1
        if problem:
            say(f"❌ {problem}")
            return code
        root_steps = (
            cls.root_steps(RemoteState.ensure_dir(RemoteState.base_dir() / "download")) or []
        )
        for line in cls.plan_lines(dns_name, relay_name, root_steps):
            say(line)
        if dry_run:
            return 0
        if not BOX_HALF_BUILT:
            say("")
            say(f"❌ {NOT_BUILT}; nothing was changed.")
            return 1
        if not yes and not confirm("Go ahead?"):
            say("Nothing was changed.")
            return 1
        return 0 if cls.execute(dns_name, relay_name, root_steps, say) else 1

    @classmethod
    def machine_problem(cls) -> str:
        """Why this machine cannot run remote access, if it cannot.

        Returns:
            str: The reason, or "" on a node with a pinned NetBird client for its architecture.
        """
        if not NodeIdentity.read():
            return (
                "Remote access is run by a node: `ling-admin node enable` (or `server start`) on this "
                "machine first."
            )
        if cls.root_steps(RemoteState.ensure_dir(RemoteState.base_dir() / "download")) is None:
            return "No NetBird client is pinned for this machine's system and architecture."
        return ""

    @classmethod
    def state_problem(cls, dns_name: str, relay_name: str) -> str:
        """Why the existing state forbids these names, if it does.

        Args:
            dns_name: The box's name.
            relay_name: The relay's name.

        Returns:
            str: The reason, or "".
        """
        state = RemoteState.load()
        current = RemoteState.text(state, "dns_name")
        if current and current != dns_name:
            return (
                f"Remote access is set up through {current}. To change the box: "
                "`ling-admin remote remove`, then setup."
            )
        scope = RemoteState.text(state, "ca_scope")
        if RemoteCA.exists() and scope:
            outside = [
                name for name in (dns_name, relay_name) if not RemoteCA.in_scope(name, scope)
            ]
            if outside:
                return (
                    f"The node's CA may sign names under {scope} only, and {', '.join(outside)} is not. "
                    "A box outside it needs a new CA: `ling-admin remote remove --purge`, then setup, and every "
                    "client enrolled again."
                )
        return ""

    @classmethod
    def execute(
        cls, dns_name: str, relay_name: str, root_steps: list[RemoteStep], say: Say
    ) -> bool:
        """Carries out the setup, stage by stage, saving what each stage made.

        Args:
            dns_name: The box's name.
            relay_name: The relay's name.
            root_steps: The node's steps under sudo for its client and trust store.
            say: Where lines go.

        Returns:
            bool: Whether every stage succeeded.
        """
        state = RemoteState.load()
        state.update({"dns_name": dns_name, "relay_name": relay_name})
        state.setdefault("ca_scope", RemoteCA.default_scope(dns_name))
        stages: list[Callable[[dict[str, object], Say], bool]] = [
            cls.stage_certificates,
            cls.stage_control_plane,
            cls.stage_owner,
            cls.stage_box,
            cls.stage_tunnel,
            lambda current, report: cls.stage_node_peer(current, root_steps, report),
        ]
        for stage in stages:
            ok = stage(state, say)
            RemoteState.save(state)
            if not ok:
                return False
        say(f"✅ Remote access is set up. The node is {cls.overlay_name(state)} on the overlay.")
        say(
            "   Enrol a client while it is on this LAN: `ling-admin node remote code` here, then on the client"
        )
        say("   `ling node remote join --code <the 8 digits>`.")
        return True

    @classmethod
    def stage_certificates(cls, state: dict[str, object], say: Say) -> bool:
        """The CA, once, and the two server certificates, when absent, near expiry or for other names.

        Args:
            state: The remote state, updated in place.
            say: Where lines go.

        Returns:
            bool: Whether the CA and both certificates exist afterwards.
        """
        dns_name, relay_name = (
            RemoteState.text(state, "dns_name"),
            RemoteState.text(state, "relay_name"),
        )
        if not RemoteCA.create(RemoteState.text(state, "ca_scope"), cls.hostname()):
            say("❌ Could not make the node's CA (openssl).")
            return False
        issued = RemoteState.section(state, "certificates")
        wanted = {
            "management": ([dns_name, "localhost"], RemoteControlPlane.tls_dir()),
            "relay": ([relay_name], RemoteState.relay_dir()),
        }
        for name, (sans, folder) in wanted.items():
            if issued.get(name) == sans and not RemoteCA.needs_renewal(folder / f"{name}.crt"):
                continue
            if not RemoteCA.issue(name, sans, folder):
                say(f"❌ Could not issue the {name} certificate (openssl).")
                return False
            issued[name] = sans
            say(f"→ Issued the {name} certificate for {', '.join(sans)}")
        state["certificates"] = issued
        return True

    @classmethod
    def stage_control_plane(cls, state: dict[str, object], say: Say) -> bool:
        """Writes the configuration and (re)starts the control plane.

        Args:
            state: The remote state, updated in place (the relay secret and the store key).
            say: Where lines go.

        Returns:
            bool: Whether management answers on loopback afterwards.
        """
        if not RemoteState.text(state, "relay_secret"):
            state["relay_secret"] = secrets.token_urlsafe(SECRET_BYTES)
        if not RemoteState.text(state, "store_key"):
            state["store_key"] = base64.b64encode(secrets.token_bytes(STORE_KEY_BYTES)).decode()
        RemoteControlPlane.write_config(
            RemoteControlPlane.render_config(
                RemoteState.text(state, "dns_name"),
                RemoteState.text(state, "relay_name"),
                RemoteState.text(state, "relay_secret"),
                RemoteState.text(state, "store_key"),
            )
        )
        owner = RemoteState.section(state, "owner")
        started, error = RemoteControlPlane.start(setup_pat=not owner.get("token"))
        if not started:
            say(f"❌ The control plane did not start: {error or 'the loopback proxy failed'}")
            return False
        if not RemoteManagementApi.wait_until_answering(START_WAIT_S):
            say(
                "❌ The control plane is not answering on 127.0.0.1:33073; `docker logs dreamference-remote` says why."
            )
            return False
        say("→ The control plane runs on loopback")
        return True

    @classmethod
    def stage_owner(cls, state: dict[str, object], say: Say, now: float | None = None) -> bool:
        """The owner account and its token: made once on loopback, rotated when near expiry.

        Args:
            state: The remote state, updated in place.
            say: Where lines go.
            now: The time; defaults to the clock.

        Returns:
            bool: Whether a valid token is held afterwards.
        """
        current = time.time() if now is None else now
        owner = RemoteState.section(state, "owner")
        token, user_id = owner.get("token"), owner.get("user_id")
        if isinstance(token, str) and token and isinstance(user_id, str):
            expires = owner.get("token_expires")
            if isinstance(expires, (int, float)) and expires - current > TOKEN_RENEW_DAYS * DAY_S:
                return True
            rotated = RemoteManagementApi.rotate_token(
                user_id, token, f"ling-admin {time.strftime('%Y-%m-%d')}"
            )
            if rotated is None:
                say(
                    "⚠️  Could not rotate the administrator token; `remote status` shows when it expires."
                )
                return True
            owner.update(
                {
                    "token": rotated[0],
                    "token_id": rotated[1],
                    "token_expires": current + TOKEN_DAYS * DAY_S,
                }
            )
            say("→ Rotated the administrator token")
            return True
        password = secrets.token_urlsafe(PASSWORD_BYTES)
        email = f"owner@{RemoteState.text(state, 'dns_name')}"
        created = RemoteManagementApi.create_owner(email, OWNER_NAME, password)
        if created is None:
            say("❌ Management refused to create the owner account: it has one already, made by something else. "
                "`ling-admin remote remove --purge` starts the control plane afresh.")  # fmt: skip
            return False
        owner.update({"user_id": created["user_id"], "email": email, "password": password,
                      "token": created["personal_access_token"],
                      "token_expires": current + TOKEN_DAYS * DAY_S})  # fmt: skip
        state["owner"] = owner
        say("→ Created the owner account and its token (kept in remote.json, 0600)")
        # The setup endpoint's token is never offered again: the container restarts without it.
        started, error = RemoteControlPlane.start(setup_pat=False)
        if not started or not RemoteManagementApi.wait_until_answering(START_WAIT_S):
            say(f"❌ The control plane did not restart: {error}")
            return False
        return True

    @classmethod
    def stage_box(cls, state: dict[str, object], say: Say) -> bool:
        """The box's half (Phase 2): the tunnel key here, then the box over SSH.

        Args:
            state: The remote state.
            say: Where lines go.

        Returns:
            bool: Whether the box is ready for the tunnel.
        """
        if not RemoteTunnel.ensure_key():
            say("❌ Could not make the tunnel's SSH key (ssh-keygen).")
            return False
        if not BOX_HALF_BUILT:
            say(f"❌ {NOT_BUILT}.")
            return False
        return True

    @classmethod
    def stage_tunnel(cls, state: dict[str, object], say: Say) -> bool:
        """Installs and starts the tunnel unit.

        Args:
            state: The remote state.
            say: Where lines go.

        Returns:
            bool: Whether systemd took the unit.
        """
        if not RemoteTunnel.install(RemoteState.text(state, "dns_name")):
            say("❌ Could not start mightling-remote-tunnel.service (systemd user units).")
            return False
        say(f"→ The tunnel to {RemoteState.text(state, 'dns_name')} runs")
        return True

    @classmethod
    def stage_node_peer(
        cls, state: dict[str, object], root_steps: list[RemoteStep], say: Say
    ) -> bool:
        """Installs NetBird's client here, trusts the CA, and enrols the node, once.

        Args:
            state: The remote state, updated in place (`node_peer`, `overlay_domain`).
            root_steps: The install and trust steps.
            say: Where lines go.

        Returns:
            bool: Whether the node is a peer afterwards.
        """
        dns_name = RemoteState.text(state, "dns_name")
        peer = RemoteState.section(state, "node_peer")
        if cls.overlay_name(state):
            if peer.get("management") == dns_name:
                return True
            # Enrolled through an earlier box: the same peer, its key kept, a new management URL.
            if not cls.run_steps([RemoteNodePeer.reconnect_step(dns_name)], say):
                return False
            peer["management"] = dns_name
            return True
        return cls.run_steps(root_steps, say) and cls.join_node_peer(state, say)

    @classmethod
    def join_node_peer(cls, state: dict[str, object], say: Say) -> bool:
        """Mints the node's own one-off key and brings its client up with it.

        Args:
            state: The remote state, updated in place.
            say: Where lines go.

        Returns:
            bool: Whether the node is a peer afterwards.
        """
        dns_name = RemoteState.text(state, "dns_name")
        token = RemoteState.text(RemoteState.section(state, "owner"), "token")
        key = RemoteManagementApi.create_setup_key(token, f"node {cls.hostname()}")
        if key is None:
            say("❌ Management did not mint a setup key for the node.")
            return False
        key_file = RemoteState.base_dir() / "node-setup-key"
        RemoteState.write_private(key_file, f"{key['key']}\n")
        try:
            joined = cls.run_steps(
                [RemoteNodePeer.up_step(dns_name, key_file, cls.hostname())], say
            )
        finally:
            key_file.unlink(missing_ok=True)
        if not joined:
            return False
        return cls.record_node_peer(state, token, say)

    @classmethod
    def record_node_peer(cls, state: dict[str, object], token: str, say: Say) -> bool:
        """Remembers the node's peer as management names it (a taken label gets a suffix).

        Args:
            state: The remote state, updated in place.
            token: The administrator's token.
            say: Where lines go.

        Returns:
            bool: Whether management lists the node.
        """
        peers = RemoteManagementApi.peers(token) or []
        mine = [
            peer
            for peer in peers
            if str(peer.get("hostname", "")).split(".", 1)[0].lower() == cls.hostname()
        ]
        if not mine:
            say("❌ The node joined, but management does not list it.")
            return False
        peer = mine[-1]
        state["node_peer"] = {
            "id": peer.get("id"),
            "dns_label": peer.get("dns_label"),
            "name": peer.get("name"),
            "management": RemoteState.text(state, "dns_name"),
        }
        state["overlay_domain"] = RemoteManagementApi.dns_domain(token)
        return True

    # -- status ------------------------------------------------------------------------------

    @classmethod
    def status(cls, say: Say, now: float | None = None) -> int:
        """`ling-admin remote status`.

        Args:
            say: Where lines go.
            now: The time; defaults to the clock.

        Returns:
            int: The exit code.
        """
        state = RemoteState.load()
        if not RemoteState.text(state, "dns_name") and not RemoteCA.exists():
            say("Remote access: not set up (`ling-admin remote setup <dns-name>`).")
            return 0
        current = time.time() if now is None else now
        say(f"Box:            {RemoteState.text(state, 'dns_name') or 'none (removed)'}")
        say(f"Relay:          {RemoteState.text(state, 'relay_name') or 'none'}")
        say(f"Node:           {cls.overlay_name(state) or 'not enrolled'}")
        say(
            f"CA:             {cls.expiry_text(RemoteCA.cert_path())}, SHA-256 {RemoteCA.fingerprint()[:23]}…"
        )
        for name, folder in (
            ("management", RemoteControlPlane.tls_dir()),
            ("relay", RemoteState.relay_dir()),
        ):
            say(f"Certificate:    {name}, {cls.expiry_text(folder / f'{name}.crt')}")
        say(
            f"Control plane:  {'running' if RemoteControlPlane.running() else 'stopped'} (dreamference-remote)"
        )
        say(f"Tunnel:         {RemoteTunnel.state()}")
        owner = RemoteState.section(state, "owner")
        expires = owner.get("token_expires")
        if isinstance(expires, (int, float)):
            days = int((expires - current) // DAY_S)
            warning = "  ⚠️  `remote setup` rotates it" if days < TOKEN_RENEW_DAYS else ""
            say(f"Admin token:    expires in {days} days{warning}")
        token = RemoteState.text(owner, "token")
        peers = RemoteManagementApi.peers(token) if token and RemoteControlPlane.running() else None
        if peers is not None:
            connected = sum(1 for peer in peers if peer.get("connected") is True)
            say(f"Peers:          {len(peers)} enrolled, {connected} connected now")
        say("Box units:      not checked (the box's half is REMOTE_ACCESS §13, Phase 2)")
        return 0

    @classmethod
    def expiry_text(cls, cert: Path) -> str:
        """A certificate's remaining life, for `status`.

        Args:
            cert: The certificate.

        Returns:
            str: `valid N more days` (with a warning when renewal is due), or `missing`.
        """
        left = RemoteCA.days_left(cert)
        if left is None:
            return "missing"
        return f"valid {left} more days" + (
            "  ⚠️  `remote setup` reissues it" if RemoteCA.needs_renewal(cert) else ""
        )

    # -- remove ------------------------------------------------------------------------------

    @classmethod
    def remove(cls, purge: bool, yes: bool, say: Say, confirm: Confirm) -> int:
        """`ling-admin remote remove [--purge]`.

        Args:
            purge: Also delete the CA, the management state (every peer) and the CA's trust here.
            yes: Do not ask.
            say: Where lines go.
            confirm: Asks a yes/no question.

        Returns:
            int: The exit code.
        """
        state = RemoteState.load()
        if not state and not RemoteCA.exists():
            say("Remote access is not set up here; nothing to remove.")
            return 0
        lines, steps = cls.removal_plan(state, purge)
        for line in lines:
            say(line)
        if not yes and not confirm("Go ahead?"):
            say("Nothing was changed.")
            return 1
        RemoteTunnel.remove()
        cls.run_steps(steps, say)
        if purge:
            RemoteControlPlane.purge()
            cls.delete_folder(RemoteState.base_dir())
            say("✅ Remote access is removed, with its CA and every peer.")
            return 0
        RemoteControlPlane.stop()
        for key in ("dns_name", "relay_name", "relay_secret"):
            state.pop(key, None)
        RemoteState.save(state)
        say("✅ Remote access is removed; the CA and the management state are kept.")
        return 0

    @classmethod
    def removal_plan(
        cls, state: dict[str, object], purge: bool
    ) -> tuple[list[str], list[RemoteStep]]:
        """What `remove` says before it asks, and its steps under sudo.

        Args:
            state: The remote state.
            purge: Whether the CA and every peer go too.

        Returns:
            tuple[list[str], list[RemoteStep]]: The lines, and the root steps (only with `purge`:
            the node leaves the overlay and the CA leaves the system trust store).
        """
        lines = ["This stops the tunnel and the control plane on this node."]
        steps: list[RemoteStep] = []
        if purge:
            steps = [RemoteNodePeer.down_step()] if cls.overlay_name(state) else []
            steps += RemoteNodePeer.untrust_steps()
            lines.append(
                "With --purge it also deletes the CA and every peer: each client must enrol again."
            )
        else:
            lines += [
                "The CA, the management state and every peer are kept: a new box under the same name",
                "needs nothing on the clients; under another name, each client enrols again.",
            ]
        lines += [f"  {step.display()}" for step in steps]
        return lines, steps

    @classmethod
    def delete_folder(cls, folder: Path) -> None:
        """Deletes the remote-access folder and everything in it.

        Args:
            folder: The folder.
        """
        if not folder.is_dir():
            return
        for path in sorted(folder.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink()
        folder.rmdir()
