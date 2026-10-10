"""
Enrolling a client, on the LAN, with an eight-digit code (specs/DREAMFERENCE_MIGHTLING_REMOTE_ACCESS.md §4.1).

`ling-admin remote code` prints a code and listens on the node's LAN for ten minutes, for one
client. The client (`ling node remote join --code …`) proves it knows the code without sending it,
and the node proves the same back over the bundle it returns, so neither a passer-by on the LAN
nor someone answering in the node's place can complete the exchange without the code:

    client → node   POST /mightling/enrol  {"nonce": <32 hex>, "proof": HMAC(code, "mightling-enrol-client|" + nonce),
                                            "client": <host name>}
    node → client   {"bundle": <JSON text>, "mac": HMAC(code, "mightling-enrol-server|" + nonce + "|" + bundle)}

HMAC is HMAC-SHA256 keyed with the code's eight ASCII digits, every value lowercase hex. The bundle
holds the node's CA certificate, the management URL (the box's name), the relay's address, the
overlay's domain, the node's overlay name and id, and a setup key minted for this client: one use,
ten minutes. A wrong proof costs one of ten attempts; the tenth withdraws the code. The listener
refuses a request from an address that is not on a private network or that is in the overlay's
range (RFC 6598), so enrolment cannot be done from outside or through the overlay itself.

What this does not stop, stated: a device on the LAN that sees the exchange learns the setup key,
which is spent by the client within seconds and is worth nothing after ten minutes; it cannot learn
the code from the proof except by trying all 10^8 codes against it, by which time the code is spent.
"""

import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, Final, Optional

from dreamference.remote.remote_settings import CODE_ATTEMPTS, CODE_MINUTES, ENROL_PATH, ENROL_PORT

CLIENT_LABEL: Final[str] = "mightling-enrol-client|"
SERVER_LABEL: Final[str] = "mightling-enrol-server|"
OVERLAY_RANGE: Final = ipaddress.ip_network("100.64.0.0/10")


class RemoteEnrolment:
    """The code, the proofs and the listener."""

    @classmethod
    def new_code(cls) -> str:
        """
        Returns:
            str: Eight random digits.
        """
        return f"{secrets.randbelow(10 ** 8):08d}"

    @classmethod
    def mac(cls, code: str, message: str) -> str:
        """
        Args:
            code: The eight digits.
            message: What is authenticated.

        Returns:
            str: HMAC-SHA256 of the message keyed with the code, lowercase hex.
        """
        return hmac.new(code.encode("ascii"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    @classmethod
    def client_proof(cls, code: str, nonce: str) -> str:
        """
        Args:
            code: The eight digits.
            nonce: The client's nonce.

        Returns:
            str: The client's proof.
        """
        return cls.mac(code, CLIENT_LABEL + nonce)

    @classmethod
    def server_mac(cls, code: str, nonce: str, bundle: str) -> str:
        """
        Args:
            code: The eight digits.
            nonce: The client's nonce.
            bundle: The bundle's exact text.

        Returns:
            str: The node's proof over the bundle.
        """
        return cls.mac(code, SERVER_LABEL + nonce + "|" + bundle)

    @classmethod
    def lan_address(cls, address: str) -> bool:
        """
        Args:
            address: The peer's address as the socket reports it.

        Returns:
            bool: Whether a request from it may enrol: a private, link-local or loopback address,
            never the overlay's range.
        """
        try:
            ip = ipaddress.ip_address(address.split("%")[0])
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if isinstance(ip, ipaddress.IPv4Address) and ip in OVERLAY_RANGE:
            return False
        return ip.is_private or ip.is_link_local or ip.is_loopback

    @classmethod
    def answer(cls, code: str, request: Dict[str, Any], make_bundle: Callable[[str], Optional[Dict[str, Any]]]) -> Optional[Dict[str, str]]:
        """
        Checks one request and makes its answer.

        Args:
            code: The eight digits.
            request: The client's JSON.
            make_bundle: Makes the bundle for a client name (mints its setup key); None on failure.

        Returns:
            Optional[Dict[str, str]]: The answer, or None when the proof is wrong or malformed.
        """
        nonce, proof = request.get("nonce"), request.get("proof")
        if not isinstance(nonce, str) or not isinstance(proof, str) or len(nonce) != 32:
            return None
        if not all(c in "0123456789abcdef" for c in nonce):
            return None
        if not hmac.compare_digest(proof.lower(), cls.client_proof(code, nonce)):
            return None
        client = str(request.get("client") or "client")[:63]
        bundle = make_bundle(client)
        if bundle is None:
            return {"error": "the node could not mint a setup key"}
        text = json.dumps(bundle, sort_keys=True)
        return {"bundle": text, "mac": cls.server_mac(code, nonce, text)}

    @classmethod
    def serve(cls, code: str, make_bundle: Callable[[str], Optional[Dict[str, Any]]],
              port: int = ENROL_PORT, minutes: int = CODE_MINUTES) -> str:
        """
        Listens for one enrolment.

        Args:
            code: The eight digits.
            make_bundle: Makes the bundle for a client name.
            port: The port.
            minutes: How long the code is valid.

        Returns:
            str: `enrolled:<client>`, `expired` or `withdrawn` (ten wrong proofs).
        """
        state: Dict[str, Any] = {"failures": 0, "outcome": None}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def _send(self, status: int, body: Dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:  # noqa: N802 (the standard library's name)
                if self.path != ENROL_PATH:
                    self._send(404, {"error": "not found"})
                    return
                if not cls.lan_address(self.client_address[0]):
                    self._send(403, {"error": "enrolment is on the node's local network only"})
                    return
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), 4096)
                    request = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, OSError):
                    request = {}
                answer = cls.answer(code, request if isinstance(request, dict) else {}, make_bundle)
                if answer is None:
                    state["failures"] += 1
                    left = CODE_ATTEMPTS - state["failures"]
                    if left <= 0:
                        state["outcome"] = "withdrawn"
                    self._send(401, {"error": "wrong code", "attempts_left": max(left, 0)})
                    return
                if "error" in answer:
                    self._send(500, answer)
                    return
                self._send(200, answer)
                state["outcome"] = f"enrolled:{str(request.get('client') or 'client')[:63]}"

        server = HTTPServer(("", port), Handler)
        server.timeout = 1
        deadline = time.monotonic() + minutes * 60
        try:
            while state["outcome"] is None and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        return state["outcome"] or "expired"
