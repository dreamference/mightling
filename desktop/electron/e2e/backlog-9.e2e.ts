// The parity backlog's B9 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): two threads on one checkout.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-9
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";
import { projectFolders } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B9: a second thread in a checkout where another is working is told which, with Open and a worktree", async () => {
  const { page, home, close } = await open();
  try {
    // A turn that runs a few seconds (the stand-in's plan) in the website project, where nothing else works
    // (in fetcher, the stand-in's flaky retry thread is always working).
    const { website } = projectFolders(home);
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(website);
    await page.locator(".composer textarea").fill("Fix the retry with a plan.");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "The agent's plan" })).toBeVisible();
    // A new thread in the same folder: the row names the working one.
    await page.keyboard.press("Control+n");
    await page.getByRole("textbox", { name: "Project folder" }).fill(website);
    const clash = page.locator(".checkout-clash");
    await expect(clash).toContainText("Another thread is working in this checkout: Fix the retry with a plan.");
    await expect(clash.getByRole("button", { name: "Start it in a worktree" })).toBeVisible();
    // A folder where nothing works: nothing to say.
    await page.getByRole("textbox", { name: "Project folder" }).fill(`${home}/elsewhere`);
    await expect(clash).toHaveCount(0);
    // When the turn ends the row goes.
    await page.getByRole("textbox", { name: "Project folder" }).fill(website);
    await expect(clash).toHaveCount(0, { timeout: 15_000 });
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
