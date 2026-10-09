// The `.deb` declares exactly the relationships forge.config.js lists (linux/deb-maker.js):
// electron-installer-debian's defaults (libsecret, the trash helpers, a bare libgtk-3-0, its
// Recommends and Suggests) are not added, and Conflicts/Replaces reach the control file.
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const { ownRelationsInstaller } = require("../linux/deb-maker.js");
const forgeConfig = require("../forge.config.js");

const debOptions = () => {
  const maker = forgeConfig.makers.find((entry: { name?: string }) => entry.name === "deb");
  return maker.configOrConfigFetcher.options;
};

/** A packaged app's folder as the installer reads it: its package.json and Electron's version file. */
function fakePackagedApp(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mightling-deb-test-"));
  fs.mkdirSync(path.join(dir, "resources", "app"), { recursive: true });
  fs.writeFileSync(path.join(dir, "resources", "app", "package.json"), JSON.stringify({ name: "mightling-desktop", version: "1.6.0" }));
  fs.writeFileSync(path.join(dir, "version"), require("electron/package.json").version);
  return dir;
}

describe("the .deb's control file", () => {
  it("names only our relationships", async () => {
    const src = fakePackagedApp();
    const ours = debOptions();
    const Installer = ownRelationsInstaller();
    const installer = new Installer({ options: ours, src, dest: src, arch: "arm64", logger: () => {} });
    try {
      const defaults = await installer.generateDefaults();
      // What would otherwise be merged in (if this stops holding, the installer changed: re-read it).
      expect(defaults.depends).toContain("libsecret-1-0");
      expect(defaults.recommends.length).toBeGreaterThan(0);

      await installer.generateOptions();
      expect(installer.options.depends).toEqual(ours.depends);
      expect(installer.options.recommends).toEqual([]);
      expect(installer.options.suggests).toEqual([]);

      installer.stagingDir = path.join(src, "staging");
      fs.mkdirSync(path.join(installer.stagingDir, "DEBIAN"), { recursive: true });
      await installer.createControl();
      const control = fs.readFileSync(path.join(installer.stagingDir, "DEBIAN", "control"), "utf8");
      const field = (name: string) => control.match(new RegExp(`^${name}: (.*)$`, "m"))?.[1];
      expect(field("Package")).toBe("mightling");
      expect(field("Depends")).toBe(ours.depends.join(", "));
      expect(field("Recommends")).toBeUndefined();
      expect(field("Suggests")).toBeUndefined();
      expect(field("Conflicts")).toBe("puffin, mightling-app");
      expect(field("Replaces")).toBe("puffin, mightling-app");
      expect(control).not.toMatch(/libsecret|trash-cli|gvfs/);
      // Description, with its continuation lines, still ends the paragraph.
      expect(control.trimEnd().split("\n").slice(-1)[0]).toMatch(/^ /);
    } finally {
      fs.rmSync(src, { recursive: true, force: true });
    }
  });
});
