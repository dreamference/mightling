// The parity backlog's B4 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): the Codex app's other composer commands and the skills in the / menu.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-4
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B4: the Codex app's other commands run their actions, and enabled skills are in the / menu", async () => {
  const { page, home, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    const composer = page.locator(".composer textarea");
    // /pin pins the thread shown; /pin again unpins it.
    await composer.fill("/pin");
    await composer.press("Enter");
    await expect(page.locator(".project.pinned .thread-row")).toHaveCount(1);
    await composer.fill("/pin");
    await composer.press("Enter");
    await expect(page.locator(".project.pinned")).toHaveCount(0);
    // /hooks and /plugins open Skills and tools on their tab.
    await composer.fill("/hooks");
    await composer.press("Enter");
    const tools = page.getByRole("dialog", { name: "Skills and tools" });
    await expect(tools).toBeVisible();
    await tools.getByRole("button", { name: "Close" }).click();
    await expect(tools).toHaveCount(0);
    // An enabled skill follows the commands in the / menu; choosing it inserts $name.
    await composer.fill("/pdf");
    await expect(page.getByRole("listbox", { name: "Commands" }).locator("li", { hasText: "$pdf-search" })).toBeVisible();
    await composer.press("Enter");
    await expect(composer).toHaveValue(/^\$pdf-search\s?$/);
    // /reasoning with an effort the model does not offer says which it does.
    await composer.fill("/reasoning extreme");
    await composer.press("Enter");
    await expect(page.getByText(/The model takes one of|offers no choice of effort/)).toBeVisible();
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
