// The app-server's process as AppServer runs it, against a stand-in `ling` (test-ling.ts).

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { AppServer } from "./server";
import { nodeScript } from "./test-ling";

describe("the app-server's process", () => {
  const saved = { CODEX_HOME: process.env.CODEX_HOME, MIGHTLING_BIN: process.env.MIGHTLING_BIN };
  afterEach(() => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });

  // A stopped server's output still in the pipes is not this app's any more: a restarted one would get the
  // old one's requests and notifications, and the window its lines.
  it.skipIf(process.platform === "win32")("delivers nothing from a server once it is stopped", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-server-stopped-"));
    process.env.CODEX_HOME = path.join(dir, "home");
    // Keeps both pipes full until it is killed.
    process.env.MIGHTLING_BIN = nodeScript(dir, "ling", [
      'const line = JSON.stringify({ method: "thread/name/updated", params: { threadId: "old", threadName: "x".repeat(200) } }) + "\\n";',
      'const flood = (stream, text) => { while (stream.write(text.repeat(50))); stream.once("drain", () => flood(stream, text)); };',
      'flood(process.stdout, line); flood(process.stderr, "old server line\\n");',
    ].join("\n") + "\n");
    let stopped = false;
    const early: string[] = [];
    const late: string[] = [];
    const server = new AppServer(null, { deliver: (event) => (stopped ? late : early).push(event.channel), busyChanged: () => {} });
    try {
      server.start();
      await new Promise((resolve) => setTimeout(resolve, 300));
      server.stop();
      stopped = true;
      await new Promise((resolve) => setTimeout(resolve, 300));
      expect(early).toContain("work://message");
      expect(late.filter((channel) => channel === "work://stderr" || channel === "work://message")).toEqual([]);
    } finally {
      server.stop();
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it.skipIf(process.platform === "win32")("closing its input throws nothing in this process", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-server-closed-"));
    process.env.CODEX_HOME = path.join(dir, "home");
    // Closes its stdin and lives on a while, as a server going down does.
    process.env.MIGHTLING_BIN = nodeScript(dir, "ling", 'require("fs").closeSync(0); setTimeout(() => {}, 3000);\n');
    const server = new AppServer(null, { deliver: () => {}, busyChanged: () => {} });
    const uncaught: unknown[] = [];
    const record = (error: unknown) => uncaught.push(error);
    process.on("uncaughtException", record);
    try {
      server.start();
      await new Promise((resolve) => setTimeout(resolve, 500));
      await server.send({ id: 1, method: "initialize", params: { clientInfo: { name: "test", version: "0" } } }).catch(() => undefined);
      await new Promise((resolve) => setTimeout(resolve, 200));
      expect(uncaught).toEqual([]);
    } finally {
      process.off("uncaughtException", record);
      server.stop();
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
