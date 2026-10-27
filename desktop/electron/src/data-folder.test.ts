// The data folder's rule against the cases `ling app` is held to too
// (ling-rs/vectors/app-data-folder.json), and the one-time move of 1.7.1's Windows folder.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import appConfig from "../app.json";
import { dataFolder, legacyDataFolder, settleDataFolder, type FolderOps } from "./data-folder";
import vectors from "../../../ling-rs/vectors/app-data-folder.json";

interface Case {
  name: string;
  platform: string;
  env: Record<string, string>;
  expected: string | null;
  legacy: string | null;
}

describe("the data folder", () => {
  it("is named after the app's identifier", () => {
    expect(vectors.identifier).toBe(appConfig.identifier);
  });

  for (const each of vectors.cases as Case[]) {
    it(each.name, () => {
      expect(dataFolder(each.platform, each.env, vectors.identifier)).toBe(each.expected);
      expect(legacyDataFolder(each.platform, each.env, vectors.identifier)).toBe(each.legacy);
    });
  }
});

describe("the move of 1.7.1's Windows folder", () => {
  const scratch = () => fs.mkdtempSync(path.join(os.tmpdir(), "ling-data-folder-"));
  const quiet = () => {
    const lines: string[] = [];
    return { lines, log: (line: string) => lines.push(line) };
  };

  it("moves the old folder, with what is in it, when the new one does not exist", () => {
    const root = scratch();
    const legacy = path.join(root, "profile", ".local", "share", "app");
    const current = path.join(root, "AppData", "Local", "app");
    fs.mkdirSync(path.join(legacy, "Local Storage"), { recursive: true });
    fs.writeFileSync(path.join(legacy, "prefs.json"), "{\"theme\":\"dark\"}");
    const { lines, log } = quiet();
    expect(settleDataFolder(legacy, current, fs, log)).toBe(current);
    expect(fs.existsSync(legacy)).toBe(false);
    expect(fs.readFileSync(path.join(current, "prefs.json"), "utf8")).toBe("{\"theme\":\"dark\"}");
    expect(fs.existsSync(path.join(current, "Local Storage"))).toBe(true);
    expect(lines).toEqual([`ling-app: moved ${legacy} to ${current}`]);
  });

  it("leaves both and uses the new one when both exist", () => {
    const root = scratch();
    const legacy = path.join(root, "old");
    const current = path.join(root, "new");
    fs.mkdirSync(legacy);
    fs.mkdirSync(current);
    fs.writeFileSync(path.join(legacy, "prefs.json"), "old");
    fs.writeFileSync(path.join(current, "prefs.json"), "new");
    const { lines, log } = quiet();
    expect(settleDataFolder(legacy, current, fs, log)).toBe(current);
    expect(fs.readFileSync(path.join(legacy, "prefs.json"), "utf8")).toBe("old");
    expect(fs.readFileSync(path.join(current, "prefs.json"), "utf8")).toBe("new");
    expect(lines).toHaveLength(1);
  });

  it("does nothing without an old folder, or off Windows", () => {
    const root = scratch();
    const current = path.join(root, "new");
    const { lines, log } = quiet();
    expect(settleDataFolder(path.join(root, "old"), current, fs, log)).toBe(current);
    expect(settleDataFolder(null, current, fs, log)).toBe(current);
    expect(fs.existsSync(current)).toBe(false);
    expect(lines).toEqual([]);
  });

  it("keeps the old folder, and uses it, when the move fails", () => {
    const root = scratch();
    const legacy = path.join(root, "old");
    const current = path.join(root, "other-volume", "new");
    fs.mkdirSync(legacy);
    fs.writeFileSync(path.join(legacy, "prefs.json"), "kept");
    const crossDevice: FolderOps = {
      existsSync: fs.existsSync,
      mkdirSync: fs.mkdirSync,
      renameSync: () => {
        throw Object.assign(new Error("EXDEV: cross-device link not permitted"), { code: "EXDEV" });
      },
    };
    const { lines, log } = quiet();
    expect(settleDataFolder(legacy, current, crossDevice, log)).toBe(legacy);
    expect(fs.readFileSync(path.join(legacy, "prefs.json"), "utf8")).toBe("kept");
    expect(fs.existsSync(current)).toBe(false);
    expect(lines).toEqual([`ling-app: could not move ${legacy} to ${current} (EXDEV); it is used where it is this time`]);
  });
});
