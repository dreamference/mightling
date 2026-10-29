import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BUNDLE_NAME, bundleName, bundleServer, entryPath, fetchingCommand, installBundle, listBundles, readArchive, readBundle, removeBundle, settingValues, substitute, ENTRY_LIMIT } from "./mcpb";
import { makeZip, manifestText, type FixtureEntry } from "./test-zip";

let scratch: string;
let codexHome: string;
let places: { home: string; desktop: string; documents: string; downloads: string };

beforeEach(() => {
  scratch = fs.mkdtempSync(path.join(os.tmpdir(), "mcpb-test-"));
  codexHome = path.join(scratch, ".mightling");
  places = { home: path.join(scratch, "home"), desktop: path.join(scratch, "home", "Desktop"), documents: path.join(scratch, "home", "Documents"), downloads: path.join(scratch, "home", "Downloads") };
});
afterEach(() => fs.rmSync(scratch, { recursive: true, force: true }));

/** Writes a crafted bundle into the scratch folder and returns its path. */
function bundle(entries: FixtureEntry[], signature?: Buffer, name = "b.mcpb"): string {
  const file = path.join(scratch, name);
  fs.writeFileSync(file, makeZip(entries, signature));
  return file;
}
const good = (extra: Record<string, unknown> = {}): FixtureEntry[] => [
  { name: "manifest.json", data: manifestText(extra) },
  { name: "server/" },
  { name: "server/index.js", data: "console.log('hi')\n" },
];

describe("entryPath", () => {
  it("keeps a plain relative path and drops a folder's trailing slash", () => {
    expect(entryPath("server/index.js")).toBe("server/index.js");
    expect(entryPath("server/")).toBe("server");
  });
  it.each(["/etc/passwd", "C:/x", "c:x", "../x", "a/../../x", "a/./b", "a//b", "a\\..\\b", "a\u0000b", ""])("refuses %j", (name) => {
    expect(() => entryPath(name)).toThrow();
  });
});

describe("readArchive", () => {
  it("lists a good bundle's entries, unsigned", async () => {
    const archive = await readArchive(bundle(good()));
    expect(archive.signed).toBe(false);
    expect(archive.entries.map((entry) => [entry.path, entry.directory])).toEqual([["manifest.json", false], ["server", true], ["server/index.js", false]]);
  });
  it("finds the zip before a signature block and says the bundle is signed", async () => {
    const archive = await readArchive(bundle(good(), Buffer.from("not really DER")));
    expect(archive.signed).toBe(true);
    expect(archive.entries).toHaveLength(3);
  });
  it("refuses a damaged signature block", async () => {
    const file = bundle(good(), Buffer.from("x"));
    const bytes = fs.readFileSync(file);
    const at = bytes.lastIndexOf(Buffer.from("MCPB_SIG_V1")) + 11;
    bytes.writeUInt32LE(999, at);
    fs.writeFileSync(file, bytes);
    await expect(readArchive(file)).rejects.toThrow(/signature block/);
  });
  it("refuses a file that is not a zip", async () => {
    const file = path.join(scratch, "x.mcpb");
    fs.writeFileSync(file, "hello, this is not a zip at all".repeat(4));
    await expect(readArchive(file)).rejects.toThrow(/not a zip/);
  });
  it.each([
    ["an absolute path", "/tmp/evil", /absolute/],
    ["a path with ..", "../evil", /leaves its folder/],
    ["a nested ..", "server/../../evil", /leaves its folder/],
    ["a Windows drive", "C:/evil", /absolute/],
    ["a backslash", "..\\evil", /unsafe/],
  ])("refuses %s", async (_what, name, message) => {
    await expect(readArchive(bundle([...good(), { name, data: "x" }]))).rejects.toThrow(message);
  });
  it("refuses a link, even one pointing inside", async () => {
    await expect(readArchive(bundle([...good(), { name: "server/link", data: "index.js", mode: 0o120777 }]))).rejects.toThrow(/link/);
  });
  it("refuses a device or other special file", async () => {
    await expect(readArchive(bundle([...good(), { name: "server/fifo", data: "", mode: 0o010644 }]))).rejects.toThrow(/not a file or a folder/);
  });
  it("refuses an encrypted entry and an unknown method", async () => {
    await expect(readArchive(bundle([...good(), { name: "secret", data: "x", flags: 1 }]))).rejects.toThrow(/encrypted/);
    await expect(readArchive(bundle([...good(), { name: "odd", data: "x", method: 12 }]))).rejects.toThrow(/method 12/);
  });
  it("refuses the same path twice, whatever its case", async () => {
    await expect(readArchive(bundle([...good(), { name: "Server/Index.js", data: "x" }]))).rejects.toThrow(/twice/);
  });
  it("refuses more files than the limit", async () => {
    const many = Array.from({ length: ENTRY_LIMIT + 1 }, (_, index) => ({ name: `f${index}`, data: "", deflate: false }));
    await expect(readArchive(bundle(many))).rejects.toThrow(/more than 20000 files/);
  });
  it("refuses a bundle whose declared sizes add up past the limit", async () => {
    await expect(readArchive(bundle([...good(), { name: "a", data: "x", size: 0x7fffffff }, { name: "b", data: "x", size: 0x7fffffff }]))).rejects.toThrow(/more than 1024 MB/);
  });
});

