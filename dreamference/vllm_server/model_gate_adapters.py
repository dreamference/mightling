"""The model gate's adapter rules (specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §8.3, §10.1).

Copied beside `model_gate_service.py` into the gate's container and imported by it, so it uses
the standard library only. The gate is where an adapter is selected and loaded: a request names
`<base>+<name>`; the gate looks the name up in the list `server start` verified (`adapters.json` in
its state folder, read on every request like the run's record), refuses one that is not there (404,
never the base in its place), loads it into the engine on first use from the path recorded there
(hashing its files first), unloads the least recently used one past the engine's limit, and
rewrites the id to SGLang's `<base>:<name>` on the way in and back on the way out. Nothing a request
says ever becomes a path, a client may not name an engine-side adapter itself, and the engine's
load and unload endpoints are refused on the public port. The engine is started with an API key
that only the gate holds, which it adds to every request it forwards.
"""

import asyncio
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Final

LIST_FILE: Final[str] = "adapters.json"
KEY_FILE: Final[str] = "engine.key"
ADAPTER_SEPARATOR: Final[str] = "+"
ENGINE_SEPARATOR: Final[str] = ":"

# Refused on the public port whatever the gate's state: only the gate loads adapters.
REFUSED_PATHS: Final[tuple[str, ...]] = (
    "/load_lora_adapter",
    "/load_lora_adapter_from_tensors",
    "/unload_lora_adapter",
)
# The endpoints whose body names a model; the gate reads and rewrites theirs.
GENERATION_PATHS: Final[tuple[str, ...]] = (
    "/v1/responses",
    "/v1/chat/completions",
    "/v1/completions",
)
MODELS_PATH: Final[str] = "/v1/models"
ENGINE_TIMEOUT_S: Final[float] = 120.0
CHUNK: Final[int] = 1 << 20
HTTP_OK: Final[int] = 200
LORA_PATH_FIELD: Final[str] = "lora_path"
FRAMING_LENGTH: Final[str] = "length"
FRAMING_CHUNKED: Final[str] = "chunked"
CRLF: Final[bytes] = b"\r\n"
ADAPTER_FIELDS: Final[tuple[str, ...]] = ("version", "display_name", "description", "trust")
CHUNKED: Final[bytes] = b"transfer-encoding: chunked"


