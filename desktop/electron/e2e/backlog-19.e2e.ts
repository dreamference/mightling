// The parity backlog's B19 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): the Codex app's other links.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-19
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";
import { projectFolders } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

const link = (app: import("@playwright/test").ElectronApplication, url: string) =>
  app.evaluate(({ app: electronApp }, url) => {
    electronApp.emit("second-instance", {}, ["ling-app", url], "/", { argv: [url] });
  }, url);

test("B19: the Codex app's other links: a new thread with its words and folder, a Settings page, Scheduled, a plugin", async () => {
  const { app, page, home, close } = await open();
  try {
    const fetcher = projectFolders(home).fetcher;
    await link(app, `ling://threads/new?prompt=${encodeURIComponent("Bump the retry limit")}&path=${encodeURIComponent(fetcher)}`);
    await expect(page.locator(".composer textarea")).toHaveValue("Bump the retry limit");
    await expect(page.getByRole("textbox", { name: "Project folder" })).toHaveValue(fetcher);
    // A folder that is not there is ignored: the words come, the folder named before stays.
    await link(app, `ling://new?path=${encodeURIComponent(fetcher + "-gone")}&prompt=x`);
    await expect(page.locator(".composer textarea")).toHaveValue("x");
    await page.waitForTimeout(300);
    await expect(page.getByRole("textbox", { name: "Project folder" })).toHaveValue(fetcher);
    await link(app, "ling://settings/notifications");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await expect(settings.getByRole("heading", { name: "Notifications" })).toBeVisible();
    await page.keyboard.press("Escape");
    await link(app, "ling://automations");
    await expect(page.getByRole("dialog", { name: "Scheduled" })).toBeVisible();
    await page.keyboard.press("Escape");
    await link(app, "ling://plugins/lint@team");
    const tools = page.getByRole("dialog", { name: "Skills and tools" });
    await expect(tools.locator("tr.focused")).toContainText("lint");
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