describe("readBundle", () => {
  it("shows the manifest: names, author, the command with the folder in it, the settings, unsigned", async () => {
    const { summary } = await readBundle(bundle(good()), codexHome, places, "linux");
    const dir = path.join(codexHome, "mcpb", "notes-search");
    expect(summary).toMatchObject({
      name: "notes-search", displayName: "Notes search", version: "1.2.0", author: "Example Tools", serverType: "node",
      command: "node", args: [`${dir}/server/index.js`, "--root", "${user_config.root}"], env: { NOTES_TOKEN: "${user_config.token}" },
      signed: false, files: 2, fetches: null, refusal: null, installed: false,
    });
    expect(summary.settings.map((setting) => [setting.key, setting.type, setting.required, setting.sensitive, setting.default])).toEqual([
      ["root", "directory", true, false, `${places.home}/notes`], ["token", "string", false, true, null], ["limit", "number", false, false, 10],
    ]);
  });
  it("refuses a bundle with no manifest, or a manifest that is not JSON", async () => {
    await expect(readBundle(bundle([{ name: "x", data: "y" }]), codexHome, places)).rejects.toThrow(/no manifest.json/);
    await expect(readBundle(bundle([{ name: "manifest.json", data: "{" }]), codexHome, places)).rejects.toThrow(/not JSON/);
  });
  it("says why a uv bundle or one for another platform is not installed here", async () => {
    expect((await readBundle(bundle(good({ server: { type: "uv", entry_point: "src/server.py" } })), codexHome, places, "linux")).summary.refusal).toMatch(/fetch its Python packages/);
    expect((await readBundle(bundle(good({ compatibility: { platforms: ["darwin", "win32"] } })), codexHome, places, "linux")).summary.refusal).toMatch(/only on darwin, win32/);
  });
  it("names a package runner as fetching", async () => {
    const { summary } = await readBundle(bundle(good({ server: { type: "node", mcp_config: { command: "npx", args: ["-y", "something"] } } })), codexHome, places);
    expect(summary.fetches).toBe("npx");
  });
});

