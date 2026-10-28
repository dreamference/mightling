// Windows quality (docs/dev/desktop.md, "Windows quality"), what a Linux display can check of it: the
// tray's image drawn for each scaling, as nativeImage loads it, and the title bar's room for the window
// controls taken from the overlay, which Electron draws on Linux too. Needs a display and the Vite
// build (`npm run package` first).
//   npm run e2e -- windows-quality
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("the tray's Windows image has one drawing per scaling, found beside tray.png", async () => {
  const { app, close } = await open();
  try {
    const file = path.join(__dirname, "..", "icons", "tray.png");
    const scales = await app.evaluate(({ nativeImage }, image) => nativeImage.createFromPath(image).getScaleFactors(), file);
    expect([...scales].sort()).toEqual([1, 1.25, 1.5, 2, 2.5, 3]);
  } finally {
    await close();
  }
});

test("the title bar leaves the window controls the room the overlay says they take", async () => {
  const { page, close } = await open();
  try {
    const measured = await page.evaluate(() => {
      const overlay = (navigator as unknown as { windowControlsOverlay?: { visible: boolean; getTitlebarAreaRect(): DOMRect } }).windowControlsOverlay;
      const area = overlay?.getTitlebarAreaRect();
      const bar = document.querySelector(".thread-pane .bar");
      return {
        visible: overlay?.visible ?? false,
        right: area ? window.innerWidth - area.x - area.width : null,
        padding: bar ? parseFloat(getComputedStyle(bar).paddingRight) : null,
      };
    });
    expect(measured.visible).toBe(true);
    expect(measured.right).toBeGreaterThan(0);
    expect(measured.padding).toBeCloseTo(Math.max(16, measured.right! + 12), 0);
  } finally {
    await close();
  }
});
