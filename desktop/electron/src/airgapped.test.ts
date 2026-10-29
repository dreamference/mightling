import { describe, expect, it } from "vitest";

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { airgapped, airgappedLevel, airgappedOn, countsAsOn, parseStatus, setDefault, type StatusRun } from "./airgapped";
import { nodeScript } from "./test-ling";

describe("the air-gap status line", () => {
  it("is read from ling airgapped's first line", () => {
    expect(parseStatus("Airgapped: on (this session)\n  off  …\n")).toEqual({ level: "on", source: "this session" });
    expect(parseStatus("Airgapped: off (default)")).toEqual({ level: "off", source: "default" });
    expect(parseStatus("Usage: ling airgapped [default <off|on>]")).toBeNull();
  });
});

describe("a level that refuses the network", () => {
  it("is on, or a launcher that did not answer: what reaches the network fails closed", async () => {
    expect(countsAsOn({ level: "on", source: "this session" })).toBe(true);
    expect(countsAsOn({ level: "off", source: "default" })).toBe(false);
    expect(countsAsOn(await airgapped(path.join(os.tmpdir(), "no-such-ling-here"), null))).toBe(true);
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

  // What a push or a link checks: a project's own `on` (its dreamference.toml) is seen, and its source named.
  it("names the stricter level, here or in the project's folder, and an unanswered one as unknown", async () => {
    const folder = os.tmpdir();
    const project = answering({ here: "off", [folder]: "on" });
    expect(await airgappedLevel("ling", "t-1", folder, project.run)).toEqual({ level: "on", source: "test" });
    expect(project.asked.map(([, cwd]) => cwd)).toEqual([null, folder]);
    expect(await airgappedLevel("ling", null, folder, answering({ here: "off", [folder]: "off" }).run)).toEqual({ level: "off", source: "test" });
    expect(countsAsOn(await airgappedLevel("ling", null, folder, answering({ here: "off", [folder]: null }).run))).toBe(true);
  });

  it("fails closed: no answer is on", async () => {
    expect(await airgappedOn("ling", "t-1", null, answering({ here: null }).run)).toBe(true);
  });

  it("asks only here when the thread's folder is unknown or gone, and never passes an odd id", async () => {
    const gone = answering({ here: "off" });
    expect(await airgappedOn("ling", "../x", "/no/such/folder", gone.run)).toBe(false);
    expect(gone.asked).toEqual([[["airgapped"], null]]);
  });

  it("runs the launcher binary to read status and set the default level", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-airgapped-test-"));
    try {
      const log = path.join(dir, "args.json");
      const ling = nodeScript(dir, "ling", `const fs = require("fs");
const args = process.argv.slice(2);
fs.writeFileSync(${JSON.stringify(log)}, JSON.stringify(args));
if (args[1] === "fail") process.exit(1);
process.stdout.write("Airgapped: " + (args[2] || "off") + " (user config)\\n");
`);
      expect(await airgapped(ling, null)).toEqual({ level: "off", source: "user config" });
      expect(await setDefault(ling, "on")).toBe("Airgapped: on (user config)");
      expect(JSON.parse(fs.readFileSync(log, "utf8"))).toEqual(["airgapped", "default", "on"]);
      expect(await airgapped(path.join(dir, "missing-ling"), null)).toEqual({ level: "off", source: "unknown (ling airgapped did not answer)" });
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
