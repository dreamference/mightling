// The tray's count of threads (DESKTOP §16 B20) and its image on each system.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({ BrowserWindow: class {}, Menu: {}, Notification: class {}, Tray: class {}, app: {}, nativeImage: {}, powerSaveBlocker: {} }));

import { trayIconFile, trayStatus } from "./shell";

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

  it("has an image drawn for each Windows scaling, which nativeImage finds beside tray.png", () => {
    expect(trayIconFile("linux")).toBe("32x32.png");
    expect(trayIconFile("darwin")).toBe("32x32.png");
    expect(trayIconFile("win32")).toBe("tray.png");
    const icons = path.join(__dirname, "..", "icons");
    // A PNG's width is the big-endian number at byte 16 (its IHDR chunk).
    const width = (file: string) => fs.readFileSync(path.join(icons, file)).readUInt32BE(16);
    const sizes: [string, number][] = [["tray.png", 16], ["tray@1.25x.png", 20], ["tray@1.5x.png", 24], ["tray@2x.png", 32], ["tray@2.5x.png", 40], ["tray@3x.png", 48]];
    for (const [file, size] of sizes) expect([file, width(file)]).toEqual([file, size]);
  });
});
