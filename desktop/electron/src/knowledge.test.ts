// A project's knowledge files (knowledge.ts): copies in the app's own folder for the project, a name
// taken getting a number, nothing outside the folder reached, and a project id checked.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { addKnowledge, knowledgeFolder, listKnowledge, removeKnowledge } from "./knowledge";

const scratch: string[] = [];
const folder = () => {
  const made = fs.mkdtempSync(path.join(os.tmpdir(), "mightling-knowledge-"));
  scratch.push(made);
  return made;
};
afterEach(() => {
  for (const made of scratch.splice(0)) fs.rmSync(made, { recursive: true, force: true });
});

describe("a project's knowledge files", () => {
  it("are copies in $CODEX_HOME/projects/<id>/knowledge, the originals untouched, a taken name numbered", async () => {
    const home = folder();
    const source = folder();
    fs.writeFileSync(path.join(source, "style.md"), "# Style\n");
    fs.mkdirSync(path.join(source, "other"));
    fs.writeFileSync(path.join(source, "other", "style.md"), "# Another\n");
    expect(await listKnowledge(home, "p-1")).toEqual([]);
    const added = await addKnowledge(home, "p-1", [path.join(source, "style.md"), path.join(source, "other", "style.md")]);
    expect(added.map((file) => file.name)).toEqual(["style (2).md", "style.md"]);
    expect(added[1].path).toBe(path.join(home, "projects", "p-1", "knowledge", "style.md"));
    expect(fs.readFileSync(path.join(source, "style.md"), "utf8")).toBe("# Style\n");
    expect((await removeKnowledge(home, "p-1", "style.md")).map((file) => file.name)).toEqual(["style (2).md"]);
  });

  it("refuse a folder, a link, a name with a folder in it and a project id that is not one", async () => {
    const home = folder();
    const source = folder();
    fs.writeFileSync(path.join(source, "real.txt"), "x");
    fs.symlinkSync(path.join(source, "real.txt"), path.join(source, "link.txt"));
    await expect(addKnowledge(home, "p", [source])).rejects.toThrow(/not a file/);
    await expect(addKnowledge(home, "p", [path.join(source, "link.txt")])).rejects.toThrow(/not a file/);
    await expect(removeKnowledge(home, "p", "../../config.toml")).rejects.toThrow(/not a file name/);
    await expect(removeKnowledge(home, "p", ".hidden")).rejects.toThrow(/not a file name/);
    expect(() => knowledgeFolder(home, "../x")).toThrow(/not a project id/);
  });
});
