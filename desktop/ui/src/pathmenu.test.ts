// One menu on every path (DESKTOP §16 B14).
import { describe, expect, it } from "vitest";

import { menuPosition, pathOf } from "./PathMenu";

describe("the path menu", () => {
  it("finds the absolute path a right-clicked element names", () => {
    const row = { closest: () => ({ dataset: { path: "/w/app/a.py" } }) };
    expect(pathOf(row as unknown as Element)).toBe("/w/app/a.py");
    const relative = { closest: () => ({ dataset: { path: "a.py" } }) };
    expect(pathOf(relative as unknown as Element)).toBeNull();
    expect(pathOf(null)).toBeNull();
  });

  it("opens inside the window", () => {
    expect(menuPosition(10, 10, 220, 150, { width: 800, height: 600 })).toEqual({ left: 10, top: 10 });
    expect(menuPosition(790, 590, 220, 150, { width: 800, height: 600 })).toEqual({ left: 576, top: 446 });
  });
});
