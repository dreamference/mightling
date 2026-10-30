"""
The page the desktop app's audit session opens in a preview tab
(specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §5, Phase 3 item 4).

This module provides the PreviewPage class: a loopback HTTP server, for the length of one
`ling-admin audit egress --app` session, serving one page that reaches for the internet every
way a page can (a picture, a script, a stylesheet, a `fetch`, DNS prefetch and preconnect hints (in the page and added by script),
a link followed by script, and a WebRTC peer connection with a STUN server and a TURN server
over TCP). The app opens it in a preview tab (`MIGHTLING_APP_AUDIT_PREVIEW`);
the preview's rules must turn every one of those away before a connection or a DNS query, which
the trace then shows. The page's own port is the one loopback port it adds to the allowlist.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, List

# Sites the page reaches for. Reserved example names, so even a leak would ask nobody real.
PAGE: Final[str] = """<!doctype html>
<html><head><title>Mightling audit page</title>
<link rel="dns-prefetch" href="//example.org">
<link rel="preconnect" href="https://example.net">
<link rel="stylesheet" href="https://example.com/audit.css">
<script src="https://example.com/audit.js"></script>
</head><body>
<h1>The egress audit's preview page</h1>
<img src="https://example.com/audit.png" alt="a picture from the internet">
<a id="out" href="https://example.org/">a link out</a>
<script>
fetch("https://example.com/api").catch(() => {});
const hint = document.createElement("link");
hint.rel = "dns-prefetch";
hint.href = "//example.net";
document.head.append(hint);
setTimeout(() => document.getElementById("out").click(), 500);
// A peer connection with a STUN server and a TURN server over TCP: neither passes the request rules,
// so only the session's proxy (a dead one at /airgapped on) and the WebRTC policy keep them off the network.
try {
  const peer = new RTCPeerConnection({ iceServers: [
    { urls: "stun:stun.example.org:3478" },
    { urls: "turn:turn.example.net:443?transport=tcp", username: "audit", credential: "audit" },
  ] });
  peer.createDataChannel("audit");
  peer.createOffer().then((offer) => peer.setLocalDescription(offer)).catch(() => {});
} catch (error) {}
fetch("/seen");
</script>
</body></html>
"""


class PreviewPage:
    """A loopback server for the audit's preview page, recording what was asked of it."""

    def __init__(self) -> None:
        self.paths: List[str] = []
        page = PAGE.encode()
        paths = self.paths

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (the standard library's name)
                paths.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        """The loopback port the page is on."""
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        """The page's address, as the app's `MIGHTLING_APP_AUDIT_PREVIEW` takes it."""
        return f"http://127.0.0.1:{self.port}/"

    def loaded(self) -> bool:
        """
        Whether the preview loaded the page and ran it (its script asks for `/seen`).

        Returns:
            bool: True once the page's own request came back.
        """
        return "/seen" in self.paths

    def start(self) -> "PreviewPage":
        """Serves the page until stop()."""
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stops serving and closes the port."""
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "PreviewPage":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
