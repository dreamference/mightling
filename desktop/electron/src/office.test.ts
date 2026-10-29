// Office files previewed (DESKTOP §16 B31): which files, the sandboxed command, and the conversion with a stand-in LibreOffice.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { conversionCommand, findOffice, isOfficeFile, officePreview, onPath } from "./office";

describe("office previews", () => {
  it("convert office documents only", () => {
    expect(isOfficeFile("/d/report.docx")).toBe(true);
    expect(isOfficeFile("/d/sheet.ODS")).toBe(true);
    expect(isOfficeFile("/d/notes.md")).toBe(false);
    expect(isOfficeFile("/d/docx")).toBe(false);
  });

  // bubblewrap is Linux's, and officePreview builds this command only there; on Windows the profile's
  // path would come out with backslashes.
  it.skipIf(process.platform === "win32")("run LibreOffice in bubblewrap with the network unshared, the root read-only, a profile of its own", () => {
    const { command, args } = conversionCommand("/usr/bin/bwrap", "/usr/bin/soffice", "/d/a.docx", "/tmp/s");
    expect(command).toBe("/usr/bin/bwrap");
    expect(args).toContain("--unshare-net");
    expect(args.slice(0, 3)).toEqual(["--ro-bind", "/", "/"]);
    expect(args).toContain("-env:UserInstallation=file:///tmp/s/profile");
    expect(args.slice(-6)).toEqual(["--nolockcheck", "--convert-to", "pdf", "--outdir", "/tmp/s/out", "/d/a.docx"]);
  });

  // A read-only root still lets a process connect to a socket that has a path: the session bus under
  // /run/user would start a process outside the sandbox, with the network.
  it.skipIf(process.platform === "win32")("hide the sockets under /run from LibreOffice", () => {
    const { args } = conversionCommand("/usr/bin/bwrap", "/usr/bin/soffice", "/d/a.docx", "/tmp/s");
    const run = args.findIndex((arg, i) => arg === "--tmpfs" && args[i + 1] === "/run");
    expect(run).toBeGreaterThan(2);
    expect(run).toBeLessThan(args.indexOf("--bind"));
  });

  // The same holds for a socket under the user's home (an agent's, an editor's), and the home holds the
  // user's keys; LibreOffice sees an empty one, with the scratch folder, the file and the command still bound.
  it.skipIf(process.platform === "win32")("hide the user's home from LibreOffice", () => {
    const { args } = conversionCommand("/usr/bin/bwrap", "/usr/bin/soffice", "/h/u/d/a.docx", "/h/u/tmp/s", "/h/u");
    const home = args.findIndex((arg, i) => arg === "--tmpfs" && args[i + 1] === "/h/u");
    expect(home).toBeGreaterThan(2);
    expect(home).toBeLessThan(args.indexOf("--bind"));
    expect(args.slice(args.indexOf("--bind"), args.indexOf("--bind") + 3)).toEqual(["--bind", "/h/u/tmp/s", "/h/u/tmp/s"]);
    expect(args).toContain("/h/u/d/a.docx");
  });

  // A LibreOffice installed under the home (unpacked there, or linked from ~/.local/bin) stays visible:
  // its installation folder is bound read-only and run by its real path.
  it.skipIf(process.platform === "win32")("keep a LibreOffice installed under the home", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "office-home-"));
    try {
      const program = path.join(home, "opt", "libreoffice", "program");
      fs.mkdirSync(program, { recursive: true });
      fs.writeFileSync(path.join(program, "soffice"), "");
      fs.mkdirSync(path.join(home, "bin"));
      fs.symlinkSync(path.join(program, "soffice"), path.join(home, "bin", "soffice"));
      const real = fs.realpathSync(path.join(program, "soffice"));
      const { args } = conversionCommand("/usr/bin/bwrap", path.join(home, "bin", "soffice"), "/d/a.docx", "/tmp/s", home);
      const install = path.dirname(path.dirname(real));
      const bound = args.findIndex((arg, i) => arg === "--ro-bind" && args[i + 1] === install && args[i + 2] === install);
      expect(bound).toBeGreaterThan(args.findIndex((arg, i) => arg === "--tmpfs" && args[i + 1] === home));
      expect(args[args.indexOf("--new-session") + 7]).toBe(real);
    } finally {
      fs.rmSync(home, { recursive: true, force: true });
    }
  });

  it("say so without LibreOffice, and refuse what is not an office file", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "office-test-"));
    const file = path.join(dir, "a.docx");
    fs.writeFileSync(file, "x");
    try {
      expect(findOffice({ PATH: dir })).toBeNull();
      expect(await officePreview(file, { PATH: dir }, "linux")).toEqual({ unavailable: expect.stringContaining("needs LibreOffice") });
      await expect(officePreview(path.join(dir, "a.md"), { PATH: dir }, "linux")).rejects.toThrow("not an office file");
      await expect(officePreview("a.docx", { PATH: dir }, "linux")).rejects.toThrow("not an office file");
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it.skipIf(process.platform !== "linux" || !onPath(["bwrap"]))("convert with a stand-in LibreOffice that cannot reach the network", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "office-test-"));
    const file = path.join(dir, "Report One.docx");
    fs.writeFileSync(file, "x");
    const soffice = path.join(dir, "soffice");
    // Writes <outdir>/<name>.pdf, saying in it whether a socket to the outside could be made.
    fs.writeFileSync(soffice, `#!/bin/bash
out=""; prev=""; for a in "$@"; do [ "$prev" = "--outdir" ] && out="$a"; prev="$a"; last="$a"; done
name=$(basename "$last"); name="\${name%.*}"
if (exec 3<>/dev/tcp/1.1.1.1/80) 2>/dev/null; then net=reached; else net=none; fi
printf '%%PDF-1.4 %s\\n' "$net" > "$out/$name.pdf"
`);
    fs.chmodSync(soffice, 0o755);
    try {
      const answer = await officePreview(file, { PATH: process.env.PATH, MIGHTLING_SOFFICE: soffice }, "linux");
      expect("pdf" in answer).toBe(true);
      const text = Buffer.from((answer as { pdf: string }).pdf, "base64").toString();
      expect(text.startsWith("%PDF")).toBe(true);
      expect(text).toContain("none");
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
