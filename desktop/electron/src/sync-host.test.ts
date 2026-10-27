// The sync host (sync-host.ts, specs/DREAMFERENCE_MIGHTLING_SYNC.md §18.2) against the real
// `AppServer` with a scripted `ling` playing the app-server, and a stand-in hub on loopback. It
// checks what reaches the hub (nothing without a pairing or in the audit's session; only followed
// notifications and thread-scoped requests of threads that leave the device), that commands go
// through the policy as a window's would, that the host's own answers never reach a window, and
// the back-off. Everything lives in a scratch folder; nothing connects anywhere but 127.0.0.1.

import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { ForView } from "./api";
import { AppServer } from "./server";
import { EVENT_CAP, HOST_ID_PREFIX, SyncHost, hubBase, privateIp, readConfig, type HostOptions } from "./sync-host";
import { nodeScript } from "./test-ling";

type Json = Record<string, any>;

const scratches: string[] = [];
const cleanups: (() => void | Promise<void>)[] = [];
const savedEnv = { ...process.env };
const scratch = (tag: string) => {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), `ling-app-sync-${tag}-`)));
  scratches.push(dir);
  return dir;
};
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup();
  for (const dir of scratches.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
  for (const key of Object.keys(process.env)) if (!(key in savedEnv)) delete process.env[key];
  Object.assign(process.env, savedEnv);
});

const until = async (check: () => boolean, ms = 5000) => {
  const deadline = Date.now() + ms;
  while (!check()) {
    if (Date.now() > deadline) throw new Error("timed out");
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
};
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

interface Posted {
  url: string;
  headers: http.IncomingHttpHeaders;
  body: Json;
  at: number;
}

/** A stand-in hub: records every post, hands out the commands pushed to it, holds an idle post as the hub does. */
class Hub {
  readonly posts: Posted[] = [];
  private commands: unknown[] = [];
  private held: http.ServerResponse[] = [];
  failures = 0;
  base = "";
  private readonly server = http.createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => (body += chunk));
    request.on("end", () => {
      const url = new URL(request.url ?? "/", "http://127.0.0.1");
      this.posts.push({ url: request.url ?? "", headers: request.headers, body: body ? JSON.parse(body) : {}, at: Date.now() });
      if (this.failures > 0) {
        this.failures--;
        response.writeHead(500);
        response.end();
        return;
      }
      const wait = Number(url.searchParams.get("wait") ?? "0");
      if (this.commands.length > 0 || wait === 0) return this.answer(response);
      this.held.push(response);
      setTimeout(() => {
        const at = this.held.indexOf(response);
        if (at >= 0) {
          this.held.splice(at, 1);
          this.answer(response);
        }
      }, wait * 1000);
    });
  });

  async listen(): Promise<void> {
    await new Promise<void>((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    const address = this.server.address();
    if (address === null || typeof address === "string") throw new Error("no port");
    this.base = `http://127.0.0.1:${address.port}`;
  }

  push(command: Json): void {
    this.commands.push(command);
    const held = this.held.shift();
    if (held) this.answer(held);
  }

  private answer(response: http.ServerResponse): void {
    const commands = this.commands.splice(0);
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ commands }));
  }

  get events(): Json[] {
    return this.posts.flatMap((post) => post.body.events ?? []);
  }

  ack(id: number): Json | undefined {
    return this.posts.flatMap((post) => post.body.acks ?? []).find((ack: Json) => ack.id === id);
  }

  async close(): Promise<void> {
    for (const response of this.held.splice(0)) response.end();
    this.server.closeAllConnections();
    await new Promise((resolve) => this.server.close(resolve));
  }
}

const THREADS = [
  { id: "t-1", cwd: "/home/user/proj" },
  { id: "t-2", cwd: "/home/user/proj" },
  { id: "t-3", cwd: "/home/user/secret/repo" },
  { id: "t-4", cwd: "/home/user/other" },
];

/**
 * A stand-in `ling` app-server: answers the requests the host makes (a turn started is announced
 * with `turn/started`, an interrupt with `turn/completed`), records every line it reads, and writes
 * whatever the test appends to `emit.jsonl`, as the server's own messages.
 */
