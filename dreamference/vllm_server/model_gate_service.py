"""
The model gate: the one door in front of the model server (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §18).

This file is executed *inside* the gate's container, not on the host: `ModelGate` bind-mounts a
copy of it into the model server's own image (which carries a Python) and runs it there, as
`DiffusionServerManager` does with its service. It is imported on the host too (by the tests and
by `ModelGate` for the state it reports), so it uses the standard library only.

The engine listens on loopback at an internal port; this proxy owns the public port, on every
interface, as the engine did. Every request passes through untouched (streamed both ways, never
buffered) unless a SWE-bench run holds the gate: then a request that is not the run's is answered
503 in the API's own error shape, naming the run and its ETA, and never reaches the engine.

A request is the run's when it comes from the run's internal Docker network: its containers reach
the model at that network's gateway, so their source address is in the network's subnet, which
the run writes into its record. No other client can send from there without the Docker socket.

The state is two files the host writes and this process only reads, on every request:
`run.json` (the run's record, refreshed by a heartbeat) and `pause.json` (`ling-admin night
pause`). A missing, unreadable or stale run record means the gate is open, so a run that dies
without cleaning up cannot keep the model closed: a heartbeat older than `STALE_S` is ignored.
"""

import argparse
import asyncio
import ipaddress
import json
import os
import sys
import time
from typing import Any, Dict, Final, List, Optional, Tuple

# Answered by the gate itself, never forwarded: what the gate is doing, as JSON.
PROBE_PATH: Final[str] = "/mightling-gate"

RUN_FILE: Final[str] = "run.json"
PAUSE_FILE: Final[str] = "pause.json"

# A run's record counts only while its heartbeat is this recent (the run refreshes it every 15 s).
STALE_S: Final[float] = 180.0

# Read-only probes pass even while the gate is closed: the launcher reads /v1/models before its
# first turn, and the run's own admission, Night Shift and `ling-code` read /metrics. None of them
# runs the model. `/health_generate` does, and is not here.
READ_ONLY_PATHS: Final[Tuple[str, ...]] = (
    "/health", "/v1/models", "/metrics", "/get_model_info", "/get_server_info",
    "/server_info", "/model_info", "/version", "/ping", PROBE_PATH,
)

HEAD_LIMIT: Final[int] = 1 << 20
BUFFER: Final[int] = 1 << 16
UPSTREAM_CONNECT_TIMEOUT_S: Final[float] = 10.0
# A refused request's body is read before the answer, so closing never resets the connection
# under the client (which would lose the 503 and its message); bounded in size and time.
DRAIN_LIMIT: Final[int] = 64 << 20
DRAIN_TIMEOUT_S: Final[float] = 30.0

REFUSAL_CODE: Final[str] = "benchmark_running"
# Codex retries a 5xx about 30 times and honours Retry-After with no upper bound: 0 makes those
# retries immediate, so the message reaches the user in a moment instead of after ~25 s of
# backoff. Never a real delay: an hour here would hang a turn for a day.
RETRY_AFTER: Final[str] = "0"

DROPPED_REQUEST_HEADERS: Final[Tuple[bytes, ...]] = (b"connection", b"keep-alive", b"proxy-connection")


