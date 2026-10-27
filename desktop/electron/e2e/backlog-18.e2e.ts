// The parity backlog's B18 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): the pet's threads, blocked state and place.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-18
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, readPrefs } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B18: the pet lists the threads that need you, opens one, and keeps its place", async () => {
  const { app, page, home, close } = await open();
  try {
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("button", { name: "Pets" }).click();
    await settings.getByRole("checkbox", { name: "Show the pet" }).check();
    await page.keyboard.press("Escape");
    await expect.poll(() => app.windows().some((entry) => entry.url().endsWith("#pet")), { timeout: 15_000 }).toBe(true);
    const pet = app.windows().find((entry) => entry.url().endsWith("#pet"))!;
    // A thread with an approval waiting: the stand-in asks one when the flaky retry thread opens.
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".approval").waitFor();
    // Another Work thread in front: the waiting one is left behind it.
    await page.locator(".thread", { hasText: "Compress the hero image" }).click();
    const body = pet.locator(".pet-body");
    await expect(body).toHaveClass(/mood-waiting/);
    await body.click();
    const needs = pet.getByRole("list", { name: "Threads that need you" });
    await expect(needs).toContainText("Waiting Fix the flaky retry test");
    await needs.getByRole("button", { name: /Fix the flaky retry test/ }).click();
    await expect(page.locator(".bar .title")).toHaveText("Fix the flaky retry test");
    // Moved, its place is kept for the next start.
    await pet.evaluate(() => (window as unknown as { electronBridge: { sendMessageFromView: (m: object) => Promise<unknown> } }).electronBridge.sendMessageFromView({ type: "pet/move", dx: -200, dy: -100 }));
    await expect.poll(() => readPrefs(home).petCorner ?? null).not.toBeNull();
    const corner = readPrefs(home).petCorner as { right: number; bottom: number };
    expect(Number.isFinite(corner.right) && Number.isFinite(corner.bottom)).toBe(true);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
