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
