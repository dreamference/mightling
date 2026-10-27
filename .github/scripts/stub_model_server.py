"""A stand-in for the model server, enough for one `ling exec` session, for testing on CI.

    python .github/scripts/stub_model_server.py <port> [--shell-call <command>] [--record <file>]

`GET /v1/models` names one model with its context length, as the launcher reads it; `POST
/v1/responses` streams one assistant message, "pong", and completes. Anything else is 404, which
the launcher reads as "not offered" (no `/metrics`, for instance). Used by
.github/workflows/windows.yml for `ling audit egress` (specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md
§18), which needs a session that really talks to a model server and replies.

`--shell-call <command>` makes the first turn a call of the session's shell tool with that command
(whichever of `exec_command`, `shell_command` or `shell` the request offers), and the turn after
its output the message "pong". `--record <file>` appends each request's JSON body to the file,
one per line, so a test can read what the session sent: the project instructions a hook added, a
tool call's output or the refusal in its place (WINDOWS_ARM §23.4 W1).
"""

import http.server
import json
import sys

MODEL = {"id": "stub-model", "object": "model", "owned_by": "ci", "max_model_len": 32768}

USAGE = {"input_tokens": 1, "input_tokens_details": None, "output_tokens": 1,
         "output_tokens_details": None, "total_tokens": 2}

def sse(events: list) -> bytes:
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()

def shell_call(request: dict, command: str) -> dict | None:
    """A function call of the request's shell tool running `command`, or None if it offers none."""
    names = {tool.get("name") for tool in request.get("tools") or [] if isinstance(tool, dict)}
    if "exec_command" in names:
        name, arguments = "exec_command", {"cmd": command}
    elif "shell_command" in names:
        name, arguments = "shell_command", {"command": command}
    elif "shell" in names:
        name, arguments = "shell", {"command": ["powershell.exe", "-NoProfile", "-Command", command]}
    else:
        return None
    return {"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": name,
            "arguments": json.dumps(arguments)}

def has_call_output(request: dict) -> bool:
    items = request.get("input")
    return isinstance(items, list) and any(
        isinstance(item, dict) and item.get("type", "").endswith("call_output") for item in items)

def main() -> None:
    port = int(sys.argv[1])
    options = dict(zip(sys.argv[2::2], sys.argv[3::2]))
    command = options.get("--shell-call")
    record = options.get("--record")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (the stdlib's name)
            if self.path.rstrip("/") == "/v1/models":
                self.reply(200, json.dumps({"object": "list", "data": [MODEL]}).encode(), "application/json")
            else:
                self.reply(404, b"{}", "application/json")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if self.path.rstrip("/") != "/v1/responses":
                self.reply(404, b"{}", "application/json")
                return
            try:
                request = json.loads(raw)
            except ValueError:
                request = {}
            if record:
                with open(record, "a", encoding="utf-8") as file:
                    file.write(json.dumps(request) + "\n")
            item = None
            if command and not has_call_output(request):
                item = shell_call(request, command)
            if item is None:
                item = {"type": "message", "role": "assistant", "id": "msg-1",
                        "content": [{"type": "output_text", "text": "pong"}]}
            body = sse([
                {"type": "response.created", "response": {"id": "resp-1"}},
                {"type": "response.output_item.done", "item": item},
                {"type": "response.completed", "response": {"id": "resp-1", "usage": USAGE}},
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
