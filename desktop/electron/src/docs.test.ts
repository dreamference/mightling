// Opening a cited document: only an id reaches `ling docs locate`, and only its answer is opened.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { docsAdd, docsReindex, docsRemove, docsStatus, locate } from "./docs";
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
});
