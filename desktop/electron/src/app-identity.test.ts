// The app's Application User Model ID on Windows (app-identity.ts): set for the process, and written
// into the shortcuts that start this executable, only where it is missing.
import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({ app: {}, shell: {} }));

import { APP_USER_MODEL_ID, claimAppIdentity, needsId, shortcutFolders, type IdentityHost } from "./app-identity";

const exe = "/apps/Mightling/App/Mightling.exe";
const appData = "/users/u/AppData/Roaming";

function host(links: Record<string, { target: string; appUserModelId?: string } | Error>, overrides: Partial<IdentityHost> = {}) {
  const ids: string[] = [];
  const updated: string[] = [];
  const { entry, pins } = shortcutFolders({ APPDATA: appData });
  const value: IdentityHost = {
    platform: "win32",
    packaged: true,
    exe,
    env: { APPDATA: appData },
    setId: (id) => ids.push(id),
    list: (folder) => {
      if (folder !== pins) throw new Error("no such folder");
      return Object.keys(links).filter((file) => file.startsWith(`${pins}\\`)).map((file) => file.slice(pins!.length + 1));
    },
    read: (file) => {
      const link = links[file];
      if (!link) throw new Error("not found");
      if (link instanceof Error) throw link;
      return link;
    },
    update: (file, target, id) => {
      expect(target).toBe(exe);
      updated.push(`${file}=${id}`);
      return true;
    },
    ...overrides,
  };
  return { value, ids, updated, entry: entry!, pins: pins! };
}

describe("the app's ID on Windows", () => {
  it("is the app's identifier", () => {
    expect(APP_USER_MODEL_ID).toBe("dev.dreamference.mightling");
  });

  it("finds the Start menu entry the installer makes and the taskbar pins in APPDATA", () => {
    expect(shortcutFolders({})).toEqual({ entry: null, pins: null });
    const { entry, pins } = shortcutFolders({ APPDATA: "C:\\Users\\u\\AppData\\Roaming" });
    expect(entry).toBe("C:\\Users\\u\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Mightling.lnk");
    expect(pins).toBe("C:\\Users\\u\\AppData\\Roaming\\Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\TaskBar");
  });

  it("needs writing only into a shortcut that starts this executable and lacks it", () => {
    expect(needsId({ target: exe }, exe, "win32")).toBe(true);
    expect(needsId({ target: exe.toUpperCase() }, exe, "win32")).toBe(true);
    expect(needsId({ target: exe.toUpperCase() }, exe, "linux")).toBe(false);
    expect(needsId({ target: exe, appUserModelId: APP_USER_MODEL_ID }, exe, "win32")).toBe(false);
    expect(needsId({ target: exe, appUserModelId: "electron.app.Mightling" }, exe, "win32")).toBe(true);
    expect(needsId({ target: "/elsewhere/Mightling.exe" }, exe, "win32")).toBe(false);
    expect(needsId({ target: "" }, exe, "win32")).toBe(false);
  });

  it("is set for the process and written where missing, into this executable's shortcuts only", () => {
    const { entry, pins } = shortcutFolders({ APPDATA: appData });
    const { value, ids, updated } = host({
      [entry!]: { target: exe },
      [`${pins}\\Mightling.lnk`]: { target: exe, appUserModelId: "electron.app.Mightling" },
      [`${pins}\\Other copy.lnk`]: { target: "/old/Mightling.exe" },
      [`${pins}\\Done.lnk`]: { target: exe, appUserModelId: APP_USER_MODEL_ID },
      [`${pins}\\Broken.lnk`]: new Error("not a shortcut"),
      [`${pins}\\notes.txt`]: { target: exe },
    });
    expect(claimAppIdentity(value)).toEqual([entry, `${pins}\\Mightling.lnk`]);
    expect(ids).toEqual([APP_USER_MODEL_ID]);
    expect(updated).toEqual([`${entry}=${APP_USER_MODEL_ID}`, `${pins}\\Mightling.lnk=${APP_USER_MODEL_ID}`]);
  });

  it("writes nothing when the shortcuts carry it, there are none, or the pins folder is missing", () => {
    const { entry } = shortcutFolders({ APPDATA: appData });
    expect(claimAppIdentity(host({ [entry!]: { target: exe, appUserModelId: APP_USER_MODEL_ID } }).value)).toEqual([]);
    expect(claimAppIdentity(host({}).value)).toEqual([]);
    const noPins = host({ [entry!]: { target: exe } }, { list: () => { throw new Error("missing"); } });
    expect(claimAppIdentity(noPins.value)).toEqual([entry]);
    // A write Windows refuses is not reported as written.
    expect(claimAppIdentity(host({ [entry!]: { target: exe } }, { update: () => false }).value)).toEqual([]);
  });

  it("touches no shortcut in a development run, and nothing at all off Windows", () => {
    const { entry } = shortcutFolders({ APPDATA: appData });
    const dev = host({ [entry!]: { target: exe } }, { packaged: false });
    expect(claimAppIdentity(dev.value)).toEqual([]);
    expect(dev.ids).toEqual([APP_USER_MODEL_ID]);
    expect(dev.updated).toEqual([]);
    const linux = host({ [entry!]: { target: exe } }, { platform: "linux" });
    expect(claimAppIdentity(linux.value)).toEqual([]);
    expect(linux.ids).toEqual([]);
  });
});
