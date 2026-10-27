// The parity backlog's B24 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): recent questions in Quick Entry.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-24
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B24: Quick Entry's box lists the recent Ask questions, and one click opens one in the app window", async () => {
  const { app, page, home, close } = await open();
  try {
    const opened = app.waitForEvent("window");
    await app.evaluate(({ app: electronApp }) => {
      electronApp.emit("open-url", { preventDefault: () => {} }, "ling://quick");
    });
    const box = await opened;
    await box.waitForLoadState("domcontentloaded");
    const recent = box.getByRole("navigation", { name: "Recent questions" });
    await expect(recent).toContainText("Why does the GB10 share memory with the GPU?");
    // Ask's questions only: no Work thread.
    await expect(recent).not.toContainText("Fix the flaky retry test");
    await recent.getByRole("button", { name: /GB10 share memory/ }).click();
    await expect.poll(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some((window) => window.webContents.getURL().endsWith("#quick") && window.isVisible()))).toBe(false);
    await expect(page.locator(".item.user", { hasText: "Why does the GB10 share memory with the GPU?" })).toBeVisible();
    // Shown in Ask, where it was asked.
    await expect(page.locator(".work.view-ask")).toBeVisible();
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
