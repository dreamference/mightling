import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { LING_VERSION_FILE, versionText, wantsVersion } from "./version";

describe("--version", () => {
  it("is asked for by --version only", () => {
    expect(wantsVersion(["--version"])).toBe(true);
    expect(wantsVersion(["--work", "--version"])).toBe(true);
    expect(wantsVersion([])).toBe(false);
    expect(wantsVersion(["--work", "--cwd", "/home/u/p"])).toBe(false);
  });

  it("prints the app's version and the bundled ling's, read from the stamp", () => {
    const read = (file: string) => {
      expect(file).toBe(path.join("/opt/res", LING_VERSION_FILE));
      return "ling 1.6.0\n";
    };
    expect(versionText("Mightling", "1.6.0", "/opt/res", read)).toBe("Mightling 1.6.0\nling 1.6.0\n");
  });

  it("says when no ling is bundled or the app is not packaged", () => {
    const missing = () => {
      throw new Error("ENOENT");
    };
    expect(versionText("Mightling", "1.6.0", "/opt/res", missing)).toBe("Mightling 1.6.0\nling: not bundled\n");
    expect(versionText("Mightling", "1.6.0", null, missing)).toBe("Mightling 1.6.0\nling: not bundled\n");
    expect(versionText("Mightling", "1.6.0", "/opt/res", () => "\n")).toBe("Mightling 1.6.0\nling: version unknown\n");
  });

  it("reads the file forge.config.js writes", () => {
    const config = fs.readFileSync(path.join(__dirname, "..", "forge.config.js"), "utf8");
    expect(config).toContain(`"${LING_VERSION_FILE}"`);
  });
});
