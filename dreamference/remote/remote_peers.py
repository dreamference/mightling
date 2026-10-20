"""`ling-admin node remote code|peers|revoke`: who is on the overlay (REMOTE_ACCESS §4.1, §4.4).

`code` opens one enrolment window (`RemoteEnrolment`, served by `RemoteEnrolmentServer`) with a
one-off setup key minted for it, and withdraws the key in management when the window closes
unused. `peers` lists the overlay's peers with their last connection; `revoke` deletes one, so its
key is forgotten and its overlay address released at once, and it can come back only through the
LAN. Lines go through `say`; the CLI prints them.
"""

import time
from collections.abc import Callable

from dreamference.node.node_identity import NodeIdentity
from dreamference.remote.netbird_pins import NETBIRD_VERSION
from dreamference.remote.remote_access import RemoteAccess
from dreamference.remote.remote_ca import RemoteCA
from dreamference.remote.remote_control_plane import RemoteControlPlane
from dreamference.remote.remote_enrolment import CODE_LIFETIME_S, ENROL_PORT, RemoteEnrolment
from dreamference.remote.remote_enrolment_server import RemoteEnrolmentServer
from dreamference.remote.remote_management_api import Json, RemoteManagementApi
from dreamference.remote.remote_state import RemoteState

Say = Callable[[str], None]
ID_PREFIX_MIN = 4
EXIT_USAGE = 2
SECONDS_PER_MINUTE = 60


