// The parity backlog's B10 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): every thread reachable.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-10
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, readPrefs } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B10: the sidebar reads every page of threads, so an old project is listed; by date across projects", async () => {
  const { page, home, close } = await open({ env: { MIGHTLING_STAND_IN_PAGES: "1" } });
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    // The thread on the second page, in a project seen nowhere else.
    await expect(page.locator(".project h2", { hasText: "quiet" })).toBeVisible();
    await expect(page.locator(".thread", { hasText: "Rename the old config keys" })).toBeVisible();
    expect(received(home).filter((m) => m.method === "thread/list" && m.params?.cursor === "page-2")).toHaveLength(1);
    // Chronological: one list by date, each row naming its project; the project groups go.
    await page.getByRole("button", { name: "Chronological" }).click();
    const byDate = page.locator(".project.chronological");
    await expect(byDate.locator(".thread").last()).toContainText("Rename the old config keys · quiet");
    await expect(page.locator(".project h2", { hasText: "quiet" })).toHaveCount(0);
    await expect.poll(() => readPrefs(home).threadOrder).toBe("chronological");
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
