// The parity backlog's B14 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): one menu on every path.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-14
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";
import { projectFolders } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B14: a right-click on a file's path offers Attach, Open in the editor, Show in the folder and Copy path", async () => {
  const { app, page, home, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    await page.getByRole("button", { name: "Decline" }).click();
    // The file the thread's turn changed, in the stream.
    const named = page.locator(".item.files code[data-path]").first();
    await named.click({ button: "right" });
    const menu = page.locator(".path-menu");
    await expect(menu.getByRole("menuitem")).toHaveText(["Attach to the message", "Open in the editor", "Show in the folder", "Copy path"]);
    await menu.getByRole("menuitem", { name: "Attach to the message" }).click();
    await expect(menu).toHaveCount(0);
    await expect(page.locator(".composer textarea")).toHaveValue(/^@\S*client\.py $/);
    // Show in the folder goes to the file manager through the main process, for a file that is there.
    const file = path.join(projectFolders(home).fetcher, "notes.md");
    fs.writeFileSync(file, "# Notes\n");
    await app.evaluate(({ shell }) => {
      const g = globalThis as unknown as { revealed: string[] };
      g.revealed = [];
      shell.showItemInFolder = (where: string) => { g.revealed.push(where); };
    });
    await page.evaluate((where) => {
      const tag = document.createElement("code");
      tag.dataset.path = where;
      tag.textContent = "notes.md";
      tag.id = "probe";
      document.querySelector(".turns")?.appendChild(tag);
    }, file);
    await page.locator("#probe").click({ button: "right" });
    await menu.getByRole("menuitem", { name: "Show in the folder" }).click();
    await expect.poll(() => app.evaluate(() => (globalThis as unknown as { revealed: string[] }).revealed)).toEqual([file]);
    // Esc closes it.
    await page.locator("#probe").click({ button: "right" });
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
