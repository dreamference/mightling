// Local environments (specs/DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md §2.6, §7 question 3): a
// project's setup script, run when a worktree is created for it, and its actions, the buttons that
// start a dev server or the tests. Read from both `.codex/` and `.mightling/` in the project, so a
// repository shared with Codex users needs nothing extra; written only to `.mightling/` (the owner,
// 2026-10-16), so Mightling never puts a vendor-named folder in a project. The vendor's generated
// file is not documented; this reads the shape its documentation describes (a setup script with
// per-platform overrides, actions with a name, an icon and a script with per-platform overrides)
// under the obvious names, and writes Mightling's own. No Electron here.

import { randomUUID } from "node:crypto";
import fsp from "node:fs/promises";
import path from "node:path";

import { parse, stringify } from "smol-toml";

import * as userFs from "./userfs";

export const ICONS = ["play", "test", "build", "debug", "terminal", "globe", "tool"] as const;
export type ActionIcon = (typeof ICONS)[number];

export interface Action {
  name: string;
  icon: ActionIcon;
  /** The script for this platform. */
  script: string;
}

export interface Environment {
  /** Where it was read from, relative to the project: `.mightling/environments/default.toml`. */
  file: string;
  /** `.mightling` or `.codex`. */
  folder: string;
  name: string;
  /** The setup script for this platform, "" for none. */
  setup: string;
  actions: Action[];
}

const FOLDERS = [".mightling", ".codex"];
// Forward slashes on every platform: the page shows these names and sends them back (path.join accepts them on Windows).
export const WRITE_FILE = path.posix.join(".mightling", "environments", "default.toml");

type Table = Record<string, unknown>;
const isTable = (value: unknown): value is Table => typeof value === "object" && value !== null && !Array.isArray(value);

function platformKey(platform: NodeJS.Platform): string {
  return platform === "darwin" ? "macos" : platform === "win32" ? "windows" : "linux";
}

/** A script with per-platform overrides: `script` (or `command`), overridden by `linux`/`macos`/`windows`. */
function scriptOf(value: unknown, platform: NodeJS.Platform): string {
  if (typeof value === "string") return value;
  if (!isTable(value)) return "";
  const own = value[platformKey(platform)];
  if (typeof own === "string" && own.trim()) return own;
  if (isTable(own) && typeof own.script === "string") return own.script;
  for (const key of ["script", "command", "cmd", "run"]) if (typeof value[key] === "string") return value[key] as string;
  return "";
}

/** One environment file's contents, in the shape above. */
export function readEnvironment(text: string, file: string, platform: NodeJS.Platform = process.platform): Environment {
  const data = parse(text) as Table;
  const folder = file.split(/[\\/]/)[0];
  const setup = scriptOf(data.setup ?? data.setup_script ?? data.setupScript, platform);
  const list = Array.isArray(data.actions) ? data.actions : [];
  const actions: Action[] = [];
  for (const entry of list) {
    if (!isTable(entry)) continue;
    const script = scriptOf(entry, platform);
    const name = typeof entry.name === "string" && entry.name.trim() ? entry.name.trim() : `Action ${actions.length + 1}`;
    const icon = ICONS.includes(entry.icon as ActionIcon) ? (entry.icon as ActionIcon) : "play";
    if (script.trim()) actions.push({ name, icon, script });
  }
  const name = typeof data.name === "string" && data.name.trim() ? data.name.trim() : path.basename(file, ".toml");
  return { file, folder, name, setup, actions };
}

/** Every environment of `project`, `.mightling/` first; a file that does not parse is reported, not fatal. */
export async function environments(project: string, platform: NodeJS.Platform = process.platform): Promise<{ found: Environment[]; problems: string[] }> {
  const found: Environment[] = [];
  const problems: string[] = [];
  for (const folder of FOLDERS) {
    const candidates = [path.posix.join(folder, "environment.toml")];
    try {
      for (const entry of (await userFs.readdir(path.join(project, folder, "environments"))).sort((a, b) => a.name.localeCompare(b.name))) {
        if (entry.name.endsWith(".toml")) candidates.push(path.posix.join(folder, "environments", entry.name));
      }
    } catch (error) {
      if (error instanceof userFs.UserFsTimeout) throw error;
      // No environments folder.
    }
    for (const file of candidates) {
      const full = path.join(project, file);
      const stat = await userFs.statOrNull(full);
      if (!stat?.isFile() || stat.size > 256 * 1024) continue;
      try {
        found.push(readEnvironment(await userFs.readText(full), file, platform));
      } catch (error) {
        problems.push(`${file}: ${error instanceof Error ? error.message.split("\n")[0] : String(error)}`);
      }
    }
  }
  return { found, problems };
}

