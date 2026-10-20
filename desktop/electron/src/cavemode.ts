// How terse answers are (specs/DREAMFERENCE_MIGHTLING_CAVE_MODE.md §11.5): Settings › General shows the
// level new sessions start at and changes it, and `/cavemode` in the composer does the same for a
// thread. Asked of and written by `ling cavemode`, the launcher's own resolver and writer, as `ling web`
// does for a browser (ling-rs/web/src/cave.rs), so the level's tiers have no copy here.

import { execFile } from "node:child_process";
import { command } from "./platform";

import type { CaveMode } from "./api";

export const LEVELS = ["off", "lite", "full", "ultra"] as const;
export const BUILT_IN = "built-in";

const THREAD = /^[A-Za-z0-9_-]{1,128}$/;

/** `ling cavemode`'s arguments for a change: one thread's level, or the default (`built-in` drops it). */
export function changeArgs(level: string, thread: string | null): string[] {
  const name = level.toLowerCase();
  if (thread !== null) {
    if (!THREAD.test(thread)) throw new Error("no such thread");
    if (!(LEVELS as readonly string[]).includes(name)) throw new Error("the level is off, lite, full or ultra");
    return ["cavemode", "--thread", thread, name];
  }
  if (!(LEVELS as readonly string[]).includes(name) && name !== BUILT_IN) throw new Error("the level is off, lite, full, ultra or built-in");
  return ["cavemode", "default", name];
}

function run(ling: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const exec = command(ling, args);
    execFile(exec.file, exec.args, { timeout: 15_000, shell: exec.shell, windowsHide: true }, (error, stdout, stderr) => {
      if (error) reject(new Error(String(stderr || stdout || error.message).trim()));
      else resolve(String(stdout).trim());
    });
  });
}

/** The level in force for `thread`, or for a new session. */
export async function cavemodeStatus(ling: string, thread: string | null): Promise<CaveMode> {
  const args = ["cavemode", ...(thread && THREAD.test(thread) ? ["--thread", thread] : []), "--json"];
  return JSON.parse(await run(ling, args)) as CaveMode;
}

/** Sets the level; resolves with what `ling cavemode` printed. */
export function cavemodeSet(ling: string, level: string, thread: string | null): Promise<string> {
  return run(ling, changeArgs(level, thread));
}
