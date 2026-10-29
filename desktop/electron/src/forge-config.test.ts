// What forge.config.js tells each platform about the app (DESKTOP_ELECTRON §3, decision 7, and §10):
// the .deb's desktop entry, and the Mac bundle's Info.plist.
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";

import app from "../app.json";

const require = createRequire(import.meta.url);
const forgeConfig = require("../forge.config.js");

const debOptions = () => forgeConfig.makers.find((entry: { name?: string }) => entry.name === "deb").configOrConfigFetcher.options;

describe("the .deb's desktop entry", () => {
  it("is a utility and a development tool, and opens both schemes' links", () => {
    expect(debOptions().categories).toEqual(["Utility", "Development"]);
    expect(debOptions().mimeType).toEqual(["x-scheme-handler/ling", "x-scheme-handler/mightling"]);
    expect([app.scheme, ...app.legacySchemes]).toEqual(["ling", "mightling"]);
  });

  it("describes the app as it is: several windows on the one bundled ling (§13)", () => {
    expect(debOptions().productDescription).toMatch(/as many windows as you open/);
    expect(debOptions().productDescription).not.toMatch(/one window/);
  });
});

describe("the Mac bundle's Info.plist", () => {
  it("claims both schemes, the developer-tools category and the Local Network entries", () => {
    const packager = forgeConfig.packagerConfig;
    expect(packager.protocols).toEqual([{ name: app.productName, schemes: ["ling", "mightling"] }]);
    expect(packager.appCategoryType).toBe("public.app-category.developer-tools");
    expect(packager.extendInfo.NSLocalNetworkUsageDescription).toMatch(/local network/);
    expect(packager.extendInfo.NSBonjourServices).toEqual(["_mightling-node._tcp"]);
    expect(packager.extendInfo.NSMicrophoneUsageDescription).toMatch(/dictate/);
  });
});