class RemotePeers:
    """Enrolment codes, the peer list and revocation."""

    @classmethod
    def ready(cls, say: Say) -> tuple[dict[str, object], str] | None:
        """The state and the administrator's token, when remote access can answer.

        Args:
            say: Where the reason goes when it cannot.

        Returns:
            tuple[dict[str, object], str] | None: The state and the token, or None.
        """
        state = RemoteState.load()
        token = RemoteState.text(RemoteState.section(state, "owner"), "token")
        if (
            not RemoteAccess.overlay_name(state)
            or not token
            or not RemoteState.text(state, "dns_name")
        ):
            say(
                "❌ Remote access is not set up here: `ling-admin remote setup <dns-name>` on the node first."
            )
            return None
        if not RemoteControlPlane.running():
            say(
                "❌ The control plane is stopped: `ling-admin remote setup <dns-name>` starts it again."
            )
            return None
        return state, token

    @classmethod
    def bundle(cls, state: dict[str, object], setup_key: str) -> dict[str, object]:
        """What an enrolling client receives (REMOTE_ACCESS §4.1, step 2).

        Args:
            state: The remote state.
            setup_key: The one-off key minted for this client.

        Returns:
            dict[str, object]: The bundle: names, the CA and the key; never an address.
        """
        dns_name, relay_name = (
            RemoteState.text(state, "dns_name"),
            RemoteState.text(state, "relay_name"),
        )
        return {
            "node": NodeIdentity.read() or "",
            "node_name": RemoteAccess.hostname(),
            "remote_name": RemoteAccess.overlay_name(state),
            "remote_domain": RemoteState.text(state, "overlay_domain"),
            "management_url": f"https://{dns_name}:443",
            "relay": f"rels://{relay_name}:443",
            "ca_pem": RemoteCA.pem(),
            "ca_sha256": RemoteCA.fingerprint(),
            "setup_key": setup_key,
            "netbird_version": NETBIRD_VERSION,
        }

    @classmethod
    def code(cls, say: Say, port: int = ENROL_PORT) -> int:
        """`ling-admin node remote code`: one code, served on the LAN until used or ten minutes pass.

        Args:
            say: Where lines go.
            port: The enrolment port (the tests pass a free one).

        Returns:
            int: 0 when a client received the bundle, 1 otherwise.
        """
        ready = cls.ready(say)
        if ready is None:
            return 1
        state, token = ready
        key = RemoteManagementApi.create_setup_key(
            token, f"ling node remote join, {time.strftime('%Y-%m-%d %H:%M')}"
        )
        if key is None:
            say("❌ Management did not mint a setup key.")
            return 1
        enrolment = RemoteEnrolment(cls.bundle(state, str(key["key"])))
        try:
            server = RemoteEnrolmentServer(("", port), enrolment, say)
        except OSError as error:
            RemoteManagementApi.delete_setup_key(token, str(key["id"]))
            say(f"❌ Could not listen on port {port}: {error}")
            return 1
        say(f"Enrolment code: {enrolment.code[:4]} {enrolment.code[4:]}")
        say(
            f"Valid {CODE_LIFETIME_S // SECONDS_PER_MINUTE} minutes, once. On the client, on this LAN:"
        )
        say(f"    ling node remote join --code {enrolment.code}")
        say(f"The client must see the CA fingerprint {RemoteCA.fingerprint()[:23]}…")
        try:
            used = server.serve_window()
        except KeyboardInterrupt:
            used = False
        if used:
            say(
                "✅ A client received the bundle; `ling-admin node remote peers` lists it once it joins."
            )
            return 0
        RemoteManagementApi.delete_setup_key(token, str(key["id"]))
        say("The code was not used; its setup key is withdrawn.")
        return 1

    @classmethod
    def peers(cls, say: Say) -> int:
        """`ling-admin node remote peers`.

        Args:
            say: Where lines go.

        Returns:
            int: The exit code.
        """
        ready = cls.ready(say)
        if ready is None:
            return 1
        state, token = ready
        listed = RemoteManagementApi.peers(token)
        if listed is None:
            say("❌ Management did not answer.")
            return 1
        mine = RemoteState.section(state, "node_peer").get("id")
        domain = RemoteState.text(state, "overlay_domain")
        for peer in sorted(listed, key=lambda item: str(item.get("name", ""))):
            seen = (
                "connected now"
                if peer.get("connected") is True
                else f"last seen {peer.get('last_seen') or '?'}"
            )
            label = f"{peer.get('dns_label')}.{domain}"
            this = "  (this node)" if peer.get("id") == mine else ""
            say(f"{peer.get('name', '?'):<20} {label:<40} {peer.get('os', '?'):<16} {seen}{this}")
        if not listed:
            say("No peers.")
        return 0

    @classmethod
    def match(cls, peers: list[Json], wanted: str) -> list[Json]:
        """The peers a word names: an id (or a prefix of four characters or more), a name or a label.

        Args:
            peers: The overlay's peers.
            wanted: What the user typed.

        Returns:
            list[Json]: The peers it names.
        """
        word = wanted.lower().strip().rstrip(".")
        found = []
        for peer in peers:
            names = {str(peer.get(key, "")).lower() for key in ("name", "dns_label", "hostname")}
            label = str(peer.get("dns_label", "")).lower()
            peer_id = str(peer.get("id", "")).lower()
            by_id = peer_id == word or (len(word) >= ID_PREFIX_MIN and peer_id.startswith(word))
            first, dot, _ = word.partition(".")
            if by_id or word in names or (dot and first == label):
                found.append(peer)
        return found

    @classmethod
    def revoke(cls, wanted: str, say: Say) -> int:
        """`ling-admin node remote revoke <peer>`.

        Args:
            wanted: The peer's id, name or overlay label.
            say: Where lines go.

        Returns:
            int: The exit code.
        """
        ready = cls.ready(say)
        if ready is None:
            return 1
        state, token = ready
        listed = RemoteManagementApi.peers(token)
        if listed is None:
            say("❌ Management did not answer.")
            return 1
        found = cls.match(listed, wanted)
        if len(found) != 1:
            say(
                f"❌ {'No peer' if not found else 'Several peers'} named {wanted}: `ling-admin node remote peers`."
            )
            return EXIT_USAGE
        peer = found[0]
        if peer.get("id") == RemoteState.section(state, "node_peer").get("id"):
            say(
                "❌ That is this node; `ling-admin remote remove --purge` takes the whole overlay away."
            )
            return EXIT_USAGE
        if not RemoteManagementApi.delete_peer(token, str(peer.get("id"))):
            say("❌ Management did not delete the peer.")
            return 1
        say(
            f"✅ Revoked {peer.get('name')}: its key is forgotten, and it can be enrolled again only on the LAN."
        )
        return 0
