"""The listener behind `ling-admin node remote code`: one HTTP endpoint, for ten minutes at most.

It runs in the foreground of the command that printed the code and closes when the code is used,
withdrawn or expired, so no port stays open after enrolment. Plain HTTP on the trusted LAN: the
bundle's secret is a one-off key that is spent on first use, and the CA it carries is the one the
client then pins (REMOTE_ACCESS §4.1).
"""

import http.server
from collections.abc import Callable

from dreamference.remote.remote_enrolment import RemoteEnrolment
from dreamference.remote.remote_join_handler import RemoteJoinHandler

POLL_S = 1.0


class RemoteEnrolmentServer(http.server.HTTPServer):
    """An HTTP server holding one enrolment window."""

    def __init__(
        self, address: tuple[str, int], enrolment: RemoteEnrolment, on_event: Callable[[str], None]
    ) -> None:
        """Binds the listener.

        Args:
            address: Where to listen (all interfaces, the enrolment port).
            enrolment: The window it serves.
            on_event: Called with one line per request, for the person at the node.
        """
        self.enrolment = enrolment
        self.on_event = on_event
        super().__init__(address, RemoteJoinHandler)
        self.timeout = POLL_S

    def serve_window(self) -> bool:
        """Serves until the code is used, withdrawn or expired.

        Returns:
            bool: Whether a client received the bundle.
        """
        try:
            while self.enrolment.open():
                self.handle_request()
        finally:
            self.server_close()
        return self.enrolment.used
