// Quick Entry's box (DESKTOP_PARITY_CLAUDE §4.2): what a key does in it.
import { describe, expect, it } from "vitest";

import { quickKey } from "./QuickView";

describe("Quick Entry's box", () => {
  it("sends on Enter, keeps Shift+Enter for a new line, hides on Esc, and leaves an input method's Enter alone", () => {
    expect(quickKey({ key: "Enter", shiftKey: false })).toBe("send");
    expect(quickKey({ key: "Enter", shiftKey: true })).toBeNull();
    expect(quickKey({ key: "Escape", shiftKey: false })).toBe("hide");
    expect(quickKey({ key: "Enter", shiftKey: false, isComposing: true })).toBeNull();
    expect(quickKey({ key: "a", shiftKey: false })).toBeNull();
  });
});
