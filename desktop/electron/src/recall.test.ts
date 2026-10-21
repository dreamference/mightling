// Earlier questions for the agent (DESKTOP_PARITY_CLAUDE §4.4): the copies and the skill, on and off.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { HISTORY_DIR, MAX_BYTES, SKILL_DIR, disableRecall, enableRecall, pruneRecall, recallIndex, recallOn, writeRecall } from "./recall";

const homes: string[] = [];
const home = () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ling-app-recall-"));
  homes.push(dir);
  return dir;
};
afterEach(() => {
  for (const dir of homes.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

describe("earlier questions", () => {
  it("keeps nothing while off, and refuses a write", () => {
    const h = home();
    expect(recallOn(h)).toBe(false);
    expect(() => writeRecall(h, "t1", 1, "# Q")).toThrow(/off/);
    expect(fs.existsSync(path.join(h, HISTORY_DIR))).toBe(false);
  });

  it("on: a skill naming the folder, copies kept by thread, archived ones dropped; off deletes it all", () => {
    const h = home();
    enableRecall(h);
    const skill = fs.readFileSync(path.join(h, SKILL_DIR, "SKILL.md"), "utf8");
    expect(skill).toMatch(/^---\nname: earlier-questions\ndescription: /);
    expect(skill).toContain(path.join(h, HISTORY_DIR));
    expect(skill).toContain("Only read them");
    writeRecall(h, "t1", 100, "# NVFP4\n\nAnswer one");
    writeRecall(h, "t2", 200, "# Memory\n\nAnswer two");
    expect(recallIndex(h)).toEqual({ t1: 100, t2: 200 });
    expect(fs.statSync(path.join(h, HISTORY_DIR, "t1.md")).mode & 0o777).toBe(0o600);
    expect(() => writeRecall(h, "../evil", 1, "x")).toThrow(/thread id/);
    expect(() => writeRecall(h, "t3", 1, 42)).toThrow(/text/);
    // A long one keeps its start.
    writeRecall(h, "t4", 1, "x".repeat(MAX_BYTES + 100));
    expect(fs.readFileSync(path.join(h, HISTORY_DIR, "t4.md"), "utf8")).toContain("is not kept");
    expect(pruneRecall(h, ["t2"])).toBe(2);
    expect(fs.readdirSync(path.join(h, HISTORY_DIR)).sort()).toEqual(["index.json", "t2.md"]);
    expect(recallIndex(h)).toEqual({ t2: 200 });
    disableRecall(h);
    expect(fs.existsSync(path.join(h, HISTORY_DIR))).toBe(false);
    expect(fs.existsSync(path.join(h, SKILL_DIR))).toBe(false);
    expect(recallOn(h)).toBe(false);
  });
});
