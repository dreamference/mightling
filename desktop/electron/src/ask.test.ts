// Ask threads in the app (ask.ts, server.ts): the folders, the attachments, and a whole Ask thread
// through `AppServer` against a scripted `ling` that plays both `prompt show --composed` and the
// app-server. Everything lives in a scratch folder; no real `ling` and no model server is used.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { FILE_CAP, IMAGE_CAP, INCOGNITO_MARK, cleanFileName, composePrompt, folderOf, forgetIncognito, isFolder, isWorkFolder, linkThread, MessagePrompts, markIncognito, newFolder, sweepIncognito, writeUpload } from "./ask";
import type { ForView } from "./api";
import { samePath } from "./platform";
import { AppServer } from "./server";
import { nodeScript } from "./test-ling";

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
  return nodeScript(dir, "ling", `const fs = require("fs");
const args = process.argv.slice(2);
if (args[0] === "prompt") {
  process.stdout.write("COMPOSED " + args[2] + " IN " + process.cwd());
  process.exit(0);
}
if (args[0] === "airgapped") {
  process.stdout.write('{"level":"' + (process.env.STUB_AIRGAPPED || "off") + '","tier":"default","source":"stub","invalid":[]}\\n');
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
    // Windows has no POSIX modes; the folder is private there by the profile's own ACLs.
    if (process.platform !== "win32") expect(fs.statSync(folder).mode & 0o777).toBe(0o700);
    expect(isFolder(root, folder)).toBe(true);
    expect(folderOf(root, "0199-thread")).toBeNull();
    linkThread(root, "0199-thread", folder);
    // A relative link here; on Windows a junction, which always holds the absolute target.
    const target = fs.readlinkSync(path.join(root, "0199-thread"));
    if (process.platform === "win32") expect(samePath(target, folder)).toBe(true);
    else expect(target).toBe(path.basename(folder));
    expect(folderOf(root, "0199-thread")).toBe(folder);
    // A second link for the same thread changes nothing; a path is not a thread id.
    linkThread(root, "0199-thread", folder);
    expect(() => linkThread(root, "../escape", folder)).toThrow();
    expect(folderOf(root, "../escape")).toBeNull();
    // A link planted in the root that points out of it is not an Ask folder.
    fs.symlinkSync(home, path.join(root, "planted"), process.platform === "win32" ? "junction" : undefined);
    expect(folderOf(root, "planted")).toBeNull();
    expect(isFolder(root, home)).toBe(false);
  });

  it("take a Work folder as one outside the Ask root, existing and named in full (DESKTOP §16 B33)", () => {
    const home = scratch("work");
    const root = path.join(home, "ask");
    const question = newFolder(root);
    const repo = path.join(home, "repo");
    fs.mkdirSync(repo);
    expect(isWorkFolder(root, repo)).toBe(true);
    expect(isWorkFolder(root, question)).toBe(false);
    expect(isWorkFolder(root, root)).toBe(false);
    expect(isWorkFolder(root, path.join(home, "missing"))).toBe(false);
    expect(isWorkFolder(root, "repo")).toBe(false);
    fs.writeFileSync(path.join(home, "file"), "x");
    expect(isWorkFolder(root, path.join(home, "file"))).toBe(false);
    fs.symlinkSync(question, path.join(home, "into-ask"), process.platform === "win32" ? "junction" : undefined);
    expect(isWorkFolder(root, path.join(home, "into-ask"))).toBe(false);
    // Inside the root, a folder whose name begins with two dots is still inside.
    fs.mkdirSync(path.join(root, "..dots"));
    expect(isWorkFolder(root, path.join(root, "..dots"))).toBe(false);
    expect(new MessagePrompts(root, null).workFolder(repo)).toBe(true);
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

  it("keep a character the composed prompt's output splits between two writes", async () => {
    const dir = scratch("compose-split");
    // "é" is two bytes in UTF-8: the first ends one write, the second starts the next.
    const ling = nodeScript(dir, "ling", `process.stdout.write(Buffer.from([0x63, 0x61, 0x66, 0xc3]), () => setTimeout(() => process.stdout.write(Buffer.from([0xa9, 0x0a])), 50));
`);
    expect(await composePrompt(ling, "ask", path.join(dir, "ask"), process.env)).toBe("café\n");
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

describe("plugins and the air gap in the app (AIRGAPPED §14.12)", () => {
  const saved = { CODEX_HOME: process.env.CODEX_HOME, MIGHTLING_BIN: process.env.MIGHTLING_BIN, STUB_AIRGAPPED: process.env.STUB_AIRGAPPED };
  afterEach(() => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });

  it("asks ling for the level: at on a thread starts without plugins and nothing is installed; at off both pass", async () => {
    const dir = scratch("plugins");
    process.env.CODEX_HOME = path.join(dir, "home");
    process.env.MIGHTLING_BIN = scriptedLing(dir);
    const server = new AppServer(null, { deliver: () => {}, busyChanged: () => {} });
    server.start();
    const received = () => fs.readFileSync(path.join(dir, "received.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
    try {
      process.env.STUB_AIRGAPPED = "on";
      await server.send({ id: 1, method: "thread/start", params: { model: "m", cwd: "/work/repo", config: { "features.plugins": true } } });
      await expect(server.send({ id: 2, method: "plugin/install", params: { marketplacePath: "/m/marketplace.json", pluginName: "lint" } })).rejects.toThrow(/airgapped is on/);
      await expect(server.send({ id: 3, method: "marketplace/add", params: { source: "acme/plugins" } })).rejects.toThrow(/airgapped is on/);
      process.env.STUB_AIRGAPPED = "off";
      await server.send({ id: 4, method: "marketplace/add", params: { source: "acme/plugins" } });
      await server.send({ id: 5, method: "thread/start", params: { model: "m", cwd: "/work/repo" } });
      await until(() => fs.existsSync(path.join(dir, "received.jsonl")) && received().length >= 3);
      const [onStart, added, offStart] = received();
      expect(onStart.params).toEqual({ model: "m", cwd: "/work/repo", config: { "features.plugins": false } });
      expect(added).toMatchObject({ id: 4, method: "marketplace/add" });
      expect(offStart.params).toEqual({ model: "m", cwd: "/work/repo" });
    } finally {
      server.stop();
    }
  });
});

describe("incognito questions (DESKTOP_PARITY_CLAUDE §4.3)", () => {
  const question = (root: string, id: string) => {
    const folder = newFolder(root);
    linkThread(root, id, folder);
    fs.writeFileSync(path.join(folder, "notes.md"), "a file the agent wrote\n");
    return folder;
  };

  it("deletes a marked question's folder, files and link, and nothing else", () => {
    const root = path.join(scratch("incognito"), "ask");
    const secret = question(root, "t-secret");
    const kept = question(root, "t-kept");
    expect(markIncognito(root, "t-secret")).toBe(secret);
    expect(fs.existsSync(path.join(secret, INCOGNITO_MARK))).toBe(true);
    // A question that is not marked is never deleted this way.
    expect(() => forgetIncognito(root, "t-kept")).toThrow(/not incognito/);
    expect(forgetIncognito(root, "t-secret")).toBe(true);
    expect(fs.existsSync(secret)).toBe(false);
    expect(fs.existsSync(path.join(root, "t-secret"))).toBe(false);
    expect(() => fs.lstatSync(path.join(root, "t-secret"))).toThrow();
    expect(fs.readFileSync(path.join(kept, "notes.md"), "utf8")).toContain("agent");
    expect(folderOf(root, "t-kept")).toBe(kept);
    // Nothing left: nothing to do; a thread id that could leave the root is refused.
    expect(forgetIncognito(root, "t-secret")).toBe(false);
    expect(() => forgetIncognito(root, "../x")).toThrow(/not a thread id/);
    expect(() => markIncognito(root, "t-unknown")).toThrow(/only an Ask question/);
  });

  it("sweeps what a closed or crashed app left, keeping every other question", () => {
    const root = path.join(scratch("sweep"), "ask");
    const left = question(root, "t-left");
    const kept = question(root, "t-kept");
    markIncognito(root, "t-left");
    expect(sweepIncognito(root)).toBe(1);
    expect(fs.existsSync(left)).toBe(false);
    expect(() => fs.lstatSync(path.join(root, "t-left"))).toThrow();
    expect(folderOf(root, "t-kept")).toBe(kept);
    expect(sweepIncognito(root)).toBe(0);
    expect(sweepIncognito(path.join(root, "missing"))).toBe(0);
  });

  it("tracks readiness, busy turns, serverRequests, observers, protocol errors and incognito through AppServer", async () => {
    const dir = scratch("server-lifecycle");
    const lingPath = nodeScript(dir, "ling", `const fs = require("fs");
const args = process.argv.slice(2);
if (args[0] === "prompt") { process.stdout.write("COMPOSED " + args[2]); process.exit(0); }
if (args[0] === "airgapped") { process.stdout.write('{"level":"off","tier":"default","source":"stub","invalid":[]}\\n'); process.exit(0); }
let buf = "";
process.stdin.on("data", (chunk) => {
  buf += chunk;
  let nl;
  while ((nl = buf.indexOf("\\n")) >= 0) {
    const line = buf.slice(0, nl);
    buf = buf.slice(nl + 1);
    const msg = JSON.parse(line);
    if (msg.method === "initialize") {
      process.stdout.write(JSON.stringify({ id: msg.id, result: { userAgent: "ling" } }) + "\\n");
    } else if (msg.method === "thread/start") {
      process.stdout.write("\\n");
      process.stdout.write("not-json-at-all\\n");
      process.stdout.write(JSON.stringify({ id: msg.id, result: { thread: { id: "t-ask", cwd: msg.params.cwd } } }) + "\\n");
      process.stdout.write(JSON.stringify({ method: "turn/started", params: { threadId: "t-ask", turn: { id: "turn-1" } } }) + "\\n");
      process.stdout.write(JSON.stringify({ id: "req-1", method: "item/commandExecution/requestApproval", params: { threadId: "t-ask" } }) + "\\n");
      process.stdout.write(JSON.stringify({ method: "serverRequest/resolved", params: { requestId: "req-1" } }) + "\\n");
      process.stdout.write(JSON.stringify({ method: "turn/completed", params: { threadId: "t-ask", turn: { id: "turn-1" } } }) + "\\n");
    }
  }
});
`);
    const delivered: ForView[] = [];
    const observed: Record<string, unknown>[] = [];
    const busyStates: boolean[] = [];
    const server = new AppServer(dir, {
      deliver: (message) => delivered.push(message),
      busyChanged: (busy) => busyStates.push(busy),
    });
    server.observe((message) => {
      observed.push(message as Record<string, unknown>);
      return false;
    });
    const savedHome = process.env.CODEX_HOME;
    const savedBin = process.env.MIGHTLING_BIN;
    process.env.CODEX_HOME = dir;
    // On Windows the stand-in is ling.cmd, which the lookup for ling.exe does not find: name it.
    process.env.MIGHTLING_BIN = lingPath;
    try {
      expect(server.running).toBe(false);
      await expect(server.send({ id: 1, method: "thread/list", params: {} })).rejects.toThrow("not running");
      expect(server.start().started).toBe(true);
      expect(server.start().started).toBe(false);
      expect(server.running).toBe(true);
      expect(server.ready).toBe(false);

      await server.send({ id: 1, method: "initialize", params: {} });
      await until(() => delivered.some((m) => m.channel === "work://message" && typeof m.payload === "object" && m.payload !== null && "result" in m.payload));
      await server.send({ method: "initialized" });
      expect(server.ready).toBe(true);

      await server.send({ id: 2, method: "thread/start", params: { prompt: "ask" } });
      await until(() => busyStates.includes(true) && busyStates.at(-1) === false);
      expect(delivered.some((m) => m.channel === "work://protocol-error" && m.payload === "not-json-at-all")).toBe(true);
      expect(observed.some((m) => m.method === "turn/started")).toBe(true);

      expect(typeof server.incognito("mark", "t-ask")).toBe("string");
      expect(server.incognito("forget", "t-ask")).toBe(true);
      expect(server.incognito("sweep")).toBe(0);
    } finally {
      server.stop();
      if (savedHome === undefined) delete process.env.CODEX_HOME;
      else process.env.CODEX_HOME = savedHome;
      if (savedBin === undefined) delete process.env.MIGHTLING_BIN;
      else process.env.MIGHTLING_BIN = savedBin;
    }
  });
});