describe("names, substitution and settings", () => {
  it("makes a manifest's name a server name", () => {
    expect(bundleName("My MCP Extension")).toBe("My-MCP-Extension");
    expect(bundleName("@scope/tool.v2")).toBe("scope-tool-v2");
    expect(BUNDLE_NAME.test(bundleName("x".repeat(200)))).toBe(true);
    expect(() => bundleName("../..")).toThrow();
  });
  it("substitutes the folder, the user's folders and the settings", () => {
    const where = { dir: "/b", ...places };
    expect(substitute("${__dirname}/a ${HOME} ${user_config.k} ${unknown}", where, { k: ["x", "y"] })).toBe(`/b/a ${places.home} x,y \${unknown}`);
    expect(substitute("${user_config.k}", where, null)).toBe("${user_config.k}");
    expect(substitute("${user_config.missing}", where, {})).toBe("");
  });
  it("expands a whole-argument setting with several values into one argument each, applies the platform's override", () => {
    const manifest = JSON.parse(manifestText({
      server: { type: "binary", mcp_config: { command: "server/tool", args: ["${user_config.dirs}"], env: {}, platform_overrides: { win32: { args: ["--win", "${user_config.dirs}"] } } } },
    }));
    expect(bundleServer(manifest, "/b", places, { dirs: ["/x", "/y"] }, "linux")).toEqual({ command: "/b/server/tool", args: ["/x", "/y"], cwd: "/b" });
    expect(bundleServer(manifest, "/b", places, { dirs: ["/x"] }, "win32").args).toEqual(["--win", "/x"]);
    expect(bundleServer(manifest, "/b", places, { dirs: ["/x"] }, "win32").command).toMatch(/tool\.exe$/);
  });
  it("runs node with the entry point when a bundle has no mcp_config", () => {
    const manifest = { name: "n", server: { type: "node", entry_point: "server/index.js" } };
    expect(bundleServer(manifest, "/b", places, {}, "linux")).toEqual({ command: "node", args: ["/b/server/index.js"], cwd: "/b" });
  });
  it("checks the settings: required, numbers in range, defaults", () => {
    const settings = [
      { key: "root", type: "directory" as const, title: "Root", description: "", required: true, sensitive: false, multiple: false, default: null, min: null, max: null },
      { key: "limit", type: "number" as const, title: "Limit", description: "", required: false, sensitive: false, multiple: false, default: 10, min: 1, max: 50 },
    ];
    expect(() => settingValues(settings, {})).toThrow(/Root is required/);
    expect(() => settingValues(settings, { root: "/n", limit: "99" })).toThrow(/between 1 and 50/);
    expect(settingValues(settings, { root: "/n", unasked: "x" })).toEqual({ root: "/n", limit: 10 });
  });
  it("knows the package runners", () => {
    expect(fetchingCommand("/usr/bin/npx")).toBe("npx");
    expect(fetchingCommand("C:\\tools\\uvx.exe")).toBe("uvx");
    expect(fetchingCommand("node")).toBeNull();
  });
});

