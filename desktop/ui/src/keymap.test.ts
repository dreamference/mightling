// The keymap (DESKTOP_PARITY §5 "Keymap"): §2.19 as JSON, no binding shared by two actions in one
// context, and the table and the code agree on which actions have a handler.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { KEYMAP, chordOf, conflicts, keyAction, keyLabel, matches, matchesChord, parseChord, setMacKeys, shortcutLabel } from "./keymap";

// The table as Linux and Windows read it, whatever machine runs the tests; the Mac's own test sets it.
beforeEach(() => setMacKeys(false));
afterEach(() => setMacKeys(false));

// The UI's sources as text, to find the actions their handlers name.
const sources = import.meta.glob(["./*.tsx", "./*.ts", "!./*.test.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

function handled(): Set<string> {
  const ids = new Set<string>();
  for (const [file, text] of Object.entries(sources)) {
    if (file.endsWith("keymap.ts")) continue;
    for (const match of text.matchAll(/(?:keyAction|shortcutLabel)\("([A-Za-z0-9]+)"\)/g)) ids.add(match[1]);
  }
  return ids;
}

const key = (k: string, mods: Partial<{ ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean }> = {}) =>
  ({ key: k, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, ...mods });

describe("the keymap", () => {
  it("holds §2.19's table: every group, unique ids, and no two actions sharing a binding in one context", () => {
    expect(new Set(KEYMAP.map((action) => action.group))).toEqual(new Set(["General", "Chats", "Input", "Workspace"]));
    expect(new Set(KEYMAP.map((action) => action.id)).size).toBe(KEYMAP.length);
    expect(conflicts()).toEqual([]);
    expect(conflicts([...KEYMAP, { ...keyAction("newChat"), id: "clash" }])).toEqual(["Ctrl+N in global: newChat and clash"]);
    // Spot checks against §2.19.
    expect(keyAction("commandMenu").keys).toEqual(["Ctrl+Shift+P", "Ctrl+K"]);
    expect(keyAction("modelPicker").keys).toEqual(["Ctrl+Shift+M"]);
    expect(keyAction("restorePrompt").keys).toEqual(["ArrowUp"]);
    expect(keyAction("environmentAction1").keys).toEqual(["Super+Shift+D"]);
    expect(keyAction("viewAsk").keys).toEqual(["Alt+1"]);
  });

  it("marks built exactly the actions the UI handles", () => {
    const ids = handled();
    const built = KEYMAP.filter((action) => action.status === "built").map((action) => action.id).sort();
    expect([...ids].filter((id) => KEYMAP.some((action) => action.id === id)).sort()).toEqual(built);
    for (const id of ids) expect(() => keyAction(id)).not.toThrow();
  });

  it("matches events to bindings, modifiers exactly", () => {
    expect(parseChord("Ctrl+Alt+Shift+O")).toEqual({ ctrl: true, shift: true, alt: true, meta: false, key: "O" });
    expect(matches(key("M", { ctrlKey: true, shiftKey: true }), keyAction("modelPicker"))).toBe(true);
    expect(matches(key("m", { ctrlKey: true }), keyAction("modelPicker"))).toBe(false);
    expect(matches(key("m", { altKey: true }), keyAction("recentModels"))).toBe(true);
    expect(matches(key("Enter"), keyAction("approve"))).toBe(true);
    expect(matches(key("Enter", { shiftKey: true }), keyAction("approve"))).toBe(false);
    expect(matches(key("Escape"), keyAction("decline"))).toBe(true);
    expect(matches(key("k", { ctrlKey: true }), keyAction("commandMenu"))).toBe(true);
    // Shifted punctuation: Ctrl+Shift+. arrives as ">", Ctrl+= is often typed as Ctrl++.
    expect(matchesChord(key(">", { ctrlKey: true, shiftKey: true }), "Ctrl+Shift+.")).toBe(true);
    expect(matchesChord(key("+", { ctrlKey: true, shiftKey: true }), "Ctrl+=")).toBe(true);
    expect(matchesChord(key("=", { ctrlKey: true }), "Ctrl+=")).toBe(true);
    expect(matchesChord(key("d", { metaKey: true, shiftKey: true }), "Super+Shift+D")).toBe(true);
    // A sequence (Escape Escape) is its owner's to match, never a single press.
    expect(matches(key("Escape"), keyAction("editPrevious"))).toBe(false);
  });
});

describe("on a Mac", () => {
  it("Ctrl is the Command key, Super the Control key; Tab and the terminal keep Control", () => {
    setMacKeys(true);
    // Cmd+P searches files and Cmd+T opens a preview; Control+P and Control+T do not.
    expect(matches(key("p", { metaKey: true }), keyAction("searchFiles"))).toBe(true);
    expect(matches(key("p", { ctrlKey: true }), keyAction("searchFiles"))).toBe(false);
    expect(matches(key("t", { metaKey: true }), keyAction("previewOpen"))).toBe(true);
    expect(matches(key("M", { metaKey: true, shiftKey: true }), keyAction("modelPicker"))).toBe(true);
    expect(matches(key(">", { metaKey: true, shiftKey: true }), keyAction("commentMode"))).toBe(true);
    // Super is the Control key there, so Super+Alt+P does not collide with Ctrl+Alt+P.
    expect(matches(key("p", { ctrlKey: true, altKey: true }), keyAction("togglePet"))).toBe(true);
    expect(matches(key("p", { metaKey: true, altKey: true }), keyAction("pinChat"))).toBe(true);
    // Control+Tab cycles threads on a Mac too, and Control+L clears the terminal.
    expect(matches(key("Tab", { ctrlKey: true }), keyAction("nextChat"))).toBe(true);
    expect(matches(key("Tab", { metaKey: true }), keyAction("nextChat"))).toBe(false);
    expect(matches(key("l", { ctrlKey: true }), keyAction("clearTerminal"))).toBe(true);
    expect(matches(key("l", { metaKey: true }), keyAction("goToLine"))).toBe(true);
    // A recorded binding is written as the table writes it, and labels name the Mac's keys.
    expect(chordOf(key("p", { metaKey: true, shiftKey: true }))).toBe("Ctrl+Shift+P");
    expect(chordOf(key("d", { ctrlKey: true, shiftKey: true }))).toBe("Shift+Super+D");
    expect(chordOf(key("Tab", { ctrlKey: true }))).toBe("Ctrl+Tab");
    expect(shortcutLabel("searchFiles")).toBe("Cmd+P");
    expect(shortcutLabel("togglePet")).toBe("Ctrl+Option+P");
    expect(shortcutLabel("nextChat")).toBe("Ctrl+Tab");
    expect(keyLabel("Ctrl+L", "terminal")).toBe("Ctrl+L");
    setMacKeys(false);
    expect(matches(key("p", { ctrlKey: true }), keyAction("searchFiles"))).toBe(true);
    expect(shortcutLabel("searchFiles")).toBe("Ctrl+P");
  });
});
