// The forwarder against a stand-in web UI on an ephemeral port: the same cases as the Rust
// `forwarder.rs` tests (a streamed response, an upgraded connection, the message page, a node that
// does not answer). No port 3000 is touched.
import net from "node:net";
import { describe, expect, it } from "vitest";

import { createServer, messagePage, toMessage, toNode } from "./forwarder";

function standIn(handle: (socket: net.Socket) => void): Promise<string> {
  return new Promise((resolve) => {
    const server = net.createServer(handle);
    server.listen(0, "127.0.0.1", () => resolve(`127.0.0.1:${(server.address() as net.AddressInfo).port}`));
  });
}

function forwarder(upstream: Parameters<typeof createServer>[0]): Promise<number> {
  return new Promise((resolve) => {
    const server = createServer(upstream);
    server.listen(0, "127.0.0.1", () => resolve((server.address() as net.AddressInfo).port));
  });
}

function readHead(socket: net.Socket): Promise<string> {
  return new Promise((resolve) => {
    let head = "";
    const onData = (chunk: Buffer) => {
      head += chunk.toString("latin1");
      const end = head.indexOf("\r\n\r\n");
      if (end >= 0) {
        socket.off("data", onData);
        // Paused before the leftover goes back, or a flowing socket re-emits it with no listener.
        socket.pause();
        const rest = head.slice(end + 4);
        if (rest) socket.unshift(Buffer.from(rest, "latin1"));
        resolve(head.slice(0, end + 4));
      }
    };
    socket.on("data", onData);
    socket.resume();
  });
}

const readAll = (socket: net.Socket) =>
  new Promise<string>((resolve) => {
    let text = "";
    socket.on("data", (chunk) => (text += chunk.toString("latin1")));
    socket.on("close", () => resolve(text));
    socket.resume();
  });

const readExact = (socket: net.Socket, count: number) =>
  new Promise<string>((resolve) => {
    let text = "";
    const onData = (chunk: Buffer) => {
      text += chunk.toString("latin1");
      if (text.length >= count) {
        socket.off("data", onData);
        socket.pause();
        if (text.length > count) socket.unshift(Buffer.from(text.slice(count), "latin1"));
        resolve(text.slice(0, count));
      }
    };
    socket.on("data", onData);
    socket.resume();
  });

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("the loopback forwarder", () => {
  it("passes a streamed response through piece by piece with the Host header unchanged", async () => {
    const node = await standIn(async (connection) => {
      const head = await readHead(connection);
      const host = head.includes("Host: localhost:3000\r\n") ? "host-ok" : "host-rewritten";
      connection.write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n");
      for (const piece of ["data: one\n\n", "data: two\n\n", host]) {
        connection.write(piece);
        await sleep(150);
      }
      connection.end();
    });
    const port = await forwarder(toNode(node));
    const client = net.createConnection({ host: "127.0.0.1", port });
    client.write("GET /api/chat/stream HTTP/1.1\r\nHost: localhost:3000\r\n\r\n");
    expect(await readHead(client)).toMatch(/^HTTP\/1\.1 200 OK/);
    const started = Date.now();
    expect(await readExact(client, 11)).toBe("data: one\n\n");
    expect(Date.now() - started).toBeLessThan(140);
    expect(await readAll(client)).toBe("data: two\n\nhost-ok");
  });

  it("carries an upgraded connection both ways until one side closes", async () => {
    const node = await standIn(async (connection) => {
      const head = await readHead(connection);
      expect(head).toContain("Upgrade: websocket");
      connection.write("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n");
      for (let i = 0; i < 2; i++) {
        const frame = await readExact(connection, 5);
        connection.write(frame.toUpperCase());
      }
      connection.end();
    });
    const port = await forwarder(toNode(node));
    const client = net.createConnection({ host: "127.0.0.1", port });
    client.write("GET /ws HTTP/1.1\r\nHost: localhost:3000\r\nUpgrade: websocket\r\n\r\n");
    expect(await readHead(client)).toMatch(/^HTTP\/1\.1 101/);
    for (const frame of ["hello", "again"]) {
      client.write(frame);
      expect(await readExact(client, 5)).toBe(frame.toUpperCase());
    }
    expect(await readAll(client)).toBe("");
  });

  it("answers every request with the message page when there is no node", async () => {
    const port = await forwarder(toMessage("No Mightling node found on this network.\nStart one on a GB10 <now>."));
    const client = net.createConnection({ host: "127.0.0.1", port });
    client.write("GET /app HTTP/1.1\r\nHost: localhost:3000\r\n\r\n");
    const response = await readAll(client);
    expect(response).toMatch(/^HTTP\/1\.1 503 /);
    expect(response).toContain("<p>No Mightling node found on this network.</p>");
    expect(response).toContain("<p>Start one on a GB10 &lt;now&gt;.</p>");
    expect(response).toContain('http-equiv="refresh"');
  });

  it("turns a node that does not answer into a page, not a reset", async () => {
    const port = await forwarder(toNode("127.0.0.1:9"));
    const client = net.createConnection({ host: "127.0.0.1", port });
    client.write("GET /app HTTP/1.1\r\nHost: localhost:3000\r\n\r\n");
    expect(await readAll(client)).toContain("127.0.0.1:9 is not answering");
  });

  it("escapes the message", () => {
    expect(messagePage("a <b> & c")).toContain("<p>a &lt;b&gt; &amp; c</p>");
  });
});
