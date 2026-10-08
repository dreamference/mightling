"""A stand-in for GitHub's release API, serving the assets in one folder, for testing install.ps1.

    python .github/scripts/fake_release.py <assets-dir> <port> <repo> <tag>

`GET /repos/<repo>/releases/latest` and `/releases/tags/<tag>` answer with a release whose assets
are every file in <assets-dir>; each asset's `url` serves the file itself, as GitHub's API does
for `Accept: application/octet-stream`. Used by .github/workflows/windows.yml only.
"""

import http.server
import json
import os
import sys


def main() -> None:
    folder, port, repo, tag = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
    base = f"http://127.0.0.1:{port}"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (the stdlib's name)
            release_paths = (f"/repos/{repo}/releases/latest", f"/repos/{repo}/releases/tags/{tag}")
            if self.path in release_paths:
                assets = [{"name": name, "url": f"{base}/assets/{name}"} for name in sorted(os.listdir(folder))]
                self.reply(200, json.dumps({"tag_name": tag, "assets": assets}).encode(), "application/json")
            elif self.path.startswith("/assets/"):
                path = os.path.join(folder, os.path.basename(self.path))
                if os.path.isfile(path):
                    with open(path, "rb") as handle:
                        self.reply(200, handle.read(), "application/octet-stream")
                else:
                    self.reply(404, b"not found", "text/plain")
            else:
                self.reply(404, b"not found", "text/plain")

        def reply(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
