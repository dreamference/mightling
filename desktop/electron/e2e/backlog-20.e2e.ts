// The parity backlog's B20 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): the menu and the tray.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-20
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

const clickMenu = (app: import("@playwright/test").ElectronApplication, top: string, label: string) =>
  app.evaluate(({ Menu, BrowserWindow }, [top, label]) => {
    const item = Menu.getApplicationMenu()?.items.find((entry) => entry.label === top)?.submenu?.items.find((entry) => entry.label === label);
    if (!item) throw new Error(`no ${top} › ${label}`);
    item.click(undefined, BrowserWindow.getAllWindows()[0], undefined as unknown as Electron.WebContents);
  }, [top, label]);

test("B20: File › New Thread, Open Folder… and Settings…, and Help › Show Logs", async () => {
  const { app, page, home, close } = await open();
  try {
    await clickMenu(app, "File", "Settings…");
    await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await clickMenu(app, "File", "New Thread");
    await expect(page.getByRole("textbox", { name: "Project folder" })).toBeVisible();
    await app.evaluate(({ shell }) => {
      const g = globalThis as unknown as { opened: string[] };
      g.opened = [];
      shell.openPath = async (where: string) => { g.opened.push(where); return ""; };
    });
    await clickMenu(app, "Help", "Show Logs");
    await expect.poll(() => app.evaluate(() => (globalThis as unknown as { opened: string[] }).opened)).toEqual([path.join(home, ".mightling", "log")]);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
