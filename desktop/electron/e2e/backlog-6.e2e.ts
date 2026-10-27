// The parity backlog's B6 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): the first run.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-6
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, readPrefs } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B6: the first run names the ways in once, to someone with no threads; a thread with no folder starts from it", async () => {
  const { page, home, close } = await open({ env: { MIGHTLING_STAND_IN_EMPTY: "1" }, waitForThreads: false });
  try {
    const welcome = page.getByRole("dialog", { name: "Welcome to Mightling" });
    await expect(welcome).toBeVisible();
    await expect(welcome).toContainText("/airgapped on");
    await welcome.getByRole("button", { name: /Bring over another agent's setup/ }).click();
    await expect(welcome).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Skills and tools" })).toBeVisible();
    await expect.poll(() => readPrefs(home).welcomed).toBe(true);
    // Seen once: a reload does not show it again.
    await page.reload();
    await page.getByRole("button", { name: "Work", exact: true }).waitFor();
    await expect(page.getByRole("dialog", { name: "Welcome to Mightling" })).toHaveCount(0);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