function scriptedLing(dir: string, askFolder: string): string {
  const threads = [...THREADS, { id: "t-ask", cwd: path.join(askFolder, "q-0123456789abcdef") }];
  return nodeScript(dir, "ling", `const fs = require("fs");
const args = process.argv.slice(2);
if (args[0] === "airgapped") {
  process.stdout.write("Airgapped: off (stub)\\n");
  process.exit(0);
}
if (args[0] === "prompt") {
  process.stdout.write("COMPOSED");
  process.exit(0);
}
const log = ${JSON.stringify(path.join(dir, "received.jsonl"))};
const emit = ${JSON.stringify(path.join(dir, "emit.jsonl"))};
const out = (message) => process.stdout.write(JSON.stringify(message) + "\\n");
let offset = 0;
setInterval(() => {
  let text;
  try { text = fs.readFileSync(emit, "utf8"); } catch { return; }
  const fresh = text.slice(offset);
  const end = fresh.lastIndexOf("\\n");
  if (end < 0) return;
  offset += end + 1;
  for (const line of fresh.slice(0, end).split("\\n")) if (line) process.stdout.write(line + "\\n");
}, 10);
let threads = 0;
let turns = 0;
let buffer = "";
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let newline;
  while ((newline = buffer.indexOf("\\n")) >= 0) {
    const line = buffer.slice(0, newline);
    buffer = buffer.slice(newline + 1);
    fs.appendFileSync(log, line + "\\n");
    handle(JSON.parse(line));
  }
});
function handle(message) {
  if (!("id" in message) || !message.method) return;
  const params = message.params || {};
  switch (message.method) {
    case "thread/list":
      return out({ id: message.id, result: { data: ${JSON.stringify(threads)}, nextCursor: null, backwardsCursor: null } });
    case "thread/loaded/list":
      return out({ id: message.id, result: { data: ["t-1", "t-3"], nextCursor: null } });
    case "model/list":
      return out({ id: message.id, result: { data: [{ id: "m-1", model: "m-1", hidden: false }, { id: "m-hidden", model: "m-hidden", hidden: true }], nextCursor: null } });
    case "thread/resume":
      return out({ id: message.id, result: { thread: { id: params.threadId, cwd: "/home/user/proj" } } });
    case "thread/start":
      return out({ id: message.id, result: { thread: { id: "t-new-" + ++threads, cwd: params.cwd } } });
    case "turn/start": {
      const turn = "turn-" + ++turns;
      out({ id: message.id, result: { turn: { id: turn } } });
      return out({ method: "turn/started", params: { threadId: params.threadId, turn: { id: turn } } });
    }
    case "turn/interrupt":
      out({ id: message.id, result: {} });
      return out({ method: "turn/completed", params: { threadId: params.threadId, turn: { id: params.turnId, status: "interrupted" } } });
    default:
      return out({ id: message.id, result: {} });
  }
}
`);
}

interface Rig {
  dir: string;
  home: string;
  hub: Hub;
  server: AppServer;
  host: SyncHost;
  delivered: ForView[];
  told: { text: string; thread: string | null }[];
  received: () => Json[];
  emit: (message: Json) => void;
}

