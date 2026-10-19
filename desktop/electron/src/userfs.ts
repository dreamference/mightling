// The main process's reads of the user's folders (a thread's project, a repository, its worktrees),
// never on the main thread and never unbounded. On macOS the first access to a folder under
// ~/Documents, ~/Desktop or ~/Downloads waits on the system's "would like to access files" prompt
// until it is answered; a synchronous call there froze the whole app, its window and SIGTERM
// included (the 1.7.0 Mac test, `repositories()` walking ~/Downloads). Here every call goes through
// `fs.promises`, which runs on libuv's thread pool, and gives up after `USER_FS_TIMEOUT_MS`, so an
// IPC reply comes back either way. No Electron here.

import type { Dirent, Stats } from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";

/** The calls this module makes, which a test can replace (`setUserFs`). */
export interface UserFsCalls {
  stat(place: string): Promise<Stats>;
  lstat(place: string): Promise<Stats>;
  readdir(place: string): Promise<Dirent[]>;
  realpath(place: string): Promise<string>;
  readFile(place: string): Promise<string>;
  copyFile(from: string, to: string): Promise<void>;
  mkdir(place: string): Promise<void>;
}

const REAL: UserFsCalls = {
  stat: (place) => fsp.stat(place),
  lstat: (place) => fsp.lstat(place),
  readdir: (place) => fsp.readdir(place, { withFileTypes: true }),
  realpath: (place) => fsp.realpath(place),
  readFile: (place) => fsp.readFile(place, "utf8"),
  // Never over a file that is there.
  copyFile: (from, to) => fsp.copyFile(from, to, fsp.constants.COPYFILE_EXCL),
  mkdir: async (place) => {
    await fsp.mkdir(place, { recursive: true });
  },
};

let calls: UserFsCalls = REAL;

/** How long one call may take before it is given up (a permission prompt nobody answers, a hung network share). */
export let USER_FS_TIMEOUT_MS = 10_000;

/** Replaces the calls and the time limit (tests); null puts the real ones back. */
export function setUserFs(replacement: Partial<UserFsCalls> | null, timeoutMs = 10_000): void {
  calls = replacement ? { ...REAL, ...replacement } : REAL;
  USER_FS_TIMEOUT_MS = timeoutMs;
}

export class UserFsTimeout extends Error {}

/** `call`, or a `UserFsTimeout` once the time limit passes (the call itself goes on in the pool). */
function bounded<T>(what: string, place: string, call: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new UserFsTimeout(`${place} did not answer within ${Math.round(USER_FS_TIMEOUT_MS / 1000)} s (${what}); on a Mac, answer the folder-access prompt`)),
      USER_FS_TIMEOUT_MS,
    );
    call().then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

/** The entry, or null when there is none (or it cannot be read); a timeout is thrown. */
export async function statOrNull(place: string): Promise<Stats | null> {
  try {
    return await bounded("stat", place, () => calls.stat(place));
  } catch (error) {
    if (error instanceof UserFsTimeout) throw error;
    return null;
  }
}

/** Like `statOrNull`, without following a final link. */
export async function lstatOrNull(place: string): Promise<Stats | null> {
  try {
    return await bounded("lstat", place, () => calls.lstat(place));
  } catch (error) {
    if (error instanceof UserFsTimeout) throw error;
    return null;
  }
}

export async function exists(place: string): Promise<boolean> {
  return (await statOrNull(place)) !== null;
}

export async function isDirectory(place: string): Promise<boolean> {
  return (await statOrNull(place))?.isDirectory() ?? false;
}

export function readdir(place: string): Promise<Dirent[]> {
  return bounded("readdir", place, () => calls.readdir(place));
}

export function readText(place: string): Promise<string> {
  return bounded("read", place, () => calls.readFile(place));
}

export function copyFile(from: string, to: string): Promise<void> {
  return bounded("copy", from, () => calls.copyFile(from, to));
}

export function mkdir(place: string): Promise<void> {
  return bounded("mkdir", place, () => calls.mkdir(place));
}

/** A path as the file system has it: links followed, short names expanded (`platform.ts`'s `realPath`, off the main thread). */
export async function realPath(place: string): Promise<string> {
  return path.resolve(await bounded("realpath", place, () => calls.realpath(path.resolve(place))));
}

/** `realPath` for a place that exists, else the path resolved as it is. */
export async function resolvedPath(place: string): Promise<string> {
  try {
    return await realPath(place);
  } catch (error) {
    if (error instanceof UserFsTimeout) throw error;
    return path.resolve(place);
  }
}

/** A path as `samePath` compares it: real, and folded to lower case on Windows. */
export async function pathKey(place: string, platform: NodeJS.Platform = process.platform): Promise<string> {
  const real = await resolvedPath(place);
  return platform === "win32" ? real.toLowerCase() : real;
}

/** Whether two paths name the same place (case-insensitively on Windows). */
export async function samePath(a: string, b: string, platform: NodeJS.Platform = process.platform): Promise<boolean> {
  return (await pathKey(a, platform)) === (await pathKey(b, platform));
}
