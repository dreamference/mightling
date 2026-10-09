// Ask threads in the app (ask.ts, server.ts): the folders, the attachments, and a whole Ask thread
// through `AppServer` against a scripted `ling` that plays both `prompt show --composed` and the
// app-server. Everything lives in a scratch folder; no real `ling` and no model server is used.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { FILE_CAP, IMAGE_CAP, cleanFileName, composePrompt, folderOf, isFolder, linkThread, newFolder, writeUpload } from "./ask";
import type { ForView } from "./api";
import { AppServer } from "./server";

const scratches: string[] = [];
const scratch = (tag: string) => {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), `ling-app-ask-${tag}-`)));
  scratches.push(dir);
  return dir;
};
afterEach(() => {
  for (const dir of scratches.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

/**
 * A stand-in `ling`: `prompt show <name> --composed` prints a known text and where it ran; `app-server`
 * answers `thread/start` with a new thread whose `cwd` is the one it was given, and records every
 * line it reads.
 */
function scriptedLing(dir: string): string {
  const file = path.join(dir, "ling");
  fs.writeFileSync(file, `#!/usr/bin/env node
const fs = require("fs");
const args = process.argv.slice(2);
if (args[0] === "prompt") {
  process.stdout.write("COMPOSED " + args[2] + " IN " + process.cwd());
  process.exit(0);
}
const log = ${JSON.stringify(path.join(dir, "received.jsonl"))};
let buffer = "";
let threads = 0;
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let newline;
  while ((newline = buffer.indexOf("\\n")) >= 0) {
    const line = buffer.slice(0, newline);
    buffer = buffer.slice(newline + 1);
    fs.appendFileSync(log, line + "\\n");
    const message = JSON.parse(line);
    if (message.method === "thread/start") {
      const id = "thread-" + ++threads;
      process.stdout.write(JSON.stringify({ id: message.id, result: { thread: { id, cwd: message.params.cwd } } }) + "\\n");
    } else if ("id" in message && message.method) {
      process.stdout.write(JSON.stringify({ id: message.id, result: {} }) + "\\n");
    }
  }
});
`);
  fs.chmodSync(file, 0o755);
  return file;
}

const until = async (check: () => boolean, ms = 5000) => {
  const deadline = Date.now() + ms;
  while (!check()) {
    if (Date.now() > deadline) throw new Error("timed out");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
};

describe("Ask folders", () => {
  it("are private, named after their thread once it exists, and never outside the root", () => {
    const home = scratch("folders");
    const root = path.join(home, "ask");
    const folder = newFolder(root);
    expect(path.basename(folder)).toMatch(/^q-[0-9a-f]{16}$/);
    expect(fs.statSync(folder).mode & 0o777).toBe(0o700);
    expect(isFolder(root, folder)).toBe(true);
    expect(folderOf(root, "0199-thread")).toBeNull();
    linkThread(root, "0199-thread", folder);
    expect(fs.readlinkSync(path.join(root, "0199-thread"))).toBe(path.basename(folder));
    expect(folderOf(root, "0199-thread")).toBe(folder);
    // A second link for the same thread changes nothing; a path is not a thread id.
    linkThread(root, "0199-thread", folder);
    expect(() => linkThread(root, "../escape", folder)).toThrow();
    expect(folderOf(root, "../escape")).toBeNull();
    // A link planted in the root that points out of it is not an Ask folder.
    fs.symlinkSync(home, path.join(root, "planted"));
    expect(folderOf(root, "planted")).toBeNull();
    expect(isFolder(root, home)).toBe(false);
  });

  it("take attachments within the caps, under safe names, never over a file", () => {
    const home = scratch("upload");
    const root = path.join(home, "ask");
    const folder = newFolder(root);
    linkThread(root, "t1", folder);
    const data = Buffer.from("hello").toString("base64");
    const first = writeUpload(root, { thread: "t1", name: "../../etc/passwd", kind: "file", data });
    expect(first).toEqual({ path: path.join(folder, "passwd"), bytes: 5 });
    expect(fs.readFileSync(first.path, "utf8")).toBe("hello");
    expect(writeUpload(root, { thread: "t1", name: "passwd", kind: "file", data }).path).toBe(path.join(folder, "passwd-1"));
    expect(() => writeUpload(root, { thread: "other", name: "a", kind: "file", data })).toThrow(/not one/);
    expect(() => writeUpload(root, { thread: "t1", name: "a", kind: "video" as "file", data })).toThrow();
    const tooBig = Buffer.alloc(IMAGE_CAP + 1).toString("base64");
    expect(() => writeUpload(root, { thread: "t1", name: "big.png", kind: "image", data: tooBig })).toThrow(/20 MB/);
    expect(FILE_CAP).toBe(100 * 1024 * 1024);
    expect(cleanFileName(".bashrc")).toBe("bashrc");
    expect(cleanFileName("C:\\Users\\me\\photo.jpg")).toBe("photo.jpg");
    expect(cleanFileName("")).toBe("attachment");
    expect(cleanFileName("a\u0000b\nc.txt")).toBe("abc.txt");
  });

  it("compose their prompt with ling itself, in the Ask root", async () => {
    const dir = scratch("compose");
    const ling = scriptedLing(dir);
    const root = path.join(dir, "home", "ask");
    expect(await composePrompt(ling, "ask", root, process.env)).toBe(`COMPOSED ask IN ${root}`);
    await expect(composePrompt(path.join(dir, "missing"), "ask", root, process.env)).rejects.toThrow();
  });
});

describe("an Ask thread in the app", () => {
  const saved = { CODEX_HOME: process.env.CODEX_HOME, MIGHTLING_BIN: process.env.MIGHTLING_BIN };
  afterEach(() => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });

  it("gets the composed prompt, a fresh folder and the sandbox, and its folder is named after it", async () => {
    const dir = scratch("server");
    const home = path.join(dir, "home");
    process.env.CODEX_HOME = home;
    process.env.MIGHTLING_BIN = scriptedLing(dir);
    const delivered: ForView[] = [];
    const server = new AppServer(null, { deliver: (message) => delivered.push(message), busyChanged: () => {} });
    const started = server.start();
    expect(started.ask_root).toBe(path.join(home, "ask"));
    try {
      // The page names the prompt and a folder of its own choosing; the policy decides both.
      await server.send({ id: 1, method: "thread/start", params: { model: "m", prompt: "ask", cwd: "/etc", baseInstructions: "evil" } });
      await until(() => delivered.some((message) => message.channel === "work://message"));
      const received = fs.readFileSync(path.join(dir, "received.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
      const params = received[0].params;
      expect(params.baseInstructions).toBe(`COMPOSED ask IN ${path.join(home, "ask")}`);
      expect(params.sandbox).toBe("workspace-write");
      expect(params.prompt).toBeUndefined();
      expect(path.dirname(params.cwd)).toBe(path.join(home, "ask"));
      expect(path.basename(params.cwd)).toMatch(/^q-[0-9a-f]{16}$/);
      // Answered, the folder is named after the thread, and attachments land in it.
      await until(() => folderOf(path.join(home, "ask"), "thread-1") !== null);
      expect(folderOf(path.join(home, "ask"), "thread-1")).toBe(params.cwd);
      const uploaded = server.upload({ thread: "thread-1", name: "shot.png", kind: "image", data: Buffer.from("png").toString("base64") });
      expect(uploaded.path).toBe(path.join(params.cwd, "shot.png"));
      // Resumed, it stays in its folder whatever the page asks; a prompt on resume is refused.
      await server.send({ id: 2, method: "thread/resume", params: { threadId: "thread-1", cwd: "/elsewhere", sandbox: "danger-full-access" } });
      await expect(server.send({ id: 3, method: "thread/resume", params: { threadId: "thread-1", prompt: "ask" } })).rejects.toThrow();
      // Work goes on as before: a project folder is left alone.
      await server.send({ id: 4, method: "thread/start", params: { model: "m", cwd: "/work/repo" } });
      await until(() => fs.readFileSync(path.join(dir, "received.jsonl"), "utf8").trim().split("\n").length >= 3);
      const all = fs.readFileSync(path.join(dir, "received.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
      expect(all[1].params).toEqual({ threadId: "thread-1", cwd: params.cwd, sandbox: "workspace-write" });
      expect(all[2].params).toEqual({ model: "m", cwd: "/work/repo" });
      // Nothing outside the allow-list reaches the server.
      await expect(server.send({ id: 5, method: "feedback/upload", params: {} })).rejects.toThrow();
    } finally {
      server.stop();
    }
  });
});