/** A paired device (unless `paired` is false), its app-server past the handshake, a hub, and a host on both. */
async function rig(tag: string, options: { paired?: boolean; status?: boolean; exclude?: string[]; hostOptions?: HostOptions } = {}): Promise<Rig> {
  const dir = scratch(tag);
  const home = path.join(dir, "codex-home");
  fs.mkdirSync(path.join(home, "sync"), { recursive: true });
  const hub = new Hub();
  await hub.listen();
  cleanups.push(() => hub.close());
  if (options.paired !== false) {
    fs.writeFileSync(path.join(home, "sync", "device.toml"), `device = "dev-laptop"\nname = "laptop"\nsecret = "s3cret"\nport = 3100\nlocal = false\n`);
  }
  if (options.status !== false) {
    fs.writeFileSync(path.join(home, "sync", "agent-status.json"), JSON.stringify({ hub: hub.base, device: "dev-laptop", pending: 0 }));
  }
  if (options.exclude) {
    fs.mkdirSync(path.join(dir, ".config", "dreamference"), { recursive: true });
    fs.writeFileSync(path.join(dir, ".config", "dreamference", "config.toml"), `[sync]\nexclude = ${JSON.stringify(options.exclude)}\n`);
  }
  process.env.HOME = dir;
  process.env.CODEX_HOME = home;
  process.env.MIGHTLING_BIN = scriptedLing(dir, path.join(home, "ask"));
  const delivered: ForView[] = [];
  const server = new AppServer(null, { deliver: (message) => delivered.push(message), busyChanged: () => {} });
  server.start();
  cleanups.push(() => server.stop());
  // A window's handshake: the host's own requests wait for it.
  await server.send({ id: 1, method: "initialize", params: { clientInfo: { name: "test", version: "0" } } });
  await server.send({ method: "initialized" });
  const told: { text: string; thread: string | null }[] = [];
  const host = new SyncHost(server, {
    env: { ...process.env }, told: (text, thread) => told.push({ text, thread }),
    waitSeconds: 1, batchMs: 20, refreshMs: 200, configMs: 50, backoffMs: 50, backoffMaxMs: 200, idleMs: 20, requestMs: 3000,
    ...options.hostOptions,
  });
  cleanups.push(() => host.stop());
  const received = () => {
    try {
      return fs.readFileSync(path.join(dir, "received.jsonl"), "utf8").trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
    } catch {
      return [];
    }
  };
  const emit = (message: Json) => fs.appendFileSync(path.join(dir, "emit.jsonl"), `${JSON.stringify(message)}\n`);
  return { dir, home, hub, server, host, delivered, told, received, emit };
}

/** The host's first post carries holding, models and projects once it has asked the server. */
const offered = (hub: Hub) => hub.posts.some((post) => (post.body.models ?? []).length > 0);
const methods = (rig: Rig) => rig.received().map((message) => message.method ?? "answer");

