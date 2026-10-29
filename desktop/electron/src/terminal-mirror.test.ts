// The terminal tabs every window sees, and their recent output (DESKTOP_ELECTRON §13).
import { describe, expect, it } from "vitest";

import { BACKLOG_BYTES, MAX_TERMINALS, TerminalMirror, sessionOf } from "./terminal-mirror";

const tab = (processId: string, extra: Record<string, unknown> = {}) => ({ processId, threadId: "t1", cwd: "/w", title: "Terminal", exitCode: null, error: null, ...extra });
const output = (processId: string, text: string) => ({ method: "command/exec/outputDelta", params: { processId, deltaBase64: Buffer.from(text).toString("base64") } });

describe("the terminal mirror", () => {
  it("lists what windows publish, updates in place, and forgets what any window closes", () => {
    const mirror = new TerminalMirror();
    expect(mirror.publish(tab("term-1"))).toBe(true);
    expect(mirror.publish(tab("term-1"))).toBe(false);
    expect(mirror.publish(tab("term-2", { threadId: null }))).toBe(true);
    expect(mirror.publish(tab("term-1", { exitCode: 0 }))).toBe(true);
    expect(mirror.list().map((entry) => [entry.processId, entry.exitCode])).toEqual([["term-2", null], ["term-1", 0]]);
    expect(mirror.forget("term-2")).toBe(true);
    expect(mirror.forget("term-2")).toBe(false);
    expect(mirror.list().map((entry) => entry.processId)).toEqual(["term-1"]);
  });

  it("refuses a tab that is not one", () => {
    expect(() => sessionOf(null)).toThrow();
    expect(() => sessionOf(tab("../etc"))).toThrow();
    expect(() => sessionOf(tab("term-1", { threadId: 3 }))).toThrow();
    expect(() => sessionOf(tab("term-1", { cwd: undefined }))).toThrow();
    expect(sessionOf({ ...tab("term-1"), extra: "dropped" })).toEqual(tab("term-1"));
  });

  it("keeps each tab's recent output, bounded, and drops it with the tab", () => {
    const mirror = new TerminalMirror();
    mirror.publish(tab("term-1"));
    mirror.observe(output("term-1", "hello "));
    mirror.observe(output("term-1", "world"));
    mirror.observe({ method: "turn/started", params: { processId: "term-1" } });
    expect(Buffer.from(mirror.backlog("term-1"), "base64").toString()).toBe("hello world");
    mirror.observe(output("term-1", "x".repeat(BACKLOG_BYTES)));
    const kept = Buffer.from(mirror.backlog("term-1"), "base64");
    expect(kept.length).toBe(BACKLOG_BYTES);
    expect(BACKLOG_BYTES).toBe(256 * 1024);
    expect(kept.toString().endsWith("x")).toBe(true);
    mirror.forget("term-1");
    expect(mirror.backlog("term-1")).toBe("");
    expect(mirror.backlog(5)).toBe("");
  });

  it("holds a bounded number of tabs", () => {
    const mirror = new TerminalMirror();
    for (let index = 0; index < MAX_TERMINALS + 5; index++) {
      mirror.publish(tab(`term-${index}`));
      mirror.observe(output(`term-${index}`, "a"));
    }
    expect(mirror.list()).toHaveLength(MAX_TERMINALS);
    expect(mirror.list()[0].processId).toBe("term-5");
    expect(mirror.backlog("term-0")).toBe("");
    mirror.clear();
    expect(mirror.list()).toEqual([]);
  });
});
