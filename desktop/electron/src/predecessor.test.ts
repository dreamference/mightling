// The app's change of identity in 1.9.0 (Mightling to Ragon): the old data folder moves once, a
// pin or entry of the old app is pointed at the new one, and the package replaces the old one.
// The old names come from predecessor.json, the one file that holds them.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({ app: {}, shell: {} }));

import appConfig from "../app.json";
import predecessor from "../predecessor.json";
import { APP_USER_MODEL_ID, claimAppIdentity, isPredecessorLink, type IdentityHost } from "./app-identity";
import { dataFolder, predecessorDataFolders, settleFromPredecessors } from "./data-folder";

const quiet = () => ({ log: () => undefined });

describe("the identity change", () => {
  it("gives the app a new identifier and keeps the old one only in predecessor.json", () => {
    expect(appConfig.identifier).not.toBe(predecessor.identifier);
    expect(APP_USER_MODEL_ID).toBe(appConfig.identifier);
    expect(predecessor.appUserModelIds).toContain(predecessor.identifier);
    expect(predecessor.appUserModelIds).not.toContain(APP_USER_MODEL_ID);
    expect(predecessor.legacySchemes).not.toContain(appConfig.scheme);
    expect(predecessor.debPackages).not.toContain(appConfig.packageName);
  });

  it("lists the old folders after the new identifier's own 1.7.1 folder", () => {
    const env = { LOCALAPPDATA: "C:\\Users\\u\\AppData\\Local", USERPROFILE: "C:\\Users\\u" };
    expect(predecessorDataFolders("win32", env, appConfig.identifier, [predecessor.identifier])).toEqual([
      `C:\\Users\\u\\.local\\share\\${appConfig.identifier}`,
      `C:\\Users\\u\\AppData\\Local\\${predecessor.identifier}`,
      `C:\\Users\\u\\.local\\share\\${predecessor.identifier}`,
    ]);
    expect(predecessorDataFolders("linux", { HOME: "/home/u" }, appConfig.identifier, [predecessor.identifier])).toEqual([
      `/home/u/.local/share/${predecessor.identifier}`,
    ]);
    expect(predecessorDataFolders("linux", {}, appConfig.identifier, [predecessor.identifier])).toEqual([]);
  });

  it("moves the old app's data to the new folder once, and never over an existing one", () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "predecessor-"));
    const env = { HOME: home };
    const current = dataFolder("linux", env, appConfig.identifier)!;
    const old = dataFolder("linux", env, predecessor.identifier)!;
    fs.mkdirSync(old, { recursive: true });
    fs.writeFileSync(path.join(old, "prefs.json"), "kept");
    const earlier = predecessorDataFolders("linux", env, appConfig.identifier, [predecessor.identifier]);
    expect(settleFromPredecessors(earlier, current, fs, quiet().log)).toBe(current);
    expect(fs.readFileSync(path.join(current, "prefs.json"), "utf8")).toBe("kept");
    expect(fs.existsSync(old)).toBe(false);
    // A second run changes nothing; an old folder that reappears is left alone.
    fs.mkdirSync(old, { recursive: true });
    expect(settleFromPredecessors(earlier, current, fs, quiet().log)).toBe(current);
    expect(fs.existsSync(old)).toBe(true);
  });
});

const localAppData = "C:\\Users\\u\\AppData\\Local";
const oldExe = `${localAppData}\\${predecessor.installedExe}`;
const newExe = `${localAppData}\\Programs\\${appConfig.productName}\\App\\${appConfig.productName}.exe`;

describe("a shortcut of the old app", () => {
  it("is recognised by its old ID or by the old install's executable", () => {
    const env = { LOCALAPPDATA: localAppData };
    expect(isPredecessorLink({ target: oldExe }, env)).toBe(true);
    expect(isPredecessorLink({ target: oldExe.toUpperCase() }, env)).toBe(true);
    expect(isPredecessorLink({ target: "D:\\elsewhere.exe", appUserModelId: predecessor.identifier }, env)).toBe(true);
    expect(isPredecessorLink({ target: "D:\\dev\\Mightling.exe" }, env)).toBe(false);
    expect(isPredecessorLink({ target: newExe, appUserModelId: APP_USER_MODEL_ID }, env)).toBe(false);
    expect(isPredecessorLink({ target: "" }, {})).toBe(false);
  });

  it("is pointed at this executable, with the new ID, on a start (pins and entries), and then left alone", () => {
    const appData = "C:\\Users\\u\\AppData\\Roaming";
    const pins = `${appData}\\Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\TaskBar`;
    const links: Record<string, { target: string; appUserModelId?: string }> = {
      [`${pins}\\${predecessor.productName}.lnk`]: { target: oldExe, appUserModelId: predecessor.identifier },
      [`${pins}\\Notes.lnk`]: { target: "D:\\notes.exe" },
    };
    const updated: string[] = [];
    const host: IdentityHost = {
      platform: "win32",
      packaged: true,
      exe: newExe,
      env: { APPDATA: appData, LOCALAPPDATA: localAppData },
      setId: () => undefined,
      list: (folder) => {
        if (folder !== pins) throw new Error("no such folder");
        return Object.keys(links).map((file) => file.slice(pins.length + 1));
      },
      read: (file) => {
        const link = links[file];
        if (!link) throw new Error("not found");
        return link;
      },
      update: (file, target, id) => {
        links[file] = { target, appUserModelId: id };
        updated.push(`${file}=${target}|${id}`);
        return true;
      },
    };
    expect(claimAppIdentity(host)).toEqual([`${pins}\\${predecessor.productName}.lnk`]);
    expect(updated).toEqual([`${pins}\\${predecessor.productName}.lnk=${newExe}|${APP_USER_MODEL_ID}`]);
    expect(claimAppIdentity(host)).toEqual([]);
  });
});
