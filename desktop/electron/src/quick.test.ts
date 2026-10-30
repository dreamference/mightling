// Quick Entry (DESKTOP_PARITY_CLAUDE §4.2): which shortcuts may be system-wide, where they work, what the box sends.
import { describe, expect, it } from "vitest";

import { DEFAULT_SHORTCUT, QUICK_MAX, QUICK_SIZE, RecentQuestions, accelerator, quickPrompt, quickSize, shortcutsWork } from "./quick";

describe("Quick Entry's shortcut", () => {
  it("turns the keymap's spelling into Electron's, Ctrl standing for Cmd on a Mac as in the keymap", () => {
    expect(accelerator(DEFAULT_SHORTCUT)).toBe("CommandOrControl+Alt+Space");
    expect(accelerator("Super+Shift+k")).toBe("Super+Shift+K");
    expect(accelerator("Alt+F12")).toBe("Alt+F12");
    expect(accelerator("ctrl+alt+pageup")).toBe("CommandOrControl+Alt+PageUp");
  });

  it("refuses what would take ordinary typing or is not a key", () => {
    expect(accelerator("Space")).toBeNull();
    expect(accelerator("Shift+A")).toBeNull();
    expect(accelerator("Ctrl+Ctrl+A")).toBeNull();
    expect(accelerator("Ctrl+Alt+Sp ace")).toBeNull();
    expect(accelerator("Hyper+A")).toBeNull();
    expect(accelerator("Ctrl+Alt+é")).toBeNull();
    expect(accelerator(42)).toBeNull();
    expect(accelerator(`Ctrl+${"A".repeat(80)}`)).toBeNull();
  });

  it("is not possible on a Wayland session, is elsewhere", () => {
    expect(shortcutsWork("linux", { XDG_SESSION_TYPE: "x11", DISPLAY: ":0" })).toBe(true);
    expect(shortcutsWork("linux", { XDG_SESSION_TYPE: "wayland", DISPLAY: ":0", WAYLAND_DISPLAY: "wayland-0" })).toBe(false);
    expect(shortcutsWork("darwin", {})).toBe(true);
    expect(shortcutsWork("win32", {})).toBe(true);
  });
});

describe("Quick Entry's question", () => {
  it("is trimmed and capped, and nothing when empty", () => {
    expect(quickPrompt("  What is NVFP4?\n")).toBe("What is NVFP4?");
    expect(quickPrompt("   ")).toBeNull();
    expect(quickPrompt(null)).toBeNull();
    expect(quickPrompt("x".repeat(QUICK_MAX + 10))).toHaveLength(QUICK_MAX);
  });
});

describe("Quick Entry's recent questions (DESKTOP §16 B24)", () => {
  const thread = (id: string, cwd: string, preview: string, updatedAt: number, extra: Record<string, unknown> = {}) => ({ id, cwd, preview, name: null, updatedAt, ...extra });

  it("lists Ask's questions only, newest first, at most five, never an incognito one", () => {
    const recent = new RecentQuestions(() => "/home/u/.mightling/ask");
    recent.observe({ id: 1, result: { data: [
      thread("a1", "/home/u/.mightling/ask/q-1", "What is NVFP4?", 10),
      thread("w1", "/home/u/src/fetcher", "Fix the retry", 50),
      thread("a2", "/home/u/.mightling/ask/q-2", "How much  memory\nhas the GB10?", 20),
      thread("a3", "/home/u/.mightling/ask/q-3", "A secret", 30, { ephemeral: true }),
      thread("a4", "/home/u/.mightling/asking/q-4", "Not under the root", 40),
    ] } });
    expect(recent.list()).toEqual([{ id: "a2", title: "How much memory has the GB10?" }, { id: "a1", title: "What is NVFP4?" }]);
    for (let n = 0; n < 7; n++) recent.observe({ method: "thread/started", params: { thread: thread(`n${n}`, `/home/u/.mightling/ask/n${n}`, `Question ${n}`, 100 + n) } });
    expect(recent.list().map((q) => q.id)).toEqual(["n6", "n5", "n4", "n3", "n2"]);
  });

  it("follows renames, a new turn and an archive, and opens only what it listed", () => {
    const recent = new RecentQuestions(() => "/r/ask");
    recent.observe({ id: 2, result: { data: [thread("a", "/r/ask/a", "First", 1), thread("b", "/r/ask/b", "Second", 2)] } });
    recent.observe({ method: "thread/name/updated", params: { threadId: "a", threadName: "Renamed" } });
    recent.observe({ method: "turn/started", params: { threadId: "a", turn: { id: "t" } } });
    expect(recent.list()).toEqual([{ id: "a", title: "Renamed" }, { id: "b", title: "Second" }]);
    recent.observe({ method: "thread/archived", params: { threadId: "a" } });
    expect(recent.listed("a")).toBe(false);
    expect(recent.listed("b")).toBe(true);
    expect(recent.listed({ id: "b" })).toBe(false);
    recent.reset();
    expect(recent.list()).toEqual([]);
  });

  it("lists nothing it cannot place under Ask's root, so the box opens nothing else", () => {
    const rootless = new RecentQuestions(() => null);
    rootless.observe({ id: 1, result: { data: [thread("a", "/r/ask/a", "First", 1)] } });
    expect(rootless.list()).toEqual([]);
    expect(rootless.listed("a")).toBe(false);

    const recent = new RecentQuestions(() => "/r/ask");
    recent.observe({ id: 1, result: { data: [
      thread("up", "/r/ask/../other/x", "Escapes the root", 1),
      thread("root", "/r/ask", "The root itself", 2),
      thread("none", "", "No folder", 3),
      thread("blank", "/r/ask/b", "   ", 4),
      thread(7 as unknown as string, "/r/ask/n", "A number for an id", 5),
    ] } });
    recent.observe(null);
    recent.observe("text");
    recent.observe({ method: "thread/started", params: { thread: null } });
    expect(recent.list()).toEqual([]);
    for (const id of ["up", "root", "none", "blank", 7]) expect(recent.listed(id), String(id)).toBe(false);
  });

  it("titles a question by its name before its preview, keeps a newer time, and renames or moves up only what it listed", () => {
    const recent = new RecentQuestions(() => "/r/ask");
    recent.observe({ id: 2, result: { thread: thread("n", "/r/ask/n", "the preview", 10, { name: "  Named \n question " }) } });
    expect(recent.list()).toEqual([{ id: "n", title: "Named question" }]);
    recent.observe({ method: "thread/name/updated", params: { threadId: "n", threadName: "   " } });
    expect(recent.list()).toEqual([{ id: "n", title: "Named question" }]);
    recent.observe({ method: "thread/name/updated", params: { threadId: "other", threadName: "X" } });
    recent.observe({ method: "turn/started", params: { threadId: "other" } });
    expect(recent.listed("other")).toBe(false);
    recent.observe({ id: 3, result: { data: [thread("late", "/r/ask/l", "Late", 50), thread("mid", "/r/ask/m", "Mid", 20)] } });
    // An older answer read again does not move a question back.
    recent.observe({ id: 4, result: { thread: thread("late", "/r/ask/l", "Late", 5) } });
    expect(recent.list().map((question) => question.id)).toEqual(["late", "mid", "n"]);
  });

  it("grows the box by a line a question, up to five", () => {
    expect(quickSize(0)).toEqual(QUICK_SIZE);
    expect(quickSize(2).height).toBe(QUICK_SIZE.height + 8 + 56);
    expect(quickSize(9)).toEqual(quickSize(5));
  });
});