export interface EnvironmentEdit {
  name: string;
  setup: string;
  actions: { name: string; icon: string; script: string }[];
}

/** `existing` (a script or a table of them) with this platform's script set to `script`: the key it was read from is the one replaced. */
function withScript(existing: unknown, script: string, platform: NodeJS.Platform): Table {
  const table: Table = isTable(existing) ? { ...existing } : {};
  const key = platformKey(platform);
  const own = table[key];
  if (typeof own === "string" && own.trim()) table[key] = script;
  else if (isTable(own) && typeof own.script === "string") table[key] = { ...own, script };
  else table[["script", "command", "cmd", "run"].find((name) => typeof table[name] === "string") ?? "script"] = script;
  return table;
}

/**
 * Writes Mightling's environment file, `.mightling/environments/default.toml`; never `.codex/`. The edit holds this
 * platform's scripts: the file's other keys, the other platforms' overrides among them, are kept, of the setup and of
 * each action still there (matched by name).
 */
export async function writeEnvironment(project: string, edit: unknown, platform: NodeJS.Platform = process.platform): Promise<string> {
  if (!isTable(edit)) throw new Error("an environment is an object");
  const name = typeof edit.name === "string" && edit.name.trim() ? edit.name.trim().slice(0, 100) : "Default";
  const setup = typeof edit.setup === "string" ? edit.setup.slice(0, 64 * 1024) : "";
  const actions = (Array.isArray(edit.actions) ? edit.actions : []).slice(0, 20).flatMap((entry) => {
    if (!isTable(entry) || typeof entry.script !== "string" || !entry.script.trim()) return [];
    return [{
      name: typeof entry.name === "string" && entry.name.trim() ? entry.name.trim().slice(0, 60) : "Action",
      icon: ICONS.includes(entry.icon as ActionIcon) ? (entry.icon as string) : "play",
      script: entry.script.slice(0, 16 * 1024),
    }];
  });
  const target = path.join(project, WRITE_FILE);
  // The project's folders may be links: the file goes inside the project, and replaces a link rather than writing through it.
  for (let folder = path.dirname(WRITE_FILE); folder !== "."; folder = path.dirname(folder)) {
    if ((await userFs.lstatOrNull(path.join(project, folder)))?.isSymbolicLink()) throw new Error(`${folder} is a link, not a folder of the project`);
  }
  // The file as it is, read only when it is a file of its own (never through a link) and parses.
  let before: Table = {};
  if ((await userFs.lstatOrNull(target))?.isFile()) {
    try {
      const parsed = parse(await userFs.readText(target));
      if (isTable(parsed)) before = parsed;
    } catch (error) {
      if (error instanceof userFs.UserFsTimeout) throw error;
    }
  }
  const document: Table = { ...before, name };
  delete document.setup_script;
  delete document.setupScript;
  delete document.setup;
  delete document.actions;
  if (setup.trim()) document.setup = withScript(before.setup ?? before.setup_script ?? before.setupScript, setup, platform);
  const unused = (Array.isArray(before.actions) ? before.actions : []).filter(isTable);
  if (actions.length) {
    document.actions = actions.map((action) => {
      const at = unused.findIndex((entry) => entry.name === action.name);
      const old = at >= 0 ? unused.splice(at, 1)[0] : {};
      return { ...withScript(old, action.script, platform), name: action.name, icon: action.icon };
    });
  }
  await userFs.mkdir(path.dirname(target));
  const header = "# Mightling's local environment for this project (setup when a worktree is made; actions in the\n# app's top bar). Read by the Mightling desktop app; .codex/ environments are read too.\n";
  const partial = `${target}.${process.pid}.${randomUUID()}.tmp`;
  try {
    await fsp.writeFile(partial, header + stringify(document) + "\n", { flag: "wx" });
    await fsp.rename(partial, target);
  } catch (error) {
    await fsp.rm(partial, { force: true });
    throw error;
  }
  return WRITE_FILE;
}