describe("installBundle, listBundles, removeBundle", () => {
  it("unpacks into $CODEX_HOME/mcpb/<name>/ and returns the server config.toml gets", async () => {
    const file = bundle(good());
    const { name, dir, server } = await installBundle(file, codexHome, places, { expected: "notes-search", values: { root: "/notes", token: "s3cret" }, replace: false, platform: "linux" });
    expect(name).toBe("notes-search");
    expect(dir).toBe(path.join(codexHome, "mcpb", "notes-search"));
    expect(fs.readFileSync(path.join(dir, "server", "index.js"), "utf8")).toBe("console.log('hi')\n");
    expect(server).toEqual({ command: "node", args: [`${dir}/server/index.js`, "--root", "/notes"], env: { NOTES_TOKEN: "s3cret" }, cwd: dir });
    // Nothing is left beside it.
    expect(fs.readdirSync(path.join(codexHome, "mcpb"))).toEqual(["notes-search"]);
    expect(await listBundles(codexHome)).toEqual([{ name: "notes-search", displayName: "Notes search", version: "1.2.0", author: "Example Tools", dir }]);
  });
  it("refuses to overwrite an installed bundle unless asked, and replaces it when asked", async () => {
    const file = bundle(good());
    const options = { expected: "notes-search", values: { root: "/n" }, replace: false, platform: "linux" as const };
    await installBundle(file, codexHome, places, options);
    fs.writeFileSync(path.join(codexHome, "mcpb", "notes-search", "left.txt"), "x");
    await expect(installBundle(file, codexHome, places, options)).rejects.toThrow(/installed already/);
    expect((await readBundle(file, codexHome, places)).summary.installed).toBe(true);
    await installBundle(file, codexHome, places, { ...options, replace: true });
    expect(fs.existsSync(path.join(codexHome, "mcpb", "notes-search", "left.txt"))).toBe(false);
    expect(fs.readdirSync(path.join(codexHome, "mcpb"))).toEqual(["notes-search"]);
  });
  // The old bundle is moved aside before the new one takes its place: when that second move fails, the old one is put back.
  it("keeps the installed bundle when its replacement cannot be moved in", async () => {
    const file = bundle(good());
    const options = { expected: "notes-search", values: { root: "/n" }, replace: true, platform: "linux" as const };
    await installBundle(file, codexHome, places, options);
    const dir = path.join(codexHome, "mcpb", "notes-search");
    fs.writeFileSync(path.join(dir, "left.txt"), "x");
    const rename = fsp.rename.bind(fsp);
    const spy = vi.spyOn(fsp, "rename").mockImplementation(async (from, to) => {
      if (String(from).endsWith(".partial") && to === dir) throw Object.assign(new Error("busy"), { code: "EBUSY" });
      return rename(from, to);
    });
    try {
      await expect(installBundle(file, codexHome, places, options)).rejects.toThrow(/busy/);
    } finally {
      spy.mockRestore();
    }
    expect(fs.readFileSync(path.join(dir, "left.txt"), "utf8")).toBe("x");
    expect(fs.readdirSync(path.join(codexHome, "mcpb"))).toEqual(["notes-search"]);
  });
  it("leaves nothing behind when an entry fails its checksum", async () => {
    const file = bundle(good());
    const bytes = fs.readFileSync(file);
    // The central directory's CRC is the one checked: corrupt it there.
    const central = bytes.lastIndexOf(Buffer.from("server/index.js")) - 46;
    bytes.writeUInt32LE(0x12345678, central + 16);
    fs.writeFileSync(file, bytes);
    await expect(installBundle(file, codexHome, places, { expected: "notes-search", values: { root: "/n" }, replace: false, platform: "linux" })).rejects.toThrow(/checksum/);
    expect(fs.readdirSync(path.join(codexHome, "mcpb"))).toEqual([]);
  });
  it("refuses an entry that inflates past the size it declares (a zip bomb)", async () => {
    const file = bundle([...good(), { name: "bomb", data: Buffer.alloc(1024 * 1024), size: 10 }]);
    await expect(installBundle(file, codexHome, places, { expected: "notes-search", values: { root: "/n" }, replace: false, platform: "linux" })).rejects.toThrow(/size it declares/);
    expect(fs.readdirSync(path.join(codexHome, "mcpb"))).toEqual([]);
  });
  it("refuses a file that changed since it was shown, and a required setting left empty", async () => {
    const file = bundle(good());
    await expect(installBundle(file, codexHome, places, { expected: "other", values: { root: "/n" }, replace: false })).rejects.toThrow(/changed since/);
    const noDefault = bundle(good({ user_config: { root: { type: "directory", title: "Notes folder", required: true } } }), undefined, "c.mcpb");
    await expect(installBundle(noDefault, codexHome, places, { expected: "notes-search", values: {}, replace: false })).rejects.toThrow(/Notes folder is required/);
  });
  it("asks about a package runner a setting names, before anything is installed", async () => {
    const file = bundle(good({ user_config: { runner: { type: "string", title: "Runner", default: "npx" } }, server: { type: "node", mcp_config: { command: "${user_config.runner}", args: ["-y", "something"] } } }));
    expect((await readBundle(file, codexHome, places)).summary.fetches).toBeNull();
    const asked: string[] = [];
    const fetching = async (command: string) => {
      asked.push(command);
      throw new Error(`${command} fetches what it runs`);
    };
    await expect(installBundle(file, codexHome, places, { expected: "notes-search", values: {}, replace: false, platform: "linux", fetching })).rejects.toThrow(/npx fetches/);
    expect(asked).toEqual(["npx"]);
    expect(fs.existsSync(path.join(codexHome, "mcpb"))).toBe(false);
  });
  it("makes a binary command inside the bundle runnable", async () => {
    if (process.platform === "win32") return;
    const file = bundle([{ name: "manifest.json", data: manifestText({ server: { type: "binary", mcp_config: { command: "${__dirname}/bin/tool", args: [] } } }) }, { name: "bin/tool", data: "#!/bin/sh\n", mode: 0o100644 }]);
    const { dir } = await installBundle(file, codexHome, places, { expected: "notes-search", values: { root: "/n" }, replace: false, platform: "linux" });
    expect(fs.statSync(path.join(dir, "bin", "tool")).mode & 0o111).not.toBe(0);
  });
  it("removes a bundle's folder, and refuses a name that is not one", async () => {
    await installBundle(bundle(good()), codexHome, places, { expected: "notes-search", values: { root: "/n" }, replace: false, platform: "linux" });
    await removeBundle(codexHome, "notes-search");
    expect(await listBundles(codexHome)).toEqual([]);
    await removeBundle(codexHome, "notes-search");
    await expect(removeBundle(codexHome, "../x")).rejects.toThrow(/not a bundle name/);
  });
});
