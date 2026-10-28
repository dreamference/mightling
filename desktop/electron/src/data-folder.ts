// The app's data folder (Electron's `userData`: Chromium's profile, the window state, the
// preferences), by one rule with `ling app`, which empties its `Cache` (ling-rs/src/app.rs,
// `data_dir_for`); both are tested against ling-rs/vectors/app-data-folder.json.
//
// On Windows it is `%LOCALAPPDATA%\<identifier>` (`%USERPROFILE%\AppData\Local\<identifier>`
// without the variable), where a Windows app keeps its own data. Up to 1.7.1 it was
// `<HOME or USERPROFILE>\.local\share\<identifier>` there too (WINDOWS_ARM §23.4 W4), and
// `settleDataFolder` moves that folder once, as the rename migration moved the Tauri app's folder
// (ling-rs/src/rename.rs, `rename_if_free`). Elsewhere it stays `~/.local/share/<identifier>`, the
// Tauri app's folder, where `ling app` and `ling-admin desktop` expect it. No Electron here, so the
// tests run the rule for every platform on any machine.

import fs from "node:fs";
import path from "node:path";

/** The environment variables the rule reads. */
export type Env = Record<string, string | undefined>;

function set(env: Env, name: string): string | null {
  const value = env[name];
  return value ? value : null;
}

/** `base` without separators at its end, then `parts`, joined with the platform's separator. */
function joined(base: string, windows: boolean, parts: string[]): string {
  const trimmed = base.replace(windows ? /[\\/]+$/ : /\/+$/, "");
  return [trimmed, ...parts].join(windows ? "\\" : "/");
}

/** The app's data folder on `platform` (a `process.platform` value) with `env`, or null without a home. */
export function dataFolder(platform: string, env: Env, identifier: string): string | null {
  if (platform === "win32") {
    const local = set(env, "LOCALAPPDATA");
    if (local) return joined(local, true, [identifier]);
    const profile = set(env, "USERPROFILE");
    return profile ? joined(profile, true, ["AppData", "Local", identifier]) : null;
  }
  const home = set(env, "HOME") ?? set(env, "USERPROFILE");
  return home ? joined(home, false, [".local", "share", identifier]) : null;
}

/** The folder 1.7.1 and earlier used on Windows, to be moved to `dataFolder`'s; null elsewhere. */
export function legacyDataFolder(platform: string, env: Env, identifier: string): string | null {
  if (platform !== "win32") return null;
  const home = set(env, "HOME") ?? set(env, "USERPROFILE");
  return home ? joined(home, true, [".local", "share", identifier]) : null;
}

/**
 * Every folder an earlier run of the app may have kept its data in, newest first: the Windows
 * `.local\share` folder of 1.7.1 under the current identifier, then each earlier identifier's
 * folder (the rename of 1.9.0 changed the identifier), and on Windows that identifier's 1.7.1 folder.
 */
export function predecessorDataFolders(platform: string, env: Env, identifier: string, earlier: readonly string[]): string[] {
  const found: string[] = [];
  for (const id of [identifier, ...earlier]) {
    const candidates = id === identifier ? [legacyDataFolder(platform, env, id)] : [dataFolder(platform, env, id), legacyDataFolder(platform, env, id)];
    for (const folder of candidates) if (folder && !found.includes(folder)) found.push(folder);
  }
  return found;
}

/**
 * The folder to use this run, moving the first of `predecessors` that exists to `current`
 * (`settleDataFolder`'s rules, for a list).
 */
export function settleFromPredecessors(
  predecessors: readonly string[],
  current: string,
  ops: FolderOps = fs,
  log: (line: string) => void = (line) => console.error(line),
): string {
  return settleDataFolder(predecessors.find((folder) => ops.existsSync(folder)) ?? null, current, ops, log);
}

/** The file system calls `settleDataFolder` makes, replaceable in tests. */
export interface FolderOps {
  existsSync(target: string): boolean;
  mkdirSync(target: string, options: { recursive: true }): unknown;
  renameSync(from: string, to: string): void;
}

/**
 * The folder to use this run, after moving `legacy` to `current` when only `legacy` exists.
 *
 * Runs before Electron is ready, so Chromium has opened neither. When both exist both stay and
 * `current` is used. When the move fails (`EXDEV` across volumes, a file held open by an older
 * app still running) nothing is lost: `legacy` stays where it is and is used this run, and the
 * next start tries again.
 */
export function settleDataFolder(
  legacy: string | null,
  current: string,
  ops: FolderOps = fs,
  log: (line: string) => void = (line) => console.error(line),
): string {
  if (!legacy || !ops.existsSync(legacy)) return current;
  if (ops.existsSync(current)) {
    log(`ling-app: ${legacy} is left as it is; the app's data is in ${current}`);
    return current;
  }
  try {
    ops.mkdirSync(path.dirname(current), { recursive: true });
    ops.renameSync(legacy, current);
    log(`ling-app: moved ${legacy} to ${current}`);
    return current;
  } catch (error) {
    const code = (error as NodeJS.ErrnoException)?.code;
    const why = code ?? (error instanceof Error ? error.message : String(error));
    log(`ling-app: could not move ${legacy} to ${current} (${why}); it is used where it is this time`);
    return legacy;
  }
}
