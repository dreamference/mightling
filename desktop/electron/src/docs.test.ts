// Opening a cited document: only an id reaches `ling docs locate`, and only its answer is opened.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { docsAdd, docsReindex, docsRemove, docsStatus, locate, syncKnowledge } from "./docs";
import { nodeScript } from "./test-ling";

describe("a cited document", () => {
  it("is found by its id through ling docs locate, and nothing else is asked", async () => {
    const home = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-docs-open-")));
    const log = path.join(home, "calls.txt");
    const file = path.join(home, "report.pdf");
    const ling = nodeScript(home, "ling", `require("fs").appendFileSync(${JSON.stringify(log)}, process.argv.slice(2).join(" ") + "\\n");
if (process.argv[4] === "documents/42") console.log(JSON.stringify({ path: ${JSON.stringify(file)}, kind: "pdf" }));
else { console.error("ling-docs: no document documents:7"); process.exit(1); }
`);
    await expect(locate(ling, "documents/42")).resolves.toBe(file);
    await expect(locate(ling, "documents/7")).rejects.toThrow("no document documents:7");
    for (const bad of ["../etc/1", "/etc/passwd", "documents/1.pdf", "documents", 42]) {
      await expect(locate(ling, bad)).rejects.toThrow("not a document");
    }
    expect(fs.readFileSync(log, "utf8").trim().split("\n")).toEqual(["docs locate documents/42 --json", "docs locate documents/7 --json"]);
  });

  it("manages the collections through ling docs, with names and full paths only", async () => {
    const home = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-docs-settings-")));
    const log = path.join(home, "calls.txt");
    const ling = nodeScript(home, "ling", `require("fs").appendFileSync(${JSON.stringify(log)}, process.argv.slice(2).join(" ") + "\\n");
if (process.argv[3] === "status") console.log(JSON.stringify({ collections: [{ name: "documents", counts: { indexed: 3 } }], runtime_missing: null }));
else if (process.argv[3] === "add" && process.argv[4] === "/home/x/Downloads/ssh") { console.error("ling-docs: /home/x/.ssh is never indexed"); process.exit(1); }
else console.log("done");
`);
    await expect(docsStatus(ling)).resolves.toEqual({ collections: [{ name: "documents", counts: { indexed: 3 } }], runtime_missing: null });
    await expect(docsAdd(ling, "/home/x/Notes")).resolves.toBe("done");
    await expect(docsAdd(ling, "/home/x/Downloads/ssh")).rejects.toThrow("never indexed");
    await expect(docsRemove(ling, "notes")).resolves.toBe("done");
    await expect(docsReindex(ling, "documents")).resolves.toBe("done");
    await expect(docsAdd(ling, "relative/folder")).rejects.toThrow("full path");
    await expect(docsRemove(ling, "../x")).rejects.toThrow("not a collection");
    expect(fs.readFileSync(log, "utf8").trim().split("\n")).toEqual(["docs status --json", "docs add /home/x/Notes", "docs add /home/x/Downloads/ssh", "docs remove notes", "docs reindex documents"]);
  });

  it("keeps a project's knowledge folder as a collection of its own (PARITY_CLAUDE §4.7)", async () => {
    const home = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "ling-docs-knowledge-")));
    const log = path.join(home, "calls.txt");
    const ling = nodeScript(home, "ling", `require("fs").appendFileSync(${JSON.stringify(log)}, process.argv.slice(2).join(" ") + "\\n");
if (process.argv[3] === "add" && process.argv[4] === "/k/known") { console.error("ling-docs: collection known already exists"); process.exit(1); }
else if (process.argv[3] === "add" && process.argv[4] === "/k/ssh") { console.error("ling-docs: /k/ssh is never indexed"); process.exit(1); }
else if (process.argv[3] === "remove" && process.argv[4] === "gone") { console.error("ling-docs: no collection gone"); process.exit(1); }
else console.log("done");
`);
    const calls = () => fs.readFileSync(log, "utf8").trim().split("\n");
    // Its first file: the collection is made, then indexed.
    await syncKnowledge(ling, "/k/fresh", "project-p-1", 1);
    expect(calls()).toEqual(["docs add /k/fresh --name project-p-1", "docs reindex project-p-1"]);
    // A file more: the collection is there already, and is indexed again.
    await syncKnowledge(ling, "/k/known", "known", 2);
    expect(calls().slice(2)).toEqual(["docs add /k/known --name known", "docs reindex known"]);
    // Any other refusal is not swallowed, and nothing is indexed.
    await expect(syncKnowledge(ling, "/k/ssh", "ssh", 1)).rejects.toThrow("never indexed");
    expect(calls().slice(4)).toEqual(["docs add /k/ssh --name ssh"]);
    // Its last file gone: the collection is removed, and a failed remove is not an error.
    await syncKnowledge(ling, "/k/fresh", "project-p-1", 0);
    await expect(syncKnowledge(ling, "/k/gone", "gone", 0)).resolves.toBeUndefined();
    expect(calls().slice(5)).toEqual(["docs remove project-p-1", "docs remove gone"]);
    // A name that is not a collection runs nothing.
    await expect(syncKnowledge(ling, "/k/x", "../x", 1)).rejects.toThrow("not a collection");
    expect(calls()).toHaveLength(7);
  });
});
