// Quick Entry's box (DESKTOP_PARITY_CLAUDE §4.2): what a key does in it.
import { describe, expect, it } from "vitest";

import { quickKey, recentQuestions } from "./QuickView";

describe("Quick Entry's box", () => {
  it("sends on Enter, keeps Shift+Enter for a new line, hides on Esc, and leaves an input method's Enter alone", () => {
    expect(quickKey({ key: "Enter", shiftKey: false })).toBe("send");
    expect(quickKey({ key: "Enter", shiftKey: true })).toBeNull();
    expect(quickKey({ key: "Escape", shiftKey: false })).toBe("hide");
    expect(quickKey({ key: "Enter", shiftKey: false, isComposing: true })).toBeNull();
    expect(quickKey({ key: "a", shiftKey: false })).toBeNull();
  });
});

describe("Quick Entry's recent questions (DESKTOP §16 B24)", () => {
  it("takes the main process's list as it is, and nothing that is not one", () => {
    expect(recentQuestions([{ id: "a", title: "What is NVFP4?" }, { id: 3, title: "x" }, null])).toEqual([{ id: "a", title: "What is NVFP4?" }]);
    expect(recentQuestions("no")).toEqual([]);
  });
});
