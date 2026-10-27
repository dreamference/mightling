// Ask threads' scratch folders in the app (specs/DREAMFERENCE_MIGHTLING_ASK.md §3.1), under
// `$CODEX_HOME/ask/`: a port of `ling-rs/web/src/ask.rs` and of `ling web`'s `/api/upload`, so a
// thread asked in the app and one asked in a browser live in the same place, the same way.
//
// The thread id does not exist yet when `thread/start` is vetted, and a running session's `cwd`
// cannot be renamed, so each folder is created under a random name and, once the server answers
// with the new thread's id, `ask/<thread-id>` is made a link to it. The link is also how an Ask
// thread is recognised later: a thread with one is resumed and forked in its folder, whatever the
// page asks for, and attachments for it land there. No Electron here, so this tests on its own.

import { spawn } from "node:child_process";
import { command } from "./platform";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import type { PromptSource } from "./policy";

/** The largest image an attachment may be (`ling web`'s `IMAGE_CAP`). */
export const IMAGE_CAP = 20 * 1024 * 1024;
/** The largest other file (`FILE_CAP`). */
export const FILE_CAP = 100 * 1024 * 1024;
/** Composing reads the apps' state and the code index; it never takes this long. */
export const COMPOSE_TIMEOUT_MS = 60_000;

/** Whether a thread id is safe as a file name here. */
export function validThreadId(id: string): boolean {
  return id.length > 0 && id.length <= 128 && /^[A-Za-z0-9_-]+$/.test(id);
}

/** The root of every Ask folder. */
export function askRoot(codexHome: string): string {
  return path.join(codexHome, "ask");
}

/** Creates a folder private to the user (0700), parents included. */
export function privateDir(folder: string): void {
  fs.mkdirSync(folder, { recursive: true });
  if (process.platform !== "win32") fs.chmodSync(folder, 0o700);
}

/** The root, created, as the server will name the threads' `cwd` (canonical). */
export function canonicalRoot(root: string): string {
  privateDir(root);
  return fs.realpathSync(root);
}

/** Creates a new, empty folder for one Ask thread. */
export function newFolder(root: string): string {
  privateDir(root);
  const folder = path.join(root, `q-${randomBytes(8).toString("hex")}`);
  fs.mkdirSync(folder, { mode: 0o700 });
  if (process.platform !== "win32") fs.chmodSync(folder, 0o700);
  return fs.realpathSync(folder);
}

const realDir = (folder: string): string | null => {
  try {
    const real = fs.realpathSync(folder);
    return fs.statSync(real).isDirectory() ? real : null;
  } catch {
    return null;
  }
};

/** Whether a path is one of the Ask folders: a folder whose parent is the root itself. */
export function isFolder(root: string, folder: string): boolean {
  const real = realDir(folder);
  const realRoot = realDir(root);
  return real !== null && realRoot !== null && path.dirname(real) === realRoot;
}

/** The folder of an Ask thread, or null for any other thread. */
export function folderOf(root: string, threadId: string): string | null {
  if (!validThreadId(threadId)) return null;
  const folder = realDir(path.join(root, threadId));
  const realRoot = realDir(root);
  return folder !== null && realRoot !== null && path.dirname(folder) === realRoot ? folder : null;
}

/**
 * Whether a path is a folder a Work thread may work in: an existing directory named by an absolute
 * path, neither the Ask root nor inside it, links resolved (`ling web`'s `is_work_folder`). An Ask
 * thread's fork may move there (a project's primary folder, DESKTOP §16 B33).
 */
export function isWorkFolder(root: string, folder: string): boolean {
  if (!path.isAbsolute(folder)) return false;
  const real = realDir(folder);
  if (real === null) return false;
  // Without a home folder there is no Ask root to stay out of.
  if (!root) return true;
  const relative = path.relative(realDir(root) ?? path.resolve(root), real);
  return relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative);
}

/** Names a folder after the thread that works in it: `ask/<thread-id>` → the folder. */
export function linkThread(root: string, threadId: string, folder: string): void {
  if (!validThreadId(threadId)) throw new Error(`not a thread id: ${threadId}`);
  const link = path.join(root, threadId);
  try {
    fs.lstatSync(link);
    return;
  } catch {
    // Not there yet.
  }
  // A relative link, as `ling web` makes; Windows gets a junction, which needs no privilege.
  if (process.platform === "win32") fs.symlinkSync(folder, link, "junction");
  else fs.symlinkSync(path.basename(folder), link);
}

/** The policy's view of the Ask folders while one message is vetted, with the prompt composed beforehand. */
export class MessagePrompts implements PromptSource {
  /** The folder a vetted `thread/start` created, if it created one. */
  created: string | null = null;

  constructor(
    private readonly root: string,
    private readonly prompt: { name: string; text: string } | null,
    /** The configured air-gap level was `on` when the message arrived (`ling airgapped`). */
    private readonly airgapped = false,
  ) {}

  composed(name: string): string {
    if (this.prompt && this.prompt.name === name) return this.prompt.text;
    throw new Error(`the prompt ${name} could not be composed`);
  }

  newScratchFolder(): string {
    try {
      this.created = newFolder(this.root);
    } catch (error) {
      throw new Error(`could not create an Ask folder: ${error instanceof Error ? error.message : String(error)}`);
    }
    return this.created;
  }

  scratchFolderOf(threadId: string): string | null {
    return folderOf(this.root, threadId);
  }

  workFolder(cwd: string): boolean {
    return isWorkFolder(this.root, cwd);
  }

  /** The level the host resolved before vetting: the named thread's for a `!` command, else the configured one. */
  airgappedOn(_threadId: string | null = null): boolean {
    return this.airgapped;
  }
}

