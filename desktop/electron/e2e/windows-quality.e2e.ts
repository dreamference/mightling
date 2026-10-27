// Windows quality (docs/dev/desktop.md, "Windows quality"), what a Linux display can check of it: the
// tray's image drawn for each scaling, as nativeImage loads it. Needs a display and the Vite build
// (`npm run package` first).
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
