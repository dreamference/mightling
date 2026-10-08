import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({ screen: {} }));

import { clamped } from "./window-state";

describe("restored bounds", () => {
  const area = { x: 0, y: 0, width: 1920, height: 1080 };
  it("stay on the display and above the minimum size", () => {
    expect(clamped({ x: 100, y: 100, width: 800, height: 600 }, area, { width: 480, height: 600 })).toEqual({ x: 100, y: 100, width: 800, height: 600 });
    // Off the right edge: pulled back in.
    expect(clamped({ x: 1800, y: 900, width: 800, height: 600 }, area, { width: 480, height: 600 })).toEqual({ x: 1120, y: 480, width: 800, height: 600 });
    // Bigger than the display: shrunk to it; smaller than the minimum: grown to it.
    expect(clamped({ x: 0, y: 0, width: 4000, height: 100 }, area, { width: 480, height: 600 })).toEqual({ x: 0, y: 0, width: 1920, height: 600 });
    expect(clamped(undefined, area, { width: 480, height: 600 })).toBeUndefined();
  });
});
