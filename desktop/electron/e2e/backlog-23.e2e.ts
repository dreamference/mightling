// The parity backlog's B23 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): a readable font.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-23
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, readPrefs } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B23: Settings › Appearance offers a bundled readable font, used at once and kept", async () => {
  const { page, home, close } = await open();
  try {
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("button", { name: "Appearance" }).click();
    await settings.getByLabel(/A font made for reading/).check();
    await expect.poll(() => (readPrefs(home).appearance as { fontUi?: string } | undefined)?.fontUi).toContain("Atkinson Hyperlegible");
    await expect(settings.getByRole("textbox", { name: "Interface font" })).toHaveValue(/Atkinson Hyperlegible/);
    await page.keyboard.press("Escape");
    const font = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
    expect(font).toContain("Atkinson Hyperlegible");
    // The face is the bundled one: loaded from the app, not fetched.
    await expect.poll(() => page.evaluate(async () => (await document.fonts.load('16px "Atkinson Hyperlegible"')).length)).toBeGreaterThan(0);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
