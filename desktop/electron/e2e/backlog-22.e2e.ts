// The parity backlog's B22 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): Enter for multiline prompts.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-22
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, readPrefs } from "./backlog-session";
import { projectFolders, received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B22: Enter sends a one-line prompt and, once it has several lines, starts another", async () => {
  const { page, home, close } = await open();
  try {
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByLabel(/Enter sends a one-line prompt/).check();
    await expect.poll(() => readPrefs(home).enterBehaviour).toBe("multiline");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(projectFolders(home).fetcher);
    const composer = page.locator(".composer textarea");
    await composer.fill("First line\nsecond line");
    await composer.press("End");
    await composer.press("Enter");
    await expect(composer).toHaveValue("First line\nsecond line\n");
    expect(received(home).some((m) => m.method === "turn/start")).toBe(false);
    await composer.press("Control+Enter");
    await expect.poll(() => received(home).some((m) => m.method === "turn/start")).toBe(true);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
