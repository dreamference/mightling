// Work's pasted images written under $CODEX_HOME, never into the project (DESKTOP §16 B13).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { attachmentsRoot, pruneAttachments, writeWorkAttachment } from "./attachments";

describe("Work's attachments", () => {
  it("are written into a day's folder under CODEX_HOME, private, with a safe and unused name", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-attach-"));
    const day = new Date("2026-10-27T10:00:00Z");
    const first = writeWorkAttachment(home, { name: "../shot.png", kind: "image", data: Buffer.from("PNG").toString("base64") }, day);
    const second = writeWorkAttachment(home, { name: "shot.png", kind: "image", data: Buffer.from("PNG2").toString("base64") }, day);
    expect(first.path).toBe(path.join(attachmentsRoot(home), "20261027", "shot.png"));
    expect(second.path).toBe(path.join(attachmentsRoot(home), "20261027", "shot-1.png"));
    expect(fs.readFileSync(second.path, "utf8")).toBe("PNG2");
    if (process.platform !== "win32") expect(fs.statSync(path.dirname(first.path)).mode & 0o777).toBe(0o700);
    expect(() => writeWorkAttachment(home, { name: "x", kind: "image", data: "" }, day)).toThrow("empty");
    expect(() => writeWorkAttachment(home, { name: "x", kind: "video" as "image", data: "eA==" }, day)).toThrow("kind");
  });

  it("keep two weeks of day folders", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "ling-attach-prune-"));
    const root = attachmentsRoot(home);
    for (const name of ["20261001", "20261020", "notes"]) fs.mkdirSync(path.join(root, name), { recursive: true });
    expect(pruneAttachments(root, new Date("2026-10-27T00:00:00Z"))).toBe(1);
    expect(fs.readdirSync(root).sort()).toEqual(["20261020", "notes"]);
    expect(pruneAttachments(path.join(home, "none"))).toBe(0);
  });
});
