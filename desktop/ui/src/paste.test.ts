// Long pastes as attachments (DESKTOP_PARITY_CLAUDE §4.6).
import { describe, expect, it } from "vitest";

import { LONG_PASTE, isLongPaste, pasteLabel, withPastes } from "./composer";

describe("a long paste", () => {
  it("is one of 2,000 characters or 40 lines", () => {
    expect(isLongPaste("short")).toBe(false);
    expect(isLongPaste("x".repeat(LONG_PASTE.chars))).toBe(true);
    expect(isLongPaste(Array.from({ length: 40 }, (_, i) => `line ${i}`).join("\n"))).toBe(true);
    expect(isLongPaste(Array.from({ length: 39 }, (_, i) => `line ${i}`).join("\n"))).toBe(false);
  });

  it("is named by its lines, or its characters on one line", () => {
    expect(pasteLabel("a\nb\nc\n")).toBe("Pasted text · 3 lines");
    expect(pasteLabel("x".repeat(2500))).toBe("Pasted text · 2,500 characters");
  });

  it("goes into a Work message after the words, fenced past any fence inside it", () => {
    expect(withPastes("Why does this fail?", ["Traceback\n  boom\n"])).toBe("Why does this fail?\n\nPasted text 1:\n```\nTraceback\n  boom\n```");
    expect(withPastes("", ["```js\nx\n```", "b"])).toBe("Pasted text 1:\n````\n```js\nx\n```\n````\n\nPasted text 2:\n```\nb\n```");
  });
});