describe("what the host reads", () => {
  it("needs a pairing and a hub the agent found, and never runs in the audit's session", () => {
    const dir = scratch("config");
    const home = path.join(dir, "codex-home");
    const env = { HOME: dir, CODEX_HOME: home };
    expect(readConfig(env)).toBeNull();
    fs.mkdirSync(path.join(home, "sync"), { recursive: true });
    fs.writeFileSync(path.join(home, "sync", "device.toml"), `# a comment\ndevice = "dev-1"\nsecret = "abc"\nport = 3100\nlocal = true\n`);
    expect(readConfig(env)).toBeNull();
    fs.writeFileSync(path.join(home, "sync", "agent-status.json"), JSON.stringify({ hub: "" }));
    expect(readConfig(env)).toBeNull();
    fs.writeFileSync(path.join(home, "sync", "agent-status.json"), JSON.stringify({ hub: "http://192.168.50.105:3100" }));
    expect(readConfig(env)).toEqual({ device: "dev-1", secret: "abc", hub: "http://192.168.50.105:3100", exclude: [] });
    expect(readConfig({ ...env, MIGHTLING_APP_AUDIT: "45" })).toBeNull();
    fs.mkdirSync(path.join(dir, ".config", "dreamference"), { recursive: true });
    fs.writeFileSync(path.join(dir, ".config", "dreamference", "config.toml"), `[sync]\nexclude = ["~/private", "/home/user/work", "relative"]\n`);
    expect(readConfig(env)?.exclude).toEqual([path.join(dir, "private"), "/home/user/work"]);
  });

  it("takes a hub only on the user's own network, over plain http", () => {
    expect(hubBase("http://192.168.50.105:3100")).toBe("http://192.168.50.105:3100");
    expect(hubBase("http://127.0.0.1:3100/")).toBe("http://127.0.0.1:3100");
    expect(hubBase("http://[fd12::1]:3100")).toBe("http://[fd12::1]:3100");
    expect(hubBase("http://node.local:3100")).toBe("http://node.local:3100");
    expect(hubBase("http://203.0.113.1:3100")).toBeNull();
    expect(hubBase("https://192.168.50.105:3100")).toBeNull();
    expect(hubBase("file:///etc/passwd")).toBeNull();
    expect(hubBase("")).toBeNull();
    for (const ip of ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.50.105", "169.254.1.1", "100.64.0.1", "::1", "fd12::1", "fe80::1", "::ffff:192.168.50.2"]) expect(privateIp(ip), ip).toBe(true);
    for (const ip of ["203.0.113.1", "172.32.0.1", "100.128.0.1", "2001:db8::1", "::ffff:203.0.113.1"]) expect(privateIp(ip), ip).toBe(false);
  });
});

describe("the sync host", () => {
  it("sends nothing without a pairing, without a hub, or in the audit's session", async () => {
    const unpaired = await rig("unpaired", { paired: false });
    unpaired.host.start();
    const nohub = await rig("nohub", { status: false });
    nohub.host.start();
    await pause(400);
    expect(unpaired.hub.posts).toHaveLength(0);
    expect(nohub.hub.posts).toHaveLength(0);
    // Not even the server is asked anything.
    expect(methods(unpaired)).toEqual(["initialize", "initialized"]);
    expect(methods(nohub)).toEqual(["initialize", "initialized"]);

    const audit = await rig("audit", { hostOptions: { env: { ...process.env, MIGHTLING_APP_AUDIT: "45" } } });
    audit.host.start();
    expect(audit.host.active).toBe(false);
    await pause(300);
    expect(audit.hub.posts).toHaveLength(0);
  });

  it("posts holding, models and projects, and only followed events of threads that leave the device", async () => {
    const r = await rig("events", { exclude: ["/home/user/secret"] });
    r.host.start();
    await until(() => offered(r.hub));
    const first = r.hub.posts.find((post) => (post.body.models ?? []).length > 0)!;
    expect(first.url).toBe("/api/sync/host?wait=1");
    expect(first.headers.authorization).toBe("Bearer s3cret");
    expect(first.headers.origin).toBe(r.hub.base);
    expect(first.body).toMatchObject({ host: r.host.hostId, kind: "desktop", models: ["m-1"], events: [], acks: [] });
    // t-3 works under an excluded folder; the Ask root is no project.
    expect(first.body.holding).toEqual(["t-1"]);
    expect(first.body.projects).toEqual(["/home/user/proj", "/home/user/other"]);

    r.emit({ method: "turn/started", params: { threadId: "t-1", turn: { id: "turn-9" } } });
    r.emit({ method: "item/agentMessage/delta", params: { threadId: "t-1", itemId: "i", delta: "hi" } });
    r.emit({ method: "thread/tokenUsage/updated", params: { threadId: "t-1" } });
    r.emit({ method: "account/updated", params: {} });
    r.emit({ method: "item/agentMessage/delta", params: { threadId: "t-3", itemId: "i", delta: "secret" } });
    r.emit({ id: 7, method: "item/commandExecution/requestApproval", params: { threadId: "t-1", turnId: "turn-9", itemId: "c" } });
    r.emit({ id: 9, method: "account/chatgptAuthTokens/refresh", params: {} });
    r.emit({ method: "item/completed", params: { threadId: "t-1", item: { type: "commandExecution", aggregatedOutput: "x".repeat(EVENT_CAP + 10) } } });
    r.emit({ method: "turn/completed", params: { threadId: "t-1", turn: { id: "turn-9" } } });
    await until(() => r.hub.events.some((event) => event.message.method === "turn/completed"));
    const events = r.hub.events;
    expect(events.every((event) => event.thread === "t-1")).toBe(true);
    expect(events.map((event) => event.message.method)).toEqual(["turn/started", "item/agentMessage/delta", "item/commandExecution/requestApproval", "item/completed", "turn/completed"]);
    expect(events[2].message.id).toBe(7);
    expect(events[3].message).toEqual({ method: "item/completed", params: { threadId: "t-1", truncated: expect.any(Number) } });
    const eventPost = r.hub.posts.find((post) => (post.body.events ?? []).length > 0)!;
    expect(eventPost.url).toBe("/api/sync/host?wait=0");
    // Batched: the burst went up in one or two posts, not one each.
    expect(r.hub.posts.filter((post) => (post.body.events ?? []).length > 0).length).toBeLessThanOrEqual(2);

    // The windows got the server's messages, and none of the host's own answers.
    const toWindows = r.delivered.filter((message) => message.channel === "work://message").map((message) => message.payload as Json);
    expect(toWindows.some((message) => message.method === "item/agentMessage/delta")).toBe(true);
    expect(toWindows.some((message) => typeof message.id === "string" && message.id.startsWith(HOST_ID_PREFIX))).toBe(false);
    // The host's requests did reach the server, under its own ids.
    const own = r.received().filter((message) => typeof message.id === "string" && message.id.startsWith(HOST_ID_PREFIX));
    expect(new Set(own.map((message) => message.method))).toEqual(new Set(["thread/list", "thread/loaded/list", "model/list"]));
    // A window's own request is still answered to it, beside the host's.
    await r.server.send({ id: 2, method: "thread/list", params: { limit: 1 } });
    await until(() => r.delivered.some((message) => message.channel === "work://message" && (message.payload as Json).id === 2 && "result" in (message.payload as Json)));
  });

  it("answers an approval through the policy, and acks a refusal as failed", async () => {
    const r = await rig("approve");
    r.host.start();
    await until(() => offered(r.hub));
    r.emit({ id: 7, method: "item/commandExecution/requestApproval", params: { threadId: "t-1", turnId: "turn-9", itemId: "c" } });
    r.emit({ id: 8, method: "item/tool/requestUserInput", params: { threadId: "t-1", turnId: "turn-9", itemId: "q" } });
    await until(() => r.hub.events.some((event) => event.message.id === 8));

    r.hub.push({ id: 1, kind: "approve", thread: "t-1", request: 7, decision: "accept", by: "the phone" });
    await until(() => r.hub.ack(1) !== undefined);
    expect(r.hub.ack(1)).toMatchObject({ id: 1, ok: true });
    expect(r.received()).toContainEqual({ id: 7, result: { decision: "accept" } });
    expect(r.told).toContainEqual({ text: "Approved from the phone", thread: "t-1" });

    // Answered already: the policy has no request 7 waiting.
    r.hub.push({ id: 2, kind: "approve", thread: "t-1", request: 7, decision: "accept", by: "the phone" });
    await until(() => r.hub.ack(2) !== undefined);
    expect(r.hub.ack(2)).toMatchObject({ id: 2, ok: false, error: "no server request 7 is waiting for an answer" });
    // Never asked at all.
    r.hub.push({ id: 3, kind: "approve", thread: "t-1", request: 41, decision: "decline", by: "the phone" });
    await until(() => r.hub.ack(3) !== undefined);
    expect(r.hub.ack(3)).toMatchObject({ ok: false, error: "no server request 41 is waiting for an answer" });
    // A question for the user is answered on the device.
    r.hub.push({ id: 4, kind: "approve", thread: "t-1", request: 8, decision: "accept", by: "the phone" });
    await until(() => r.hub.ack(4) !== undefined);
    expect(r.hub.ack(4)).toMatchObject({ ok: false, error: "item/tool/requestUserInput is answered on this device" });
    expect(r.received().filter((message) => message.id === 8)).toHaveLength(0);
    expect(r.received().filter((message) => message.id === 7 && "result" in message)).toHaveLength(1);
  });

  it("resumes a thread it does not hold, starts a turn, steers a running one and stops it", async () => {
    const r = await rig("send");
    r.host.start();
    await until(() => offered(r.hub));

    r.hub.push({ id: 1, kind: "send", thread: "t-2", text: "carry on", model: "m-1", by: "the phone" });
    await until(() => r.hub.ack(1) !== undefined);
    expect(r.hub.ack(1)).toEqual({ id: 1, ok: true, thread: "t-2" });
    const own = () => r.received().filter((message) => typeof message.id === "string" && message.id.startsWith(HOST_ID_PREFIX) && !["thread/list", "thread/loaded/list", "model/list"].includes(message.method));
    expect(own().map((message) => message.method)).toEqual(["thread/resume", "turn/start"]);
    expect(own()[0].params.threadId).toBe("t-2");
    expect(own()[1].params).toEqual({ threadId: "t-2", input: [{ type: "text", text: "carry on", text_elements: [] }], model: "m-1" });
    expect(r.told).toContainEqual({ text: "Message from the phone sent", thread: "t-2" });

    // The turn runs now (turn/started): a second message steers it, with no resume.
    await until(() => r.hub.events.some((event) => event.message.method === "turn/started" && event.thread === "t-2"));
    r.hub.push({ id: 2, kind: "send", thread: "t-2", text: "and this", by: "the phone" });
    await until(() => r.hub.ack(2) !== undefined);
    expect(r.hub.ack(2)).toMatchObject({ ok: true });
    expect(own().map((message) => message.method)).toEqual(["thread/resume", "turn/start", "turn/steer"]);
    expect(own()[2].params).toEqual({ threadId: "t-2", input: [{ type: "text", text: "and this", text_elements: [] }], expectedTurnId: "turn-1" });

    r.hub.push({ id: 3, kind: "stop", thread: "t-2", by: "the phone" });
    await until(() => r.hub.ack(3) !== undefined);
    expect(r.hub.ack(3)).toMatchObject({ ok: true, thread: "t-2" });
    expect(own()[3]).toMatchObject({ method: "turn/interrupt", params: { threadId: "t-2", turnId: "turn-1" } });
    expect(r.told).toContainEqual({ text: "Stopped from the phone", thread: "t-2" });
    await until(() => r.hub.events.some((event) => event.message.method === "turn/completed" && event.thread === "t-2"));
    r.hub.push({ id: 4, kind: "stop", thread: "t-2", by: "the phone" });
    await until(() => r.hub.ack(4) !== undefined);
    expect(r.hub.ack(4)).toMatchObject({ ok: false, error: "no turn is running in this thread" });

    // A thread it holds is not resumed again.
    r.hub.push({ id: 5, kind: "send", thread: "t-1", text: "hello", by: "the phone" });
    await until(() => r.hub.ack(5) !== undefined);
    expect(own().slice(4).map((message) => message.method)).toEqual(["turn/start"]);
  });

  it("starts a thread only in a folder it offered, and acks the new thread", async () => {
    const r = await rig("start");
    r.host.start();
    await until(() => offered(r.hub));
    r.hub.push({ id: 1, kind: "start", cwd: "/home/user/proj", text: "hello there", by: "the phone" });
    await until(() => r.hub.ack(1) !== undefined);
    expect(r.hub.ack(1)).toEqual({ id: 1, ok: true, thread: "t-new-1" });
    const own = r.received().filter((message) => message.method === "thread/start" || message.method === "turn/start");
    expect(own[0].params.cwd).toBe("/home/user/proj");
    expect(own[1].params).toMatchObject({ threadId: "t-new-1", input: [{ type: "text", text: "hello there", text_elements: [] }] });
    expect(r.told).toContainEqual({ text: "New thread from the phone", thread: "t-new-1" });

    r.hub.push({ id: 2, kind: "start", cwd: "/etc", text: "hi", by: "the phone" });
    await until(() => r.hub.ack(2) !== undefined);
    expect(r.hub.ack(2)).toMatchObject({ ok: false, error: "a new thread starts in one of the folders this device offers" });
    expect(r.received().filter((message) => message.method === "thread/start")).toHaveLength(1);
  });

  it("backs off on errors, doubling to the cap, and resets on an answer", async () => {
    const r = await rig("backoff", { hostOptions: { backoffMs: 60, backoffMaxMs: 150 } });
    r.hub.failures = 4;
    r.host.start();
    await until(() => r.hub.posts.length >= 6, 8000);
    const gaps = r.hub.posts.slice(0, 5).map((post, index, posts) => (index === 0 ? 0 : post.at - posts[index - 1].at)).slice(1);
    // 60, 120, 150 (the cap), 150.
    expect(gaps[0]).toBeGreaterThanOrEqual(55);
    expect(gaps[1]).toBeGreaterThanOrEqual(115);
    expect(gaps[2]).toBeGreaterThanOrEqual(145);
    expect(gaps[3]).toBeGreaterThanOrEqual(145);
    expect(gaps[3]).toBeLessThan(400);
    expect(r.host.currentBackoff).toBe(0);
  });

  it("stops with the server: nothing more is posted", async () => {
    const r = await rig("stop");
    r.host.start();
    await until(() => offered(r.hub));
    r.host.stop();
    const count = r.hub.posts.length;
    r.emit({ method: "turn/started", params: { threadId: "t-1", turn: { id: "x" } } });
    await pause(300);
    expect(r.hub.posts.length).toBe(count);
  });
});
