"""Enrolment on the LAN, node side: `ling-admin node remote code` (REMOTE_ACCESS §4.1, §14.2 item 5).

The node prints an eight-digit code, valid ten minutes and for one use, and listens for it on the
LAN while it is valid. A client that presents it (`ling node remote join --code …`) gets the
bundle: the CA certificate, the management URL (the box's name), the relay's name, the overlay
domain, the node's overlay name and id, and a one-off setup key minted for it. Ten wrong codes
withdraw it. The listener answers only addresses of a local network: loopback (this machine is a
peer already), NetBird's own range (the overlay is not a route for enrolment) and anything public
are refused, which is §4.1's "refuses off the LAN" from the node's side.

The setup key lives a day in NetBird at the least, so when the ten minutes end unused the caller
withdraws it in management; a bundle copied later holds a dead key.
"""

import hmac
import ipaddress
import json
import secrets
import time

CODE_DIGITS = 8
CODE_LIFETIME_S = 600
MAX_ATTEMPTS = 10
ENROL_PORT = 33074
JOIN_PATH = "/mightling-remote/join"
BUNDLE_VERSION = 1
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_FORBIDDEN = 403
HTTP_GONE = 410
# A client on the node's LAN has one of these; NetBird's peers are in the shared address space
# (RFC 6598), which is refused before this list is read.
LAN_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    )
)
OVERLAY_NETWORK = ipaddress.ip_network("100.64.0.0/10")


class RemoteEnrolment:
    """One enrolment code and what it unlocks."""

    def __init__(
        self, bundle: dict[str, object], code: str | None = None, now: float | None = None
    ) -> None:
        """Opens an enrolment window.

        Args:
            bundle: What a client that presents the code receives.
            code: The code; a fresh random one by default.
            now: The window's start (the monotonic clock by default).
        """
        self.bundle = bundle
        self.code = code or self.new_code()
        self.deadline = (time.monotonic() if now is None else now) + CODE_LIFETIME_S
        self.attempts = 0
        self.used = False
        self.withdrawn = False

    @classmethod
    def new_code(cls) -> str:
        """A random eight-digit code.

        Returns:
            str: The digits, with leading zeros kept.
        """
        return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"

    @classmethod
    def lan_peer(cls, address: str) -> bool:
        """Whether a connection comes from the local network.

        Args:
            address: The client's address as the socket reports it (an IPv4-mapped IPv6 address
                counts as its IPv4 address).

        Returns:
            bool: True for a private or link-local address that is not NetBird's range.
        """
        try:
            parsed = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return False
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
            parsed = parsed.ipv4_mapped
        if parsed in OVERLAY_NETWORK:
            return False
        return any(parsed in network for network in LAN_NETWORKS)

    def open(self, now: float | None = None) -> bool:
        """Whether the code can still be used.

        Args:
            now: The time on the monotonic clock; defaults to it.

        Returns:
            bool: False once used, withdrawn or past its ten minutes.
        """
        current = time.monotonic() if now is None else now
        return not self.used and not self.withdrawn and current < self.deadline

    def handle(
        self, address: str, body: bytes, now: float | None = None
    ) -> tuple[int, dict[str, object]]:
        """Answers one join request.

        Args:
            address: The client's address.
            body: The request's JSON body, `{"code": "…"}`.
            now: The time on the monotonic clock; defaults to it.

        Returns:
            tuple[int, dict[str, object]]: The HTTP status and the JSON answer: the bundle on
            success, an `error` otherwise.
        """
        if not self.lan_peer(address):
            return HTTP_FORBIDDEN, {"error": "Enrolment works only from the node's local network."}
        if not self.open(now):
            return HTTP_GONE, {
                "error": "This code is used, withdrawn or expired: ask for a new one on the node."
            }
        try:
            request = json.loads(body or b"{}")
        except ValueError:
            return HTTP_BAD_REQUEST, {"error": "Not JSON."}
        code = request.get("code") if isinstance(request, dict) else None
        if not isinstance(code, str) or not hmac.compare_digest(code.strip(), self.code):
            self.attempts += 1
            if self.attempts >= MAX_ATTEMPTS:
                self.withdrawn = True
                return HTTP_GONE, {"error": "Too many wrong codes: this one is withdrawn."}
            return HTTP_FORBIDDEN, {
                "error": "Wrong code.",
                "attempts_left": MAX_ATTEMPTS - self.attempts,
            }
        self.used = True
        return HTTP_OK, {"version": BUNDLE_VERSION, **self.bundle}
