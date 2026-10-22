import { describe, expect, it } from "vitest";

import os from "node:os";

import { airgappedOn, parseStatus, type StatusRun } from "./airgapped";

describe("the air-gap status line", () => {
  it("is read from ling airgapped's first line", () => {
    expect(parseStatus("Airgapped: on (this session)\n  off  …\n")).toEqual({ level: "on", source: "this session" });
    expect(parseStatus("Airgapped: off (default)")).toEqual({ level: "off", source: "default" });
    expect(parseStatus("Usage: ling airgapped [default <off|on>]")).toBeNull();
  });
});

describe("the level a binding refusal reads", () => {
  const answering = (levels: Record<string, "off" | "on" | null>): { run: StatusRun; asked: [string[], string | null][] } => {
    const asked: [string[], string | null][] = [];
    const run: StatusRun = async (_ling, args, cwd) => {
      asked.push([args, cwd]);
      const level = levels[cwd ?? "here"];
      return level ? { level, source: "test" } : null;
    };
    return { run, asked };
  };

  it("asks the launcher about the thread, here and in the thread's own folder, and the stricter wins", async () => {
    const folder = os.tmpdir();
    const off = answering({ here: "off", [folder]: "off" });
    expect(await airgappedOn("ling", "t-1", folder, off.run)).toBe(false);
    expect(off.asked).toEqual([[["airgapped", "--thread", "t-1"], null], [["airgapped", "--thread", "t-1"], folder]]);
    expect(await airgappedOn("ling", "t-1", folder, answering({ here: "off", [folder]: "on" }).run)).toBe(true);
    expect(await airgappedOn("ling", "t-1", null, answering({ here: "on" }).run)).toBe(true);
  });

  it("fails closed: no answer is on", async () => {
    expect(await airgappedOn("ling", "t-1", null, answering({ here: null }).run)).toBe(true);
  });

  it("asks only here when the thread's folder is unknown or gone, and never passes an odd id", async () => {
    const gone = answering({ here: "off" });
    expect(await airgappedOn("ling", "../x", "/no/such/folder", gone.run)).toBe(false);
    expect(gone.asked).toEqual([[["airgapped"], null]]);
  });
});
