// The parity backlog's B13 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): an image pasted in Work.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-13
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open } from "./backlog-session";
import { projectFolders, received } from "./stand-in";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B13: an image pasted in Work is attached, written under CODEX_HOME and sent by its path; the chip says a text-only model will not see it", async () => {
  const { page, home, close } = await open();
  try {
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.getByRole("textbox", { name: "Project folder" }).fill(projectFolders(home).fetcher);
    const composer = page.locator(".composer textarea");
    await composer.focus();
    // A screenshot on the clipboard: a PNG with no file behind it.
    await composer.evaluate((element) => {
      const png = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="), (c) => c.charCodeAt(0));
      const data = new DataTransfer();
      data.items.add(new File([png], "image.png", { type: "image/png" }));
      element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    });
    const chip = page.locator(".attachment", { hasText: "image.png" });
    await expect(chip).toBeVisible();
    await expect(chip).toContainText("Text-only model: not seen");
    await composer.fill("What does this screenshot show?");
    await composer.press("Enter");
    await expect.poll(() => {
      const start = received(home).find((m) => m.method === "turn/start");
      const inputs = (start?.params?.input ?? []) as { type: string; path?: string }[];
      return inputs.find((input) => input.type === "localImage")?.path ?? null;
    }).toMatch(/\.mightling[\\/]attachments[\\/]\d{8}[\\/]image\.png$/);
    // Nothing was written into the project.
    expect(fs.readdirSync(projectFolders(home).fetcher)).toEqual([]);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