class ModelGateAdapters:
    """What the gate does with adapters: the list, the checks, the loader and the rewrites."""

    def __init__(self, state_dir: str, upstream: tuple[str, int]) -> None:
        """Keeps where the list is and where the engine is.

        Args:
            state_dir: The gate's state folder (`adapters.json`, `engine.key`).
            upstream: The engine's loopback address and port.
        """
        self.state_dir = state_dir
        self.upstream = upstream
        self.loaded: OrderedDict[str, bool] = OrderedDict()
        self.synced = False
        self.locks: dict[str, asyncio.Lock] = {}
        # One load at a time: a load makes room against the count of what is loaded, and two
        # concurrent loads would each count without the other and leave the engine over its limit.
        self.load_lock = asyncio.Lock()

    # -- the list and the key ------------------------------------------------------------------

    def listing(self) -> dict[str, object] | None:
        """The adapters `server start` verified for the served base.

        Returns:
            dict[str, object] | None: `base` (the served id), `max_loaded` and `adapters` (name to
            `path`, `files`, `display_name`, `description`, `version`, `trust`); None when the
            server serves no adapters.
        """
        try:
            with (Path(self.state_dir) / LIST_FILE).open(encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or not isinstance(record.get("adapters"), dict):
            return None
        return record

    def key(self) -> str | None:
        """The engine's API key, which only the gate holds.

        Returns:
            str | None: The key, or None when there is none.
        """
        try:
            with (Path(self.state_dir) / KEY_FILE).open(encoding="utf-8") as handle:
                return handle.read().strip() or None
        except OSError:
            return None

    # -- the request ---------------------------------------------------------------------------

    @classmethod
    def refusal(cls, status: int, code: str, message: str) -> tuple[int, dict[str, object]]:
        """An answer in the API's own error shape.

        Args:
            status: The HTTP status.
            code: The error's code.
            message: What the person reads.

        Returns:
            tuple[int, dict[str, object]]: The status and the body.
        """
        kind = "invalid_request_error" if status < 500 else "service_unavailable"  # noqa: PLR2004
        return status, {"error": {"message": message, "type": kind, "code": code, "param": "model"}}

    def check(
        self, record: dict[str, object], listing: dict[str, object]
    ) -> tuple[int, dict[str, object]] | str | None:
        """Decides what a generation request's body asks for.

        Args:
            record: The request's JSON body.
            listing: `listing()`'s answer.

        Returns:
            tuple | str | None: A refusal (status and body); the adapter's name when the request
            names one that may be served; None for the base.
        """
        model = record.get("model")
        if LORA_PATH_FIELD in record:
            return self.refusal(
                400,
                "adapter_not_allowed",
                "Name an adapter as <base>+<name>; lora_path is refused.",
            )
        if not isinstance(model, str) or ADAPTER_SEPARATOR not in model:
            if isinstance(model, str) and ENGINE_SEPARATOR in model:
                return self.refusal(400, "adapter_not_allowed", "Name an adapter as <base>+<name>.")
            return None
        base, _, name = model.partition(ADAPTER_SEPARATOR)
        adapters = listing.get("adapters")
        if not isinstance(adapters, dict) or name not in adapters:
            return self.refusal(
                404,
                "adapter_not_installed",
                f"The adapter {name} is not installed on this node (`ling-admin adapter list`); "
                "`/adapter off` or a new session uses the base model.",
            )
        if base != listing.get("base"):
            return self.refusal(
                404,
                "adapter_base_mismatch",
                f"The adapter {name} is for {listing.get('base')}, not {base}.",
            )
        return name

    # -- the loader ----------------------------------------------------------------------------

    async def ensure_loaded(self, name: str, listing: dict[str, object]) -> str | None:
        """Loads an adapter into the engine if it is not loaded, once under concurrent requests.

        Args:
            name: An adapter of the list.
            listing: `listing()`'s answer.

        Returns:
            str | None: None when it is loaded; otherwise why it could not be.
        """
        lock = self.locks.setdefault(name, asyncio.Lock())
        async with lock:
            # A loaded adapter does not wait behind another adapter's load.
            if self.synced and name in self.loaded:
                self.loaded.move_to_end(name)
                return None
            async with self.load_lock:
                if not self.synced:
                    await self.sync(listing)
                if name in self.loaded:
                    self.loaded.move_to_end(name)
                    return None
                adapters = listing["adapters"]
                entry = adapters[name] if isinstance(adapters, dict) else {}
                problem = await asyncio.to_thread(self.files_problem, entry)
                if problem:
                    return problem
                await self.make_room(int(str(listing.get("max_loaded") or 8)))
                status, body = await self.engine_call(
                    "POST", "/load_lora_adapter", {"lora_name": name, "lora_path": entry["path"]}
                )
                if status != HTTP_OK:
                    return (
                        f"the engine did not load {name}: {body[:300].decode('utf-8', 'replace')}"
                    )
                self.loaded[name] = True
                return None

    async def sync(self, listing: dict[str, object]) -> None:
        """Learns which adapters the engine holds (those given at its start), and unloads removed ones.

        Args:
            listing: `listing()`'s answer.
        """
        status, body = await self.engine_call("GET", MODELS_PATH, None)
        if status != HTTP_OK:
            return
        adapters = listing.get("adapters")
        for name in self.engine_adapters(body):
            if isinstance(adapters, dict) and name in adapters:
                self.loaded[name] = True
            else:
                await self.engine_call("POST", "/unload_lora_adapter", {"lora_name": name})
        self.synced = True

    async def make_room(self, limit: int) -> None:
        """Unloads the least recently used adapters until one more fits.

        Args:
            limit: The most the engine holds at once.
        """
        while len(self.loaded) >= max(1, limit):
            oldest, _ = self.loaded.popitem(last=False)
            status, _ = await self.engine_call(
                "POST", "/unload_lora_adapter", {"lora_name": oldest}
            )
            if status != HTTP_OK:
                # The engine may still hold it: the next request asks the engine what it holds.
                self.synced = False

    @classmethod
    def files_problem(cls, entry: object) -> str | None:
        """Hashes an adapter's files against the list (a file changed since `server start`).

        Args:
            entry: The adapter's entry in the list.

        Returns:
            str | None: The reason it is refused, or None.
        """
        if not isinstance(entry, dict) or not isinstance(entry.get("files"), dict):
            return "its entry names no files"
        for relative, digest in entry["files"].items():
            if cls.file_digest(Path(str(entry.get("path"))) / relative) != digest:
                return (
                    f"{relative} is missing or changed since the server started; it is not served"
                )
        return None

    @classmethod
    def file_digest(cls, path: Path) -> str:
        """`sha256:<hex>` of a file, or an empty string when it cannot be read.

        Args:
            path: The file.

        Returns:
            str: The digest.
        """
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK), b""):
                    hasher.update(chunk)
        except OSError:
            return ""
        return f"sha256:{hasher.hexdigest()}"

    # -- /v1/models ----------------------------------------------------------------------------

    @classmethod
    def engine_adapters(cls, body: bytes) -> list[str]:
        """The adapter names the engine's own `/v1/models` lists.

        Args:
            body: Its JSON body.

        Returns:
            list[str]: Names of the entries that have a parent.
        """
        try:
            data = json.loads(body).get("data")
        except (ValueError, AttributeError):
            return []
        return [
            str(entry["id"])
            for entry in data or []
            if isinstance(entry, dict) and entry.get("parent")
        ]

    @classmethod
    def models(cls, body: bytes, listing: dict[str, object]) -> dict[str, object]:
        """The model list clients see: the base, then one entry per verified adapter.

        Args:
            body: The engine's `/v1/models` body.
            listing: `listing()`'s answer.

        Returns:
            dict[str, object]: The list, with the engine's own adapter entries dropped.
        """
        try:
            record = json.loads(body)
        except ValueError:
            record = None
        if not isinstance(record, dict):
            return {"object": "list", "data": []}
        data = [
            entry
            for entry in record.get("data") or []
            if isinstance(entry, dict) and not entry.get("parent")
        ]
        base = next((entry for entry in data if entry.get("id") == listing.get("base")), None)
        adapters = listing.get("adapters")
        if base is not None and isinstance(adapters, dict):
            data.extend(cls.entry(base, name, entry) for name, entry in sorted(adapters.items()))
        record["data"] = data
        return record

    @classmethod
    def entry(cls, base: dict[str, object], name: str, adapter: object) -> dict[str, object]:
        """One adapter's entry in the model list (the contract of the spec's §10.1).

        Args:
            base: The base's entry.
            name: The adapter's name.
            adapter: Its entry in the list.

        Returns:
            dict[str, object]: The entry.
        """
        fields = adapter if isinstance(adapter, dict) else {}
        return {
            "id": f"{base['id']}{ADAPTER_SEPARATOR}{name}",
            "object": "model",
            "created": base.get("created"),
            "owned_by": base.get("owned_by"),
            "root": base["id"],
            "parent": base["id"],
            "max_model_len": base.get("max_model_len"),
            "mightling_adapter": {"name": name, **{key: fields.get(key) for key in ADAPTER_FIELDS}},
        }

    # -- a generation request ------------------------------------------------------------------

    @classmethod
    async def read_body(
        cls, reader: asyncio.StreamReader, framing: tuple[str, int], limit: int
    ) -> bytes | None:
        """Reads a request's whole body.

        Args:
            reader: The client.
            framing: `ModelGateService.framing()`'s answer.
            limit: The most read.

        Returns:
            bytes | None: The body (de-chunked), or None when it is larger than `limit`.
        """
        kind, length = framing
        if kind == FRAMING_LENGTH:
            return await reader.readexactly(length) if length <= limit else None
        if kind != FRAMING_CHUNKED:
            return b""
        out = bytearray()
        while True:
            size = int((await reader.readuntil(b"\r\n")).split(b";", 1)[0].strip() or b"0", 16)
            if size == 0:
                while await reader.readuntil(CRLF) != CRLF:
                    pass
                return bytes(out)
            if len(out) + size > limit:
                return None
            out += (await reader.readexactly(size + 2))[:size]

    async def rewrite(
        self, body: bytes, listing: dict[str, object]
    ) -> tuple[tuple[int, dict[str, object]] | None, bytes, tuple[bytes, bytes] | None]:
        """Checks a generation request and rewrites its adapter id for the engine.

        Args:
            body: The request's body.
            listing: `listing()`'s answer.

        Returns:
            tuple: A refusal (status and body) or None; the body to send; and the (engine id,
            client id) pair to swap back in the answer, None for the base.
        """
        try:
            record = json.loads(body)
        except ValueError:
            record = None
        if not isinstance(record, dict):
            return None, body, None
        decision = self.check(record, listing)
        if isinstance(decision, tuple):
            return decision, body, None
        if decision is None:
            return None, body, None
        problem = await self.ensure_loaded(decision, listing)
        if problem:
            message = f"The adapter {decision} cannot be served now: {problem}."
            return self.refusal(503, "adapter_unavailable", message), body, None
        client = str(record["model"])
        record["model"] = self.engine_model(client)
        swap = (json.dumps(record["model"]).encode("utf-8"), json.dumps(client).encode("utf-8"))
        return None, json.dumps(record).encode("utf-8"), swap

    # -- the engine ----------------------------------------------------------------------------

    async def engine_call(
        self, method: str, path: str, payload: dict[str, object] | None
    ) -> tuple[int, bytes]:
        """One request of the gate's own to the engine, with the engine's key.

        Args:
            method: `GET` or `POST`.
            path: The path.
            payload: A JSON body, or None.

        Returns:
            tuple[int, bytes]: The status (0 when the engine did not answer) and the body.
        """
        data = json.dumps(payload).encode("utf-8") if payload is not None else b""
        lines = [
            f"{method} {path} HTTP/1.1",
            f"Host: {self.upstream[0]}:{self.upstream[1]}",
            "Connection: close",
            f"Content-Length: {len(data)}",
        ]
        if payload is not None:
            lines.append("Content-Type: application/json")
        key = self.key()
        if key:
            lines.append(f"Authorization: Bearer {key}")
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(*self.upstream), ENGINE_TIMEOUT_S
            )
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + data)
            await writer.drain()
            answer = await asyncio.wait_for(reader.read(), ENGINE_TIMEOUT_S)
        except (TimeoutError, OSError):
            return 0, b""
        finally:
            if writer is not None:
                writer.close()
        return self.parse_response(answer)

    @classmethod
    def parse_response(cls, answer: bytes) -> tuple[int, bytes]:
        """Splits a whole HTTP response read to its end.

        Args:
            answer: The bytes the engine sent before closing.

        Returns:
            tuple[int, bytes]: The status and the body, de-chunked.
        """
        head, _, body = answer.partition(b"\r\n\r\n")
        parts = head.split(b"\r\n", 1)[0].split(b" ")
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if CHUNKED in head.lower():
            body = cls.dechunk(body)
        return status, body

    @classmethod
    def dechunk(cls, body: bytes) -> bytes:
        """Joins a chunked body's chunks.

        Args:
            body: The chunked body.

        Returns:
            bytes: Its data.
        """
        out = bytearray()
        while body:
            size_line, _, rest = body.partition(b"\r\n")
            try:
                size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
            except ValueError:
                break
            if size == 0:
                break
            out += rest[:size]
            body = rest[size + 2 :]
        return bytes(out)

    # -- the rewrites --------------------------------------------------------------------------

    @classmethod
    def engine_model(cls, model: str) -> str:
        """`<base>+<name>` as SGLang names it, `<base>:<name>`: the same length.

        Args:
            model: The client's id.

        Returns:
            str: The engine's.
        """
        return model.replace(ADAPTER_SEPARATOR, ENGINE_SEPARATOR, 1)

    @classmethod
    def replace_stream(
        cls, data: bytes, carry: bytes, old: bytes, new: bytes
    ) -> tuple[bytes, bytes]:
        """Replaces `old` by `new` (the same length) in a stream, across chunk boundaries.

        Args:
            data: The bytes just read.
            carry: What the previous call held back.
            old: The bytes to replace.
            new: Their replacement, of the same length.

        Returns:
            tuple[bytes, bytes]: The bytes to send now and the tail held back: only the longest
            tail that could still begin `old`, so a token is never delayed by anything else.
        """
        joined = (carry + data).replace(old, new)
        for keep in range(min(len(old) - 1, len(joined)), 0, -1):
            if old.startswith(joined[-keep:]):
                return joined[:-keep], joined[-keep:]
        return joined, b""
