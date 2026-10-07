// The loopback forwarder that lets the Chat window keep loading `http://localhost:3000/app` on a
// machine that is not the node (specs/DREAMFERENCE_MIGHTLING_NODE.md §7).
//
// The web UI is served by the node. A window pointed at `http://<node address>:3000` would not be
// a secure context, so the page would have no `navigator.mediaDevices` and the microphone could
// not work; `http://localhost` is exempt. So the app binds a loopback port and passes every
// connection through to the node, byte for byte:
//
// * it forwards TCP, not HTTP, so a streamed answer and a WebSocket upgrade need no handling of
//   their own, and the `Host` header reaches the node as the browser wrote it (`localhost:3000`),
//   which is what keeps redirects and absolute links pointing back at the forwarder;
// * with no node to forward to it answers every request with one page that says so, in place of
//   the window's bare connection error.

import net from "node:net";

/** The port the window has always used: a web origin includes its port, so it must not change. */
export const PREFERRED_PORT = 3000;

/** Used when something else on this machine already holds the preferred port. */
export const FALLBACK_PORT = 33000;

const CONNECT_TIMEOUT_MS = 5000;

/** What the forwarder does with a connection. */
export type Upstream = { kind: "node"; target: string } | { kind: "message"; text: string };

export const toNode = (target: string): Upstream => ({ kind: "node", target });
export const toMessage = (text: string): Upstream => ({ kind: "message", text });

/** Binds the loopback port (the preferred one, else the fallback) and serves it for as long as the process lives. */
export async function start(upstream: Upstream): Promise<number> {
  const server = createServer(upstream);
  for (const port of [PREFERRED_PORT, FALLBACK_PORT]) {
    try {
      await listen(server, port);
      return port;
    } catch (error) {
      if (port === FALLBACK_PORT) throw error;
    }
  }
  throw new Error("no loopback port");
}

/** The server itself, for callers (and tests) that pick the port. */
export function createServer(upstream: Upstream): net.Server {
  return net.createServer((client) => {
    if (upstream.kind === "node") forward(client, upstream.target);
    else answer(client, upstream.text);
  });
}

function listen(server: net.Server, port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const failed = (error: Error) => {
      server.off("listening", ok);
      reject(error);
    };
    const ok = () => {
      server.off("error", failed);
      resolve();
    };
    server.once("error", failed);
    server.once("listening", ok);
    server.listen(port, "127.0.0.1");
  });
}

/** Connects to the node and copies bytes both ways until either side closes; a node that does not answer gets the client a page, not a reset. */
function forward(client: net.Socket, target: string): void {
  const [host, portText] = splitHostPort(target);
  const node = net.createConnection({ host, port: Number(portText) });
  node.setNoDelay(true);
  client.setNoDelay(true);
  const timer = setTimeout(() => node.destroy(new Error("timeout")), CONNECT_TIMEOUT_MS);
  node.once("connect", () => {
    clearTimeout(timer);
    // A half-close on either side ends that direction and lets the other finish.
    client.pipe(node, { end: true });
    node.pipe(client, { end: true });
  });
  node.once("error", () => {
    clearTimeout(timer);
    if (!client.destroyed) {
      answer(client, `The Mightling node at ${target} is not answering. Is its web UI running? On the node: ling-admin chat start`);
    }
  });
  client.once("error", () => node.destroy());
  client.once("close", () => node.destroy());
}

function splitHostPort(target: string): [string, string] {
  const at = target.lastIndexOf(":");
  const host = target.slice(0, at);
  return [host.startsWith("[") ? host.slice(1, -1) : host, target.slice(at + 1)];
}

/** Answers one request with the message page, after reading the request's head so the client is not reset while writing it. */
function answer(client: net.Socket, message: string): void {
  let head = "";
  const finish = () => {
    client.off("data", onData);
    const body = messagePage(message);
    client.end(
      `HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ${Buffer.byteLength(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n${body}`,
    );
  };
  const onData = (chunk: Buffer) => {
    head += chunk.toString("latin1");
    if (head.includes("\r\n\r\n") || head.length > 64 * 1024) finish();
  };
  client.on("data", onData);
  client.setTimeout(2000, finish);
  client.once("error", () => {});
}

/** The page shown in place of the web UI. It reloads itself, so the window recovers when the node comes back. */
export function messagePage(message: string): string {
  const paragraphs = message
    .split("\n")
    .map((line) => `<p>${escape(line)}</p>`)
    .join("");
  return (
    '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="10">' +
    "<title>Mightling</title><style>body{font-family:Roboto,system-ui,sans-serif;background:#fff;color:#111;" +
    "max-width:40em;margin:18vh auto;padding:0 1.5em;line-height:1.5}h1{font-size:1.3em}p{margin:.4em 0}</style>" +
    `</head><body><h1>Mightling</h1>${paragraphs}</body></html>`
  );
}

const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
