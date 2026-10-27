// Memories one by one (DESKTOP §16 B17): the consolidated files shown and saved, nothing else.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { listMemoryFiles, writeMemoryFile } from "./memoryfiles";

describe("the memory files", () => {
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
