// The parity backlog's B7 end to end (specs/DREAMFERENCE_MIGHTLING_DESKTOP.md §16): dictation's key and word list.
// Needs a display and the Vite build (`npm run package` first).
//   npm run e2e -- backlog-7
import fs from "node:fs";
import { expect, test } from "@playwright/test";

import { closeLeftovers } from "./apps";
import { appServers, main, open, readPrefs } from "./backlog-session";

test.skip(!process.env.DISPLAY || !fs.existsSync(main), "needs a display and the Vite build");
test.afterEach(closeLeftovers);

test("B7: Ctrl+Shift+V starts and stops dictation, and the word list in Settings › Voice goes with the recording", async () => {
  const { app, page, home, close } = await open({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
  try {
    await page.keyboard.press("Control+,");
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("navigation", { name: "Settings pages" }).getByRole("button", { name: "Voice" }).click();
    const words = settings.getByRole("textbox", { name: "Words to spell as written" });
    await words.fill("NVFP4\nfetcher/client.py\n\nNVFP4");
    await words.blur();
    await expect.poll(() => readPrefs(home).dictationWords).toEqual(["NVFP4", "fetcher/client.py"]);
    await page.keyboard.press("Escape");
    // The speech-to-text service replaced in the main process: what it is sent is recorded.
    await app.evaluate(() => {
      const g = globalThis as unknown as { prompts: unknown[]; fetch: typeof fetch };
      g.prompts = [];
      g.fetch = (async (_url: string, init: RequestInit) => {
        g.prompts.push((init.body as FormData).get("prompt"));
        return new Response(JSON.stringify({ text: "Check fetcher/client.py" }), { status: 200 });
      }) as unknown as typeof fetch;
    });
    await page.getByRole("button", { name: "Work", exact: true }).click();
    await page.locator(".thread", { hasText: "flaky retry" }).click();
    await page.locator(".composer textarea").focus();
    await page.keyboard.press("Control+Shift+V");
    await expect(page.getByRole("button", { name: "Stop dictation" })).toBeVisible();
    await page.waitForTimeout(500);
    await page.keyboard.press("Control+Shift+V");
    await expect(page.locator(".composer textarea")).toHaveValue("Check fetcher/client.py");
    expect(await app.evaluate(() => (globalThis as unknown as { prompts: unknown[] }).prompts)).toEqual(["NVFP4, fetcher/client.py"]);
    expect(appServers(home)).toBe(1);
  } finally {
    await close();
  }
});
