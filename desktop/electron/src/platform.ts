// What differs on Windows for the main process (DESKTOP_PARITY Phase 3 item 12, the 1.7.1 port):
// comparing paths (git answers with forward slashes and long names, Node's temporary folder can be
// an 8.3 short name, and case does not matter), running a `.cmd` (a script stand-in, or a wrapper a
// user put on Path) where an `.exe` is usual, and the shell a project's task runs in. No Electron here.

import fs from "node:fs";
import path from "node:path";

const windows = (platform: NodeJS.Platform) => platform === "win32";

/** A path as the file system has it: links followed, short names expanded, separators the platform's. */
export function realPath(place: string): string {
  return path.resolve(fs.realpathSync.native(path.resolve(place)));
}

/** Whether two paths name the same place (case-insensitively on Windows). */
export function samePath(a: string, b: string, platform: NodeJS.Platform = process.platform): boolean {
  const left = realPath(a);
  const right = realPath(b);
  return windows(platform) ? left.toLowerCase() === right.toLowerCase() : left === right;
}

/** A path as `samePath` compares it: real, and folded to lower case on Windows. */
export function pathKey(place: string, platform: NodeJS.Platform = process.platform): string {
  const real = fs.existsSync(place) ? realPath(place) : path.resolve(place);
  return windows(platform) ? real.toLowerCase() : real;
}

/** One argument quoted for cmd.exe, for running a `.cmd`/`.bat` file (Node runs those only through a shell). */
function quoteForCmd(arg: string): string {
  if (/^[\w./:=@,+-]+$/.test(arg)) return arg;
  return `"${arg.replace(/"/g, '""')}"`;
}

/**
 * How to start `file` with `args`: as it is, or on Windows, for a `.cmd`/`.bat`, through cmd.exe
 * with each argument quoted (Node refuses to start those files without a shell).
 */
export function command(file: string, args: string[], platform: NodeJS.Platform = process.platform): { file: string; args: string[]; shell: boolean } {
  if (windows(platform) && /\.(cmd|bat)$/i.test(file)) return { file: quoteForCmd(file), args: args.map(quoteForCmd), shell: true };
  return { file, args, shell: false };
}

/** The shell a project's task runs in, and how it is handed a script: the user's login shell, or cmd.exe on Windows. */
export function taskShell(script: string, platform: NodeJS.Platform = process.platform, env: NodeJS.ProcessEnv = process.env): { file: string; args: string[] } {
  if (windows(platform)) return { file: env.ComSpec || "cmd.exe", args: ["/d", "/s", "/c", script] };
  return { file: env.SHELL || "/bin/bash", args: ["-l", "-c", script] };
}

/** A path relative to a folder, with forward slashes whatever the platform (for files the page shows and compares). */
export function slashed(relative: string): string {
  return relative.split(path.sep).join("/");
}
