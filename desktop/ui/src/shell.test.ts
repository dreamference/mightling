// The shell (DESKTOP_PARITY §2.1, §2.19): back and forward, undo and redo of app actions, the font
// size steps, the surfaces Ctrl+W closes and Ctrl+Shift+T reopens, the command menu's filter, and
// shortcuts the user rebinds.
import { afterEach, describe, expect, it } from "vitest";

import { chordOf, conflicts, effectiveKeymap, keyAction, matches, setOverrides } from "./keymap";
import { ClosedSurfaces, History, UndoStack, filterEntries, nextScale, type MenuEntry } from "./shell";

const key = (k: string, mods: Partial<{ ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean }> = {}) =>
  ({ key: k, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, ...mods });

afterEach(() => setOverrides({}));

describe("the shell", () => {
  it("goes back and forward through the places the window showed", () => {
    const history = new History();
    history.visit({ view: "ask", thread: null });
    history.visit({ view: "work", thread: "a" });
    history.visit({ view: "work", thread: "a" });
    history.visit({ view: "work", thread: "b" });
    expect(history.back()).toEqual({ view: "work", thread: "a" });
    expect(history.back()).toEqual({ view: "ask", thread: null });
    expect(history.back()).toBeNull();
    expect(history.forward()).toEqual({ view: "work", thread: "a" });
    history.visit({ view: "work", thread: "c" });
    expect(history.canGoForward).toBe(false);
  });

  it("undoes and redoes the app's own actions", async () => {
    const done: string[] = [];
    const stack = new UndoStack();
    stack.push({ label: "Archive", undo: () => void done.push("unarchive"), redo: () => void done.push("archive") });
    stack.push({ label: "Rename", undo: () => void done.push("old name"), redo: () => void done.push("new name") });
    expect(await stack.undo()).toBe("Rename");
    expect(await stack.undo()).toBe("Archive");
    expect(await stack.undo()).toBeNull();
    expect(await stack.redo()).toBe("Archive");
    expect(done).toEqual(["old name", "unarchive", "archive"]);
    stack.push({ label: "Pin", undo: () => {}, redo: () => {} });
    expect(stack.canRedo).toBe(false);
  });

  it("steps the text size as zoom levels do, and reopens closed surfaces last first", () => {
    expect(nextScale(1, 1)).toBe(1.1);
    expect(nextScale(1, -1)).toBe(0.9);
    expect(nextScale(2, 1)).toBe(2);
    expect(nextScale(1.5, 0)).toBe(1);
    const closed = new ClosedSurfaces();
    closed.close("review");
    closed.close("bottomPanel");
    expect(closed.reopen()).toBe("bottomPanel");
    expect(closed.reopen()).toBe("review");
    expect(closed.reopen()).toBeNull();
  });

  it("filters the command menu by every word, labels that start with the query first", () => {
    const entries: MenuEntry[] = [
      { id: "t1", kind: "thread", label: "Fix the flaky retry test", detail: "/src/fetcher", shortcut: null },
      { id: "fontBigger", kind: "command", label: "Font size bigger", detail: "General", shortcut: "Ctrl+=" },
      { id: "p", kind: "project", label: "fetcher", detail: "/src/fetcher", shortcut: null },
    ];
    expect(filterEntries(entries, "fetch").map((entry) => entry.id)).toEqual(["p", "t1"]);
    expect(filterEntries(entries, "flaky test").map((entry) => entry.id)).toEqual(["t1"]);
    expect(filterEntries(entries, "ctrl+=").map((entry) => entry.id)).toEqual(["fontBigger"]);
  });

  it("applies rebound shortcuts and spells a key event as the table does", () => {
    expect(chordOf(key("m", { ctrlKey: true, altKey: true }))).toBe("Ctrl+Alt+M");
    expect(chordOf(key("Control", { ctrlKey: true }))).toBeNull();
    setOverrides({ modelPicker: ["Ctrl+Alt+M"], nonsense: ["Ctrl+X"], recentModels: "Alt+M" });
    expect(keyAction("modelPicker").keys).toEqual(["Ctrl+Alt+M"]);
    expect(matches(key("m", { ctrlKey: true, altKey: true }), keyAction("modelPicker"))).toBe(true);
    expect(matches(key("M", { ctrlKey: true, shiftKey: true }), keyAction("modelPicker"))).toBe(false);
    expect(keyAction("recentModels").keys).toEqual(["Alt+M"]);
    expect(conflicts(effectiveKeymap())).toEqual([]);
    setOverrides({ modelPicker: ["Ctrl+N"] });
    expect(conflicts(effectiveKeymap())).toEqual(["Ctrl+N in global: newChat and modelPicker"]);
  });
});
