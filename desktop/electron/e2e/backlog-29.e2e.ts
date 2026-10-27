// The parity backlog's B29 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): preview area comments and address history.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-29
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { main, open } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B29: preview comments on an element and on a dragged area go to the composer together; the address suggests the tab's history", async () => {
  const http = await import("node:http");
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html");
    response.end('<!doctype html><title>Area page</title><body style="margin:0;font:16px sans-serif"><h1 id="hero" style="margin:24px">The hero</h1><section style="height:400px;background:#eee"></section></body>');
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `127.0.0.1:${(server.address() as { port: number }).port}/`;
  const { app, page, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "Compress the hero" }).click();
    await page.keyboard.press("Control+t");
    const address = page.getByRole("combobox", { name: "Address" });
    await address.fill(url);
    await address.press("Enter");
    await expect(page.getByRole("tab", { name: /Area page/ })).toBeVisible({ timeout: 20_000 });
    await expect.poll(() => app.windows().some((entry) => entry.url().startsWith("http://127.0.0.1"))).toBe(true);
    const view = app.windows().find((entry) => entry.url().startsWith("http://127.0.0.1"))!;
    // The tab's own history: the address it loaded, offered back.
    await expect(page.locator("datalist option").first()).toHaveAttribute("value", /127\.0\.0\.1/);
    // An element first, kept waiting with Add another, which picks again.
    await page.getByRole("button", { name: "Comment on the page" }).click();
    await view.locator("#hero").click();
    await page.getByRole("textbox", { name: "Comment on the element" }).fill("One line on phones.");
    await page.getByRole("button", { name: "Add another" }).click();
    await expect(page.locator(".preview-note").first()).toBeVisible();
    // Then an area, dragged over the grey section.
    await view.mouse.move(40, 120);
    await view.mouse.down();
    await view.mouse.move(200, 220, { steps: 5 });
    await view.mouse.up();
    await expect(page.locator(".preview-comment .picked")).toContainText("An area of");
    await page.getByRole("textbox", { name: "Comment on the element" }).fill("Too much grey.");
    await page.getByRole("button", { name: "Send all 2 to the composer" }).click();
    const composer = page.locator(".composer textarea");
    await expect(composer).toHaveValue(/the element `h1#hero` \("The hero"\): One line on phones\./);
    await expect(composer).toHaveValue(/the area of \d+ × \d+ px at \(\d+, \d+\), around `[^`]+`: Too much grey\./);
  } finally {
    await close();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
