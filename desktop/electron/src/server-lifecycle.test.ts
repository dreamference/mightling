// The app-server's life in the app (DESKTOP_ELECTRON §13): started once, ended by a crash, a failed
// start or Stop, and started again. `spawn` is stood in for, so each child is driven by the test: what
// it writes, when it ends, and what a child already replaced still writes after.
import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

class FakeChild extends EventEmitter {
  readonly stdin = new PassThrough();
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  readonly kill = vi.fn(() => true);
  written: unknown[] = [];
  constructor(readonly pid: number | undefined) {
    super();
    this.stdin.on("data", (chunk: Buffer) => {
      for (const line of chunk.toString().split("\n").filter(Boolean)) this.written.push(JSON.parse(line));
    });
  }
  /** One line on the protocol channel. */
  say(message: unknown): void {
    this.stdout.write(`${JSON.stringify(message)}\n`);
  }
}

const children: FakeChild[] = [];
const next: { failing: boolean } = { failing: false };

vi.mock("node:child_process", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:child_process")>();
  return {
    ...actual,
    spawn: vi.fn(() => {
      const child = new FakeChild(next.failing ? undefined : 4000 + children.length);
      children.push(child);
      if (next.failing) {
        next.failing = false;
        // As Node does for a missing binary: `error`, no `exit`.
        setImmediate(() => child.emit("error", Object.assign(new Error("spawn ling ENOENT"), { code: "ENOENT" })));
      }
      return child;
    }),
  };
});

import type { ForView } from "./api";
import { AppServer } from "./server";

/** Lets the streams' and readline's queued callbacks run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

const APPROVAL = { id: 7, method: "item/commandExecution/requestApproval", params: { threadId: "t1", turnId: "u1", itemId: "i1" } };

describe("the app-server's lifecycle", () => {
  let dir: string;
  let delivered: ForView[];
  let busy: boolean[];
  let server: AppServer;
  const saved = { home: process.env.CODEX_HOME, bin: process.env.MIGHTLING_BIN };

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "server-lifecycle-"));
    const ling = path.join(dir, "ling");
    fs.writeFileSync(ling, "");
    process.env.CODEX_HOME = dir;
    process.env.MIGHTLING_BIN = ling;
    children.length = 0;
    delivered = [];
    busy = [];
    server = new AppServer(null, { deliver: (message) => delivered.push(message), busyChanged: (value) => busy.push(value) });
  });

  afterEach(() => {
    process.env.CODEX_HOME = saved.home;
    process.env.MIGHTLING_BIN = saved.bin;
    if (saved.home === undefined) delete process.env.CODEX_HOME;
    if (saved.bin === undefined) delete process.env.MIGHTLING_BIN;
    fs.rmSync(dir, { recursive: true, force: true });
  });

  const on = (channel: string) => delivered.filter((message) => message.channel === channel).map((message) => message.payload);

  it("starts one child however often it is asked", () => {
    expect(server.start().started).toBe(true);
    expect(server.start().started).toBe(false);
    expect(children).toHaveLength(1);
  });

  it("after a crash forgets the dead server's requests and its busy turn, and starts a fresh one", async () => {
    server.start();
    const first = children[0];
    first.stderr.write("a warning\n");
    first.say({ method: "turn/started", params: { threadId: "t1", turn: { id: "u1" } } });
    first.say(APPROVAL);
    await settle();
    expect(on("work://stderr")).toContain("a warning");
    expect(on("work://message")).toContainEqual(APPROVAL);
    expect(busy).toEqual([true]);
    expect(fs.existsSync(path.join(dir, "night", "busy", String(first.pid)))).toBe(true);

    first.emit("exit", 101);
    expect(server.running).toBe(false);
    expect(on("work://exit")).toEqual([101]);
    expect(busy).toEqual([true, false]);
    expect(fs.existsSync(path.join(dir, "night", "busy", String(first.pid)))).toBe(false);
    await expect(server.send({ id: 7, result: { decision: "accept" } })).rejects.toThrow("not running");

    expect(server.start().started).toBe(true);
    // The new server never asked request 7: an approval for the dead one's is refused, not forwarded.
    await expect(server.send({ id: 7, result: { decision: "accept" } })).rejects.toThrow("no server request");
    await settle();
    expect(children[1].written).toEqual([]);
  });

  it("says why a start failed, and can start again", async () => {
    next.failing = true;
    expect(server.start().started).toBe(true);
    await settle();
    expect(server.running).toBe(false);
    expect(String(on("work://stderr")[0])).toMatch(/could not start .*ling app-server: spawn ling ENOENT/);
    expect(on("work://exit")).toEqual([null]);
    expect(server.start().started).toBe(true);
    expect(server.running).toBe(true);
  });

  it("on Stop kills the child once, and what it still writes after a restart reaches no window", async () => {
    server.start();
    const first = children[0];
    server.stop();
    expect(first.kill).toHaveBeenCalledWith("SIGKILL");
    expect(on("work://exit")).toEqual([null]);
    server.stop();
    expect(on("work://exit")).toEqual([null]);

    server.start();
    const second = children[1];
    // Lines the killed child had written before it died, read after the new one started, and its
    // late `error`/`exit`: none of them is the running server's.
    first.say(APPROVAL);
    first.stderr.write("the old server's last words\n");
    await settle();
    first.emit("error", new Error("kill EPERM"));
    first.emit("exit", null);
    expect(server.running).toBe(true);
    expect(on("work://message")).not.toContainEqual(APPROVAL);
    expect(on("work://stderr")).not.toContain("the old server's last words");
    expect(on("work://exit")).toEqual([null]);
    await expect(server.send({ id: 7, result: { decision: "accept" } })).rejects.toThrow("no server request");

    // The running one is unaffected: its own request is answered.
    second.say(APPROVAL);
    await settle();
    await server.send({ id: 7, result: { decision: "accept" } });
    await settle();
    expect(second.written).toEqual([{ id: 7, result: { decision: "accept" } }]);
  });
});
