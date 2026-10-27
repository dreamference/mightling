// The parity backlog's B21 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): subagent marks.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-21
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B21: each subagent has one mark, the same in the panel and on the sidebar row of the thread that started it", async () => {
  const { page, home, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    const panel = page.getByLabel("Subagents", { exact: true });
    await expect(panel).toContainText("1 of 2 working");
    const marks = panel.locator(".subagent .agent-mark");
    await expect(marks).toHaveCount(2);
    const working = panel.locator(".subagent", { has: page.locator(".running") }).locator(".agent-mark");
    const glyph = await working.textContent();
    const colour = await working.evaluate((element) => getComputedStyle(element).color);
    // The sidebar row of the thread shows its working subagent's mark, the same glyph and colour.
    const row = page.locator(".thread-row", { hasText: "flaky retry" }).first().locator(".agent-mark");
    await expect(row).toHaveCount(1);
    await expect(row).toHaveText(glyph ?? "");
    expect(await row.evaluate((element) => getComputedStyle(element).color)).toBe(colour);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
