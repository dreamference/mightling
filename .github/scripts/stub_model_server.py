"""A stand-in for the model server, enough for one `puffin exec` session, for testing on CI.

    python .github/scripts/stub_model_server.py <port>

`GET /v1/models` names one model with its context length, as the launcher reads it; `POST
/v1/responses` streams one assistant message, "pong", and completes. Anything else is 404, which
the launcher reads as "not offered" (no `/metrics`, for instance). Used by
.github/workflows/windows.yml for `puffin audit egress` (specs/DREAMFERENCE_PUFFIN_WINDOWS_ARM.md
§18), which needs a session that really talks to a model server and replies.
"""

import http.server
import json
import sys

MODEL = {"id": "stub-model", "object": "model", "owned_by": "ci", "max_model_len": 32768}


def sse(events: list) -> bytes:
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()


def main() -> None:
    port = int(sys.argv[1])

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (the stdlib's name)
            if self.path.rstrip("/") == "/v1/models":
                self.reply(200, json.dumps({"object": "list", "data": [MODEL]}).encode(), "application/json")
            else:
                self.reply(404, b"{}", "application/json")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if self.path.rstrip("/") != "/v1/responses":
                self.reply(404, b"{}", "application/json")
                return
            usage = {"input_tokens": 1, "input_tokens_details": None, "output_tokens": 1,
                     "output_tokens_details": None, "total_tokens": 2}
            body = sse([
                {"type": "response.created", "response": {"id": "resp-1"}},
                {"type": "response.output_item.done", "item": {
                    "type": "message", "role": "assistant", "id": "msg-1",
                    "content": [{"type": "output_text", "text": "pong"}]}},
                {"type": "response.completed", "response": {"id": "resp-1", "usage": usage}},
            ])
            self.reply(200, body, "text/event-stream")

        def reply(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A002 (the stdlib's name)
            sys.stderr.write(f"stub model server: {format % args}\n")

    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
