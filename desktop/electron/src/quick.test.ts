// Quick Entry (DESKTOP_PARITY_CLAUDE §4.2): which shortcuts may be system-wide, where they work, what the box sends.
import { describe, expect, it } from "vitest";

import { DEFAULT_SHORTCUT, QUICK_MAX, accelerator, quickPrompt, shortcutsWork } from "./quick";

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
