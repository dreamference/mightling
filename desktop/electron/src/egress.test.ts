import { describe, expect, it, vi } from "vitest";

const defaultSession = vi.hoisted(() => ({
  setPermissionRequestHandler: vi.fn(),
  setPermissionCheckHandler: vi.fn(),
  setSpellCheckerDictionaryDownloadURL: vi.fn(),
  setSpellCheckerEnabled: vi.fn(),
}));

vi.mock("electron", () => ({ app: { commandLine: { appendSwitch: vi.fn() } }, session: { defaultSession } }));

import { app } from "electron";

import { SWITCHES, applySessionRules, applySwitches } from "./egress";

describe("the switches that keep Chromium quiet", () => {
  it("are the Codex app's background-networking set plus Cast discovery, always applied", () => {
    applySwitches("linux");
    const calls = (app.commandLine.appendSwitch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(calls).toEqual(SWITCHES.map(([name, value]) => (value === undefined ? [name] : [name, value])));
    const features = SWITCHES.find(([name]) => name === "disable-features")?.[1] ?? "";
    for (const feature of ["AimEnabled", "AutofillServerCommunication", "NetworkTimeServiceQuerying", "MediaRouter"]) {
      expect(features.split(",")).toContain(feature);
    }
    expect(SWITCHES.map(([name]) => name)).toContain("disable-background-networking");
    expect(SWITCHES.map(([name]) => name)).toContain("disable-component-update");
    expect(SWITCHES).toContainEqual(["blink-settings", "dnsPrefetchingEnabled=false"]);
    // No GPU switch: Electron's defaults, as the Codex app runs.
    expect(SWITCHES.some(([name]) => /gpu|angle|gl/.test(name))).toBe(false);
  });

  it("add the mock keychain on macOS only, so the app never waits on a keychain prompt", async () => {
    const { platformSwitches } = await import("./egress");
    expect(platformSwitches("darwin")).toEqual([["use-mock-keychain"]]);
    for (const platform of ["linux", "win32"] as const) expect(platformSwitches(platform)).toEqual([]);
    const append = app.commandLine.appendSwitch as unknown as { mock: { calls: unknown[][] }; mockClear(): void };
    append.mockClear();
    applySwitches("darwin");
    expect(append.mock.calls).toContainEqual(["use-mock-keychain"]);
    append.mockClear();
    applySwitches("linux");
    expect(append.mock.calls.some(([name]) => name === "use-mock-keychain")).toBe(false);
  });
});

describe("the permissions a page gets", () => {
  it("are the clipboard and, for the app's own page, the microphone alone", async () => {
    const { grantsCheck, grantsRequest } = await import("./egress");
    expect(grantsRequest("clipboard-sanitized-write", {})).toBe(true);
    expect(grantsRequest("media", { mediaTypes: ["audio"], requestingUrl: "app://-/index.html" })).toBe(true);
    expect(grantsRequest("media", { mediaTypes: ["audio", "video"], requestingUrl: "app://-/index.html" })).toBe(false);
    expect(grantsRequest("media", { mediaTypes: ["video"], requestingUrl: "app://-/index.html" })).toBe(false);
    expect(grantsRequest("media", { mediaTypes: [], requestingUrl: "app://-/index.html" })).toBe(false);
    expect(grantsRequest("media", { mediaTypes: ["audio"], requestingUrl: "https://example.com/" })).toBe(false);
    expect(grantsRequest("media", { mediaTypes: ["audio"], requestingUrl: "app://-evil/" })).toBe(false);
    for (const other of ["geolocation", "notifications", "display-capture", "clipboard-read", "openExternal"]) {
      expect(grantsRequest(other, { requestingUrl: "app://-/index.html" })).toBe(false);
    }
    expect(grantsCheck("media", "app://-", { mediaType: "audio" })).toBe(true);
    expect(grantsCheck("media", "app://-", { mediaType: "video" })).toBe(false);
    expect(grantsCheck("media", "http://localhost:3100", { mediaType: "audio" })).toBe(false);
    expect(grantsCheck("clipboard-sanitized-write", "app://-", {})).toBe(true);
  });
});

describe("the permissions a page gets, at their edges", () => {
  it("never follow a media type into another permission, nor a page's origin past the URL that asks", async () => {
    const { grantsCheck, grantsRequest } = await import("./egress");
    for (const other of ["geolocation", "notifications", "display-capture", "clipboard-read"]) {
      expect(grantsRequest(other, { mediaTypes: ["audio"], requestingUrl: "app://-/index.html" })).toBe(false);
      expect(grantsCheck(other, "app://-", { mediaType: "audio" })).toBe(false);
    }
    // The URL that asks decides; the origin stands in only when there is none.
    expect(grantsRequest("media", { mediaTypes: ["audio"], requestingUrl: "https://example.com/", securityOrigin: "app://-" })).toBe(false);
    expect(grantsRequest("media", { mediaTypes: ["audio"], securityOrigin: "app://-" })).toBe(true);
    expect(grantsRequest("media", { mediaTypes: ["audio"], securityOrigin: "https://example.com" })).toBe(false);
    // A check names the microphone or it is refused: no kind at all, or a screen, is not audio.
    expect(grantsCheck("media", "app://-", {})).toBe(false);
    expect(grantsCheck("media", "app://-", { mediaType: "unknown" })).toBe(false);
  });
});

describe("the session rules", () => {
  type RequestHandler = (contents: unknown, permission: string, callback: (granted: boolean) => void, details: unknown) => void;
  type CheckHandler = (contents: unknown, permission: string, origin: string, details: unknown) => boolean;

  it("answer every permission request and check by the same rule, and point spellcheck at the app's own scheme", () => {
    for (const fn of Object.values(defaultSession)) fn.mockReset();
    applySessionRules();
    const request = defaultSession.setPermissionRequestHandler.mock.calls[0][0] as RequestHandler;
    const check = defaultSession.setPermissionCheckHandler.mock.calls[0][0] as CheckHandler;
    const answer = (permission: string, details: unknown) => {
      const callback = vi.fn();
      request(null, permission, callback, details);
      expect(callback).toHaveBeenCalledTimes(1);
      return callback.mock.calls[0][0];
    };
    expect(answer("media", { mediaTypes: ["audio"], requestingUrl: "app://-/index.html" })).toBe(true);
    expect(answer("media", { mediaTypes: ["video"], requestingUrl: "app://-/index.html" })).toBe(false);
    expect(answer("geolocation", { requestingUrl: "app://-/index.html" })).toBe(false);
    expect(answer("media", { mediaTypes: ["audio"], requestingUrl: "https://example.com/" })).toBe(false);
    expect(check(null, "media", "app://-", { mediaType: "audio" })).toBe(true);
    expect(check(null, "media", "https://example.com", { mediaType: "audio" })).toBe(false);
    expect(check(null, "notifications", "app://-", {})).toBe(false);
    expect(defaultSession.setSpellCheckerDictionaryDownloadURL).toHaveBeenCalledWith("app://-/dictionaries/");
    expect(defaultSession.setSpellCheckerEnabled).not.toHaveBeenCalled();
  });

  it("turn spellcheck off when its download URL cannot be pointed away", () => {
    for (const fn of Object.values(defaultSession)) fn.mockReset();
    defaultSession.setSpellCheckerDictionaryDownloadURL.mockImplementation(() => {
      throw new Error("no spellchecker here");
    });
    applySessionRules();
    expect(defaultSession.setSpellCheckerEnabled).toHaveBeenCalledWith(false);
  });
});
