"""The request handler of the enrolment listener: `POST /mightling-remote/join` (REMOTE_ACCESS §4.1)."""

import http.server
import json
from typing import TYPE_CHECKING

from dreamference.remote.remote_enrolment import JOIN_PATH

if TYPE_CHECKING:
    from dreamference.remote.remote_enrolment_server import RemoteEnrolmentServer

MAX_BODY = 4096
HTTP_NOT_FOUND = 404
HTTP_OK = 200


class RemoteJoinHandler(http.server.BaseHTTPRequestHandler):
    """`POST /mightling-remote/join`."""

    server: "RemoteEnrolmentServer"

    def do_POST(self) -> None:
        """Answers a join request."""
        if self.path != JOIN_PATH:
            self.answer(HTTP_NOT_FOUND, {"error": "Not found."})
            return
        length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
        body = self.rfile.read(length) if length > 0 else b""
        address = str(self.client_address[0])
        status, payload = self.server.enrolment.handle(address, body)
        self.server.on_event(
            f"{address}: bundle sent"
            if status == HTTP_OK
            else f"{address}: refused ({payload.get('error')})"
        )
        self.answer(status, payload)

    def answer(self, status: int, payload: dict[str, object]) -> None:
        """Writes a JSON answer.

        Args:
            status: The HTTP status.
            payload: The JSON body.
        """
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the base class's signature
        """Keeps http.server's own log off the terminal; `on_event` says what happened."""
