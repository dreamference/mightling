// The tray's count of threads (DESKTOP §16 B20).
import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({ BrowserWindow: class {}, Menu: {}, Notification: class {}, Tray: class {}, app: {}, nativeImage: {}, powerSaveBlocker: {} }));

import { trayStatus } from "./shell";

describe("the tray", () => {
  it("says how many threads wait for you and how many work, or nothing", () => {
    expect(trayStatus({ working: 0, waiting: 0 })).toBeNull();
    expect(trayStatus({ working: 2, waiting: 0 })).toBe("2 working");
    expect(trayStatus({ working: 1, waiting: 1 })).toBe("1 waiting for you, 1 working");
    // Night Shift's unread results (DESKTOP §16 B41).
    expect(trayStatus({ working: 0, waiting: 0, night: 0 })).toBeNull();
    expect(trayStatus({ working: 1, waiting: 0, night: 1 })).toBe("1 working, 1 night result unread");
    expect(trayStatus({ working: 0, waiting: 0, night: 3 })).toBe("3 night results unread");
  });
});
