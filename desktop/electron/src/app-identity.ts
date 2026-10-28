// Windows' name for the app, its Application User Model ID. Windows shows a notification only for an
// ID that a Start menu shortcut carries, labels it with that shortcut's name and icon, and groups a
// window on the taskbar with the pinned shortcut of the same ID. Electron otherwise names the app
// `electron.app.Mightling`, which no shortcut carries, and stock NSIS cannot write the ID into the
// shortcut installer.nsi makes. So, on Windows, the app takes its identifier (app.json) as its ID, and
// the packaged app writes the same ID into the Start menu entry and any taskbar pin that start this
// very executable (a pin or an entry of another copy is left alone). Only a missing ID is written,
// so a start with everything in place reads two shortcuts and writes nothing.

import fs from "node:fs";
import path from "node:path";
import { app, shell } from "electron";

import appConfig from "../app.json";
import predecessor from "../predecessor.json";
import { pathKey } from "./platform";

export const APP_USER_MODEL_ID: string = appConfig.identifier;

/** The shortcuts that may start the app: the Start menu entry installer.nsi creates, and the user's taskbar pins. */
export function shortcutFolders(env: NodeJS.ProcessEnv): { entry: string | null; pins: string | null } {
  if (!env.APPDATA) return { entry: null, pins: null };
  return {
    entry: path.win32.join(env.APPDATA, "Microsoft", "Windows", "Start Menu", "Programs", `${appConfig.productName}.lnk`),
    pins: path.win32.join(env.APPDATA, "Microsoft", "Internet Explorer", "Quick Launch", "User Pinned", "TaskBar"),
  };
}

/** Whether a shortcut starts `exe` and lacks the app's ID. Pure. */
export function needsId(link: { target: string; appUserModelId?: string }, exe: string, platform: NodeJS.Platform = process.platform): boolean {
  if (!link.target || link.appUserModelId === APP_USER_MODEL_ID) return false;
  return pathKey(link.target, platform) === pathKey(exe, platform);
}

/**
 * Whether a shortcut belongs to the app this one replaced (1.9.0 changed the identifier): it carries
 * one of the old IDs, or starts the executable of the old per-user install. A pin or entry of any
 * other copy is not the old app's, and is left alone. Pure.
 */
export function isPredecessorLink(link: { target: string; appUserModelId?: string }, env: NodeJS.ProcessEnv): boolean {
  if (link.appUserModelId && predecessor.appUserModelIds.includes(link.appUserModelId)) return true;
  if (!link.target || !env.LOCALAPPDATA) return false;
  const old = path.win32.join(env.LOCALAPPDATA, predecessor.installedExe);
  return pathKey(link.target, "win32") === pathKey(old, "win32");
}

/** What claimAppIdentity touches, Electron's and the file system's by default (tests give stand-ins). */
export interface IdentityHost {
  platform: NodeJS.Platform;
  packaged: boolean;
  exe: string;
  env: NodeJS.ProcessEnv;
  setId(id: string): void;
  list(folder: string): string[];
  read(file: string): { target: string; appUserModelId?: string };
  /** Writes the ID into a shortcut, its target as read (Electron's "update" wants one). */
  update(file: string, target: string, id: string): boolean;
}

const electronHost = (): IdentityHost => ({
  platform: process.platform,
  packaged: app.isPackaged,
  exe: process.execPath,
  env: process.env,
  setId: (id) => app.setAppUserModelId(id),
  list: (folder) => fs.readdirSync(folder),
  read: (file) => shell.readShortcutLink(file),
  update: (file, target, id) => shell.writeShortcutLink(file, "update", { target, appUserModelId: id }),
});

/**
 * On Windows: the app's ID for this process, and in the shortcuts that start this executable.
 * Returns the shortcuts it wrote. Never throws: a shortcut it cannot read or write is skipped.
 */
export function claimAppIdentity(host: IdentityHost = electronHost()): string[] {
  if (host.platform !== "win32") return [];
  host.setId(APP_USER_MODEL_ID);
  // A development run's executable is Electron's own, which no shortcut of the app starts.
  if (!host.packaged) return [];
  const { entry, pins } = shortcutFolders(host.env);
  const candidates: string[] = entry ? [entry] : [];
  // The desktop shortcut installer.nsi makes.
  if (host.env.USERPROFILE) candidates.push(path.win32.join(host.env.USERPROFILE, "Desktop", `${appConfig.productName}.lnk`));
  if (pins) {
    try {
      candidates.push(...host.list(pins).filter((name) => name.toLowerCase().endsWith(".lnk")).map((name) => path.win32.join(pins, name)));
    } catch {
      // No pins folder: nothing pinned.
    }
  }
  const written: string[] = [];
  for (const file of candidates) {
    try {
      const link = host.read(file);
      // The old app's pin or entry follows the app: pointed at this executable, with the new ID.
      if (isPredecessorLink(link, host.env) && pathKey(link.target, host.platform) !== pathKey(host.exe, host.platform)) {
        if (host.update(file, host.exe, APP_USER_MODEL_ID)) written.push(file);
        continue;
      }
      if (needsId(link, host.exe, host.platform) && host.update(file, link.target, APP_USER_MODEL_ID)) written.push(file);
    } catch {
      // Missing, or not a shortcut Windows can read: left as it is.
    }
  }
  return written;
}