/**
 * The system prompt a session under `name` receives: `ling prompt show <name> --composed`, run in
 * the Ask root with the app-server's own environment, as `ling web` runs it.
 */
export function composePrompt(ling: string, name: string, cwd: string, env: NodeJS.ProcessEnv, timeoutMs = COMPOSE_TIMEOUT_MS): Promise<string> {
  privateDir(cwd);
  return new Promise((resolve, reject) => {
    const run = command(ling, ["prompt", "show", name, "--composed"]);
    const child = spawn(run.file, run.args, { cwd, env, stdio: ["ignore", "pipe", "pipe"], shell: run.shell, windowsHide: true });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`composing the ${name} prompt took too long`));
    }, timeoutMs);
    child.stdout.on("data", (chunk) => (stdout += String(chunk)));
    child.stderr.on("data", (chunk) => (stderr += String(chunk)));
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(new Error(`could not run ${ling}: ${error.message}`));
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code !== 0 || stdout.trim() === "") reject(new Error(`the ${name} prompt could not be composed: ${stderr.trim()}`));
      else resolve(stdout);
    });
  });
}

/** A file name the page sent, made safe to write: its last component, printable, not hidden. */
export function cleanFileName(name: string): string {
  const base = name.split(/[/\\]/).pop() ?? "";
  // eslint-disable-next-line no-control-regex
  const cleaned = Array.from(base.replace(/[\u0000-\u001f\u007f-\u009f]/g, "")).slice(0, 128).join("").trim().replace(/^\.+/, "");
  return cleaned === "" ? "attachment" : cleaned;
}

/** A path in `folder` for `name` that does not exist yet. */
export function unusedPath(folder: string, name: string): string {
  const exists = (file: string) => {
    try {
      fs.lstatSync(file);
      return true;
    } catch {
      return false;
    }
  };
  const first = path.join(folder, name);
  if (!exists(first)) return first;
  const dot = name.lastIndexOf(".");
  const [stem, extension] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  for (let n = 1; ; n++) {
    const candidate = path.join(folder, `${stem}-${n}${extension}`);
    if (!exists(candidate)) return candidate;
  }
}

export interface Upload {
  thread: string;
  name: string;
  kind: "image" | "file";
  /** The bytes, base64: the window's messages are JSON. */
  data: string;
}

/** An attachment into an Ask thread's folder, as `ling web`'s `/api/upload` writes one. */
export function writeUpload(root: string, upload: Upload): { path: string; bytes: number } {
  if (upload.kind !== "image" && upload.kind !== "file") throw new Error("kind is image or file");
  const cap = upload.kind === "image" ? IMAGE_CAP : FILE_CAP;
  const folder = folderOf(root, typeof upload.thread === "string" ? upload.thread : "");
  if (!folder) throw new Error("Attachments go to an Ask thread, and that is not one.");
  if (typeof upload.data !== "string" || upload.data.length > Math.ceil(cap / 3) * 4) throw new Error(`The limit is ${cap >> 20} MB.`);
  const bytes = Buffer.from(upload.data, "base64");
  if (bytes.length > cap) throw new Error(`The limit is ${cap >> 20} MB.`);
  const file = unusedPath(folder, cleanFileName(typeof upload.name === "string" ? upload.name : ""));
  fs.writeFileSync(file, bytes, { flag: "wx", mode: 0o600 });
  return { path: file, bytes: bytes.length };
}

/** The mark in an incognito question's folder (DESKTOP_PARITY_CLAUDE §4.3): the folder goes when the question is left. */
export const INCOGNITO_MARK = ".mightling-incognito";

/** Marks an Ask thread's folder incognito; answers the folder. Only an Ask thread's folder can be marked. */
export function markIncognito(root: string, threadId: string): string {
  const folder = folderOf(root, threadId);
  if (!folder) throw new Error("only an Ask question can be incognito");
  fs.writeFileSync(path.join(folder, INCOGNITO_MARK), "This question is incognito: Mightling deletes this folder when you leave it.\n", { mode: 0o600 });
  return folder;
}

/** Removes a link under the root that names a folder (`ask/<thread-id>` → `q-…`), when it does. */
function unlinkTo(root: string, names: Set<string>): void {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isSymbolicLink()) continue;
    const link = path.join(root, entry.name);
    try {
      if (names.has(path.basename(fs.readlinkSync(link)))) fs.unlinkSync(link);
    } catch {
      // Gone already.
    }
  }
}

/**
 * Deletes an incognito question: its folder, with every file in it, and the link that names it.
 * Refuses a folder that is not marked incognito, so no other question can be deleted this way.
 * Answers false when there was nothing left to delete.
 */
export function forgetIncognito(root: string, threadId: string): boolean {
  if (!validThreadId(threadId)) throw new Error(`not a thread id: ${threadId}`);
  const folder = folderOf(root, threadId);
  if (!folder) return false;
  if (!fs.existsSync(path.join(folder, INCOGNITO_MARK))) throw new Error("this question is not incognito; it is kept");
  fs.rmSync(folder, { recursive: true, force: true });
  unlinkTo(root, new Set([path.basename(folder)]));
  return true;
}

/** Deletes every incognito folder left under the root (an app that quit or crashed while one was open); answers how many. */
export function sweepIncognito(root: string): number {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch {
    return 0;
  }
  const gone = new Set<string>();
  for (const entry of entries) {
    if (!entry.isDirectory() || !/^q-[0-9a-f]+$/.test(entry.name)) continue;
    const folder = path.join(root, entry.name);
    if (!fs.existsSync(path.join(folder, INCOGNITO_MARK))) continue;
    fs.rmSync(folder, { recursive: true, force: true });
    gone.add(entry.name);
  }
  if (gone.size) unlinkTo(root, gone);
  return gone.size;
}
