import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({ app: { commandLine: { appendSwitch: vi.fn() } }, session: {} }));

import { app } from "electron";

import { SWITCHES, applySwitches } from "./egress";

describe("the switches that keep Chromium quiet", () => {
  it("are the Codex app's background-networking set plus Cast discovery, always applied", () => {
    applySwitches();
    const calls = (app.commandLine.appendSwitch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(calls).toEqual(SWITCHES.map(([name, value]) => (value === undefined ? [name] : [name, value])));
    const features = SWITCHES.find(([name]) => name === "disable-features")?.[1] ?? "";
    for (const feature of ["AimEnabled", "AutofillServerCommunication", "NetworkTimeServiceQuerying", "MediaRouter"]) {
      expect(features.split(",")).toContain(feature);
    }
    expect(SWITCHES.map(([name]) => name)).toContain("disable-background-networking");
    expect(SWITCHES.map(([name]) => name)).toContain("disable-component-update");
    // No GPU switch: Electron's defaults, as the Codex app runs.
    expect(SWITCHES.some(([name]) => /gpu|angle|gl/.test(name))).toBe(false);
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