class ModelGateService:
    """The proxy and the rules it applies; the rules are classmethods the host shares."""

    def __init__(self, listen: Tuple[str, int], upstream: Tuple[str, int], state_dir: str) -> None:
        """
        Args:
            listen: Address and port to serve on (the model server's public port).
            upstream: The engine's loopback address and internal port.
            state_dir: The directory holding `run.json` and `pause.json`.
        """
        self.listen = listen
        self.upstream = upstream
        self.state_dir = state_dir
        self.server: Optional[asyncio.AbstractServer] = None

    # -- the rules -----------------------------------------------------------------------------

    @classmethod
    def read_json(cls, path: str) -> Optional[Dict[str, Any]]:
        """
        Args:
            path: A JSON file.

        Returns:
            Optional[Dict[str, Any]]: Its object, or None if it is missing or not an object.
        """
        try:
            with open(path, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    @classmethod
    def describe_eta(cls, seconds: Any) -> Optional[str]:
        """
        Args:
            seconds: Seconds left, or None when not known yet.

        Returns:
            Optional[str]: `about 9 h left`, `about 1 h 25 min left`, `about 40 min left`.
        """
        if not isinstance(seconds, (int, float)) or seconds < 0:
            return None
        minutes = max(1, int(round(seconds / 60)))
        if minutes < 60:
            return f"about {minutes} min left"
        hours, rest = divmod(minutes, 60)
        if hours < 3 and rest >= 5:
            return f"about {hours} h {rest} min left"
        return f"about {int(round(minutes / 60))} h left"

    @classmethod
    def message(cls, run: Dict[str, Any]) -> str:
        """
        The refusal's text.

        Args:
            run: The run's record.

        Returns:
            str: e.g. "The model is running a benchmark (night 1, 37/100 done, about 9 h left).
            Try later or run `ling-admin night pause`."
        """
        parts = [str(run.get("label") or f"SWE-bench run {run.get('run', '?')}")]
        total, done = run.get("total"), run.get("done")
        if isinstance(total, int) and total > 0 and isinstance(done, int):
            parts.append(f"{done}/{total} done")
        eta = cls.describe_eta(run.get("eta_s"))
        if eta:
            parts.append(eta)
        return (f"The model is running a benchmark ({', '.join(parts)}). "
                "Try later or run `ling-admin night pause`.")

    @classmethod
    def state(cls, state_dir: str, now: Optional[float] = None) -> Dict[str, Any]:
        """
        What the gate does right now, from its two files.

        Args:
            state_dir: The directory holding `run.json` and `pause.json`.
            now: The time; defaults to the clock.

        Returns:
            Dict[str, Any]: `state` (`open`, `closed` or `paused`), and with a live run its
            `run`, `id`, `label`, `done`, `total`, `eta_s`, `networks` and refusal `message`;
            `paused_until` while a pause is in force; `stale` when a run's record was ignored.
        """
        now = time.time() if now is None else now
        result: Dict[str, Any] = {"gate": "mightling", "state": "open"}
        pause = cls.read_json(os.path.join(state_dir, PAUSE_FILE)) or {}
        until = pause.get("until")
        if isinstance(until, (int, float)) and until > now:
            result["paused_until"] = until
        run = cls.read_json(os.path.join(state_dir, RUN_FILE))
        if run is None:
            return result
        heartbeat = run.get("heartbeat")
        networks = run.get("networks")
        if not isinstance(heartbeat, (int, float)) or now - heartbeat > STALE_S \
                or not isinstance(networks, list) or not networks:
            result["stale"] = run.get("run")
            return result
        result.update({key: run.get(key) for key in ("run", "id", "label", "done", "total", "eta_s", "started")})
        result["networks"] = [str(network) for network in networks]
        result["message"] = cls.message(run)
        result["state"] = "paused" if "paused_until" in result else "closed"
        return result

    @classmethod
    def from_networks(cls, peer: str, networks: List[str]) -> bool:
        """
        Args:
            peer: The client's address.
            networks: The run's subnets.

        Returns:
            bool: Whether the address is in one of them.
        """
        try:
            address = ipaddress.ip_address(peer.split("%", 1)[0])
        except ValueError:
            return False
        mapped = getattr(address, "ipv4_mapped", None)
        address = mapped or address
        for network in networks:
            try:
                if address in ipaddress.ip_network(network, strict=False):
                    return True
            except ValueError:
                continue
        return False

    @classmethod
    def allows(cls, state: Dict[str, Any], peer: str, method: str, path: str) -> bool:
        """
        Whether a request passes.

        Args:
            state: `state()`'s answer.
            peer: The client's address.
            method: The request's method.
            path: Its path, without the query.

        Returns:
            bool: True unless a run holds the gate and the request is neither the run's nor a
            read-only probe.
        """
        if state.get("state") != "closed":
            return True
        if method in ("GET", "HEAD") and (path in READ_ONLY_PATHS or path.startswith("/v1/models/")):
            return True
        return cls.from_networks(peer, state.get("networks") or [])

    @classmethod
    def refusal(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Args:
            state: `state()`'s answer while closed.

        Returns:
            Dict[str, Any]: The body, in the API's own error shape.
        """
        return {"error": {"message": state.get("message") or cls.message({}), "type": "service_unavailable",
                          "code": REFUSAL_CODE, "param": None}}

    # -- HTTP ----------------------------------------------------------------------------------

    @classmethod
    def parse_head(cls, head: bytes) -> Optional[Tuple[str, str, List[Tuple[bytes, bytes]]]]:
        """
        Args:
            head: The request line and headers, ending with an empty line.

        Returns:
            Optional[tuple]: `(method, path, headers)`, the path without its query; None if
            the head is not an HTTP/1.x request.
        """
        lines = head.split(b"\r\n")
        parts = lines[0].split(b" ")
        if len(parts) != 3 or not parts[2].startswith(b"HTTP/1."):
            return None
        method = parts[0].decode("latin-1")
        target = parts[1].decode("latin-1")
        if "://" in target:  # absolute form
            target = "/" + target.split("://", 1)[1].partition("/")[2]
        path = target.split("?", 1)[0] or "/"
        headers = []
        for line in lines[1:]:
            if not line:
                continue
            name, separator, value = line.partition(b":")
            if not separator:
                return None
            headers.append((name.strip().lower(), value.strip()))
        return method, path, headers

    @classmethod
    def rewrite_head(cls, head: bytes) -> bytes:
        """
        One request per connection: the hop-by-hop connection headers go and `Connection: close`
        takes their place, so the engine closes after its answer and a client never sends a
        second request down a connection that was let through for the first.

        Args:
            head: The request line and headers, ending with an empty line.

        Returns:
            bytes: The head to send to the engine.
        """
        lines = head.split(b"\r\n")
        kept = [lines[0]] + [line for line in lines[1:] if line and
                             line.partition(b":")[0].strip().lower() not in DROPPED_REQUEST_HEADERS]
        return b"\r\n".join(kept + [b"Connection: close", b"", b""])

    @classmethod
    def framing(cls, headers: List[Tuple[bytes, bytes]]) -> Tuple[str, int]:
        """
        Args:
            headers: The request's headers.

        Returns:
            Tuple[str, int]: `("chunked", 0)`, `("length", n)` or `("none", 0)`.
        """
        values = dict(headers)
        if b"chunked" in values.get(b"transfer-encoding", b"").lower():
            return "chunked", 0
        try:
            length = int(values.get(b"content-length", b"0") or 0)
        except ValueError:
            length = 0
        return ("length", length) if length > 0 else ("none", 0)

    @classmethod
    async def copy_body(cls, reader: asyncio.StreamReader, framing: Tuple[str, int],
                        sink: Optional[asyncio.StreamWriter], limit: Optional[int] = None) -> None:
        """
        Moves one request body from the client to the engine (or nowhere, to discard it).

        Args:
            reader: The client.
            framing: `framing()`'s answer.
            sink: The engine, or None to discard.
            limit: Stop after this many bytes (only when discarding).
        """
        kind, remaining = framing
        moved = 0

        async def send(data: bytes) -> None:
            nonlocal moved
            moved += len(data)
            if sink is not None:
                sink.write(data)
                await sink.drain()

        if kind == "length":
            while remaining > 0 and (limit is None or moved < limit):
                data = await reader.read(min(BUFFER, remaining))
                if not data:
                    return
                remaining -= len(data)
                await send(data)
        elif kind == "chunked":
            while limit is None or moved < limit:
                line = await reader.readuntil(b"\r\n")
                await send(line)
                size = int(line.split(b";", 1)[0].strip() or b"0", 16)
                if size == 0:
                    while True:  # trailers, then the empty line
                        trailer = await reader.readuntil(b"\r\n")
                        await send(trailer)
                        if trailer == b"\r\n":
                            return
                await send(await reader.readexactly(size + 2))

    @classmethod
    async def respond(cls, writer: asyncio.StreamWriter, status: int, reason: str, body: Dict[str, Any],
                      extra: Optional[List[Tuple[str, str]]] = None, head_only: bool = False) -> None:
        """
        Writes a JSON answer that closes the connection.

        Args:
            writer: The client.
            status: The status code.
            reason: Its reason phrase.
            body: The JSON body.
            extra: More headers.
            head_only: For a HEAD request.
        """
        data = json.dumps(body).encode("utf-8")
        lines = [f"HTTP/1.1 {status} {reason}", "Content-Type: application/json",
                 f"Content-Length: {len(data)}", "Connection: close"]
        lines += [f"{name}: {value}" for name, value in (extra or [])]
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + (b"" if head_only else data))
        await writer.drain()

    @classmethod
    async def pipe(cls, source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
        """Copies bytes as they arrive until the source ends."""
        while True:
            data = await source.read(BUFFER)
            if not data:
                return
            sink.write(data)
            await sink.drain()

    @classmethod
    async def pipe_response(cls, source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
        """
        Copies the engine's answer: its head with `Connection: close` (so the client never pools
        a connection the gate let through, whatever the engine says), then the body as it
        arrives. Interim `1xx` heads (`100 Continue`) pass as they are.
        """
        while True:
            head = await source.readuntil(b"\r\n\r\n")
            parts = head.split(b"\r\n", 1)[0].split(b" ", 2)
            code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            if 100 <= code < 200 and code != 101:
                sink.write(head)
                await sink.drain()
                continue
            sink.write(cls.rewrite_head(head))
            await sink.drain()
            break
        await cls.pipe(source, sink)

    @classmethod
    async def watch_client(cls, reader: asyncio.StreamReader) -> None:
        """
        Returns when the client goes away after its request; anything it sends meanwhile (a
        pipelined second request) is never forwarded.
        """
        while await reader.read(BUFFER):
            pass

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serves one connection: one request, decided, then refused or streamed through."""
        upstream_writer: Optional[asyncio.StreamWriter] = None
        tasks: List[asyncio.Task] = []
        try:
            peer = writer.get_extra_info("peername")
            peer_address = str(peer[0]) if peer else ""
            try:
                head = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
                return
            request = self.parse_head(head)
            if request is None:
                await self.respond(writer, 400, "Bad Request", {"error": {
                    "message": "Not an HTTP/1.x request.", "type": "invalid_request_error",
                    "code": None, "param": None}})
                return
            method, path, headers = request
            state = self.state(self.state_dir)
            if path == PROBE_PATH and method in ("GET", "HEAD"):
                await self.respond(writer, 200, "OK", state, head_only=method == "HEAD")
                return
            framing = self.framing(headers)
            expects_continue = b"100-continue" in dict(headers).get(b"expect", b"").lower()
            if not self.allows(state, peer_address, method, path):
                self.log(f"refused {method} {path} from {peer_address} ({state.get('run')})")
                if not expects_continue:
                    try:
                        await asyncio.wait_for(self.copy_body(reader, framing, None, DRAIN_LIMIT), DRAIN_TIMEOUT_S)
                    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                            ValueError, ConnectionError):
                        pass
                await self.respond(writer, 503, "Service Unavailable", self.refusal(state),
                                   [("Retry-After", RETRY_AFTER), ("X-Mightling-Gate", "closed")],
                                   head_only=method == "HEAD")
                return
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(*self.upstream), UPSTREAM_CONNECT_TIMEOUT_S)
            except (OSError, asyncio.TimeoutError):
                await self.respond(writer, 502, "Bad Gateway", {"error": {
                    "message": "The model server is not answering behind its gate; it may still be loading.",
                    "type": "service_unavailable", "code": "engine_unavailable", "param": None}},
                                   head_only=method == "HEAD")
                return
            upstream_writer.write(self.rewrite_head(head))
            await upstream_writer.drain()

            async def client_side() -> None:
                await self.copy_body(reader, framing, upstream_writer)
                await self.watch_client(reader)

            tasks = [asyncio.ensure_future(client_side()), asyncio.ensure_future(self.pipe_response(upstream_reader, writer))]
            # The answer is over when the engine closes (it was asked to) or the client leaves,
            # which also tells the engine to stop generating.
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, OSError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except BaseException:
                    pass
            for end in (upstream_writer, writer):
                if end is None:
                    continue
                try:
                    end.close()
                except (OSError, RuntimeError):
                    pass

    @classmethod
    def log(cls, line: str) -> None:
        """One line to the container's log."""
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} model gate: {line}", file=sys.stderr, flush=True)

    async def start(self) -> int:
        """
        Starts serving.

        Returns:
            int: The port served on (useful when `listen` asked for port 0).
        """
        self.server = await asyncio.start_server(self.handle, self.listen[0], self.listen[1],
                                                 limit=HEAD_LIMIT, reuse_address=True)
        return int(self.server.sockets[0].getsockname()[1])

    async def serve(self) -> None:
        """Serves until cancelled."""
        port = await self.start()
        # Not "ready" or "listening on": the model loading monitor reads those words as the engine's.
        self.log(f"on {self.listen[0]}:{port} in front of {self.upstream[0]}:{self.upstream[1]}, "
                 f"state in {self.state_dir}")
        async with self.server:
            await self.server.serve_forever()


def _address(text: str) -> Tuple[str, int]:
    host, _, port = text.rpartition(":")
    return host.strip("[]") or "0.0.0.0", int(port)


def main(argv: Optional[List[str]] = None) -> int:
    """Runs the gate: `--listen 0.0.0.0:8000 --upstream 127.0.0.1:18000 --state /mightling-gate`."""
    parser = argparse.ArgumentParser(description="Mightling's model gate")
    parser.add_argument("--listen", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args(argv)
    service = ModelGateService(_address(args.listen), _address(args.upstream), args.state)
    try:
        asyncio.run(service.serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
