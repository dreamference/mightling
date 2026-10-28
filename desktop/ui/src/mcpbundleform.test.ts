import { describe, expect, it } from "vitest";

import type { BundleSetting } from "./bridge";
import { bundleRemoval, bundleWrite, commandLine, configuredServer, formValues, initialForm, missingSettings, sizeText } from "./mcpbundleform";

const setting = (key: string, type: BundleSetting["type"], extra: Partial<BundleSetting> = {}): BundleSetting => ({
  key, type, title: key.toUpperCase(), description: "", required: false, sensitive: false, multiple: false, default: null, min: null, max: null, ...extra,
});
const settings = [
  setting("root", "directory", { required: true, default: "/home/u/notes" }),
  setting("dirs", "directory", { multiple: true, default: ["/a", "/b"] }),
  setting("limit", "number", { default: 10 }),
  setting("readOnly", "boolean", { default: true }),
  setting("token", "string", { sensitive: true }),
];

describe("MCP bundles in the window", () => {
  it("adds the bundle's server to config.toml in one write, replacing an entry of that name, and takes it out with null", () => {
    expect(bundleWrite("notes", { command: "node", args: ["/b/index.js"], cwd: "/b" })).toEqual({
      edits: [{ keyPath: "mcp_servers.notes", value: { command: "node", args: ["/b/index.js"], cwd: "/b" }, mergeStrategy: "replace" }], filePath: null, expectedVersion: null, reloadUserConfig: true,
    });
    expect(bundleRemoval("notes").edits).toEqual([{ keyPath: "mcp_servers.notes", value: null, mergeStrategy: "replace" }]);
  });
  it("opens the form on the defaults and sends typed values, an empty field left out", () => {
    const form = initialForm(settings);
    expect(form).toEqual({ root: "/home/u/notes", dirs: "/a\n/b", limit: "10", readOnly: true, token: "" });
    expect(formValues(settings, form)).toEqual({ root: "/home/u/notes", dirs: ["/a", "/b"], limit: 10, readOnly: true });
    expect(formValues(settings, { ...form, dirs: " /x \n\n/y", readOnly: false, token: "s3" })).toMatchObject({ dirs: ["/x", "/y"], readOnly: false, token: "s3" });
  });
  it("names the required settings left empty", () => {
    expect(missingSettings(settings, { ...initialForm(settings), root: "  " })).toEqual(["ROOT"]);
    expect(missingSettings(settings, initialForm(settings))).toEqual([]);
  });
  it("shows the command line with spaced arguments quoted", () => {
    expect(commandLine("node", ["/b/index.js", "--root", "/My Notes"])).toBe('node /b/index.js --root "/My Notes"');
  });
  it("knows a server configured already, and says sizes in words", () => {
    expect(configuredServer({ mcp_servers: { notes: { command: "x" } } }, "notes")).toBe(true);
    expect(configuredServer({ mcp_servers: {} }, "notes")).toBe(false);
    expect(configuredServer(null, "notes")).toBe(false);
    expect(sizeText(512)).toBe("512 bytes");
    expect(sizeText(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
