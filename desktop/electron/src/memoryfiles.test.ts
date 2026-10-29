// Memories one by one (DESKTOP §16 B17): the consolidated files shown and saved, nothing else.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { listMemoryFiles, writeMemoryFile } from "./memoryfiles";

describe("the memory files", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows only what was read when a file shrinks while it is read", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-memories-shrink-"));
    fs.mkdirSync(path.join(home, "memories"));
    fs.writeFileSync(path.join(home, "memories", "memory_summary.md"), "short now");
    const lstat = fs.promises.lstat.bind(fs.promises);
    vi.spyOn(fs.promises, "lstat").mockImplementation(async (file) => Object.assign(await lstat(file), { size: 100 }));
    expect((await listMemoryFiles(home))[0].text).toBe("short now");
  });

  it("leaves no stray file when an edit cannot be saved, and saves two edits at once", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-memories-stray-"));
    const root = path.join(home, "memories");
    fs.mkdirSync(root);
    fs.writeFileSync(path.join(root, "memory_summary.md"), "old");
    await Promise.all([writeMemoryFile(home, "memory_summary.md", "one"), writeMemoryFile(home, "memory_summary.md", "two")]);
    expect(["one", "two"]).toContain(fs.readFileSync(path.join(root, "memory_summary.md"), "utf8"));
    vi.spyOn(fs.promises, "rename").mockRejectedValue(Object.assign(new Error("EXDEV"), { code: "EXDEV" }));
    await expect(writeMemoryFile(home, "memory_summary.md", "new")).rejects.toThrow("EXDEV");
    expect(fs.readdirSync(root)).toEqual(["memory_summary.md"]);
  });

  it("lists the summary and the index when they exist, and saves an edit of one", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-memories-"));
    expect(await listMemoryFiles(home)).toEqual([]);
    const root = path.join(home, "memories");
    fs.mkdirSync(root);
    fs.writeFileSync(path.join(root, "memory_summary.md"), "## User preferences\n- Short answers.\n");
    fs.writeFileSync(path.join(root, "raw_memories.md"), "not shown");
    const listed = await listMemoryFiles(home);
    expect(listed.map((file) => [file.name, file.text])).toEqual([["memory_summary.md", "## User preferences\n- Short answers.\n"]]);
    await writeMemoryFile(home, "memory_summary.md", "## User preferences\n- Long answers.\n");
    expect(fs.readFileSync(path.join(root, "memory_summary.md"), "utf8")).toBe("## User preferences\n- Long answers.\n");
    expect(fs.readdirSync(root).sort()).toEqual(["memory_summary.md", "raw_memories.md"]);
  });

  it("writes nothing else: not another name, not a path, not a file that is gone", async () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-memories-refuse-"));
    fs.mkdirSync(path.join(home, "memories"));
    await expect(writeMemoryFile(home, "raw_memories.md", "x")).rejects.toThrow("not a memory file");
    await expect(writeMemoryFile(home, "../config.toml", "x")).rejects.toThrow("not a memory file");
    await expect(writeMemoryFile(home, "MEMORY.md", "x")).rejects.toThrow("not there");
    await expect(writeMemoryFile(home, "MEMORY.md", 3)).rejects.toThrow("at most");
  });
});
