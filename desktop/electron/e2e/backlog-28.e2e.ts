// The parity backlog's B28 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): terminal tabs in the tab area.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-28
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, readPrefs } from "./backlog-session";
import { received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B28: a terminal opens as a tab in the tab area, from New tab or, when Settings say so, from Ctrl+`", async () => {
  const { page, home, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    // Settings › Terminal tabs: in the tab area.
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("button", { name: "Project and terminal" }).click();
    const tabsPlace = settings.getByRole("radio", { name: /Open terminals as tabs/ });
    await tabsPlace.check();
    await expect.poll(() => readPrefs(home).terminalPlace).toBe("tabs");
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+`");
    const tab = page.getByRole("tabpanel", { name: "Terminal" });
    await expect(tab.locator(".xterm-rows")).toContainText("stand-in shell in");
    await page.keyboard.type("echo hello from the tab area");
    await page.keyboard.press("Enter");
    await expect(tab.locator(".xterm-rows")).toContainText("hello from the tab area");
    // Not in the bottom panel as well.
    await expect(page.getByRole("region", { name: "Bottom panel" }).locator(".xterm-rows")).toHaveCount(0);
    // New tab › Terminal opens a second one.
    await page.getByRole("button", { name: "New tab" }).click();
    await page.getByRole("menuitem", { name: "Terminal" }).click();
    await expect(page.getByRole("tab", { name: /Terminal/ })).toHaveCount(2);
    expect(received(home).filter((m) => m.method === "command/exec")).toHaveLength(2);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
