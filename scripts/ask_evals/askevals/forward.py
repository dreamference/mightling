"""The model server's one way into the jail (spec §9.2): a byte pipe between a TCP port and a
Unix socket, run once on each side of the network namespace.

    outside:  python3 forward.py unix SOCKET HOST PORT     (the socket, joined to the engine)
    inside:   python3 forward.py tcp PORT SOCKET           (loopback PORT, joined to the socket)

A Unix socket path must stay under 108 bytes, so the runner puts it in `$XDG_RUNTIME_DIR`.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def _join(
    client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, opener
) -> None:
    try:
        reader, writer = await opener()
    except OSError:
        client_writer.close()
        return
    await asyncio.gather(_pipe(client_reader, writer), _pipe(reader, client_writer))


async def serve_unix(path: str, host: str, port: int, ready: asyncio.Event | None = None) -> None:
    """Listens on the Unix socket `path` and joins each connection to `host:port`."""
    if pathlib.Path(path).exists():
        pathlib.Path(path).unlink()
    server = await asyncio.start_unix_server(
        lambda r, w: _join(r, w, lambda: asyncio.open_connection(host, port)), path
    )
    pathlib.Path(path).chmod(0o600)
    if ready:
        ready.set()
    async with server:
        await server.serve_forever()


async def serve_tcp(port: int, path: str, ready: asyncio.Event | None = None) -> None:
    """Listens on loopback `port` and joins each connection to the Unix socket `path`."""
    server = await asyncio.start_server(
        lambda r, w: _join(r, w, lambda: asyncio.open_unix_connection(path)), "127.0.0.1", port
    )
    if ready:
        ready.set()
    async with server:
        await server.serve_forever()


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[0] == "unix":
        asyncio.run(serve_unix(argv[1], argv[2], int(argv[3])))
    elif len(argv) == 3 and argv[0] == "tcp":
        asyncio.run(serve_tcp(int(argv[1]), argv[2]))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
