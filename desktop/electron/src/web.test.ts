// The Ask window's server (web.ts) against a scripted `ling`: a running `ling web` is used as it
// is, a missing one is started and owned, and only a one-time link to loopback is ever loaded.
// No process is started and no port is touched.

import { EventEmitter } from "node:events";
import type { ChildProcess } from "node:child_process";
import { describe, expect, it } from "vitest";

import { WebServer, isLoginUrl, sameServer, type Ran } from "./web";

const LINK = "http://127.0.0.1:3100/login?code=0123456789abcdef0123456789abcdef";

class FakeChild extends EventEmitter {
  exitCode: number | null = null;
  killed: string[] = [];
  kill(signal: string) {
    this.killed.push(signal);
    this.exitCode = 0;
    this.emit("exit", 0);
    return true;
  }
}

/** A `ling` whose server answers once `up` is true. */
function scripted(up: { value: boolean }, link = LINK) {
  const ran: string[][] = [];
  const started: FakeChild[] = [];
  const run = async (args: string[]): Promise<Ran> => {
    ran.push(args);
    if (args.join(" ") === "web status") return { code: up.value ? 0 : 3, stdout: "", stderr: "" };
    if (args.join(" ") === "web open --print-url") return up.value ? { code: 0, stdout: `${link}\n`, stderr: "" } : { code: 1, stdout: "", stderr: "not running" };
    return { code: 2, stdout: "", stderr: `unexpected ${args.join(" ")}` };
  };
  const start = (args: string[]) => {
    ran.push(["start", ...args]);
    const child = new FakeChild();
    started.push(child);
    // The server comes up a moment after it is started.
    setTimeout(() => (up.value = true), 5);
    return child as unknown as ChildProcess;
  };
  return { ran, started, run, start };
}

describe("the Ask window's server", () => {
  it("uses a ling web that already answers, and never stops it", async () => {
    const ling = scripted({ value: true });
    const server = new WebServer(ling.run, ling.start, 1000, 1);
    await expect(server.loginUrl()).resolves.toBe(LINK);
    expect(ling.ran).toEqual([["web", "status"], ["web", "open", "--print-url"]]);
    expect(server.owned).toBe(false);
    server.stop();
    expect(ling.started).toEqual([]);
  });

  it("starts ling web serve when nothing answers, and stops what it started", async () => {
    const ling = scripted({ value: false });
    const server = new WebServer(ling.run, ling.start, 1000, 1);
    await expect(server.loginUrl()).resolves.toBe(LINK);
    expect(ling.ran).toContainEqual(["start", "web", "serve"]);
    expect(server.owned).toBe(true);
    server.stop();
    expect(ling.started[0].killed).toEqual(["SIGTERM"]);
    expect(server.owned).toBe(false);
  });

  it("says so when the server never comes up", async () => {
    const ling = scripted({ value: false });
    const server = new WebServer(ling.run, (args) => {
      ling.ran.push(["start", ...args]);
      const child = new FakeChild();
      setTimeout(() => child.emit("exit", 1), 2);
      return child as unknown as ChildProcess;
    }, 200, 1);
    await expect(server.loginUrl()).rejects.toThrow(/did not start/);
  });

  it("loads nothing but a one-time link to loopback", async () => {
    const ling = scripted({ value: true }, "http://evil.example/login?code=0123456789abcdef0123456789abcdef");
    await expect(new WebServer(ling.run, ling.start, 1000, 1).loginUrl()).rejects.toThrow();
    expect(isLoginUrl(LINK)).toBe(true);
    expect(isLoginUrl("http://127.0.0.1:3100/login?code=short")).toBe(false);
    expect(isLoginUrl("http://127.0.0.1:3100/?code=0123456789abcdef0123456789abcdef")).toBe(false);
    expect(isLoginUrl("https://127.0.0.1:3100/login?code=0123456789abcdef0123456789abcdef")).toBe(false);
    expect(sameServer("http://127.0.0.1:3100/#work", LINK)).toBe(true);
    expect(sameServer("http://127.0.0.1:3000/app", LINK)).toBe(false);
  });
});
